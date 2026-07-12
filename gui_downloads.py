"""The Download tab: clipboard prefill, starting yt-dlp downloads, and
turning finished downloads into queue rows."""

import os
import threading
import tkinter as tk

import downloader
from models import DL_RES_OPTIONS, DL_COOKIES_OPTIONS, Job, friendly_error
from widgets import QueueRow


class DownloadsMixin:

    # ---------- link downloads ----------
    def _prefill_url_from_clipboard(self):
        """Drop a copied link straight into the URL field when the tab opens."""
        if self.url_entry.get().strip():
            return
        try:
            clip = self.clipboard_get()
        except tk.TclError:
            return
        if downloader.looks_like_url(clip):
            self.url_entry.insert(0, clip.strip())

    def on_download(self):
        url = self.url_entry.get().strip()
        if not url:
            self.status.configure(text="Paste a video link first.")
            return
        if not downloader.looks_like_url(url):
            self.status.configure(text="That doesn't look like a link.")
            return
        self.url_entry.delete(0, "end")
        self._start_download(url)

    def _download_dir(self) -> str:
        return (self.outdir_entry.get().strip()
                or os.path.join(os.path.expanduser("~"), "Downloads"))

    def _start_download(self, url):
        job = Job(id=self._next_id, path=url)
        self._next_id += 1
        job.status = "downloading"
        job.from_url = True
        job.row = QueueRow(self.queue_frame, job, self._select_job,
                           self._remove_job, self._open_job, self._context_menu,
                           self._on_row_drag, self.fonts)
        job.row.name.configure(text="🌐  " + (url if len(url) <= 66 else url[:63] + "…"))
        job.row.pack(fill="x", padx=6, pady=4)
        job.row.render(selected=False)
        self.jobs.append(job)
        self.empty_label.pack_forget()
        cancel = threading.Event()
        self._dl_cancels[job.id] = cancel
        max_height = dict(DL_RES_OPTIONS)[self.dl_res_menu.get()]
        job.dl_cap = max_height
        audio_only = bool(self.dl_audio_check.get())
        cookies = dict(DL_COOKIES_OPTIONS)[self.dl_cookies_menu.get()]
        playlist = bool(self.dl_playlist_check.get())
        if playlist:
            job.row.name.configure(text="🌐  playlist · " +
                                   (url if len(url) <= 56 else url[:53] + "…"))
        threading.Thread(target=self._download_worker,
                         args=(job.id, url, self._download_dir(), cancel,
                               max_height, audio_only, cookies, playlist),
                         daemon=True).start()

    def _download_worker(self, jid, url, outdir, cancel, max_height, audio_only,
                         cookies, playlist):
        try:
            if not downloader.has_ytdlp():
                self.msg_queue.put(("dl_setup", "Setting up the downloader (one time)…"))
                downloader.fetch_ytdlp()
            elif downloader.update_ytdlp_if_stale():
                # A stale downloader silently gets offered worse quality by
                # sites, so refresh it before downloading rather than after
                # something visibly fails.
                self.msg_queue.put(("dl_setup", "Updated the downloader."))
            paths, err = downloader.download_with_update_retry(
                url, outdir,
                lambda frac: self.msg_queue.put(("dl_progress", jid, frac)),
                cancel, max_height, audio_only, cookies, playlist,
                on_item=lambda i, n: self.msg_queue.put(("dl_item", jid, i, n)))
            self.msg_queue.put(("dl_done", jid, paths, err))
        except Exception as e:  # noqa: BLE001 - e.g. no internet for the fetch
            self.msg_queue.put(("dl_done", jid, [], str(e)))

    def _on_dl_done(self, jid, paths, err):
        self._dl_cancels.pop(jid, None)
        job = self._job(jid)
        if job is None:  # row was removed while downloading
            return
        if err == "cancelled" and not paths:
            self._set_status(jid, "cancelled")
            return
        if not paths:
            job.status, job.error = "failed", friendly_error(err)
            job.row.render(selected=(job.id == self.selected_id))
            if job.id == self.selected_id:
                self._update_details()
            return
        # The first file reuses this URL row; a playlist's remaining files
        # become their own queue rows.
        job.path = paths[0]
        job.status = "reading"
        job.progress = 0.0
        job.row.set_name(paths[0])
        job.row.render(selected=(job.id == self.selected_id))
        threading.Thread(target=self._probe_worker, args=([job],), daemon=True).start()
        if len(paths) > 1:
            self._add_paths(paths[1:])
