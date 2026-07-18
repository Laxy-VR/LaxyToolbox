"""Tests for the bitrate math and ffmpeg command construction."""

import math

import pytest

from encoder import (video_bitrate_for_target, suggest_parts, build_stages,
                     build_gif_stages, build_image_stages, build_audio_stages,
                     _time_to_seconds, AUD_ENCODERS)


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


def test_crop_filters():
    """Crop runs first in the chain (before tonemap/rotate/scale) and the
    ratio crops keep even dimensions for yuv420."""
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(crop_filter="crop=1920:800:0:140",
                                    target_height=720), "quality"))[0]
    assert "crop=1920:800:0:140,scale=-2:720" in cmd
    cmd = joined(build_stages("in.mp4", "out.mp4", _base(crop="9:16"),
                              "quality"))[0]
    assert "crop=min(iw\\,trunc(ih*9/16/2)*2):ih" in cmd
    cmd = joined(build_stages("in.mp4", "out.mp4", _base(crop="1:1"),
                              "quality"))[0]
    assert "crop=trunc(min(iw\\,ih)/2)*2:trunc(min(iw\\,ih)/2)*2" in cmd
    # a detected per-file crop wins over the ratio choice
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(crop="auto", crop_filter="crop=1280:690:0:15"),
                              "quality"))[0]
    assert "crop=1280:690:0:15" in cmd and "9/16" not in cmd
    # no crop settings: no crop filter
    cmd = joined(build_stages("in.mp4", "out.mp4", _base(), "quality"))[0]
    assert "crop=" not in cmd


def test_audio_boost_normalizes_and_reencodes():
    """Boost quiet audio: loudness normalisation needs a re-encode, so the
    boost mode carries its own AAC encode instead of stream copy."""
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(audio_mode="boost", audio_bitrate="192k"),
                              "quality"))[0]
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in cmd
    assert "-c:a aac" in cmd and "-b:a 192k" in cmd and "-ar 48000" in cmd
    assert "-c:a copy" not in cmd
    # plain copy stays untouched, no loudnorm
    cmd = joined(build_stages("in.mp4", "out.mp4", _base(), "quality"))[0]
    assert "loudnorm" not in cmd and "-c:a copy" in cmd


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


def test_gif_custom_dimensions_exact():
    """Typed pixels are exact: both sides stretch if asked, one side follows
    the aspect ratio, and custom (unlike the caps) may upscale."""
    cmd = joined(build_gif_stages("in.mp4", "out.gif",
                                  {"target_fps": 15, "gif_custom": (400, 300)}))[0]
    assert "scale=400:300:flags=lanczos" in cmd
    cmd = joined(build_gif_stages("in.mp4", "out.gif",
                                  {"target_fps": 15, "gif_custom": (500, None)}))[0]
    assert "scale=500:-1:flags=lanczos" in cmd
    cmd = joined(build_gif_stages("in.mp4", "out.gif",
                                  {"target_fps": 15, "gif_custom": (None, 128)}))[0]
    assert "scale=-1:128:flags=lanczos" in cmd
    # custom wins over a leftover height cap
    cmd = joined(build_gif_stages("in.mp4", "out.gif",
                                  {"target_fps": 15, "gif_custom": (400, None),
                                   "gif_height": 480}))[0]
    assert "scale=400:-1" in cmd and "min(480" not in cmd
    # both blank behaves like no custom size at all
    cmd = joined(build_gif_stages("in.mp4", "out.gif",
                                  {"target_fps": 15, "gif_custom": (None, None)}))[0]
    assert "scale=-2:" not in cmd and "scale=-1:" not in cmd


def test_gif_custom_dimensions_mp4_rounds_even():
    cmd = joined(build_gif_stages("in.mp4", "out_loop.mp4",
                                  {"target_fps": 15, "gif_format": "mp4",
                                   "gif_custom": (401, 301)}))[0]
    assert "scale=400:300:flags=lanczos" in cmd
    cmd = joined(build_gif_stages("in.mp4", "out_loop.mp4",
                                  {"target_fps": 15, "gif_format": "mp4",
                                   "gif_custom": (401, None)}))[0]
    assert "scale=400:-2:flags=lanczos" in cmd


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


