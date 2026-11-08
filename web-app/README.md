# Tittle Light Control Center (web app + local cube bridge)

A browser app for the Tittle Light 8x8x8 RGB cube: protocol reference, a live packet builder,
generative preset previews, and a **3D voxel editor** that uploads straight to the cube through a
small local bridge (`server.ts`).

## Run it

Needs Node.js 20 or newer, on a computer on the same Wi-Fi as the cube.

```bash
npm install
npm run dev          # web app + bridge at http://localhost:3000
```

Open http://localhost:3000. The header shows **Cube bridge online** when the bridge is up.
Set the cube's IP in the editor (or use **Find cube on network** in the Live Command Inspector tab).

For a built version: `npm run build` then `npm start`.

### Try it without the cube

```bash
npm run fake-cube    # pretend cube on 127.0.0.1:9999, in a second terminal
npm run dev
```

Set the cube IP to `127.0.0.1`. The fake cube prints every header and packet it receives, checks
sizes, order and checksums, decodes compressed frames, and answers like the real one (`01 01` for a
new animation id, cached after). It pretends to be firmware 2.5; `FAKE_CUBE_FW=2.4 npm run fake-cube`
pretends to be older firmware.

## The 3D voxel editor

- Paint one Z layer (0 = bottom, 7 = top) at a time on the 8x8 grid. The 3D view shows the whole frame
  and highlights the layer you are on. Drag to rotate.
- Tools: paint, erase (or right-click), flood fill, colour picker; mirror left/right and front/back;
  onion skin of the previous frame.
- Layer tools: clear, fill, copy up/down, extrude to every layer.
- Frames: new, duplicate, reorder, delete, and "next frame shifted" (left/right/back/forward/up/down,
  wrapping) or rotated 90°, which makes simple motion quick.
- Play previews the loop at the chosen interval (8 ms steps, 8 to 2040 ms).
- **Send to cube** uploads every frame. **This frame only** sends just the current one as a still.
- Export **Tittle .bin** (the exact bytes sent to TCP 9999) or **JSON** (`.tittle.json`, the same
  format `tittle_desktop.py` reads and writes). Import takes either.
- Work autosaves in the browser.

The animation id is a hash of the content, so re-sending an unchanged animation plays from the
cube's cache and any edit forces a fresh upload.

## The bridge (`server.ts`)

Browsers can't open raw TCP sockets, so the bridge relays for them. It only talks to local network
addresses (10.x, 172.16-31.x, 192.168.x, 169.254.x, 127.x) and listens on 127.0.0.1 by default.

| Endpoint | Body | What it does |
|---|---|---|
| `GET /api/bridge/health` | | Is the bridge up |
| `POST /api/cube/send` | `{ ip, hex }` | Sends one packet, returns the reply |
| `POST /api/cube/status` | `{ ip }` | 0x60 status query |
| `POST /api/cube/animation` | `{ ip, frames: [base64 x 1536 bytes, z-up], intervalMs, intensity, animId?, format?, orientation? }` | Full upload |
| `POST /api/cube/discover` | `{}` | UDP 10000 broadcast, then a /24 scan of port 9999 |
| `POST /api/cube/pizza` | `{ ip, seconds? }` | Easter egg (below) |

The same requests work over the WebSocket at `/ws` (`{ id, type: 'animation', ... }`) with live
`log` and `progress` messages, which the web app uses.

Upload flow, on one TCP socket: 18-byte `0x90` header, wait for the reply
(byte 4 = `01` means send the frames; byte 3 = `00` otherwise means it already has that id cached, for every animation type), then send
the frame data, waiting for the cube's ack after each packet, then close the socket so the cube
starts looping.

The frame data format depends on the cube's firmware, which the bridge reads from the `0x60` status
reply (bytes 1 and 2) the first time it talks to a cube, just like the official app:

- **Firmware 2.5 and newer, or unknown:** each frame RLE-compressed (CompressUtil / BCL RLE) and
  followed by `FF FF FF FF`; the stream is cut into `0x25` packets of up to 512 bytes:
  `[0x25, total(2), index(2), length(2), payload, checksum of the whole stream, 0D 0A]`, 50 ms apart.
- **Older firmware:** per frame `0x21` (LEDs 0-169), `0x22` (170-339), `0x23` (340-511).

The editor's **Format** menu can force either one if auto-detection guesses wrong.

**Orientation.** The editor, JSON files and presets are all z-up: index `z*64 + y*8 + x`
(x left→right, y front→back, z bottom→top). Ben's real cube turned out to use its own order,
`y*64 + (7-z)*8 + x` (its vertical index 0 is the top layer), so the bridge converts every frame just before sending it. The `.bin` export is in
the cube's order too. If a drawing appears upside down or mirrored on a cube, change **Cube
orientation** in the editor (upright/legacy layout, upside down, mirror left/right).

## Easter egg

Click **made by Claude and Chad** at the bottom of the page. The cube spins a pepperoni pizza slice
for 5 seconds, then goes back to what it was showing: the last thing sent through the bridge, or,
if nothing was, whatever mood/paint animation the cube reports it is playing (`70 05`).

Settings (environment variables): `PORT` (3000), `HOST` (127.0.0.1; use 0.0.0.0 to open the app
from a phone on the same Wi-Fi), `CUBE_PORT` (9999), `BRIDGE_ALLOW_ANY_IP=1`.

## Files

- `server.ts` – bridge + web server
- `src/tittleProtocol.ts` – packet building, chunking, .bin and JSON import/export (shared by both)
- `src/VoxelEditor.tsx` – the editor
- `src/cubeBridge.ts` – browser client for the bridge
- `scripts/fake-cube.ts` – pretend cube for testing
- `tittle_desktop.py` – the Python/Tkinter desktop app
- `decompiled_src/` – decompiled Android app, the protocol reference
