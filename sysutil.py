"""Windows platform helpers: keep-awake, taskbar flash, bundled resource paths,
and a registry of child processes so closing the app never orphans an ffmpeg."""

import os
import sys
import threading

# Long-running children (ffmpeg encodes, yt-dlp downloads) register here so
# the app can kill them on exit. Daemon threads die with the process, but
# their subprocesses would otherwise keep running headless at full CPU.
_children = set()
_children_lock = threading.Lock()


def track_child(proc):
    with _children_lock:
        _children.add(proc)


def untrack_child(proc):
    with _children_lock:
        _children.discard(proc)


def terminate_children(timeout: float = 3.0) -> None:
    """Terminate every registered child process and wait briefly for each.

    Called on window close; the short wait releases file locks so partial
    output files can be deleted before the process exits.
    """
    with _children_lock:
        procs = list(_children)
    for proc in procs:
        try:
            if proc.poll() is None:
                proc.terminate()
        except OSError:
            pass
    for proc in procs:
        try:
            proc.wait(timeout=timeout)
        except Exception:  # noqa: BLE001 - exit must never hang on a zombie
            pass


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


# ---------- taskbar progress (ITaskbarList3 via raw ctypes COM) ----------
_taskbar = None  # (SetProgressValue, SetProgressState) or False after failure


def _taskbar_methods():
    """Create the Windows ITaskbarList3 COM object once; False if unavailable."""
    global _taskbar
    if _taskbar is not None:
        return _taskbar or None
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        ole32 = ctypes.oledll.ole32
        clsid, iid = GUID(), GUID()
        ole32.CLSIDFromString("{56FDF344-FD6D-11d0-958A-006097C9A090}",
                              ctypes.byref(clsid))
        ole32.CLSIDFromString("{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}",
                              ctypes.byref(iid))
        ole32.CoInitialize(None)
        ptr = ctypes.c_void_p()
        ole32.CoCreateInstance(ctypes.byref(clsid), None, 1,  # CLSCTX_INPROC
                               ctypes.byref(iid), ctypes.byref(ptr))
        vtbl = ctypes.cast(ptr.value,
                           ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents

        def method(index, *argtypes):
            proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
            func = proto(vtbl[index])
            return lambda *args: func(ptr, *args)

        method(3)()  # HrInit
        set_value = method(9, wintypes.HWND, ctypes.c_ulonglong, ctypes.c_ulonglong)
        set_state = method(10, wintypes.HWND, ctypes.c_int)
        _taskbar = (set_value, set_state)
        return _taskbar
    except Exception:  # noqa: BLE001 - taskbar progress is decoration only
        _taskbar = False
        return None


def set_taskbar_progress(window, fraction):
    """Paint real progress on the app's taskbar button; None clears it."""
    if sys.platform != "win32":
        return
    methods = _taskbar_methods()
    if not methods:
        return
    set_value, set_state = methods
    try:
        import ctypes
        GA_ROOT = 2
        hwnd = ctypes.windll.user32.GetAncestor(window.winfo_id(), GA_ROOT)
        if fraction is None:
            set_state(hwnd, 0)  # TBPF_NOPROGRESS
        else:
            set_value(hwnd, int(max(0.0, min(fraction, 1.0)) * 1000), 1000)
    except Exception:  # noqa: BLE001
        pass


def relaunch():
    """Start a fresh copy of the app (how a theme change takes effect)."""
    import subprocess
    if getattr(sys, "frozen", False):  # packaged exe
        subprocess.Popen([sys.executable])
    else:
        subprocess.Popen([sys.executable] + sys.argv)


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