# ---------- GPU vendors: AMD (AMF) and Intel (QSV) ----------
def test_quality_amf_uses_cqp():
    """AMF constant quality: CQP with the quality preset; H.264 also sets
    the B-frame QP, and AV1 quantizes on its own 0..255 scale."""
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(encoder="amf"), "quality"))[0]
    assert "hevc_amf" in cmd and "-rc cqp" in cmd
    assert "-qp_i 22 -qp_p 22" in cmd and "-quality quality" in cmd
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(encoder="amf", codec="h264"), "quality"))[0]
    assert "h264_amf" in cmd and "-qp_b 18" in cmd  # crf 22 - 4
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(encoder="amf", codec="av1"), "quality"))[0]
    assert "av1_amf" in cmd and "-qp_i 145" in cmd  # (22 + 7) * 5


def test_quality_qsv_uses_global_quality():
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(encoder="qsv"), "quality"))[0]
    assert "hevc_qsv" in cmd and "-global_quality 22" in cmd


def test_target_amf_and_qsv_single_pass():
    s = _base(encoder="amf", video_bitrate=900, audio_mode="aac")
    cmds = joined(build_stages("in.mp4", "out.mp4", s, "target"))
    assert len(cmds) == 1
    assert "hevc_amf" in cmds[0] and "-rc vbr_peak" in cmds[0]
    assert "-b:v 900k" in cmds[0] and "-maxrate 900k" in cmds[0]
    s = _base(encoder="qsv", video_bitrate=900, audio_mode="aac")
    cmds = joined(build_stages("in.mp4", "out.mp4", s, "target"))
    assert len(cmds) == 1 and "hevc_qsv" in cmds[0] and "-b:v 900k" in cmds[0]


def test_vbv_cap_skipped_on_amf_and_qsv():
    """AMF CQP and QSV ICQ have no clean VBV; the roomy-target cap must not
    emit a maxrate there (the planner's headroom margin covers it)."""
    for vendor in ("amf", "qsv"):
        cmd = joined(build_stages("in.mp4", "out.mp4",
                                  _base(encoder=vendor, vbv_maxrate=2000),
                                  "quality"))[0]
        assert "-maxrate" not in cmd


def test_gpu_10bit_pixfmt_all_vendors():
    for vendor in ("nvenc", "amf", "qsv"):
        cmd = joined(build_stages("in.mp4", "out.mp4",
                                  _base(encoder=vendor, src_10bit=True),
                                  "quality"))[0]
        assert "-pix_fmt p010le" in cmd


# ---------- auto deinterlace ----------
def test_interlaced_source_gets_bwdif_first():
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(src_interlaced=True, target_height=720),
                              "quality"))[0]
    assert "-vf bwdif,scale=-2:720" in cmd
    # progressive sources are left alone
    cmd = joined(build_stages("in.mp4", "out.mp4", _base(), "quality"))[0]
    assert "bwdif" not in cmd


def test_gif_chain_deinterlaces():
    cmds = joined(build_gif_stages("in.mp4", "out.gif",
                                   {"gif_format": "gif", "gif_dither": "none",
                                    "src_interlaced": True}))
    assert "bwdif" in cmds[0]


# ---------- denoise ----------
def test_denoise_after_rotate_before_scale():
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(rotate="hflip", denoise="hqdn3d=2:1.5:3:3",
                                    target_height=720), "quality"))[0]
    assert "hflip,hqdn3d=2:1.5:3:3,scale=-2:720" in cmd
    cmd = joined(build_stages("in.mp4", "out.mp4", _base(), "quality"))[0]
    assert "hqdn3d" not in cmd


# ---------- audio track selection ----------
def test_audio_track_map():
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(audio_track=1), "quality"))[0]
    assert "-map 0:v:0 -map 0:a:1?" in cmd  # ? tolerates single-track files


def test_audio_track_auto_adds_no_maps():
    cmd = joined(build_stages("in.mp4", "out.mp4", _base(), "quality"))[0]
    assert "-map" not in cmd


