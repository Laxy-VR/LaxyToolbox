"""Settings state: which controls the current tab/mode shows, greying rules,
codec/hardware interplay, and snapshotting every widget into a settings dict.

Shared state contract: reads the settings widgets BuildMixin created;
_collect_settings is the one funnel from widget state to the plain dicts the
planner and encoder consume, so worker threads never touch a widget."""

import os
from tkinter import filedialog

import theme
from models import (TAB_COMPRESS, TAB_GIF, TAB_IMAGE, TAB_AUDIO, TAB_DOWNLOAD,
                    MODE_QUALITY, MODE_TARGET, MODE_GIF,
                    MODE_IMAGE, MODE_AUDIO, MODE_DOWNLOAD, CODEC_OPTIONS,
                    HW_OPTIONS, GIF_DITHER_OPTIONS, GIF_FORMAT_OPTIONS,
                    GIF_SPEED_OPTIONS, GIF_DIRECTION_OPTIONS,
                    GIF_COLORS_OPTIONS, GIF_LOSSY_OPTIONS, GIF_SIZE_OPTIONS,
                    GIF_SIZE_CUSTOM,
                    ROTATE_OPTIONS, CROP_OPTIONS, SUBS_NONE, SUBS_AUTO,
                    SUBS_PICK, IMG_FORMAT_OPTIONS, IMG_QUALITY_OPTIONS,
                    IMG_RESIZE_OPTIONS, AUD_FORMAT_OPTIONS,
                    AUD_QUALITY_OPTIONS, RESOLUTIONS, FPS_OPTIONS,
                    AUDIO_OPTIONS, DENOISE_OPTIONS, AUDIO_TRACK_OPTIONS,
                    SPEED_OPTIONS)


