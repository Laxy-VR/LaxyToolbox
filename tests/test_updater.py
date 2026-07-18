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
    ]))
    tag, page, url, sha, size = got
    assert tag == "v9.9.9" and url == "u2"
    assert sha == "ab" * 32 and size == 123


def test_pick_asset_tolerates_missing_digest():
    got = pick_asset(release([{"name": "App.exe",
                               "browser_download_url": "u", "size": 1}]))
    assert got[3] is None  # unverified download is allowed (as a browser is)


def test_pick_asset_none_without_exe():
    assert pick_asset(release([{"name": "notes.txt",
                                "browser_download_url": "u"}])) is None
    assert pick_asset(release([])) is None


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
