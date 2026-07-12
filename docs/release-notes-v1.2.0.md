# Laxy's Toolbox v1.2.0

The GIF tab grew into a small studio, videos can be rotated and subtitled, and
a batch of reliability fixes landed under the hood.

## New

- **GIF studio**: save loops as classic **GIF**, **animated WebP** (much
  smaller at better quality), or a silent **MP4 loop** (smallest). Scrub to
  your start frame with a slider and live previews of the clip's first and
  last frames. New speed control (0.25x to 4x), direction (forward, reverse,
  **boomerang**), and palette size (256/128/64 colors) for smaller GIFs.
- **Rotate/flip** on the Compress tab, for phone videos recorded sideways.
- **Burn in subtitles**: auto finds a matching .srt/.ass/.vtt next to each
  video, or pick a file, and burns it into the picture.
- **Normalize volume** option on the Audio tab.
- **Strip metadata** (EXIF, GPS) option on the Images tab for safer sharing.
- Every ready file in the queue shows a rough **predicted output size** that
  updates live as you change settings.

## Fixed

- Closing the app mid encode or mid download no longer leaves ffmpeg or
  yt-dlp running invisibly in the background; partial files are cleaned up.
- Videos with non ASCII names (Japanese, Korean, accented characters, common
  in downloaded titles) could fail to encode with a decoding error.
- The app now warns before replacing files that already exist in the output
  folder.
- "Reveal in folder" opens Explorer with the file highlighted.
- Settings are saved after every finished batch, not only on a clean exit.

## Under the hood

- CI verifies the bundled ffmpeg against a pinned checksum, and real ffmpeg
  smoke tests run on every push.
- The codebase was reorganized into focused modules with expanded tests
  (162 tests).
