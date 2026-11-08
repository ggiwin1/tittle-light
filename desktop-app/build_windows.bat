@echo off
REM Builds dist\TittleLight.exe (a single double-click app for Windows).
REM Needs Python 3.10+ from python.org (tick "Add python.exe to PATH" when installing).
cd /d "%~dp0"
py -3 -m pip install --upgrade pyinstaller pillow || python -m pip install --upgrade pyinstaller pillow
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed --name TittleLight --icon icon.png ^
    --paths . --hidden-import tittle_image tittle_desktop.py || python -m PyInstaller --noconfirm --clean --onefile --windowed --name TittleLight --icon icon.png --paths . --hidden-import tittle_image tittle_desktop.py
echo.
echo Done. Your app is dist\TittleLight.exe
pause
