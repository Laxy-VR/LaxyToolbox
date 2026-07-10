"""Tests for pure helpers in the app layer (no window is created)."""

import pytest

from app import human_size, status_display, Job, App
from models import unique_path
from probe import VideoInfo


@pytest.mark.parametrize("num,expected", [
    (0, "unknown"),
    (None, "unknown"),
    (512, "512.0 B"),
    (1536, "1.5 KB"),
    (5 * 1024 * 1024, "5.0 MB"),
])
def test_human_size(num, expected):
    assert human_size(num) == expected


@pytest.mark.parametrize("seconds,expected", [
    (5, "5s"),
    (45, "45s"),
    (125, "2m 5s"),
    (3725, "1h 2m"),
])
def test_fmt_eta(seconds, expected):
    assert App._fmt_eta(seconds) == expected


def _job(status, **kw):
    j = Job(id=0, path="clip.mp4")
    j.status = status
    for k, v in kw.items():
        setattr(j, k, v)
    return j


def test_status_display_basic():
    assert status_display(_job("ready"))[0] == "ready"
    assert status_display(_job("failed"))[0] == "failed"
    assert status_display(_job("encoding", progress=0.42))[0] == "encoding 42%"


@pytest.mark.parametrize("latest,current,newer", [
    ("v1.1", "1.0", True),
    ("1.0.1", "1.0", True),
    ("v1.0", "1.0", False),
    ("0.9", "1.0", False),
    ("v2.0-beta", "1.9", True),
    ("", "1.0", False),
])
def test_is_newer_version(latest, current, newer):
    from sysutil import is_newer_version
    assert is_newer_version(latest, current) is newer


def test_unique_path_no_collision():
    used = set()
    assert unique_path(r"C:\out\a_h265.mp4", used) == r"C:\out\a_h265.mp4"
    # same planned output from a second source file gets suffixed
    assert unique_path(r"C:\out\a_h265.mp4", used) == r"C:\out\a_h265_2.mp4"
    assert unique_path(r"C:\out\a_h265.mp4", used) == r"C:\out\a_h265_3.mp4"
    # different name is untouched
    assert unique_path(r"C:\out\b_h265.mp4", used) == r"C:\out\b_h265.mp4"


def test_unique_path_case_insensitive_on_windows():
    used = set()
    unique_path(r"C:\out\A_h265.mp4", used)
    assert unique_path(r"C:\out\a_h265.mp4", used).endswith("_2.mp4")


def test_status_display_savings_and_over_limit():
    info = VideoInfo("clip.mp4", 1920, 1080, 60, 30, "h264", "aac", 5_000_000, 100)
    done = _job("done", info=info, out_size=35, outputs=["a.mp4"])
    assert "65% smaller" in status_display(done)[0]

    over = _job("done", info=info, out_size=600, outputs=["a.mp4"],
                limit_mb=500, over_limit=True)
    assert status_display(over)[0] == "done · over limit!"
