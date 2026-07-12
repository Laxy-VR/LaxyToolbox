"""Running a batch: validating inputs, planning outputs, the encode worker
thread, progress, the message pump, and the GPU/update background probes."""

import os
import queue
import threading
import time
from tkinter import messagebox

import theme
from encoder import (run_encode, cleanup_passlogs, suggest_parts, IMG_EXT,
                     AUD_ENCODERS)
from models import (APP_NAME, APP_VERSION, GITHUB_REPO, MODE_QUALITY,
                    MODE_TARGET, MODE_SPLIT, MODE_GIF, MODE_IMAGE, MODE_AUDIO,
                    MODE_DOWNLOAD, PARTS_OPTIONS, GIF_FORMAT_OPTIONS,
                    GIF_OUT_EXT, IMG_FORMAT_OPTIONS, AUD_FORMAT_OPTIONS,
                    HW_OPTIONS, unique_path, friendly_error, human_size,
                    is_image, is_audio)
from planner import plan_job, trimmed_duration
from probe import nvenc_works, recommend_settings
from sysutil import (set_keep_awake, flash_taskbar, latest_release,
                     is_newer_version)


class RunMixin:

    # ---------- start / encode ----------
    def on_start(self):
        mode = self._mode()
        if mode == MODE_DOWNLOAD:
            self.on_download()
            return
        # Downloaded files are already platform-compressed, so they only join a
        # run when explicitly re-queued (right-click · Queue for compression).
        jobs = [j for j in self.jobs
                if j.info is not None and j.status != "downloaded"]
        # Each tab works on its own kind of file; the rest stay untouched.
        if mode == MODE_IMAGE:
            jobs = [j for j in jobs if is_image(j.path)]
            if not jobs:
                self.status.configure(text="Add some images first.")
                return
        elif mode == MODE_AUDIO:  # anything with an audio track qualifies
            jobs = [j for j in jobs if not is_image(j.path) and j.info.audio_codec]
            if not jobs:
                self.status.configure(text="Add some videos or audio files first.")
                return
        else:
            jobs = [j for j in jobs if not is_image(j.path) and not is_audio(j.path)]
            if not jobs:
                self.status.configure(text="Add some videos first.")
                return
        settings = self._collect_settings()
        size_mb = None
        # A lossless cut ignores the size limit, so a stray value must not block it.
        if mode in (MODE_TARGET, MODE_SPLIT) and not self._cut_only():
            try:
                size_mb = float(self.target_entry.get())
            except ValueError:
                self.status.configure(text="Enter a valid size in MB.")
                return
            if size_mb <= 0:
                self.status.configure(text="Size must be greater than 0.")
                return
        if mode == MODE_GIF:
            try:
                settings["gif_start"] = float(self.gif_start.get() or 0)
                settings["gif_len"] = float(self.gif_len.get() or 0)
            except ValueError:
                self.status.configure(text="Enter a valid clip start and length in seconds.")
                return
            if settings["gif_start"] < 0 or settings["gif_len"] <= 0:
                self.status.configure(text="Clip length must be greater than 0.")
                return
        settings["trim"] = None
        if mode in (MODE_QUALITY, MODE_TARGET, MODE_SPLIT):
            ts_raw = self.trim_start.get().strip()
            te_raw = self.trim_end.get().strip()
            try:
                ts = float(ts_raw) if ts_raw else 0.0
                te = float(te_raw) if te_raw else None
            except ValueError:
                self.status.configure(text="Trim times must be numbers (seconds).")
                return
            if ts < 0 or (te is not None and te <= ts):
                self.status.configure(text="Trim end must be after trim start.")
                return
            if ts > 0 or te is not None:
                settings["trim"] = (ts, te)
            settings["cut_only"] = self._cut_only()
            if settings["cut_only"] and settings["trim"] is None:
                self.status.configure(text="Set a trim range to cut (start and/or end).")
                return

        folder = self.outdir_entry.get().strip()
        if folder:
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as e:
                self.status.configure(text=f"Cannot create output folder: {e}")
                return

        parts_choice = dict(PARTS_OPTIONS)[self.parts_menu.get()] if mode == MODE_SPLIT else None
        limit_mb = size_mb if mode in (MODE_TARGET, MODE_SPLIT) else None
        claimed = set()  # output paths already taken by earlier jobs this batch
        planned = {}  # job id -> outputs, computed before anything is mutated
        for job in jobs:  # all widget access happens here on the main thread
            planned[job.id] = [unique_path(o, claimed)
                               for o in self._outputs_for(job, mode, size_mb, parts_choice,
                                                          settings.get("trim"))]
        # unique_path only dedupes within this batch; files from earlier runs
        # (or unrelated files with the same name) would be silently replaced.
        existing = [o for outs in planned.values() for o in outs if os.path.exists(o)]
        if existing:
            names = ", ".join(os.path.basename(o) for o in existing[:3])
            if len(existing) > 3:
                names += ", …"
            if not messagebox.askyesno(
                    APP_NAME,
                    f"{len(existing)} file(s) already exist and will be "
                    f"replaced:\n{names}\n\nContinue?", parent=self):
                return
        for job in jobs:
            job.outputs = planned[job.id]
            job.output = job.outputs[0] if job.outputs else None
            job.out_size = None
            job.limit_mb = limit_mb
            job.over_limit = False
            job.error = None
            job.status = "queued"
            job.progress = 0.0
            job.row.render(selected=(job.id == self.selected_id))

        self.cancel_event.clear()
        self._set_running(True)
        self.progress.set(0)
        self._batch_start = time.time()
        set_keep_awake(True)
        threading.Thread(target=self._encode_worker,
                         args=(jobs, mode, settings, size_mb), daemon=True).start()

    def on_cancel(self):
        self.cancel_event.set()
        self.status.configure(text="Cancelling…")

    def _outputs_for(self, job, mode, size_mb, parts_choice, trim=None):
        """Output file path(s) for a job (one, or several parts when splitting)."""
        folder = self.outdir_entry.get().strip() or os.path.dirname(job.path)
        stem = os.path.splitext(os.path.basename(job.path))[0]
        if mode == MODE_IMAGE:
            ext = IMG_EXT[dict(IMG_FORMAT_OPTIONS)[self.img_format_menu.get()]]
            out = os.path.join(folder, f"{stem}{ext}")
            if os.path.abspath(out) == os.path.abspath(job.path):  # same format in
                out = os.path.join(folder, f"{stem}_laxy{ext}")
            return [out]
        if mode == MODE_AUDIO:
            ext = AUD_ENCODERS[dict(AUD_FORMAT_OPTIONS)[self.aud_format_menu.get()]][1]
            out = os.path.join(folder, f"{stem}{ext}")
            if os.path.abspath(out) == os.path.abspath(job.path):  # same format in
                out = os.path.join(folder, f"{stem}_laxy{ext}")
            return [out]
        if mode == MODE_GIF:
            fmt = dict(GIF_FORMAT_OPTIONS)[self.gif_format_menu.get()]
            ext = GIF_OUT_EXT[fmt]  # .gif / .webp / _loop.mp4
            out = os.path.join(folder, f"{stem}{ext}")
            if os.path.abspath(out) == os.path.abspath(job.path):  # gif -> gif
                out = os.path.join(folder, f"{stem}_laxy{ext}")
            return [out]
        if self._cut_only():  # stream copy must stay in the source container
            src_ext = os.path.splitext(job.path)[1] or ".mp4"
            return [os.path.join(folder, f"{stem}_cut{src_ext}")]
        if mode != MODE_SPLIT:
            return [os.path.join(folder, f"{stem}_h265.mp4")]
        w, h, fps = self._effective_res_fps(job.info)
        dur = trimmed_duration(job.info.duration, trim)
        n = parts_choice or suggest_parts(dur, size_mb, w, h, fps)
        return [os.path.join(folder, f"{stem}_part{i + 1}_h265.mp4") for i in range(n)]

    def _encode_worker(self, jobs, mode, base_settings, size_mb):
        total = len(jobs)
        cancelled = False
        for idx, job in enumerate(jobs):
            if self.cancel_event.is_set():
                cancelled = True
                break
            self.msg_queue.put(("job_status", job.id, "encoding"))
            stages, passlogs, reason = plan_job(job, mode, base_settings, size_mb)
            if stages is None:
                self.msg_queue.put(("job_done", job.id, "failed", [reason]))
                self.msg_queue.put(("overall", (idx + 1) / total))
                continue

            result, tail = self._run_stages(job, stages, idx, total)
            for passlog in passlogs:
                cleanup_passlogs(passlog)
            if result in ("failed", "cancelled"):
                self._cleanup_outputs(job)  # never leave a broken/partial file
            self.msg_queue.put(("job_done", job.id, result, tail))
            self.msg_queue.put(("overall", (idx + 1) / total))
            if result == "cancelled":
                cancelled = True
                break

        if cancelled:  # mark anything not yet finished
            for job in jobs:
                self.msg_queue.put(("mark_cancelled", job.id))
        self.msg_queue.put(("all_done", cancelled))

    def _cleanup_outputs(self, job):
        for out in job.outputs:
            try:
                if out and os.path.exists(out):
                    os.remove(out)
            except OSError:
                pass

    def _run_stages(self, job, stages, idx, total):
        n = len(stages)
        tail = []
        for si, (_label, cmd, dur) in enumerate(stages):
            def on_progress(frac, speed=None, si=si):
                job_frac = (si + frac) / n
                overall = (idx + job_frac) / total
                bits = [f"{idx + 1}/{total}", os.path.basename(job.path),
                        f"{int(job_frac * 100)}%"]
                if speed:
                    bits.append(f"{speed:.1f}x")
                if overall > 0.02:
                    elapsed = time.time() - self._batch_start
                    remaining = elapsed * (1 - overall) / overall
                    bits.append(f"~{self._fmt_eta(remaining)} left")
                self.msg_queue.put(("progress", job.id, job_frac, overall, " · ".join(bits)))
            try:
                code, tail = run_encode(cmd, dur, on_progress, self.cancel_event)
            except Exception as e:  # noqa: BLE001
                return "failed", [str(e)]
            if code is None:
                return "cancelled", tail
            if code != 0:
                return "failed", tail
        return "done", tail

    @staticmethod
    def _fmt_eta(seconds):
        seconds = max(int(seconds), 0)
        if seconds >= 3600:
            return f"{seconds // 3600}h {seconds % 3600 // 60}m"
        if seconds >= 60:
            return f"{seconds // 60}m {seconds % 60}s"
        return f"{seconds}s"

    # ---------- queue message pump ----------
    def _poll_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                self._dispatch(msg)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _dispatch(self, msg):
        kind = msg[0]
        if kind == "probed":
            self._on_probed(msg[1], msg[2], msg[3])
        elif kind == "job_status":
            self._set_status(msg[1], msg[2])
        elif kind == "progress":
            _, jid, job_frac, overall, text = msg
            job = self._job(jid)
            if job:
                job.progress = job_frac
                job.row.render(selected=(job.id == self.selected_id))
            self.progress.set(overall)
            self.status.configure(text=text)
            self.title(f"{int(overall * 100)}% · {APP_NAME}")  # taskbar progress
        elif kind == "overall":
            self.progress.set(msg[1])
        elif kind == "job_done":
            self._on_job_done(msg[1], msg[2], msg[3])
        elif kind == "mark_cancelled":
            job = self._job(msg[1])
            if job and job.status in ("queued", "encoding"):
                self._set_status(job.id, "cancelled")
        elif kind == "all_done":
            self._on_all_done(msg[1])
        elif kind == "thumb":
            self._show_thumb(msg[1], msg[2], msg[3])
        elif kind == "dl_setup":
            self.status.configure(text=msg[1])
        elif kind == "dl_progress":
            job = self._job(msg[1])
            if job and job.status == "downloading":
                job.progress = msg[2]
                job.row.render(selected=(job.id == self.selected_id))
        elif kind == "dl_item":
            job = self._job(msg[1])
            if job and job.status == "downloading":
                job.row.name.configure(text=f"🌐  playlist · item {msg[2]} of {msg[3]}")
        elif kind == "dl_done":
            self._on_dl_done(msg[1], msg[2], msg[3])
        elif kind == "update":
            self._show_update(msg[1], msg[2])
        elif kind == "gpu_ok":
            self._on_gpu_probed(msg[1])
        elif kind == "row_thumb":
            job = self._job(msg[1])
            if job and job.row:
                job.row.set_thumbnail(msg[2])

    def _set_status(self, jid, status):
        job = self._job(jid)
        if job:
            job.status = status
            if status == "encoding":
                job.progress = 0.0
            job.row.render(selected=(job.id == self.selected_id))

    def _on_probed(self, jid, info, error):
        job = self._job(jid)
        if not job:
            return
        if info is None:
            job.status, job.error = "unsupported", error
        else:
            job.info = info
            # Downloads finish as a terminal state: already platform-compressed,
            # so they don't join compression runs unless explicitly re-queued.
            job.status = "downloaded" if job.from_url else "ready"
        job.row.render(selected=(job.id == self.selected_id))
        if (info is not None and job.from_url and info.height
                and info.height < 720 and (job.dl_cap is None or job.dl_cap >= 1080)):
            # Asked for high quality but the site served low-res: say so
            # instead of letting someone discover it as "pixelated video".
            self.status.configure(text=(
                f"Heads up: the site only served {info.height}p for "
                f"“{os.path.basename(job.path)}”. Try the Cookies option on the "
                "Download tab (sign in to the site in that browser first), or "
                "retry later."))
        if info is not None and not self._prefilled:
            self._apply_recommended(recommend_settings(info))
            self._prefilled = True
        if info is not None and self.selected_id is None:
            self._select_job(job)
        elif job.id == self.selected_id:
            self._update_details()
            self._update_note()
        else:
            self._refresh_estimates()  # newly ready rows get their estimate
        self._update_counts()

    def _on_job_done(self, jid, status, tail):
        job = self._job(jid)
        if not job:
            return
        job.status = status
        if status == "done":
            job.progress = 1.0
            sizes = [os.path.getsize(o) for o in job.outputs if o and os.path.exists(o)]
            job.out_size = sum(sizes) or None
            if job.limit_mb and sizes and max(sizes) > job.limit_mb * 1024 * 1024:
                job.over_limit = True  # a file/part landed over the requested limit
        elif tail:
            job.error = friendly_error(tail)
        job.row.render(selected=(job.id == self.selected_id))
        if job.id == self.selected_id:
            self._update_details()

    def _on_all_done(self, cancelled):
        self._set_running(False)
        set_keep_awake(False)
        self.title(APP_NAME)  # clear the taskbar progress
        done_jobs = [j for j in self.jobs if j.status == "done"]
        done = len(done_jobs)
        failed = sum(1 for j in self.jobs if j.status == "failed")
        # Total bytes saved across files where we know both sizes.
        pairs = [(j.info.size_bytes, j.out_size) for j in done_jobs
                 if j.info and j.info.size_bytes and j.out_size]
        total_in = sum(p[0] for p in pairs)
        saved = total_in - sum(p[1] for p in pairs)
        if cancelled:
            self.status.configure(text=f"Cancelled. {done} finished, {failed} failed.")
        else:
            self.progress.set(1)
            extra = f", {failed} failed" if failed else ""
            summary = f"All done. {done} file(s){extra}."
            if total_in and saved > 0:
                summary += f"  Saved {human_size(saved)} ({saved / total_in * 100:.0f}%)."
            self.status.configure(text=summary)
        if not cancelled and done:
            flash_taskbar(self)  # nudge the taskbar so you can walk away
        self._save_config()  # so a later crash can't lose this run's settings

    def _set_running(self, running):
        state = "disabled" if running else "normal"
        self.start_btn.configure(state=state)
        self.mode_seg.configure(state=state)
        self.tab_seg.configure(state=state)
        self.cancel_btn.configure(state="normal" if running else "disabled")
        if not running:
            self._sync_controls()  # re-apply cut-only/GIF greying after a run

    # ---------- GPU capability probe ----------
    def _gpu_probe_worker(self):
        self.msg_queue.put(("gpu_ok", nvenc_works()))

    def _on_gpu_probed(self, ok):
        self._gpu_ok = ok
        if not ok:
            self._hide_gpu_option()
        self._save_config()  # cache the verdict so working GPUs skip the probe

    def _hide_gpu_option(self):
        if self.hw_menu is None:
            return
        self.hw_menu.set(HW_OPTIONS[0][0])  # CPU
        self.hw_menu.grid_remove()
        self.hw_menu_label.grid_remove()
        self.crf_caption.configure(text="Quality (CRF)")
        self._sync_controls()

    # ---------- app update check ----------
    def _update_check_worker(self):
        tag, url = latest_release(GITHUB_REPO)
        if tag and url and is_newer_version(tag, APP_VERSION):
            self.msg_queue.put(("update", tag, url))

    def _show_update(self, tag, url):
        """Turn the version chip into a clickable update link."""
        import webbrowser
        self.version_label.configure(
            text=f"v{APP_VERSION} · {tag} available!", text_color=theme.ACCENT_HOVER,
            cursor="hand2")
        self.version_label.bind("<Button-1>", lambda _e: webbrowser.open(url))
