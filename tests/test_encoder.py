"""Tests for the bitrate math and ffmpeg command construction."""

import math

import pytest

from encoder import (video_bitrate_for_target, suggest_parts, build_stages,
                     build_gif_stages, build_image_stages, build_audio_stages,
                     _time_to_seconds)


def joined(stages):
    """Flatten stages into one string per command for easy substring checks."""
    return [" ".join(cmd) for _label, cmd in stages]


# ---------- bitrate math ----------
def test_video_bitrate_for_target_math():
    kbps = video_bitrate_for_target(3600, 500, 128)
    expected = 500 * 1024 * 1024 * 8 * 0.95 / 3600 / 1000 - 128
    assert math.isclose(kbps, expected, rel_tol=1e-6)


def test_video_bitrate_zero_duration():
    assert video_bitrate_for_target(0, 500, 128) == 0.0


def test_video_bitrate_scales_with_target():
    assert video_bitrate_for_target(60, 200, 0) > video_bitrate_for_target(60, 100, 0)


def test_time_to_seconds():
    assert math.isclose(_time_to_seconds("00:01:23.500000"), 83.5)
    assert _time_to_seconds("bad") == 0.0


# ---------- split part count ----------
def test_suggest_parts_hour_1440p60():
    assert suggest_parts(3600, 500, 2560, 1440, 60) == 6


def test_suggest_parts_short_video_single():
    assert suggest_parts(10, 500, 1280, 720, 30) == 1


def test_suggest_parts_capped():
    assert suggest_parts(100000, 1, 3840, 2160, 60, max_parts=8) == 8


# ---------- command construction ----------
def _base(**over):
    s = {"codec": "h265", "encoder": "cpu", "crf": 22, "preset": "slow",
         "target_height": None, "target_fps": None,
         "audio_mode": "copy", "audio_bitrate": "128k"}
    s.update(over)
    return s


def test_quality_vbv_cap_for_roomy_targets():
    """Capped quality: CRF plus a VBV ceiling (roomy Target size mode)."""
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(vbv_maxrate=2000), "quality"))[0]
    assert "-crf 22" in cmd and "-maxrate 2000k" in cmd and "-bufsize 4000k" in cmd
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(codec="av1", vbv_maxrate=2000), "quality"))[0]
    assert "-maxrate" not in cmd  # SVT-AV1 wrapper has no clean VBV
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(encoder="nvenc", vbv_maxrate=2000),
                              "quality"))[0]
    assert "-maxrate 2000k" in cmd
    cmd = joined(build_stages("in.mp4", "out.mp4", _base(), "quality"))[0]
    assert "-maxrate" not in cmd  # plain quality mode stays uncapped


def test_quality_x265_command():
    cmd = joined(build_stages("in.mp4", "out.mp4", _base(), "quality"))[0]
    assert "-c:v libx265" in cmd and "-crf 22" in cmd
    assert "-preset slow" in cmd and "-tag:v hvc1" in cmd
    assert "-pix_fmt yuv420p" in cmd
    assert cmd.strip().endswith("out.mp4")


def test_quality_av1_maps_crf_and_preset():
    cmd = joined(build_stages("in.mp4", "out.mp4", _base(codec="av1"), "quality"))[0]
    assert "-c:v libsvtav1" in cmd
    assert "-crf 29" in cmd          # 22 + 7 offset for SVT-AV1's scale
    assert "-preset 5" in cmd        # "slow" mapped to SVT numeric preset
    assert "hvc1" not in cmd         # tag is H.265-only


def test_quality_h264_maps_crf():
    cmd = joined(build_stages("in.mp4", "out.mp4", _base(codec="h264"), "quality"))[0]
    assert "-c:v libx264" in cmd and "-crf 18" in cmd  # 22 - 4 offset
    assert "hvc1" not in cmd


@pytest.mark.parametrize("codec,gpu_enc,cq", [
    ("h265", "hevc_nvenc", 22), ("av1", "av1_nvenc", 29), ("h264", "h264_nvenc", 18),
])
def test_quality_nvenc_per_codec(codec, gpu_enc, cq):
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(codec=codec, encoder="nvenc"), "quality"))[0]
    assert gpu_enc in cmd and f"-cq {cq}" in cmd and "-pix_fmt yuv420p" in cmd


def test_target_x265_is_two_pass():
    s = _base(video_bitrate=980, audio_mode="aac")
    cmds = joined(build_stages("in.mp4", "out.mp4", s, "target", passlog="pl"))
    assert len(cmds) == 2
    assert "pass=1" in cmds[0] and "pass=2" in cmds[1]
    assert "-b:v 980k" in cmds[0]


