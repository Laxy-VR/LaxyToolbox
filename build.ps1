# Build "Laxy's Compressor" into a single self contained .exe.
# Bundles ffmpeg + ffprobe inside the .exe, so it runs on machines without them.
# Requires: pip install pyinstaller
# Output: dist\Laxy Compressor.exe

$ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source
$ffprobe = (Get-Command ffprobe -ErrorAction Stop).Source
Write-Host "Bundling ffmpeg:  $ffmpeg"
Write-Host "Bundling ffprobe: $ffprobe"

pyinstaller --noconfirm --onefile --windowed `
    --name "Laxy Compressor" `
    --icon laxy.ico `
    --collect-all customtkinter `
    --collect-all tkinterdnd2 `
    --add-binary "$ffmpeg;." `
    --add-binary "$ffprobe;." `
    --add-data "laxy.ico;." `
    --add-data "fonts;fonts" `
    app.py

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nBuilt self contained: dist\Laxy Compressor.exe"
    Write-Host "You can send this single file to anyone; no Python or ffmpeg needed."
} else {
    Write-Host "`nBuild FAILED (exit $LASTEXITCODE)."
}
