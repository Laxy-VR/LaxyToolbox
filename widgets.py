"""Custom widgets for the queue list."""

import os
import tkinter as tk

import customtkinter as ctk

import theme
from models import status_display, kind_icon


class Tooltip:
    """A small hover tooltip. Appears after a short delay, hides on leave."""

    def __init__(self, widget, text, family="Segoe UI"):
        self.widget = widget
        self.text = text
        self.family = family
        self._tip = None
        self._after = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Button-1>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._after = self.widget.after(500, self._show)

    def _show(self):
        if self._tip is not None:
            return
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        self._tip.configure(bg=theme.BORDER)
        tk.Label(self._tip, text=self.text, justify="left", wraplength=300,
                 bg=theme.SURFACE2, fg=theme.TEXT, font=(self.family, 9),
                 padx=8, pady=6).pack(padx=1, pady=1)

    def _hide(self, _e=None):
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

    def _cancel(self):
        if self._after is not None:
            self.widget.after_cancel(self._after)
            self._after = None


class QueueRow(ctk.CTkFrame):
    """One file in the queue: name, status, per-file progress, remove button."""

    def __init__(self, master, job, on_select, on_remove, on_open, on_context, fonts):
        super().__init__(master, fg_color=theme.SURFACE2, corner_radius=8)
        self.job = job
        self.grid_columnconfigure(1, weight=1)
        self._thumb_img = None  # keep a reference so Tk doesn't GC the image

        self.thumb = ctk.CTkLabel(self, text="", width=48, height=30,
                                  fg_color=theme.SURFACE, corner_radius=4)
        self.thumb.grid(row=0, column=0, rowspan=2, padx=(8, 4), pady=8)

        self.name = ctk.CTkLabel(
            self, text=f"{kind_icon(job.path)}  {os.path.basename(job.path)}",
            anchor="w", font=ctk.CTkFont(family=fonts["sans"], size=13),
        )
        self.name.grid(row=0, column=1, sticky="w", padx=(6, 6), pady=(8, 0))

        self.status = ctk.CTkLabel(
            self, text="reading…", anchor="e", text_color=theme.TEXT_MUTED,
            font=ctk.CTkFont(family=fonts["mono"], size=11),
        )
        self.status.grid(row=0, column=2, sticky="e", padx=6, pady=(8, 0))

        self.remove_btn = ctk.CTkButton(
            self, text="✕", width=26, height=26, fg_color="transparent",
            hover_color=theme.BORDER, text_color=theme.TEXT_MUTED,
            command=lambda: on_remove(job),
        )
        self.remove_btn.grid(row=0, column=3, padx=(0, 8), pady=(6, 0))

        self.bar = ctk.CTkProgressBar(self, height=5)
        self.bar.set(0)
        self.bar.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(6, 12), pady=(4, 8))

        for w in (self, self.name, self.status, self.thumb):
            w.bind("<Button-1>", lambda _e: on_select(job))
            w.bind("<Double-Button-1>", lambda _e: on_open(job))
            w.bind("<Button-3>", lambda e: on_context(job, e))

    def set_name(self, path):
        self.name.configure(text=f"{kind_icon(path)}  {os.path.basename(path)}")

    def set_thumbnail(self, png):
        if not png:
            return
        try:
            import io
            from PIL import Image
            img = Image.open(io.BytesIO(png))
            scale = min(48 / img.width, 30 / img.height)
            size = (max(int(img.width * scale), 1), max(int(img.height * scale), 1))
            self._thumb_img = ctk.CTkImage(light_image=img, dark_image=img, size=size)
            self.thumb.configure(image=self._thumb_img, text="")
        except Exception:  # noqa: BLE001 - thumbnails are best-effort
            pass

    def render(self, selected: bool):
        text, color = status_display(self.job)
        self.status.configure(text=text, text_color=color)
        if self.job.status == "done":
            self.bar.set(1.0)
            self.bar.configure(progress_color=theme.SUCCESS)
        elif self.job.status in ("encoding", "downloading"):
            self.bar.set(self.job.progress)
            self.bar.configure(progress_color=theme.ACCENT)
        elif self.job.status == "failed":
            self.bar.set(1.0)
            self.bar.configure(progress_color=theme.ERROR)
        else:
            self.bar.set(0.0)
        self.configure(border_width=2 if selected else 0, border_color=theme.ACCENT)