def test_target_x264_uses_native_pass_flags():
    s = _base(codec="h264", video_bitrate=980, audio_mode="aac")
    cmds = joined(build_stages("in.mp4", "out.mp4", s, "target", passlog="pl"))
    assert len(cmds) == 2
    assert "-pass 1" in cmds[0] and "-pass 2" in cmds[1]
    assert "x265-params" not in cmds[0]


def test_target_av1_single_pass_abr():
    s = _base(codec="av1", video_bitrate=980, audio_mode="aac")
    cmds = joined(build_stages("in.mp4", "out.mp4", s, "target"))
    assert len(cmds) == 1
    assert "libsvtav1" in cmds[0] and "-b:v 980k" in cmds[0]


def test_target_nvenc_is_single_pass():
    s = _base(encoder="nvenc", video_bitrate=980, audio_mode="aac")
    cmds = joined(build_stages("in.mp4", "out.mp4", s, "target"))
    assert len(cmds) == 1
    assert "hevc_nvenc" in cmds[0] and "-multipass" in cmds[0]


def test_filters_downscale_and_fps():
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(target_height=1080, target_fps=30), "quality"))[0]
    assert "scale=-2:1080" in cmd and "fps=30" in cmd


def test_segment_adds_ss_and_t():
    cmd = joined(build_stages("in.mp4", "out.mp4", _base(), "quality",
                              segment=(2.0, 3.0)))[0]
    assert "-ss 2.000" in cmd and "-t 3.000" in cmd


def test_segment_trims_input_not_output():
    """Regression: -t after -i caps the OUTPUT, which breaks timeline
    stretching filters (boomerang lost its bounce, speed covered the wrong
    span) and makes palette GIFs read the whole source before writing."""
    for stages in (build_stages("in.mp4", "out.mp4", _base(), "quality",
                                segment=(2.0, 3.0)),
                   build_gif_stages("in.mp4", "out.gif",
                                    {"target_fps": 15}, segment=(2.0, 3.0))):
        cmd = stages[0][1]
        assert cmd.index("-t") < cmd.index("-i")


def test_gif_command():
    cmd = joined(build_gif_stages("in.mp4", "out.gif",
                                  {"target_fps": 15, "gif_height": 480}))[0]
    assert "palettegen" in cmd and "paletteuse" in cmd
    assert "stats_mode=diff" in cmd and "diff_mode=rectangle" in cmd
    assert "fps=15" in cmd and "-loop 0" in cmd
    assert cmd.strip().endswith("out.gif")


def test_gif_size_caps_height_never_upscales():
    cmd = joined(build_gif_stages("in.mp4", "out.gif",
                                  {"target_fps": 15, "gif_height": 480}))[0]
    assert "scale=-2:min(480\\,ih):flags=lanczos" in cmd
    # no size chosen: no scale filter at all ("scale" alone would also match
    # the default bayer_scale dither, so check the filter form)
    cmd = joined(build_gif_stages("in.mp4", "out.gif", {"target_fps": 15}))[0]
    assert "scale=-2:" not in cmd
    # the Compress tab's Resolution must no longer leak into loops
    cmd = joined(build_gif_stages("in.mp4", "out.gif",
                                  {"target_fps": 15, "target_height": 1080}))[0]
    assert "scale=-2:" not in cmd


def test_gif_mp4_loop_dimensions_stay_even():
    """libx264 yuv420p rejects odd dimensions, so the MP4 loop must round
    them to even both with a size cap and without one."""
    cmd = joined(build_gif_stages("in.mp4", "out_loop.mp4",
                                  {"target_fps": 15, "gif_format": "mp4",
                                   "gif_height": 480}))[0]
    assert "scale=-2:trunc(min(480\\,ih)/2)*2:flags=lanczos" in cmd
    cmd = joined(build_gif_stages("in.mp4", "out_loop.mp4",
                                  {"target_fps": 15, "gif_format": "mp4"}))[0]
    assert "scale=trunc(iw/2)*2:trunc(ih/2)*2" in cmd


def test_gif_dither_option():
    cmd = joined(build_gif_stages("in.mp4", "out.gif",
                                  {"target_fps": 15, "gif_dither": "floyd_steinberg"}))[0]
    assert "dither=floyd_steinberg" in cmd


def test_gif_default_fps_when_keep_original():
    cmd = joined(build_gif_stages("in.mp4", "out.gif", {"target_fps": None}))[0]
    assert "fps=15" in cmd  # falls back to 15 for GIFs


def test_gif_webp_output():
    cmd = joined(build_gif_stages("in.mp4", "out.webp",
                                  {"target_fps": 15, "gif_format": "webp"}))[0]
    assert "libwebp" in cmd and "-loop 0" in cmd and "-an" in cmd
    assert "palettegen" not in cmd  # WebP needs no GIF palette


