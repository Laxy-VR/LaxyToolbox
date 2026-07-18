"""Shared constants, the Job model, and small formatting helpers."""

import os
from dataclasses import dataclass, field

import theme
from probe import VideoInfo

APP_NAME = "Laxy's Toolbox"
APP_VERSION = "1.7.0"
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
                      ("M4A (smaller, modern)", "m4a"),
                      ("Opus (smallest, modern)", "opus")]
AUD_QUALITY_OPTIONS = [("High (256k)", "256k"), ("Balanced (192k)", "192k"),
                       ("Small (128k)", "128k")]

# Max resolution when downloading (yt-dlp -S res: sorting; None = best available)
DL_RES_OPTIONS = [("Best available", None), ("Max 2160p", 2160),
                  ("Max 1080p", 1080), ("Max 720p", 720)]
# Borrow a browser's logged-in session: the reliable fix when a site serves
# only low quality (bot suspicion) or requires login. Firefox decrypts most
# reliably on Windows; newer Chrome/Edge sometimes block cookie access.
DL_COOKIES_OPTIONS = [("No cookies", None), ("Firefox", "firefox"),
                      ("Edge", "edge"), ("Chrome", "chrome")]

CODEC_OPTIONS = [("H.265 (recommended)", "h265"),
                 ("AV1 (smallest, modern devices)", "av1"),
                 ("H.264 (max compatibility)", "h264")]

# One-click setting bundles. Values are the menu *labels* (what the widgets
# show); a preset only sets the keys it lists, so it stays valid on any machine
# (unknown values are skipped). Users can save their own on top of these.
PRESET_PLACEHOLDER = "Presets ▾"
BUILTIN_PRESETS = {
    "Discord · under 500 MB": {"tab": "Compress", "mode": "Target size",
                               "size": "500", "codec": "H.265 (recommended)"},
    "Discord · under 10 MB": {"tab": "Compress", "mode": "Target size",
                              "size": "10", "codec": "H.265 (recommended)"},
    "Archive · top quality": {"tab": "Compress", "mode": "Best quality",
                              "codec": "H.265 (recommended)", "crf": 18,
                              "preset": "slow"},
    "Smallest file · AV1": {"tab": "Compress", "mode": "Target size", "size": "25",
                            "codec": "AV1 (smallest, modern devices)"},
}
# Hardware menu. Only vendors whose encoders exist in the bundled ffmpeg AND
# pass a real test encode on this machine are offered (see probe.gpu_works).
HW_OPTIONS = [("CPU (best quality)", "cpu"),
              ("NVIDIA GPU (fastest)", "nvenc"),
              ("AMD GPU (fastest)", "amf"),
              ("Intel GPU (fast)", "qsv")]

# Denoise (hqdn3d). Film grain and sensor noise eat bitrate; a light denoise
# makes grainy footage compress dramatically better at the same quality.
DENOISE_OPTIONS = [("No denoise", None),
                   ("Light", "hqdn3d=2:1.5:3:3"),
                   ("Medium", "hqdn3d=4:3:6:4.5")]

# Which audio track to keep. OBS style recordings often carry the mic and the
# game as separate tracks; "mix" folds every track into one. The menu only
# appears when a queued file actually has more than one audio track.
AUDIO_TRACK_OPTIONS = [("Auto (default track)", None),
                       ("Track 1", 0), ("Track 2", 1),
                       ("Track 3", 2), ("Track 4", 3),
                       ("Mix all tracks", "mix")]
PARTS_OPTIONS = [("Auto", None), ("2", 2), ("3", 3), ("4", 4), ("6", 6), ("8", 8)]

GIF_DITHER_OPTIONS = [("Bayer (clean)", "bayer:bayer_scale=5"),
                      ("Floyd-Steinberg (smooth)", "floyd_steinberg"),
                      ("None (flat colors)", "none")]
# Animated WebP is typically far smaller than GIF at better quality; an MP4
# loop is smaller still. GIF stays the default for maximum compatibility.
GIF_FORMAT_OPTIONS = [("GIF (classic)", "gif"),
                      ("WebP (much smaller)", "webp"),
                      ("MP4 loop (smallest)", "mp4")]
GIF_SPEED_OPTIONS = [("0.25x", 0.25), ("0.5x", 0.5), ("1x", 1.0),
                     ("1.5x", 1.5), ("2x", 2.0), ("4x", 4.0)]
