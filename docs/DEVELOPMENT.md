# Development notes

How the app is put together, how a change ships, and the gotchas that were
learned the hard way. Read this before touching the ffmpeg pin or the release
workflow.

## Architecture

| Module | Responsibility |
|---|---|
| `app.py` | The CustomTkinter GUI: the `App` class, tabs, queue, settings, and the background workers. |
| `models.py` | Constants (tabs, modes, dropdown options), the `Job` dataclass, small pure helpers (`status_display`, `unique_path`, `kind_icon`). |
| `encoder.py` | Builds and runs every ffmpeg command: `build_stages` (video, all codecs and modes), `build_gif_stages`, `build_image_stages`, `build_audio_stages`, `build_cut_stages`, plus the size math (`video_bitrate_for_target`, `suggest_parts`). |
| `probe.py` | Reads metadata via ffprobe (`probe_video` → `VideoInfo`), recommends settings, resolves the bundled ffmpeg/ffprobe paths, detects GPU encoders (`gpu_codecs`, `nvenc_works`), extracts preview frames. |
| `downloader.py` | yt-dlp integration: fetch and self update the binary, build the download command, parse progress, locate the finished file. |
| `widgets.py` | `QueueRow`, the per file list item. |
| `sysutil.py` | Windows helpers: keep awake, taskbar flash, bundled resource paths, GitHub release lookup for the update check. |
| `theme.py` | Brand palette, private font loading, CustomTkinter theme override. |

### Threading model

Tkinter is not thread safe, so the app follows one rule everywhere: **worker
threads never touch widgets**. Long work (probing, encoding, downloading, the
GPU test, the update check) runs in daemon threads that post tuples onto
`App.msg_queue`; the main thread polls it every 100 ms (`_poll_queue` →
`_dispatch`) and updates the UI. Settings are snapshotted on the main thread
when a run starts, so mid run UI changes cannot corrupt a batch.

### Layout

The bottom bar (progress, status, Start/Cancel) is packed `side="bottom"`
first, so it is always visible. The middle section (queue, tabs, settings) is
built by `_build_middle(parent)`; at startup the app measures its required
height against the screen and, when it does not fit (small laptops, high DPI
scaling), rebuilds the middle inside a `CTkScrollableFrame`
(`_make_middle_scrollable`).

### Tabs and modes

The five tabs map to internal modes via `App._mode()`: the Compress tab uses
the segmented sub mode (quality/target/split), while GIF, Images, Audio, and
Download tabs are modes of their own. Each tab only processes its own kind of
file (`models.is_image` / `is_audio`); everything else in the queue is left
untouched.

## How a change ships

1. Commit and push to `main`. CI runs the test suite on a Windows runner.
2. Bump `APP_VERSION` in `models.py`, commit, push.
3. `git tag vX.Y.Z && git push origin vX.Y.Z`
4. On GitHub: create a release from that tag. CI builds the exe (downloading
   its own pinned ffmpeg) and attaches it to the release.
5. Every installed copy checks the latest release at startup and shows an
   update link in the header when it is newer than `APP_VERSION`.

**Releases build from the workflow as it exists at the tagged commit.** A tag
placed before a workflow fix will faithfully rebuild the old, broken recipe.
When in doubt, verify before publishing:

```powershell
git show vX.Y.Z:.github/workflows/ci.yml | Select-String ffmpeg
git show vX.Y.Z:models.py | Select-String APP_VERSION
```

A sanity check that has caught real bugs three times: **compare the release
exe size against a local build**. Unexplained size differences mean different
ingredients.

## Gotchas (each one cost a broken release or a debugging session)

### ffmpeg pinning
- CI must bundle gyan's **full** build, not essentials: essentials lacks
  `libsvtav1`, silently breaking CPU AV1 encoding (v1.0 shipped this bug).
- The pin is **7.1.1** deliberately: ffmpeg 8.x NVENC requires NVIDIA driver
  610+, which would silently break GPU encoding for most users (v1.0.1
  shipped this bug). When bumping the pin, run the encode matrix and a real
  NVENC encode on actual hardware first.
- Local builds bundle whatever `Get-Command ffmpeg` finds. A polluted global
  Python also bloats local exes (PyInstaller bundles importable extras like
  numpy); the CI exe from a clean environment is the canonical artifact.

### yt-dlp
- `--ignore-config` is mandatory: a user's own yt-dlp config (for example
  `-f worst`) would otherwise hijack every download on every site.
- `--print` implies quiet, which suppresses progress lines; `--progress`
  forces them back on.
- The frozen yt-dlp exe drops non ASCII characters from its piped output, so
  the printed file path is unreliable for unicode titles;
  `newest_media_file()` finds the finished file by timestamp as a fallback.
- A stale yt-dlp gets quietly served low resolutions by sites (no error, just
  a pixelated file). `update_ytdlp_if_stale()` self updates before
  downloading when the local copy is over a week old.

### GPU encoding
- Encoder presence in the ffmpeg build says nothing about the machine: AMD
  and Intel GPUs, or old NVIDIA drivers, fail at encode time. `nvenc_works()`
  runs a real one frame test encode in the background on first launch and
  caches the verdict in the config (`nvenc_ok`).

### Color and HDR
- Everything pins `yuv420p` for playability, **except** 10 bit sources going
  to H.265/AV1, which keep 10 bit (`yuv420p10le` / `p010le` on NVENC).
- HDR (PQ/HLG) squeezed to 8 bit SDR without tone mapping looks washed out;
  `encoder.TONEMAP` (a zscale + hable chain) handles H.264 and GIF outputs
  from HDR sources.

### Misc
- One quality slider maps across codec scales via `CODECS[...]["crf_off"]`
  (x265 CRF 23 ≈ x264 CRF 19 ≈ SVT-AV1 CRF 30).
- x265 two pass uses `-x265-params pass=N`; x264 uses native `-pass N`;
  SVT-AV1 target mode is single pass ABR.
- Output paths are deduplicated per batch (`unique_path`) so same named
  inputs cannot overwrite each other's results.
- The config lives at `~/.laxy_compressor.json`; the last download log at
  `%LOCALAPPDATA%\LaxyCompressor\last_download.log`.
