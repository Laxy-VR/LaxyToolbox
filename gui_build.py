"""Constructing the window: header, toolbar, queue area, the settings card
for every tab, tooltips, and the short-screen scrollable fallback."""

import os

import customtkinter as ctk

import theme
from models import (APP_NAME, APP_VERSION, TAB_COMPRESS, TAB_GIF, TAB_IMAGE,
                    TAB_AUDIO, TAB_DOWNLOAD, MODE_QUALITY, MODE_TARGET,
                    MODE_SPLIT, DL_RES_OPTIONS, DL_COOKIES_OPTIONS,
                    PRESET_PLACEHOLDER, CODEC_OPTIONS, HW_OPTIONS,
                    GIF_DITHER_OPTIONS, GIF_FORMAT_OPTIONS, GIF_SPEED_OPTIONS,
                    GIF_DIRECTION_OPTIONS, GIF_COLORS_OPTIONS, ROTATE_OPTIONS,
                    SUBS_NONE, SUBS_AUTO, SUBS_PICK, IMG_FORMAT_OPTIONS,
                    IMG_QUALITY_OPTIONS, IMG_RESIZE_OPTIONS,
                    AUD_FORMAT_OPTIONS, AUD_QUALITY_OPTIONS, PARTS_OPTIONS,
                    PRESETS, RESOLUTIONS, FPS_OPTIONS, AUDIO_OPTIONS)
from probe import gpu_codecs
from sysutil import resource_path
from widgets import Tooltip


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
        ctk.CTkLabel(title_row, text=APP_NAME, text_color="#a99ce0",
                     font=self.f("heading", 22, "bold")).pack(side="left")
        self.version_label = ctk.CTkLabel(
            title_row, text=f"v{APP_VERSION}", text_color=theme.TEXT_MUTED,
            font=self.f("mono", 11))
        self.version_label.pack(side="left", padx=(10, 0), pady=(8, 0))
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
        ctk.CTkLabel(rows_, text="Scrub").pack(side="left")
        self._gif_slider_max = 60.0
        self.gif_slider = ctk.CTkSlider(rows_, from_=0, to=60, width=250,
                                        command=self._on_gif_slider)
        self.gif_slider.set(0)
        self.gif_slider.pack(side="left", padx=(10, 0))
        row0 = ctk.CTkFrame(gif_left, fg_color="transparent")
        row0.pack(anchor="w", pady=(6, 0))
        ctk.CTkLabel(row0, text="Clip start").pack(side="left")
        self.gif_start = ctk.CTkEntry(row0, width=70)
        self.gif_start.insert(0, "0")
        self.gif_start.bind("<KeyRelease>", lambda _e: self._on_gif_clip_edited())
        self.gif_start.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(row0, text="s   length").pack(side="left", padx=(4, 0))
        self.gif_len = ctk.CTkEntry(row0, width=70)
        self.gif_len.insert(0, "5")
        self.gif_len.bind("<KeyRelease>", lambda _e: self._on_gif_clip_edited())
        self.gif_len.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(row0, text="s", text_color=theme.TEXT_MUTED).pack(side="left", padx=(4, 0))
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
        self._menu_row(card, 9, 0, "Rotate", [r[0] for r in ROTATE_OPTIONS],
                       ROTATE_OPTIONS[0][0], "rotate_menu", self._on_setting_changed)
        # Burn-in subtitles: none, auto (same-named file), or a picked file.
        self._subs_path = None
        self._menu_row(card, 9, 2, "Subtitles", [SUBS_NONE, SUBS_AUTO, SUBS_PICK],
                       SUBS_NONE, "subs_menu", self._on_subs_change)

        # Optional trim (Compress tab only): encode just start..end seconds
        self.trim_frame = ctk.CTkFrame(card, fg_color="transparent")
        self.trim_frame.grid(row=10, column=0, columnspan=4, sticky="w",
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
        self.note_label.grid(row=11, column=0, columnspan=4, sticky="w",
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
            self.audio_menu: "Copy keeps the original audio untouched. Remove "
                             "audio strips the sound (handy for gameplay clips).",
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
            self.gif_speed_menu: "Play the clip faster or slower. 2x halves the "
                                 "length, which also halves the file size.",
            self.rotate_menu: "Fix a phone video recorded sideways, or mirror "
                              "the picture.",
            self.subs_menu: "Burn subtitles permanently into the picture. Auto "
                            "uses a subtitle file with the same name as each "
                            "video (clip.srt next to clip.mp4).",
            self.aud_normalize_check: "Evens out the volume: quiet recordings get "
                                      "louder, harsh peaks get tamed.",
            self.img_strip_check: "Removes hidden info like camera model, date, "
                                  "and GPS location. Good idea before sharing "
                                  "photos publicly.",
        }
        if self.hw_menu is not None:
            tips[self.hw_menu] = ("GPU is much faster. CPU gives slightly better "
                                  "quality per megabyte.")
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
