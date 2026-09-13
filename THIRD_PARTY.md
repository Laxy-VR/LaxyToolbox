# Third party software

Laxy's Toolbox itself is MIT licensed (see [LICENSE](LICENSE)). The Windows
executable includes or uses the following software, each under its own
license.

## Bundled programs

These are separate programs that the app runs; they are not linked into it.

| Software | License | Source |
|---|---|---|
| **FFmpeg** 7.1.1 (`ffmpeg.exe`, `ffprobe.exe`), gyan.dev full build, which includes x264, x265, SVT-AV1, libaom, libwebp, and other libraries | GPL v3 | https://ffmpeg.org · build details: https://www.gyan.dev/ffmpeg/builds/ |
| **Gifsicle** 1.95 (`gifsicle.exe`) by Eddie Kohler, used for the lossy GIF option | GPL v2 | https://www.lcdf.org/gifsicle/ · Windows builds: https://eternallybored.org/misc/gifsicle/ |

## Bundled libraries

| Software | License | Source |
|---|---|---|
| **Python** runtime and standard library | PSF License | https://www.python.org |
| **Tcl/Tk** 8.6 | Tcl/Tk License (BSD style) | https://www.tcl-lang.org |
| **CustomTkinter** | MIT | https://github.com/TomSchimansky/CustomTkinter |
| **darkdetect** (used by CustomTkinter) | BSD 3-Clause | https://github.com/albertosottile/darkdetect |
| **packaging** (used by CustomTkinter) | Apache 2.0 or BSD 2-Clause | https://github.com/pypa/packaging |
| **tkinterdnd2** | MIT | https://github.com/pmgagne/tkinterdnd2 |
| **tkdnd** 2.10.1, the drag and drop library tkinterdnd2 wraps, by Georgios Petasis | BSD style | https://github.com/petasis/tkdnd |
| **Pillow**, including the image libraries its wheels ship (listed in Pillow's own license file) | MIT-CMU | https://python-pillow.org |
| **Fonts:** DM Sans, JetBrains Mono, IBM Plex Mono | SIL Open Font License 1.1 (see [fonts/OFL.txt](fonts/OFL.txt)) | |

## Downloaded on first use (not bundled)

| Software | License | Source |
|---|---|---|
| **yt-dlp**, fetched from its official GitHub releases (checksum verified) for the Download tab, and updated in place | Unlicense (public domain); its Windows exe includes components under their own licenses | https://github.com/yt-dlp/yt-dlp |

## Build tooling

| Software | License | Source |
|---|---|---|
| **PyInstaller**, which packages the app into one exe | GPL v2 with the Bootloader Exception, which allows distributing the produced executable under any license | https://pyinstaller.org |
