"""Tests for the yt-dlp download helpers (pure logic, no network)."""

import pytest

import os
import time

from downloader import (parse_progress, looks_like_url, build_dl_command,
                        newest_media_file)


@pytest.mark.parametrize("line,expected", [
    ("[download]  42.3% of  120.5MiB at 8.2MiB/s ETA 00:12", 0.423),
    ("[download] 100% of 120MiB in 00:15", 1.0),
    ("[download]   0.0% of ~5MiB", 0.0),
])
def test_parse_progress(line, expected):
    assert parse_progress(line) == pytest.approx(expected)


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


def test_newest_media_file_none_when_nothing_new(tmp_path):
    old = tmp_path / "old.mp4"
    old.write_bytes(b"x")
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    assert newest_media_file(str(tmp_path), since=time.time() - 60) is None
    assert newest_media_file(str(tmp_path / "missing"), since=0) is None
