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

from probe import NO_WINDOW, FFMPEG
from sysutil import track_child, untrack_child

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
_ITEM = re.compile(r"Downloading (?:item|video) (\d+) of (\d+)")


def parse_progress(line: str):
    """Fraction 0..1 from a yt-dlp '[download]  42.3% of ...' line, else None."""
    m = _PCT.search(line)
    return float(m.group(1)) / 100 if m else None


def parse_item(line: str):
    """(index, total) from a '[download] Downloading item 3 of 12' line, else
    None. Lets the UI show progress through a playlist."""
    m = _ITEM.search(line)
    return (int(m.group(1)), int(m.group(2))) if m else None


def looks_like_url(text: str) -> bool:
    return bool(re.match(r"https?://\S+$", (text or "").strip()))


def build_dl_command(url: str, template: str, max_height=None,
                     audio_only=False, cookies_browser=None,
                     playlist=False) -> list:
    """The yt-dlp invocation (pure, for tests). `max_height` caps resolution
    via format sorting: prefer the best format no taller than the cap.
    `audio_only` extracts just the audio track as MP3. `cookies_browser`
    borrows that browser's session, which unlocks full quality on sites that
    withhold HD formats from clients they distrust. `playlist` downloads every
    video the link points to instead of just the one."""
    cmd = [YTDLP_PATH, url,
           "--ignore-config",         # a user's own yt-dlp config (e.g. -f worst)
                                      # must never hijack the app's downloads
           "-o", template,
           "--yes-playlist" if playlist else "--no-playlist",
           "--windows-filenames",
           "--newline",               # one progress line per update
           "--progress",              # --print implies quiet; force progress back on
           "--no-simulate",
           "--print", "after_move:filepath"]  # prints the final file path
    # yt-dlp needs ffmpeg to merge HD video+audio streams; without it, sites
    # like YouTube degrade to the single pre-merged 360p stream. Point it at
    # the app's bundled ffmpeg so downloads never depend on the user's PATH.
    if os.path.isabs(FFMPEG):
        cmd += ["--ffmpeg-location", os.path.dirname(FFMPEG)]
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


def media_files_since(outdir: str, since: float):
    """All finished media files in `outdir` touched at or after `since`,
    oldest first. Backs both the single-file fallback and the playlist sweep
    that catches files whose printed path was mangled by a non-ASCII title."""
    found = []
    try:
        names = os.listdir(outdir)
    except OSError:
        return found
    for name in names:
        p = os.path.join(outdir, name)
        if (os.path.splitext(name)[1].lower() not in _MEDIA_OUT_EXTS
                or name.endswith((".part", ".ytdl")) or not os.path.isfile(p)):
            continue
        m = os.path.getmtime(p)
        if m >= since - 2:
            found.append((m, p))
    return [p for _m, p in sorted(found)]


def newest_media_file(outdir: str, since: float):
    """The most recently modified finished media file in `outdir` since `since`.

    Fallback for locating a completed download: yt-dlp prints the final file
    path, but its piped output silently mangles non-ASCII titles (the frozen
    exe ignores encoding env vars), so the printed path may not match the real
    file on disk. The file's timestamp always tells the truth.
    """
    files = media_files_since(outdir, since)
    return files[-1] if files else None


def download(url: str, outdir: str, on_progress, cancel_event, max_height=None,
             audio_only=False, cookies_browser=None, playlist=False,
             on_item=None):
    """Download `url` into `outdir` as mp4 (or mp3). Returns (filepaths, error).

    filepaths is a list (one entry normally, several for a playlist) and is
    empty on failure. error is None on success and "cancelled" when the
    caller's cancel_event fired. `on_item(index, total)` reports progress
    through a playlist.
    """
    os.makedirs(outdir, exist_ok=True)
    # A playlist keeps its running order via the index prefix; a single video
    # just uses its title.
    template = os.path.join(
        outdir, "%(playlist_index)s · %(title).70s.%(ext)s" if playlist
        else "%(title).80s.%(ext)s")
    cmd = build_dl_command(url, template, max_height, audio_only,
                           cookies_browser, playlist)
    started = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            bufsize=1, creationflags=NO_WINDOW)
    track_child(proc)
    filepaths = []
    seen = set()

    def remember(path):
        norm = os.path.normcase(os.path.abspath(path))
        if norm not in seen:
            seen.add(norm)
            filepaths.append(path)

    tail = deque(maxlen=15)
    log_lines = [f"$ {' '.join(cmd)}"]
    try:
        for line in proc.stdout:
            if cancel_event.is_set():
                proc.terminate()
                proc.wait()
                return filepaths, "cancelled"
            line = line.strip()
            if not line:
                continue
            tail.append(line)
            log_lines.append(line)
            item = parse_item(line)
            if item and on_item:
                on_item(*item)
            frac = parse_progress(line)
            if frac is not None:
                on_progress(frac)
            elif not line.startswith("[") and os.path.exists(line):
                remember(line)  # the printed after_move:filepath
        proc.wait()
    finally:
        untrack_child(proc)
    try:  # full output of the most recent download, for diagnosing bad results
        with open(DL_LOG_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(log_lines) + f"\nexit code: {proc.returncode}\n")
    except OSError:
        pass
    # A non-ASCII title mangles the printed path, so reconcile against what
    # actually landed on disk since we started (also catches a mangled single
    # file). For a playlist this recovers any items we couldn't read a path for.
    if not filepaths or playlist:
        for extra in media_files_since(outdir, started):
            remember(extra)
    if filepaths:
        return filepaths, None
    if proc.returncode != 0:
        err = next((ln for ln in reversed(tail) if "ERROR" in ln),
                   tail[-1] if tail else "download failed")
        return [], err
    return [], "could not locate the downloaded file"


def download_with_update_retry(url, outdir, on_progress, cancel_event,
                               max_height=None, audio_only=False,
                               cookies_browser=None, playlist=False,
                               on_item=None):
    """Download; on failure, self-update yt-dlp once and try again.

    Sites change their internals constantly and a stale yt-dlp is the most
    common cause of failures, so one automatic update-and-retry fixes most of
    them without the user doing anything.
    """
    paths, err = download(url, outdir, on_progress, cancel_event, max_height,
                          audio_only, cookies_browser, playlist, on_item)
    if paths or err == "cancelled" or cancel_event.is_set():
        return paths, err
    update_ytdlp()
    return download(url, outdir, on_progress, cancel_event, max_height,
                    audio_only, cookies_browser, playlist, on_item)
