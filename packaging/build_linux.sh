#!/usr/bin/env bash
# Build the Linux app: packaging/build_linux.sh   (run from the repo root)
# Output: dist/Gallery/Gallery  and  dist/Gallery-linux.tar.gz
set -euo pipefail

python3 -m venv .venv
.venv/bin/python -m pip install -q -e ".[build]"
.venv/bin/python -m PyInstaller packaging/gallery.spec --noconfirm --distpath dist --workpath build

# Prove the bundle works before shipping it (headless).
QT_QPA_PLATFORM=offscreen dist/Gallery/Gallery --selftest

tar -C dist -czf dist/Gallery-linux.tar.gz Gallery
echo "Done: dist/Gallery-linux.tar.gz"
echo "Optional: copy packaging/gallery.desktop to ~/.local/share/applications and edit its Exec path."
