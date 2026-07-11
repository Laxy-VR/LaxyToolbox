"""Laxy: a themed, queue based CustomTkinter GUI for H.265 batch compression
with ffmpeg.

Add one file or a whole folder, choose one shared setting (best quality auto, or
a target size like Discord's 500 MB limit), and it works through the queue,
applying the same policy to every file with per file and overall progress.
"""

import json
import os
import queue
import re
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _DND_AVAILABLE = True
except Exception:  # noqa: BLE001 - drag-and-drop is optional
    _DND_AVAILABLE = False


import theme
from probe import (probe_video, recommend_settings, gpu_codecs, nvenc_works,
                   estimate_h265_bitrate_kbps, VideoInfo)
from encoder import (build_stages, build_gif_stages, build_image_stages,
                     build_audio_stages, build_cut_stages, suggest_parts,
                     run_encode, video_bitrate_for_target, cleanup_passlogs,
                     IMG_EXT, AUD_ENCODERS)

from models import (APP_NAME, APP_VERSION, CONFIG_PATH, TAB_COMPRESS, TAB_GIF,
                    TAB_IMAGE, TAB_AUDIO, TAB_DOWNLOAD, MODE_QUALITY,
                    MODE_TARGET, MODE_SPLIT, MODE_GIF, MODE_IMAGE, MODE_AUDIO,
                    MODE_DOWNLOAD, DL_RES_OPTIONS, CODEC_OPTIONS, HW_OPTIONS,
                    GIF_DITHER_OPTIONS, IMG_FORMAT_OPTIONS, IMG_QUALITY_OPTIONS,
                    IMG_RESIZE_OPTIONS, AUD_FORMAT_OPTIONS, AUD_QUALITY_OPTIONS,
                    PARTS_OPTIONS, PRESETS, RESOLUTIONS, FPS_OPTIONS,
                    AUDIO_OPTIONS, MEDIA_EXTS, is_image, is_audio, human_size,
                    unique_path, Job, status_display)
from widgets import QueueRow
from sysutil import (set_keep_awake, flash_taskbar, resource_path,
                     latest_release, is_newer_version)
from models import GITHUB_REPO
import downloader


_AppBase = (ctk.CTk, TkinterDnD.DnDWrapper) if _DND_AVAILABLE else (ctk.CTk,)