def test_audio_mix_builds_amix_graph():
    """Mix all tracks: amix folds every stream; copy cannot survive a mix,
    so it falls back to AAC."""
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(audio_track="mix", audio_track_count=2),
                              "quality"))[0]
    assert "[0:a:0][0:a:1]amix=inputs=2:duration=longest[aout]" in cmd
    assert "-map 0:v:0 -map [aout]" in cmd
    assert "-c:a aac" in cmd and "-c:a copy" not in cmd


def test_audio_mix_with_boost_joins_graph():
    """Boost's loudnorm must live inside the mix graph: -af on a stream fed
    by a complex filtergraph is an ffmpeg error."""
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(audio_track="mix", audio_track_count=2,
                                    audio_mode="boost", audio_bitrate="192k"),
                              "quality"))[0]
    assert "amix=inputs=2:duration=longest,loudnorm=" in cmd
    assert "-af" not in cmd and "-b:a 192k" in cmd


def test_audio_mix_single_track_noop():
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(audio_track="mix", audio_track_count=1),
                              "quality"))[0]
    assert "amix" not in cmd and "-map" not in cmd


def test_audio_track_ignored_when_removing_audio():
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(audio_track=1, audio_mode="none"),
                              "quality"))[0]
    assert "-map" not in cmd and "-an" in cmd


def test_audio_opus_in_ogg():
    """Opus encodes with libopus into .ogg (the extension players and
    Discord's inline player actually recognise)."""
    assert AUD_ENCODERS["opus"] == ("libopus", ".ogg")
    cmd = joined(build_audio_stages("in.mp4", "out.ogg",
                                    {"aud_format": "opus",
                                     "aud_bitrate": "128k"}))[0]
    assert "libopus" in cmd and "-b:a 128k" in cmd and "-vn" in cmd


# ---------- video speed ----------
def test_speed_video_and_audio_chain():
    """2x: setpts halves the pts, atempo re-times audio (forcing AAC even
    from copy), and fps comes AFTER setpts so the output rate is honoured."""
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(speed=2.0, target_fps=30), "quality"))[0]
    assert "setpts=PTS/2.0,fps=30" in cmd
    assert "-af atempo=2" in cmd
    assert "-c:a aac" in cmd and "-c:a copy" not in cmd


def test_speed_quarter_chains_atempo():
    """0.25x is below atempo's floor, so it chains two 0.5x stages."""
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(speed=0.25), "quality"))[0]
    assert "atempo=0.5,atempo=0.5" in cmd


def test_speed_subtitles_burn_before_retime():
    """Subtitles render on the original clock, so the burn-in must come
    before setpts (after it they would drift)."""
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(speed=2.0, subtitles="s.srt"), "quality"))[0]
    assert cmd.index("subtitles=") < cmd.index("setpts=")


def test_speed_1x_leaves_everything_alone():
    cmd = joined(build_stages("in.mp4", "out.mp4", _base(speed=1.0), "quality"))[0]
    assert "setpts" not in cmd and "atempo" not in cmd and "-c:a copy" in cmd


def test_speed_with_mix_joins_graph():
    cmd = joined(build_stages("in.mp4", "out.mp4",
                              _base(speed=2.0, audio_track="mix",
                                    audio_track_count=2), "quality"))[0]
    assert "amix=inputs=2:duration=longest,atempo=2" in cmd


def test_gif_crop_filter_applies():
    """A per-file crop box also crops GIFs made from that file."""
    cmds = joined(build_gif_stages("in.mp4", "out.gif",
                                   {"gif_format": "gif", "gif_dither": "none",
                                    "crop_filter": "crop=640:640:100:0"}))
    assert "crop=640:640:100:0" in cmds[0]


# ---------- audio tab: copy / lossless / trim / track / speed ----------
def test_audio_copy_remuxes():
    from encoder import audio_copy_ext
    cmd = joined(build_audio_stages("in.mp4", "out.m4a",
                                    {"aud_format": "copy"}))[0]
    assert "-c:a copy" in cmd and "-vn" in cmd
    assert "-b:a" not in cmd and "-af" not in cmd
    assert audio_copy_ext("aac") == ".m4a"
    assert audio_copy_ext("mp3") == ".mp3"
    assert audio_copy_ext("opus") == ".ogg"
    assert audio_copy_ext("pcm_s24le") == ".wav"
    assert audio_copy_ext("weirdcodec") == ".mka"


