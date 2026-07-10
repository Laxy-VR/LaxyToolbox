"""Build and run the ffmpeg H.265 encode, reporting live progress."""

import glob
import os
import subprocess
from collections import deque

from probe import NO_WINDOW, FFMPEG


def _video_filters(settings: dict) -> str:
    """Build the -vf filter chain from optional downscale / fps changes.

    scale=-2:H keeps the aspect ratio and forces an even width (H.265 needs
    even dimensions). fps=N drops the frame rate, which gives the encoder far
    more bits per frame at a fixed target size.
    """
    parts = []
    if _needs_tonemap(settings):
        parts.append(TONEMAP)
    if settings.get("target_height"):
        parts.append(f"scale=-2:{settings['target_height']}")
    if settings.get("target_fps"):
        parts.append(f"fps={settings['target_fps']}")
    return ",".join(parts)


def _audio_args(settings: dict) -> list[str]:
    if settings["audio_mode"] == "none":
        return ["-an"]  # strip the audio track entirely
    if settings["audio_mode"] == "copy":
        return ["-c:a", "copy"]
    return ["-c:a", "aac", "-b:a", str(settings["audio_bitrate"])]


def video_bitrate_for_target(duration: float, target_mb: float,
                             audio_kbps: int, safety: float = 0.95) -> float:
    """Video bitrate (kbps) needed to land a `duration`s clip near target_mb.

    The safety factor leaves headroom for container overhead and the small
    variance of bitrate targeting, so we stay comfortably under the limit.
    """
    if duration <= 0:
        return 0.0
    target_bits = target_mb * 1024 * 1024 * 8 * safety
    total_kbps = target_bits / duration / 1000
    return total_kbps - audio_kbps


PROGRESS = ["-progress", "pipe:1", "-nostats"]

# One codec table drives every command. crf_off maps the app's single quality
# slider (x265-scaled, 16..32) onto each codec's own CRF/CQ scale so the same
# slider position gives roughly the same visual quality everywhere.
# `tag` makes H.265 mp4s play in QuickTime / on Apple devices.
CODECS = {
    "h265": {"cpu": "libx265", "gpu": "hevc_nvenc", "crf_off": 0,
             "tag": ["-tag:v", "hvc1"]},
    "av1":  {"cpu": "libsvtav1", "gpu": "av1_nvenc", "crf_off": 7, "tag": []},
    "h264": {"cpu": "libx264", "gpu": "h264_nvenc", "crf_off": -4, "tag": []},
}

# SVT-AV1 takes numeric presets (0 = slowest/best) instead of x264/x265 names.
_SVT_PRESETS = {"ultrafast": 12, "superfast": 11, "veryfast": 10, "faster": 9,
                "fast": 8, "medium": 6, "slow": 5, "slower": 4, "veryslow": 3}

# 4:2:0 8-bit plays everywhere and is required by the NVENC encoders; 4:4:4
# screen recordings are converted rather than failing.
PIXFMT = ["-pix_fmt", "yuv420p"]

# HDR (PQ/HLG) squeezed to SDR without tone mapping looks washed out and grey,
# so when the output must be 8-bit SDR we run a proper tone map first.
TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,"
           "tonemap=tonemap=hable:desat=0,"
           "zscale=p=bt709:t=bt709:m=bt709:r=tv,format=yuv420p")


def _keeps_hdr(settings: dict) -> bool:
    """H.265/AV1 carry 10-bit HDR natively, so those outputs stay untouched."""
    return (settings.get("codec", "h265") in ("h265", "av1")
            and settings.get("src_10bit"))


def _pix_args(settings: dict, gpu: bool) -> list:
    if _keeps_hdr(settings):
        return ["-pix_fmt", "p010le" if gpu else "yuv420p10le"]
    return list(PIXFMT)


def _needs_tonemap(settings: dict) -> bool:
    return bool(settings.get("src_hdr")) and not _keeps_hdr(settings)


