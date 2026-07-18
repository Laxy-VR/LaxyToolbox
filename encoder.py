"""Build and run the ffmpeg H.265 encode, reporting live progress."""

import glob
import os
import subprocess
from collections import deque

from probe import NO_WINDOW, FFMPEG, GIFSICLE, GPU_ENCODERS
from sysutil import track_child, untrack_child

GPU_VENDORS = tuple(GPU_ENCODERS)  # ("nvenc", "amf", "qsv")


def _is_gpu(settings: dict) -> bool:
    return settings.get("encoder") in GPU_VENDORS


def _escape_chars(s: str, chars: str) -> str:
    return "".join(("\\" + c) if c in chars else c for c in s)


def _subtitles_filter(path: str) -> str:
    """The subtitles burn-in filter with the path escaped for a filtergraph.

    ffmpeg parses the string twice (the graph, then the filter's own options),
    so special characters need TWO levels of backslash escaping; a drive colon
    ends up as C\\\\\\:. This is the scheme from ffmpeg's own filtergraph
    escaping docs; quoting instead breaks on the second parse.
    """
    p = os.path.abspath(path).replace("\\", "/")
    p = _escape_chars(p, "\\':")       # level 2: the filter's option value
    p = _escape_chars(p, "\\',;[]")    # level 1: the filtergraph string
    return f"subtitles=filename={p}"


def _video_filters(settings: dict) -> str:
    """Build the -vf filter chain from optional rotate / downscale / fps /
    subtitle changes.

    scale=-2:H keeps the aspect ratio and forces an even width (H.265 needs
    even dimensions). fps=N drops the frame rate, which gives the encoder far
    more bits per frame at a fixed target size. Subtitles render last so they
    stay crisp at the final output resolution.
    """
    parts = []
    # Deinterlace before anything else touches the frames: comb artifacts
    # survive scaling and look terrible encoded. Applied automatically when
    # the probe saw an interlaced field order; progressive sources skip it.
    if settings.get("src_interlaced"):
        parts.append("bwdif")
    # Crop next: it applies to the source picture (auto detection measured
    # the source) and cuts the pixels every later filter has to process.
    if settings.get("crop_filter"):      # per-file "crop=w:h:x:y" from auto
        parts.append(settings["crop_filter"])
    elif settings.get("crop") == "9:16":  # centered vertical window (Shorts)
        parts.append("crop=min(iw\\,trunc(ih*9/16/2)*2):ih")
    elif settings.get("crop") == "1:1":   # centered square, even for yuv420
        parts.append("crop=trunc(min(iw\\,ih)/2)*2:trunc(min(iw\\,ih)/2)*2")
    if _needs_tonemap(settings):
        parts.append(TONEMAP)
    if settings.get("rotate"):
        parts.append(settings["rotate"])
    if settings.get("denoise"):
        # Denoise at the source resolution, before any downscale: hqdn3d
        # works best on the original pixels, and grain is what eats bitrate.
        parts.append(settings["denoise"])
    if settings.get("target_height"):
        parts.append(f"scale=-2:{settings['target_height']}")
    speed = float(settings.get("speed") or 1.0)
    if speed != 1.0:
        # Subtitles burn in BEFORE the speed change: their clock follows the
        # original timestamps, so re-timing first would desync them. Then
        # setpts re-times the frames, and fps last locks the requested
        # output rate (the GIF chain uses the same ordering).
        if settings.get("subtitles"):
            parts.append(_subtitles_filter(settings["subtitles"]))
        parts.append(f"setpts=PTS/{speed}")
        if settings.get("target_fps"):
            parts.append(f"fps={settings['target_fps']}")
    else:
        if settings.get("target_fps"):
            parts.append(f"fps={settings['target_fps']}")
        if settings.get("subtitles"):
            parts.append(_subtitles_filter(settings["subtitles"]))
    return ",".join(parts)