def test_gif_mp4_loop_output():
    cmd = joined(build_gif_stages("in.mp4", "out_loop.mp4",
                                  {"target_fps": 15, "gif_format": "mp4"}))[0]
    assert "libx264" in cmd and "-an" in cmd and "faststart" in cmd
    assert "palettegen" not in cmd


def test_gif_speed_uses_setpts():
    cmd = joined(build_gif_stages("in.mp4", "out.gif",
                                  {"target_fps": 15, "gif_speed": 2.0}))[0]
    assert "setpts=PTS/2.0" in cmd
    cmd = joined(build_gif_stages("in.mp4", "out.gif",
                                  {"target_fps": 15, "gif_speed": 1.0}))[0]
    assert "setpts" not in cmd  # 1x adds no filter


def test_gif_direction_reverse_and_boomerang():
    cmd = joined(build_gif_stages("in.mp4", "out.gif",
                                  {"target_fps": 15, "gif_direction": "reverse"}))[0]
    assert ",reverse" in cmd and "concat" not in cmd
    cmd = joined(build_gif_stages("in.mp4", "out.gif",
                                  {"target_fps": 15, "gif_direction": "boomerang"}))[0]
    assert "concat=n=2" in cmd and "reverse[r]" in cmd
    # the boomerang graph must still feed the palette stages for classic GIF
    assert cmd.index("concat=n=2") < cmd.index("palettegen")


def test_gif_palette_colors():
    cmd = joined(build_gif_stages("in.mp4", "out.gif",
                                  {"target_fps": 15, "gif_colors": 128}))[0]
    assert "max_colors=128" in cmd


def test_gif_lossy_adds_gifsicle_stage():
    stages = build_gif_stages("in.mp4", "out.gif",
                              {"target_fps": 15, "gif_lossy": 80})
    assert [lbl for lbl, _c in stages] == ["gif", "optimize"]
    opt = " ".join(stages[1][1])
    assert "--lossy=80" in opt and "-O3" in opt and opt.endswith("out.gif")
    # off / non-gif formats get no gifsicle pass
    assert len(build_gif_stages("in.mp4", "out.gif",
                                {"target_fps": 15, "gif_lossy": None})) == 1
    assert len(build_gif_stages("in.mp4", "out.webp",
                                {"target_fps": 15, "gif_format": "webp",
                                 "gif_lossy": 80})) == 1


def test_gif_dedupe_after_fps():
    cmd = joined(build_gif_stages("in.mp4", "out.gif",
                                  {"target_fps": 15, "gif_dedupe": True}))[0]
    assert "mpdecimate" in cmd
    assert cmd.index("fps=15") < cmd.index("mpdecimate")  # fps first, or it
    cmd = joined(build_gif_stages("in.mp4", "out.gif",  # re-duplicates frames
                                  {"target_fps": 15}))[0]
    assert "mpdecimate" not in cmd


def test_gif_output_duration():
    from encoder import gif_output_duration
    assert gif_output_duration(5.0, {}) == 5.0
    assert gif_output_duration(5.0, {"gif_speed": 2.0}) == 2.5
    assert gif_output_duration(5.0, {"gif_speed": 0.5}) == 10.0
    assert gif_output_duration(5.0, {"gif_speed": 2.0,
                                     "gif_direction": "boomerang"}) == 5.0


def test_rotate_filter_before_scale():
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(rotate="transpose=1", target_height=1080),
                              "quality"))[0]
    assert "transpose=1,scale=-2:1080" in cmd
    cmd = joined(build_stages("in.mp4", "out.mp4", _base(rotate="hflip,vflip"),
                              "quality"))[0]
    assert "hflip,vflip" in cmd


def test_subtitles_filter_escaping():
    """Two-level backslash escaping per ffmpeg's filtergraph docs; validated
    against a real ffmpeg by the smoke test."""
    from encoder import _subtitles_filter
    f = _subtitles_filter(r"C:\subs\my clip's.srt")
    assert f == r"subtitles=filename=C\\:/subs/my clip\\\'s.srt"


def test_subtitles_render_last_in_chain():
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(subtitles="s.srt", target_height=720),
                              "quality"))[0]
    assert "subtitles=filename=" in cmd
    assert cmd.index("scale=-2:720") < cmd.index("subtitles=")


def test_audio_normalize_option():
    cmd = joined(build_audio_stages("in.mp4", "out.mp3",
                                    {"aud_format": "mp3", "aud_bitrate": "192k",
                                     "aud_normalize": True}))[0]
    assert "loudnorm" in cmd and "-ar 48000" in cmd
    cmd = joined(build_audio_stages("in.mp4", "out.mp3",
                                    {"aud_format": "mp3", "aud_bitrate": "192k"}))[0]
    assert "loudnorm" not in cmd