class App(*_AppBase):
    def __init__(self, fonts):
        super().__init__()
        self.fonts = fonts
        self.title(APP_NAME)
        self.configure(fg_color=theme.BG)

        # Enable drag-and-drop of files/folders onto the window (optional).
        self._dnd_ok = False
        if _DND_AVAILABLE:
            try:
                self.TkdndVersion = TkinterDnD._require(self)
                self._dnd_ok = True
            except Exception:  # noqa: BLE001 - fall back to buttons only
                self._dnd_ok = False

        self.jobs: list[Job] = []
        self.selected_id: int | None = None
        self._next_id = 0
        self._prefilled = False
        # None = unverified (offer GPU, verify in background), True/False = known
        self._gpu_ok = None

        self.cancel_event = threading.Event()
        self.msg_queue: queue.Queue = queue.Queue()
        self._dl_cancels: dict = {}  # per-download cancel events, keyed by job id

        self._build_ui()
        # Size to fit every control (measured on the taller Compress tab) and
        # forbid shrinking below that, so the action buttons are never hidden.
        self.update_idletasks()
        needed = self.winfo_reqheight()
        usable = self.winfo_screenheight() - 80  # leave room for the taskbar
        min_h = min(needed, usable)
        self.minsize(680, min_h)
        # Open a bit taller than the minimum when the screen allows it, giving
        # the file queue some breathing room.
        self.geometry(f"760x{min(needed + 60, usable)}")
        self._load_config()
        self._set_app_icon()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-o>", lambda _e: self.on_add_files())
        self.bind("<Delete>", self._on_delete_key)
        self.bind("<Return>", self._on_return_key)
        if self._dnd_ok:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)
        if GITHUB_REPO:
            self.after(1500, lambda: threading.Thread(
                target=self._update_check_worker, daemon=True).start())
        # Verify NVENC actually works here unless a previous run already
        # confirmed it (an AMD GPU or old NVIDIA driver fails at encode time
        # even though the bundled ffmpeg lists the encoders).
        if self._gpu_codecs and self._gpu_ok is not True:
            threading.Thread(target=self._gpu_probe_worker, daemon=True).start()
        self.after(100, self._poll_queue)

    # ---------- fonts ----------
    def f(self, role, size, weight="normal"):
        return ctk.CTkFont(family=self.fonts[role], size=size, weight=weight)

    # ---------- UI ----------
    def _build_ui(self):
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(18, 6))
        title_row = ctk.CTkFrame(header, fg_color="transparent")
        title_row.pack(fill="x")
        ctk.CTkLabel(title_row, text=APP_NAME, text_color="#a99ce0",
                     font=self.f("heading", 22, "bold")).pack(side="left")
        self.version_label = ctk.CTkLabel(
            title_row, text=f"v{APP_VERSION}", text_color=theme.TEXT_MUTED,
            font=self.f("mono", 11))
        self.version_label.pack(side="left", padx=(10, 0), pady=(8, 0))
        ctk.CTkLabel(header, text="Batch compression · video, GIF, images, and audio",
                     text_color=theme.TEXT_MUTED, font=self.f("sans", 12)).pack(anchor="w")

        # Toolbar
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill="x", padx=20, pady=(6, 6))
        ctk.CTkButton(toolbar, text="＋ Add files", command=self.on_add_files,
                      width=110).pack(side="left")
        ctk.CTkButton(toolbar, text="Add folder", command=self.on_add_folder,
                      width=110).pack(side="left", padx=8)
        ctk.CTkButton(toolbar, text="Clear", command=self.on_clear, width=80,
                      fg_color=theme.SURFACE2, hover_color=theme.BORDER,
                      text_color=theme.TEXT).pack(side="right")
        ctk.CTkButton(toolbar, text="Open output folder", command=self.on_open_output,
                      width=150, fg_color=theme.SURFACE2, hover_color=theme.BORDER,
                      text_color=theme.TEXT).pack(side="right", padx=8)

        # Queue
        self.queue_frame = ctk.CTkScrollableFrame(self, fg_color=theme.SURFACE,
                                                  corner_radius=12, height=150)
        self.queue_frame.pack(fill="both", expand=True, padx=20, pady=(0, 8))
        self.empty_label = ctk.CTkLabel(
            self.queue_frame,
            text="Drag videos or images here, or use Add files / Add folder.",
            text_color=theme.TEXT_MUTED, font=self.f("sans", 13))
        self.empty_label.pack(pady=30)

        # Selected-file details
        self.detail_label = ctk.CTkLabel(self, text="", anchor="w", justify="left",
                                         wraplength=640, text_color=theme.TEXT_MUTED,
                                         font=self.f("mono", 11))
        self.detail_label.pack(fill="x", padx=20, pady=(0, 6))

        # Top-level tab: compress to H.265, or make/shrink GIFs
        self.tab_seg = ctk.CTkSegmentedButton(
            self, values=[TAB_COMPRESS, TAB_GIF, TAB_IMAGE, TAB_AUDIO, TAB_DOWNLOAD],
            command=self._on_tab_change,
            font=self.f("sans", 13, "bold"), height=34)
        self.tab_seg.set(TAB_COMPRESS)
        self.tab_seg.pack(fill="x", padx=20, pady=(0, 6))

        # Settings card (shared across the whole queue). Two menu columns keep
        # the card short enough to fit fully on a 1080p screen.
        card = ctk.CTkFrame(self, fg_color=theme.SURFACE, corner_radius=12)
        card.pack(fill="x", padx=20, pady=(0, 8))
        card.grid_columnconfigure(1, weight=1)
        card.grid_columnconfigure(3, weight=1)

        self.settings_title = ctk.CTkLabel(card, text="Settings · applied to every file",
                                           font=self.f("sans", 13, "bold"))
        self.settings_title.grid(row=0, column=0, columnspan=4, sticky="w",
                                 padx=14, pady=(12, 8))

        self.mode_seg = ctk.CTkSegmentedButton(
            card, values=[MODE_QUALITY, MODE_TARGET, MODE_SPLIT],
            command=self._on_mode_change)
        self.mode_seg.set(MODE_QUALITY)
        self.mode_seg.grid(row=1, column=0, columnspan=4, sticky="ew", padx=14, pady=(0, 8))

        # Mode-specific parameters (max size / parts), used in target & split modes
        self.params_frame = ctk.CTkFrame(card, fg_color="transparent")
        self.params_frame.grid(row=2, column=0, columnspan=4, sticky="w", padx=12)
        self.size_label = ctk.CTkLabel(self.params_frame, text="Max size")
        self.target_entry = ctk.CTkEntry(self.params_frame, width=80)
        self.target_entry.insert(0, "500")
        self.target_entry.bind("<KeyRelease>", lambda _e: self._update_note())
        self.size_unit = ctk.CTkLabel(self.params_frame, text="MB", text_color=theme.TEXT_MUTED)
        self.parts_label = ctk.CTkLabel(self.params_frame, text="Parts")
        self.parts_menu = ctk.CTkOptionMenu(self.params_frame, width=90,
                                            values=[p[0] for p in PARTS_OPTIONS],
                                            command=self._on_setting_changed)
        self.parts_menu.set(PARTS_OPTIONS[0][0])

        # GIF controls (shown only in Make GIF mode): clip range + dithering on
        # the left, a live preview of the frame at the clip start on the right.
        self.gif_frame = ctk.CTkFrame(card, fg_color="transparent")
        gif_left = ctk.CTkFrame(self.gif_frame, fg_color="transparent")
        gif_left.grid(row=0, column=0, sticky="nw")
        row0 = ctk.CTkFrame(gif_left, fg_color="transparent")
        row0.pack(anchor="w")
        ctk.CTkLabel(row0, text="Clip start").pack(side="left")
        self.gif_start = ctk.CTkEntry(row0, width=70)
        self.gif_start.insert(0, "0")
        self.gif_start.bind("<KeyRelease>", lambda _e: self._on_gif_clip_edited())
        self.gif_start.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(row0, text="s   length").pack(side="left", padx=(4, 0))
        self.gif_len = ctk.CTkEntry(row0, width=70)
        self.gif_len.insert(0, "5")
        self.gif_len.bind("<KeyRelease>", lambda _e: self._update_note())
        self.gif_len.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(row0, text="s", text_color=theme.TEXT_MUTED).pack(side="left", padx=(4, 0))
        row1 = ctk.CTkFrame(gif_left, fg_color="transparent")
        row1.pack(anchor="w", pady=(8, 0))
        ctk.CTkLabel(row1, text="Dithering").pack(side="left")
        self.dither_menu = ctk.CTkOptionMenu(row1, width=210,
                                             values=[d[0] for d in GIF_DITHER_OPTIONS])
        self.dither_menu.set(GIF_DITHER_OPTIONS[0][0])
        self.dither_menu.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(row1, text="·   short clips only; GIFs get big fast",
                     text_color=theme.TEXT_MUTED).pack(side="left", padx=(10, 0))
        self.gif_preview = ctk.CTkLabel(self.gif_frame, text="preview",
                                        text_color=theme.TEXT_MUTED,
                                        width=160, height=90, fg_color=theme.SURFACE2,
                                        corner_radius=8)
        self.gif_preview.grid(row=0, column=1, sticky="ne", padx=(16, 0))
        self._thumb_after = None   # debounce timer for preview refreshes
        self._thumb_token = 0      # ignore stale async thumbnail results
        self._thumb_image = None   # keep a reference so Tk doesn't GC it
        self._thumb_target = None  # which preview label the pending thumb is for

        # Image controls (shown only on the Images tab)
        self.image_frame = ctk.CTkFrame(card, fg_color="transparent")
        img_left = ctk.CTkFrame(self.image_frame, fg_color="transparent")
        img_left.grid(row=0, column=0, sticky="nw")
        for irow, (label, values, default, attr) in enumerate([
                ("Format", [o[0] for o in IMG_FORMAT_OPTIONS], IMG_FORMAT_OPTIONS[0][0], "img_format_menu"),
                ("Quality", [o[0] for o in IMG_QUALITY_OPTIONS], IMG_QUALITY_OPTIONS[1][0], "img_quality_menu"),
                ("Resize", [o[0] for o in IMG_RESIZE_OPTIONS], IMG_RESIZE_OPTIONS[0][0], "img_resize_menu")]):
            ctk.CTkLabel(img_left, text=label, width=60, anchor="w").grid(
                row=irow, column=0, sticky="w", pady=3)
            menu = ctk.CTkOptionMenu(img_left, width=230, values=values,
                                     command=self._on_setting_changed)
            menu.set(default)
            menu.grid(row=irow, column=1, sticky="w", padx=(8, 0), pady=3)
            setattr(self, attr, menu)
        self.img_preview = ctk.CTkLabel(self.image_frame, text="preview",
                                        text_color=theme.TEXT_MUTED,
                                        width=160, height=90, fg_color=theme.SURFACE2,
                                        corner_radius=8)
        self.img_preview.grid(row=0, column=1, sticky="ne", padx=(16, 0))
        self.image_frame.grid_columnconfigure(1, weight=1)

        # Download controls (shown only on the Download tab)
        self.download_frame = ctk.CTkFrame(card, fg_color="transparent")
        self.download_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.download_frame,
                     text="Video link (YouTube, Twitter, and most sites):",
                     ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        self.url_entry = ctk.CTkEntry(self.download_frame,
                                      placeholder_text="https://…")
        self.url_entry.grid(row=1, column=0, columnspan=2, sticky="ew")
        dlrow = ctk.CTkFrame(self.download_frame, fg_color="transparent")
        dlrow.grid(row=2, column=0, sticky="w", pady=(8, 0))
        ctk.CTkLabel(dlrow, text="Resolution").pack(side="left")
        self.dl_res_menu = ctk.CTkOptionMenu(dlrow, width=150,
                                             values=[o[0] for o in DL_RES_OPTIONS])
        self.dl_res_menu.set(DL_RES_OPTIONS[0][0])
        self.dl_res_menu.pack(side="left", padx=(8, 0))
        self.dl_audio_check = ctk.CTkCheckBox(dlrow, text="Audio only (MP3)")
        self.dl_audio_check.pack(side="left", padx=(20, 0))

        # Audio conversion controls (shown only on the Audio tab)
        self.audio_frame = ctk.CTkFrame(card, fg_color="transparent")
        ctk.CTkLabel(self.audio_frame, text="Format", width=60, anchor="w").grid(
            row=0, column=0, sticky="w", pady=3)
        self.aud_format_menu = ctk.CTkOptionMenu(
            self.audio_frame, width=230, values=[o[0] for o in AUD_FORMAT_OPTIONS],
            command=self._on_setting_changed)
        self.aud_format_menu.set(AUD_FORMAT_OPTIONS[0][0])
        self.aud_format_menu.grid(row=0, column=1, sticky="w", padx=(8, 0), pady=3)
        ctk.CTkLabel(self.audio_frame, text="Quality", width=60, anchor="w").grid(
            row=1, column=0, sticky="w", pady=3)
        self.aud_quality_menu = ctk.CTkOptionMenu(
            self.audio_frame, width=230, values=[o[0] for o in AUD_QUALITY_OPTIONS],
            command=self._on_setting_changed)
        self.aud_quality_menu.set(AUD_QUALITY_OPTIONS[1][0])
        self.aud_quality_menu.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=3)

        # Codec + hardware selectors, side by side. The hardware menu only
        # exists when this machine has any NVENC encoder at all.
        self._gpu_codecs = gpu_codecs()
        self._menu_row(card, 3, 0, "Codec", [c[0] for c in CODEC_OPTIONS],
                       CODEC_OPTIONS[0][0], "codec_menu", self._on_codec_change)
        if self._gpu_codecs:
            self._menu_row(card, 3, 2, "Hardware", [h[0] for h in HW_OPTIONS],
                           HW_OPTIONS[0][0], "hw_menu", self._on_codec_change)
        else:
            self.hw_menu = None
            self.hw_menu_label = None

        # CRF / CQ row
        self.crf_caption = ctk.CTkLabel(card, text="Quality (CRF)")
        self.crf_caption.grid(row=4, column=0, sticky="w", padx=14, pady=(4, 0))
        self.crf_value = ctk.CTkLabel(card, text="23", font=self.f("mono", 12))
        self.crf_value.grid(row=4, column=3, sticky="e", padx=14, pady=(4, 0))
        self.crf_slider = ctk.CTkSlider(card, from_=16, to=32, number_of_steps=16,
                                        command=self._on_crf)
        self.crf_slider.grid(row=5, column=0, columnspan=4, sticky="ew", padx=14)
        self.crf_slider.set(23)
        self.crf_hint = ctk.CTkLabel(
            card, text="lower = better quality, bigger file   ·   higher = smaller file",
            text_color=theme.TEXT_MUTED, font=self.f("sans", 11))
        self.crf_hint.grid(row=6, column=0, columnspan=4, sticky="w", padx=14, pady=(0, 6))

        self._menu_row(card, 7, 0, "Preset (speed)", PRESETS, "slow", "preset_menu")
        self._menu_row(card, 7, 2, "Audio", [a[0] for a in AUDIO_OPTIONS],
                       AUDIO_OPTIONS[2][0], "audio_menu", self._on_setting_changed)
        self._menu_row(card, 8, 0, "Resolution", [r[0] for r in RESOLUTIONS],
                       RESOLUTIONS[0][0], "res_menu", self._on_setting_changed)
        self._menu_row(card, 8, 2, "Frame rate", [f[0] for f in FPS_OPTIONS],
                       FPS_OPTIONS[0][0], "fps_menu", self._on_setting_changed)

        # Optional trim (Compress tab only): encode just start..end seconds
        self.trim_frame = ctk.CTkFrame(card, fg_color="transparent")
        self.trim_frame.grid(row=9, column=0, columnspan=4, sticky="w",
                             padx=14, pady=(2, 0))
        ctk.CTkLabel(self.trim_frame, text="Trim").pack(side="left")
        self.trim_start = ctk.CTkEntry(self.trim_frame, width=70,
                                       placeholder_text="start")
        self.trim_start.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(self.trim_frame, text="s   to").pack(side="left", padx=(4, 0))
        self.trim_end = ctk.CTkEntry(self.trim_frame, width=70,
                                     placeholder_text="end")
        self.trim_end.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(self.trim_frame, text="s", text_color=theme.TEXT_MUTED).pack(
            side="left", padx=(4, 0))
        self.cut_only_check = ctk.CTkCheckBox(
            self.trim_frame, text="Cut only (no re-encode)",
            command=self._on_cut_only_toggle)
        self.cut_only_check.pack(side="left", padx=(20, 0))

        self.note_label = ctk.CTkLabel(card, text="", anchor="w", justify="left",
                                       wraplength=620, text_color="#9a8fd0",
                                       font=self.f("sans", 12))
        self.note_label.grid(row=10, column=0, columnspan=4, sticky="w",
                             padx=14, pady=(4, 12))

        # Output folder
        out = ctk.CTkFrame(self, fg_color="transparent")
        out.pack(fill="x", padx=20, pady=(0, 6))
        out.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(out, text="Save to").grid(row=0, column=0, padx=(0, 8))
        self.outdir_entry = ctk.CTkEntry(
            out, placeholder_text="Same folder as each source (files get “_h265”)")
        self.outdir_entry.grid(row=0, column=1, sticky="ew")
        # keep the Download tab's note in sync while the user edits the folder
        self.outdir_entry.bind("<KeyRelease>", lambda _e: self._update_note())
        ctk.CTkButton(out, text="Browse", width=80, command=self.on_browse_outdir).grid(
            row=0, column=2, padx=(8, 0))

        # Progress + status
        self.progress = ctk.CTkProgressBar(self)
        self.progress.set(0)
        self.progress.pack(fill="x", padx=20, pady=(6, 4))
        self.status = ctk.CTkLabel(self, text="Ready.", anchor="w",
                                   text_color=theme.TEXT_MUTED, font=self.f("sans", 12))
        self.status.pack(fill="x", padx=20)

        # Actions
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=20, pady=12)
        actions.grid_columnconfigure((0, 1), weight=1)
        self.start_btn = ctk.CTkButton(actions, text="Start compression",
                                       height=40, font=self.f("sans", 14, "bold"),
                                       command=self.on_start)
        self.start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.cancel_btn = ctk.CTkButton(actions, text="Cancel", height=40,
                                        fg_color=theme.SURFACE2, hover_color=theme.BORDER,
                                        text_color=theme.TEXT, command=self.on_cancel,
                                        state="disabled")
        self.cancel_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        self._refresh_mode()  # sync all mode-dependent UI now that widgets exist

    def _menu_row(self, parent, row, col, label, values, default, attr, command=None):
        lbl = ctk.CTkLabel(parent, text=label)
        lbl.grid(row=row, column=col, sticky="w", padx=(14, 4), pady=6)
        menu = ctk.CTkOptionMenu(parent, values=values, command=command)
        menu.set(default)
        menu.grid(row=row, column=col + 1, sticky="ew", padx=(0, 14), pady=6)
        setattr(self, attr, menu)
        setattr(self, attr + "_label", lbl)

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

    def _on_drop(self, event):
        """Handle files/folders dropped onto the window."""
        # tkdnd braces paths with spaces; parse with a regex rather than Tcl
        # splitlist, which would eat backslashes in Windows paths.
        tokens = re.findall(r"\{[^}]*\}|\S+", event.data)
        paths = []
        for token in tokens:
            p = token[1:-1] if token.startswith("{") and token.endswith("}") else token
            if os.path.isdir(p):
                paths += [os.path.join(p, n) for n in sorted(os.listdir(p))
                          if os.path.splitext(n)[1].lower() in MEDIA_EXTS]
            elif os.path.splitext(p)[1].lower() in MEDIA_EXTS:
                paths.append(p)
        if paths:
            self._add_paths(paths)
        else:
            self.status.configure(text="Drop videos, GIFs, or images (or a folder of them).")

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
                           self.fonts)
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
        threading.Thread(target=self._download_worker,
                         args=(job.id, url, self._download_dir(), cancel,
                               max_height, audio_only),
                         daemon=True).start()

    def _download_worker(self, jid, url, outdir, cancel, max_height, audio_only):
        try:
            if not downloader.has_ytdlp():
                self.msg_queue.put(("dl_setup", "Setting up the downloader (one time)…"))
                downloader.fetch_ytdlp()
            elif downloader.update_ytdlp_if_stale():
                # A stale downloader silently gets offered worse quality by
                # sites, so refresh it before downloading rather than after
                # something visibly fails.
                self.msg_queue.put(("dl_setup", "Updated the downloader."))
            path, err = downloader.download_with_update_retry(
                url, outdir,
                lambda frac: self.msg_queue.put(("dl_progress", jid, frac)),
                cancel, max_height, audio_only)
            self.msg_queue.put(("dl_done", jid, path, err))
        except Exception as e:  # noqa: BLE001 - e.g. no internet for the fetch
            self.msg_queue.put(("dl_done", jid, None, str(e)))

    def _on_dl_done(self, jid, path, err):
        self._dl_cancels.pop(jid, None)
        job = self._job(jid)
        if job is None:  # row was removed while downloading
            return
        if err == "cancelled":
            self._set_status(jid, "cancelled")
            return
        if path is None:
            job.status, job.error = "failed", err
            job.row.render(selected=(job.id == self.selected_id))
            if job.id == self.selected_id:
                self._update_details()
            return
        # Swap the URL job into a normal local-file job and probe it.
        job.path = path
        job.status = "reading"
        job.progress = 0.0
        job.row.set_name(path)
        job.row.render(selected=(job.id == self.selected_id))
        threading.Thread(target=self._probe_worker, args=([job],), daemon=True).start()

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
                               self.fonts)
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

    def on_browse_outdir(self):
        folder = filedialog.askdirectory(title="Choose output folder")
        if folder:
            self.outdir_entry.delete(0, "end")
            self.outdir_entry.insert(0, folder)
            self._update_note()

    def _probe_worker(self, jobs):
        for job in jobs:
            try:
                info = probe_video(job.path)
                self.msg_queue.put(("probed", job.id, info, None))
            except Exception as e:  # noqa: BLE001
                self.msg_queue.put(("probed", job.id, None, str(e)))

    # ---------- selection / notes ----------
    def _select_job(self, job):
        self.selected_id = job.id
        for j in self.jobs:
            j.row.render(selected=(j.id == self.selected_id))
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
        if job.status == "done" and job.out_size:
            parts = f"{len(job.outputs)} parts, " if len(job.outputs) > 1 else ""
            text += f"      →   {parts}{human_size(job.out_size)}"
            if i.size_bytes:
                pct = (1 - job.out_size / i.size_bytes) * 100
                text += f" ({pct:.0f}% smaller)" if pct >= 0 else f" ({abs(pct):.0f}% larger)"
            if job.over_limit:
                text += f"   ⚠ over the {job.limit_mb:.0f} MB limit"
        self.detail_label.configure(text=text)

    def _update_note(self):
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
        Returns None on invalid input."""
        try:
            start = float(self.gif_start.get() or 0)
            length = float(self.gif_len.get() or 0)
        except ValueError:
            return None
        if start < 0 or length <= 0:
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
        w, h, _ = self._effective_res_fps(info)
        fps = dict(FPS_OPTIONS)[self.fps_menu.get()] or 15
        est = (w * h * fps * length * 0.20) / (1024 * 1024)  # rough, very content dependent
        return (f"GIF of {length:.0f}s from {start:.0f}s at {w}×{h}, {fps:.0f} fps. "
                f"Rough size ~{est:.1f} MB. Lower fps / smaller size keeps it small.")

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

    def _on_setting_changed(self, _value=None):
        self._update_note()

    # ---------- GIF preview thumbnail ----------
    def _on_gif_clip_edited(self):
        self._update_note()
        self._schedule_gif_preview()

    def _schedule_gif_preview(self):
        """Debounce preview refreshes while the user is still typing."""
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
            if not is_image(job.path):
                return
            seconds = 0.0
            self._thumb_target = self.img_preview
        else:
            try:
                seconds = float(self.gif_start.get() or 0)
            except ValueError:
                return
            if job.info.duration > 0:
                seconds = min(max(seconds, 0), max(job.info.duration - 0.1, 0))
            self._thumb_target = self.gif_preview
        self._thumb_token += 1
        token = self._thumb_token
        threading.Thread(target=self._thumb_worker,
                         args=(job.path, seconds, token), daemon=True).start()

    def _thumb_worker(self, path, seconds, token):
        from probe import extract_frame_png
        png = extract_frame_png(path, seconds)
        self.msg_queue.put(("thumb", token, png))

    def _show_thumb(self, token, png):
        if token != self._thumb_token or not png or self._thumb_target is None:
            return
        try:
            import io
            from PIL import Image
            img = Image.open(io.BytesIO(png))
            scale = min(160 / img.width, 90 / img.height)
            size = (max(int(img.width * scale), 1), max(int(img.height * scale), 1))
            self._thumb_image = ctk.CTkImage(light_image=img, dark_image=img, size=size)
            self._thumb_target.configure(image=self._thumb_image, text="")
        except Exception:  # noqa: BLE001 - preview is best-effort only
            pass

    def _layout_params(self, mode):
        for w in (self.size_label, self.target_entry, self.size_unit,
                  self.parts_label, self.parts_menu):
            w.pack_forget()
        self.params_frame.grid_remove()
        self.gif_frame.grid_remove()
        self.image_frame.grid_remove()
        self.audio_frame.grid_remove()
        self.download_frame.grid_remove()
        if mode == MODE_QUALITY:
            return
        if mode == MODE_GIF:
            self.gif_frame.grid(row=2, column=0, columnspan=4, sticky="w", padx=12)
            return
        if mode == MODE_IMAGE:
            self.image_frame.grid(row=2, column=0, columnspan=4, sticky="ew",
                                  padx=12, pady=(0, 6))
            return
        if mode == MODE_AUDIO:
            self.audio_frame.grid(row=2, column=0, columnspan=4, sticky="w",
                                  padx=12, pady=(0, 6))
            return
        if mode == MODE_DOWNLOAD:
            self.download_frame.grid(row=2, column=0, columnspan=4, sticky="ew",
                                     padx=14, pady=(0, 6))
            return
        self.params_frame.grid()
        self.size_label.configure(text="Max size" if mode == MODE_TARGET else "Max per part")
        self.size_label.pack(side="left")
        self.target_entry.pack(side="left", padx=8)
        if mode == MODE_TARGET:
            self.size_unit.configure(text="MB   ·   each file fits under this (Discord Nitro: 500)")
            self.size_unit.pack(side="left")
        else:
            self.size_unit.configure(text="MB")
            self.size_unit.pack(side="left")
            self.parts_label.pack(side="left", padx=(16, 6))
            self.parts_menu.pack(side="left")

    _TAB_MODES = {TAB_GIF: MODE_GIF, TAB_IMAGE: MODE_IMAGE,
                  TAB_AUDIO: MODE_AUDIO, TAB_DOWNLOAD: MODE_DOWNLOAD}

    def _mode(self):
        """Effective mode: the tab decides for GIF/Images/Audio/Download,
        else the Compress sub-mode."""
        return self._TAB_MODES.get(self.tab_seg.get()) or self.mode_seg.get()

    def _compress_only_widgets(self):
        """Rows that only make sense for video output (hidden on the GIF tab)."""
        widgets = [self.crf_caption, self.crf_value, self.crf_slider, self.crf_hint,
                   self.preset_menu, self.preset_menu_label,
                   self.audio_menu, self.audio_menu_label,
                   self.codec_menu, self.codec_menu_label]
        if self.hw_menu is not None and self._gpu_ok is not False:
            widgets += [self.hw_menu, self.hw_menu_label]
        return widgets

    def _resfps_widgets(self):
        return [self.res_menu, self.res_menu_label, self.fps_menu, self.fps_menu_label]

    def _refresh_mode(self):
        mode = self._mode()
        tab = self.tab_seg.get()
        titles = {TAB_COMPRESS: ("Settings · applied to every file", "Start compression"),
                  TAB_GIF: ("GIF settings · applied to every file", "Make GIFs"),
                  TAB_IMAGE: ("Image settings · applied to every file", "Compress images"),
                  TAB_AUDIO: ("Audio settings · applied to every file", "Extract audio"),
                  TAB_DOWNLOAD: ("Download a video", "Download")}
        title, btn = titles[tab]
        self.settings_title.configure(text=title)
        self.start_btn.configure(text=btn)

        def show(widgets, on):
            for w in widgets:
                w.grid() if on else w.grid_remove()

        compress = tab == TAB_COMPRESS
        show([self.mode_seg, self.trim_frame], compress)
        show(self._compress_only_widgets(), compress)
        show(self._resfps_widgets(), compress or tab == TAB_GIF)
        # The empty-field default differs per tab; say so honestly.
        if tab == TAB_DOWNLOAD:
            dl_default = os.path.join(os.path.expanduser("~"), "Downloads")
            self.outdir_entry.configure(placeholder_text=f"Your Downloads folder ({dl_default})")
            self._prefill_url_from_clipboard()
        else:
            self.outdir_entry.configure(
                placeholder_text="Same folder as each source (files get a suffix)")
        self._layout_params(mode)
        self._set_crf_enabled(mode == MODE_QUALITY and not self._cut_only())
        self._sync_controls()
        self._update_note()

    def _on_tab_change(self, _value=None):
        self._refresh_mode()
        self._schedule_gif_preview()

    def _on_mode_change(self, _value=None):
        self._refresh_mode()

    def _codec_value(self):
        return dict(CODEC_OPTIONS)[self.codec_menu.get()]

    def _hw_value(self):
        """'cpu' or 'nvenc', falling back to cpu when the GPU can't do the codec
        or the machine failed the real NVENC probe."""
        if self.hw_menu is None or self._gpu_ok is False:
            return "cpu"
        hw = dict(HW_OPTIONS)[self.hw_menu.get()]
        if hw == "nvenc" and self._codec_value() not in self._gpu_codecs:
            return "cpu"
        return hw

    def _on_codec_change(self, _value=None):
        nvenc = self._hw_value() == "nvenc"
        self.crf_caption.configure(text="Quality (CQ)" if nvenc else "Quality (CRF)")
        # Offer the GPU only for codecs this machine can hardware-encode.
        if self.hw_menu is not None:
            if self._codec_value() in self._gpu_codecs:
                self.hw_menu.configure(values=[h[0] for h in HW_OPTIONS])
            else:
                self.hw_menu.set(HW_OPTIONS[0][0])
                self.hw_menu.configure(values=[HW_OPTIONS[0][0]])
        self._sync_controls()
        self._update_note()

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

    def _open_job(self, job):
        """Double-click: open a finished file (or its folder), else just select."""
        outs = [o for o in (job.outputs or []) if o and os.path.exists(o)]
        if job.status == "done" and outs:
            self._reveal(outs[0] if len(outs) == 1 else os.path.dirname(outs[0]))
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

    def _context_menu(self, job, event):
        self._select_job(job)
        menu = tk.Menu(self, tearoff=0)
        outs = [o for o in (job.outputs or []) if o and os.path.exists(o)]
        if job.status == "done" and outs:
            menu.add_command(label="Open", command=lambda: self._reveal(outs[0]))
            menu.add_command(label="Reveal in folder",
                             command=lambda: self._reveal(os.path.dirname(outs[0])))
            menu.add_separator()
        elif job.status == "downloaded":
            menu.add_command(label="Open", command=lambda: self._reveal(job.path))
            menu.add_command(label="Reveal in folder",
                             command=lambda: self._reveal(os.path.dirname(job.path)))
            menu.add_command(label="Queue for compression",
                             command=lambda: self._requeue_download(job))
            menu.add_separator()
        menu.add_command(label="Remove from queue", command=lambda: self._remove_job(job))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

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
        for job in jobs:  # all widget access happens here on the main thread
            job.outputs = [unique_path(o, claimed)
                           for o in self._outputs_for(job, mode, size_mb, parts_choice,
                                                      settings.get("trim"))]
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

    @staticmethod
    def _trimmed_duration(duration, trim):
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
            out = os.path.join(folder, f"{stem}.gif")
            if os.path.abspath(out) == os.path.abspath(job.path):  # gif -> gif
                out = os.path.join(folder, f"{stem}_laxy.gif")
            return [out]
        if self._cut_only():  # stream copy must stay in the source container
            src_ext = os.path.splitext(job.path)[1] or ".mp4"
            return [os.path.join(folder, f"{stem}_cut{src_ext}")]
        if mode != MODE_SPLIT:
            return [os.path.join(folder, f"{stem}_h265.mp4")]
        w, h, fps = self._effective_res_fps(job.info)
        dur = self._trimmed_duration(job.info.duration, trim)
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
            stages, passlogs, reason = self._plan_job(job, mode, base_settings, size_mb)
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

    def _plan_job(self, job, mode, base_settings, size_mb):
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
            stages = [(lbl, cmd, length) for lbl, cmd in build_gif_stages(
                job.path, job.outputs[0], settings, segment=(start, length))]
            return stages, [], None

        # Video modes share the optional trim: encode only start..end seconds.
        trim = settings.get("trim")
        t0 = min(trim[0], max(dur - 0.1, 0)) if (trim and dur > 0) \
            else (trim[0] if trim else 0.0)
        dur_eff = self._trimmed_duration(dur, trim)
        if trim and dur_eff <= 0:
            return None, [], "the trim range is outside this video"
        seg_all = (t0, dur_eff) if trim else None

        if settings.get("cut_only"):  # lossless stream copy of the trim range
            stages = [(lbl, cmd, dur_eff) for lbl, cmd in
                      build_cut_stages(job.path, job.outputs[0], seg_all)]
            return stages, [], None

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
            self._show_thumb(msg[1], msg[2])
        elif kind == "dl_setup":
            self.status.configure(text=msg[1])
        elif kind == "dl_progress":
            job = self._job(msg[1])
            if job and job.status == "downloading":
                job.progress = msg[2]
                job.row.render(selected=(job.id == self.selected_id))
        elif kind == "dl_done":
            self._on_dl_done(msg[1], msg[2], msg[3])
        elif kind == "update":
            self._show_update(msg[1], msg[2])
        elif kind == "gpu_ok":
            self._on_gpu_probed(msg[1])

    def _job(self, jid):
        return next((j for j in self.jobs if j.id == jid), None)

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
                f"“{os.path.basename(job.path)}”. Retrying later or on another "
                "network often gets the full quality."))
        if info is not None and not self._prefilled:
            self._apply_recommended(recommend_settings(info))
            self._prefilled = True
        if info is not None and self.selected_id is None:
            self._select_job(job)
        elif job.id == self.selected_id:
            self._update_details()
            self._update_note()
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
            job.error = tail[-1]
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

    # ---------- helpers ----------
    def _collect_settings(self) -> dict:
        audio_mode, audio_bitrate = dict(AUDIO_OPTIONS)[self.audio_menu.get()]
        return {
            "codec": self._codec_value(),
            "encoder": self._hw_value(),  # "cpu" or "nvenc"
            "crf": int(self.crf_slider.get()),
            "preset": self.preset_menu.get(),
            "target_height": dict(RESOLUTIONS)[self.res_menu.get()],
            "target_fps": dict(FPS_OPTIONS)[self.fps_menu.get()],
            "audio_mode": audio_mode,
            "audio_bitrate": audio_bitrate or "128k",
            "gif_dither": dict(GIF_DITHER_OPTIONS)[self.dither_menu.get()],
            "img_format": dict(IMG_FORMAT_OPTIONS)[self.img_format_menu.get()],
            "img_quality": dict(IMG_QUALITY_OPTIONS)[self.img_quality_menu.get()],
            "img_resize": dict(IMG_RESIZE_OPTIONS)[self.img_resize_menu.get()],
            "aud_format": dict(AUD_FORMAT_OPTIONS)[self.aud_format_menu.get()],
            "aud_bitrate": dict(AUD_QUALITY_OPTIONS)[self.aud_quality_menu.get()],
        }

    def _on_crf(self, value):
        self.crf_value.configure(text=str(int(value)))
        self._update_note()  # keep the estimated output size in sync

    def _set_crf_enabled(self, enabled):
        self.crf_slider.configure(state="normal" if enabled else "disabled")
        self.crf_caption.configure(text_color=theme.TEXT if enabled else theme.TEXT_MUTED)
        if enabled:
            hint = "lower = better quality, bigger file   ·   higher = smaller file"
        elif self._mode() == MODE_GIF:
            hint = "(GIF quality comes from frame rate and size, not CRF)"
        else:
            hint = "(quality is set by the size limit in this mode)"
        self.crf_hint.configure(text=hint)

    def _cut_only(self) -> bool:
        return (self.tab_seg.get() == TAB_COMPRESS
                and bool(self.cut_only_check.get()))

    def _on_cut_only_toggle(self):
        self._sync_controls()
        self._set_crf_enabled(self._mode() == MODE_QUALITY and not self._cut_only())
        self._update_note()

    def _sync_controls(self):
        """Grey out controls that don't apply to the current mode/encoder."""
        gif = self._mode() == MODE_GIF
        cut = self._cut_only()  # lossless cut ignores every encode setting
        off = gif or cut
        nvenc = self._hw_value() == "nvenc"
        self.codec_menu.configure(state="disabled" if off else "normal")
        if self.hw_menu is not None:
            self.hw_menu.configure(state="disabled" if off else "normal")
        self.preset_menu.configure(state="disabled" if (off or nvenc) else "normal")
        self.audio_menu.configure(state="disabled" if off else "normal")
        self.res_menu.configure(state="disabled" if cut else "normal")
        self.fps_menu.configure(state="disabled" if cut else "normal")
        self.mode_seg.configure(state="disabled" if cut else "normal")

    def _apply_recommended(self, rec):
        self.crf_slider.set(rec["crf"])
        self._on_crf(rec["crf"])
        self.preset_menu.set(rec["preset"])
        if rec["audio_mode"] == "copy":
            self.audio_menu.set(AUDIO_OPTIONS[0][0])
        elif rec.get("audio_bitrate") == "192k":
            self.audio_menu.set(AUDIO_OPTIONS[1][0])
        else:
            self.audio_menu.set(AUDIO_OPTIONS[2][0])

    def _update_counts(self):
        if self.start_btn.cget("state") == "disabled":
            return
        ready = sum(1 for j in self.jobs
                    if j.info is not None and j.status != "downloaded")
        if not self.jobs:
            self.status.configure(text="Ready.")
        else:
            self.status.configure(text=f"{len(self.jobs)} file(s) · {ready} ready to compress.")

    def _set_running(self, running):
        state = "disabled" if running else "normal"
        self.start_btn.configure(state=state)
        self.mode_seg.configure(state=state)
        self.tab_seg.configure(state=state)
        self.cancel_btn.configure(state="normal" if running else "disabled")
        if not running:
            self._sync_controls()  # re-apply cut-only/GIF greying after a run

    # ---------- config persistence + icon ----------
    def _load_config(self):
        """Restore last-used workflow settings. Quality (crf/preset/audio) is
        left to the per-file auto-recommendation, so it isn't restored here."""
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            return

        def set_menu(menu, key):
            if menu is not None and cfg.get(key) in menu.cget("values"):
                menu.set(cfg[key])

        if isinstance(cfg.get("nvenc_ok"), bool):
            self._gpu_ok = cfg["nvenc_ok"]
            if self._gpu_ok is False:
                self._hide_gpu_option()
        if cfg.get("tab") in (TAB_COMPRESS, TAB_GIF, TAB_IMAGE, TAB_AUDIO,
                              TAB_DOWNLOAD):
            self.tab_seg.set(cfg["tab"])
        if cfg.get("mode") in (MODE_QUALITY, MODE_TARGET, MODE_SPLIT):
            self.mode_seg.set(cfg["mode"])
        set_menu(self.codec_menu, "codec")
        set_menu(self.hw_menu, "hardware")
        set_menu(self.res_menu, "resolution")
        set_menu(self.fps_menu, "fps")
        set_menu(self.parts_menu, "parts")
        set_menu(self.dither_menu, "dither")
        set_menu(self.img_format_menu, "img_format")
        set_menu(self.img_quality_menu, "img_quality")
        set_menu(self.img_resize_menu, "img_resize")
        set_menu(self.aud_format_menu, "aud_format")
        set_menu(self.aud_quality_menu, "aud_quality")
        set_menu(self.dl_res_menu, "dl_resolution")
        if cfg.get("dl_audio"):
            self.dl_audio_check.select()
        if cfg.get("size"):
            self.target_entry.delete(0, "end")
            self.target_entry.insert(0, str(cfg["size"]))
        if cfg.get("outdir"):
            self.outdir_entry.insert(0, cfg["outdir"])
        if cfg.get("geometry"):  # restore last window size/position (minsize clamps it)
            try:
                self.geometry(cfg["geometry"])
            except tk.TclError:
                pass
        self._on_codec_change()
        self._refresh_mode()

    def _save_config(self):
        cfg = {
            "geometry": self.geometry(),
            "tab": self.tab_seg.get(),
            "mode": self.mode_seg.get(),
            "resolution": self.res_menu.get(),
            "fps": self.fps_menu.get(),
            "parts": self.parts_menu.get(),
            "size": self.target_entry.get().strip(),
            "outdir": self.outdir_entry.get().strip(),
            "codec": self.codec_menu.get(),
            "dither": self.dither_menu.get(),
            "img_format": self.img_format_menu.get(),
            "img_quality": self.img_quality_menu.get(),
            "img_resize": self.img_resize_menu.get(),
            "aud_format": self.aud_format_menu.get(),
            "aud_quality": self.aud_quality_menu.get(),
            "dl_resolution": self.dl_res_menu.get(),
            "dl_audio": bool(self.dl_audio_check.get()),
        }
        if self._gpu_ok is not None:
            cfg["nvenc_ok"] = self._gpu_ok
        if self.hw_menu is not None:
            cfg["hardware"] = self.hw_menu.get()
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except OSError:
            pass

    def _set_app_icon(self):
        icon = resource_path("laxy.ico")
        if os.path.exists(icon):
            try:
                self.iconbitmap(icon)
            except Exception:  # noqa: BLE001 - non-Windows or bad icon
                pass

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

    def _on_close(self):
        for ev in self._dl_cancels.values():  # stop in-flight downloads
            ev.set()
        self._save_config()
        self.destroy()


