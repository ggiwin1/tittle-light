#!/bin/bash
# Builds dist/TittleLight.app and dist/TittleLight-mac.zip (a double-click app for macOS).
# Needs Python 3.10+ from python.org (its Tk works best). Run:  bash build_mac.sh
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
"$PY" -m venv .build-venv
. .build-venv/bin/activate
pip install --upgrade pip pyinstaller pillow
pyinstaller --noconfirm --clean --windowed --name TittleLight --icon icon.png \
    --osx-bundle-identifier com.claudeandchad.tittlelight \
    --paths . --hidden-import tittle_image tittle_desktop.py
cd dist && ditto -c -k --keepParent TittleLight.app TittleLight-mac.zip
echo
echo "Done. Your app is dist/TittleLight.app (zipped: dist/TittleLight-mac.zip)."
echo "The app is not signed: the first time, right-click it and choose Open."
