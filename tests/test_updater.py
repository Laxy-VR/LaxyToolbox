"""Tests for the in-app updater: asset picking, verified downloads, and the
rename swap with rollback."""

import hashlib
import threading

from updater import pick_asset, download, apply, sweep_leftovers


def release(assets):
    return {"tag_name": "v9.9.9", "html_url": "https://example/rel",
            "assets": assets}


# ---------- pick_asset ----------
def test_pick_asset_finds_exe_and_digest():
    got = pick_asset(release([
        {"name": "Source.zip", "browser_download_url": "u1", "size": 5},
        {"name": "Laxy.Toolbox.exe", "browser_download_url": "u2",
         "digest": "sha256:" + "ab" * 32, "size": 123},
    ]), system="win32")
    tag, page, url, sha, size = got
    assert tag == "v9.9.9" and url == "u2"
    assert sha == "ab" * 32 and size == 123


def test_pick_asset_tolerates_missing_digest():
    got = pick_asset(release([{"name": "App.exe",
                               "browser_download_url": "u", "size": 1}]),
                     system="win32")
    assert got[3] is None  # unverified download is allowed (as a browser is)


def test_pick_asset_none_without_exe():
    assert pick_asset(release([{"name": "notes.txt",
                                "browser_download_url": "u"}]),
                      system="win32") is None
    assert pick_asset(release([]), system="win32") is None


def _both_builds():
    return release([
        {"name": "Laxy.Toolbox.exe", "browser_download_url": "win",
         "digest": "sha256:" + "aa" * 32, "size": 139},
        {"name": "Laxy.Toolbox-x86_64.AppImage", "browser_download_url": "lin",
         "digest": "sha256:" + "bb" * 32, "size": 120},
    ])


def test_pick_asset_takes_each_platforms_own_build():
    """A release carries both builds; Windows must never install the
    AppImage and Linux never the exe."""
    assert pick_asset(_both_builds(), system="win32")[2] == "win"
    linux = pick_asset(_both_builds(), system="linux")
    assert linux[2] == "lin" and linux[3] == "bb" * 32


def test_pick_asset_linux_ignores_an_exe_only_release():
    """Releases before 1.8.0 had no AppImage: no update offered on Linux."""
    assert pick_asset(release([{"name": "Laxy.Toolbox.exe",
                                "browser_download_url": "u"}]),
                      system="linux") is None


def test_exe_path_is_the_appimage_file_on_linux(monkeypatch):
    """Inside an AppImage sys.executable is in the read-only mount; the swap
    must target the AppImage file the runtime names in $APPIMAGE."""
    import sys
    import pytest
    if sys.platform == "win32":
        pytest.skip("AppImage")
    from updater import exe_path
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("APPIMAGE", "/home/x/Laxy.Toolbox-x86_64.AppImage")
    assert exe_path() == "/home/x/Laxy.Toolbox-x86_64.AppImage"
    monkeypatch.delenv("APPIMAGE")  # a plain PyInstaller folder: no self-update
    assert exe_path() is None


def test_apply_makes_the_new_build_executable(tmp_path):
    import os
    import sys
    import pytest
    if sys.platform == "win32":
        pytest.skip("execute bit")
    current = tmp_path / "app.AppImage"
    current.write_bytes(b"OLD")
    new = tmp_path / "app.AppImage.new"
    new.write_bytes(b"NEW")  # a fresh download: not executable
    assert apply(str(new), current=str(current)) is None
    assert os.access(current, os.X_OK) and current.read_bytes() == b"NEW"


# ---------- download ----------
def _file_url(path):
    return "file:///" + str(path).replace("\\", "/")


def test_download_verifies_sha256(tmp_path):
    src = tmp_path / "new.bin"
    src.write_bytes(b"new exe bytes")
    dest = tmp_path / "out.bin"
    sha = hashlib.sha256(b"new exe bytes").hexdigest()
    assert download(_file_url(src), str(dest), sha256=sha) is None
    assert dest.read_bytes() == b"new exe bytes"
    assert not (tmp_path / "out.bin.part").exists()


def test_download_rejects_bad_checksum(tmp_path):
    src = tmp_path / "new.bin"
    src.write_bytes(b"tampered")
    dest = tmp_path / "out.bin"
    err = download(_file_url(src), str(dest), sha256="00" * 32)
    assert err and "checksum" in err
    assert not dest.exists() and not (tmp_path / "out.bin.part").exists()


def test_download_cancel(tmp_path):
    src = tmp_path / "new.bin"
    src.write_bytes(b"x" * 1000)
    ev = threading.Event()
    ev.set()
    err = download(_file_url(src), str(tmp_path / "out.bin"), cancel=ev)
    assert err == "cancelled"
    assert not (tmp_path / "out.bin").exists()


class _TruncatedResponse:
    """A response whose connection drops after 1000 of 100000 bytes."""
    headers = {"Content-Length": "100000"}

    def __init__(self):
        self._chunks = [b"MZ" + b"\0" * 998]

    def read(self, _n=-1):
        return self._chunks.pop() if self._chunks else b""

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def test_download_rejects_truncated_body(tmp_path, monkeypatch):
    """Regression: a dropped connection just ends the stream early; with no
    digest to compare, the partial exe was accepted and would be installed."""
    import updater
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda *a, **k: _TruncatedResponse())
    dest = tmp_path / "app.exe.new"
    err = download("https://example/app.exe", str(dest), sha256=None)
    assert err and "incomplete" in err
    assert not dest.exists() and not (tmp_path / "app.exe.new.part").exists()


# ---------- apply (the rename swap) ----------
def test_apply_swaps_and_keeps_old(tmp_path):
    current = tmp_path / "app.exe"
    current.write_bytes(b"OLD")
    new = tmp_path / "app.exe.new"
    new.write_bytes(b"NEW")
    assert apply(str(new), current=str(current)) is None
    assert current.read_bytes() == b"NEW"
    # .old survives until the next startup, so a bad exe can be rolled back
    assert (tmp_path / "app.exe.old").read_bytes() == b"OLD"
    assert not new.exists()


def test_apply_rolls_back_when_new_is_missing(tmp_path):
    current = tmp_path / "app.exe"
    current.write_bytes(b"OLD")
    err = apply(str(tmp_path / "ghost.new"), current=str(current))
    assert err and "install" in err
    assert current.read_bytes() == b"OLD"  # the working exe came back


def test_apply_refuses_dev_runs():
    assert "packaged" in apply("whatever")  # not frozen: no exe_path


def test_sweep_leftovers(tmp_path):
    current = tmp_path / "app.exe"
    current.write_bytes(b"APP")
    for suffix in (".old", ".new", ".new.part"):
        (tmp_path / f"app.exe{suffix}").write_bytes(b"junk")
    sweep_leftovers(current=str(current))
    assert current.exists()
    for suffix in (".old", ".new", ".new.part"):
        assert not (tmp_path / f"app.exe{suffix}").exists()
