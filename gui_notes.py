"""The advisory layer: the per-mode note under the settings, per-row output
size estimates, and the GIF/image preview thumbnails with the scrubber."""

import os
import threading

import customtkinter as ctk

from encoder import (gif_output_duration, suggest_parts,
                     video_bitrate_for_target)
from models import (MODE_TARGET, MODE_SPLIT, MODE_GIF, MODE_IMAGE, MODE_AUDIO,
                    MODE_DOWNLOAD, RESOLUTIONS, FPS_OPTIONS, AUDIO_OPTIONS,
                    PARTS_OPTIONS, IMG_FORMAT_OPTIONS, is_image, is_audio,
                    human_size, parse_time)
from planner import estimate_output_bytes
from probe import VideoInfo, recommend_settings, estimate_h265_bitrate_kbps


class NotesMixin:

    def _update_note(self):
        self._refresh_estimates()
        mode = self._mode()
        if mode == MODE_DOWNLOAD:
            self.note_label.configure(text=(
                f"Downloads save to {self._download_dir()} (follows the Save to "
                "folder below). They are not compressed automatically since sites "
                "already compress their videos; right-click a downloaded file to "
                "queue it. DRM protected links will not work."))
            return
        if self._cut_only():
            self.note_label.configure(text=(
                "Cuts the trim range without re-encoding: instant and lossless, "
                "but cut points snap to keyframes, so the clip may start up to a "
                "few seconds early. The settings above don't apply."))
            return
        job = self._selected_job()
        if job is None or job.info is None:
            self.note_label.configure(text="")
            return
        if mode == MODE_TARGET:
            self.note_label.configure(text=self._target_note(job.info))
        elif mode == MODE_SPLIT:
            self.note_label.configure(text=self._split_note(job.info))
        elif mode == MODE_GIF:
            self.note_label.configure(text=self._gif_note(job.info))
        elif mode == MODE_IMAGE:
            self.note_label.configure(text=self._image_note(job.info))
        elif mode == MODE_AUDIO:
            fmt = self.aud_format_menu.get().split(" ")[0]
            self.note_label.configure(text=(
                f"Extracts the audio track from videos (or converts audio files) "
                f"to {fmt}. Images are skipped on this tab."))
        else:
            note = recommend_settings(job.info).get("note", "")
            est = self._quality_estimate(job.info)
            warn = ""
            th = dict(RESOLUTIONS)[self.res_menu.get()]
            tf = dict(FPS_OPTIONS)[self.fps_menu.get()]
            if (th and job.info.height and th < job.info.height) or \
                    (tf and job.info.fps and tf < job.info.fps - 0.5):
                warn = ("⚠ Resolution/Frame rate is set below the source, so this "
                        "will downscale. Pick Keep original if unintended.  ")
            self.note_label.configure(text=f"{warn}{note}  {est}".strip())

    def _job_in_current_mode(self, job) -> bool:
        """Whether the active tab would process this file if Start were hit."""
        mode = self._mode()
        if mode == MODE_IMAGE:
            return is_image(job.path)
        if mode == MODE_AUDIO:
            return not is_image(job.path) and bool(job.info.audio_codec)
        return not is_image(job.path) and not is_audio(job.path)

    def _refresh_estimates(self):
        """Recompute the rough output size shown on each ready row."""
        if self.start_btn.cget("state") == "disabled":
            return  # never touch rows mid-run
        mode = self._mode()
        if mode == MODE_DOWNLOAD:
            return
        settings = self._collect_settings()
        settings["cut_only"] = self._cut_only()
        settings["gif_start"] = parse_time(self.gif_start.get()) or 0
        settings["gif_len"] = parse_time(self.gif_len.get()) or 0
        ts = parse_time(self.trim_start.get()) if self.trim_start.get().strip() else 0.0
        te = parse_time(self.trim_end.get()) if self.trim_end.get().strip() else None
        if ts is None:
            settings["trim"] = None
        else:
            settings["trim"] = (ts, te) if (ts > 0 or te is not None) else None
        try:
            size_mb = float(self.target_entry.get())
        except ValueError:
            size_mb = None
        parts = dict(PARTS_OPTIONS)[self.parts_menu.get()] if mode == MODE_SPLIT else None
        for job in self.jobs:
            est = None
            if (job.status == "ready" and job.info is not None
                    and self._job_in_current_mode(job)):
                est = estimate_output_bytes(job.info, mode, settings, size_mb, parts)
                est = int(est) if est else None
            if est != job.est_size:
                job.est_size = est
                if job.row is not None:  # rows are absent mid UI-rebuild
                    job.row.render(selected=(job.id == self.selected_id))
        self._update_counts()  # keep the batch total in the status bar fresh

    def _effective_res_fps(self, info):
        """Resolution/fps after the chosen downscale, for a bpp estimate."""
        th = dict(RESOLUTIONS)[self.res_menu.get()]
        if th and info.height:
            w, h = round(info.width * th / info.height), th
        else:
            w, h = info.width, info.height
        fps = dict(FPS_OPTIONS)[self.fps_menu.get()] or info.fps or 30
        return w, h, fps

    @staticmethod
    def _quality_word(bpp):
        return "good" if bpp >= 0.035 else "okay" if bpp >= 0.018 else "low"

    def _quality_estimate(self, info: VideoInfo) -> str:
        """A rough predicted output size for constant-quality (CRF/CQ) encoding.

        Very content dependent, so it's clearly labelled as rough. Uses a simple
        x265 model: ~0.045 bits per pixel at CRF 23, halving every +6 CRF.
        """
        w, h, fps = self._effective_res_fps(info)
        if not (w and h and fps and info.duration > 0):
            return ""
        crf = int(self.crf_slider.get())
        vkbps = estimate_h265_bitrate_kbps(w, h, fps, crf, self._codec_value())
        audio_mode, audio_bitrate = dict(AUDIO_OPTIONS)[self.audio_menu.get()]
        akbps = 0 if audio_mode in ("copy", "none") \
            else int(str(audio_bitrate).rstrip("k"))
        est_bytes = (vkbps + akbps) * 1000 * info.duration / 8
        if info.size_bytes:
            pct = (1 - est_bytes / info.size_bytes) * 100
            tail = f", {pct:.0f}% smaller" if pct >= 0 else ""
            return f"Estimated output ~{human_size(est_bytes)}{tail} (rough)."
        return f"Estimated output ~{human_size(est_bytes)} (rough)."

    def _target_note(self, info: VideoInfo) -> str:
        try:
            target_mb = float(self.target_entry.get())
        except ValueError:
            return "Enter a target size in MB."
        if info.duration <= 0:
            return "Unknown duration, so a size cannot be targeted for this file."
        vkbps = video_bitrate_for_target(info.duration, target_mb, 128)
        if vkbps < 50:
            return "⚠ Target too small for this file. Raise the size or shorten the clip."
        w, h, fps = self._effective_res_fps(info)
        quality_kbps = estimate_h265_bitrate_kbps(
            w, h, fps, int(self.crf_slider.get()), self._codec_value())
        if quality_kbps and quality_kbps * 1.2 <= vkbps and w and h and fps:
            est = quality_kbps * 1000 * info.duration / 8
            return (f"“{os.path.basename(info.path)}” fits well under "
                    f"{target_mb:.0f} MB: it will be encoded at full quality "
                    f"(~{human_size(est)}), with the limit as a safety cap. "
                    "Small videos are never inflated to fill the target.")
        bpp = (vkbps * 1000) / (w * h * fps) if w and h and fps else 0
        msg = (f"For “{os.path.basename(info.path)}”: {target_mb:.0f} MB gives about "
               f"{int(vkbps)} kbps. Predicted quality at {w}×{h}@{fps:.0f}: "
               f"{self._quality_word(bpp)}.")
        if bpp < 0.035 and (h > 1080 or fps > 30):
            msg += "  Tip: drop to 1080p and/or 30 fps for better results."
        elif bpp < 0.018:
            msg += "  Tight for that size. Try the Split to fit mode instead."
        return msg

    def _split_note(self, info: VideoInfo) -> str:
        try:
            max_mb = float(self.target_entry.get())
        except ValueError:
            return "Enter a max size per part in MB."
        if info.duration <= 0:
            return "Unknown duration, so this file cannot be split by size."
        w, h, fps = self._effective_res_fps(info)
        chosen = dict(PARTS_OPTIONS)[self.parts_menu.get()]
        n = chosen or suggest_parts(info.duration, max_mb, w, h, fps)
        seg = info.duration / n
        vkbps = video_bitrate_for_target(seg, max_mb, 128)
        bpp = (vkbps * 1000) / (w * h * fps) if w and h and fps else 0
        seg_m, seg_s = divmod(int(seg), 60)
        auto = " (auto)" if chosen is None else ""
        return (f"For “{os.path.basename(info.path)}”: {n} part(s){auto} of about "
                f"{seg_m}m{seg_s:02d}s, each under {max_mb:.0f} MB at ~{int(vkbps)} kbps. "
                f"Predicted quality at {w}×{h}@{fps:.0f}: {self._quality_word(bpp)}.")

    def _gif_clip(self, info):
        """Parsed (start, length) for the GIF clip, clamped to the source.
        Accepts seconds or mm:ss. Returns None on invalid input."""
        start = parse_time(self.gif_start.get()) if self.gif_start.get().strip() else 0.0
        length = parse_time(self.gif_len.get()) if self.gif_len.get().strip() else 0.0
        if start is None or length is None or length <= 0:
            return None
        if info.duration > 0:
            start = min(start, max(info.duration - 0.1, 0))
            length = min(length, info.duration - start)
        return start, length

    def _gif_note(self, info: VideoInfo) -> str:
        clip = self._gif_clip(info)
        if clip is None:
            return "Enter a clip start and length in seconds."
        start, length = clip
        settings = self._collect_settings()
        # Loops size from their own height cap (never upscaling), not the
        # Compress tab's Resolution menu.
        gh = settings.get("gif_height")
        if gh and info.height and gh < info.height:
            w, h = round(info.width * gh / info.height), gh
        else:
            w, h = info.width, info.height
        fps = dict(FPS_OPTIONS)[self.fps_menu.get()] or 15
        settings["gif_start"], settings["gif_len"] = start, length
        fmt = settings["gif_format"]
        out_len = gif_output_duration(length, settings)
        est = estimate_output_bytes(info, MODE_GIF, settings)
        name = dict(zip(("gif", "webp", "mp4"), ("GIF", "WebP", "MP4 loop")))[fmt]
        text = (f"{name} of {out_len:.1f}s (clip from {start:.0f}s) at {w}×{h}, "
                f"{fps:.0f} fps. Rough size ~{human_size(est)}.")
        if fmt == "gif":
            text += " WebP or MP4 loop above makes a much smaller file."
        return text

    def _image_note(self, info: VideoInfo) -> str:
        if not is_image(info.path):
            return "This tab compresses images; videos in the queue are skipped here."
        fmt = self.img_format_menu.get()
        hints = {"webp": "great quality, plays everywhere modern",
                 "avif": "smallest files, needs recent apps to view",
                 "jpeg": "biggest of the three, opens absolutely everywhere"}
        key = dict(IMG_FORMAT_OPTIONS)[fmt]
        return (f"Converts each image to {fmt.split(' ')[0]} · {hints[key]}. "
                "Quality High is near lossless; Small squeezes hardest.")

    # ---------- clip/trim range sliders + preview thumbnails ----------
    @staticmethod
    def _fill_entry(entry, seconds):
        entry.delete(0, "end")
        entry.insert(0, f"{seconds:.1f}")

    def _configure_gif_slider(self, duration):
        """Point both range sliders at the selected file's timeline."""
        if not duration or duration <= 0:
            return
        self._gif_slider_max = max(duration, 0.1)
        self.gif_range.configure_range(0, self._gif_slider_max)
        self.trim_range.configure_range(0, self._gif_slider_max)
        self._sync_gif_range_from_entries()
        self._sync_trim_range_from_entries()

    def _clip_sliders_active(self) -> bool:
        """Drags only mean something once a file with a duration is selected;
        before that the sliders are inert instead of writing junk times."""
        job = self._selected_job()
        return (job is not None and job.info is not None
                and job.info.duration > 0)

    def _on_gif_range(self, lo, hi):
        """Range slider drag: write clip start + length into the entries."""
        if not self._clip_sliders_active():
            return
        self._fill_entry(self.gif_start, lo)
        self._fill_entry(self.gif_len, hi - lo)
        self._update_note()
        self._schedule_gif_preview()

    def _sync_gif_range_from_entries(self):
        start = parse_time(self.gif_start.get()) or 0.0
        length = parse_time(self.gif_len.get()) or 0.0
        self.gif_range.set_values(start, start + max(length, 0.01))

    def _on_gif_clip_edited(self):
        self._sync_gif_range_from_entries()  # set_values fires no command
        self._update_note()
        self._schedule_gif_preview()

    def _on_trim_range(self, lo, hi):
        """Trim slider drag: a full-width range means 'no trim' (clear fields)."""
        if not self._clip_sliders_active():
            return
        span = self._gif_slider_max
        if lo <= span * 0.005 and hi >= span * 0.995:
            self.trim_start.delete(0, "end")
            self.trim_end.delete(0, "end")
        else:
            self._fill_entry(self.trim_start, lo)
            self._fill_entry(self.trim_end, hi)
        self._update_note()

    def _sync_trim_range_from_entries(self):
        start = parse_time(self.trim_start.get()) or 0.0
        end = parse_time(self.trim_end.get()) if self.trim_end.get().strip() else None
        self.trim_range.set_values(start, end if end else self._gif_slider_max)

    def _on_trim_edited(self):
        self._sync_trim_range_from_entries()
        self._update_note()

    def _schedule_gif_preview(self):
        """Debounce preview refreshes while the user is still typing/scrubbing."""
        if self._thumb_after is not None:
            self.after_cancel(self._thumb_after)
        self._thumb_after = self.after(350, self._request_gif_preview)

    def _request_gif_preview(self):
        self._thumb_after = None
        mode = self._mode()
        job = self._selected_job()
        if mode not in (MODE_GIF, MODE_IMAGE) or job is None or job.info is None:
            return
        if mode == MODE_IMAGE:
            if is_image(job.path):
                self._request_thumb(job.path, 0.0, self.img_preview)
            return
        clip = self._gif_clip(job.info)
        if clip is None:
            return
        start, length = clip
        end = start + max(length - 0.05, 0)
        if job.info.duration > 0:
            end = min(end, max(job.info.duration - 0.05, 0))
        self._request_thumb(job.path, start, self.gif_preview)
        self._request_thumb(job.path, end, self.gif_preview_end)

    def _request_thumb(self, path, seconds, target):
        token = self._thumb_tokens.get(target, 0) + 1
        self._thumb_tokens[target] = token
        threading.Thread(target=self._thumb_worker,
                         args=(path, seconds, token, target), daemon=True).start()

    def _thumb_worker(self, path, seconds, token, target):
        from probe import extract_frame_png
        png = extract_frame_png(path, seconds)
        self.msg_queue.put(("thumb", token, target, png))

    def _show_thumb(self, token, target, png):
        if not png or self._thumb_tokens.get(target) != token:
            return
        try:
            import io
            from PIL import Image
            img = Image.open(io.BytesIO(png))
            scale = min(160 / img.width, 90 / img.height)
            size = (max(int(img.width * scale), 1), max(int(img.height * scale), 1))
            self._thumb_images[target] = ctk.CTkImage(light_image=img,
                                                      dark_image=img, size=size)
            target.configure(image=self._thumb_images[target], text="")
        except Exception:  # noqa: BLE001 - preview is best-effort only
            pass
