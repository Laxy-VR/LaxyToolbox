"""Download videos from links using yt-dlp.

yt-dlp is fetched from its official GitHub releases on first use into the
app's data folder (not bundled into the exe), so it can self-update when
sites change their internals and keep working without rebuilding the app.
It cannot download DRM-protected content; such links simply fail.
"""

import os
import re
import subprocess
import time
import urllib.request
from collections import deque

from probe import NO_WINDOW

YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
APPDATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA")
                           or os.path.expanduser("~"), "LaxyCompressor")
YTDLP_PATH = os.path.join(APPDATA_DIR, "yt-dlp.exe")
DL_LOG_PATH = os.path.join(APPDATA_DIR, "last_download.log")


def has_ytdlp() -> bool:
    return os.path.exists(YTDLP_PATH)


def fetch_ytdlp(on_progress=None) -> None:
    """Download yt-dlp.exe from the official release (about 17 MB, one time)."""
    os.makedirs(APPDATA_DIR, exist_ok=True)
    tmp = YTDLP_PATH + ".part"
    with urllib.request.urlopen(YTDLP_URL, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if on_progress and total:
                on_progress(got / total)
    os.replace(tmp, YTDLP_PATH)


def update_ytdlp() -> None:
    """Let yt-dlp replace itself with the latest release (fixes broken sites)."""
    try:
        subprocess.run([YTDLP_PATH, "-U"], capture_output=True, text=True,
                       timeout=180, creationflags=NO_WINDOW)
        os.utime(YTDLP_PATH)  # mark as freshly checked even if already latest
    except Exception:  # noqa: BLE001 - update is best-effort
        pass


def update_ytdlp_if_stale(max_age_days: float = 7.0) -> bool:
    """Self-update when the local yt-dlp hasn't been checked recently.

    Sites quietly stop offering high resolutions to outdated clients (no
    error, just a worse file), so downloads shouldn't run on a stale copy.
    Returns True when an update check ran.
    """
    try:
        age_days = (time.time() - os.path.getmtime(YTDLP_PATH)) / 86400
    except OSError:
        return False
    if age_days < max_age_days:
        return False
    update_ytdlp()
    return True


_PCT = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")


def parse_progress(line: str):
    """Fraction 0..1 from a yt-dlp '[download]  42.3% of ...' line, else None."""
    m = _PCT.search(line)
    return float(m.group(1)) / 100 if m else None


def looks_like_url(text: str) -> bool:
    return bool(re.match(r"https?://\S+$", (text or "").strip()))


def build_dl_command(url: str, template: str, max_height=None,
                     audio_only=False, cookies_browser=None) -> list:
    """The yt-dlp invocation (pure, for tests). `max_height` caps resolution
    via format sorting: prefer the best format no taller than the cap.
    `audio_only` extracts just the audio track as MP3. `cookies_browser`
    borrows that browser's session, which unlocks full quality on sites that
    withhold HD formats from clients they distrust."""
    cmd = [YTDLP_PATH, url,
           "--ignore-config",         # a user's own yt-dlp config (e.g. -f worst)
                                      # must never hijack the app's downloads
           "-o", template,
           "--no-playlist",           # one link = one video
           "--windows-filenames",
           "--newline",               # one progress line per update
           "--progress",              # --print implies quiet; force progress back on
           "--no-simulate",
           "--print", "after_move:filepath"]  # prints the final file path
    if cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    if audio_only:
        cmd += ["-x", "--audio-format", "mp3"]
    else:
        cmd += ["--merge-output-format", "mp4"]
        if max_height:
            cmd += ["-S", f"res:{max_height}"]
    return cmd


_MEDIA_OUT_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3"}


def newest_media_file(outdir: str, since: float):
    """The most recently modified finished media file in `outdir` since `since`.

    Fallback for locating a completed download: yt-dlp prints the final file
    path, but its piped output silently mangles non-ASCII titles (the frozen
    exe ignores encoding env vars), so the printed path may not match the real
    file on disk. The file's timestamp always tells the truth.
    """
    best, best_m = None, 0.0
    try:
        names = os.listdir(outdir)
    except OSError:
        return None
    for name in names:
        p = os.path.join(outdir, name)
        if (os.path.splitext(name)[1].lower() not in _MEDIA_OUT_EXTS
                or name.endswith((".part", ".ytdl")) or not os.path.isfile(p)):
            continue
        m = os.path.getmtime(p)
        if m >= since - 2 and m > best_m:
            best, best_m = p, m
    return best


def download(url: str, outdir: str, on_progress, cancel_event, max_height=None,
             audio_only=False, cookies_browser=None):
    """Download `url` into `outdir` as mp4 (or mp3). Returns (filepath, error).

    filepath is None on failure; error is None on success and "cancelled" when
    the caller's cancel_event fired.
    """
    os.makedirs(outdir, exist_ok=True)
    template = os.path.join(outdir, "%(title).80s.%(ext)s")
    cmd = build_dl_command(url, template, max_height, audio_only, cookies_browser)
    started = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            bufsize=1, creationflags=NO_WINDOW)
    filepath = None
    tail = deque(maxlen=15)
    log_lines = [f"$ {' '.join(cmd)}"]
    for line in proc.stdout:
        if cancel_event.is_set():
            proc.terminate()
            proc.wait()
            return None, "cancelled"
        line = line.strip()
        if not line:
            continue
        tail.append(line)
        log_lines.append(line)
        frac = parse_progress(line)
        if frac is not None:
            on_progress(frac)
        elif not line.startswith("[") and os.path.exists(line):
            filepath = line  # the printed after_move:filepath
    proc.wait()
    try:  # full output of the most recent download, for diagnosing bad results
        with open(DL_LOG_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(log_lines) + f"\nexit code: {proc.returncode}\n")
    except OSError:
        pass
    if proc.returncode != 0:
        err = next((ln for ln in reversed(tail) if "ERROR" in ln),
                   tail[-1] if tail else "download failed")
        return None, err
    if not filepath:  # printed path was mangled (e.g. non-ASCII title)
        filepath = newest_media_file(outdir, started)
    if filepath:
        return filepath, None
    return None, "could not locate the downloaded file"


def download_with_update_retry(url, outdir, on_progress, cancel_event,
                               max_height=None, audio_only=False,
                               cookies_browser=None):
    """Download; on failure, self-update yt-dlp once and try again.

    Sites change their internals constantly and a stale yt-dlp is the most
    common cause of failures, so one automatic update-and-retry fixes most of
    them without the user doing anything.
    """
    path, err = download(url, outdir, on_progress, cancel_event,
                         max_height, audio_only, cookies_browser)
    if path or err == "cancelled" or cancel_event.is_set():
        return path, err
    update_ytdlp()
    return download(url, outdir, on_progress, cancel_event,
                    max_height, audio_only, cookies_browser)
