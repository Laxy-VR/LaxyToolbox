"""Shared constants, the Job model, and small formatting helpers."""

import os
from dataclasses import dataclass, field

import theme
from probe import VideoInfo

APP_NAME = "Laxy's Compressor"
APP_VERSION = "1.0.3"
# The app checks this repo's latest GitHub release at startup and offers
# updates. Empty string disables the check entirely.
GITHUB_REPO = "Laxy-VR/LaxyToolbox"
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".laxy_compressor.json")

TAB_COMPRESS = "Compress"
TAB_GIF = "GIF"
TAB_IMAGE = "Images"
TAB_AUDIO = "Audio"
TAB_DOWNLOAD = "Download"

MODE_QUALITY = "Best quality"
MODE_TARGET = "Target size"
MODE_SPLIT = "Split to fit"
MODE_GIF = "Make GIF"        # internal effective mode when the GIF tab is active
MODE_IMAGE = "Compress images"  # internal effective mode for the Images tab
MODE_AUDIO = "Extract audio"    # internal effective mode for the Audio tab
MODE_DOWNLOAD = "Download"      # internal effective mode for the Download tab

AUD_FORMAT_OPTIONS = [("MP3 (plays everywhere)", "mp3"),
                      ("M4A (smaller, modern)", "m4a")]
AUD_QUALITY_OPTIONS = [("High (256k)", "256k"), ("Balanced (192k)", "192k"),
                       ("Small (128k)", "128k")]

# Max resolution when downloading (yt-dlp -S res: sorting; None = best available)
DL_RES_OPTIONS = [("Best available", None), ("Max 2160p", 2160),
                  ("Max 1080p", 1080), ("Max 720p", 720)]

CODEC_OPTIONS = [("H.265 (recommended)", "h265"),
                 ("AV1 (smallest, modern devices)", "av1"),
                 ("H.264 (max compatibility)", "h264")]
HW_OPTIONS = [("CPU (best quality)", "cpu"), ("GPU (fastest)", "nvenc")]
PARTS_OPTIONS = [("Auto", None), ("2", 2), ("3", 3), ("4", 4), ("6", 6), ("8", 8)]

GIF_DITHER_OPTIONS = [("Bayer (clean)", "bayer:bayer_scale=5"),
                      ("Floyd-Steinberg (smooth)", "floyd_steinberg"),
                      ("None (flat colors)", "none")]

IMG_FORMAT_OPTIONS = [("WebP (recommended)", "webp"),
                      ("AVIF (smallest)", "avif"),
                      ("JPEG (max compatibility)", "jpeg")]
IMG_QUALITY_OPTIONS = [("High (near lossless)", "high"),
                       ("Balanced", "balanced"),
                       ("Small", "small")]
# Resize is either a multiplier ("mul") or a max height cap ("h", never upscales).
IMG_RESIZE_OPTIONS = [("Keep original", None),
                      ("2x larger", ("mul", 2.0)),
                      ("1.5x larger", ("mul", 1.5)),
                      ("0.5x smaller", ("mul", 0.5)),
                      ("Max 2160p", ("h", 2160)),
                      ("Max 1080p", ("h", 1080)),
                      ("Max 720p", ("h", 720))]

PRESETS = ["ultrafast", "superfast", "veryfast", "faster",
           "fast", "medium", "slow", "slower", "veryslow"]
RESOLUTIONS = [("Keep original", None), ("2160p (4K)", 2160), ("1440p", 1440),
               ("1080p", 1080), ("720p", 720), ("480p", 480)]
FPS_OPTIONS = [("Keep original", None), ("60", 60), ("30", 30), ("24", 24),
               ("15", 15), ("10", 10)]
AUDIO_OPTIONS = [("Copy (no re-encode)", ("copy", None)), ("AAC 192k", ("aac", "192k")),
                 ("AAC 128k", ("aac", "128k")), ("AAC 96k", ("aac", "96k")),
                 ("Remove audio", ("none", None))]

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv",
              ".flv", ".mpg", ".mpeg", ".ts", ".m2ts", ".gif"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".avif"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma"}
MEDIA_EXTS = VIDEO_EXTS | IMAGE_EXTS | AUDIO_EXTS


def is_image(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXTS


def is_audio(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in AUDIO_EXTS


def kind_icon(path: str) -> str:
    """A small glyph so mixed queues scan at a glance."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".gif":
        return "🎞"
    if ext in IMAGE_EXTS:
        return "🖼"
    if ext in AUDIO_EXTS:
        return "🎵"
    if ext in VIDEO_EXTS:
        return "🎬"
    return "🌐"  # a URL still downloading


def unique_path(path: str, used: set) -> str:
    """Keep two queue items from writing the same file (e.g. clip.mp4 and
    clip.mkv, or same-named files from different folders with one shared
    output folder). Appends _2, _3… until the path is unique in this batch.
    `used` holds normalized paths already claimed and is updated in place."""
    candidate = path
    n = 2
    while os.path.normcase(os.path.abspath(candidate)) in used:
        stem, ext = os.path.splitext(path)
        candidate = f"{stem}_{n}{ext}"
        n += 1
    used.add(os.path.normcase(os.path.abspath(candidate)))
    return candidate


def human_size(num_bytes) -> str:
    if not num_bytes:
        return "unknown"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


@dataclass
class Job:
    id: int
    path: str
    info: VideoInfo | None = None
    error: str | None = None
    status: str = "reading"  # reading|ready|unsupported|queued|encoding|done|failed|cancelled
    progress: float = 0.0
    output: str | None = None
    outputs: list = field(default_factory=list)  # all output files (>1 when split)
    out_size: int | None = None                  # total bytes written
    limit_mb: float | None = None                # per-file size limit (target/split)
    over_limit: bool = False                     # an output exceeded that limit
    from_url: bool = False                       # arrived via the Download tab
    dl_cap: int | None = None                    # resolution cap chosen, None = best
    row: object = field(default=None, repr=False)  # QueueRow, set by the app


def status_display(job: Job):
    """(text, colour) for a job's current status."""
    if job.status == "encoding":
        return (f"encoding {int(job.progress * 100)}%", theme.ACCENT_HOVER)
    if job.status == "downloading":
        return (f"downloading {int(job.progress * 100)}%", theme.ACCENT_HOVER)
    if job.status == "done":
        if job.over_limit:
            return ("done · over limit!", theme.WARNING)
        if job.out_size and job.info and job.info.size_bytes:
            pct = (1 - job.out_size / job.info.size_bytes) * 100
            if pct >= 0:
                return (f"done · {pct:.0f}% smaller", theme.SUCCESS)
            return (f"done · {abs(pct):.0f}% larger", theme.ERROR)
        return ("done ✓", theme.SUCCESS)
    if job.status == "downloaded":
        # Show what quality actually arrived; sites sometimes serve less than
        # asked, and this makes that visible before anyone hits play.
        if job.info and job.info.height:
            return (f"downloaded ✓ · {job.info.height}p", theme.SUCCESS)
        return ("downloaded ✓", theme.SUCCESS)
    return {
        "reading": ("reading…", theme.TEXT_MUTED),
        "ready": ("ready", theme.TEXT),
        "unsupported": ("unsupported", theme.ERROR),
        "queued": ("queued", theme.TEXT_MUTED),
        "failed": ("failed", theme.ERROR),
        "cancelled": ("cancelled", theme.TEXT_MUTED),
    }[job.status]
