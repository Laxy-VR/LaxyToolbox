"""Tests for the yt-dlp download helpers (pure logic, no network)."""

import pytest

import os
import time

from downloader import (parse_progress, parse_item, looks_like_url,
                        build_dl_command, newest_media_file, media_files_since)


@pytest.mark.parametrize("line,expected", [
    ("[download]  42.3% of  120.5MiB at 8.2MiB/s ETA 00:12", 0.423),
    ("[download] 100% of 120MiB in 00:15", 1.0),
    ("[download]   0.0% of ~5MiB", 0.0),
])
def test_parse_progress(line, expected):
    assert parse_progress(line) == pytest.approx(expected)


@pytest.mark.parametrize("line,expected", [
    ("[download] Downloading item 3 of 12", (3, 12)),
    ("[download] Downloading video 1 of 5", (1, 5)),
    ("[download]  42.3% of 120MiB", None),
    ("[youtube] abc: Downloading webpage", None),
])
def test_parse_item(line, expected):
    assert parse_item(line) == expected


@pytest.mark.parametrize("line", [
    "[youtube] abc123: Downloading webpage",
    "[Merger] Merging formats into \"clip.mp4\"",
    "C:\\Users\\x\\Downloads\\clip.mp4",
    "",
])
def test_parse_progress_ignores_other_lines(line):
    assert parse_progress(line) is None


@pytest.mark.parametrize("text,ok", [
    ("https://www.youtube.com/watch?v=abc", True),
    ("http://x.com/user/status/1", True),
    ("  https://youtu.be/abc  ", True),
    ("youtube.com/watch?v=abc", False),   # no scheme
    ("not a link", False),
    ("", False),
    (None, False),
])
def test_looks_like_url(text, ok):
    assert looks_like_url(text) is ok


def test_build_dl_command_defaults():
    cmd = " ".join(build_dl_command("https://u", "tmpl"))
    assert "--no-playlist" in cmd and "--merge-output-format mp4" in cmd
    assert "--progress" in cmd            # --print implies quiet; must force it back
    assert "after_move:filepath" in cmd
    assert "-S" not in cmd                # no resolution cap by default
    # Regression: a machine-local yt-dlp config (e.g. "-f worst") must never
    # hijack the app's downloads into low quality.
    assert "--ignore-config" in cmd


def test_build_dl_command_playlist():
    cmd = " ".join(build_dl_command("https://u", "tmpl", playlist=True))
    assert "--yes-playlist" in cmd and "--no-playlist" not in cmd
    # default stays single-video
    cmd = " ".join(build_dl_command("https://u", "tmpl"))
    assert "--no-playlist" in cmd and "--yes-playlist" not in cmd


def test_build_dl_command_resolution_cap():
    cmd = " ".join(build_dl_command("https://u", "tmpl", max_height=1080))
    assert "-S res:1080" in cmd


def test_build_dl_command_audio_only():
    cmd = " ".join(build_dl_command("https://u", "tmpl", audio_only=True))
    assert "-x" in cmd and "--audio-format mp3" in cmd
    assert "--merge-output-format" not in cmd


def test_build_dl_command_points_ytdlp_at_bundled_ffmpeg(monkeypatch):
    """Regression: without ffmpeg, yt-dlp cannot merge HD streams and YouTube
    degrades to the lone 360p pre-merged format. The app must hand yt-dlp its
    bundled ffmpeg instead of relying on the user's PATH."""
    import downloader as dl
    monkeypatch.setattr(dl, "FFMPEG", r"C:\bundle\ffmpeg.exe")
    cmd = " ".join(dl.build_dl_command("https://u", "tmpl"))
    assert r"--ffmpeg-location C:\bundle" in cmd
    # dev mode (bare name from PATH): no flag, let yt-dlp search normally
    monkeypatch.setattr(dl, "FFMPEG", "ffmpeg")
    cmd = " ".join(dl.build_dl_command("https://u", "tmpl"))
    assert "--ffmpeg-location" not in cmd


def test_build_dl_command_cookies():
    cmd = " ".join(build_dl_command("https://u", "tmpl", cookies_browser="firefox"))
    assert "--cookies-from-browser firefox" in cmd
    cmd = " ".join(build_dl_command("https://u", "tmpl"))
    assert "--cookies-from-browser" not in cmd  # opt-in only


