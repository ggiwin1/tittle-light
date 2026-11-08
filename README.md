# Tittle Light Kit for Ben

Everything you need to run your Tittle Light cube now that the official cloud app is dead.
Your computer and the cube must be on the same Wi-Fi.

## Start here: the desktop app

**Easiest:** download the app from the [Releases page](https://github.com/ggiwin1/tittle-light/releases/latest) (`TittleLight-Windows.zip`, or the Mac zip for Apple Silicon or Intel), or use the `apps/` folder if this kit has one:
- **Windows:** right-click `TittleLight-Windows.zip`, choose **Extract All**, open the `TittleLight`
  folder and double-click `TittleLight.exe`. Keep the files in that folder together. If Windows says
  "Windows protected your PC", click **More info**, then **Run anyway**.
- **Mac:** unzip the Mac zip. The first time, right-click `TittleLight.app` and
  choose **Open**. The app isn't signed, so a plain double-click gets blocked.

**Or run the Python file** in `desktop-app/` (always works, and gets updates first):
1. Install Python 3 from https://www.python.org/downloads/. On Windows, tick
   **"Add python.exe to PATH"** in the installer.
2. Double-click `desktop-app/tittle_desktop.py`, or open a terminal in that folder and run
   `python tittle_desktop.py`.
3. Optional: for the 🖼️ Picture tab, run `pip install pillow` once.

The app looks for the cube on its own. If it can't find it, type the cube's IP address
next to **IP:** and press **Sniff / Connect**. Your router's list of connected devices
shows the cube's IP.

### What's in the app

| Tab | What it does |
| --- | --- |
| 💡 Lamp | Any solid colour and brightness. The red **Off** button is at the top. |
| 🎨 Look-alikes | Fireplace, Wave, Rain, Snow, Sparkle, Fireworks, Vortex, plus Sunset, Constellation, Snow Globe and Confetti (names from the original Indiegogo campaign). These are **not** the real originals, only look-alikes (see "Original animations" below). |
| ✨ 3D & Fairies | Fairies 2.0 and 3D effects, plus 8 new ones (🆕): Aurora, Lava Lamp, Spiral Galaxy, Heartbeat, Warp Speed, Plasma, Bouncing Balls, Rainbow Spin. |
| ⏰ Schedule | Turns the cube **off every night** and **back on every morning**. The timer is saved on the cube, so it works with the app closed. |
| 🖼️ Picture | Turns any photo into a light mode, either directly or by having an AI (ChatGPT, Gemini, Claude…) design it. |
| 📁 Files | Opens animations from the web editor (`.json`, `.bin`) and MagicaVoxel `.vox` files, and saves them as JSON. |
| ♻️ Originals | Tools for finding the original animations (see below). |
| 🕒 Clock | **Live clock** in 12-hour (a purple bar on top means PM) or 24-hour time. The app draws the time and updates it every minute while the app is open, or a clock face the cube still has saved from the old app. |
| 🎵 Music | Starts a visualizer the cube still has saved from the old app. The new visualizers were never in the phone app, so this only works if the cube kept one. |
| 🔍 Debug | **🧭 Show test pattern** checks that animations stand the right way up (blue floor, green wall facing you, red edge on the left). If not, tick *Upside down* or *Mirror left/right*. It also shows every message sent to and from the cube. If something doesn't work, press **📋 Copy log** and paste it to Chad, or **💾 Save log…** and send him the file. |

### Smoothness and speed

The 🎨 and ✨ tabs have a **Smoothness** choice and a **Speed** slider. **Smooth** (the default) sends 60 to
100 frames per animation. If uploads feel slow or fail, pick **Light**. If the cube copes, try **Ultra**.
The Debug log shows how long each upload took.

## Original animations

The official moods and emojis were never inside the phone app. It downloaded them from the
Tittle server, which is now shut down. Two places may still have them:

1. **The cube.** In ♻️ Originals, press **What is it playing now?**. If the cube is playing
   an old mood, this shows the mood's number, and **Play stored** brings it back. **Scan**
   searches a range of numbers for other moods the cube has saved.
2. **An old phone that had the Tittle app.**
   - **iPhone (no jailbreak needed):** connect it to a computer and make a backup with
     **Encrypt local backup turned off**. Use Finder on a Mac, or iTunes / the Apple Devices app on Windows.
     Then, in ♻️ Originals, press **📱 Open iPhone backup…** and pick the backup folder:
     - Mac: `~/Library/Application Support/MobileSync/Backup/<long ID>`
     - Windows: `%APPDATA%\Apple Computer\MobileSync\Backup\<long ID>` or `%USERPROFILE%\Apple\MobileSync\Backup\<long ID>`

     The app finds the Tittle app's files in the backup, lists any animations it can read, and
     offers to copy the raw files out. Press **💾 Save all…** to keep every animation as JSON, so you
     don't need the phone again. If something is missing, zip the copied files and send them to Chad.
   - **Android:** the app's `tittle.db` needs a rooted phone or an adb backup. Open it with
     **Open tittle.db…**.

## Other things in this kit

- `image-tool/`: the picture tool on its own (`python tittle_image.py`, needs `pip install
  pillow`), plus `AI_PROMPT.md`, the text to paste into any AI to design a new light mode.
  The desktop app's 🖼️ Picture tab does the same thing.
- `web-app/`: a browser version with a 3D voxel editor for drawing your own animations
  frame by frame. It needs Node.js 20 or newer:
  1. Open a terminal in `web-app/` and run `npm install` (only the first time).
  2. Run `npm run dev` and open http://localhost:3000.
  3. To use it from a phone on the same Wi-Fi, start it with `HOST=0.0.0.0 npm run dev`
     instead, then open `http://<this computer's IP>:3000` on the phone.
- `desktop-app/build_windows.bat` and `desktop-app/build_mac.sh`: these rebuild the
  double-click apps from the Python file. Run the `.bat` on Windows or `bash build_mac.sh`
  on a Mac. The result appears in `desktop-app/dist/`.

## Windows Defender

Apps made from Python files are sometimes flagged as a "Trojan" by mistake, because malware
authors use the same packaging tool. Since r7 the Windows app is built in a way that avoids
most of these false alarms. If Defender still quarantines it:
- run the Python file instead (see above), which never triggers this, or
- restore it from **Windows Security → Protection history → Allow**, and report the false
  positive to Microsoft at https://www.microsoft.com/wdsi/filesubmission so it gets cleared for everyone.

## Good to know

- The window title shows the app version. This kit is **2026.11.08-r7**. See `CHANGELOG.md` for what changed in each version. If you see no ⏰ Schedule tab,
  you are running an older copy: delete it and use the one in this kit.
- Nothing here has been tested on a real cube yet, only on a simulator. If something
  misbehaves, the 🔍 Debug tab shows what the cube replied.
- Animations you play are uploaded into the cube and loop there on their own, so you can
  close the app afterwards.
- Psst: click the small credit in the bottom-right corner of the desktop app. 🍕
