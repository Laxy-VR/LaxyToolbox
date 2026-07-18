"""In-app updating: fetch the newest release exe from GitHub, verify it,
swap it in place of the running exe, and let the app relaunch.

A running Windows exe cannot be overwritten but CAN be renamed, so the
install is the classic swap: current -> .old, downloaded .new -> current,
relaunch. The .old survives until the next startup sweeps it, so a botched
install can always be rolled back to a working exe. Every step returns an
error string instead of raising; the caller falls back to opening the
release page in the browser, which is what the app did before this existed.
"""

import hashlib
import json
import os
import sys
import urllib.request

_HEADERS = {"Accept": "application/vnd.github+json",
            "User-Agent": "LaxyCompressor"}


def exe_path():
    """The running packaged exe, or None in a dev run (no self-update)."""
    return sys.executable if getattr(sys, "frozen", False) else None


def pick_asset(release: dict):
    """(tag, page_url, download_url, sha256 or None, size) from a GitHub
    release API payload, or None when it has no exe asset."""
    asset = next((a for a in release.get("assets", [])
                  if str(a.get("name", "")).lower().endswith(".exe")), None)
    if not asset or not asset.get("browser_download_url"):
        return None
    # GitHub publishes a sha256 digest per asset; older releases may lack it,
    # in which case the download is accepted unverified (as a browser would).
    digest = str(asset.get("digest") or "")
    sha = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
    return (release.get("tag_name"), release.get("html_url"),
            asset["browser_download_url"], sha, int(asset.get("size") or 0))


def latest_asset(repo: str):
    """pick_asset() of the newest release, or None on any failure."""
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            return pick_asset(json.load(r))
    except Exception:  # noqa: BLE001 - offline/rate limit: caller falls back
        return None


def download(url: str, dest: str, sha256=None, on_progress=None, cancel=None):
    """Stream `url` to `dest` (via dest.part), verifying sha256 when given.

    Returns None on success or a short human-readable error string.
    on_progress(fraction) fires as bytes arrive; `cancel` is an optional
    threading.Event that aborts the download.
    """
    tmp = dest + ".part"
    try:
        digest = hashlib.sha256()
        req = urllib.request.Request(url, headers={"User-Agent": "LaxyCompressor"})
        with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                if cancel is not None and cancel.is_set():
                    return "cancelled"
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                digest.update(chunk)
                got += len(chunk)
                if on_progress and total:
                    on_progress(got / total)
        if sha256 and digest.hexdigest().lower() != sha256.lower():
            return "the download did not match the release checksum"
        os.replace(tmp, dest)
        return None
    except Exception as e:  # noqa: BLE001 - network errors become a message
        return str(e)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def apply(new_path: str, current=None):
    """Swap `new_path` into place of the running exe. None, or an error.

    current -> current.old, new -> current. The .old is deliberately kept
    (startup sweeps it): if the rename of the new exe fails midway, the old
    one is put straight back, so there is always a runnable exe on disk.
    """
    current = current or exe_path()
    if not current:
        return "not running from a packaged exe"
    old = current + ".old"
    try:
        if os.path.exists(old):
            os.remove(old)
    except OSError:
        return "an earlier update file is still locked; restart and try again"
    try:
        os.rename(current, old)
    except OSError as e:
        return f"could not stage the update: {e}"
    try:
        os.rename(new_path, current)
    except OSError as e:
        try:
            os.rename(old, current)  # roll back to the working exe
        except OSError:
            pass
        return f"could not install the update: {e}"
    return None


def sweep_leftovers(current=None):
    """Delete update leftovers (.old / .new / .new.part) next to the exe.

    Called at startup. The .old may still be locked by the exiting previous
    process; that is fine, the next launch gets it."""
    current = current or exe_path()
    if not current:
        return
    for suffix in (".old", ".new", ".new.part"):
        try:
            path = current + suffix
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