def test_audio_lossless_formats_skip_bitrate():
    for fmt, enc in (("flac", "flac"), ("wav", "pcm_s16le")):
        cmd = joined(build_audio_stages("in.mp4", "o",
                                        {"aud_format": fmt}))[0]
        assert f"-c:a {enc}" in cmd and "-b:a" not in cmd


def test_audio_segment_trims_input_side():
    cmd = joined(build_audio_stages("in.mp3", "out.mp3",
                                    {"aud_format": "mp3", "aud_bitrate": "192k"},
                                    segment=(10.0, 20.0)))[0]
    assert cmd.index("-ss 10.000") < cmd.index("-i in.mp3")
    assert "-t 20.000" in cmd


def test_audio_track_and_mix():
    cmd = joined(build_audio_stages("in.mkv", "out.mp3",
                                    {"aud_format": "mp3", "aud_bitrate": "192k",
                                     "aud_track": 1}))[0]
    assert "-map 0:a:1?" in cmd
    cmd = joined(build_audio_stages("in.mkv", "out.mp3",
                                    {"aud_format": "mp3", "aud_bitrate": "192k",
                                     "aud_track": "mix",
                                     "audio_track_count": 3}))[0]
    assert "[0:a:0][0:a:1][0:a:2]amix=inputs=3" in cmd and "-map [aout]" in cmd


def test_audio_speed_and_normalize_chain():
    cmd = joined(build_audio_stages("in.mp3", "out.mp3",
                                    {"aud_format": "mp3", "aud_bitrate": "192k",
                                     "aud_speed": 1.5, "aud_normalize": True}))[0]
    assert "-af atempo=1.5,loudnorm=" in cmd and "-ar 48000" in cmd


# ---------- image tab: png / crop / rotate / size ladder ----------
def test_image_png_lossless():
    cmd = joined(build_image_stages("in.heic", "out.png",
                                    {"img_format": "png",
                                     "img_quality": "balanced",
                                     "img_resize": None}))[0]
    assert "-c:v png" in cmd and "-quality" not in cmd


def test_image_crop_and_rotate_in_chain():
    cmd = joined(build_image_stages("in.jpg", "out.webp",
                                    {"img_format": "webp",
                                     "img_quality": "balanced",
                                     "img_resize": ("h", 1080),
                                     "crop_filter": "crop=800:800:10:20",
                                     "img_rotate": "transpose=1"}))[0]
    assert r"crop=800:800:10:20,transpose=1,scale=-2:min(1080\,ih)" in cmd


def test_image_attempts_ladder():
    from encoder import image_attempts
    s = {"img_format": "webp", "img_quality": "balanced", "img_resize": None}
    att = image_attempts(s)
    # starts at the user's own quality, walks down, then shrinks
    assert att[0]["img_q_value"] == 80 and att[0]["img_shrink"] is None
    qs = [a["img_q_value"] for a in att if a["img_shrink"] is None]
    assert qs == [80, 62, 45, 30, 20]
    shrinks = [a["img_shrink"] for a in att if a["img_shrink"]]
    assert shrinks == [0.85, 0.7, 0.55, 0.4, 0.3]
    assert all(a["img_q_value"] == 20 for a in att if a["img_shrink"])
    # jpeg's scale is inverted (higher number = worse quality)
    s = {"img_format": "jpeg", "img_quality": "high", "img_resize": None}
    qs = [a["img_q_value"] for a in image_attempts(s) if a["img_shrink"] is None]
    assert qs == [3, 6, 10, 15, 22, 31]


def test_image_shrink_applies_scale():
    cmd = joined(build_image_stages("in.png", "out.webp",
                                    {"img_format": "webp",
                                     "img_quality": "balanced",
                                     "img_resize": None, "img_q_value": 20,
                                     "img_shrink": 0.7}))[0]
    assert "scale=trunc(iw*0.7/2)*2" in cmd and "-quality 20" in cmd
