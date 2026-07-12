"""Settings state: which controls the current tab/mode shows, greying rules,
codec/hardware interplay, and snapshotting every widget into a settings dict."""

import os
from tkinter import filedialog

import theme
from models import (TAB_COMPRESS, TAB_GIF, TAB_IMAGE, TAB_AUDIO, TAB_DOWNLOAD,
                    MODE_QUALITY, MODE_TARGET, MODE_GIF,
                    MODE_IMAGE, MODE_AUDIO, MODE_DOWNLOAD, CODEC_OPTIONS,
                    HW_OPTIONS, GIF_DITHER_OPTIONS, GIF_FORMAT_OPTIONS,
                    GIF_SPEED_OPTIONS, GIF_DIRECTION_OPTIONS,
                    GIF_COLORS_OPTIONS, ROTATE_OPTIONS, SUBS_NONE, SUBS_AUTO,
                    SUBS_PICK, IMG_FORMAT_OPTIONS, IMG_QUALITY_OPTIONS,
                    IMG_RESIZE_OPTIONS, AUD_FORMAT_OPTIONS,
                    AUD_QUALITY_OPTIONS, RESOLUTIONS, FPS_OPTIONS,
                    AUDIO_OPTIONS)


class SettingsMixin:

    def _on_setting_changed(self, _value=None):
        self._update_note()

    def _on_gif_format_change(self, _value=None):
        # Palette options only exist for classic GIF output.
        fmt = dict(GIF_FORMAT_OPTIONS)[self.gif_format_menu.get()]
        state = "normal" if fmt == "gif" else "disabled"
        self.dither_menu.configure(state=state)
        self.gif_colors_menu.configure(state=state)
        self._update_note()

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
        if self.hw_menu is not None and self._gpu_ok is not False:
            widgets += [self.hw_menu, self.hw_menu_label]
        return widgets

    def _compress_advanced_widgets(self):
        """Compress controls tucked behind the Advanced toggle (fps is handled
        separately: it stays visible on the GIF tab where it matters most)."""
        return [self.preset_menu, self.preset_menu_label,
                self.rotate_menu, self.rotate_menu_label,
                self.subs_menu, self.subs_menu_label]

    def _toggle_advanced(self):
        self._advanced_open = not self._advanced_open
        self.advanced_btn.configure(
            text="Advanced ▴" if self._advanced_open else "Advanced ▾")
        self._refresh_mode()

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

    # ---------- helpers ----------
    def _collect_settings(self) -> dict:
        audio_mode, audio_bitrate = dict(AUDIO_OPTIONS)[self.audio_menu.get()]
        subs_mode, subs_path = self._subs_settings()
        return {
            "codec": self._codec_value(),
            "encoder": self._hw_value(),  # "cpu" or "nvenc"
            "crf": int(self.crf_slider.get()),
            "preset": self.preset_menu.get(),
            "target_height": dict(RESOLUTIONS)[self.res_menu.get()],
            "target_fps": dict(FPS_OPTIONS)[self.fps_menu.get()],
            "audio_mode": audio_mode,
            "audio_bitrate": audio_bitrate or "128k",
            "rotate": dict(ROTATE_OPTIONS)[self.rotate_menu.get()],
            "subs_mode": subs_mode,
            "subs_path": subs_path,
            "gif_dither": dict(GIF_DITHER_OPTIONS)[self.dither_menu.get()],
            "gif_format": dict(GIF_FORMAT_OPTIONS)[self.gif_format_menu.get()],
            "gif_speed": dict(GIF_SPEED_OPTIONS)[self.gif_speed_menu.get()],
            "gif_direction": dict(GIF_DIRECTION_OPTIONS)[self.gif_direction_menu.get()],
            "gif_colors": dict(GIF_COLORS_OPTIONS)[self.gif_colors_menu.get()],
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
        nvenc = self._hw_value() == "nvenc"
        self.codec_menu.configure(state="disabled" if off else "normal")
        if self.hw_menu is not None:
            self.hw_menu.configure(state="disabled" if off else "normal")
        self.preset_menu.configure(state="disabled" if (off or nvenc) else "normal")
        self.audio_menu.configure(state="disabled" if off else "normal")
        self.res_menu.configure(state="disabled" if cut else "normal")
        self.fps_menu.configure(state="disabled" if cut else "normal")
        self.rotate_menu.configure(state="disabled" if cut else "normal")
        self.subs_menu.configure(state="disabled" if cut else "normal")
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
