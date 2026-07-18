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


def test_target_mode_tight_cap_uses_two_pass():
    """A cap below what quality needs keeps the precise 2-pass targeting."""
    stages, passlogs, reason = plan_job(make_job(), MODE_TARGET, settings(), 10)
    assert reason is None and len(passlogs) == 1
    assert [lbl for lbl, _c, _d in stages] == ["analyze", "encode"]


def test_target_mode_roomy_cap_uses_capped_quality():
    """Regression (friend's 12 MB video ballooning toward a 500 MB target):
    a roomy cap must encode at constant quality with a VBV ceiling, not
    inflate the file to fill the target."""
    stages, passlogs, reason = plan_job(make_job(), MODE_TARGET, settings(), 500)
    assert reason is None and passlogs == []
    assert len(stages) == 1
    cmd = " ".join(stages[0][1])
    assert "-crf" in cmd and "-maxrate" in cmd and "-bufsize" in cmd
    assert "pass=1" not in cmd
    assert "-c:a copy" in cmd  # roomy cap keeps lossless audio copy too
    # the ceiling is clamped to a sane margin, not the raw (huge) cap,
    # whose doubled bufsize would overflow ffmpeg's 32-bit field
    maxrate = int(cmd.split("-maxrate ")[1].split("k")[0])
    assert maxrate < 100_000


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
    """Tight size targeting needs a known audio bitrate, so 'copy' must be
    replaced (the roomy capped-quality path keeps copy)."""
    stages, _p, reason = plan_job(make_job(), MODE_TARGET,
                                  settings(audio_mode="copy"), 10)
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


def test_auto_crop_applies_when_bars_are_real(monkeypatch):
    import planner
    monkeypatch.setattr(planner, "detect_crop", lambda p, d: (1920, 800, 0, 140))
    stages, _p, reason = plan_job(make_job(), MODE_QUALITY,
                                  settings(crop="auto"), None)
    assert reason is None
    assert "crop=1920:800:0:140" in " ".join(stages[0][1])


def test_auto_crop_rejects_suspect_detections(monkeypatch):
    import planner
    # nearly the full frame (bars under 8 px): not worth a crop
    monkeypatch.setattr(planner, "detect_crop", lambda p, d: (1916, 1076, 2, 2))
    stages, _p, _r = plan_job(make_job(), MODE_QUALITY,
                              settings(crop="auto"), None)
    assert "crop=" not in " ".join(stages[0][1])
    # a tiny area (dark scene fooled the detector): keep the frame whole
    monkeypatch.setattr(planner, "detect_crop", lambda p, d: (320, 180, 800, 450))
    stages, _p, _r = plan_job(make_job(), MODE_QUALITY,
                              settings(crop="auto"), None)
    assert "crop=" not in " ".join(stages[0][1])
    # detection failed entirely: encode proceeds uncropped
    monkeypatch.setattr(planner, "detect_crop", lambda p, d: None)
    stages, _p, reason = plan_job(make_job(), MODE_QUALITY,
                                  settings(crop="auto"), None)
    assert reason is None and "crop=" not in " ".join(stages[0][1])


# ---------- output size estimates ----------
def _info(**kw):
    d = {"width": 1920, "height": 1080, "duration": 60.0, "fps": 30.0}
    d.update(kw)
    return VideoInfo("clip.mp4", d["width"], d["height"], d["duration"],
                     d["fps"], "h264", "aac", 8_000_000, 60_000_000)


def test_estimate_quality_mode_scales_with_crf():
    low = estimate_output_bytes(_info(), MODE_QUALITY, settings(crf=28))
    high = estimate_output_bytes(_info(), MODE_QUALITY, settings(crf=18))
    assert 0 < low < high


def test_estimate_target_and_split():
    # A roomy cap predicts the QUALITY size (the file is not inflated)...
    quality = estimate_output_bytes(_info(), MODE_QUALITY, settings())
    est = estimate_output_bytes(_info(), MODE_TARGET, settings(), size_mb=100)
    assert est == pytest.approx(quality)
    # ...while a tight cap predicts the cap itself.
    est = estimate_output_bytes(_info(), MODE_TARGET, settings(), size_mb=5)
    assert est == pytest.approx(5 * 1024 * 1024 * 0.95)
    est = estimate_output_bytes(_info(), MODE_SPLIT, settings(), size_mb=100,
                                parts_choice=3)
    assert est == pytest.approx(3 * 100 * 1024 * 1024 * 0.95)


