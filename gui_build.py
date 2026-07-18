"""Constructing the window: header, toolbar, queue area, the settings card
for every tab, tooltips, and the short-screen scrollable fallback.

Shared state contract: every widget attribute the other mixins read
(self.status, self.progress, the per tab menus and entries) is created here
in _build_ui, which App.__init__ calls exactly once before anything else
touches the UI. Renaming a widget attribute here means updating its users in
the other gui_*.py mixins; grep before renaming."""

import os

import customtkinter as ctk

import theme
from models import (APP_NAME, APP_VERSION, TAB_COMPRESS, TAB_GIF, TAB_IMAGE,
                    TAB_AUDIO, TAB_DOWNLOAD, MODE_QUALITY, MODE_TARGET,
                    MODE_SPLIT, DL_RES_OPTIONS, DL_COOKIES_OPTIONS,
                    PRESET_PLACEHOLDER, CODEC_OPTIONS, HW_OPTIONS,
                    GIF_DITHER_OPTIONS, GIF_FORMAT_OPTIONS, GIF_SPEED_OPTIONS,
                    GIF_DIRECTION_OPTIONS, GIF_COLORS_OPTIONS,
                    GIF_LOSSY_OPTIONS, GIF_SIZE_OPTIONS, ROTATE_OPTIONS,
                    CROP_OPTIONS,
                    SUBS_NONE, SUBS_AUTO, SUBS_PICK, IMG_FORMAT_OPTIONS,
                    IMG_QUALITY_OPTIONS, IMG_RESIZE_OPTIONS,
                    AUD_FORMAT_OPTIONS, AUD_QUALITY_OPTIONS, PARTS_OPTIONS,
                    PRESETS, RESOLUTIONS, FPS_OPTIONS, AUDIO_OPTIONS,
                    DENOISE_OPTIONS, AUDIO_TRACK_OPTIONS, SPEED_OPTIONS)
from probe import gpu_vendors, has_gifsicle
from sysutil import resource_path
from widgets import Tooltip, RangeSlider, QueueRow


