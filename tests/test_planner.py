"""Tests for planner.plan_job: the job -> ffmpeg stage planning logic."""

import pytest

from models import (Job, MODE_QUALITY, MODE_TARGET, MODE_SPLIT, MODE_GIF,
                    MODE_IMAGE, MODE_AUDIO)
from planner import (plan_job, trimmed_duration, resolve_subtitles,
                     estimate_output_bytes)
from probe import VideoInfo


def make_job(path="in.mp4", outputs=None, duration=60.0, width=1920,
             height=1080, fps=30.0):
    j = Job(id=1, path=path)
    j.info = VideoInfo(path, width, height, duration, fps, "h264", "aac",
                       8_000_000, 60_000_000)
    j.outputs = outputs or ["out.mp4"]
    return j


def settings(**over):
    s = {"codec": "h265", "encoder": "cpu", "crf": 22, "preset": "veryfast",
         "target_height": None, "target_fps": None,
         "audio_mode": "copy", "audio_bitrate": "128k", "trim": None,
         "gif_dither": "none", "img_format": "webp", "img_quality": "balanced",
         "img_resize": None, "aud_format": "mp3", "aud_bitrate": "192k"}
    s.update(over)
    return s


# ---------- trimmed_duration ----------
@pytest.mark.parametrize("duration,trim,expected", [
    (60.0, None, 60.0),
    (60.0, (10.0, 40.0), 30.0),
    (60.0, (10.0, None), 50.0),      # open end = to the end of the file
    (60.0, (10.0, 999.0), 50.0),     # end clamped to the source
    (60.0, (999.0, None), pytest.approx(0.1)),  # start clamped near the end
    (0.0, (10.0, 40.0), 30.0),       # unknown duration: trust the trim
])
def test_trimmed_duration(duration, trim, expected):
    assert trimmed_duration(duration, trim) == expected


# ---------- per-mode planning ----------
def test_quality_mode_single_stage():
    stages, passlogs, reason = plan_job(make_job(), MODE_QUALITY, settings(), None)
    assert reason is None and passlogs == []
    assert len(stages) == 1
    label, cmd, dur = stages[0]
    assert label == "encode" and dur == 60.0
    assert "libx265" in " ".join(cmd)


def test_target_mode_two_pass_with_passlog():
    stages, passlogs, reason = plan_job(make_job(), MODE_TARGET, settings(), 100)
    assert reason is None and len(passlogs) == 1
    assert [lbl for lbl, _c, _d in stages] == ["analyze", "encode"]


def test_target_too_small_fails_with_reason():
    stages, _p, reason = plan_job(make_job(duration=3600), MODE_TARGET,
                                  settings(), 1)
    assert stages is None and "too small" in reason


def test_split_mode_stage_per_part():
    job = make_job(outputs=["p1.mp4", "p2.mp4", "p3.mp4"], duration=300)
    stages, passlogs, reason = plan_job(job, MODE_SPLIT, settings(), 100)
    assert reason is None and len(passlogs) == 3
    assert len(stages) == 6  # x265 2-pass per part
    assert stages[0][0].startswith("part 1")
    # segments tile the whole file: each part is 100s
    assert all(d == pytest.approx(100.0) for _l, _c, d in stages)


def test_trim_limits_the_encode():
    stages, _p, reason = plan_job(make_job(), MODE_QUALITY,
                                  settings(trim=(10.0, 40.0)), None)
    assert reason is None
    cmd = " ".join(stages[0][1])
    assert "-ss 10.000" in cmd and "-t 30.000" in cmd
    assert stages[0][2] == 30.0


def test_trim_past_end_clamps_to_tail():
    """A trim starting past the end is clamped to a sliver at the tail rather
    than failing (the UI validates the numbers; the planner stays lenient)."""
    stages, _p, reason = plan_job(make_job(duration=20), MODE_QUALITY,
                                  settings(trim=(50.0, 60.0)), None)
    assert reason is None
    assert "-ss 19.900" in " ".join(stages[0][1])
    assert stages[0][2] == pytest.approx(0.1)


def test_cut_only_stream_copies():
    stages, _p, reason = plan_job(
        make_job(), MODE_QUALITY,
        settings(cut_only=True, trim=(5.0, 15.0)), None)
    assert reason is None
    cmd = " ".join(stages[0][1])
    assert "-c copy" in cmd and "libx265" not in cmd


def test_gif_start_past_end_clamps_to_tail():
    stages, _p, reason = plan_job(make_job(duration=5),
                                  MODE_GIF, settings(gif_start=10.0, gif_len=5.0),
                                  None)
    assert reason is None
    assert "-ss 4.900" in " ".join(stages[0][1])  # clamped near the end
    assert stages[0][2] == pytest.approx(0.1)


def test_gif_zero_length_fails():
    stages, _p, reason = plan_job(make_job(duration=5),
                                  MODE_GIF, settings(gif_start=0.0, gif_len=0.0),
                                  None)
    assert stages is None and "past the end" in reason