def _atempo_chain(speed: float) -> list[str]:
    """atempo filters matching a video speed change ([] at 1x). One atempo
    covers 0.5x..100x; slower speeds chain halvings (0.25x = two 0.5x)."""
    if speed == 1.0 or speed <= 0:
        return []
    chain = []
    while speed < 0.5:
        chain.append("atempo=0.5")
        speed *= 2
    chain.append(f"atempo={speed:g}")
    return chain


def _audio_args(settings: dict) -> list[str]:
    if settings["audio_mode"] == "none":
        return ["-an"]  # strip the audio track entirely
    chain = _atempo_chain(float(settings.get("speed") or 1.0))
    if settings["audio_mode"] == "boost":
        # EBU R128 loudness normalisation lifts quiet audio (gameplay mics) to
        # a standard level; same filter as the Audio tab's Normalize. loudnorm
        # resamples to 192 kHz internally, so pin a sane output rate. It runs
        # after any atempo so the loudness is measured on the final timing.
        chain.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        return ["-af", ",".join(chain), "-ar", "48000",
                "-c:a", "aac", "-b:a", str(settings.get("audio_bitrate") or "192k")]
    if chain:  # a speed change must re-time the audio, so copy re-encodes
        bitrate = settings["audio_bitrate"] if settings["audio_mode"] == "aac" \
            else "192k"
        return ["-af", ",".join(chain), "-c:a", "aac", "-b:a", str(bitrate)]
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
# slider position gives roughly the same visual quality everywhere. GPU
# encoder names come from probe.GPU_ENCODERS (one per vendor per codec).
# `tag` makes H.265 mp4s play in QuickTime / on Apple devices.
CODECS = {
    "h265": {"cpu": "libx265", "crf_off": 0, "tag": ["-tag:v", "hvc1"]},
    "av1":  {"cpu": "libsvtav1", "crf_off": 7, "tag": []},
    "h264": {"cpu": "libx264", "crf_off": -4, "tag": []},
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
    """Base ffmpeg args. `segment` (start, dur) trims one piece for splitting.

    Both -ss and -t go BEFORE -i, so they trim the INPUT: the filters only
    ever see the clip. With -t after -i it becomes an output-duration cap,
    which breaks every filter that stretches the timeline (speed, reverse,
    boomerang: the whole source gets processed, then the result is chopped),
    and it forces palette GIFs to read the entire source before writing
    anything, which looks like a hang on long videos.
    """
    args = [FFMPEG, "-y"]
    if segment:
        args += ["-ss", f"{segment[0]:.3f}", "-t", f"{segment[1]:.3f}"]
    args += ["-i", input_path]
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
    """Constant quality args for the selected GPU vendor's encoder.

    Each vendor has its own idea of constant quality: NVENC's CQ, AMF's CQP,
    and QSV's ICQ (-global_quality) all sit on roughly the H.264/HEVC 0..51
    scale, so the shared slider mapping carries over.
    """
    codec = settings.get("codec", "h265")
    vendor = settings["encoder"]
    enc = GPU_ENCODERS[vendor][codec]
    q = _codec_crf(settings)
    if vendor == "nvenc":
        return ["-c:v", enc, "-preset", "p5", "-rc", "vbr",
                "-cq", str(q), "-b:v", "0"]
    if vendor == "amf":
        if codec == "av1":
            q = min(255, q * 5)  # AV1 AMF quantizes on a 0..255 scale
        args = ["-c:v", enc, "-quality", "quality", "-rc", "cqp",
                "-qp_i", str(q), "-qp_p", str(q)]
        if codec == "h264":
            args += ["-qp_b", str(q)]
        return args
    return ["-c:v", enc, "-preset", "slower", "-global_quality", str(q)]


def _track_args(settings: dict):
    """(-map arguments, audio args override) for the audio track choice.

    Default (auto) adds nothing: ffmpeg keeps picking its default streams. A
    numbered track maps that stream explicitly (the trailing ? keeps batch
    files with fewer tracks working). "mix" folds every track into one with
    amix; a mix cannot be stream copied, so copy falls back to AAC, and the
    boost mode's loudnorm joins the mix graph (an -af on a stream fed by a
    complex graph is an ffmpeg error).
    """
    track = settings.get("audio_track")
    if track is None or settings["audio_mode"] == "none":
        return [], None
    if track != "mix":
        return ["-map", "0:v:0", "-map", f"0:a:{int(track)}?"], None
    n = int(settings.get("audio_track_count") or 0)
    if n < 2:
        return [], None  # nothing to mix; keep the default streams
    chain = ["".join(f"[0:a:{i}]" for i in range(n))
             + f"amix=inputs={n}:duration=longest"]
    chain += _atempo_chain(float(settings.get("speed") or 1.0))
    if settings["audio_mode"] == "boost":
        chain.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        aargs = ["-ar", "48000", "-c:a", "aac",
                 "-b:a", str(settings.get("audio_bitrate") or "192k")]
    else:
        bitrate = settings["audio_bitrate"] if settings["audio_mode"] == "aac" \
            else "192k"
        aargs = ["-c:a", "aac", "-b:a", str(bitrate)]
    graph = ",".join(chain)
    return (["-filter_complex", graph + "[aout]",
             "-map", "0:v:0", "-map", "[aout]"], aargs)


def build_stages(input_path: str, output_path: str, settings: dict, mode: str,
                 passlog: str | None = None, segment=None):
    """Return a list of (label, command) stages for one output file.

    mode "quality": single constant-quality pass (CRF on CPU; CQ / CQP / ICQ
                    on NVENC / AMF / QSV).
    mode "target":  size-targeted. x265/x264 use a real 2-pass; SVT-AV1 uses
                    single-pass ABR; NVENC uses its internal full-res
                    multipass; AMF uses peak-constrained VBR; QSV plain VBR.
    `segment` (start, dur) encodes just that slice, used by split-to-fit.
    """
    codec = settings.get("codec", "h265")
    info = CODECS[codec]
    gpu = _is_gpu(settings)
    vendor = settings.get("encoder")
    vf = _video_filters(settings)
    filt = ["-vf", vf] if vf else []
    base = _input_args(input_path, segment)
    tag = info["tag"]
    maps, aargs = _track_args(settings)
    audio = aargs if aargs is not None else _audio_args(settings)

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
        pass2 = common + filt + p2 + tag + maps + audio + PROGRESS + [output_path]
        return [("analyze", pass1), ("encode", pass2)]

    if mode == "target" and not gpu:  # SVT-AV1: single-pass average bitrate
        vb = f"{int(settings['video_bitrate'])}k"
        preset = _SVT_PRESETS.get(str(settings["preset"]), 6)
        cmd = base + ["-c:v", info["cpu"], "-preset", str(preset), "-b:v", vb] \
            + _pix_args(settings, gpu) + filt + maps + audio \
            + PROGRESS + [output_path]
        return [("encode", cmd)]

    if mode == "target":  # GPU size target, single stage per vendor
        vb = int(settings["video_bitrate"])
        enc = GPU_ENCODERS[vendor][codec]
        rate = ["-b:v", f"{vb}k", "-maxrate", f"{vb}k", "-bufsize", f"{2 * vb}k"]
        if vendor == "nvenc":
            vargs = ["-c:v", enc, "-preset", "p5", "-rc", "vbr"] + rate \
                + ["-multipass", "fullres"]
        elif vendor == "amf":
            vargs = ["-c:v", enc, "-quality", "quality", "-rc", "vbr_peak"] + rate
        else:  # qsv
            vargs = ["-c:v", enc, "-preset", "slower"] + rate
        cmd = base + vargs + _pix_args(settings, gpu) + tag + filt \
            + maps + audio + PROGRESS + [output_path]
        return [("encode", cmd)]

    # quality mode, single pass
    vargs = _gpu_quality_args(settings) if gpu else _cpu_quality_args(settings)
    cap = settings.get("vbv_maxrate")
    if cap and (vendor == "nvenc" or (not gpu and codec in ("h265", "h264"))):
        # Capped quality (roomy Target size): CRF decides the size, the VBV
        # ceiling guarantees the limit. SVT-AV1's wrapper has no clean VBV,
        # and AMF's CQP / QSV's ICQ modes ignore or reject maxrate; there the
        # planner's headroom margin suffices.
        vargs += ["-maxrate", f"{int(cap)}k", "-bufsize", f"{int(2 * cap)}k"]
    cmd = base + vargs + _pix_args(settings, gpu) + tag + filt + maps + audio \
        + PROGRESS + [output_path]
    return [("encode", cmd)]


def gif_output_duration(length: float, settings: dict) -> float:
    """Seconds of animation a clip of `length`s produces after the speed and
    direction options (boomerang plays forward then reversed, so it doubles)."""
    speed = float(settings.get("gif_speed") or 1.0)
    out = length / speed if speed > 0 else length
    if settings.get("gif_direction") == "boomerang":
        out *= 2
    return out


def build_gif_stages(input_path: str, output_path: str, settings: dict, segment=None):
    """One command turning a clip into a loop: classic GIF (via a one-pass
    palette: split / palettegen / paletteuse), animated WebP (much smaller),
    or a silent MP4 (smallest). `segment` trims a short clip, which is what
    you almost always want.

    For GIF, stats_mode=diff biases the palette toward moving areas and
    diff_mode re-encodes only changed rectangles, which improves colours and
    shrinks the file on typical clips.
    """
    fmt = settings.get("gif_format", "gif")
    fps = settings.get("target_fps") or 15  # loops want a low frame rate
    speed = float(settings.get("gif_speed") or 1.0)
    direction = settings.get("gif_direction", "forward")
    chain = []
    if settings.get("src_interlaced"):  # comb artifacts ruin palettes too
        chain.append("bwdif")
    if settings.get("crop_filter"):  # per-file crop box; source geometry first
        chain.append(settings["crop_filter"])
    if settings.get("src_hdr"):  # GIF/WebP palettes are SDR; tone map first
        chain.append(TONEMAP)
    # setpts before fps: after the speed change, fps drops (or duplicates)
    # frames to the target rate, so 2x really halves the frame count.
    if speed != 1.0:
        chain.append(f"setpts=PTS/{speed}")
    chain.append(f"fps={fps}")
    custom = settings.get("gif_custom") or (None, None)
    gif_height = settings.get("gif_height")
    if custom[0] or custom[1]:
        # Exact typed dimensions. Unlike the height caps these DO upscale
        # (typed numbers are deliberate, e.g. a 128x128 emote). A blank side
        # follows the aspect ratio. The MP4 loop encodes yuv420p, which needs
        # even dimensions; the values are literal, so round them here.
        cw, ch = custom
        if fmt == "mp4":
            cw, ch = cw and max(cw - cw % 2, 2), ch and max(ch - ch % 2, 2)
        auto = "-2" if fmt == "mp4" else "-1"
        chain.append(f"scale={cw or auto}:{ch or auto}:flags=lanczos")
    elif gif_height:
        # Cap the height, never upscale (min with ih).
        if fmt == "mp4":
            chain.append(f"scale=-2:trunc(min({gif_height}\\,ih)/2)*2:flags=lanczos")
        else:
            chain.append(f"scale=-2:min({gif_height}\\,ih):flags=lanczos")
    elif fmt == "mp4":  # keep original size, but still round to even for yuv420p
        chain.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    if settings.get("gif_dedupe"):
        # Drop near-identical frames AFTER the fps normalisation (fps would
        # just re-duplicate them). GIF/WebP store per-frame delays, so the
        # remaining frames keep the original timing.
        chain.append("mpdecimate")
    graph = ",".join(chain)
    # reverse buffers the whole (short) clip in memory, so it comes after the
    # fps/scale reductions to keep that buffer small.
    if direction == "reverse":
        graph += ",reverse"
    elif direction == "boomerang":
        graph += ",split[f][b];[b]reverse[r];[f][r]concat=n=2:v=1"

    if fmt == "webp":
        cmd = _input_args(input_path, segment) + \
            ["-filter_complex", graph, "-c:v", "libwebp", "-quality", "75",
             "-compression_level", "6", "-loop", "0", "-an", "-pix_fmt",
             "yuva420p"] + PROGRESS + [output_path]
        return [("webp", cmd)]
    if fmt == "mp4":
        cmd = _input_args(input_path, segment) + \
            ["-filter_complex", graph, "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "23", "-pix_fmt", "yuv420p", "-an",
             "-movflags", "+faststart"] + PROGRESS + [output_path]
        return [("mp4", cmd)]

    dither = settings.get("gif_dither", "bayer:bayer_scale=5")
    colors = int(settings.get("gif_colors") or 256)
    graph += (f",split[s0][s1];[s0]palettegen=max_colors={colors}"
              ":stats_mode=diff[p];"
              f"[s1][p]paletteuse=dither={dither}:diff_mode=rectangle")
    cmd = _input_args(input_path, segment) + \
        ["-filter_complex", graph, "-loop", "0"] + PROGRESS + [output_path]
    stages = [("gif", cmd)]
    lossy = settings.get("gif_lossy")
    if lossy:
        # gifsicle's lossy LZW pass: the one size lever ffmpeg doesn't have,
        # typically 30-60% smaller for barely visible artifacts. -b rewrites
        # the file in place; -O3 also re-optimises frame deltas.
        stages.append(("optimize", [GIFSICLE, "-b", "-O3",
                                    f"--lossy={int(lossy)}", output_path]))
    return stages


def build_cut_stages(input_path: str, output_path: str, segment):
    """Cut `segment` (start, dur) without re-encoding: -c copy is instant and
    lossless, but cut points snap to the nearest keyframes, so the result can
    start up to a few seconds before the requested time."""
    start, dur = segment
    cmd = [FFMPEG, "-y", "-ss", f"{start:.3f}", "-i", input_path,
           "-t", f"{dur:.3f}", "-c", "copy", "-avoid_negative_ts", "make_zero"] \
        + PROGRESS + [output_path]
    return [("cut", cmd)]


# Opus goes in an .ogg container: same format, but the extension far more
# players (and Discord's inline player) recognise than .opus.
AUD_ENCODERS = {"mp3": ("libmp3lame", ".mp3"), "m4a": ("aac", ".m4a"),
                "opus": ("libopus", ".ogg")}


def build_audio_stages(input_path: str, output_path: str, settings: dict):
    """One command extracting/converting the audio track to MP3 or M4A."""
    enc, _ = AUD_ENCODERS[settings.get("aud_format", "mp3")]
    bitrate = settings.get("aud_bitrate", "192k")
    filt = []
    if settings.get("aud_normalize"):
        # EBU R128 loudness normalisation; loudnorm resamples to 192 kHz
        # internally, so pin a sane output rate.
        filt = ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000"]
    cmd = [FFMPEG, "-y", "-i", input_path, "-vn"] + filt + \
        ["-c:a", enc, "-b:a", str(bitrate)] + PROGRESS + [output_path]
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
    strip = ["-map_metadata", "-1"] if settings.get("img_strip") else []
    cmd = [FFMPEG, "-y", "-i", input_path] + (["-vf", vf] if vf else []) \
        + vargs + strip + ["-frames:v", "1"] + PROGRESS + [output_path]
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
        # ffmpeg echoes file names into its log; the default locale codec
        # (cp1252) can't decode many UTF-8 bytes and would fail the encode.
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=NO_WINDOW,
    )
    track_child(proc)

    tail = deque(maxlen=25)  # keep the last lines around for error messages
    speed = None
    try:
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
    finally:
        untrack_child(proc)
