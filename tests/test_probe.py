"""Tests for metadata parsing and the H.265 recommendation logic."""

import math

import pytest

from probe import (VideoInfo, recommend_settings, estimate_h265_bitrate_kbps,
                   _parse_fraction)


def make(width=1920, height=1080, fps=30.0, vcodec="h264", acodec="aac",
         bitrate=8_000_000, duration=60.0, size=60_000_000):
    return VideoInfo("clip.mp4", width, height, duration, fps, vcodec, acodec,
                     bitrate, size)


@pytest.mark.parametrize("value,expected", [
    ("30000/1001", 29.97),
    ("25", 25.0),
    ("0/0", 0.0),
    ("bad", 0.0),
])
def test_parse_fraction(value, expected):
    assert math.isclose(_parse_fraction(value), expected, rel_tol=1e-3)


def test_bpp_computed_and_none():
    info = make(width=1000, height=1000, fps=10, bitrate=10_000_000)
    assert math.isclose(info.bpp, 10_000_000 / (1000 * 1000 * 10))
    assert make(bitrate=None).bpp is None


@pytest.mark.parametrize("height,crf", [
    (2160, 24), (1440, 23), (1080, 22), (720, 21), (480, 20),
])
def test_recommend_crf_by_resolution(height, crf):
    assert recommend_settings(make(height=height))["crf"] == crf


def test_recommend_preset_and_keep_resolution():
    rec = recommend_settings(make())
    assert rec["preset"] == "slow"
    assert rec["target_height"] is None


def test_recommend_audio_copy_vs_reencode():
    assert recommend_settings(make(acodec="aac"))["audio_mode"] == "copy"
    assert recommend_settings(make(acodec="ac3"))["audio_mode"] == "copy"
    pcm = recommend_settings(make(acodec="pcm_s16le"))
    assert pcm["audio_mode"] == "aac" and pcm["audio_bitrate"] == "192k"


def test_recommend_notes():
    assert "already H.265" in recommend_settings(make(vcodec="hevc"))["note"]
    # very high bitrate -> large reduction expected
    assert "large reduction" in recommend_settings(
        make(bitrate=60_000_000, height=1080))["note"]
    # already efficient (very low bitrate)
    assert "already efficiently compressed" in recommend_settings(
        make(bitrate=1_500_000))["note"]


def test_high_bitrate_4k_not_called_efficient():
    # 16 Mbps H.264 4K30: low bits-per-pixel but plenty to gain from H.265.
    note = recommend_settings(make(width=3840, height=2160, fps=30,
                                   vcodec="h264", bitrate=16_000_000))["note"]
    assert "already efficiently compressed" not in note
    assert "reduction" in note


def test_efficient_codec_note():
    for vc in ("av1", "vp9"):
        assert "efficient codec" in recommend_settings(make(vcodec=vc))["note"]


def test_estimate_h265_bitrate():
    assert estimate_h265_bitrate_kbps(0, 0, 0, 23) == 0.0
    # higher CRF -> lower estimated bitrate
    assert (estimate_h265_bitrate_kbps(1920, 1080, 30, 28)
            < estimate_h265_bitrate_kbps(1920, 1080, 30, 20))


def test_hdr_and_bit_depth_detection():
    hdr = make()
    hdr.pix_fmt, hdr.color_transfer = "yuv420p10le", "smpte2084"
    assert hdr.is_10bit and hdr.is_hdr
    hlg = make()
    hlg.pix_fmt, hlg.color_transfer = "yuv420p10le", "arib-std-b67"
    assert hlg.is_hdr
    sdr = make()
    sdr.pix_fmt, sdr.color_transfer = "yuv420p", "bt709"
    assert not sdr.is_10bit and not sdr.is_hdr
    assert not make().is_10bit  # pix_fmt None
