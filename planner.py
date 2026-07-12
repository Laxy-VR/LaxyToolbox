"""Turn one queued job into the ffmpeg stages that produce its outputs.

Pure planning logic, extracted from the GUI so it can be unit tested: given a
job (with probed info and pre-computed output paths), the effective mode, and
a settings snapshot, return the (label, command, duration) stages plus any
2-pass log prefixes to clean up afterwards. No widgets, no threads.
"""

import os
import tempfile

from encoder import (build_stages, build_gif_stages, build_image_stages,
                     build_audio_stages, build_cut_stages,
                     video_bitrate_for_target, gif_output_duration,
                     suggest_parts)
from models import (MODE_QUALITY, MODE_TARGET, MODE_SPLIT, MODE_GIF,
                    MODE_IMAGE, MODE_AUDIO, SUB_EXTS)
from probe import estimate_h265_bitrate_kbps


def trimmed_duration(duration, trim):
    """Effective seconds after applying an optional (start, end) trim."""
    if not trim:
        return duration
    t0, t1 = trim
    if duration > 0:
        t0 = min(t0, max(duration - 0.1, 0))
        t1 = min(t1, duration) if t1 is not None else duration
    if t1 is None:
        return max(duration - t0, 0)
    return max(t1 - t0, 0)


def resolve_subtitles(settings, video_path):
    """The subtitle file to burn into `video_path`, or None.

    "auto" looks for a same-named .srt/.ass/.vtt next to the source, so one
    shared setting still does the right thing per file across a batch."""
    mode = settings.get("subs_mode")
    if mode == "auto":
        stem = os.path.splitext(video_path)[0]
        for ext in SUB_EXTS:
            if os.path.exists(stem + ext):
                return stem + ext
        return None
    if mode == "file":
        path = settings.get("subs_path")
        return path if path and os.path.exists(path) else None
    return None


def plan_job(job, mode, base_settings, size_mb):
    """Build (stages, passlogs, reason). stages is None with a reason on failure.
    Each stage is (label, command, duration_seconds) for progress scaling."""
    settings = dict(base_settings)
    # Per-file source traits the encoder needs: 10-bit stays 10-bit on
    # H.265/AV1, and HDR gets tone mapped when the output must be SDR.
    settings["src_10bit"] = job.info.is_10bit
    settings["src_hdr"] = job.info.is_hdr
    dur = job.info.duration
    if settings["audio_mode"] == "none":
        audio_kbps = 0
    elif settings["audio_mode"] == "copy":
        audio_kbps = 128
    else:
        audio_kbps = int(str(settings["audio_bitrate"]).rstrip("k"))
    # NVENC size targeting is less precise than x265 2-pass, so leave it a
    # bit more headroom to stay under the limit.
    safety = 0.90 if settings.get("encoder") == "nvenc" else 0.95

    if mode == MODE_IMAGE:
        stages = [(lbl, cmd, 1.0) for lbl, cmd in
                  build_image_stages(job.path, job.outputs[0], settings)]
        return stages, [], None

    if mode == MODE_AUDIO:
        stages = [(lbl, cmd, dur) for lbl, cmd in
                  build_audio_stages(job.path, job.outputs[0], settings)]
        return stages, [], None

    if mode == MODE_GIF:
        start = max(settings.get("gif_start", 0.0), 0.0)
        length = settings.get("gif_len", 5.0)
        if dur > 0:
            start = min(start, max(dur - 0.1, 0.0))
            length = min(length, dur - start)
        if length <= 0:
            return None, [], "clip start is past the end of this file"
        # Progress tracks the OUTPUT timeline, which speed/boomerang stretch.
        out_len = gif_output_duration(length, settings)
        stages = [(lbl, cmd, out_len) for lbl, cmd in build_gif_stages(
            job.path, job.outputs[0], settings, segment=(start, length))]
        return stages, [], None

    # Video modes share the optional trim: encode only start..end seconds.
    trim = settings.get("trim")
    t0 = min(trim[0], max(dur - 0.1, 0)) if (trim and dur > 0) \
        else (trim[0] if trim else 0.0)
    dur_eff = trimmed_duration(dur, trim)
    if trim and dur_eff <= 0:
        return None, [], "the trim range is outside this video"
    seg_all = (t0, dur_eff) if trim else None

    if settings.get("cut_only"):  # lossless stream copy of the trim range
        stages = [(lbl, cmd, dur_eff) for lbl, cmd in
                  build_cut_stages(job.path, job.outputs[0], seg_all)]
        return stages, [], None

    # Re-encoding modes can burn in subtitles; resolved per file here so
    # "auto" finds each video's own matching subtitle in a batch.
    settings["subtitles"] = resolve_subtitles(settings, job.path)

    if mode == MODE_QUALITY:
        stages = [(lbl, cmd, dur_eff) for lbl, cmd
                  in build_stages(job.path, job.outputs[0], settings, "quality",
                                  segment=seg_all)]
        return stages, [], None

    def size_settings(video_kbps):
        s = dict(settings)
        if s["audio_mode"] == "copy":  # target needs a known audio size
            s["audio_mode"], s["audio_bitrate"] = "aac", "128k"
        s["video_bitrate"] = int(video_kbps)
        return s

    if mode == MODE_TARGET:
        vkbps = video_bitrate_for_target(dur_eff, size_mb, audio_kbps, safety)
        if dur_eff <= 0 or vkbps < 50:
            return None, [], "target too small for this file"
        passlog = os.path.join(tempfile.gettempdir(), f"vc_{os.getpid()}_{job.id}_pass")
        stages = [(lbl, cmd, dur_eff) for lbl, cmd in build_stages(
            job.path, job.outputs[0], size_settings(vkbps), "target",
            passlog=passlog, segment=seg_all)]
        return stages, [passlog], None

    # split mode: one target-encode per part, over equal time segments
    n = len(job.outputs)
    if dur_eff <= 0 or n < 1:
        return None, [], "cannot split this file"
    seg = dur_eff / n
    vkbps = video_bitrate_for_target(seg, size_mb, audio_kbps, safety)
    if vkbps < 50:
        return None, [], "parts still too big; raise the size or the part count"
    s = size_settings(vkbps)
    stages, passlogs = [], []
    for i, out in enumerate(job.outputs):
        start = t0 + i * seg
        part_dur = seg if i < n - 1 else max(t0 + dur_eff - start, 0.0)
        passlog = os.path.join(tempfile.gettempdir(), f"vc_{os.getpid()}_{job.id}_p{i}")
        for lbl, cmd in build_stages(job.path, out, s, "target",
                                     passlog=passlog, segment=(start, part_dur)):
            stages.append((f"part {i + 1} {lbl}", cmd, part_dur))
        passlogs.append(passlog)
    return stages, passlogs, None


