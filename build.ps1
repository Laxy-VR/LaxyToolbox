# Build "Laxy's Toolbox" into a single self contained .exe.
# Bundles ffmpeg + ffprobe inside the .exe, so it runs on machines without them.
# Requires: pip install pyinstaller
# Output: dist\Laxy Toolbox.exe

$ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source
$ffprobe = (Get-Command ffprobe -ErrorAction Stop).Source
$gifsicle = (Get-Command gifsicle -ErrorAction Stop).Source
Write-Host "Bundling ffmpeg:   $ffmpeg"
Write-Host "Bundling ffprobe:  $ffprobe"
Write-Host "Bundling gifsicle: $gifsicle"

pyinstaller --noconfirm --onefile --windowed `
    --name "Laxy Toolbox" `
    --icon laxy.ico `
    --collect-all customtkinter `
    --collect-all tkinterdnd2 `
    --add-binary "$ffmpeg;." `
    --add-binary "$ffprobe;." `
    --add-binary "$gifsicle;." `
    --add-data "laxy.ico;." `
    --add-data "fonts;fonts" `
    app.py

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nBuilt self contained: dist\Laxy Toolbox.exe"
    Write-Host "You can send this single file to anyone; no Python or ffmpeg needed."
} else {
    Write-Host "`nBuild FAILED (exit $LASTEXITCODE)."
}
