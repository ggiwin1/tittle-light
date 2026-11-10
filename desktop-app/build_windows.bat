@echo off
REM Builds dist\TittleLight\TittleLight.exe (a folder app; single-file builds get flagged by antivirus).
REM Needs Python 3.10+ from python.org (tick "Add python.exe to PATH" when installing).
cd /d "%~dp0"
py -3 -m pip install --upgrade pyinstaller pillow pystray || python -m pip install --upgrade pyinstaller pillow pystray
py -3 -m PyInstaller --noconfirm --clean --onedir --windowed --name TittleLight --icon icon.png --paths . --hidden-import tittle_image --hidden-import pystray._win32 --add-data "icon.png;." --splash splash.png tittle_desktop.py || python -m PyInstaller --noconfirm --clean --onedir --windowed --name TittleLight --icon icon.png --paths . --hidden-import tittle_image --hidden-import pystray._win32 --add-data "icon.png;." --splash splash.png tittle_desktop.py
echo.
echo Done. Your app is dist\TittleLight\TittleLight.exe (keep the whole folder together)
pause
