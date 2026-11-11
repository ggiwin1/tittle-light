# Finding the original animations in the old Tittle app on a Mac (do-it-yourself)

Apple Silicon Macs (M1 and later) can run iPhone apps. If the old Tittle app is installed on your Mac,
the moods and emojis it downloaded may still be sitting in its data folder. Unlike an iPhone backup,
this folder also includes the app's **caches**, which is where downloaded files usually end up.

Nobody else needs access. You look and copy everything yourself, and the Tittle Light app only reads
what you point it at, on your own computer.

## 0. Before you start: don't open the old app yet

When the app opens, it may try to reach the old Tittle server. That server is gone, so the app could
decide its downloads are out of date and clear them. Copy the data first (steps 1 to 3). Opening the
app afterwards is fine (see step 5).

## 1. Find the app's data folder

iPhone apps on a Mac keep their data in a "container" folder:

1. In Finder, press **⇧⌘G** (Go → Go to Folder…), type `~/Library/Containers` and press Return.
2. Many folders are named after the app (`com.something.tittle`). iPhone apps often get a **long
   random name** like `8A1F…-…` instead. To find the right one:
   - type **tittle** in the Finder search box, and pick **"Containers"** (not "This Mac") next to "Search:", or
   - sort by **Date Modified** and look at the folders changed when you last used the app, or
   - open each random-named folder: Finder usually shows the app's name and icon on the right one.
3. Also check `~/Library/Group Containers` (same way, search for **tittle** or **clarity**). Some apps
   keep shared data there.

The Android app's ID was `com.clarityhk.tittle`, so the iPhone app's folder probably has **tittle** or
**clarityhk** in its name or details.

## 2. Copy the whole folder

Inside the container, the useful part is the **Data** folder. Copy the whole container folder (or at
least `Data`) to somewhere like your Desktop, for example `Desktop/Tittle-copy`. Copying never changes
the original.

If macOS asks "Finder would like to access data from other apps", click **Allow**. This is a one-time
privacy check for that folder.

## 3. What's inside, and what to look for

In your copy, look in these places, biggest files first:

| Where (inside `Data/`) | What might be there |
| --- | --- |
| `Library/Caches/<app id>/Cache.db` and the `fsCachedData` folder next to it | **Best bet.** Files the app downloaded from app.tittlelight.com (the moods and emojis). `fsCachedData` holds one file per download, with random names. |
| `Library/Application Support/` | Core Data databases: `*.sqlite` (plus `-wal` / `-shm` files next to them) |
| `Documents/` | `*.sqlite`, `*.db`, `*.realm`, `*.json` |
| `Library/Preferences/*.plist` | Settings, possibly the list of downloaded animations and their IDs |
| `tmp/` | Sometimes half-finished downloads |

How to recognise animation data:
- Database tables with *animation*, *frame*, *emoji* or *mood* in the name (Core Data tables start
  with `Z`, for example `ZANIMATION` or `ZFRAME`).
- Text like `{"12":[255,128,0,1], ...}`: an LED number (0 to 511) followed by red, green, blue. This is
  how the Android app stored each frame.
- Each animation also had an ID, a name, a version and a time interval.

## 4. Let the Tittle Light app read your copy

In the Tittle Light desktop app, go to **♻ Originals → From an old phone's Tittle app**:

- **📂 Open copied folder…** and pick your `Tittle-copy` folder. The app reads every file in it
  (databases, cached downloads, JSON, plists) and lists each animation it recognises.
- Or **📂 Open app database…** to open one `.sqlite` / `.db` file.

Then select an animation and press **⚡ Play** to send it to the cube. **💾 Save all…** keeps them all as
JSON files that you can replay forever, even if the Mac copy is lost later.

Nothing is uploaded anywhere. The app only reads the files you pick, on your computer.

## 5. Optional: let the old app play them

After you've made your copy, you can open the old app on the Mac. If it still lists moods and they
play on the cube (it talks to the cube over your Wi-Fi, just like the phone did), the cube stores each
one under its original ID. While one is showing, press **♻ Originals → 🔎 Ask the cube** in the Tittle
Light app, then **⭐ Save**. After that it plays with one click, even without the old app.

## 6. If the app finds nothing

Don't send any of the data itself. Just send Chad:
- the **file names and sizes** of the biggest files in your copy, and
- for any database, the **table names** (open it with the free *DB Browser for SQLite*, the
  "Database Structure" tab).

That's enough to add support for the format.
