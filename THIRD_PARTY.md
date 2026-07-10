# Third party software

Laxy's Compressor itself is MIT licensed (see LICENSE). The distributed
Windows executable bundles or uses the following software, each under its own
license:

## Bundled in the executable
- **FFmpeg (ffmpeg.exe, ffprobe.exe)** · GPL v3 build by gyan.dev, including
  x264 and x265. FFmpeg is a separate program invoked by this app, not linked
  into it. Source code and build details: https://ffmpeg.org and
  https://www.gyan.dev/ffmpeg/builds/
- **CustomTkinter** · MIT · https://github.com/TomSchimansky/CustomTkinter
- **tkinterdnd2 / tkdnd** · MIT · https://github.com/pmgagne/tkinterdnd2
- **Pillow** · MIT-CMU (HPND) · https://python-pillow.org
- **Python** and its standard library · PSF license · https://python.org
- **Fonts**: DM Sans, JetBrains Mono, IBM Plex Mono · SIL Open Font License
  1.1 (see fonts/OFL.txt)

## Fetched at first use (not bundled)
- **yt-dlp** · Unlicense (public domain) · downloaded from its official
  GitHub releases for the link download feature and updated in place ·
  https://github.com/yt-dlp/yt-dlp

## Build tooling
- **PyInstaller** · GPL v2 with the Bootloader Exception, which permits
  distributing the produced executable under any license ·
  https://pyinstaller.org