def _effective_res_fps(info, settings):
    """Resolution/fps after the chosen downscale, for size estimates."""
    th = settings.get("target_height")
    if th and info.height:
        w, h = round(info.width * th / info.height), th
    else:
        w, h = info.width, info.height
    fps = settings.get("target_fps") or info.fps or 30
    return w, h, fps


# Rough bytes per pixel-frame of a 256-colour GIF, and how the other loop
# formats compare on typical footage. Very content dependent, like every
# estimate here; the UI labels them "~".
_GIF_BPPF = 0.20
_LOOP_FACTOR = {"gif": 1.0, "webp": 0.35}


def estimate_output_bytes(info, mode, settings, size_mb=None,
                          parts_choice=None) -> float | None:
    """Rough predicted output size in bytes for a job, or None when the mode
    has no sensible prediction (images) or the inputs are unusable."""
    dur_eff = trimmed_duration(info.duration, settings.get("trim"))
    w, h, fps = _effective_res_fps(info, settings)

    if mode == MODE_AUDIO:
        if info.duration <= 0:
            return None
        kbps = int(str(settings.get("aud_bitrate", "192k")).rstrip("k"))
        return kbps * 1000 * info.duration / 8

    if mode == MODE_IMAGE:
        return None  # too content dependent to be worth a number

    if mode == MODE_GIF:
        if not (w and h and info.duration >= 0):
            return None
        try:
            start = max(float(settings.get("gif_start") or 0), 0.0)
            length = float(settings.get("gif_len") or 0)
        except (TypeError, ValueError):
            return None
        if info.duration > 0:
            start = min(start, max(info.duration - 0.1, 0))
            length = min(length, info.duration - start)
        if length <= 0:
            return None
        gfps = settings.get("target_fps") or 15
        out_len = gif_output_duration(length, settings)
        fmt = settings.get("gif_format", "gif")
        if fmt == "mp4":
            vkbps = estimate_h265_bitrate_kbps(w, h, gfps, 23, "h264")
            return vkbps * 1000 * out_len / 8
        return w * h * gfps * out_len * _GIF_BPPF * _LOOP_FACTOR.get(fmt, 1.0)

    # Video encode modes need a real duration.
    if dur_eff <= 0:
        return None
    if settings.get("cut_only"):  # stream copy keeps the source bitrate
        if not (info.size_bytes and info.duration > 0):
            return None
        return info.size_bytes * dur_eff / info.duration

    if mode == MODE_TARGET and size_mb:
        return size_mb * 1024 * 1024 * 0.95
    if mode == MODE_SPLIT and size_mb:
        n = parts_choice or suggest_parts(dur_eff, size_mb, w, h, fps)
        return n * size_mb * 1024 * 1024 * 0.95

    # Quality (CRF/CQ) mode: the x265 bits-per-pixel model per codec.
    if not (w and h and fps):
        return None
    vkbps = estimate_h265_bitrate_kbps(w, h, fps, int(settings.get("crf", 23)),
                                       settings.get("codec", "h265"))
    audio_mode = settings.get("audio_mode", "copy")
    akbps = 0 if audio_mode in ("copy", "none") \
        else int(str(settings.get("audio_bitrate", "128k")).rstrip("k"))
    return (vkbps + akbps) * 1000 * dur_eff / 8