def test_estimate_gif_formats_ordered():
    s = settings(gif_start=0, gif_len=5, gif_speed=1.0, gif_direction="forward")
    gif = estimate_output_bytes(_info(), MODE_GIF, dict(s, gif_format="gif"))
    webp = estimate_output_bytes(_info(), MODE_GIF, dict(s, gif_format="webp"))
    mp4 = estimate_output_bytes(_info(), MODE_GIF, dict(s, gif_format="mp4"))
    assert gif > webp > 0 and mp4 > 0 and mp4 < gif


def test_estimate_gif_uses_its_own_height_cap():
    s = settings(gif_start=0, gif_len=5, gif_speed=1.0, gif_direction="forward")
    full = estimate_output_bytes(_info(), MODE_GIF, s)
    small = estimate_output_bytes(_info(), MODE_GIF, dict(s, gif_height=480))
    # 1080p -> 480p is (480/1080)^2 of the pixels
    assert small == pytest.approx(full * (480 / 1080) ** 2, rel=0.01)
    # a cap above the source never inflates the estimate
    same = estimate_output_bytes(_info(height=360, width=640), MODE_GIF, s)
    capped = estimate_output_bytes(_info(height=360, width=640), MODE_GIF,
                                   dict(s, gif_height=480))
    assert capped == pytest.approx(same)
    # and the Compress tab's Resolution setting is ignored for loops
    leak = estimate_output_bytes(_info(), MODE_GIF, dict(s, target_height=240))
    assert leak == pytest.approx(full)


def test_gif_output_dims():
    from planner import gif_output_dims
    # caps: shrink to fit, never upscale
    assert gif_output_dims(1920, 1080, {"gif_height": 480}) == (853, 480)
    assert gif_output_dims(640, 360, {"gif_height": 480}) == (640, 360)
    assert gif_output_dims(1920, 1080, {}) == (1920, 1080)
    # custom: exact, one blank side follows the aspect, and it MAY upscale
    assert gif_output_dims(1920, 1080, {"gif_custom": (400, 300)}) == (400, 300)
    assert gif_output_dims(1920, 1080, {"gif_custom": (400, None)}) == (400, 225)
    assert gif_output_dims(1920, 1080, {"gif_custom": (None, 540)}) == (960, 540)
    assert gif_output_dims(100, 100, {"gif_custom": (None, 128)}) == (128, 128)
    # custom beats a leftover cap; blank custom falls through to the cap
    assert gif_output_dims(1920, 1080, {"gif_custom": (400, 300),
                                        "gif_height": 480}) == (400, 300)
    assert gif_output_dims(1920, 1080, {"gif_custom": (None, None),
                                        "gif_height": 480}) == (853, 480)


def test_estimate_ratio_crops_shrink_the_frame():
    full = estimate_output_bytes(_info(), MODE_QUALITY, settings())
    vertical = estimate_output_bytes(_info(), MODE_QUALITY,
                                     settings(crop="9:16"))
    square = estimate_output_bytes(_info(), MODE_QUALITY, settings(crop="1:1"))
    assert 0 < vertical < square < full
    # auto is unknown until encode time; the estimate stays whole-frame
    auto = estimate_output_bytes(_info(), MODE_QUALITY, settings(crop="auto"))
    assert auto == pytest.approx(full)


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


# ---------- per-file source traits reach the encoder ----------
def test_plan_passes_interlace_and_track_count():
    job = make_job()
    job.info.field_order = "tt"
    job.info.audio_tracks = 3
    stages, _p, reason = plan_job(job, MODE_QUALITY,
                                  settings(audio_track="mix"), None)
    assert reason is None
    cmd = " ".join(stages[0][1])
    assert "bwdif" in cmd
    assert "amix=inputs=3" in cmd  # each file mixes its own track count


def test_gpu_target_safety_margin():
    """All GPU vendors get the wider 0.90 safety margin in target mode."""
    from planner import plan_job as pj
    for vendor in ("nvenc", "amf", "qsv"):
        stages, _p, reason = pj(make_job(), MODE_TARGET,
                                settings(encoder=vendor), 5)
        assert reason is None
        cmd = " ".join(stages[-1][1])
        assert "_" + vendor in cmd or vendor == "nvenc" and "nvenc" in cmd


# ---------- per-file trim and crop ----------
def test_job_trim_wins_over_shared_trim():
    job = make_job()
    job.trim = (5.0, 25.0)
    stages, _p, reason = plan_job(job, MODE_QUALITY,
                                  settings(trim=(0.0, 10.0)), None)
    assert reason is None
    cmd = " ".join(stages[0][1])
    assert "-ss 5.000" in cmd and "-t 20.000" in cmd
    assert stages[0][2] == 20.0


def test_job_trim_open_end():
    job = make_job(duration=60)
    job.trim = (50.0, None)
    stages, _p, reason = plan_job(job, MODE_QUALITY, settings(), None)
    assert reason is None and stages[0][2] == pytest.approx(10.0)


