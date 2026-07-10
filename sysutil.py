"""Windows platform helpers: keep-awake, taskbar flash, bundled resource paths."""

import os
import sys


def set_keep_awake(on: bool):
    """Stop Windows from sleeping while a long encode runs (no-op elsewhere).

    Only the system is kept awake; the display may still turn off, since an
    encode doesn't need the screen.
    """
    if sys.platform != "win32":
        return
    import ctypes
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:  # noqa: BLE001
        pass


def flash_taskbar(window):
    """Flash the taskbar button until the window is brought to the foreground."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes
        FLASHW_ALL = 0x00000003
        FLASHW_TIMERNOFG = 0x0000000C

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND),
                        ("dwFlags", wintypes.DWORD), ("uCount", wintypes.UINT),
                        ("dwTimeout", wintypes.DWORD)]

        # winfo_id() is Tk's inner client window (class TkChild); the taskbar
        # button belongs to its top-level ancestor, so flash that instead.
        GA_ROOT = 2
        hwnd = ctypes.windll.user32.GetAncestor(window.winfo_id(), GA_ROOT)
        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd,
                          FLASHW_ALL | FLASHW_TIMERNOFG, 0, 0)
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
    except Exception:  # noqa: BLE001
        pass


def resource_path(name: str) -> str:
    """Path to a bundled resource, working both in dev and in a PyInstaller build."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def _version_tuple(v: str) -> tuple:
    parts = []
    for piece in (v or "").lstrip("vV").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer_version(latest: str, current: str) -> bool:
    return _version_tuple(latest) > _version_tuple(current)


def latest_release(repo: str):
    """(version, page_url) of the newest GitHub release, or (None, None).

    Anonymous API call; failures (offline, rate limit, no releases yet) are
    swallowed because an update check must never break the app.
    """
    import json
    import urllib.request
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "LaxyCompressor"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
        return data.get("tag_name"), data.get("html_url")
    except Exception:  # noqa: BLE001
        return None, None