class BuildMixin:

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
        ctk.CTkLabel(title_row, text=APP_NAME, text_color=theme.TITLE,
                     font=self.f("heading", 22, "bold")).pack(side="left")
        self.version_label = ctk.CTkLabel(
            title_row, text=f"v{APP_VERSION}", text_color=theme.TEXT_MUTED,
            font=self.f("mono", 11))
        self.version_label.pack(side="left", padx=(10, 0), pady=(8, 0))
        ctk.CTkButton(title_row, text="⚙", width=32, height=26,
                      fg_color="transparent", hover_color=theme.SURFACE2,
                      text_color=theme.TEXT_MUTED, font=self.f("sans", 15),
                      command=self._open_app_settings).pack(side="right")
        ctk.CTkLabel(header, text="Batch compression · video, GIF, images, and audio",
                     text_color=theme.TEXT_MUTED, font=self.f("sans", 12)).pack(anchor="w")

        # Bottom bar first (pinned): whatever happens to the middle section on
        # short screens, progress and the action buttons stay visible.
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(side="bottom", fill="x")
        self.progress = ctk.CTkProgressBar(bottom)
        self.progress.set(0)
        self.progress.pack(fill="x", padx=20, pady=(6, 4))
        self.status = ctk.CTkLabel(bottom, text="Ready.", anchor="w",
                                   text_color=theme.TEXT_MUTED, font=self.f("sans", 12))
        self.status.pack(fill="x", padx=20)
        actions = ctk.CTkFrame(bottom, fg_color="transparent")
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
        ctk.CTkButton(toolbar, text="Clear finished", command=self.on_clear_finished,
                      width=120, fg_color=theme.SURFACE2, hover_color=theme.BORDER,
                      text_color=theme.TEXT).pack(side="right", padx=8)
        ctk.CTkButton(toolbar, text="Open output folder", command=self.on_open_output,
                      width=150, fg_color=theme.SURFACE2, hover_color=theme.BORDER,
                      text_color=theme.TEXT).pack(side="right")

        # Middle section: queue + settings. Rebuilt inside a scrollable frame
        # by _make_middle_scrollable() when the screen is too short for it.
        self._middle = ctk.CTkFrame(self, fg_color="transparent")
        self._middle.pack(fill="both", expand=True)
        self._build_middle(self._middle)

    def _make_middle_scrollable(self):
        """Swap the middle section into a scrollable container (short screens)."""
        self._middle.destroy()
        self._middle = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._middle.pack(fill="both", expand=True)
        self._build_middle(self._middle, queue_height=90)

    def _widest_tab_reqwidth(self) -> int:
        """Required window width measured with the widest tab (GIF: three
        control rows plus the preview column) shown. The startup sizing uses
        this so no tab ever clips its right edge at the default size."""
        current = self.tab_seg.get()
        self.tab_seg.set(TAB_GIF)
        self._refresh_mode()
        self.update_idletasks()
        width = self.winfo_reqwidth()
        self.tab_seg.set(current)
        self._refresh_mode()
        self.update_idletasks()
        return width

    def _fit_window_height(self):
        """Grow the window when the layout needs more room (e.g. the Advanced
        section opened), capped to the working screen height. Never shrinks:
        whatever size the user dragged the window to is respected."""
        self.update_idletasks()
        needed = self.winfo_reqheight()
        if needed > self.winfo_height():
            usable = self.winfo_screenheight() - 80
            self.geometry(f"{self.winfo_width()}x{min(needed, usable)}")

    # ---------- live re-theming ----------
    def _apply_accent(self, name):
        """Switch the accent without restarting: re-apply the theme, rebuild
        the widgets in place, and put every setting and queue row back."""
        theme.apply_theme(name)
        self._rebuild_ui()

    def _rebuild_ui(self):
        """Tear down and reconstruct the whole UI, preserving session state.

        CustomTkinter widgets take their colors at creation, so a theme change
        means new widgets. State that lives outside widgets (self.jobs) is
        re-attached; state that lives inside them is snapshotted via the
        preset machinery, which round-trips every setting by design.
        """
        snapshot = self._collect_preset()
        outdir = self.outdir_entry.get()
        url = self.url_entry.get()
        playlist = bool(self.dl_playlist_check.get())
        advanced = self._advanced_open
        was_scrollable = isinstance(self._middle, ctk.CTkScrollableFrame)

        for job in self.jobs:  # rows die with the teardown; nothing may render
            job.row = None     # them until they're recreated below
        for w in self.winfo_children():
            w.destroy()
        self._settings_frame = None  # the panel died with the teardown
        self.configure(fg_color=theme.BG)
        self._build_ui()
        if was_scrollable:
            self._make_middle_scrollable()

        for job in self.jobs:  # re-attach the queue to the fresh queue_frame
            job.row = QueueRow(self.queue_frame, job, self._select_job,
                               self._remove_job, self._open_job,
                               self._context_menu, self._on_row_drag, self.fonts)
            job.row.pack(fill="x", padx=6, pady=4)
            job.row.render(selected=(job.id == self.selected_id))
            if job.thumb_png:
                job.row.set_thumbnail(job.thumb_png)
        if self.jobs:
            self.empty_label.pack_forget()

        self._advanced_open = advanced
        if advanced:
            self.advanced_btn.configure(text="Advanced ▴")
        self._apply_preset(snapshot)  # restores tab, mode, and every control
        self.outdir_entry.insert(0, outdir)
        self.url_entry.insert(0, url)
        if playlist:
            self.dl_playlist_check.select()
        self._refresh_hw_menu()  # drop vendors that failed their probe
        selected = self._selected_job()
        if selected is not None:
            self._select_job(selected)
        self._update_counts()

    def _build_middle(self, mid, queue_height=150):
        # Queue
        self.queue_frame = ctk.CTkScrollableFrame(mid, fg_color=theme.SURFACE,
                                                  corner_radius=12, height=queue_height)
        self.queue_frame.pack(fill="both", expand=True, padx=20, pady=(0, 8))
        self.empty_label = ctk.CTkLabel(
            self.queue_frame,
            text="Drag videos or images here, or use Add files / Add folder.",
            text_color=theme.TEXT_MUTED, font=self.f("sans", 13))
        self.empty_label.pack(pady=30)

        # Selected-file details
        self.detail_label = ctk.CTkLabel(mid, text="", anchor="w", justify="left",
                                         wraplength=640, text_color=theme.TEXT_MUTED,
                                         font=self.f("mono", 11))
        self.detail_label.pack(fill="x", padx=20, pady=(0, 6))

        # Top-level tab: compress to H.265, or make/shrink GIFs
        self.tab_seg = ctk.CTkSegmentedButton(
            mid, values=[TAB_COMPRESS, TAB_GIF, TAB_IMAGE, TAB_AUDIO, TAB_DOWNLOAD],
            command=self._on_tab_change,
            font=self.f("sans", 13, "bold"), height=34)
        self.tab_seg.set(TAB_COMPRESS)
        self.tab_seg.pack(fill="x", padx=20, pady=(0, 6))

        # Settings card (shared across the whole queue). Two menu columns keep
        # the card short enough to fit fully on a 1080p screen.
        card = ctk.CTkFrame(mid, fg_color=theme.SURFACE, corner_radius=12)
        card.pack(fill="x", padx=20, pady=(0, 8))
        card.grid_columnconfigure(1, weight=1)
        card.grid_columnconfigure(3, weight=1)

        header_bar = ctk.CTkFrame(card, fg_color="transparent")
        header_bar.grid(row=0, column=0, columnspan=4, sticky="ew", padx=14, pady=(12, 8))
        self.settings_title = ctk.CTkLabel(header_bar, text="Settings · applied to every file",
                                           font=self.f("sans", 13, "bold"))
        self.settings_title.pack(side="left")
        ctk.CTkButton(header_bar, text="Save preset", width=90, command=self._open_preset_dialog,
                      fg_color=theme.SURFACE2, hover_color=theme.BORDER,
                      text_color=theme.TEXT).pack(side="right")
        self.presets_menu = ctk.CTkOptionMenu(header_bar, width=170,
                                              values=self._preset_names(),
                                              command=self._on_preset_selected)
        self.presets_menu.set(PRESET_PLACEHOLDER)
        self.presets_menu.pack(side="right", padx=(0, 8))

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

        # GIF controls (shown only in Make GIF mode): scrubber, clip range,
        # look and motion options on the left; live previews of the clip's
        # first and last frames on the right.
        self.gif_frame = ctk.CTkFrame(card, fg_color="transparent")
        gif_left = ctk.CTkFrame(self.gif_frame, fg_color="transparent")
        gif_left.grid(row=0, column=0, sticky="nw")
        rows_ = ctk.CTkFrame(gif_left, fg_color="transparent")
        rows_.pack(anchor="w")
        ctk.CTkLabel(rows_, text="Clip").pack(side="left")
        self._gif_slider_max = 60.0
        self.gif_range = RangeSlider(rows_, width=280,
                                     command=self._on_gif_range)
        self.gif_range.set_values(0, 5)
        self.gif_range.pack(side="left", padx=(10, 0))
        row0 = ctk.CTkFrame(gif_left, fg_color="transparent")
        row0.pack(anchor="w", pady=(6, 0))
        ctk.CTkLabel(row0, text="Clip start").pack(side="left")
        self.gif_start = ctk.CTkEntry(row0, width=70)
        self.gif_start.insert(0, "0")
        self.gif_start.bind("<KeyRelease>", lambda _e: self._on_gif_clip_edited())
        self.gif_start.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(row0, text="length").pack(side="left", padx=(6, 0))
        self.gif_len = ctk.CTkEntry(row0, width=70)
        self.gif_len.insert(0, "5")
        self.gif_len.bind("<KeyRelease>", lambda _e: self._on_gif_clip_edited())
        self.gif_len.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(row0, text="s or mm:ss", text_color=theme.TEXT_MUTED).pack(
            side="left", padx=(6, 0))
        ctk.CTkLabel(row0, text="Size").pack(side="left", padx=(14, 0))
        self.gif_size_menu = ctk.CTkOptionMenu(
            row0, width=120, values=[s[0] for s in GIF_SIZE_OPTIONS],
            command=self._on_gif_size_change)
        self.gif_size_menu.set(GIF_SIZE_OPTIONS[0][0])
        self.gif_size_menu.pack(side="left", padx=(8, 0))
        # Exact pixel fields, revealed by the Custom… size choice. Leave one
        # blank to keep the shape; fill both for exact (possibly stretched).
        self.gif_w = ctk.CTkEntry(row0, width=52, placeholder_text="width")
        self.gif_w.bind("<KeyRelease>", lambda _e: self._on_setting_changed())
        self.gif_size_x = ctk.CTkLabel(row0, text="×", text_color=theme.TEXT_MUTED)
        self.gif_h = ctk.CTkEntry(row0, width=52, placeholder_text="height")
        self.gif_h.bind("<KeyRelease>", lambda _e: self._on_setting_changed())
        row1 = ctk.CTkFrame(gif_left, fg_color="transparent")
        row1.pack(anchor="w", pady=(8, 0))
        ctk.CTkLabel(row1, text="Dithering").pack(side="left")
        self.dither_menu = ctk.CTkOptionMenu(row1, width=190,
                                             values=[d[0] for d in GIF_DITHER_OPTIONS])
        self.dither_menu.set(GIF_DITHER_OPTIONS[0][0])
        self.dither_menu.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(row1, text="Colors").pack(side="left", padx=(12, 0))
        self.gif_colors_menu = ctk.CTkOptionMenu(
            row1, width=110, values=[c[0] for c in GIF_COLORS_OPTIONS],
            command=self._on_setting_changed)
        self.gif_colors_menu.set(GIF_COLORS_OPTIONS[0][0])
        self.gif_colors_menu.pack(side="left", padx=(8, 0))
        # Lossy compression needs gifsicle; the menu only appears when the
        # tool is available (always, in the packaged exe).
        self._gifsicle_ok = has_gifsicle()
        self.gif_lossy_menu = ctk.CTkOptionMenu(
            row1, width=90, values=[o[0] for o in GIF_LOSSY_OPTIONS],
            command=self._on_setting_changed)
        self.gif_lossy_menu.set(GIF_LOSSY_OPTIONS[0][0])
        if self._gifsicle_ok:
            ctk.CTkLabel(row1, text="Lossy").pack(side="left", padx=(12, 0))
            self.gif_lossy_menu.pack(side="left", padx=(8, 0))
        row2 = ctk.CTkFrame(gif_left, fg_color="transparent")
        row2.pack(anchor="w", pady=(8, 0))
        ctk.CTkLabel(row2, text="Save as").pack(side="left")
        self.gif_format_menu = ctk.CTkOptionMenu(
            row2, width=190, values=[f[0] for f in GIF_FORMAT_OPTIONS],
            command=self._on_gif_format_change)
        self.gif_format_menu.set(GIF_FORMAT_OPTIONS[0][0])
        self.gif_format_menu.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(row2, text="Speed").pack(side="left", padx=(12, 0))
        self.gif_speed_menu = ctk.CTkOptionMenu(
            row2, width=70, values=[s[0] for s in GIF_SPEED_OPTIONS],
            command=self._on_setting_changed)
        self.gif_speed_menu.set("1x")
        self.gif_speed_menu.pack(side="left", padx=(8, 0))
        self.gif_direction_menu = ctk.CTkOptionMenu(
            row2, width=110, values=[d[0] for d in GIF_DIRECTION_OPTIONS],
            command=self._on_setting_changed)
        self.gif_direction_menu.set(GIF_DIRECTION_OPTIONS[0][0])
        self.gif_direction_menu.pack(side="left", padx=(8, 0))
        self.gif_dedupe_check = ctk.CTkCheckBox(
            row2, text="Skip still frames", command=self._on_setting_changed)
        self.gif_dedupe_check.pack(side="left", padx=(14, 0))
        previews = ctk.CTkFrame(self.gif_frame, fg_color="transparent")
        previews.grid(row=0, column=1, sticky="ne", padx=(16, 0))
        self.gif_preview = ctk.CTkLabel(previews, text="start",
                                        text_color=theme.TEXT_MUTED,
                                        width=160, height=90, fg_color=theme.SURFACE2,
                                        corner_radius=8)
        self.gif_preview.pack()
        self.gif_preview_end = ctk.CTkLabel(previews, text="end",
                                            text_color=theme.TEXT_MUTED,
                                            width=160, height=90, fg_color=theme.SURFACE2,
                                            corner_radius=8)
        self.gif_preview_end.pack(pady=(6, 0))
        self._thumb_after = None   # debounce timer for preview refreshes
        self._thumb_tokens = {}    # preview label -> latest request token
        self._thumb_images = {}    # preview label -> CTkImage (kept from GC)

        # Image controls (shown only on the Images tab)
        self.image_frame = ctk.CTkFrame(card, fg_color="transparent")
        img_left = ctk.CTkFrame(self.image_frame, fg_color="transparent")
        img_left.grid(row=0, column=0, sticky="nw")
        for irow, (label, values, default, attr) in enumerate([
                ("Format", [o[0] for o in IMG_FORMAT_OPTIONS],
                 IMG_FORMAT_OPTIONS[0][0], "img_format_menu"),
                ("Quality", [o[0] for o in IMG_QUALITY_OPTIONS],
                 IMG_QUALITY_OPTIONS[1][0], "img_quality_menu"),
                ("Resize", [o[0] for o in IMG_RESIZE_OPTIONS],
                 IMG_RESIZE_OPTIONS[0][0], "img_resize_menu")]):
            ctk.CTkLabel(img_left, text=label, width=60, anchor="w").grid(
                row=irow, column=0, sticky="w", pady=3)
            menu = ctk.CTkOptionMenu(img_left, width=230, values=values,
                                     command=self._on_setting_changed)
            menu.set(default)
            menu.grid(row=irow, column=1, sticky="w", padx=(8, 0), pady=3)
            setattr(self, attr, menu)
        self.img_strip_check = ctk.CTkCheckBox(
            img_left, text="Strip metadata (EXIF, GPS)",
            command=self._on_setting_changed)
        self.img_strip_check.grid(row=3, column=0, columnspan=2, sticky="w",
                                  pady=(6, 0))
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
        ctk.CTkLabel(dlrow, text="Cookies").pack(side="left", padx=(20, 0))
        self.dl_cookies_menu = ctk.CTkOptionMenu(
            dlrow, width=110, values=[o[0] for o in DL_COOKIES_OPTIONS])
        self.dl_cookies_menu.set(DL_COOKIES_OPTIONS[0][0])
        self.dl_cookies_menu.pack(side="left", padx=(8, 0))
        # Toggles on their own row so nothing clips at the minimum width.
        dltoggles = ctk.CTkFrame(self.download_frame, fg_color="transparent")
        dltoggles.grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.dl_audio_check = ctk.CTkCheckBox(dltoggles, text="Audio only (MP3)")
        self.dl_audio_check.pack(side="left")
        self.dl_playlist_check = ctk.CTkCheckBox(dltoggles, text="Whole playlist")
        self.dl_playlist_check.pack(side="left", padx=(24, 0))

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
        self.aud_normalize_check = ctk.CTkCheckBox(
            self.audio_frame, text="Normalize volume",
            command=self._on_setting_changed)
        self.aud_normalize_check.grid(row=2, column=0, columnspan=2, sticky="w",
                                      pady=(6, 0))

        # Codec + hardware selectors, side by side. The hardware menu only
        # exists when this ffmpeg build has any GPU encoder at all; vendors
        # that fail their real test encode are dropped by _refresh_hw_menu.
        self._gpu_codecs = gpu_vendors()  # vendor -> set of codecs in build
        self._menu_row(card, 3, 0, "Codec", [c[0] for c in CODEC_OPTIONS],
                       CODEC_OPTIONS[0][0], "codec_menu", self._on_codec_change)
        if self._gpu_codecs:
            self._menu_row(card, 3, 2, "Hardware",
                           [label for label, v in HW_OPTIONS
                            if v == "cpu" or v in self._gpu_codecs],
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

        # Essentials row: the settings almost everyone touches.
        self._menu_row(card, 7, 0, "Audio", [a[0] for a in AUDIO_OPTIONS],
                       AUDIO_OPTIONS[2][0], "audio_menu", self._on_setting_changed)
        self._menu_row(card, 7, 2, "Resolution", [r[0] for r in RESOLUTIONS],
                       RESOLUTIONS[0][0], "res_menu", self._on_setting_changed)

        # Everything below the toggle is hidden until Advanced is opened.
        self._advanced_open = False
        self.advanced_btn = ctk.CTkButton(
            card, text="Advanced ▾", width=110, height=24,
            fg_color="transparent", hover_color=theme.SURFACE2,
            text_color=theme.TEXT_MUTED, command=self._toggle_advanced)
        self.advanced_btn.grid(row=8, column=0, sticky="w", padx=10, pady=(2, 0))
        self._menu_row(card, 9, 0, "Preset (speed)", PRESETS, "slow", "preset_menu")
        self._menu_row(card, 9, 2, "Frame rate", [f[0] for f in FPS_OPTIONS],
                       FPS_OPTIONS[0][0], "fps_menu", self._on_setting_changed)
        self._menu_row(card, 10, 0, "Rotate", [r[0] for r in ROTATE_OPTIONS],
                       ROTATE_OPTIONS[0][0], "rotate_menu", self._on_setting_changed)
        # Burn-in subtitles: none, auto (same-named file), or a picked file.
        self._subs_path = None
        self._menu_row(card, 10, 2, "Subtitles", [SUBS_NONE, SUBS_AUTO, SUBS_PICK],
                       SUBS_NONE, "subs_menu", self._on_subs_change)
        self._menu_row(card, 11, 0, "Crop", [c[0] for c in CROP_OPTIONS],
                       CROP_OPTIONS[0][0], "crop_menu", self._on_setting_changed)
        self._menu_row(card, 11, 2, "Denoise", [d[0] for d in DENOISE_OPTIONS],
                       DENOISE_OPTIONS[0][0], "denoise_menu", self._on_setting_changed)
        # Only shown when a queued file has more than one audio track.
        self._menu_row(card, 12, 0, "Audio track",
                       [t[0] for t in AUDIO_TRACK_OPTIONS],
                       AUDIO_TRACK_OPTIONS[0][0], "track_menu",
                       self._on_setting_changed)
        self._menu_row(card, 12, 2, "Speed", [s[0] for s in SPEED_OPTIONS],
                       SPEED_OPTIONS[0][0], "speed_menu", self._on_setting_changed)
        # Encode 5 seconds from the middle with the current settings, to
        # judge quality before committing to a long encode.
        self.sample_btn = ctk.CTkButton(
            card, text="Test a 5s sample", width=130, height=24,
            fg_color="transparent", hover_color=theme.SURFACE2,
            text_color=theme.TEXT_MUTED, command=self.on_sample)
        self.sample_btn.grid(row=8, column=3, sticky="e", padx=14, pady=(2, 0))

        # Optional trim (Compress tab only): encode just start..end seconds
        self.trim_frame = ctk.CTkFrame(card, fg_color="transparent")
        self.trim_frame.grid(row=13, column=0, columnspan=4, sticky="w",
                             padx=14, pady=(2, 0))
        trim_left = ctk.CTkFrame(self.trim_frame, fg_color="transparent")
        trim_left.grid(row=0, column=0, sticky="nw")
        trow = ctk.CTkFrame(trim_left, fg_color="transparent")
        trow.pack(anchor="w")
        ctk.CTkLabel(trow, text="Trim").pack(side="left")
        self.trim_start = ctk.CTkEntry(trow, width=70,
                                       placeholder_text="start")
        self.trim_start.bind("<KeyRelease>", lambda _e: self._on_trim_edited())
        self.trim_start.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(trow, text="to").pack(side="left", padx=(6, 0))
        self.trim_end = ctk.CTkEntry(trow, width=70,
                                     placeholder_text="end")
        self.trim_end.bind("<KeyRelease>", lambda _e: self._on_trim_edited())
        self.trim_end.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(trow, text="s or mm:ss", text_color=theme.TEXT_MUTED).pack(
            side="left", padx=(6, 0))
        self.cut_only_check = ctk.CTkCheckBox(
            trow, text="Cut only (no re-encode)",
            command=self._on_cut_only_toggle)
        self.cut_only_check.pack(side="left", padx=(20, 0))
        self.trim_range = RangeSlider(trim_left, width=480,
                                      command=self._on_trim_range)
        self.trim_range.pack(anchor="w", padx=(42, 0), pady=(4, 0))
        # Small start/end frame previews so you can see what you're cutting.
        tprev = ctk.CTkFrame(self.trim_frame, fg_color="transparent")
        tprev.grid(row=0, column=1, sticky="n", padx=(14, 0))
        self.trim_preview = ctk.CTkLabel(tprev, text="start",
                                         text_color=theme.TEXT_MUTED,
                                         width=110, height=62,
                                         fg_color=theme.SURFACE2, corner_radius=8)
        self.trim_preview.pack(side="left")
        self.trim_preview_end = ctk.CTkLabel(tprev, text="end",
                                             text_color=theme.TEXT_MUTED,
                                             width=110, height=62,
                                             fg_color=theme.SURFACE2, corner_radius=8)
        self.trim_preview_end.pack(side="left", padx=(8, 0))

        self.note_label = ctk.CTkLabel(card, text="", anchor="w", justify="left",
                                       wraplength=620, text_color=theme.NOTE,
                                       font=self.f("sans", 12))
        self.note_label.grid(row=14, column=0, columnspan=4, sticky="w",
                             padx=14, pady=(4, 12))

        # Output folder
        out = ctk.CTkFrame(mid, fg_color="transparent")
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

        self._refresh_mode()  # sync all mode-dependent UI now that widgets exist
        self._add_tooltips()

    def _add_tooltips(self):
        """Plain-language hints for the settings a newcomer won't know."""
        fam = self.fonts["sans"]
        tips = {
            self.codec_menu: "H.265 is the best all-round choice. AV1 makes the "
                             "smallest files but needs a fairly modern device to "
                             "play. H.264 plays on everything but is the largest.",
            self.crf_slider: "Quality knob. Lower = better looking and bigger; "
                             "higher = smaller and softer. ~18 is near lossless, "
                             "~28 is small.",
            self.preset_menu: "Encoding speed. Slower squeezes the file a little "
                              "smaller at the same quality, but takes longer.",
            self.audio_menu: "Copy keeps the original audio untouched. Boost "
                             "quiet audio brings a too-quiet mic up to a normal "
                             "level. Remove audio strips the sound entirely.",
            self.res_menu: "Downscale the video. Keep original unless you want a "
                           "smaller frame size.",
            self.dl_cookies_menu: "If a site only gives low quality or needs a "
                                  "login, pick a browser you're signed into and "
                                  "it downloads as that browser.",
            self.cut_only_check: "Trim without re-encoding: instant and lossless, "
                                 "but cut points snap to keyframes (may start a "
                                 "second or two early).",
            self.dl_playlist_check: "Download every video the link points to, not "
                                    "just one. Each video is added to the queue as "
                                    "it finishes.",
            self.gif_format_menu: "WebP is far smaller than GIF at better quality "
                                  "and works on Discord and every modern app. MP4 "
                                  "loop is the smallest of all.",
            self.gif_direction_menu: "Boomerang plays forward then backward. Keep "
                                     "clips short: Reverse and Boomerang hold the "
                                     "whole clip in memory while encoding.",
            self.gif_colors_menu: "Fewer colors make a clearly smaller GIF. Works "
                                  "best on screen recordings and simple footage.",
            self.gif_lossy_menu: "Lossy compression (gifsicle): usually 30-60% "
                                 "smaller for barely visible artifacts. Light is "
                                 "a safe default; Strong squeezes hardest.",
            self.gif_dedupe_check: "Drops frames where nothing moved (great for "
                                   "screen recordings). Timing is preserved, the "
                                   "file just skips the still parts.",
            self.gif_speed_menu: "Play the clip faster or slower. 2x halves the "
                                 "length, which also halves the file size.",
            self.gif_size_menu: "Output size. The Max choices cap the height "
                                "(width follows, never upscales); 480p is plenty "
                                "for chat. Custom lets you type exact pixels: "
                                "leave width or height blank to keep the shape.",
            self.rotate_menu: "Fix a phone video recorded sideways, or mirror "
                              "the picture.",
            self.crop_menu: "Remove black bars measures each file and cuts the "
                            "bars off automatically. Vertical 9:16 crops the "
                            "center for Shorts/TikTok; Square 1:1 for "
                            "thumbnails. Crops are centered.",
            self.subs_menu: "Burn subtitles permanently into the picture. Auto "
                            "uses a subtitle file with the same name as each "
                            "video (clip.srt next to clip.mp4).",
            self.aud_normalize_check: "Evens out the volume: quiet recordings get "
                                      "louder, harsh peaks get tamed.",
            self.img_strip_check: "Removes hidden info like camera model, date, "
                                  "and GPS location. Good idea before sharing "
                                  "photos publicly.",
            self.denoise_menu: "Softens film grain and sensor noise. Grainy "
                               "footage compresses far better with Light on, "
                               "so files also get smaller.",
            self.track_menu: "Files with several audio tracks (OBS: game and "
                             "mic separate): keep one track, or mix them all "
                             "into one.",
            self.sample_btn: "Encodes 5 seconds from the middle of the "
                             "selected video with the current settings and "
                             "opens it, so you can judge quality before a "
                             "long encode.",
            self.speed_menu: "Play the video faster (timelapse) or slower. "
                             "Audio is re-timed to match, and 2x roughly "
                             "halves the file size.",
        }
        if self.hw_menu is not None:
            tips[self.hw_menu] = ("GPU is much faster. CPU gives slightly better "
                                  "quality per megabyte. Only graphics cards "
                                  "that pass a real test encode are listed.")
        for widget, text in tips.items():
            try:
                Tooltip(widget, text, fam)
            except NotImplementedError:
                pass  # some CustomTkinter widgets don't support bind

    def _menu_row(self, parent, row, col, label, values, default, attr, command=None):
        lbl = ctk.CTkLabel(parent, text=label)
        lbl.grid(row=row, column=col, sticky="w", padx=(14, 4), pady=6)
        menu = ctk.CTkOptionMenu(parent, values=values, command=command)
        menu.set(default)
        menu.grid(row=row, column=col + 1, sticky="ew", padx=(0, 14), pady=6)
        setattr(self, attr, menu)
        setattr(self, attr + "_label", lbl)

    def _set_app_icon(self):
        icon = resource_path("laxy.ico")
        if os.path.exists(icon):
            try:
                self.iconbitmap(icon)
            except Exception:  # noqa: BLE001 - non-Windows or bad icon
                pass
