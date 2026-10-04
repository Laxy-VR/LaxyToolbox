"""Tests for the child process registry that prevents orphaned encoders."""

import subprocess
import sys
import time

from sysutil import track_child, untrack_child, terminate_children


def _sleeper():
    return subprocess.Popen([sys.executable, "-c",
                             "import time; time.sleep(60)"])


def test_terminate_children_kills_tracked_process():
    proc = _sleeper()
    track_child(proc)
    try:
        assert proc.poll() is None  # alive before
        terminate_children(timeout=10)
        assert proc.poll() is not None  # dead after
    finally:
        untrack_child(proc)
        if proc.poll() is None:
            proc.kill()


def test_untracked_process_is_left_alone():
    proc = _sleeper()
    track_child(proc)
    untrack_child(proc)
    try:
        terminate_children(timeout=1)
        time.sleep(0.2)
        assert proc.poll() is None  # still running: it was untracked
    finally:
        proc.kill()


def test_relaunch_env_scrubs_pyinstaller_state(monkeypatch):
    """Regression: an inherited _PYI*/_MEIPASS* state makes a relaunched
    onefile exe reuse (and then lose) the parent's _MEI temp directory."""
    from sysutil import _relaunch_env
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", r"C:\Temp\_MEI123")
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", r"C:\app.exe")
    monkeypatch.setenv("_MEIPASS2", r"C:\Temp\_MEI123")
    monkeypatch.setenv("SOME_NORMAL_VAR", "kept")
    env = _relaunch_env()
    assert not any(k.startswith(("_PYI", "_MEIPASS")) for k in env)
    assert env["SOME_NORMAL_VAR"] == "kept"
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def _alive(pid):
    import ctypes
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    k32.GetExitCodeProcess(handle, ctypes.byref(code))
    k32.CloseHandle(handle)
    return code.value == 259  # STILL_ACTIVE


def test_kill_tree_stops_grandchildren():
    """Regression: yt-dlp.exe is a PyInstaller onefile launcher, so a plain
    terminate() killed the launcher and left the real downloader running."""
    import pytest
    if sys.platform != "win32":
        pytest.skip("Windows process trees")
    from sysutil import kill_tree
    script = ("import subprocess, sys, time\n"
              "c = subprocess.Popen([sys.executable, '-c', "
              "'import time; time.sleep(60)'])\n"
              "print(c.pid, flush=True)\n"
              "time.sleep(60)\n")
    parent = subprocess.Popen([sys.executable, "-c", script],
                              stdout=subprocess.PIPE, text=True)
    grandchild = int(parent.stdout.readline())
    try:
        assert _alive(grandchild)
        kill_tree(parent)
        parent.wait(timeout=10)
        for _ in range(50):
            if not _alive(grandchild):
                break
            time.sleep(0.1)
        assert not _alive(grandchild)
    finally:
        if _alive(grandchild):
            subprocess.run(["taskkill", "/F", "/PID", str(grandchild)],
                           capture_output=True)


def test_terminate_children_tolerates_already_dead():
    proc = _sleeper()
    proc.kill()
    proc.wait()
    track_child(proc)
    try:
        terminate_children(timeout=1)  # must not raise
    finally:
        untrack_child(proc)


def _alive_posix(pid):
    """Running (a reaped or zombie process counts as gone)."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def test_kill_tree_stops_grandchildren_on_linux():
    """The Linux side of the same regression: yt-dlp_linux is a PyInstaller
    onefile launcher too. The child runs in its own process group, and
    kill_tree signals the whole group."""
    import os
    import pytest
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux process groups")
    from sysutil import child_popen_kwargs, kill_tree
    script = ("import subprocess, sys, time\n"
              "c = subprocess.Popen([sys.executable, '-c', "
              "'import time; time.sleep(60)'])\n"
              "print(c.pid, flush=True)\n"
              "time.sleep(60)\n")
    parent = subprocess.Popen([sys.executable, "-c", script],
                              stdout=subprocess.PIPE, text=True,
                              **child_popen_kwargs())
    grandchild = int(parent.stdout.readline())
    try:
        assert _alive_posix(grandchild)
        assert os.getpgid(parent.pid) == parent.pid  # leads its own group
        kill_tree(parent)
        parent.wait(timeout=10)
        for _ in range(50):
            if not _alive_posix(grandchild):
                break
            time.sleep(0.1)
        assert not _alive_posix(grandchild)
    finally:
        if _alive_posix(grandchild):
            os.kill(grandchild, 9)


def test_kill_tree_never_signals_the_apps_own_group():
    """A child that shares the app's process group must not get a group
    kill: that would take the app (and the test run) down with it."""
    import pytest
    if sys.platform == "win32":
        pytest.skip("POSIX process groups")
    from sysutil import kill_tree
    proc = _sleeper()  # no new session: same group as this process
    kill_tree(proc)    # only proc itself dies
    proc.wait(timeout=10)
    assert proc.poll() is not None


def test_child_popen_kwargs_per_platform():
    from sysutil import child_popen_kwargs
    kw = child_popen_kwargs()
    if sys.platform == "win32":
        assert "creationflags" in kw
    else:
        assert kw == {"start_new_session": True}


def test_file_uris_round_trip(tmp_path):
    """Copy and paste on Linux speak text/uri-list; odd names survive."""
    import pytest
    if sys.platform == "win32":
        pytest.skip("Linux clipboard (Windows uses CF_HDROP)")
    from sysutil import _file_uris, _paths_from_uris
    names = [tmp_path / "my clip's 50%.mp4", tmp_path / "日本語 #1.png"]
    text = _file_uris([str(p) for p in names])
    assert text.endswith("\r\n") and "%20" in text  # spaces get encoded
    assert _paths_from_uris(text) == [str(p) for p in names]


def test_paths_from_uris_skips_comments_and_remote():
    from sysutil import _paths_from_uris
    text = ("# copied from Dolphin\r\n"
            "file:///home/x/a.mp4\r\n"
            "https://example.com/b.mp4\r\n"
            "file://otherhost/c.mp4\r\n"
            "file://localhost/home/x/d.mp4\r\n")
    assert _paths_from_uris(text) == ["/home/x/a.mp4", "/home/x/d.mp4"]


def test_data_dir_follows_xdg_on_linux(monkeypatch):
    import pytest
    if sys.platform == "win32":
        pytest.skip("XDG")
    import sysutil
    monkeypatch.setenv("XDG_DATA_HOME", "/tmp/xdg-data")
    assert sysutil._data_dir() == "/tmp/xdg-data/LaxyToolbox"
    monkeypatch.delenv("XDG_DATA_HOME")
    assert sysutil._data_dir().endswith("/.local/share/LaxyToolbox")


def test_restore_system_env_puts_back_the_original(monkeypatch):
    """Regression guard: a frozen Linux build's LD_LIBRARY_PATH points at
    the bundle; children (xdg-open, yt-dlp) must get the user's own back."""
    import os
    import pytest
    if sys.platform == "win32":
        pytest.skip("LD_LIBRARY_PATH")
    import sysutil
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/mine")
    sysutil.restore_system_env()
    assert os.environ["LD_LIBRARY_PATH"] == "/opt/mine"
    assert "LD_LIBRARY_PATH_ORIG" not in os.environ
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123")  # no original: drop it
    sysutil.restore_system_env()
    assert "LD_LIBRARY_PATH" not in os.environ
