#!/usr/bin/env bash
# Build "Laxy's Toolbox" for Linux as one self-contained AppImage.
# The Linux counterpart of build.ps1, and what CI runs for releases.
#
# Bundles ffmpeg + ffprobe (static, pinned) and gifsicle (built from pinned
# source) inside the AppImage, so it runs on machines without them.
# Every download is checked against a pinned SHA256 before it is used.
#
# Requires: python3 with pip, gcc, make, curl (and the app's requirements:
#   pip install -r requirements.txt pyinstaller==6.21.0)
# Output:   dist/Laxy.Toolbox-x86_64.AppImage
#           (./build.sh --tools-only: just fetch/build the pinned tools into
#            build-linux/tools, for running the ffmpeg smoke tests)
#
# Build on the OLDEST distro you want to support (CI uses Ubuntu 22.04): the
# AppImage needs at least the glibc of the machine it was built on.
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$PWD"
WORK="$ROOT/build-linux"
PYTHON="${PYTHON:-python3}"

# ffmpeg: BtbN's static GPL build of the 7.1 branch (same branch as the
# Windows build's 7.1.1; 8.x NVENC needs NVIDIA driver 610+). BtbN keeps these
# monthly snapshots for about two years.
FFMPEG_URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-07-31-14-10/ffmpeg-n7.1.5-12-g1fdbca85aa-linux64-gpl-7.1.tar.xz"
FFMPEG_SHA256="c1e6caf48923dd8e6bc5e54d51ba70c321175b8162ae9c414c392990e72f0e79"
# gifsicle has no official Linux binary: build the release source (cross
# checked against the GitHub tag v1.95 when pinned).
GIFSICLE_URL="https://www.lcdf.org/gifsicle/gifsicle-1.95.tar.gz"
GIFSICLE_SHA256="b2711647009fd2a13130f3be160532ed46538e762bfc0f020dea50618a7dc950"
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage"
APPIMAGETOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
# The runtime is the small launcher at the front of every AppImage.
# appimagetool would otherwise download an unpinned "continuous" one.
RUNTIME_URL="https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64"
RUNTIME_SHA256="2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d"

fetch() {  # fetch URL SHA256 DEST: download once, always verify
    local url="$1" sha="$2" dest="$3"
    if [ ! -f "$dest" ] || ! echo "$sha  $dest" | sha256sum -c --status; then
        echo "Downloading $(basename "$dest")"
        curl -fsSL -o "$dest.part" "$url"
        mv "$dest.part" "$dest"
    fi
    echo "$sha  $dest" | sha256sum -c --quiet || {
        echo "Checksum mismatch for $dest" >&2; rm -f "$dest"; exit 1; }
}

mkdir -p "$WORK/tools"
TOOLS="$WORK/tools"

# --- ffmpeg + ffprobe ---
fetch "$FFMPEG_URL" "$FFMPEG_SHA256" "$WORK/ffmpeg.tar.xz"
rm -rf "$WORK/ffmpeg-src" && mkdir -p "$WORK/ffmpeg-src"
tar -xJf "$WORK/ffmpeg.tar.xz" -C "$WORK/ffmpeg-src" --strip-components=1
cp "$WORK/ffmpeg-src/bin/ffmpeg" "$WORK/ffmpeg-src/bin/ffprobe" "$TOOLS/"

# --- gifsicle ---
fetch "$GIFSICLE_URL" "$GIFSICLE_SHA256" "$WORK/gifsicle.tar.gz"
rm -rf "$WORK/gifsicle-src" && mkdir -p "$WORK/gifsicle-src"
tar -xzf "$WORK/gifsicle.tar.gz" -C "$WORK/gifsicle-src" --strip-components=1
(cd "$WORK/gifsicle-src" && ./configure --disable-gifview --disable-gifdiff -q \
    && make -s -j"$(nproc)") >/dev/null
cp "$WORK/gifsicle-src/src/gifsicle" "$TOOLS/"
strip "$TOOLS/gifsicle" 2>/dev/null || true

# --- appimagetool + runtime ---
fetch "$APPIMAGETOOL_URL" "$APPIMAGETOOL_SHA256" "$TOOLS/appimagetool"
fetch "$RUNTIME_URL" "$RUNTIME_SHA256" "$TOOLS/runtime-x86_64"
chmod +x "$TOOLS/appimagetool" "$TOOLS"/ffmpeg "$TOOLS"/ffprobe "$TOOLS"/gifsicle

echo "Bundling: $("$TOOLS/ffmpeg" -version | head -1)"
echo "Bundling: $("$TOOLS/gifsicle" --version | head -1)"

# CI's test job only needs the pinned tools on PATH for the smoke tests.
if [ "${1:-}" = "--tools-only" ]; then
    echo "Tools ready in $TOOLS"
    exit 0
fi

# --- the app (PyInstaller, one folder: an AppImage is already one file) ---
rm -rf "$WORK/pyi" "$WORK/AppDir"
"$PYTHON" -m PyInstaller --noconfirm --onedir --windowed \
    --name "Laxy Toolbox" \
    --distpath "$WORK/pyi/dist" --workpath "$WORK/pyi/work" --specpath "$WORK/pyi" \
    --collect-all customtkinter \
    --collect-all tkinterdnd2 \
    --add-binary "$TOOLS/ffmpeg:." \
    --add-binary "$TOOLS/ffprobe:." \
    --add-binary "$TOOLS/gifsicle:." \
    --add-data "$ROOT/laxy.png:." \
    --add-data "$ROOT/fonts:fonts" \
    "$ROOT/app.py"

# --- AppDir ---
APPDIR="$WORK/AppDir"
mkdir -p "$APPDIR/usr/lib"
cp -a "$WORK/pyi/dist/Laxy Toolbox" "$APPDIR/usr/lib/laxy-toolbox"
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/laxy-toolbox/Laxy Toolbox" "$@"
EOF
chmod +x "$APPDIR/AppRun"
# StartupWMClass matches app.WM_CLASS, so the taskbar shows this icon.
cat > "$APPDIR/laxy-toolbox.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Laxy's Toolbox
Comment=Compress video, make GIFs, convert images and audio, download links
Exec=laxy-toolbox %F
Icon=laxy-toolbox
Categories=AudioVideo;Video;Graphics;
Terminal=false
StartupWMClass=Laxy-toolbox
EOF
cp "$ROOT/laxy.png" "$APPDIR/laxy-toolbox.png"
ln -sf laxy-toolbox.png "$APPDIR/.DirIcon"

# --- AppImage ---
mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/Laxy.Toolbox-x86_64.AppImage"
rm -f "$OUT"
# --appimage-extract-and-run: appimagetool is itself an AppImage, and this
# lets it run where FUSE isn't available (CI containers).
ARCH=x86_64 "$TOOLS/appimagetool" --appimage-extract-and-run \
    --runtime-file "$TOOLS/runtime-x86_64" --no-appstream \
    "$APPDIR" "$OUT"

echo
echo "Built self contained: $OUT ($(du -h "$OUT" | cut -f1))"
echo "Check it finds its bundled tools: \"$OUT\" --selftest selftest.json"