def test_job_crop_wins_over_crop_menu():
    job = make_job()
    job.crop = (1280, 720, 320, 180)
    stages, _p, reason = plan_job(job, MODE_QUALITY,
                                  settings(crop="9:16"), None)
    assert reason is None
    cmd = " ".join(stages[0][1])
    assert "crop=1280:720:320:180" in cmd and "9/16" not in cmd


def test_cut_only_without_any_trim_fails_per_file():
    stages, _p, reason = plan_job(make_job(), MODE_QUALITY,
                                  settings(cut_only=True, trim=None), None)
    assert stages is None and "no trim range" in reason


def test_cut_only_uses_job_trim():
    job = make_job()
    job.trim = (5.0, 15.0)
    stages, _p, reason = plan_job(job, MODE_QUALITY,
                                  settings(cut_only=True, trim=None), None)
    assert reason is None
    assert "-c copy" in " ".join(stages[0][1])


# ---------- speed on the output timeline ----------
def test_speed_scales_progress_duration():
    stages, _p, reason = plan_job(make_job(), MODE_QUALITY,
                                  settings(speed=2.0), None)
    assert reason is None and stages[0][2] == pytest.approx(30.0)  # 60s at 2x


def test_speed_scales_target_bitrate():
    """A 2x speed halves the output seconds, so the same size cap allows
    roughly double the bitrate."""
    def target_kbps(spd):
        stages, _p, reason = plan_job(make_job(), MODE_TARGET,
                                      settings(speed=spd), 10)
        assert reason is None
        cmd = " ".join(stages[-1][1])
        return int(cmd.split("-b:v ")[1].split("k")[0])
    assert target_kbps(2.0) == pytest.approx(2 * target_kbps(1.0), rel=0.15)


def test_speed_shrinks_estimate():
    est_1x = estimate_output_bytes(_info(), MODE_QUALITY, settings())
    est_2x = estimate_output_bytes(_info(), MODE_QUALITY, settings(speed=2.0))
    assert est_2x == pytest.approx(est_1x / 2)


# ---------- audio trim / speed, image flatten and attempts ----------
def test_audio_mode_trim_and_speed():
    job = make_job(outputs=["out.mp3"])
    job.trim = (10.0, 40.0)
    stages, _p, reason = plan_job(job, MODE_AUDIO,
                                  settings(aud_speed=2.0), None)
    assert reason is None
    cmd = " ".join(stages[0][1])
    assert "-ss 10.000" in cmd and "-t 30.000" in cmd
    assert stages[0][2] == pytest.approx(15.0)  # 30s of audio at 2x


def test_audio_copy_ignores_speed():
    stages, _p, reason = plan_job(make_job(outputs=["o.m4a"]), MODE_AUDIO,
                                  settings(aud_format="copy", aud_speed=2.0),
                                  None)
    assert reason is None
    assert "atempo" not in " ".join(stages[0][1])
    assert stages[0][2] == 60.0  # copy keeps real time


def test_image_flatten_transparent_for_jpeg(tmp_path):
    from PIL import Image
    src = tmp_path / "sticker.png"
    Image.new("RGBA", (64, 64), (255, 0, 0, 0)).save(src)
    job = make_job(str(src), outputs=[str(tmp_path / "out.jpg")])
    stages, temps, reason = plan_job(job, MODE_IMAGE,
                                     settings(img_format="jpeg"), None)
    assert reason is None and len(temps) == 1
    assert temps[0] in " ".join(stages[0][1])  # encodes the flattened temp
    import os
    assert os.path.exists(temps[0])
    from encoder import cleanup_passlogs
    cleanup_passlogs(temps[0])
    assert not os.path.exists(temps[0])


def test_image_opaque_needs_no_flatten(tmp_path):
    from PIL import Image
    src = tmp_path / "photo.png"
    Image.new("RGB", (64, 64), (0, 128, 0)).save(src)
    job = make_job(str(src), outputs=[str(tmp_path / "out.jpg")])
    stages, temps, reason = plan_job(job, MODE_IMAGE,
                                     settings(img_format="jpeg"), None)
    assert reason is None and temps == []
    assert str(src) in " ".join(stages[0][1])


def test_plan_image_attempts_carries_crop():
    from planner import plan_image_attempts
    job = make_job("in.png", outputs=["out.webp"])
    job.crop = (100, 100, 4, 4)
    plans, temps = plan_image_attempts(job, settings(img_format="webp",
                                                     img_max_kb=256))
    assert len(plans) > 5 and temps == []
    assert all("crop=100:100:4:4" in " ".join(p[0][1]) for p in plans)