GIF_DIRECTION_OPTIONS = [("Forward", "forward"), ("Reverse", "reverse"),
                         ("Boomerang", "boomerang")]
# Fewer palette colors shrink a GIF a lot on simple footage (gif only).
GIF_COLORS_OPTIONS = [("256 colors", 256), ("128 colors", 128),
                      ("64 colors", 64)]
# gifsicle --lossy level: higher = smaller file, more artifacts. Values are
# the sweet spots ezgif-style tools use across its 0..200 range.
GIF_LOSSY_OPTIONS = [("Off", None), ("Light", 30), ("Medium", 80),
                     ("Strong", 140)]
# Output height cap for loops (never upscales). File size grows with the
# square of this, so full-resolution GIFs get huge; 480p is plenty for chat.
# "Custom…" reveals exact width x height fields (those DO allow upscaling:
# typed dimensions are deliberate, e.g. a 128x128 emote from a small source).
GIF_SIZE_CUSTOM = "custom"
GIF_SIZE_OPTIONS = [("Max 480p", 480), ("Max 360p", 360), ("Max 240p", 240),
                    ("Max 720p", 720), ("Keep original", None),
                    ("Custom…", GIF_SIZE_CUSTOM)]
GIF_OUT_EXT = {"gif": ".gif", "webp": ".webp", "mp4": "_loop.mp4"}

# Crop the picture. "auto" detects each file's black bars at encode time;
# the ratio crops cut a centered window (for Shorts/TikTok or thumbnails).
CROP_OPTIONS = [("No crop", None),
                ("Remove black bars", "auto"),
                ("Vertical 9:16 (Shorts)", "9:16"),
                ("Square 1:1", "1:1")]

# Playback speed for the Compress tab (timelapses, slow motion). A speed
# change re-times the audio with atempo, which always re-encodes it.
SPEED_OPTIONS = [("1x (normal)", 1.0), ("0.25x", 0.25), ("0.5x", 0.5),
                 ("1.5x", 1.5), ("2x", 2.0), ("4x", 4.0)]

# Rotation/flip for phone videos recorded sideways. Values are ffmpeg filters.
ROTATE_OPTIONS = [("No rotation", None),
                  ("Rotate 90° right", "transpose=1"),
                  ("Rotate 90° left", "transpose=2"),
                  ("Rotate 180°", "hflip,vflip"),
                  ("Flip horizontal", "hflip"),
                  ("Flip vertical", "vflip")]

# Burn-in subtitles: auto finds a same-named .srt/.ass/.vtt next to each video.
SUBS_NONE = "No subtitles"
SUBS_AUTO = "Auto (matching .srt)"
SUBS_PICK = "Choose file…"
SUB_EXTS = (".srt", ".ass", ".vtt")

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
                 ("Boost quiet audio", ("boost", "192k")),
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


def parse_time(text) -> float | None:
    """Seconds from '90', '90.5', '1:30', or '1:02:03.5'. None when invalid.

    Every time field accepts both plain seconds and clock style, because
    players show 1:30 and nobody wants to compute 90 in their head."""
    s = str(text or "").strip()
    if not s or "-" in s:
        return None
    parts = s.split(":")
    if len(parts) > 3:
        return None
    total = 0.0
    for part in parts:
        try:
            total = total * 60 + float(part or 0)
        except ValueError:
            return None
    return total


def human_size(num_bytes) -> str:
    if not num_bytes:
        return "unknown"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


