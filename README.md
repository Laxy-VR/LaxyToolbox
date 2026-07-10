# Laxy's Compressor

A desktop app that compresses videos (H.265 / AV1 / H.264), makes and shrinks
GIFs, and converts images (WebP / AVIF / JPEG), with metadata-driven
recommendations, batch queueing, and live progress, all powered by `ffmpeg`.

## Requirements
- Python 3.10+
- `ffmpeg` and `ffprobe` on your PATH (the packaged .exe bundles its own)

## Setup (once)
```powershell
pip install -r requirements.txt
```

## Run
```powershell
python app.py
```

## Build a standalone .exe
```powershell
pip install pyinstaller
./build.ps1
```
Produces `dist\Laxy Compressor.exe` (a single file you can double click,
no Python needed). The `.exe` still calls `ffmpeg`/`ffprobe` from your PATH, so
keep them installed. To run on a machine without ffmpeg, bundle it too (ask and
I'll wire that in).

## What it does
Add one file or a whole folder, pick **one shared setting**, and it works
through the queue, applying the same policy to every file with per-file and
overall progress. Dark UI themed to the site's palette (`theme.py`).

## Five tabs

### Compress (video to H.265)
- **Best quality** · single-pass constant quality. Picks the smallest file with
  no visible quality loss, tuned to the source.
- **Trim** · optional start/end seconds to compress just a section. A
  **Cut only (no re-encode)** checkbox makes the cut instant and lossless
  instead; cut points snap to keyframes, so they can shift by a second or two.
- **Remove audio** · an Audio menu option that strips the track (gameplay clips).
- **Target size** · size-targeted bitrate. Enter a max size (e.g. 500 MB for
  Discord Nitro) and it computes the bitrate to fit under it, with a live
  prediction of the resulting quality.
- **Split to fit** · splits a long video into parts that each fit under the
  limit, so an hour-long clip becomes several good-looking uploads instead of
  one soft one. Part count is Auto or chosen.

### GIF
Export a short clip as an animated GIF (clip start + length, fps, size), using a
2-in-1 palette pass tuned for moving content, with a choice of dithering. A live
preview shows the frame at your chosen clip start so you can find the moment
without leaving the app. `.gif` files can also be imported here to shrink an
existing GIF, or on the Compress tab to turn one into MP4.

### Images
Batch convert PNG / JPEG / BMP / WebP to **WebP** (recommended), **AVIF**
(smallest), or **JPEG** (max compatibility) at three quality levels, with
optional resize (2x / 1.5x / 0.5x, or a max-height cap that never upscales).
A preview shows the selected image. Videos and images can share the queue;
each tab only processes its own kind.

### Audio
Extract the audio track from videos, or convert audio files (WAV, FLAC, and
more), to MP3 or M4A at three quality levels.

### Download
Paste a video link (YouTube, Twitter, and most sites) and it downloads to the
output folder via yt-dlp, with an optional resolution cap, an Audio only (MP3)
toggle, and live progress in the queue. Downloaded files are **not** compressed automatically (sites already
compress their videos); right-click one and choose Queue for compression to
opt it in. yt-dlp is fetched from its official GitHub release on first use and
self-updates when a site breaks, so downloads keep working without app
rebuilds. DRM protected content is not supported.

## Codecs and encoders
- **H.265 (default)** · the best balance of compression and playback support.
- **AV1** · 20 to 30% smaller than H.265; plays on modern devices and browsers.
- **H.264** · larger files, but plays on everything ever made.
- **Hardware**: CPU (best quality per byte, real two-pass for target/split) or
  GPU/NVENC (much faster). The GPU option appears per codec, based on what the
  machine's card supports. One quality slider maps across all codecs.

## Extras
- **Brand typography bundled**: DM Sans / JetBrains Mono / IBM Plex Mono ship
  inside the exe and load privately at startup on any machine.
- **Batch progress in the window title**, visible from the taskbar.
- **File-type icons** in the queue so mixed batches scan at a glance.
- **Drag and drop** files or a folder onto the window to queue them.
- **Live ETA + encode speed** in the status line during a run.
- **Keeps the PC awake** while encoding, and **flashes the taskbar** when the
  batch finishes so you can walk away.
- **Double-click** a finished file to open it; **right-click** a row to
  open / reveal / remove; shortcuts: Ctrl+O add, Delete remove, Enter start.
- Per-file size and percent saved shown when each file finishes.
- Partial output is deleted if a job is cancelled or fails.
- Last-used settings and window size are remembered between launches; an Open
  output folder button reveals the results.

## How it works
- **probe.py** · runs `ffprobe`, returns a `VideoInfo`, `recommend_settings()`
  picks defaults from the source, and `has_nvenc()` detects GPU encoding.
- **encoder.py** · `build_stages()` assembles the ffmpeg command(s) for the
  chosen encoder/mode (optionally a time segment for splitting);
  `video_bitrate_for_target()` and `suggest_parts()` do the size math;
  `run_encode()` runs a command and parses `-progress` for live progress.
- **app.py** · the CustomTkinter GUI: a scrollable job queue, shared settings,
  and a background encode thread that reports progress back through a queue.
- **models.py** · constants, the `Job` dataclass, `human_size`, `status_display`.
- **widgets.py** · the `QueueRow` list item.
- **sysutil.py** · Windows helpers (keep-awake, taskbar flash, resource paths).
- **theme.py** · brand palette, font resolution (falls back to Segoe UI /
  Consolas when the brand fonts aren't installed), and the CustomTkinter theme
  override.

## Tests
```powershell
pip install pytest
pytest -q
```
Covers the pure logic (recommendation, bitrate math, part counts, command
construction, helpers). No ffmpeg or display needed.

## Key setting: CRF
CRF is the quality knob for x265. Lower = better quality and a bigger file;
higher = smaller file. ~20 is near-transparent, ~28 is visibly compressed.
The defaults aim for "looks the same, noticeably smaller".
