# Changelog

The version is shown in the desktop app's window title. Newest first.

## 2026.11.08-r8

- **Live clock no longer takes over:** playing any preset, colour, stored mood or Off now stops the
  live clock. Before, it came back at the next minute.
- **Why the live clock works this way:** Clock A uses the computer's clock and sends the time
  to the cube every minute, so the app has to stay open. The cube has its own clock (Clock B), but
  it can only draw a clock face it still has stored, because those faces came from the old Tittle
  server. New **🔍 Find stored faces** button in 🕒 Clock tries face IDs 1 to 100. If it finds one,
  the cube shows it and keeps time by itself, with no app needed.

## 2026.11.08-r7

- **Orientation, take two:** r6 stood everything upright but upside down. The vertical axis is
  now flipped, so Pizza, Jellyfish and the clock face the right way. The 🔍 Debug tab's
  **🧭 Show test pattern** still checks it: blue floor, green wall facing you, red edge on the
  left, white dot top right. If your cube differs, tick *Upside down* or *Mirror left/right*.
  Settings saved by r6 are ignored, because they were made with the wrong default.
- **Save the cube's stored moods:** ♻️ Originals has a new **⭐ Saved presets** list. Press
  **What is it playing now?** while the cube shows an original mood (for example one started by an
  old schedule), then **⭐ Save ID above** and give it a name. One click plays it again later.
  The animation itself stays on the cube, so a factory reset would still erase it.
- **Windows Defender false alarm:** the Windows app is now a folder (`TittleLight-Windows.zip`)
  built with its own compiled PyInstaller bootloader, instead of a single self-extracting `.exe`.
  Single-file Python apps are a common false positive. If Defender still complains, see
  "Windows Defender" in the README.
- Added this changelog. Each GitHub release now shows its section.

## 2026.11.08-r6

- **Orientation:** the cube stores its LEDs front to back first, so presets lay on their backs.
  Frames are now converted for the cube when they are sent. Added the test pattern and the
  Upside down / Mirror / Old layout switches in 🔍 Debug.
- **No more slow reloads:** the app only recognised the cube's "I already have this" reply for
  emojis, so it re-sent presets in full every time. It now follows the official app's rule for
  every type.
- **12-hour clock:** the live clock has a 12-hour option, on by default. A purple bar on top means PM.
- **iPhone backups:** ♻️ Originals → **📱 Open iPhone backup…** reads an unencrypted Finder/iTunes
  backup, finds the Tittle app's files, plays what it can read and can copy the raw files out.
- The web app got the same orientation and reload fixes.
- First GitHub release with Windows and Mac apps.

## 2026.11.08-r5

- **Smooth animations:** presets are now 60 to 100 frames instead of 16, with dots gliding between
  LEDs and seamless loops. Added Smoothness (Light / Smooth / Ultra) and Speed controls.
- Added look-alikes of moods named in the original launch press: Sunset, Constellation,
  Snow Globe and Confetti. Fire is now "Fireplace".

## 2026.11.08-r4

- **Clock and music:** the official clock faces and visualizers came from the Tittle server, so
  asking for them timed out. Added a live clock that the app draws itself. The stored faces and
  visualizers only play if the cube kept a copy.
- "Official Presets" renamed **Look-alikes**, because they are not the originals.
- Uploads default to the raw chunk format that worked on Ben's cube.
- App version in the title bar; **📋 Copy log** and **💾 Save log…** in 🔍 Debug.

## Earlier (r1 to r3)

- Desktop app finished from Gemini's start: lamp, presets, 3D & Fairies with 8 new presets,
  ⏰ Schedule (cube-side overnight off and morning on), ♻️ Originals tools (stored IDs, scan,
  tittle.db import), 📁 Files (JSON, `.bin`, MagicaVoxel `.vox`), 🖼️ Picture tool, pizza easter egg.