def _codec_crf(settings: dict) -> int:
    info = CODECS[settings.get("codec", "h265")]
    return max(0, min(51, int(settings["crf"]) + info["crf_off"]))


def _input_args(input_path: str, segment) -> list[str]:
    """Base ffmpeg args. `segment` (start, dur) trims one piece for splitting;
    -ss before -i is a fast seek, -t after -i limits the duration."""
    args = [FFMPEG, "-y"]
    if segment:
        args += ["-ss", f"{segment[0]:.3f}"]
    args += ["-i", input_path]
    if segment:
        args += ["-t", f"{segment[1]:.3f}"]
    return args


def _cpu_quality_args(settings: dict) -> list[str]:
    codec = settings.get("codec", "h265")
    info = CODECS[codec]
    crf = _codec_crf(settings)
    if codec == "av1":
        preset = _SVT_PRESETS.get(str(settings["preset"]), 6)
        return ["-c:v", info["cpu"], "-preset", str(preset), "-crf", str(crf)]
    return ["-c:v", info["cpu"], "-preset", str(settings["preset"]), "-crf", str(crf)]


def _gpu_quality_args(settings: dict) -> list[str]:
    info = CODECS[settings.get("codec", "h265")]
    return ["-c:v", info["gpu"], "-preset", "p5", "-rc", "vbr",
            "-cq", str(_codec_crf(settings)), "-b:v", "0"]


def build_stages(input_path: str, output_path: str, settings: dict, mode: str,
                 passlog: str | None = None, segment=None):
    """Return a list of (label, command) stages for one output file.

    mode "quality": single constant-quality pass (CRF on CPU, CQ on NVENC).
    mode "target":  size-targeted. x265/x264 use a real 2-pass; SVT-AV1 uses
                    single-pass ABR; NVENC uses its internal full-res multipass.
    `segment` (start, dur) encodes just that slice, used by split-to-fit.
    """
    codec = settings.get("codec", "h265")
    info = CODECS[codec]
    gpu = settings.get("encoder") == "nvenc"
    vf = _video_filters(settings)
    filt = ["-vf", vf] if vf else []
    base = _input_args(input_path, segment)
    tag = info["tag"]

    if mode == "target" and not gpu and codec in ("h265", "h264"):
        vb = f"{int(settings['video_bitrate'])}k"
        common = base + ["-c:v", info["cpu"], "-preset", str(settings["preset"]),
                         "-b:v", vb] + _pix_args(settings, gpu)
        if codec == "h265":
            p1 = ["-x265-params", "pass=1", "-passlogfile", passlog]
            p2 = ["-x265-params", "pass=2", "-passlogfile", passlog]
        else:  # x264 uses native -pass flags
            p1 = ["-pass", "1", "-passlogfile", passlog]
            p2 = ["-pass", "2", "-passlogfile", passlog]
        pass1 = common + filt + p1 + ["-an"] + PROGRESS + ["-f", "null", os.devnull]
        pass2 = common + filt + p2 + tag + _audio_args(settings) + PROGRESS + [output_path]
        return [("analyze", pass1), ("encode", pass2)]

    if mode == "target" and not gpu:  # SVT-AV1: single-pass average bitrate
        vb = f"{int(settings['video_bitrate'])}k"
        preset = _SVT_PRESETS.get(str(settings["preset"]), 6)
        cmd = base + ["-c:v", info["cpu"], "-preset", str(preset), "-b:v", vb] \
            + _pix_args(settings, gpu) + filt + _audio_args(settings) \
            + PROGRESS + [output_path]
        return [("encode", cmd)]

    if mode == "target":  # NVENC size target (internal multipass, single stage)
        vb = int(settings["video_bitrate"])
        vargs = ["-c:v", info["gpu"], "-preset", "p5", "-rc", "vbr",
                 "-b:v", f"{vb}k", "-maxrate", f"{vb}k", "-bufsize", f"{2 * vb}k",
                 "-multipass", "fullres"]
        cmd = base + vargs + _pix_args(settings, gpu) + tag + filt \
            + _audio_args(settings) + PROGRESS + [output_path]
        return [("encode", cmd)]

    # quality mode, single pass
    vargs = _gpu_quality_args(settings) if gpu else _cpu_quality_args(settings)
    cmd = base + vargs + _pix_args(settings, gpu) + tag + filt + _audio_args(settings) \
        + PROGRESS + [output_path]
    return [("encode", cmd)]


