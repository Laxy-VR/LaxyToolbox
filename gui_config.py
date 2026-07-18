"""Persistence: the config file (last-used settings, window geometry, the
cached GPU verdict) and named setting presets, built-in and user-saved.

Shared state contract: reads and writes the widgets BuildMixin created and
the core state App.__init__ owns (_user_presets, _gpu_ok, _advanced_open);
_load_config must run after _build_ui so every widget it restores exists."""

import json
import tkinter as tk

import customtkinter as ctk

import theme
from models import (APP_NAME, APP_VERSION, CONFIG_PATH, GITHUB_REPO,
                    TAB_COMPRESS, TAB_GIF, TAB_IMAGE, TAB_AUDIO,
                    TAB_DOWNLOAD, MODE_QUALITY, MODE_TARGET, MODE_SPLIT,
                    BUILTIN_PRESETS, PRESET_PLACEHOLDER)


class ConfigMixin:

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

        ok = cfg.get("gpu_ok")
        if isinstance(ok, dict):
            self._gpu_ok = {k: v for k, v in ok.items() if isinstance(v, bool)}
        elif isinstance(cfg.get("nvenc_ok"), bool):  # pre-1.6 single GPU cache
            self._gpu_ok = {"nvenc": cfg["nvenc_ok"]}
        self._refresh_hw_menu()
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
        set_menu(self.rotate_menu, "rotate")
        set_menu(self.crop_menu, "crop")
        set_menu(self.denoise_menu, "denoise")
        set_menu(self.track_menu, "audio_track")
        set_menu(self.speed_menu, "speed")
        set_menu(self.dither_menu, "dither")
        set_menu(self.gif_format_menu, "gif_format")
        set_menu(self.gif_speed_menu, "gif_speed")
        set_menu(self.gif_direction_menu, "gif_direction")
        set_menu(self.gif_colors_menu, "gif_colors")
        set_menu(self.gif_lossy_menu, "gif_lossy")
        set_menu(self.gif_size_menu, "gif_size")
        if cfg.get("gif_custom_w"):
            self.gif_w.insert(0, str(cfg["gif_custom_w"]))
        if cfg.get("gif_custom_h"):
            self.gif_h.insert(0, str(cfg["gif_custom_h"]))
        if cfg.get("gif_dedupe"):
            self.gif_dedupe_check.select()
        set_menu(self.img_format_menu, "img_format")
        set_menu(self.img_quality_menu, "img_quality")
        set_menu(self.img_resize_menu, "img_resize")
        set_menu(self.img_max_menu, "img_max")
        set_menu(self.img_rotate_menu, "img_rotate")
        set_menu(self.aud_format_menu, "aud_format")
        set_menu(self.aud_quality_menu, "aud_quality")
        set_menu(self.aud_speed_menu, "aud_speed")
        set_menu(self.aud_track_menu, "aud_track")
        set_menu(self.dl_res_menu, "dl_resolution")
        set_menu(self.dl_cookies_menu, "dl_cookies")
        if cfg.get("dl_audio"):
            self.dl_audio_check.select()
        if cfg.get("aud_normalize"):
            self.aud_normalize_check.select()
        if cfg.get("img_strip"):
            self.img_strip_check.select()
        if cfg.get("advanced"):  # reopen the Advanced section if it was open
            self._advanced_open = True
            self.advanced_btn.configure(text="Advanced ▴")
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
        if isinstance(cfg.get("presets"), dict):
            self._user_presets = {str(k): v for k, v in cfg["presets"].items()
                                  if isinstance(v, dict)}
            self._refresh_preset_menu()
        self._on_codec_change()
        self._on_gif_format_change()  # sync dither/colors enabled state
        self._on_gif_size_change()    # show the custom fields if restored
        self._refresh_mode()
        if self._advanced_open:  # restored open: make sure nothing clips
            self._fit_window_height()

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
            "rotate": self.rotate_menu.get(),
            "crop": self.crop_menu.get(),
            "denoise": self.denoise_menu.get(),
            "audio_track": self.track_menu.get(),
            "speed": self.speed_menu.get(),
            "dither": self.dither_menu.get(),
            "gif_format": self.gif_format_menu.get(),
            "gif_speed": self.gif_speed_menu.get(),
            "gif_direction": self.gif_direction_menu.get(),
            "gif_colors": self.gif_colors_menu.get(),
            "gif_lossy": self.gif_lossy_menu.get(),
            "gif_size": self.gif_size_menu.get(),
            "gif_custom_w": self.gif_w.get().strip(),
            "gif_custom_h": self.gif_h.get().strip(),
            "gif_dedupe": bool(self.gif_dedupe_check.get()),
            "img_format": self.img_format_menu.get(),
            "img_quality": self.img_quality_menu.get(),
            "img_resize": self.img_resize_menu.get(),
            "img_max": self.img_max_menu.get(),
            "img_rotate": self.img_rotate_menu.get(),
            "img_strip": bool(self.img_strip_check.get()),
            "aud_format": self.aud_format_menu.get(),
            "aud_quality": self.aud_quality_menu.get(),
            "aud_speed": self.aud_speed_menu.get(),
            "aud_track": self.aud_track_menu.get(),
            "aud_normalize": bool(self.aud_normalize_check.get()),
            "dl_resolution": self.dl_res_menu.get(),
            "dl_cookies": self.dl_cookies_menu.get(),
            "dl_audio": bool(self.dl_audio_check.get()),
            "advanced": self._advanced_open,
            "accent": theme.ACCENT_NAME,
        }
        if self._gpu_ok:
            cfg["gpu_ok"] = self._gpu_ok
        if self.hw_menu is not None:
            cfg["hardware"] = self.hw_menu.get()
        if self._user_presets:
            cfg["presets"] = self._user_presets
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except OSError:
            pass

    # ---------- setting presets ----------
    def _preset_names(self):
        return ([PRESET_PLACEHOLDER] + list(BUILTIN_PRESETS)
                + list(self._user_presets))

    def _refresh_preset_menu(self):
        self.presets_menu.configure(values=self._preset_names())
        self.presets_menu.set(PRESET_PLACEHOLDER)

    def _collect_preset(self) -> dict:
        """A full snapshot of the current settings for a user preset."""
        d = {
            "tab": self.tab_seg.get(), "mode": self.mode_seg.get(),
            "codec": self.codec_menu.get(), "crf": int(self.crf_slider.get()),
            "preset": self.preset_menu.get(), "resolution": self.res_menu.get(),
            "fps": self.fps_menu.get(), "parts": self.parts_menu.get(),
            "audio": self.audio_menu.get(), "dither": self.dither_menu.get(),
            "rotate": self.rotate_menu.get(), "crop": self.crop_menu.get(),
            "denoise": self.denoise_menu.get(),
            "audio_track": self.track_menu.get(),
            "speed": self.speed_menu.get(),
            "gif_format": self.gif_format_menu.get(),
            "gif_speed": self.gif_speed_menu.get(),
            "gif_direction": self.gif_direction_menu.get(),
            "gif_colors": self.gif_colors_menu.get(),
            "gif_lossy": self.gif_lossy_menu.get(),
            "gif_size": self.gif_size_menu.get(),
            "gif_custom_w": self.gif_w.get().strip(),
            "gif_custom_h": self.gif_h.get().strip(),
            "gif_dedupe": bool(self.gif_dedupe_check.get()),
            "img_strip": bool(self.img_strip_check.get()),
            "aud_normalize": bool(self.aud_normalize_check.get()),
            "img_format": self.img_format_menu.get(),
            "img_quality": self.img_quality_menu.get(),
            "img_resize": self.img_resize_menu.get(),
            "img_max": self.img_max_menu.get(),
            "img_rotate": self.img_rotate_menu.get(),
            "aud_format": self.aud_format_menu.get(),
            "aud_quality": self.aud_quality_menu.get(),
            "aud_speed": self.aud_speed_menu.get(),
            "aud_track": self.aud_track_menu.get(),
            "dl_resolution": self.dl_res_menu.get(),
            "dl_cookies": self.dl_cookies_menu.get(),
            "dl_audio": bool(self.dl_audio_check.get()),
            "size": self.target_entry.get().strip(),
            "trim_start": self.trim_start.get().strip(),
            "trim_end": self.trim_end.get().strip(),
            "cut_only": bool(self.cut_only_check.get()),
            "gif_start": self.gif_start.get().strip(),
            "gif_len": self.gif_len.get().strip(),
        }
        if self.hw_menu is not None:
            d["hardware"] = self.hw_menu.get()
        return d

    def _apply_preset(self, d: dict):
        """Apply a preset dict; only keys it lists are touched, and only when
        their value is valid on this machine (unknown values are skipped)."""
        def menu(widget, key):
            if widget is not None and key in d and d[key] in widget.cget("values"):
                widget.set(d[key])

        def entry(widget, key):
            if key in d:
                widget.delete(0, "end")
                widget.insert(0, str(d[key]))

        def check(widget, key):
            if key in d:
                widget.select() if d[key] else widget.deselect()

        # Tab first so the right widgets exist, then everything else.
        if d.get("tab") in self.tab_seg.cget("values"):
            self.tab_seg.set(d["tab"])
        if d.get("mode") in self.mode_seg.cget("values"):
            self.mode_seg.set(d["mode"])
        menu(self.codec_menu, "codec")
        menu(self.hw_menu, "hardware")
        menu(self.preset_menu, "preset")
        menu(self.res_menu, "resolution")
        menu(self.fps_menu, "fps")
        menu(self.parts_menu, "parts")
        menu(self.audio_menu, "audio")
        menu(self.dither_menu, "dither")
        menu(self.rotate_menu, "rotate")
        menu(self.crop_menu, "crop")
        menu(self.denoise_menu, "denoise")
        menu(self.track_menu, "audio_track")
        menu(self.speed_menu, "speed")
        menu(self.gif_format_menu, "gif_format")
        menu(self.gif_speed_menu, "gif_speed")
        menu(self.gif_direction_menu, "gif_direction")
        menu(self.gif_colors_menu, "gif_colors")
        menu(self.gif_lossy_menu, "gif_lossy")
        menu(self.gif_size_menu, "gif_size")
        entry(self.gif_w, "gif_custom_w")
        entry(self.gif_h, "gif_custom_h")
        check(self.gif_dedupe_check, "gif_dedupe")
        check(self.img_strip_check, "img_strip")
        check(self.aud_normalize_check, "aud_normalize")
        menu(self.img_format_menu, "img_format")
        menu(self.img_quality_menu, "img_quality")
        menu(self.img_resize_menu, "img_resize")
        menu(self.img_max_menu, "img_max")
        menu(self.img_rotate_menu, "img_rotate")
        menu(self.aud_format_menu, "aud_format")
        menu(self.aud_quality_menu, "aud_quality")
        menu(self.aud_speed_menu, "aud_speed")
        menu(self.aud_track_menu, "aud_track")
        menu(self.dl_res_menu, "dl_resolution")
        menu(self.dl_cookies_menu, "dl_cookies")
        check(self.dl_audio_check, "dl_audio")
        check(self.cut_only_check, "cut_only")
        entry(self.target_entry, "size")
        entry(self.trim_start, "trim_start")
        entry(self.trim_end, "trim_end")
        entry(self.gif_start, "gif_start")
        entry(self.gif_len, "gif_len")
        if "crf" in d:
            self.crf_slider.set(int(d["crf"]))
            self._on_crf(int(d["crf"]))
        self._on_codec_change()
        self._on_gif_format_change()
        self._on_gif_size_change()
        self._refresh_mode()

    def _on_preset_selected(self, name):
        if name == PRESET_PLACEHOLDER:
            return
        preset = BUILTIN_PRESETS.get(name) or self._user_presets.get(name)
        if preset:
            self._apply_preset(preset)
            self.status.configure(text=f"Applied preset: {name}")
        self.presets_menu.set(PRESET_PLACEHOLDER)  # act like a menu of actions

    def _open_preset_dialog(self):
        """Save the current settings as a named preset, and delete existing ones."""
        dlg = ctk.CTkToplevel(self)
        dlg.title("Save preset")
        dlg.geometry("380x320")
        dlg.transient(self)
        dlg.configure(fg_color=theme.BG)
        ctk.CTkLabel(dlg, text="Save current settings as:",
                     font=self.f("sans", 13)).pack(padx=16, pady=(16, 6), anchor="w")
        row = ctk.CTkFrame(dlg, fg_color="transparent")
        row.pack(fill="x", padx=16)
        name_entry = ctk.CTkEntry(row, placeholder_text="Preset name")
        name_entry.pack(side="left", fill="x", expand=True)

        listbox = ctk.CTkScrollableFrame(dlg, fg_color=theme.SURFACE, height=140)
        listbox.pack(fill="both", expand=True, padx=16, pady=12)

        def rebuild_list():
            for w in listbox.winfo_children():
                w.destroy()
            if not self._user_presets:
                ctk.CTkLabel(listbox, text="No saved presets yet.",
                             text_color=theme.TEXT_MUTED).pack(pady=10)
            for pname in list(self._user_presets):
                r = ctk.CTkFrame(listbox, fg_color="transparent")
                r.pack(fill="x", pady=2)
                ctk.CTkLabel(r, text=pname, anchor="w").pack(side="left", padx=(4, 0))
                ctk.CTkButton(r, text="✕", width=26, fg_color="transparent",
                              hover_color=theme.BORDER, text_color=theme.ERROR,
                              command=lambda n=pname: delete(n)).pack(side="right")

        def delete(n):
            self._user_presets.pop(n, None)
            self._refresh_preset_menu()
            rebuild_list()

        def save(_e=None):
            name = name_entry.get().strip()
            if not name or name == PRESET_PLACEHOLDER or name in BUILTIN_PRESETS:
                self.status.configure(text="Pick a name that isn't a built-in preset.")
                return
            self._user_presets[name] = self._collect_preset()
            self._refresh_preset_menu()
            dlg.destroy()
            self.status.configure(text=f"Saved preset: {name}")

        ctk.CTkButton(row, text="Save", width=70, command=save).pack(side="left", padx=(8, 0))
        name_entry.bind("<Return>", save)
        rebuild_list()
        name_entry.focus_set()
        dlg.grab_set()

    # ---------- app settings (gear button, in-window panel) ----------
    def _open_app_settings(self):
        """Toggle the settings panel: it takes the middle section's place in
        the main window (no separate window) until closed."""
        if getattr(self, "_settings_frame", None) is not None:
            self._close_app_settings()
            return
        self._middle.pack_forget()
        panel = ctk.CTkFrame(self, fg_color="transparent")
        panel.pack(fill="both", expand=True)
        self._settings_frame = panel
        self.bind("<Escape>", lambda _e: self._close_app_settings())

        card = ctk.CTkFrame(panel, fg_color=theme.SURFACE, corner_radius=12)
        card.pack(fill="both", expand=True, padx=20, pady=(0, 8))
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 4))
        ctk.CTkLabel(head, text="App settings",
                     font=self.f("sans", 14, "bold")).pack(side="left")
        ctk.CTkButton(head, text="✕  Close", width=80,
                      fg_color=theme.SURFACE2, hover_color=theme.BORDER,
                      text_color=theme.TEXT,
                      command=self._close_app_settings).pack(side="right")

        ctk.CTkLabel(card, text="Accent color",
                     font=self.f("sans", 13, "bold")).pack(padx=16, pady=(8, 4),
                                                           anchor="w")
        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=16)
        grid.grid_columnconfigure((0, 1, 2), weight=1)

        def pick(name):
            if self.start_btn.cget("state") == "disabled":
                self.status.configure(
                    text="Finish or cancel the current batch first.")
                return
            self._close_app_settings()
            if name != theme.ACCENT_NAME:
                self._apply_accent(name)
                self._save_config()
                self.status.configure(text=f"Theme: {name}.")

        for i, (name, colors) in enumerate(theme.ACCENTS.items()):
            current = " ✓" if name == theme.ACCENT_NAME else ""
            ctk.CTkButton(grid, text=name + current, height=34,
                          fg_color=colors["accent"], hover_color=colors["hover"],
                          text_color=theme.ON_ACCENT,
                          font=self.f("sans", 13, "bold"),
                          command=lambda n=name: pick(n)).grid(
                row=i // 3, column=i % 3, sticky="ew", padx=6, pady=6)

        # Credits
        import webbrowser
        repo_url = f"https://github.com/{GITHUB_REPO}"
        ctk.CTkLabel(card, text="About",
                     font=self.f("sans", 13, "bold")).pack(padx=16, pady=(16, 2),
                                                           anchor="w")
        ctk.CTkLabel(card, text=f"{APP_NAME} v{APP_VERSION} · made by Laxy",
                     font=self.f("sans", 12)).pack(padx=16, anchor="w")
        link = ctk.CTkLabel(card, text=repo_url, text_color=theme.TITLE,
                            cursor="hand2", font=self.f("sans", 12))
        link.pack(padx=16, anchor="w")
        link.bind("<Button-1>", lambda _e: webbrowser.open(repo_url))
        ctk.CTkLabel(
            card, wraplength=560, justify="left", text_color=theme.TEXT_MUTED,
            font=self.f("sans", 11),
            text=("Powered by FFmpeg (video engine), yt-dlp (downloads), "
                  "CustomTkinter and TkinterDnD2 (interface), and Pillow "
                  "(previews). Fonts: DM Sans, IBM Plex Mono, JetBrains Mono. "
                  "Each is the work of its own community; full licenses ship "
                  "in THIRD_PARTY.md.")).pack(padx=16, pady=(6, 14), anchor="w")

    def _close_app_settings(self):
        panel = getattr(self, "_settings_frame", None)
        if panel is None:
            return
        self.unbind("<Escape>")
        panel.destroy()
        self._settings_frame = None
        self._middle.pack(fill="both", expand=True)
