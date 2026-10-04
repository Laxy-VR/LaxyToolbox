# Third party software

Laxy's Toolbox itself is MIT licensed (see [LICENSE](LICENSE)). The Windows
executable and the Linux AppImage include or use the following software,
each under its own license.

## Bundled programs

These are separate programs that the app runs; they are not linked into it.

| Software | License | Source |
|---|---|---|
| **FFmpeg** 7.1.1 (`ffmpeg.exe`, `ffprobe.exe`), gyan.dev full build, which includes x264, x265, SVT-AV1, libaom, libwebp, and other libraries | GPL v3 | https://ffmpeg.org · build details: https://www.gyan.dev/ffmpeg/builds/ |
| **Gifsicle** 1.95 (`gifsicle.exe`) by Eddie Kohler, used for the lossy GIF option | GPL v2 | https://www.lcdf.org/gifsicle/ · Windows builds: https://eternallybored.org/misc/gifsicle/ |
| **FFmpeg** 7.1.5 (`ffmpeg`, `ffprobe`, Linux AppImage), BtbN static GPL build, which includes x264, x265, SVT-AV1, libaom, libwebp, and other libraries | GPL v3 | https://ffmpeg.org · build details: https://github.com/BtbN/FFmpeg-Builds |
| **Gifsicle** 1.95 (`gifsicle`, Linux AppImage), built from the release source | GPL v2 | https://www.lcdf.org/gifsicle/ |

## Bundled libraries

| Software | License | Source |
|---|---|---|
| **Python** runtime and standard library | PSF License | https://www.python.org |
| **Tcl/Tk** 8.6 | Tcl/Tk License (BSD style) | https://www.tcl-lang.org |
| **libfontconfig** (Linux; loads the bundled fonts for the app only, from the system) | MIT style | https://www.freedesktop.org/wiki/Software/fontconfig/ |
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
| **yt-dlp**, fetched from its official GitHub releases (checksum verified) for the Download tab, and updated in place: `yt-dlp.exe` on Windows, `yt-dlp_linux` on Linux | Unlicense (public domain); its standalone builds include components under their own licenses | https://github.com/yt-dlp/yt-dlp |

## Build tooling

| Software | License | Source |
|---|---|---|
| **PyInstaller**, which packages the app into one exe (and the folder inside the AppImage) | GPL v2 with the Bootloader Exception, which allows distributing the produced executable under any license | https://pyinstaller.org |
| **appimagetool** 1.9.1, which packs the Linux AppImage | MIT | https://github.com/AppImage/appimagetool |
| **AppImage type 2 runtime** (release 20251108), the small launcher at the front of the AppImage; it includes FUSE and squashfs libraries under their own licenses | MIT | https://github.com/AppImage/type2-runtime |
