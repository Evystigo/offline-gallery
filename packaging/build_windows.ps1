# Build the Windows app: packaging\build_windows.ps1   (run from the repo root, in PowerShell)
# Output: dist\Gallery\Gallery.exe  and  dist\Gallery-windows.zip
$ErrorActionPreference = "Stop"

if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -q -e ".[build]"
.\.venv\Scripts\python.exe -m PyInstaller packaging/gallery.spec --noconfirm --distpath dist --workpath build

# Prove the bundle works before shipping it (headless; writes a report because the exe has no console).
$env:QT_QPA_PLATFORM = "offscreen"
$report = Join-Path $env:TEMP "gallery-selftest.txt"
$p = Start-Process -FilePath dist\Gallery\Gallery.exe -ArgumentList "--selftest", "`"$report`"" -Wait -PassThru -WindowStyle Hidden
if ($p.ExitCode -ne 0) { throw "Self-test failed (exit $($p.ExitCode))" }
Get-Content $report

Compress-Archive -Path dist\Gallery -DestinationPath dist\Gallery-windows.zip -Force
Write-Host "Done: dist\Gallery-windows.zip"
