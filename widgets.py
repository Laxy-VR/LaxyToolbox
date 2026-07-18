"""Custom widgets for the queue list."""

import os
import tkinter as tk

import customtkinter as ctk

import theme
from models import status_display, kind_icon


class Tooltip:
    """A small hover tooltip. Appears after a short delay, hides on leave.

    `text` may be a callable returning the current text (or None/"" to show
    nothing), so a tooltip can reflect live state like a job's error."""

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
        if callable(self.text):
            text = self.text()
            if not text:
                return
        else:
            text = self.text
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        self._tip.configure(bg=theme.BORDER)
        tk.Label(self._tip, text=text, justify="left", wraplength=300,
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


class RangeSlider(tk.Canvas):
    """A two-handle slider selecting a (start, end) range.

    CustomTkinter has no dual slider, so this is a small Canvas widget in the
    app's theme. `command(lo, hi)` fires while either handle is dragged.
    """

    HANDLE_R = 7

    def __init__(self, master, width=250, height=22, command=None, bg=None):
        super().__init__(master, width=width, height=height,
                         bg=bg or theme.SURFACE, highlightthickness=0,
                         cursor="hand2")
        self._from, self._to = 0.0, 1.0
        self._lo, self._hi = 0.0, 1.0
        self._command = command
        self._drag = None  # "lo" | "hi" while a handle is held
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_move)
        self.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_drag", None))
        self._redraw()

    def configure_range(self, from_, to):
        """Set the value span (e.g. 0..duration), clamping the handles into it."""
        self._from, self._to = float(from_), max(float(to), float(from_) + 0.01)
        self._lo = min(max(self._lo, self._from), self._to)
        self._hi = min(max(self._hi, self._lo), self._to)
        self._redraw()

    def set_values(self, lo, hi):
        span_min = (self._to - self._from) * 0.005
        self._lo = min(max(float(lo), self._from), self._to)
        self._hi = min(max(float(hi), self._lo + span_min), self._to)
        self._redraw()

    def values(self):
        return self._lo, self._hi

    def _x(self, value):
        pad = self.HANDLE_R + 2
        w = max(self.winfo_width(), 2 * pad + 1)
        frac = (value - self._from) / (self._to - self._from)
        return pad + frac * (w - 2 * pad)

    def _value(self, x):
        pad = self.HANDLE_R + 2
        w = max(self.winfo_width(), 2 * pad + 1)
        frac = (x - pad) / (w - 2 * pad)
        return self._from + min(max(frac, 0.0), 1.0) * (self._to - self._from)

    def _redraw(self):
        self.delete("all")
        cy = int(self.winfo_reqheight() / 2) if self.winfo_height() <= 1 \
            else self.winfo_height() // 2
        x0, x1 = self._x(self._from), self._x(self._to)
        lo_x, hi_x = self._x(self._lo), self._x(self._hi)
        self.create_rectangle(x0, cy - 2, x1, cy + 2, fill=theme.SURFACE2,
                              outline="")
        self.create_rectangle(lo_x, cy - 2, hi_x, cy + 2, fill=theme.ACCENT,
                              outline="")
        r = self.HANDLE_R
        for x in (lo_x, hi_x):
            self.create_oval(x - r, cy - r, x + r, cy + r, fill=theme.ACCENT,
                             outline=theme.ACCENT_HOVER)

    def _on_press(self, event):
        # grab whichever handle is nearer; ties go to lo when left of the span
        d_lo = abs(event.x - self._x(self._lo))
        d_hi = abs(event.x - self._x(self._hi))
        self._drag = "lo" if d_lo < d_hi or (d_lo == d_hi and
                                             event.x < self._x(self._lo)) else "hi"
        self._on_move(event)

    def _on_move(self, event):
        if self._drag is None:
            return
        v = self._value(event.x)
        span_min = (self._to - self._from) * 0.005
        if self._drag == "lo":
            self._lo = min(v, self._hi - span_min)
            self._lo = max(self._lo, self._from)
        else:
            self._hi = max(v, self._lo + span_min)
            self._hi = min(self._hi, self._to)
        self._redraw()
        if self._command:
            self._command(self._lo, self._hi)


class QueueRow(ctk.CTkFrame):
    """One file in the queue: name, status, per-file progress, remove button.
    Rows can be dragged with the mouse to reorder the queue."""

    def __init__(self, master, job, on_select, on_remove, on_open, on_context,
                 on_drag, fonts):
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
            w.bind("<B1-Motion>", lambda e: on_drag(job, e.y_root))
        # Failed rows explain themselves on hover instead of only when selected.
        Tooltip(self.status,
                lambda: self.job.error if self.job.status in ("failed", "unsupported")
                else None,
                fonts["sans"])

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
        # Per-file trim/crop badges. The name is only rewritten when the
        # badges change, so live text (playlist progress) is not clobbered.
        marks = ("  ✂" if self.job.trim else "") + ("  ◱" if self.job.crop else "")
        if marks != getattr(self, "_marks", ""):
            self._marks = marks
            self.name.configure(
                text=f"{kind_icon(self.job.path)}  "
                     f"{os.path.basename(self.job.path)}{marks}")
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
