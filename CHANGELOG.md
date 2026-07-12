# Changelog

## v1.2.0 · 2026-07-12
- **GIF studio**: the GIF tab can now save loops as classic GIF, **animated
  WebP** (much smaller at better quality), or a silent **MP4 loop** (smallest).
  New scrubber slider with live previews of the clip's first AND last frame,
  playback speed (0.25x to 4x), direction (forward, reverse, **boomerang**),
  and a palette size choice (256/128/64 colors) for smaller GIFs.
- **Rotate/flip** on the Compress tab for sideways phone videos.
- **Burn in subtitles**: auto finds a matching .srt/.ass/.vtt next to each
  video (or pick a file) and burns it into the picture.
- **Normalize volume** option on the Audio tab.
- **Strip metadata** option on the Images tab (EXIF, GPS) for safer sharing.
- Every ready file in the queue now shows a rough **predicted output size**
  that updates live as you change settings.
- Fixed: closing the app mid encode or mid download no longer leaves ffmpeg
  or yt-dlp running invisibly in the background; partial output files are
  cleaned up too.
- Fixed: videos with non ASCII names (Japanese, Korean, accented characters,
  common in downloaded titles) could fail to encode with a decoding error.
- The app now warns before replacing files that already exist in the output
  folder (from an earlier run, for example).
- "Reveal in folder" now opens Explorer with the file highlighted instead of
  just opening the folder.
- Settings are saved after every finished batch, not only on a clean exit.
- Under the hood: CI verifies the bundled ffmpeg download against a pinned
  checksum, real ffmpeg smoke tests run on every push, job planning moved to
  its own tested module, Pillow/tkinterdnd2 are properly pinned, and the
  2,300 line app.py was split into seven focused gui modules (build, queue,
  downloads, notes, settings, run, config).

## v1.1.0 · 2026-07-11
- Renamed to **Laxy's Toolbox**, since it now does far more than compress.
- **Setting presets**: one-click bundles (Discord under 500 MB, Discord under
  10 MB, top quality archive, smallest file with AV1), plus save your own
  named presets and reload them any time.
- **Tooltips** on the settings that a newcomer won't know (codec, quality,
  speed, audio, resolution, cookies, GPU), in plain language.
- **Queue thumbnails**: each row shows a small preview frame of the file.
- **Gentler error messages**: a failed job now explains what went wrong in one
  plain sentence (disk full, file in use, no internet, private or age
  restricted video, format not available, and more) instead of a raw log line.
- **Playlist downloads**: a Whole playlist toggle on the Download tab grabs
  every video a link points to, adding each to the queue as it finishes.
- **Queue tidying**: a Clear finished button removes completed rows, and you
  can reorder the queue with Move up / Move down (or Alt+Up / Alt+Down).

## v1.0.5 · 2026-07-11
- Fixed downloads being stuck at 360p on machines without ffmpeg installed:
  yt-dlp needs ffmpeg to merge HD video and audio streams, and it could not
  see the copy bundled inside the app. Downloads now always use the bundled
  ffmpeg, independent of what is installed on the machine.

## v1.0.4 · 2026-07-11
- New Cookies option on the Download tab: borrow a browser's logged in
  session (Firefox, Edge, or Chrome) for sites that withhold HD formats or
  require login. The low quality warning now points at it.

## v1.0.3 · 2026-07-11
- Small screen support: the bottom bar (progress and action buttons) is now
  pinned and always visible, and the middle section scrolls when the screen
  is too short for it (small laptops, high DPI scaling).

## v1.0.2 · 2026-07-11
- Fixed release packaging: CI now bundles the full ffmpeg build pinned to
  7.1.1 (v1.0 lacked CPU AV1 encoding; v1.0.1 shipped ffmpeg 8.1.2 whose
  NVENC requires NVIDIA driver 610+ and broke GPU encoding on most machines).
- GPU encoding is now verified with a real test encode before the GPU option
  is offered, so machines with AMD/Intel GPUs or old NVIDIA drivers fall back
  to CPU cleanly instead of failing.

## v1.0.1 · 2026-07-11 (withdrawn)
- Release packaging attempt; shipped an ffmpeg whose NVENC required a newer
  NVIDIA driver than most machines have. Replaced by v1.0.2.

## v1.0 · 2026-07-11 (withdrawn)
- First public release of Laxy's Compressor: batch video compression
  (H.265/AV1/H.264 on CPU or NVIDIA GPU · best quality, target size, split to
  fit · trim and lossless cut · HDR aware), GIF maker with live preview,
  image conversion (WebP/AVIF/JPEG), audio extraction (MP3/M4A), and link
  downloads via self updating yt-dlp. Self contained exe with bundled ffmpeg
  and brand fonts, in app update notifications.
