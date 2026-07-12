"""End-to-end smoke tests against a real ffmpeg.

The unit tests prove the commands are built right; these prove the pinned
ffmpeg actually accepts them. This is the class of bug unit tests can't catch
and that has shipped twice (essentials build missing libsvtav1 in v1.0, the
NVENC/driver mismatch behind the 7.1.1 pin). Skipped when no ffmpeg is on
PATH, so `pytest -q` still works on a bare machine; CI always provides one.
"""

import os
import shutil
import subprocess
import threading

import pytest

from encoder import build_stages, build_gif_stages, run_encode
from probe import FFMPEG, probe_video, _encoders_list

pytestmark = pytest.mark.skipif(
    shutil.which(FFMPEG) is None and not os.path.isabs(FFMPEG),
    reason="ffmpeg not available on PATH")


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """A 1s test clip with a non-ASCII name (locks in UTF-8 log handling)."""
    path = str(tmp_path_factory.mktemp("smoke") / "clip_강남_café.mp4")
    cmd = [FFMPEG, "-y",
           "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=1",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
           "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
           "-shortest", path]
    subprocess.run(cmd, capture_output=True, check=True)
    return path


def _settings(**over):
    s = {"codec": "h265", "encoder": "cpu", "crf": 28, "preset": "ultrafast",
         "target_height": None, "target_fps": None,
         "audio_mode": "copy", "audio_bitrate": "128k"}
    s.update(over)
    return s


def test_probe_reads_generated_clip(clip):
    info = probe_video(clip)
    assert info.width == 320 and info.height == 240
    assert info.duration == pytest.approx(1.0, abs=0.2)
    assert info.video_codec == "h264" and info.audio_codec == "aac"


def test_h265_encode_end_to_end(clip):
    out = os.path.join(os.path.dirname(clip), "out_h265.mp4")
    for _label, cmd in build_stages(clip, out, _settings(), "quality"):
        code, tail = run_encode(cmd, 1.0, lambda *a: None, threading.Event())
        assert code == 0, "\n".join(tail)
    assert os.path.getsize(out) > 0
    assert probe_video(out).video_codec == "hevc"


def test_bundled_ffmpeg_has_cpu_av1():
    """Regression for the v1.0 release bug: the essentials ffmpeg build lacks
    libsvtav1, silently breaking CPU AV1. Dev and CI must use the full build
    (see DEVELOPMENT.md); this fails loudly if the wrong build sneaks in."""
    assert "libsvtav1" in _encoders_list()


def test_av1_encode_end_to_end(clip):
    out = os.path.join(os.path.dirname(clip), "out_av1.mp4")
    stages = build_stages(clip, out, _settings(codec="av1", preset="ultrafast"),
                          "quality")
    for _label, cmd in stages:
        code, tail = run_encode(cmd, 1.0, lambda *a: None, threading.Event())
        assert code == 0, "\n".join(tail)
    assert probe_video(out).video_codec == "av1"


def _run(stages):
    for _label, cmd in stages:
        code, tail = run_encode(cmd, 1.0, lambda *a: None, threading.Event())
        assert code == 0, "\n".join(tail)


@pytest.mark.parametrize("fmt,ext", [("gif", ".gif"), ("webp", ".webp"),
                                     ("mp4", "_loop.mp4")])
def test_loop_formats_end_to_end(clip, fmt, ext):
    out = os.path.join(os.path.dirname(clip), f"loop_{fmt}{ext}")
    _run(build_gif_stages(clip, out, {"target_fps": 10, "target_height": 120,
                                      "gif_format": fmt}, segment=(0, 0.5)))
    assert os.path.getsize(out) > 0
    if fmt != "webp":  # ffprobe reads no duration from animated webp
        assert probe_video(out).duration == pytest.approx(0.5, abs=0.2)


def test_boomerang_speed_gif_end_to_end(clip):
    """Regression: a 1s clip at 2x speed, boomeranged, must come out ~1s
    (0.5s forward + 0.5s back). The output-side -t bug truncated the bounce
    away entirely."""
    out = os.path.join(os.path.dirname(clip), "boom.gif")
    _run(build_gif_stages(clip, out, {"target_fps": 10, "target_height": 120,
                                      "gif_format": "gif", "gif_speed": 2.0,
                                      "gif_direction": "boomerang",
                                      "gif_colors": 64}, segment=(0, 1)))
    assert os.path.getsize(out) > 0
    assert probe_video(out).duration == pytest.approx(1.0, abs=0.25)


def test_rotate_and_subtitles_end_to_end(clip):
    """Rotate swaps the frame and the burn-in filter accepts a Windows path."""
    srt = os.path.join(os.path.dirname(clip), "burn me's.srt")
    with open(srt, "w", encoding="utf-8") as f:
        f.write("1\n00:00:00,000 --> 00:00:01,000\nhello\n")
    out = os.path.join(os.path.dirname(clip), "out_rot_subs.mp4")
    _run(build_stages(clip, out, _settings(rotate="transpose=1",
                                           subtitles=srt), "quality"))
    info = probe_video(out)
    assert (info.width, info.height) == (240, 320)  # 320x240 rotated 90°