def build_gif_stages(input_path: str, output_path: str, settings: dict, segment=None):
    """One-pass palette GIF: build an optimal 256-colour palette and apply it in
    the same ffmpeg run via split / palettegen / paletteuse. `segment` trims a
    short clip, which is what you almost always want for a GIF.

    stats_mode=diff biases the palette toward moving areas and diff_mode
    re-encodes only changed rectangles, which improves colours and shrinks the
    file on typical clips.
    """
    fps = settings.get("target_fps") or 15  # GIFs want a low frame rate
    dither = settings.get("gif_dither", "bayer:bayer_scale=5")
    chain = []
    if settings.get("src_hdr"):  # GIF palettes are SDR; tone map HDR first
        chain.append(TONEMAP)
    chain.append(f"fps={fps}")
    if settings.get("target_height"):
        chain.append(f"scale=-2:{settings['target_height']}:flags=lanczos")
    filtergraph = (",".join(chain) +
                   ",split[s0][s1];[s0]palettegen=max_colors=256:stats_mode=diff[p];"
                   f"[s1][p]paletteuse=dither={dither}:diff_mode=rectangle")
    cmd = _input_args(input_path, segment) + \
        ["-filter_complex", filtergraph, "-loop", "0"] + PROGRESS + [output_path]
    return [("gif", cmd)]


def build_cut_stages(input_path: str, output_path: str, segment):
    """Cut `segment` (start, dur) without re-encoding: -c copy is instant and
    lossless, but cut points snap to the nearest keyframes, so the result can
    start up to a few seconds before the requested time."""
    start, dur = segment
    cmd = [FFMPEG, "-y", "-ss", f"{start:.3f}", "-i", input_path,
           "-t", f"{dur:.3f}", "-c", "copy", "-avoid_negative_ts", "make_zero"] \
        + PROGRESS + [output_path]
    return [("cut", cmd)]


AUD_ENCODERS = {"mp3": ("libmp3lame", ".mp3"), "m4a": ("aac", ".m4a")}


def build_audio_stages(input_path: str, output_path: str, settings: dict):
    """One command extracting/converting the audio track to MP3 or M4A."""
    enc, _ = AUD_ENCODERS[settings.get("aud_format", "mp3")]
    bitrate = settings.get("aud_bitrate", "192k")
    cmd = [FFMPEG, "-y", "-i", input_path, "-vn", "-c:a", enc,
           "-b:a", str(bitrate)] + PROGRESS + [output_path]
    return [("audio", cmd)]


# Per-format quality values for the three image quality levels. WebP uses
# 0..100 (higher = better); AVIF uses AV1 CRF (lower = better); JPEG uses
# mjpeg's q scale 2..31 (lower = better).
IMG_QUALITY = {
    "webp": {"high": 92, "balanced": 80, "small": 62},
    "avif": {"high": 22, "balanced": 30, "small": 38},
    "jpeg": {"high": 3, "balanced": 6, "small": 10},
}
IMG_EXT = {"webp": ".webp", "avif": ".avif", "jpeg": ".jpg"}


