"""Per-file edits from the queue's right click menu: the trim dialog (range
slider with live first/last frame previews) and the crop box dialog (drag a
rectangle on a real frame). Results are stored on the Job (job.trim,
job.crop) and win over the shared settings at plan time.

Shared state contract: reads self.jobs / job.info, writes job.trim and
job.crop, and refreshes the row, details line, and estimates afterwards.
Frame extraction runs in short worker threads; widgets are only touched from
the main thread via after()."""

import io
import os
import threading
import tkinter as tk

import customtkinter as ctk
from PIL import Image, ImageTk

import theme
from models import parse_time
from probe import extract_frame_png
from widgets import RangeSlider


class EditsMixin:

    # ---------- per-file trim ----------
    def _trim_dialog(self, job):
        dur = job.info.duration if job.info else 0
        if not dur or dur <= 0:
            self.status.configure(text="This file has no known duration to trim.")
            return
        dlg = ctk.CTkToplevel(self)
        dlg.title(f"Trim · {os.path.basename(job.path)}")
        dlg.transient(self)
        dlg.configure(fg_color=theme.BG)
        dlg.resizable(False, False)

        lo0, hi0 = job.trim if job.trim else (0.0, dur)
        hi0 = dur if hi0 is None else min(hi0, dur)

        ctk.CTkLabel(dlg, text="Keep only this range of the video "
                     "(applies to this file only):",
                     font=self.f("sans", 13)).pack(padx=16, pady=(14, 6),
                                                   anchor="w")
        previews = ctk.CTkFrame(dlg, fg_color="transparent")
        previews.pack(padx=16)
        start_prev = ctk.CTkLabel(previews, text="start", width=200, height=112,
                                  text_color=theme.TEXT_MUTED,
                                  fg_color=theme.SURFACE2, corner_radius=8)
        start_prev.pack(side="left")
        end_prev = ctk.CTkLabel(previews, text="end", width=200, height=112,
                                text_color=theme.TEXT_MUTED,
                                fg_color=theme.SURFACE2, corner_radius=8)
        end_prev.pack(side="left", padx=(10, 0))

        slider = RangeSlider(dlg, width=420, bg=theme.BG)
        slider.configure_range(0, dur)
        slider.set_values(lo0, hi0)
        slider.pack(padx=16, pady=(10, 4))

        row = ctk.CTkFrame(dlg, fg_color="transparent")
        row.pack(padx=16, pady=(0, 4), anchor="w")
        ctk.CTkLabel(row, text="Start").pack(side="left")
        start_e = ctk.CTkEntry(row, width=70)
        start_e.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(row, text="to").pack(side="left", padx=(6, 0))
        end_e = ctk.CTkEntry(row, width=70)
        end_e.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(row, text=f"s or mm:ss · video is {dur:.1f}s",
                     text_color=theme.TEXT_MUTED).pack(side="left", padx=(8, 0))

        state = {"token": 0, "images": {}}

        def set_entries(lo, hi):
            start_e.delete(0, "end")
            start_e.insert(0, f"{lo:.1f}")
            end_e.delete(0, "end")
            end_e.insert(0, f"{hi:.1f}")

        def apply_preview(tok, label, png):
            if tok != state["token"] or not dlg.winfo_exists() or not png:
                return
            try:
                img = Image.open(io.BytesIO(png))
                scale = min(200 / img.width, 112 / img.height)
                size = (max(int(img.width * scale), 1),
                        max(int(img.height * scale), 1))
                state["images"][label] = ctk.CTkImage(light_image=img,
                                                      dark_image=img, size=size)
                label.configure(image=state["images"][label], text="")
            except Exception:  # noqa: BLE001 - previews are best-effort
                pass

        def refresh_previews():
            from models import is_audio
            if is_audio(job.path):
                return  # nothing to show for a sound file
            state["token"] += 1
            tok = state["token"]
            lo, hi = slider.values()

            def work():
                p1 = extract_frame_png(job.path, lo, max_width=320)
                p2 = extract_frame_png(job.path, max(hi - 0.05, 0), max_width=320)
                if dlg.winfo_exists():
                    dlg.after(0, lambda: (apply_preview(tok, start_prev, p1),
                                          apply_preview(tok, end_prev, p2)))
            threading.Thread(target=work, daemon=True).start()

        debounce = {"after": None}

        def on_range(lo, hi):
            set_entries(lo, hi)
            if debounce["after"]:
                dlg.after_cancel(debounce["after"])
            debounce["after"] = dlg.after(250, refresh_previews)

        slider._command = on_range

        def on_typed(_e=None):
            lo, hi = parse_time(start_e.get()), parse_time(end_e.get())
            if lo is not None and hi is not None and hi > lo:
                slider.set_values(min(lo, dur), min(hi, dur))
                if debounce["after"]:
                    dlg.after_cancel(debounce["after"])
                debounce["after"] = dlg.after(400, refresh_previews)
        start_e.bind("<KeyRelease>", on_typed)
        end_e.bind("<KeyRelease>", on_typed)

        def finish(trim):
            job.trim = trim
            job.row.render(selected=(job.id == self.selected_id))
            self._update_details()
            self._refresh_estimates()
            self.status.configure(
                text=f"Trim saved for {os.path.basename(job.path)}."
                if trim else f"Trim cleared for {os.path.basename(job.path)}.")
            dlg.destroy()

        def save():
            lo, hi = parse_time(start_e.get()), parse_time(end_e.get())
            if lo is None or hi is None or hi <= lo:
                self.status.configure(text="Trim end must be after trim start.")
                return
            lo, hi = max(lo, 0.0), min(hi, dur)
            if lo <= 0 and hi >= dur - 0.05:
                finish(None)  # the whole video: same as no trim
                return
            # Store an open end when the range reaches the end of the file.
            finish((lo, None if hi >= dur - 0.05 else hi))

        btns = ctk.CTkFrame(dlg, fg_color="transparent")
        btns.pack(padx=16, pady=(6, 14), anchor="e")
        ctk.CTkButton(btns, text="Clear trim", width=90,
                      fg_color=theme.SURFACE2, hover_color=theme.BORDER,
                      text_color=theme.TEXT,
                      command=lambda: finish(None)).pack(side="left")
        ctk.CTkButton(btns, text="Save", width=90,
                      command=save).pack(side="left", padx=(8, 0))

        set_entries(lo0, hi0)
        refresh_previews()
        dlg.grab_set()

    # ---------- per-file crop box ----------
    def _crop_dialog(self, job):
        info = job.info
        if not (info and info.width and info.height):
            self.status.configure(text="This file has no picture to crop.")
            return
        dur = info.duration or 0
        png = extract_frame_png(job.path, dur * 0.25 if dur > 1 else 0,
                                max_width=720)
        if not png:
            self.status.configure(text="Could not read a frame from this file.")
            return
        src = Image.open(io.BytesIO(png))
        # The preview frame may already be scaled down; map through BOTH the
        # preview scale and the display scale back to source pixels.
        to_src = info.width / src.width
        disp_scale = min(640 / src.width, 400 / src.height, 1.0)
        dw, dh = max(int(src.width * disp_scale), 1), max(int(src.height * disp_scale), 1)

        dlg = ctk.CTkToplevel(self)
        dlg.title(f"Crop · {os.path.basename(job.path)}")
        dlg.transient(self)
        dlg.configure(fg_color=theme.BG)
        dlg.resizable(False, False)
        ctk.CTkLabel(dlg, text="Drag a box over the part to keep "
                     "(applies to this file only):",
                     font=self.f("sans", 13)).pack(padx=16, pady=(14, 6),
                                                   anchor="w")
        canvas = tk.Canvas(dlg, width=dw, height=dh, highlightthickness=0,
                           bg=theme.BG, cursor="crosshair")
        canvas.pack(padx=16)
        photo = ImageTk.PhotoImage(src.resize((dw, dh)))
        canvas.create_image(0, 0, anchor="nw", image=photo)
        canvas._photo = photo  # keep a reference so Tk doesn't GC it

        dims = ctk.CTkLabel(dlg, text="No crop drawn yet.",
                            text_color=theme.TEXT_MUTED, font=self.f("mono", 11))
        dims.pack(padx=16, anchor="w", pady=(4, 0))

        # canvas-space selection [x0, y0, x1, y1]; seeded from an existing crop
        sel = {"box": None, "rect": None, "start": None}
        px_per_canvas = to_src / disp_scale  # source pixels per canvas pixel

        def to_source(box):
            x0, y0, x1, y1 = box
            x0, x1 = sorted((x0, x1))
            y0, y1 = sorted((y0, y1))
            x = max(int(x0 * px_per_canvas), 0)
            y = max(int(y0 * px_per_canvas), 0)
            w = min(int((x1 - x0) * px_per_canvas), info.width - x)
            h = min(int((y1 - y0) * px_per_canvas), info.height - y)
            w, h = w - w % 2, h - h % 2  # yuv420 needs even dimensions
            return (w, h, x, y) if w >= 16 and h >= 16 else None

        def redraw():
            if sel["rect"] is not None:
                canvas.delete(sel["rect"])
                sel["rect"] = None
            if sel["box"]:
                x0, y0, x1, y1 = sel["box"]
                sel["rect"] = canvas.create_rectangle(
                    x0, y0, x1, y1, outline=theme.ACCENT, width=2)
            crop = to_source(sel["box"]) if sel["box"] else None
            dims.configure(text=f"Keeps {crop[0]} × {crop[1]} px" if crop
                           else "No crop drawn yet (or the box is too small).")

        if job.crop:
            w, h, x, y = job.crop
            sel["box"] = (x / px_per_canvas, y / px_per_canvas,
                          (x + w) / px_per_canvas, (y + h) / px_per_canvas)
            redraw()

        def clamp(v, hi):
            return min(max(v, 0), hi)

        def on_press(e):
            sel["start"] = (clamp(e.x, dw), clamp(e.y, dh))
            sel["box"] = (*sel["start"], *sel["start"])
            redraw()

        def on_move(e):
            if sel["start"]:
                sel["box"] = (*sel["start"], clamp(e.x, dw), clamp(e.y, dh))
                redraw()
        canvas.bind("<Button-1>", on_press)
        canvas.bind("<B1-Motion>", on_move)
        canvas.bind("<ButtonRelease-1>", lambda _e: sel.update(start=None))

        def finish(crop):
            job.crop = crop
            job.row.render(selected=(job.id == self.selected_id))
            self._update_details()
            self._refresh_estimates()
            self.status.configure(
                text=f"Crop saved for {os.path.basename(job.path)}."
                if crop else f"Crop cleared for {os.path.basename(job.path)}.")
            dlg.destroy()

        def save():
            crop = to_source(sel["box"]) if sel["box"] else None
            if crop is None:
                self.status.configure(text="Drag a box on the picture first.")
                return
            finish(crop)

        btns = ctk.CTkFrame(dlg, fg_color="transparent")
        btns.pack(padx=16, pady=(6, 14), anchor="e")
        ctk.CTkButton(btns, text="Clear crop", width=90,
                      fg_color=theme.SURFACE2, hover_color=theme.BORDER,
                      text_color=theme.TEXT,
                      command=lambda: finish(None)).pack(side="left")
        ctk.CTkButton(btns, text="Save", width=90,
                      command=save).pack(side="left", padx=(8, 0))
        dlg.grab_set()
