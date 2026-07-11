# Laxy's Toolbox

A batch media toolbox for Windows: compress videos, make GIFs, convert images
and audio, and download from links. One portable exe, no install.

![Laxy's Toolbox](docs/screenshot.png)

## Download

Grab `Laxy.Toolbox.exe` from the
[latest release](https://github.com/Laxy-VR/LaxyToolbox/releases/latest) and
double click it. No Python, no ffmpeg, no installer needed.

> **First launch:** Windows SmartScreen may warn about an unknown app because
> the exe is not code signed. Click **More info · Run anyway**. The app also
> checks for new versions at startup and shows a link in the header when one
> is available.
>
> **Antivirus:** a VirusTotal scan shows 2 of 68 engines flagging the exe
> (Bkav and McAfee's generic scanner), with generic, hash named "detections".
> This is the usual false positive for an unsigned app that unpacks Python and
> ffmpeg at runtime. Every major engine (Microsoft Defender, Kaspersky,
> BitDefender, ESET, Sophos) reports it clean. The build is produced in the
> open by GitHub Actions from the tagged commit, so you can read exactly what
> goes into it.

## What it does

**🎬 Compress** · Re-encode videos to **H.265** (recommended), **AV1**
(smallest), or **H.264** (max compatibility), on CPU or NVIDIA GPU. Three
modes:
- **Best quality** picks settings from the video's own metadata for the
  smallest file with no visible quality loss, and predicts the output size.
- **Target size** fits any video under a limit (500 MB for Discord Nitro,
  for example) and warns if the result lands over.
- **Split to fit** cuts a long video into parts that each fit under the limit.

Optional **Trim** (start/end seconds) on any mode, a **Cut only** checkbox for
instant lossless trimming, and a **Remove audio** option for gameplay clips.
HDR videos keep their 10 bit color on H.265/AV1 and are properly tone mapped
otherwise.

**🎞 GIF** · Turn a clip into a GIF with a live preview of your chosen start
frame, tuned palettes, and a dithering choice. Also shrinks existing GIFs.

**🖼 Images** · Batch convert PNG/JPEG/BMP to **WebP**, **AVIF**, or **JPEG**
at three quality levels, with optional resizing that never upscales by
accident.

**🎵 Audio** · Extract the soundtrack from any video, or convert audio files,
to MP3 or M4A.

**🌐 Download** · Paste a link from YouTube, Twitter, and most sites. Pick a
max resolution or grab audio only, or turn on **Whole playlist** to fetch
every video a link points to. Downloads land in your output folder and are not
re-compressed automatically (sites already compress their videos); right click
one to queue it. DRM protected content is not supported.

**Everywhere:** drag and drop files or folders, mixed batches, thumbnail
previews and live progress with speed and time remaining, per file savings and
batch totals, one-click **presets** for common jobs, plain-language tooltips,
clear error messages, queue reordering, and a bottom bar that stays visible on
any screen size.

## FAQ

- **A download failed or came out low quality.** The downloader (yt-dlp)
  updates itself automatically, and the app shows the actual resolution that
  arrived. If a site keeps serving low quality (often stuck at 360p), it distrusts
  your network: set the **Cookies** option on the Download tab to a browser
  you are signed in with, or retry later. The full log of the last download is in
  `%LOCALAPPDATA%\LaxyCompressor\last_download.log`.
- **The GPU option is missing.** The app verifies GPU encoding with a real
  test encode on first launch. No NVIDIA GPU (or a very old driver) means the
  option is hidden and everything runs on CPU.
- **Compressing a downloaded video makes it bigger.** Platform videos are
  already heavily compressed; the app tells you this in its notes. Compress
  your own recordings, not re-downloads, for real savings.

## Development

Python 3.10+, [ffmpeg](https://ffmpeg.org) full build on PATH.

```powershell
pip install -r requirements.txt
python app.py          # run from source
pip install pytest
pytest -q              # 89 tests, no ffmpeg or display needed
pip install pyinstaller
./build.ps1            # build the standalone exe into dist/
```

Architecture, the release process, and hard won gotchas (ffmpeg pinning,
yt-dlp quirks) are documented in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
Version history is in [CHANGELOG.md](CHANGELOG.md).

## License

MIT (see [LICENSE](LICENSE)). The exe bundles third party software under
their own licenses, listed in [THIRD_PARTY.md](THIRD_PARTY.md).