def _image_vf(settings: dict) -> str:
    """Scale filter for image resize. AVIF encodes as yuv420, which requires
    even dimensions, so its sizes are always rounded down to even."""
    even = settings.get("img_format", "webp") == "avif"
    resize = settings.get("img_resize")
    if resize and resize[0] == "mul":
        f = resize[1]
        return f"scale=trunc(iw*{f}/2)*2:trunc(ih*{f}/2)*2:flags=lanczos"
    if resize and resize[0] == "h":  # cap height, never upscale (min with ih)
        h = resize[1]
        if even:
            return f"scale=-2:trunc(min({h}\\,ih)/2)*2:flags=lanczos"
        return f"scale=-2:min({h}\\,ih):flags=lanczos"
    if even:
        return "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    return ""


def build_image_stages(input_path: str, output_path: str, settings: dict):
    """One command converting a still image to WebP / AVIF / JPEG."""
    fmt = settings.get("img_format", "webp")
    q = IMG_QUALITY[fmt][settings.get("img_quality", "balanced")]
    vargs = {
        "webp": ["-c:v", "libwebp", "-quality", str(q)],
        "avif": ["-c:v", "libaom-av1", "-crf", str(q), "-b:v", "0",
                 "-still-picture", "1", "-pix_fmt", "yuv420p"],
        "jpeg": ["-c:v", "mjpeg", "-q:v", str(q), "-pix_fmt", "yuvj420p"],
    }[fmt]
    vf = _image_vf(settings)
    cmd = [FFMPEG, "-y", "-i", input_path] + (["-vf", vf] if vf else []) \
        + vargs + ["-frames:v", "1"] + PROGRESS + [output_path]
    return [("image", cmd)]


def suggest_parts(duration: float, max_mb: float, width: int, height: int,
                  fps: float, audio_kbps: int = 128, target_bpp: float = 0.025,
                  max_parts: int = 20) -> int:
    """Fewest equal-length parts so each part reaches roughly "okay" quality
    (target bits-per-pixel) when squeezed into max_mb."""
    if duration <= 0 or not (width and height and fps):
        return 1
    for n in range(1, max_parts + 1):
        vkbps = video_bitrate_for_target(duration / n, max_mb, audio_kbps)
        if vkbps > 0 and (vkbps * 1000) / (width * height * fps) >= target_bpp:
            return n
    return max_parts


def cleanup_passlogs(passlog: str) -> None:
    """Remove the stats files a 2-pass encode leaves behind."""
    for path in glob.glob(passlog + "*"):
        try:
            os.remove(path)
        except OSError:
            pass


def _time_to_seconds(value: str) -> float:
    """Parse ffmpeg's 'HH:MM:SS.microseconds' time string into seconds."""
    try:
        hh, mm, ss = value.split(":")
        return int(hh) * 3600 + int(mm) * 60 + float(ss)
    except ValueError:
        return 0.0


def run_encode(cmd, duration, on_progress, cancel_event):
    """Run ffmpeg, calling on_progress(fraction 0..1, speed) as it advances.

    `speed` is ffmpeg's encode speed relative to real time (e.g. 2.5 means 2.5x),
    or None if unknown. Returns (returncode, tail_lines); returncode is None if
    cancelled. on_progress is called from THIS thread, so the caller must marshal
    any GUI updates back to the main thread.
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # merge log into one stream to avoid deadlock
        text=True,
        bufsize=1,
        creationflags=NO_WINDOW,
    )

    tail = deque(maxlen=25)  # keep the last lines around for error messages
    speed = None
    for line in proc.stdout:
        if cancel_event.is_set():
            proc.terminate()
            proc.wait()
            return None, list(tail)

        line = line.strip()
        if line:
            tail.append(line)

        if line.startswith("speed="):
            raw = line.split("=", 1)[1].strip().rstrip("x")
            try:
                speed = float(raw)
            except ValueError:
                speed = None
        elif line.startswith("out_time=") and duration > 0:
            seconds = _time_to_seconds(line.split("=", 1)[1])
            on_progress(min(seconds / duration, 1.0), speed)
        elif line == "progress=end":
            on_progress(1.0, speed)

    proc.wait()
    return proc.returncode, list(tail)