def test_gif_clip_clamped_to_source():
    stages, _p, reason = plan_job(make_job(duration=6),
                                  MODE_GIF, settings(gif_start=2.0, gif_len=30.0),
                                  None)
    assert reason is None
    assert stages[0][2] == pytest.approx(4.0)  # length clamped to what's left


def test_image_and_audio_modes_single_stage():
    stages, _p, reason = plan_job(make_job("in.png", outputs=["out.webp"]),
                                  MODE_IMAGE, settings(), None)
    assert reason is None and "libwebp" in " ".join(stages[0][1])
    stages, _p, reason = plan_job(make_job(outputs=["out.mp3"]),
                                  MODE_AUDIO, settings(), None)
    assert reason is None and "libmp3lame" in " ".join(stages[0][1])


def test_target_mode_copy_audio_becomes_aac():
    """Size targeting needs a known audio bitrate, so 'copy' must be replaced."""
    stages, _p, reason = plan_job(make_job(), MODE_TARGET,
                                  settings(audio_mode="copy"), 100)
    assert reason is None
    final = " ".join(stages[-1][1])
    assert "-c:a aac" in final and "-c:a copy" not in final


def test_gif_stage_duration_tracks_speed_and_boomerang():
    """Progress scales to the OUTPUT timeline: 4s at 2x boomeranged is 4s."""
    stages, _p, reason = plan_job(
        make_job(outputs=["out.gif"]), MODE_GIF,
        settings(gif_start=0.0, gif_len=4.0, gif_speed=2.0,
                 gif_direction="boomerang"), None)
    assert reason is None
    assert stages[0][2] == pytest.approx(4.0)  # 4 / 2 * 2


# ---------- subtitles ----------
def test_resolve_subtitles(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    srt = tmp_path / "clip.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    assert resolve_subtitles({"subs_mode": "auto"}, str(video)) == str(srt)
    assert resolve_subtitles({"subs_mode": "auto"},
                             str(tmp_path / "other.mp4")) is None
    assert resolve_subtitles({"subs_mode": "file", "subs_path": str(srt)},
                             str(video)) == str(srt)
    assert resolve_subtitles({"subs_mode": "file", "subs_path": None},
                             str(video)) is None
    assert resolve_subtitles({"subs_mode": "none"}, str(video)) is None


def test_plan_job_burns_matching_subtitles(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    (tmp_path / "clip.srt").write_text("1\n")
    job = make_job(path=str(video))
    stages, _p, reason = plan_job(job, MODE_QUALITY,
                                  settings(subs_mode="auto"), None)
    assert reason is None
    assert "subtitles=filename=" in " ".join(stages[0][1])
    # and no subtitle filter when nothing matches
    job2 = make_job(path=str(tmp_path / "lonely.mp4"))
    stages, _p, _r = plan_job(job2, MODE_QUALITY,
                              settings(subs_mode="auto"), None)
    assert "subtitles=" not in " ".join(stages[0][1])


# ---------- output size estimates ----------
def _info(**kw):
    d = dict(width=1920, height=1080, duration=60.0, fps=30.0)
    d.update(kw)
    return VideoInfo("clip.mp4", d["width"], d["height"], d["duration"],
                     d["fps"], "h264", "aac", 8_000_000, 60_000_000)


def test_estimate_quality_mode_scales_with_crf():
    low = estimate_output_bytes(_info(), MODE_QUALITY, settings(crf=28))
    high = estimate_output_bytes(_info(), MODE_QUALITY, settings(crf=18))
    assert 0 < low < high


def test_estimate_target_and_split():
    est = estimate_output_bytes(_info(), MODE_TARGET, settings(), size_mb=100)
    assert est == pytest.approx(100 * 1024 * 1024 * 0.95)
    est = estimate_output_bytes(_info(), MODE_SPLIT, settings(), size_mb=100,
                                parts_choice=3)
    assert est == pytest.approx(3 * 100 * 1024 * 1024 * 0.95)


def test_estimate_gif_formats_ordered():
    s = settings(gif_start=0, gif_len=5, gif_speed=1.0, gif_direction="forward")
    gif = estimate_output_bytes(_info(), MODE_GIF, dict(s, gif_format="gif"))
    webp = estimate_output_bytes(_info(), MODE_GIF, dict(s, gif_format="webp"))
    mp4 = estimate_output_bytes(_info(), MODE_GIF, dict(s, gif_format="mp4"))
    assert gif > webp > 0 and mp4 > 0 and mp4 < gif


def test_estimate_audio_and_image():
    est = estimate_output_bytes(_info(), MODE_AUDIO,
                                settings(aud_bitrate="192k"))
    assert est == pytest.approx(192 * 1000 * 60 / 8)
    assert estimate_output_bytes(_info(), MODE_IMAGE, settings()) is None


def test_estimate_cut_only_is_proportional():
    est = estimate_output_bytes(_info(), MODE_QUALITY,
                                settings(cut_only=True, trim=(0.0, 30.0)))
    assert est == pytest.approx(30_000_000)  # half the 60 MB source


def test_estimate_handles_bad_gif_input():
    assert estimate_output_bytes(_info(), MODE_GIF,
                                 settings(gif_start="abc", gif_len="5",
                                          gif_format="gif")) is None
