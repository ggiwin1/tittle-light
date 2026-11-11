# Finding the original animations on an old iPhone (do-it-yourself)

The Tittle app kept a copy of every mood and emoji it downloaded. On an iPhone that copy sits in
the app's private folder. You can look at it yourself through a computer backup. Nobody else needs
access, and this app never reads your backups.

## 1. Make a backup of the old iPhone

- **Mac:** connect the phone and open Finder. Click the phone in the sidebar, then **Back Up Now**.
- **Windows:** use the Apple Devices app (or iTunes). Click the phone, then **Back Up Now**.

You can keep **Encrypt local backup** turned on. The tools below open encrypted backups on your own
computer if you type the backup password.

Backups are stored here, one folder per device (a long string of letters and numbers):

| System | Folder |
| --- | --- |
| Mac | `~/Library/Application Support/MobileSync/Backup/` |
| Windows (Apple Devices or Microsoft Store iTunes) | `%USERPROFILE%\Apple\MobileSync\Backup\` |
| Windows (iTunes from apple.com) | `%APPDATA%\Apple Computer\MobileSync\Backup\` |

Inside a backup, files are stored under scrambled names. You need a backup browser to see real
file names.

## 2. Open the backup with a backup browser

Any of these works and runs only on your computer:
- **iMazing** (Mac/Windows). The free version can browse backups and export app files.
- **iBackup Viewer** (Mac/Windows), in "Raw Files" / expert mode.
- **iMobie / AnyTrans** or similar tools, which have an "App data" view.

## 3. Find the Tittle app's folder

In the backup browser, look under **Apps** or **App Domains** for the Tittle app. In raw view, the
folder is called `AppDomain-<bundle ID>`. The Android app was `com.clarityhk.tittle`, so the iPhone
app's bundle ID most likely contains **tittle** or **clarityhk**. Search for either word.

## 4. Which files probably hold the presets

The iPhone app's file layout is not known, so check these places inside the app's folder, biggest
files first:

- `Library/Application Support/`: Core Data stores (`*.sqlite`, plus `-wal`/`-shm` files next to them)
- `Documents/`: `*.sqlite`, `*.db`, `*.realm`, `*.json`
- `Library/Preferences/*.plist`: settings, possibly the list of downloaded animations

Note: `Library/Caches/` is **never** included in backups. If the app kept its downloads only there,
the backup won't have them, and the old phone itself is the only copy.

How to recognise the animation data:
- The database has tables whose names contain *animation*, *frame*, *emoji* or *mood*.
  (Core Data tables start with `Z`, for example `ZANIMATION` or `ZFRAME`.)
- In the Android app, each frame was text like `{"12":[255,128,0,1], ...}`: an LED number (0 to 511)
  followed by red, green, blue. Each animation also had an ID, a name, a version and a time interval.
- Moods were in group 1. Other groups were emojis.

## 5. Play them (optional, offline)

Export the Tittle app's whole folder (or just the database file and its `-wal`/`-shm` files) out of
the backup browser into a folder. Then, in the desktop app, open **♻ Originals → 📂 Open copied
folder…** and pick that folder (or **📂 One database…** for a single `.sqlite` / `.db` file). The app
reads only what you pick, on your computer, and lists every animation it recognises. **💾 Save all…** keeps them as JSON files that you can replay forever.

If the app doesn't recognise the file, you don't need to share any data. The table and column
names (for example from DB Browser for SQLite) are enough to add support for the format.
