"""Custom widgets for the queue list."""

import os

import customtkinter as ctk

import theme
from models import status_display, kind_icon


class QueueRow(ctk.CTkFrame):
    """One file in the queue: name, status, per-file progress, remove button."""

    def __init__(self, master, job, on_select, on_remove, on_open, on_context, fonts):
        super().__init__(master, fg_color=theme.SURFACE2, corner_radius=8)
        self.job = job
        self.grid_columnconfigure(0, weight=1)

        self.name = ctk.CTkLabel(
            self, text=f"{kind_icon(job.path)}  {os.path.basename(job.path)}",
            anchor="w", font=ctk.CTkFont(family=fonts["sans"], size=13),
        )
        self.name.grid(row=0, column=0, sticky="w", padx=(12, 6), pady=(8, 0))

        self.status = ctk.CTkLabel(
            self, text="reading…", anchor="e", text_color=theme.TEXT_MUTED,
            font=ctk.CTkFont(family=fonts["mono"], size=11),
        )
        self.status.grid(row=0, column=1, sticky="e", padx=6, pady=(8, 0))

        self.remove_btn = ctk.CTkButton(
            self, text="✕", width=26, height=26, fg_color="transparent",
            hover_color=theme.BORDER, text_color=theme.TEXT_MUTED,
            command=lambda: on_remove(job),
        )
        self.remove_btn.grid(row=0, column=2, padx=(0, 8), pady=(6, 0))

        self.bar = ctk.CTkProgressBar(self, height=5)
        self.bar.set(0)
        self.bar.grid(row=1, column=0, columnspan=3, sticky="ew", padx=12, pady=(4, 8))

        for w in (self, self.name, self.status):
            w.bind("<Button-1>", lambda _e: on_select(job))
            w.bind("<Double-Button-1>", lambda _e: on_open(job))
            w.bind("<Button-3>", lambda e: on_context(job, e))

    def set_name(self, path):
        self.name.configure(text=f"{kind_icon(path)}  {os.path.basename(path)}")

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