# Raw ffmpeg / yt-dlp output is cryptic. Map the tell tale phrases we have seen
# to a plain sentence a non technical friend can act on. Order matters: the
# most specific phrases come first. The raw log is still saved for diagnosis.
_ERROR_HINTS = [
    # disk and output file
    (("no space left", "not enough space", "disk full"),
     "Your disk is full. Free up some space and try again."),
    (("permission denied", "error opening output", "could not open file",
      "unable to open"),
     "Could not save the file. Close it if it is open in a player, or choose a "
     "different output folder."),
    # damaged or missing source
    (("moov atom not found", "invalid data found", "could not find codec "
      "parameters", "error while decoding", "header missing"),
     "The source file looks incomplete or damaged. Try playing it first to "
     "check it works."),
    (("no such file", "does not exist"),
     "The source file was moved or deleted before it could be processed."),
    (("not divisible by 2", "divisible by 2"),
     "That video has an unusual size. Try a different resolution setting."),
    # GPU encoding
    (("cannot load nvcuda", "openencodesessionex", "no capable devices",
      "initializeencoder failed", "nvenc", "createcomponent", "_amf",
      "mfx session", "_qsv"),
     "Your graphics card could not handle this encode. Set Hardware to CPU and "
     "try again."),
    # downloads: connectivity
    (("getaddrinfo", "failed to resolve", "name resolution",
      "unable to download webpage", "network is unreachable",
      "temporary failure", "urlopen error", "connection timed out",
      "timed out"),
     "No internet connection, or the site did not respond. Check your "
     "connection and try again."),
    # downloads: availability
    (("private video", "this video is private"),
     "That video is private, so it cannot be downloaded."),
    (("video unavailable", "no longer available", "removed by the user",
      "account associated with this video has been terminated"),
     "That video is no longer available."),
    (("confirm your age", "age restricted", "age-restricted", "inappropriate "
      "for some users"),
     "That video is age restricted. Pick a browser under Cookies to use your "
     "signed in session."),
    (("members-only", "members only", "join this channel",
      "available to music premium"),
     "That video is for members only, so it cannot be downloaded."),
    (("requested format is not available", "requested format not available"),
     "The quality you asked for is not offered for this video. Try Best "
     "available under the resolution menu."),
    (("http error 429", "too many requests"),
     "The site is limiting downloads right now. Wait a while and try again."),
    (("http error 403", "forbidden"),
     "The site refused the download. Pick a browser under Cookies to use your "
     "signed in session."),
    (("this live event will begin", "premieres in", "live event will begin",
      "not started"),
     "That video is a scheduled stream that has not started yet."),
    (("unsupported url",),
     "That link is not from a site the downloader supports."),
    (("is not a valid url", "not a valid url"),
     "That does not look like a valid link. Check you copied the whole URL."),
    (("unable to extract", "unable to find"),
     "The site changed and the downloader could not read it. Try again later, "
     "or use a different link."),
]


def friendly_error(raw) -> str:
    """Turn a raw ffmpeg/yt-dlp failure into one plain, actionable sentence.

    `raw` may be a string or a list of log lines. Falls back to a tidied
    version of the last line when nothing matches a known pattern."""
    if raw is None:
        return "Something went wrong."
    lines = raw if isinstance(raw, (list, tuple)) else str(raw).splitlines()
    lines = [str(ln).strip() for ln in lines if str(ln).strip()]
    if not lines:
        return "Something went wrong."
    blob = "\n".join(lines).lower()
    for needles, message in _ERROR_HINTS:
        if any(n in blob for n in needles):
            return message
    # No known pattern: show the last line, minus noisy prefixes like
    # "ERROR:" or "[youtube] abc123:" so it reads a little cleaner.
    last = lines[-1]
    for prefix in ("ERROR: ", "ERROR:"):
        if last.startswith(prefix):
            last = last[len(prefix):].strip()
    if last.startswith("[") and "] " in last:
        last = last.split("] ", 1)[1].strip()
    return last or "Something went wrong."


@dataclass
class Job:
    id: int
    path: str
    info: VideoInfo | None = None
    error: str | None = None
    # reading|ready|unsupported|queued|encoding|done|failed|cancelled,
    # plus downloading|downloaded for jobs from the Download tab
    status: str = "reading"
    progress: float = 0.0
    output: str | None = None
    outputs: list = field(default_factory=list)  # all output files (>1 when split)
    out_size: int | None = None                  # total bytes written
    limit_mb: float | None = None                # per-file size limit (target/split)
    over_limit: bool = False                     # an output exceeded that limit
    trim: tuple | None = None    # per-file (start, end) trim; wins over shared
    crop: tuple | None = None    # per-file (w, h, x, y) crop from the crop box
    from_url: bool = False                       # arrived via the Download tab
    dl_cap: int | None = None                    # resolution cap chosen, None = best
    est_size: int | None = None                  # rough predicted output bytes
    thumb_png: bytes | None = field(default=None, repr=False)  # row preview
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
    if job.status == "ready" and job.est_size:
        return (f"ready · ~{human_size(job.est_size)}", theme.TEXT)
    return {
        "reading": ("reading…", theme.TEXT_MUTED),
        "ready": ("ready", theme.TEXT),
        "unsupported": ("unsupported", theme.ERROR),
        "queued": ("queued", theme.TEXT_MUTED),
        "failed": ("failed", theme.ERROR),
        "cancelled": ("cancelled", theme.TEXT_MUTED),
    }[job.status]
