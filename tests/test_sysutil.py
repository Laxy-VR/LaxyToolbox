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
