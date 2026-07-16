"""Read video metadata with ffprobe and recommend H.265 compression settings."""

import json
import os
import subprocess
import sys
from dataclasses import dataclass

# On Windows, stop a console window from flashing up when we call ffprobe/ffmpeg
# from inside the GUI. On other platforms this flag doesn't exist, so use 0.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _tool_path(name: str) -> str:
    """Locate ffmpeg/ffprobe.

    When packaged with PyInstaller we bundle the executables into the app, so we
    look inside the bundle first (sys._MEIPASS). Otherwise we fall back to the
    bare name and let the OS find it on PATH.
    """
    exe = name + (".exe" if sys.platform == "win32" else "")
    bundle = getattr(sys, "_MEIPASS", None)  # set only in a PyInstaller build
    if bundle:
        candidate = os.path.join(bundle, exe)
        if os.path.exists(candidate):
            return candidate
    return name


FFMPEG = _tool_path("ffmpeg")
FFPROBE = _tool_path("ffprobe")
GIFSICLE = _tool_path("gifsicle")


def has_gifsicle() -> bool:
    """True when gifsicle is available (bundled in the exe, or on PATH in
    dev). The lossy GIF option is only offered when it is."""
    if os.path.isabs(GIFSICLE):
        return True
    import shutil
    return shutil.which(GIFSICLE) is not None

_ENCODERS_CACHE = None


def _encoders_list() -> str:
    global _ENCODERS_CACHE
    if _ENCODERS_CACHE is None:
        try:
            r = subprocess.run([FFMPEG, "-hide_banner", "-encoders"],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", creationflags=NO_WINDOW)
            _ENCODERS_CACHE = r.stdout or ""
        except Exception:  # noqa: BLE001
            _ENCODERS_CACHE = ""
    return _ENCODERS_CACHE


def has_nvenc() -> bool:
    """True if this ffmpeg build exposes the NVIDIA hevc_nvenc encoder.

    Presence in the encoder list is a good proxy; if the GPU is missing the
    actual encode fails and the job is reported as failed, which is fine.
    """
    return "hevc_nvenc" in _encoders_list()


def gpu_codecs() -> set:
    """Codecs this machine can encode on the GPU ({'h265', 'av1', 'h264'})."""
    encoders = _encoders_list()
    return {codec for codec, name in
            (("h265", "hevc_nvenc"), ("av1", "av1_nvenc"), ("h264", "h264_nvenc"))
            if name in encoders}


def nvenc_works() -> bool:
    """Actually try a one frame NVENC encode.

    Encoder presence in the ffmpeg build says nothing about THIS machine:
    an AMD/Intel GPU or a too-old NVIDIA driver still fails at encode time,
    so the GPU option should only be offered when a real encode succeeds.
    Takes about a second; callers should run it off the UI thread and cache.
    """
    if not has_nvenc():
        return False
    cmd = [FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
           "-frames:v", "1", "-c:v", "hevc_nvenc", "-f", "null", os.devnull]
    try:
        r = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW,
                           timeout=20)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def extract_frame_png(path: str, seconds: float, max_width: int | None = 320) -> bytes | None:
    """Grab one frame at `seconds` as PNG bytes (for preview thumbnails).
    `max_width=None` keeps the source resolution (for saving real stills)."""
    scale = ["-vf", f"scale={max_width}:-1"] if max_width else []
    cmd = [FFMPEG, "-ss", f"{max(seconds, 0):.3f}", "-i", path,
           "-frames:v", "1"] + scale + \
          ["-f", "image2pipe", "-vcodec", "png", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW,
                           timeout=10)
        return r.stdout or None
    except Exception:  # noqa: BLE001
        return None


@dataclass
class VideoInfo:
    path: str
    width: int
    height: int
    duration: float          # seconds
    fps: float
    video_codec: str
    audio_codec: str | None
    bit_rate: int | None     # bits per second, may be None
    size_bytes: int | None
    pix_fmt: str | None = None
    color_transfer: str | None = None

    @property
    def is_10bit(self) -> bool:
        return bool(self.pix_fmt) and ("10" in self.pix_fmt or "12" in self.pix_fmt)

    @property
    def is_hdr(self) -> bool:
        """PQ or HLG transfer = HDR content that needs tone mapping for SDR."""
        return self.color_transfer in ("smpte2084", "arib-std-b67")

    @property
    def resolution_label(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def bpp(self) -> float | None:
        """Bits per pixel per frame, i.e. how compressed the source already is.

        Low value = already efficiently compressed (little to gain).
        High value = fat source (lots of room to shrink with no visible loss).
        Uses the container bitrate, so it's a slight over-estimate (includes
        audio), but that's fine for setting expectations.
        """
        if self.bit_rate and self.width and self.height and self.fps:
            return self.bit_rate / (self.width * self.height * self.fps)
        return None


def _parse_fraction(value: str) -> float:
    """Turn ffprobe's '30000/1001' style framerate into a float."""
    try:
        if "/" in value:
            num, den = value.split("/")
            den = float(den)
            return float(num) / den if den else 0.0
        return float(value)
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe_video(path: str) -> VideoInfo:
    """Run ffprobe and return a VideoInfo. Raises RuntimeError on failure."""
    cmd = [
        FFPROBE, "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        path,
    ]
    # ffprobe embeds the file name in its JSON; force UTF-8 so non-ASCII
    # names never raise a decode error under the Windows locale codec.
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", creationflags=NO_WINDOW
    )
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"ffprobe failed:\n{result.stderr.strip()}")

    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    fmt = data.get("format", {})

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        raise RuntimeError("No video or audio stream found in this file.")

    src = video or audio
    duration = float(fmt.get("duration") or src.get("duration") or 0.0)
    bit_rate = fmt.get("bit_rate") or src.get("bit_rate")

    return VideoInfo(
        path=path,
        width=int(video.get("width", 0)) if video else 0,
        height=int(video.get("height", 0)) if video else 0,
        duration=duration,
        fps=_parse_fraction(video.get("r_frame_rate", "0/1")) if video else 0.0,
        video_codec=video.get("codec_name", "unknown") if video else "none",
        audio_codec=audio.get("codec_name") if audio else None,
        bit_rate=int(bit_rate) if bit_rate else None,
        size_bytes=int(fmt["size"]) if fmt.get("size") else None,
        pix_fmt=video.get("pix_fmt") if video else None,
        color_transfer=video.get("color_transfer") if video else None,
    )