def test_image_strip_metadata_option():
    base = {"img_format": "webp", "img_quality": "balanced", "img_resize": None}
    cmd = joined(build_image_stages("in.jpg", "out.webp",
                                    dict(base, img_strip=True)))[0]
    assert "-map_metadata -1" in cmd
    cmd = joined(build_image_stages("in.jpg", "out.webp", base))[0]
    assert "-map_metadata" not in cmd


def test_remove_audio_option():
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(audio_mode="none"), "quality"))[0]
    assert "-an" in cmd and "-c:a" not in cmd


def test_hdr_10bit_preserved_on_h265_av1():
    """Regression: 10-bit HDR sources must not be crushed to 8-bit SDR."""
    s = _base(src_10bit=True, src_hdr=True)
    cmd = joined(build_stages("in.mp4", "out.mp4", s, "quality"))[0]
    assert "-pix_fmt yuv420p10le" in cmd and "tonemap" not in cmd
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(codec="av1", src_10bit=True, src_hdr=True),
                              "quality"))[0]
    assert "-pix_fmt yuv420p10le" in cmd
    # NVENC uses the GPU 10-bit format
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(encoder="nvenc", src_10bit=True, src_hdr=True),
                              "quality"))[0]
    assert "-pix_fmt p010le" in cmd


def test_hdr_tonemapped_when_output_is_sdr():
    # H.264 output stays 8-bit for compatibility, so HDR must be tone mapped
    s = _base(codec="h264", src_10bit=True, src_hdr=True)
    cmd = joined(build_stages("in.mp4", "out.mp4", s, "quality"))[0]
    assert "tonemap" in cmd and "bt709" in cmd and "-pix_fmt yuv420p" in cmd
    # GIF palettes are SDR too
    cmd = joined(build_gif_stages("in.mp4", "out.gif",
                                  {"target_fps": 15, "src_hdr": True}))[0]
    assert "tonemap" in cmd


def test_sdr_sources_unchanged():
    cmd = joined(build_stages("in.mp4", "out.mp4", _base(), "quality"))[0]
    assert "-pix_fmt yuv420p" in cmd and "tonemap" not in cmd


def test_cut_stages_stream_copy():
    from encoder import build_cut_stages
    cmd = joined(build_cut_stages("in.mkv", "out_cut.mkv", (90.0, 60.0)))[0]
    assert "-c copy" in cmd and "-ss 90.000" in cmd and "-t 60.000" in cmd
    assert "libx265" not in cmd  # no re-encode


def test_audio_stages():
    cmd = joined(build_audio_stages("in.mp4", "out.mp3",
                                    {"aud_format": "mp3", "aud_bitrate": "192k"}))[0]
    assert "-vn" in cmd and "libmp3lame" in cmd and "-b:a 192k" in cmd
    cmd = joined(build_audio_stages("in.wav", "out.m4a",
                                    {"aud_format": "m4a", "aud_bitrate": "256k"}))[0]
    assert "-c:a aac" in cmd and "-b:a 256k" in cmd


# ---------- image commands ----------
def test_image_webp():
    cmd = joined(build_image_stages("in.png", "out.webp",
                                    {"img_format": "webp", "img_quality": "balanced",
                                     "img_resize": None}))[0]
    assert "libwebp" in cmd and "-quality 80" in cmd and "-frames:v 1" in cmd


def test_image_avif_forces_even_dims():
    cmd = joined(build_image_stages("in.png", "out.avif",
                                    {"img_format": "avif", "img_quality": "small",
                                     "img_resize": None}))[0]
    assert "libaom-av1" in cmd and "-still-picture 1" in cmd
    assert "trunc(iw/2)*2" in cmd  # yuv420 needs even dimensions


def test_image_jpeg_quality_levels():
    for level, q in (("high", 3), ("balanced", 6), ("small", 10)):
        cmd = joined(build_image_stages("in.png", "out.jpg",
                                        {"img_format": "jpeg", "img_quality": level,
                                         "img_resize": None}))[0]
        assert f"-q:v {q}" in cmd


def test_image_resize_multiplier_and_cap():
    cmd = joined(build_image_stages("in.png", "o.webp",
                                    {"img_format": "webp", "img_quality": "balanced",
                                     "img_resize": ("mul", 2.0)}))[0]
    assert "iw*2.0" in cmd and "lanczos" in cmd
    cmd = joined(build_image_stages("in.png", "o.webp",
                                    {"img_format": "webp", "img_quality": "balanced",
                                     "img_resize": ("h", 1080)}))[0]
    assert "min(1080" in cmd  # caps height without upscaling
