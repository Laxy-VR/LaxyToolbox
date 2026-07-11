# Changelog

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