def test_newest_media_file_fallback(tmp_path):
    """Regression: unicode titles mangle yt-dlp's printed path, so a completed
    download must be locatable by timestamp instead."""
    old = tmp_path / "old.mp4"
    old.write_bytes(b"x")
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    (tmp_path / "ignore.part").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")
    fresh = tmp_path / "PSY - GANGNAM STYLE(강남스타일).mp4"
    fresh.write_bytes(b"x")

    found = newest_media_file(str(tmp_path), since=time.time() - 60)
    assert found == str(fresh)


def test_media_files_since_returns_all_sorted(tmp_path):
    """A playlist download reconciles against every file that landed, so the
    sweep must return them all, oldest first."""
    now = time.time()

    def make(name, age):
        p = tmp_path / name
        p.write_bytes(b"x")
        os.utime(p, (now - age, now - age))
        return p

    a = make("1.mp4", 30)
    b = make("2.mp4", 20)
    c = make("3.m4a", 10)
    make("old.mp4", 3600)
    (tmp_path / "part.part").write_bytes(b"x")  # unfinished, ignored
    got = media_files_since(str(tmp_path), since=now - 60)
    assert got == [str(a), str(b), str(c)]


def test_update_if_stale_threshold(monkeypatch, tmp_path):
    import downloader as dl
    fake = tmp_path / "yt-dlp.exe"
    fake.write_bytes(b"x")
    monkeypatch.setattr(dl, "YTDLP_PATH", str(fake))
    ran = []
    monkeypatch.setattr(dl, "update_ytdlp", lambda: ran.append(1))
    # fresh copy: no update
    assert dl.update_ytdlp_if_stale(max_age_days=7) is False and not ran
    # stale copy: update runs
    os.utime(fake, (time.time() - 10 * 86400,) * 2)
    assert dl.update_ytdlp_if_stale(max_age_days=7) is True and ran


def test_downloaded_status_shows_resolution():
    from models import Job, status_display
    from probe import VideoInfo
    j = Job(id=0, path="clip.mp4")
    j.status = "downloaded"
    j.info = VideoInfo("clip.mp4", 640, 360, 60, 30, "h264", "aac", 1, 1)
    assert status_display(j)[0] == "downloaded ✓ · 360p"