def _selftest(out_path):
    """Write the resolved ffmpeg/ffprobe paths and a version probe to a file.

    Lets us confirm a packaged build finds its bundled binaries even with no
    ffmpeg on PATH. The windowed .exe has no console, so results go to a file.
    """
    import json
    import subprocess
    from probe import FFMPEG, FFPROBE, NO_WINDOW

    result = {"frozen": getattr(sys, "frozen", False),
              "meipass": getattr(sys, "_MEIPASS", None),
              "ffmpeg": FFMPEG, "ffprobe": FFPROBE}
    for key, tool in (("ffmpeg_version", FFMPEG), ("ffprobe_version", FFPROBE)):
        try:
            r = subprocess.run([tool, "-version"], capture_output=True,
                               text=True, creationflags=NO_WINDOW)
            result[key] = (r.stdout or r.stderr).splitlines()[0] if (r.stdout or r.stderr) else "no output"
        except Exception as e:  # noqa: BLE001
            result[key] = f"ERROR: {e}"
    try:
        from tkinterdnd2 import TkinterDnD
        _r = TkinterDnD.Tk()
        _r.withdraw()
        result["tkdnd"] = getattr(_r, "TkdndVersion", "loaded")
        _r.destroy()
    except Exception as e:  # noqa: BLE001
        result["tkdnd"] = f"ERROR: {e}"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        _selftest(sys.argv[i + 1] if i + 1 < len(sys.argv) else "selftest.json")
        sys.exit(0)

    theme.load_brand_fonts(resource_path("fonts"))  # before Tk reads families
    theme.apply_theme()
    # A hidden plain-Tk root lets us read installed font families before we
    # build the UI, so the default font family is set for every widget.
    _probe_root = tk.Tk()
    _probe_root.withdraw()
    _fonts = theme.resolve_fonts(_probe_root)
    theme.set_default_font_family(_fonts["sans"])
    _probe_root.destroy()

    App(fonts=_fonts).mainloop()
