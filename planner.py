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
from probe import estimate_h265_bitrate_kbps, detect_crop


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
    # H.265/AV1, HDR gets tone mapped when the output must be SDR,
    # interlaced sources get deinterlaced, and "mix all tracks" needs each
    # file's own audio track count for its amix graph.
    settings["src_10bit"] = job.info.is_10bit
    settings["src_hdr"] = job.info.is_hdr
    settings["src_interlaced"] = job.info.is_interlaced
    settings["audio_track_count"] = job.info.audio_tracks
    # A crop drawn on this file (the crop box dialog) wins over the shared
    # Crop menu; it also applies to GIFs made from this file.
    if job.crop:
        w, h, x, y = job.crop
        settings["crop_filter"] = f"crop={w}:{h}:{x}:{y}"
        settings["crop"] = None
    dur = job.info.duration
    if settings["audio_mode"] == "none":
        audio_kbps = 0
    elif settings["audio_mode"] == "copy":
        audio_kbps = 128
    else:
        audio_kbps = int(str(settings["audio_bitrate"]).rstrip("k"))
    # GPU size targeting is less precise than x265 2-pass, so leave it a
    # bit more headroom to stay under the limit.
    safety = 0.90 if settings.get("encoder") in ("nvenc", "amf", "qsv") else 0.95

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
        # The gifsicle optimize pass is quick and reports no timeline.
        out_len = gif_output_duration(length, settings)
        stages = [(lbl, cmd, 1.0 if lbl == "optimize" else out_len)
                  for lbl, cmd in build_gif_stages(
                      job.path, job.outputs[0], settings,
                      segment=(start, length))]
        return stages, [], None

    # Video modes trim to start..end seconds. A trim set on this file
    # (right click · Trim this file) wins over the shared trim fields.
    trim = job.trim or settings.get("trim")
    t0 = min(trim[0], max(dur - 0.1, 0)) if (trim and dur > 0) \
        else (trim[0] if trim else 0.0)
    dur_eff = trimmed_duration(dur, trim)
    if trim and dur_eff <= 0:
        return None, [], "the trim range is outside this video"
    seg_all = (t0, dur_eff) if trim else None

    if settings.get("cut_only"):  # lossless stream copy of the trim range
        if not trim:
            return None, [], "no trim range set for this file"
        stages = [(lbl, cmd, dur_eff) for lbl, cmd in
                  build_cut_stages(job.path, job.outputs[0], seg_all)]
        return stages, [], None

    # A speed change stretches or shrinks the OUTPUT timeline: progress and
    # size targeting must both work in output seconds.
    spd = float(settings.get("speed") or 1.0)
    dur_out = dur_eff / spd if spd > 0 else dur_eff

    # Re-encoding modes can burn in subtitles; resolved per file here so
    # "auto" finds each video's own matching subtitle in a batch.
    settings["subtitles"] = resolve_subtitles(settings, job.path)

    # "Remove black bars" measures each file at plan time (worker thread).
    # Only act on a believable result: a real bar (over 4 px on a side) and
    # an area that is still most of the frame, so one dark sample can't
    # crop a video down to nothing.
    if settings.get("crop") == "auto" and job.info.width and job.info.height:
        c = detect_crop(job.path, dur)
        if c:
            w, h, x, y = c
            real_bars = (w <= job.info.width - 8 or h <= job.info.height - 8)
            believable = w * h >= 0.25 * job.info.width * job.info.height
            if real_bars and believable:
                settings["crop_filter"] = f"crop={w}:{h}:{x}:{y}"

    if mode == MODE_QUALITY:
        stages = [(lbl, cmd, dur_out) for lbl, cmd
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
        vkbps = video_bitrate_for_target(dur_out, size_mb, audio_kbps, safety)
        if dur_eff <= 0 or vkbps < 50:
            return None, [], "target too small for this file"
        # When the cap is far above what this video needs at the chosen
        # quality, encoding AT the cap would inflate it (a 12 MB clip given a
        # 500 MB target must not balloon). Encode at constant quality with
        # the cap as a VBV ceiling instead: as small as the content allows,
        # never over the limit. Tight caps keep the precise 2-pass targeting.
        w, h, fps = _effective_res_fps(job.info, settings)
        quality_kbps = estimate_h265_bitrate_kbps(
            w, h, fps, int(settings.get("crf", 23)),
            settings.get("codec", "h265"))
        if quality_kbps and quality_kbps * 1.2 <= vkbps:
            s = dict(settings)
            # The ceiling is a safety margin, not a goal: clamp it well below
            # absurd values (a 500 MB cap on a 3s clip is ~1.3M kbps, whose
            # doubled bufsize overflows ffmpeg's 32-bit field).
            s["vbv_maxrate"] = int(min(vkbps, quality_kbps * 4))
            stages = [(lbl, cmd, dur_out) for lbl, cmd in build_stages(
                job.path, job.outputs[0], s, "quality", segment=seg_all)]
            return stages, [], None
        passlog = os.path.join(tempfile.gettempdir(), f"vc_{os.getpid()}_{job.id}_pass")
        stages = [(lbl, cmd, dur_out) for lbl, cmd in build_stages(
            job.path, job.outputs[0], size_settings(vkbps), "target",
            passlog=passlog, segment=seg_all)]
        return stages, [passlog], None

    # split mode: one target-encode per part, over equal time segments
    n = len(job.outputs)
    if dur_eff <= 0 or n < 1:
        return None, [], "cannot split this file"
    seg = dur_eff / n              # input seconds per part
    seg_out = seg / spd if spd > 0 else seg  # output seconds (speed applied)
    vkbps = video_bitrate_for_target(seg_out, size_mb, audio_kbps, safety)
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
            stages.append((f"part {i + 1} {lbl}", cmd,
                           part_dur / spd if spd > 0 else part_dur))
        passlogs.append(passlog)
    return stages, passlogs, None


def gif_output_dims(src_w, src_h, settings):
    """Output (w, h) of a loop, for estimates and notes. Exact custom
    dimensions win (a blank side follows the aspect ratio, and typed numbers
    may upscale); otherwise the height cap applies, which never upscales."""
    cw, ch = settings.get("gif_custom") or (None, None)
    if cw or ch:
        if cw and ch:
            return cw, ch
        if cw:
            return cw, (round(src_h * cw / src_w) if src_w else src_h)
        return (round(src_w * ch / src_h) if src_h else src_w), ch
    gh = settings.get("gif_height")
    if gh and src_h and gh < src_h:
        return round(src_w * gh / src_h), gh
    return src_w, src_h


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
    # Fixed-ratio crops shrink the frame the encoder actually sees. "auto"
    # is unknown until encode time, and bars are nearly free to encode, so
    # its estimate is left alone.
    if settings.get("crop") == "9:16":
        w = min(w, round(h * 9 / 16))
    elif settings.get("crop") == "1:1":
        w = h = min(w, h)

    if mode == MODE_AUDIO:
        if info.duration <= 0:
            return None
        kbps = int(str(settings.get("aud_bitrate", "192k")).rstrip("k"))
        return kbps * 1000 * info.duration / 8

    if mode == MODE_IMAGE:
        return None  # too content dependent to be worth a number

    if mode == MODE_GIF:
        # Loops size from their own Size setting, not the Compress tab's
        # Resolution menu.
        w, h = gif_output_dims(info.width, info.height, settings)
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
        est = w * h * gfps * out_len * _GIF_BPPF * _LOOP_FACTOR.get(fmt, 1.0)
        lossy = settings.get("gif_lossy") or 0
        if fmt == "gif" and lossy:  # rough gifsicle --lossy savings
            est *= 0.7 if lossy <= 40 else 0.55 if lossy <= 100 else 0.45
        return est

    # Video encode modes need a real duration.
    if dur_eff <= 0:
        return None
    if settings.get("cut_only"):  # stream copy keeps the source bitrate
        if not (info.size_bytes and info.duration > 0):
            return None
        return info.size_bytes * dur_eff / info.duration

    # A speed change shrinks (or stretches) the output timeline, and bytes
    # follow the output seconds.
    spd = float(settings.get("speed") or 1.0)
    dur_o = dur_eff / spd if spd > 0 else dur_eff

    if mode == MODE_SPLIT and size_mb:
        n = parts_choice or suggest_parts(dur_o, size_mb, w, h, fps)
        return n * size_mb * 1024 * 1024 * 0.95

    # Quality (CRF/CQ) model: x265 bits-per-pixel per codec. Target size mode
    # produces the smaller of the quality estimate and the cap, matching the
    # capped-quality planning (a roomy cap never inflates the file).
    if not (w and h and fps):
        return size_mb * 1024 * 1024 * 0.95 if (mode == MODE_TARGET and size_mb) \
            else None
    vkbps = estimate_h265_bitrate_kbps(w, h, fps, int(settings.get("crf", 23)),
                                       settings.get("codec", "h265"))
    audio_mode = settings.get("audio_mode", "copy")
    akbps = 0 if audio_mode in ("copy", "none") \
        else int(str(settings.get("audio_bitrate", "128k")).rstrip("k"))
    quality_bytes = (vkbps + akbps) * 1000 * dur_o / 8
    if mode == MODE_TARGET and size_mb:
        return min(quality_bytes, size_mb * 1024 * 1024 * 0.95)
    return quality_bytes
