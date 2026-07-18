"""Laxy: a themed, queue based CustomTkinter GUI for H.265 batch compression
with ffmpeg.

Add one file or a whole folder, choose one shared setting (best quality auto, or
a target size like Discord's 500 MB limit), and it works through the queue,
applying the same policy to every file with per file and overall progress.

The App class is assembled from focused mixins (gui_*.py); this module holds
only the composition, startup, and shutdown.
"""

import json
import os
import queue
import sys
import threading
import tkinter as tk

import customtkinter as ctk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _DND_AVAILABLE = True
except Exception:  # noqa: BLE001 - drag-and-drop is optional
    _DND_AVAILABLE = False

import theme
from gui_build import BuildMixin
from gui_config import ConfigMixin
from gui_downloads import DownloadsMixin
from gui_edits import EditsMixin
from gui_notes import NotesMixin
from gui_queue import QueueMixin
from gui_run import RunMixin
from gui_settings import SettingsMixin
from models import APP_NAME, CONFIG_PATH, GITHUB_REPO, Job  # noqa: F401 - Job hints self.jobs
from sysutil import resource_path, terminate_children

_AppBase = (ctk.CTk, TkinterDnD.DnDWrapper) if _DND_AVAILABLE else (ctk.CTk,)


class App(BuildMixin, QueueMixin, EditsMixin, DownloadsMixin, NotesMixin,
          SettingsMixin, RunMixin, ConfigMixin, *_AppBase):
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
        # GPU verdicts per vendor ("nvenc"/"amf"/"qsv" -> bool). A vendor not
        # in the dict is unverified: offered, then probed in the background.
        self._gpu_ok: dict = {}

        self.cancel_event = threading.Event()
        self.msg_queue: queue.Queue = queue.Queue()
        self._dl_cancels: dict = {}  # per-download cancel events, keyed by job id
        self._user_presets: dict = {}  # name -> settings snapshot, from config
        self._sample_cancel = threading.Event()  # stops a running 5s sample
        self._sample_files: list = []  # temp sample encodes, swept on close

        self._build_ui()
        # Size to fit every control: height is measured on the taller
        # Compress tab, width on the wider GIF tab (controls + previews).
        self.update_idletasks()
        need_w = max(700, self._widest_tab_reqwidth())
        needed = self.winfo_reqheight()
        usable = self.winfo_screenheight() - 80  # leave room for the taskbar
        if needed > usable:
            # Screen too short for everything (small laptop, heavy display
            # scaling): scroll the middle section instead of clipping it. The
            # bottom bar is pinned, so the action buttons stay visible.
            self._make_middle_scrollable()
            self.update_idletasks()
            self.minsize(need_w, min(520, usable))
            self.geometry(f"{need_w}x{usable}")
        else:
            self.minsize(need_w, needed)
            # Open a bit taller than the minimum when the screen allows it,
            # giving the file queue some breathing room.
            self.geometry(f"{need_w}x{min(needed + 60, usable)}")
        self._load_config()
        self._set_app_icon()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-o>", lambda _e: self.on_add_files())
        self.bind("<Control-v>", self._on_paste)
        self.bind("<Delete>", self._on_delete_key)
        self.bind("<Return>", self._on_return_key)
        self.bind("<Alt-Up>", lambda _e: self._move_selected(-1))
        self.bind("<Alt-Down>", lambda _e: self._move_selected(1))
        if self._dnd_ok:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)
        if GITHUB_REPO:
            self.after(1500, lambda: threading.Thread(
                target=self._update_check_worker, daemon=True).start())
        # Verify each GPU vendor actually works here unless a previous run
        # already ruled (the wrong GPU brand or an old driver fails at encode
        # time even though the bundled ffmpeg lists the encoders).
        if any(v not in self._gpu_ok for v in self._gpu_codecs):
            threading.Thread(target=self._gpu_probe_worker, daemon=True).start()
        self.after(100, self._poll_queue)

    def _on_close(self):
        self.cancel_event.set()  # stop the encode loop scheduling more work
        self._sample_cancel.set()  # and any 5s sample encode
        for ev in self._dl_cancels.values():  # stop in-flight downloads
            ev.set()
        self._save_config()
        # Daemon threads die with the process, but their ffmpeg/yt-dlp children
        # would keep running headless. Kill them, then sweep the partial files.
        terminate_children()
        for job in self.jobs:
            if job.status in ("queued", "encoding"):
                self._cleanup_outputs(job)
        for path in self._sample_files:  # sweep temp 5s sample encodes
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
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
            out = r.stdout or r.stderr
            result[key] = out.splitlines()[0] if out else "no output"
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
    _accent = "Purple"
    try:  # the saved accent must be known before any widget takes its colors
        with open(CONFIG_PATH, encoding="utf-8") as _f:
            _accent = json.load(_f).get("accent", "Purple")
    except (OSError, ValueError):
        pass
    theme.apply_theme(_accent)
    # A hidden plain-Tk root lets us read installed font families before we
    # build the UI, so the default font family is set for every widget.
    _probe_root = tk.Tk()
    _probe_root.withdraw()
    _fonts = theme.resolve_fonts(_probe_root)
    theme.set_default_font_family(_fonts["sans"])
    _probe_root.destroy()

    App(fonts=_fonts).mainloop()
