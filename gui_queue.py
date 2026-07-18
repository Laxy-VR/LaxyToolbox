"""The file queue: adding files/folders/drops, removing and reordering rows,
selection, the details line, probing, and opening results in Explorer.

Shared state contract: the owner of self.jobs mutation (add, remove, reorder)
and self.selected_id; every other mixin only reads them. Probe results come
back through self.msg_queue and land in RunMixin's dispatcher."""

import os
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog

from models import (MEDIA_EXTS, Job, is_audio, is_image, human_size,
                    parse_time)
from probe import probe_video
from sysutil import copy_files_to_clipboard, clipboard_file_paths
from widgets import QueueRow


class QueueMixin:

    # ---------- adding / removing files ----------
    def on_add_files(self):
        paths = filedialog.askopenfilenames(
            title="Choose videos or images",
            filetypes=[("Media files", " ".join(f"*{e}" for e in sorted(MEDIA_EXTS))),
                       ("All files", "*.*")])
        self._add_paths(paths)

    def on_add_folder(self):
        folder = filedialog.askdirectory(title="Choose a folder of videos or images")
        if not folder:
            return
        paths = [os.path.join(folder, n) for n in sorted(os.listdir(folder))
                 if os.path.splitext(n)[1].lower() in MEDIA_EXTS]
        if not paths:
            self.status.configure(text="No videos or images found in that folder.")
            return
        self._add_paths(paths)

    @staticmethod
    def _expand_media(raw_paths):
        """Media files from a mixed list of files and folders (folders are
        listed one level deep); anything that isn't a media file is dropped."""
        paths = []
        for p in raw_paths:
            if os.path.isdir(p):
                paths += [os.path.join(p, n) for n in sorted(os.listdir(p))
                          if os.path.splitext(n)[1].lower() in MEDIA_EXTS]
            elif os.path.splitext(p)[1].lower() in MEDIA_EXTS:
                paths.append(p)
        return paths

    def _on_drop(self, event):
        """Handle files/folders dropped onto the window."""
        # tkdnd braces paths with spaces; parse with a regex rather than Tcl
        # splitlist, which would eat backslashes in Windows paths.
        tokens = re.findall(r"\{[^}]*\}|\S+", event.data)
        paths = self._expand_media(
            [t[1:-1] if t.startswith("{") and t.endswith("}") else t
             for t in tokens])
        if paths:
            self._add_paths(paths)
        else:
            self.status.configure(text="Drop videos, GIFs, or images (or a folder of them).")

    def _on_paste(self, _event=None):
        """Ctrl+V: add copied files to the queue, or a copied link to the
        Download tab. Paste inside a text field is left to the field."""
        if isinstance(self.focus_get(), tk.Entry):
            return
        if self.start_btn.cget("state") == "disabled":
            return  # queue is locked mid-run
        paths = self._expand_media(clipboard_file_paths())
        if paths:
            self._add_paths(paths)
            return
        try:
            text = self.clipboard_get().strip()
        except tk.TclError:
            text = ""
        if text.lower().startswith(("http://", "https://")):
            from models import TAB_DOWNLOAD
            self.tab_seg.set(TAB_DOWNLOAD)
            self._on_tab_change()
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, text)
            self.status.configure(text="Link pasted · press Download.")
        elif self._expand_media([text]):
            self._add_paths(self._expand_media([text]))
        else:
            self.status.configure(
                text="Nothing pasteable: copy media files, a folder, or a link.")

    def _add_paths(self, paths):
        existing = {j.path for j in self.jobs}
        new_jobs = []
        for path in paths:
            if path in existing:
                continue
            existing.add(path)  # also dedupe within this same batch of paths
            job = Job(id=self._next_id, path=path)
            self._next_id += 1
            job.row = QueueRow(self.queue_frame, job, self._select_job,
                               self._remove_job, self._open_job, self._context_menu,
                               self._on_row_drag, self.fonts)
            job.row.pack(fill="x", padx=6, pady=4)
            job.row.render(selected=False)
            self.jobs.append(job)
            new_jobs.append(job)
        if new_jobs:
            self.empty_label.pack_forget()
            threading.Thread(target=self._probe_worker, args=(new_jobs,),
                             daemon=True).start()
            self._update_counts()

    def _remove_job(self, job):
        if self.start_btn.cget("state") == "disabled":
            return  # don't edit the queue mid-encode
        if job.status == "downloading":  # stop the download before removing
            ev = self._dl_cancels.pop(job.id, None)
            if ev:
                ev.set()
        job.row.destroy()
        self.jobs.remove(job)
        if self.selected_id == job.id:
            self.selected_id = None
            nxt = next((j for j in self.jobs if j.info), None)
            if nxt:
                self._select_job(nxt)
            else:
                self.detail_label.configure(text="")
                self.note_label.configure(text="")
        if not self.jobs:
            self.empty_label.pack(pady=30)
        self._update_counts()

    def on_clear(self):
        if self.start_btn.cget("state") == "disabled":
            return
        for ev in self._dl_cancels.values():  # stop any in-flight downloads
            ev.set()
        self._dl_cancels.clear()
        for job in self.jobs:
            job.row.destroy()
        self.jobs.clear()
        self.selected_id = None
        self._prefilled = False
        self.detail_label.configure(text="")
        self.note_label.configure(text="")
        self.empty_label.pack(pady=30)
        self._update_counts()

    # Statuses that mean a row is finished and safe to sweep. In-flight rows
    # (reading, ready, queued, encoding, downloading) are kept.
    _FINISHED = {"done", "failed", "cancelled", "downloaded"}

    def on_clear_finished(self):
        """Tidy the queue after a batch: drop finished rows, keep the rest."""
        if self.start_btn.cget("state") == "disabled":
            return
        remaining = []
        for job in self.jobs:
            if job.status in self._FINISHED:
                job.row.destroy()
                if self.selected_id == job.id:
                    self.selected_id = None
            else:
                remaining.append(job)
        if len(remaining) == len(self.jobs):
            return  # nothing finished
        self.jobs = remaining
        if self.selected_id is None:  # reselect something, or clear the details
            nxt = next((j for j in self.jobs if j.info), None)
            if nxt:
                self._select_job(nxt)
            else:
                self.detail_label.configure(text="")
                self.note_label.configure(text="")
        if not self.jobs:
            self.empty_label.pack(pady=30)
        self._update_counts()

    def _repack_rows(self):
        """Re-pack every row to match self.jobs order (after a reorder)."""
        for job in self.jobs:
            job.row.pack_forget()
        for job in self.jobs:
            job.row.pack(fill="x", padx=6, pady=4)

    def _move_job(self, job, delta):
        """Shift a row up (delta -1) or down (delta +1) in the queue."""
        if self.start_btn.cget("state") == "disabled":
            return  # don't reorder mid-encode
        i = self.jobs.index(job)
        j = i + delta
        if 0 <= j < len(self.jobs):
            self.jobs[i], self.jobs[j] = self.jobs[j], self.jobs[i]
            self._repack_rows()

    def _move_selected(self, delta):
        if isinstance(self.focus_get(), tk.Entry):
            return  # don't hijack arrow keys while editing a text field
        job = self._selected_job()
        if job is not None:
            self._move_job(job, delta)

    def _on_row_drag(self, job, y_root):
        """Drag a row with the mouse: reorder when the pointer crosses the
        middle of a neighbouring row."""
        if self.start_btn.cget("state") == "disabled" or len(self.jobs) < 2:
            return
        try:
            i = self.jobs.index(job)
        except ValueError:
            return  # row was removed mid-drag
        for j, other in enumerate(self.jobs):
            if other is job:
                continue
            top = other.row.winfo_rooty()
            height = other.row.winfo_height()
            if top <= y_root < top + height:
                mid = top + height / 2
                if (j < i and y_root < mid) or (j > i and y_root > mid):
                    self.jobs.insert(j, self.jobs.pop(i))
                    self._repack_rows()
                break

    def on_browse_outdir(self):
        folder = filedialog.askdirectory(title="Choose output folder")
        if folder:
            self.outdir_entry.delete(0, "end")
            self.outdir_entry.insert(0, folder)
            self._update_note()

    def _probe_worker(self, jobs):
        from probe import extract_frame_png
        for job in jobs:
            try:
                info = probe_video(job.path)
                self.msg_queue.put(("probed", job.id, info, None))
                # A small preview for the row, generated serially in this same
                # thread so a big batch doesn't spawn a herd of ffmpeg calls.
                if not is_audio(job.path):
                    seconds = 1.0 if info.duration and info.duration > 2 else 0.0
                    png = extract_frame_png(job.path, seconds, max_width=96)
                    if png:
                        self.msg_queue.put(("row_thumb", job.id, png))
            except Exception as e:  # noqa: BLE001
                self.msg_queue.put(("probed", job.id, None, str(e)))

    # ---------- selection / notes ----------
    def _select_job(self, job):
        self.selected_id = job.id
        for j in self.jobs:
            j.row.render(selected=(j.id == self.selected_id))
        if job.info is not None:
            self._configure_gif_slider(job.info.duration)
        self._update_details()
        self._update_note()
        self._schedule_gif_preview()

    def _selected_job(self):
        return next((j for j in self.jobs if j.id == self.selected_id), None)

    def _update_details(self):
        job = self._selected_job()
        if job is None:
            self.detail_label.configure(text="")
            return
        if job.error:
            self.detail_label.configure(text=f"{os.path.basename(job.path)} · {job.error}")
            return
        if job.info is None:
            self.detail_label.configure(text=f"{os.path.basename(job.path)} · reading…")
            return
        i = job.info
        if is_image(job.path):  # duration/fps/audio are meaningless for stills
            text = (f"{os.path.basename(job.path)}  ·  {i.resolution_label}  "
                    f"·  {i.video_codec}  ·  {human_size(i.size_bytes)}")
        elif is_audio(job.path):
            mins, secs = divmod(int(i.duration), 60)
            text = (f"{os.path.basename(job.path)}  ·  {mins}m{secs:02d}s  "
                    f"·  {i.audio_codec or 'unknown'}  ·  {human_size(i.size_bytes)}")
        else:
            mins, secs = divmod(int(i.duration), 60)
            text = (f"{os.path.basename(job.path)}  ·  {i.resolution_label} @ {i.fps:.0f}fps  "
                    f"·  {mins}m{secs:02d}s  ·  {i.video_codec}/{i.audio_codec or 'no audio'}  "
                    f"·  {human_size(i.size_bytes)}")
        if job.trim:
            t0, t1 = job.trim
            text += f"  ·  ✂ {t0:g}s to {'end' if t1 is None else f'{t1:g}s'}"
        if job.crop:
            text += f"  ·  ◱ keeps {job.crop[0]}×{job.crop[1]}"
        if job.status == "done" and job.out_size:
            parts = f"{len(job.outputs)} parts, " if len(job.outputs) > 1 else ""
            text += f"      →   {parts}{human_size(job.out_size)}"
            if i.size_bytes:
                pct = (1 - job.out_size / i.size_bytes) * 100
                text += f" ({pct:.0f}% smaller)" if pct >= 0 else f" ({abs(pct):.0f}% larger)"
            if job.over_limit:
                text += f"   ⚠ over the {job.limit_mb:.0f} MB limit"
        self.detail_label.configure(text=text)

    def _job(self, jid):
        return next((j for j in self.jobs if j.id == jid), None)

    def _delete_selected(self):
        job = self._selected_job()
        if job is not None:
            self._remove_job(job)

    def _on_delete_key(self, _event=None):
        if isinstance(self.focus_get(), tk.Entry):
            return  # don't eat Delete while editing a text field
        self._delete_selected()

    def _on_return_key(self, _event=None):
        if self.start_btn.cget("state") == "normal":
            self.on_start()

    def on_open_output(self):
        folder = self.outdir_entry.get().strip()
        if not folder:
            job = self._selected_job() or next((j for j in self.jobs if j.output), None)
            if job and job.output:
                folder = os.path.dirname(job.output)
        if folder and os.path.isdir(folder):
            self._reveal(folder)
        else:
            self.status.configure(text="No output folder yet. Pick one or run a compression first.")

    def _reveal(self, path):
        try:
            if path and os.path.exists(path):
                os.startfile(path)  # Windows: open file or folder
                return
        except (AttributeError, OSError):
            pass
        self.status.configure(text=f"Path: {path}")

    def _reveal_select(self, path):
        """Open Explorer with `path` highlighted, not just its folder."""
        try:
            if sys.platform == "win32" and os.path.isfile(path):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
                return
        except OSError:
            pass
        self._reveal(os.path.dirname(path))

    def _open_job(self, job):
        """Double-click: open a finished file (or its folder), else just select."""
        outs = [o for o in (job.outputs or []) if o and os.path.exists(o)]
        if job.status == "done" and outs:
            if len(outs) == 1:
                self._reveal(outs[0])
            else:  # several parts: show them in their folder, first part selected
                self._reveal_select(outs[0])
        elif job.status == "downloaded":
            self._reveal(job.path)
        else:
            self._select_job(job)

    def _requeue_download(self, job):
        """Opt a downloaded file into compression runs (explicit, never automatic)."""
        job.from_url = False
        job.status = "ready"
        job.row.render(selected=(job.id == self.selected_id))
        self._update_counts()

    def _copy_to_clipboard(self, paths):
        if copy_files_to_clipboard(paths):
            what = "File" if len(paths) == 1 else f"{len(paths)} files"
            self.status.configure(
                text=f"{what} copied · paste with Ctrl+V into Discord, "
                     "Explorer, or most chat apps.")
        else:
            self.status.configure(text="Could not copy the file to the clipboard.")

    def _save_frame(self, job):
        """Grab one full-resolution frame of a video as a PNG next to it."""
        import customtkinter as ctk
        from probe import extract_frame_png
        dur = job.info.duration if job.info else 0
        dialog = ctk.CTkInputDialog(
            title="Save a frame",
            text=f"Time of the frame (seconds or mm:ss).\n"
                 f"The video is {dur:.1f}s long.")
        raw = (dialog.get_input() or "").strip()
        if not raw:
            return
        seconds = parse_time(raw)
        if seconds is None or (dur and seconds > dur):
            self.status.configure(text="That time isn't inside the video.")
            return
        png = extract_frame_png(job.path, seconds, max_width=None)
        if not png:
            self.status.configure(text="Could not read a frame at that time.")
            return
        stem = os.path.splitext(job.path)[0]
        out = f"{stem}_frame_{seconds:g}s.png"
        n = 2
        while os.path.exists(out):  # never overwrite an earlier grab
            out = f"{stem}_frame_{seconds:g}s_{n}.png"
            n += 1
        try:
            with open(out, "wb") as f:
                f.write(png)
        except OSError as e:
            self.status.configure(text=f"Could not save the frame: {e}")
            return
        self.status.configure(text=f"Frame saved · {os.path.basename(out)}")

    def _context_menu(self, job, event):
        self._select_job(job)
        menu = tk.Menu(self, tearoff=0)
        outs = [o for o in (job.outputs or []) if o and os.path.exists(o)]
        if job.status == "done" and outs:
            menu.add_command(label="Open", command=lambda: self._reveal(outs[0]))
            menu.add_command(label="Reveal in folder",
                             command=lambda: self._reveal_select(outs[0]))
            menu.add_command(label="Copy file" if len(outs) == 1 else "Copy files",
                             command=lambda: self._copy_to_clipboard(outs))
            menu.add_separator()
        elif job.status == "downloaded":
            menu.add_command(label="Open", command=lambda: self._reveal(job.path))
            menu.add_command(label="Reveal in folder",
                             command=lambda: self._reveal_select(job.path))
            menu.add_command(label="Copy file",
                             command=lambda: self._copy_to_clipboard([job.path]))
            menu.add_command(label="Queue for compression",
                             command=lambda: self._requeue_download(job))
            menu.add_separator()
        if job.info and not is_image(job.path) and not is_audio(job.path) \
                and os.path.exists(job.path):
            if self.start_btn.cget("state") != "disabled":
                menu.add_command(
                    label="Trim this file…" + ("  ✂" if job.trim else ""),
                    command=lambda: self._trim_dialog(job))
                menu.add_command(
                    label="Crop this file…" + ("  ◱" if job.crop else ""),
                    command=lambda: self._crop_dialog(job))
            menu.add_command(label="Save a frame…",
                             command=lambda: self._save_frame(job))
            menu.add_separator()
        # Reorder (only when the queue isn't locked by a run in progress).
        if self.start_btn.cget("state") != "disabled" and len(self.jobs) > 1:
            i = self.jobs.index(job)
            if i > 0:
                menu.add_command(label="Move up", command=lambda: self._move_job(job, -1))
            if i < len(self.jobs) - 1:
                menu.add_command(label="Move down", command=lambda: self._move_job(job, 1))
            menu.add_separator()
        menu.add_command(label="Remove from queue", command=lambda: self._remove_job(job))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _update_counts(self):
        if self.start_btn.cget("state") == "disabled":
            return
        ready = sum(1 for j in self.jobs
                    if j.info is not None and j.status != "downloaded")
        if not self.jobs:
            self.status.configure(text="Ready.")
            return
        text = f"{len(self.jobs)} file(s) · {ready} ready to compress."
        total_est = sum(j.est_size for j in self.jobs
                        if j.status == "ready" and j.est_size)
        if total_est:
            text += f"  Est. output ~{human_size(total_est)} total."
        self.status.configure(text=text)