# Audio codecs that sit inside an MP4 as-is, so we can copy them with zero
# quality loss instead of re-encoding.
_MP4_AUDIO_COPY_OK = {"aac", "ac3", "eac3", "mp3", "alac"}


def recommend_settings(info: VideoInfo) -> dict:
    """Pick H.265 settings for the smallest file with no visible quality loss.

    Three levers do this without hurting quality:
      • CRF set as high as still looks transparent (per resolution).
      • preset 'slow' finds a smaller encoding at the SAME quality.
      • audio copied untouched whenever the container allows it.

    CRF is the quality knob for x265: lower = better + bigger, higher = smaller.
    Higher resolutions tolerate a slightly higher CRF because per-pixel
    compression artifacts are harder to see and the files are much larger.
    """
    h = info.height or 1080
    if h >= 2160:
        crf = 24
    elif h >= 1440:
        crf = 23
    elif h >= 1080:
        crf = 22
    elif h >= 720:
        crf = 21
    else:
        crf = 20

    # Copy audio when we can (no re-encode = no loss); otherwise compress
    # cleanly at a high bitrate (e.g. a PCM/FLAC source that can't be copied).
    if info.audio_codec is None or info.audio_codec in _MP4_AUDIO_COPY_OK:
        audio_mode, audio_bitrate = "copy", None
    else:
        audio_mode, audio_bitrate = "aac", "192k"

    return {
        "crf": crf,
        "preset": "slow",
        "target_height": None,     # keep resolution, since downscaling loses detail
        "audio_mode": audio_mode,  # "copy" or "aac"
        "audio_bitrate": audio_bitrate or "128k",
        "note": _savings_note(info, crf),
    }


# How much bitrate each codec needs relative to H.265 for the same quality.
CODEC_EFFICIENCY = {"h265": 1.0, "av1": 0.75, "h264": 1.5}


def estimate_h265_bitrate_kbps(width: int, height: int, fps: float, crf: int,
                               codec: str = "h265") -> float:
    """Rough transparent-quality bitrate (kbps) for a resolution/fps at a CRF.

    Model: x265 needs ~0.045 bits per pixel at CRF 23, halving every +6 CRF;
    other codecs scale by CODEC_EFFICIENCY. Very content dependent, so callers
    should treat it as a ballpark.
    """
    if not (width and height and fps):
        return 0.0
    bpp = 0.045 * (2 ** ((23 - crf) / 6))
    return bpp * width * height * fps / 1000 * CODEC_EFFICIENCY.get(codec, 1.0)


# Codecs that are already about as efficient as H.265 (or more), so re-encoding
# to H.265 usually saves little and can even grow the file.
_EFFICIENT_CODECS = {"hevc", "h265", "av1", "vp9"}


def _savings_note(info: VideoInfo, crf: int) -> str:
    """A short, honest read on how much this particular file can shrink.

    Compares the source's actual bitrate to the bitrate H.265 needs for the same
    resolution at transparent quality, rather than a raw bits-per-pixel number
    (which mislabels high-bitrate 4K as "already compressed").
    """
    if info.video_codec in ("hevc", "h265"):
        return ("⚠ Source is already H.265. Compressing it again saves little and "
                "may reduce quality, so keeping the original is usually better.")
    if info.video_codec in _EFFICIENT_CODECS:
        return (f"⚠ Source already uses an efficient codec ({info.video_codec}); "
                "re-encoding to H.265 may not shrink it much.")

    est = estimate_h265_bitrate_kbps(info.width, info.height, info.fps, crf)
    src_kbps = info.bit_rate / 1000 if info.bit_rate else None
    if not est or not src_kbps:
        return "Tuned for visually transparent H.265 at the source resolution."

    savings = 1 - est / src_kbps
    if savings <= 0.05:
        return ("Source is already efficiently compressed, so expect only a small "
                "reduction. Pushing further would start to cost visible quality.")
    pct = round(savings * 100)
    if savings >= 0.4:
        return f"Expect a large reduction (around {pct}%) at effectively the same quality."
    if savings >= 0.2:
        return f"Expect a moderate reduction (around {pct}%) at the same quality."
    return f"Expect a modest reduction (around {pct}%) at the same quality."