# ---------- verified yt-dlp fetch ----------
class _FakeResponse:
    def __init__(self, body, length=None):
        self._body = body
        self.headers = {"Content-Length": str(len(body) if length is None else length)}

    def read(self, n=-1):
        if n is None or n < 0:
            n = len(self._body)
        chunk, self._body = self._body[:n], self._body[n:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _serve_release(monkeypatch, tmp_path, exe_body, sums_hash=None, exe_length=None):
    import hashlib
    import downloader as dl
    monkeypatch.setattr(dl, "APPDATA_DIR", str(tmp_path))
    monkeypatch.setattr(dl, "YTDLP_PATH", str(tmp_path / "yt-dlp.exe"))
    digest = sums_hash or hashlib.sha256(exe_body).hexdigest()
    sums = f"{'0' * 64}  yt-dlp_linux\n{digest}  yt-dlp.exe\n".encode()

    def urlopen(url, timeout=None):
        if url == dl.YTDLP_SUMS_URL:
            return _FakeResponse(sums)
        return _FakeResponse(exe_body, exe_length)
    monkeypatch.setattr(dl.urllib.request, "urlopen", urlopen)
    return dl


def test_fetch_ytdlp_keeps_a_verified_download(monkeypatch, tmp_path):
    dl = _serve_release(monkeypatch, tmp_path, b"MZ real exe")
    dl.fetch_ytdlp()
    assert (tmp_path / "yt-dlp.exe").read_bytes() == b"MZ real exe"


def test_fetch_ytdlp_rejects_a_truncated_download(monkeypatch, tmp_path):
    """Regression: a cut off download was kept as yt-dlp.exe, and since
    has_ytdlp only checks existence, every later download failed with it."""
    dl = _serve_release(monkeypatch, tmp_path, b"MZ partial", exe_length=100000)
    with pytest.raises(RuntimeError, match="cut off"):
        dl.fetch_ytdlp()
    assert not dl.has_ytdlp() and not list(tmp_path.iterdir())


def test_fetch_ytdlp_rejects_a_checksum_mismatch(monkeypatch, tmp_path):
    dl = _serve_release(monkeypatch, tmp_path, b"MZ tampered", sums_hash="ab" * 32)
    with pytest.raises(RuntimeError, match="checksum"):
        dl.fetch_ytdlp()
    assert not dl.has_ytdlp()


@pytest.mark.skipif(os.name != "nt", reason="Windows exe loading")
def test_download_reports_and_removes_a_damaged_exe(monkeypatch, tmp_path):
    import threading
    import downloader as dl
    bad = tmp_path / "yt-dlp.exe"
    bad.write_bytes(b"MZ" + b"\0" * 998)
    monkeypatch.setattr(dl, "YTDLP_PATH", str(bad))
    monkeypatch.setattr(dl, "DL_LOG_PATH", str(tmp_path / "log.txt"))
    out = tmp_path / "out"
    paths, err = dl.download("https://u", str(out), lambda f: None, threading.Event())
    assert (paths, err) == ([], dl.DAMAGED)
    assert not bad.exists()
    assert os.listdir(out) == []  # the staging folder is gone too


def test_damaged_ytdlp_is_refetched_not_self_updated(monkeypatch, tmp_path):
    """A broken exe can't run -U, so the retry must fetch a fresh copy."""
    import threading
    import downloader as dl
    calls = []
    results = iter([([], dl.DAMAGED), (["C:/out/clip.mp4"], None)])
    monkeypatch.setattr(dl, "download", lambda *a, **k: next(results))
    monkeypatch.setattr(dl, "fetch_ytdlp", lambda: calls.append("fetch"))
    monkeypatch.setattr(dl, "update_ytdlp", lambda: calls.append("update"))
    paths, err = dl.download_with_update_retry("https://u", str(tmp_path),
                                               lambda f: None, threading.Event())
    assert calls == ["fetch"] and paths == ["C:/out/clip.mp4"] and err is None


# ---------- staging folder + cancel (a fake yt-dlp in Python) ----------
def _fake_ytdlp(monkeypatch, tmp_path, script):
    import sys
    import downloader as dl

    def command(url, template, *a, **k):
        return [sys.executable, "-c", script, os.path.dirname(template)]
    monkeypatch.setattr(dl, "build_dl_command", command)
    monkeypatch.setattr(dl, "DL_LOG_PATH", str(tmp_path / "log.txt"))
    return dl


def test_download_never_adopts_unrelated_files(monkeypatch, tmp_path):
    """Regression: the sweep for mangled (non-ASCII) paths claimed ANY media
    file that changed in the output folder, e.g. a browser download."""
    import threading
    out = tmp_path / "Downloads"
    out.mkdir()
    (out / "browser_download.mp4").write_bytes(b"not ours")
    script = ("import os, sys\n"
              "p = os.path.join(sys.argv[1], 'clip_\\uac15.mp4')\n"
              "open(p, 'wb').write(b'video')\n"
              "print('C:/mangled/clip_?.mp4')\n")  # what the frozen exe prints
    dl = _fake_ytdlp(monkeypatch, tmp_path, script)
    paths, err = dl.download("https://u", str(out), lambda f: None, threading.Event())
    assert err is None
    assert paths == [str(out / "clip_강.mp4")]
    assert sorted(os.listdir(out)) == ["browser_download.mp4", "clip_강.mp4"]


def test_cancel_stops_a_silent_download_and_removes_partials(monkeypatch, tmp_path):
    """Regression: cancel was only noticed when yt-dlp printed a line, so a
    long silent merge ignored it; partial files were left behind too."""
    import threading
    out = tmp_path / "Downloads"
    out.mkdir()
    script = ("import os, sys, time\n"
              "open(os.path.join(sys.argv[1], 'clip.mp4.part'), 'wb').write(b'half')\n"
              "print('[download]  10.0% of 5MiB', flush=True)\n"
              "time.sleep(60)\n")  # silent from here on, like a long merge
    dl = _fake_ytdlp(monkeypatch, tmp_path, script)
    cancel = threading.Event()
    started = time.time()
    paths, err = dl.download("https://u", str(out),
                             lambda _f: threading.Timer(0.5, cancel.set).start(),
                             cancel)
    assert (paths, err) == ([], "cancelled")
    assert time.time() - started < 20
    assert os.listdir(out) == []


def test_newest_media_file_none_when_nothing_new(tmp_path):
    old = tmp_path / "old.mp4"
    old.write_bytes(b"x")
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    assert newest_media_file(str(tmp_path), since=time.time() - 60) is None
    assert newest_media_file(str(tmp_path / "missing"), since=0) is None