class SettingsMixin:

    def _on_setting_changed(self, _value=None):
        self._update_note()

    def _on_gif_format_change(self, _value=None):
        # Palette and lossy options only exist for classic GIF output.
        fmt = dict(GIF_FORMAT_OPTIONS)[self.gif_format_menu.get()]
        state = "normal" if fmt == "gif" else "disabled"
        self.dither_menu.configure(state=state)
        self.gif_colors_menu.configure(state=state)
        self.gif_lossy_menu.configure(state=state)
        self._update_note()

    def _on_gif_size_change(self, _value=None):
        # The Custom… choice reveals exact width x height fields.
        custom = dict(GIF_SIZE_OPTIONS)[self.gif_size_menu.get()] == GIF_SIZE_CUSTOM
        if custom:
            self.gif_w.pack(side="left", padx=(8, 0))
            self.gif_size_x.pack(side="left", padx=(4, 0))
            self.gif_h.pack(side="left", padx=(4, 0))
        else:
            for w in (self.gif_w, self.gif_size_x, self.gif_h):
                w.pack_forget()
        self._update_note()

    def _gif_custom_dims(self):
        """(width, height) typed into the Custom fields; a blank or invalid
        side is None. Only meaningful when the size menu says Custom…"""
        def px(entry):
            try:
                v = int(entry.get().strip())
            except (TypeError, ValueError):
                return None
            return v if 8 <= v <= 8192 else None
        return px(self.gif_w), px(self.gif_h)

    def _on_subs_change(self, value=None):
        if value == SUBS_PICK:
            path = filedialog.askopenfilename(
                title="Choose a subtitle file",
                filetypes=[("Subtitles", "*.srt *.ass *.vtt"),
                           ("All files", "*.*")])
            if path:
                self._subs_path = path
                self.subs_menu.set(os.path.basename(path))
            else:
                self._subs_path = None
                self.subs_menu.set(SUBS_NONE)
        elif value in (SUBS_NONE, SUBS_AUTO):
            self._subs_path = None
        self._update_note()

    def _subs_settings(self):
        """(subs_mode, subs_path) from the menu: 'none' | 'auto' | 'file'."""
        v = self.subs_menu.get()
        if v == SUBS_AUTO:
            return "auto", None
        if v not in (SUBS_NONE, SUBS_PICK) and self._subs_path:
            return "file", self._subs_path  # menu shows the chosen file name
        return "none", None

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

    def _compress_essential_widgets(self):
        """Always-visible Compress controls (hidden on the other tabs)."""
        widgets = [self.crf_caption, self.crf_value, self.crf_slider, self.crf_hint,
                   self.audio_menu, self.audio_menu_label,
                   self.codec_menu, self.codec_menu_label,
                   self.res_menu, self.res_menu_label]
        if self.hw_menu is not None and len(self._available_hw()) > 1:
            widgets += [self.hw_menu, self.hw_menu_label]
        return widgets

    def _compress_advanced_widgets(self):
        """Compress controls tucked behind the Advanced toggle (fps is handled
        separately: it stays visible on the GIF tab where it matters most).
        The audio track menu only appears when a queued file actually has
        more than one audio track, so it never clutters the common case."""
        widgets = [self.preset_menu, self.preset_menu_label,
                   self.rotate_menu, self.rotate_menu_label,
                   self.subs_menu, self.subs_menu_label,
                   self.crop_menu, self.crop_menu_label,
                   self.denoise_menu, self.denoise_menu_label,
                   self.speed_menu, self.speed_menu_label,
                   self.sample_btn]
        if any(j.info and j.info.audio_tracks > 1 for j in self.jobs):
            widgets += [self.track_menu, self.track_menu_label]
        return widgets

    def _toggle_advanced(self):
        self._advanced_open = not self._advanced_open
        self.advanced_btn.configure(
            text="Advanced ▴" if self._advanced_open else "Advanced ▾")
        self._refresh_mode()
        if self._advanced_open:  # the extra rows must not clip the controls
            self._fit_window_height()

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
        adv = compress and self._advanced_open
        show([self.mode_seg, self.trim_frame], compress)
        show(self._compress_essential_widgets(), compress)
        show([self.advanced_btn], compress)
        show(self._compress_advanced_widgets(), adv)
        show([self.fps_menu, self.fps_menu_label], adv or tab == TAB_GIF)
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

    def _available_hw(self):
        """(label, value) hardware choices usable right now: CPU plus every
        GPU vendor whose encoders exist in this ffmpeg build and that has not
        failed its real test encode probe."""
        opts = [HW_OPTIONS[0]]
        for label, vendor in HW_OPTIONS[1:]:
            if vendor in self._gpu_codecs and self._gpu_ok.get(vendor) is not False:
                opts.append((label, vendor))
        return opts

    def _hw_value(self):
        """The settings encoder value ('cpu', 'nvenc', 'amf', 'qsv'), falling
        back to cpu when the chosen vendor failed its probe or cannot
        hardware-encode the current codec."""
        if self.hw_menu is None:
            return "cpu"
        hw = dict(HW_OPTIONS).get(self.hw_menu.get(), "cpu")
        if hw != "cpu" and (self._gpu_ok.get(hw) is False
                            or self._codec_value() not in self._gpu_codecs.get(hw, ())):
            return "cpu"
        return hw

    def _on_codec_change(self, _value=None):
        gpu = self._hw_value() != "cpu"
        self.crf_caption.configure(text="Quality (CQ)" if gpu else "Quality (CRF)")
        # Offer each GPU vendor only for codecs it can hardware-encode here.
        if self.hw_menu is not None:
            codec = self._codec_value()
            values = [label for label, v in self._available_hw()
                      if v == "cpu" or codec in self._gpu_codecs.get(v, ())]
            current = self.hw_menu.get()
            self.hw_menu.configure(values=values)
            if current not in values:
                self.hw_menu.set(values[0])
        self._sync_controls()
        self._update_note()

    def _refresh_hw_menu(self):
        """Re-sync the Hardware menu after a probe verdict arrives: drop the
        failed vendor, and hide the menu entirely when only CPU remains."""
        if self.hw_menu is None:
            return
        if len(self._available_hw()) > 1:
            self._on_codec_change()
            return
        self.hw_menu.set(HW_OPTIONS[0][0])
        self.hw_menu.grid_remove()
        self.hw_menu_label.grid_remove()
        self.crf_caption.configure(text="Quality (CRF)")
        self._sync_controls()

    # ---------- helpers ----------
    def _collect_settings(self) -> dict:
        audio_mode, audio_bitrate = dict(AUDIO_OPTIONS)[self.audio_menu.get()]
        subs_mode, subs_path = self._subs_settings()
        gif_size = dict(GIF_SIZE_OPTIONS)[self.gif_size_menu.get()]
        return {
            "codec": self._codec_value(),
            "encoder": self._hw_value(),  # "cpu" | "nvenc" | "amf" | "qsv"
            "denoise": dict(DENOISE_OPTIONS)[self.denoise_menu.get()],
            "audio_track": dict(AUDIO_TRACK_OPTIONS)[self.track_menu.get()],
            "speed": dict(SPEED_OPTIONS)[self.speed_menu.get()],
            "crf": int(self.crf_slider.get()),
            "preset": self.preset_menu.get(),
            "target_height": dict(RESOLUTIONS)[self.res_menu.get()],
            "target_fps": dict(FPS_OPTIONS)[self.fps_menu.get()],
            "audio_mode": audio_mode,
            "audio_bitrate": audio_bitrate or "128k",
            "rotate": dict(ROTATE_OPTIONS)[self.rotate_menu.get()],
            "crop": dict(CROP_OPTIONS)[self.crop_menu.get()],
            "subs_mode": subs_mode,
            "subs_path": subs_path,
            "gif_dither": dict(GIF_DITHER_OPTIONS)[self.dither_menu.get()],
            "gif_format": dict(GIF_FORMAT_OPTIONS)[self.gif_format_menu.get()],
            "gif_speed": dict(GIF_SPEED_OPTIONS)[self.gif_speed_menu.get()],
            "gif_direction": dict(GIF_DIRECTION_OPTIONS)[self.gif_direction_menu.get()],
            "gif_colors": dict(GIF_COLORS_OPTIONS)[self.gif_colors_menu.get()],
            "gif_lossy": dict(GIF_LOSSY_OPTIONS)[self.gif_lossy_menu.get()],
            "gif_height": gif_size if gif_size != GIF_SIZE_CUSTOM else None,
            "gif_custom": (self._gif_custom_dims()
                           if gif_size == GIF_SIZE_CUSTOM else None),
            "gif_dedupe": bool(self.gif_dedupe_check.get()),
            "img_format": dict(IMG_FORMAT_OPTIONS)[self.img_format_menu.get()],
            "img_quality": dict(IMG_QUALITY_OPTIONS)[self.img_quality_menu.get()],
            "img_resize": dict(IMG_RESIZE_OPTIONS)[self.img_resize_menu.get()],
            "img_strip": bool(self.img_strip_check.get()),
            "aud_format": dict(AUD_FORMAT_OPTIONS)[self.aud_format_menu.get()],
            "aud_bitrate": dict(AUD_QUALITY_OPTIONS)[self.aud_quality_menu.get()],
            "aud_normalize": bool(self.aud_normalize_check.get()),
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
        gpu = self._hw_value() != "cpu"
        self.codec_menu.configure(state="disabled" if off else "normal")
        if self.hw_menu is not None:
            self.hw_menu.configure(state="disabled" if off else "normal")
        self.preset_menu.configure(state="disabled" if (off or gpu) else "normal")
        self.audio_menu.configure(state="disabled" if off else "normal")
        self.res_menu.configure(state="disabled" if cut else "normal")
        self.fps_menu.configure(state="disabled" if cut else "normal")
        self.rotate_menu.configure(state="disabled" if cut else "normal")
        self.subs_menu.configure(state="disabled" if cut else "normal")
        self.crop_menu.configure(state="disabled" if cut else "normal")
        self.denoise_menu.configure(state="disabled" if cut else "normal")
        self.track_menu.configure(state="disabled" if cut else "normal")
        self.speed_menu.configure(state="disabled" if cut else "normal")
        self.sample_btn.configure(state="disabled" if cut else "normal")
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
