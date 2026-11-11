"""
================================================================================
Tittle Light Desktop Control Center (Windows Native App)
Reverse-Engineered for SPIN-R LTD / Clarity Labs 8x8x8 RGB Wi-Fi Cube
================================================================================
Fixes & Enhancements:
- Single-socket persistent connection for Header + 3D Frame Chunks (0x21, 0x22, 0x23)
- Resolves "Data Not Ready" (01 01) handshake: automatically uploads animation frames
- Cube loops uploaded 3D animations automatically in hardware
- Safe 0..255 byte clamping on all procedural math generators
- Includes: Solid Lamp, Official Presets, Fairies 2.0, Novel 3D Presets, 3D Clock, Music Visualizer
- Real-time Raw Hex Debug Packet Inspector
- Custom animation import/export: JSON (Export/Import) and MagicaVoxel .vox (Import)
- Local 3D preview of the current animation, raw hex sender, Status / Mode queries
================================================================================

Run:
    python tittle_desktop.py                # auto-discovers the cube on start
    python tittle_desktop.py --ip 192.168.1.42

Protocol summary (TCP 9999, every packet ends with 0D 0A):
    0x10 solid colour / off      0x34 clock      0x41 music visualiser
    0x50 mood animation header   0x90 paint (custom 3D) header
    0x60 01 00 status query      0x70 05 query playing mode
    0x70 04 IP[4] UDP discovery broadcast to port 10000
Frames are 512 voxels x RGB = 1536 bytes, voxel index = z*64 + y*8 + x
(x left->right, y front->back, z bottom->top). Each frame is sent as three
chunks 0x21 / 0x22 / 0x23 [frameHi, frameLo] + payload + 0D 0A on the same
socket as the header, after the cube answers "data not ready" (byte 4 = 01).

Custom animation file format (.json), written by "Export JSON":
    {
      "format": "tittle-animation",
      "version": 1,
      "name": "My animation",
      "interval_ms": 100,
      "intensity": 255,
      "opcode": "0x90",                  # "0x90" paint or "0x50" mood header
      "voxel_index": "z*64 + y*8 + x",
      "encoding": "hex",
      "frames": ["<3072 hex chars = 1536 bytes>", ...]
    }
"Import JSON" is lenient. Each entry of "frames" may be any of:
    - a hex string of 1536 bytes (spaces allowed)
    - a flat list of 1536 ints (R0, G0, B0, R1, ...)
    - a list of 512 [r, g, b] triples
    - a sparse voxel list, either [[x, y, z, r, g, b], ...] or
      [{"x": 0, "y": 0, "z": 0, "color": "#ff8800"}, ...] (or r/g/b keys),
      optionally wrapped as {"voxels": [...]}
Frame strings may also be base64, and "intervalMs" is accepted for "interval_ms",
so files from the web voxel editor (.tittle.json) import directly. The top level
may also be just the list of frames.

"Import JSON / BIN" also reads .bin wire dumps: the 18-byte 0x90/0x50 header
followed by the 0x21/0x22/0x23 chunks for every frame.

"Import VOX" reads MagicaVoxel .vox files. Every model in the file becomes one
frame (a multi-model file plays as an animation). Voxels outside 8x8x8 are
cropped. MagicaVoxel's z axis is up, the same as the cube.
"""

import sys
import os
import re
import json
import time
import math
import queue
import random
import socket
import struct
import zlib
import argparse
import base64
import threading
from datetime import datetime, timedelta
import colorsys
import sqlite3
from concurrent.futures import ThreadPoolExecutor

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, colorchooser, filedialog
except ImportError:  # lets the protocol / file code be imported without a display
    tk = None

if getattr(sys, "frozen", False):   # packaged .exe / .app: keep settings in the user's home folder
    CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".tittle_light_config.json")
else:
    CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tittle_config.json")

APP_VERSION = "2026.11.11-r13"
FRAME_BYTES = 512 * 3
OPCODE_PAINT = 0x90
OPCODE_MOOD = 0x50


def read_config(path=None):
    try:
        with open(path or CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def update_config(path=None, **values):
    cfg = read_config(path)
    cfg.update(values)
    try:
        with open(path or CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def clamp(v):
    """Ensures value is an integer strictly within valid byte range 0..255."""
    return max(0, min(255, int(v)))


def voxel_index(x, y, z):
    return (z * 64) + (y * 8) + x


def hexdump(data):
    return " ".join(f"{b:02X}" for b in data)


def is_cached_ack(resp):
    """True when the cube says it already has this animation and starts playing it.
    The official app treats byte 3 == 0 as 'OK' and byte 4 == 1 as 'data not ready' for every
    animation type. (Older versions of this app only accepted 00 00 20 ..., so paint uploads the cube
    already had were sent again in full.)"""
    return bool(resp) and len(resp) > 5 and resp[0] == 0x00 and resp[3] == 0x00 and resp[4] != 0x01


def is_data_not_ready(resp):
    return bool(resp) and len(resp) > 5 and resp[4] == 0x01


# ─── Orientation ───
# Everything in this app (presets, clock, previews, JSON/VOX files, the web editor) uses
# x = left→right, y = front→back, z = bottom→top, index z*64 + y*8 + x.
# Ben's real cube showed that the cube's own order is different: its slowest axis runs front→back and
# the middle axis is vertical. Frames are converted only at the wire (to_device / from_device), and the
# Debug tab has a test pattern plus flip/mirror switches in case a cube is wired differently.
ORIENTATION = {"layout": "upright", "flip_v": False, "mirror_x": False, "tilt": 0, "turn": 0}
_PERM_CACHE = {}


def _orient_xyz(x, y, z, tilt, turn, mirror):
    """User orientation on top of the base mapping: mirror left/right, tilt forward around the
    left-right axis, then turn around the vertical axis, each in 90-degree steps."""
    cx, cy, cz = x - 3.5, y - 3.5, z - 3.5
    if mirror:
        cx = -cx
    for _ in range((tilt // 90) % 4):          # tilt forward: the top goes to the front
        cy, cz = -cz, cy
    for _ in range((turn // 90) % 4):          # turn clockwise seen from above
        cx, cy = cy, -cx
    return int(round(cx + 3.5)), int(round(cy + 3.5)), int(round(cz + 3.5))


def _device_perm():
    key = (ORIENTATION.get("layout"), bool(ORIENTATION.get("flip_v")), bool(ORIENTATION.get("mirror_x")),
           int(ORIENTATION.get("tilt", 0)), int(ORIENTATION.get("turn", 0)))
    if key not in _PERM_CACHE:
        perm = []
        for z in range(8):
            for y in range(8):
                for x in range(8):
                    xx, yy, zz = _orient_xyz(x, y, z, key[3], key[4], key[2])
                    if key[0] == "legacy":
                        v = 7 - zz if key[1] else zz
                        perm.append(v * 64 + yy * 8 + xx)      # the old (r5 and earlier) layout
                    else:
                        # Ben's cube (r6-r8 tests): front->back slowest, then height counted from the TOP, then x.
                        v = zz if key[1] else 7 - zz
                        perm.append(yy * 64 + v * 8 + xx)
        _PERM_CACHE[key] = perm
    return _PERM_CACHE[key]


def to_device(frame):
    """App (z-up) frame -> the byte order the cube expects."""
    perm, out = _device_perm(), bytearray(FRAME_BYTES)
    for li, di in enumerate(perm):
        out[di * 3:di * 3 + 3] = frame[li * 3:li * 3 + 3]
    return out


def from_device(frame):
    """Frame in the cube's byte order (tittle.db, .bin dumps) -> app (z-up) frame."""
    perm, out = _device_perm(), bytearray(FRAME_BYTES)
    for li, di in enumerate(perm):
        out[li * 3:li * 3 + 3] = frame[di * 3:di * 3 + 3]
    return out


def frame_chunks(frame_buf, frame_idx):
    """Split one 1536-byte frame into the three 0x21/0x22/0x23 packets."""
    hi, lo = (frame_idx >> 8) & 0xFF, frame_idx & 0xFF
    crlf = bytes([0x0D, 0x0A])
    return [
        bytes([0x21, hi, lo]) + bytes(frame_buf[0:510]) + crlf,      # LEDs 0..169
        bytes([0x22, hi, lo]) + bytes(frame_buf[510:1020]) + crlf,   # LEDs 170..339
        bytes([0x23, hi, lo]) + bytes(frame_buf[1020:1536]) + crlf,  # LEDs 340..511
    ]


def normalize_frame(raw_buf):
    frame = bytearray(clamp(b) for b in raw_buf[:FRAME_BYTES])
    if len(frame) < FRAME_BYTES:
        frame.extend(b"\x00" * (FRAME_BYTES - len(frame)))
    return frame


def rle_decompress(data):
    """BCL RLE decoder (Marcus Geelnard's Basic Compression Library), which the app's encoder
    targets: a long run is stored as count-1 and expands to count+1 copies of the symbol.
    (The decompiled Java uncompressData uses '< count', which disagrees with the app's own encoder.)"""
    data = bytes(data)
    if not data:
        return b""
    marker, out, pos = data[0], bytearray(), 1
    while pos < len(data):
        sym = data[pos]; pos += 1
        if sym != marker:
            out.append(sym)
            continue
        count = data[pos]; pos += 1
        if count <= 2:
            out.extend([marker] * (count + 1))
        else:
            if count & 0x80:
                count = ((count & 0x7F) << 8) + data[pos]; pos += 1
            out.extend([data[pos]] * (count + 1)); pos += 1
    return bytes(out)


def rle_compress(data):
    """BCL-style RLE, byte-compatible with the app's CompressUtil.encodeRepeatingByte:
    [marker] + stream; runs over 3 become marker, count-1 (15-bit), symbol."""
    data = bytes(data)
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    marker = counts.index(min(counts))
    out = bytearray([marker])

    def count_bytes(n):
        return bytes([n]) if n < 128 else bytes([0x80 | (n >> 8), n & 0xFF])

    i = 0
    while i < len(data):
        sym, run = data[i], 1
        while i + run < len(data) and data[i + run] == sym and run < 0x8000:
            run += 1
        if sym == marker:
            out += bytes([marker, run - 1]) if run <= 3 else bytes([marker]) + count_bytes(run - 1) + bytes([marker])
        elif run <= 3:
            out += bytes([sym]) * run
        else:
            out += bytes([marker]) + count_bytes(run - 1) + bytes([sym])
        i += run
    return bytes(out)


def compressed_packets(frames):
    """Firmware 2.5+ upload format (AnimationBinaryFrameHolder): every frame RLE-compressed
    and followed by FF FF FF FF, the whole stream cut into 512-byte 0x25 packets."""
    stream = b"".join(rle_compress(f) + b"\xff\xff\xff\xff" for f in frames)
    total = (len(stream) + 511) // 512
    checksum = sum(stream) & 0xFF          # the app checksums the whole stream, not each packet
    packets = []
    for i in range(total):
        payload = stream[i * 512:(i + 1) * 512]
        packets.append(bytes([0x25, total >> 8, total & 0xFF, i >> 8, i & 0xFF,
                              len(payload) >> 8, len(payload) & 0xFF]) + payload +
                       bytes([checksum, 0x0D, 0x0A]))
    return packets


def binary_packets(blob):
    """0x24 packets (AnimationBinaryHolder) for an animation stored as one binary blob."""
    total = (len(blob) + 511) // 512
    packets = []
    for i in range(total):
        payload = blob[i * 512:(i + 1) * 512]
        packets.append(bytes([0x24, total >> 8, total & 0xFF, i >> 8, i & 0xFF,
                              len(payload) >> 8, len(payload) & 0xFF]) + payload +
                       bytes([sum(payload) & 0xFF, 0x0D, 0x0A]))
    return packets


def frames_from_blob(blob):
    """Try to turn an app binary blob (compressed frames separated by FF FF FF FF) into frames."""
    frames = []
    for part in bytes(blob).split(b"\xff\xff\xff\xff"):
        if not part:
            continue
        raw = rle_decompress(part)
        if len(raw) != FRAME_BYTES:
            return None
        frames.append(bytearray(raw))
    return frames or None


MODE_NAMES = {0x10: "Lamp", 0x20: "Emoji", 0x90: "Paint", 0x30: "Clock", 0x34: "Clock", 0x40: "Music", 0x41: "Music", 0x50: "Mood"}
EVERY_DAY = 0x7F   # all seven weekday bits, as the app sends when every day is ticked


def content_anim_id(frames):
    """Animation ID derived from the frame data, so an edited animation never
    collides with a stale copy the cube has cached under the same ID."""
    crc = 0
    for f in frames:
        crc = zlib.crc32(bytes(f), crc)
    return (crc & 0x7FFFFFFF) or 1


class TittleClient:
    TCP_PORT = 9999
    UDP_DISCOVERY_PORT = 10000

    def __init__(self, log_callback=None, status_callback=None, config_file=CONFIG_FILE):
        self.ip = None
        self.is_connected = False
        self.config_file = config_file
        self.log_callback = log_callback or (lambda msg: None)
        self.status_callback = status_callback or (lambda status, color: None)
        self.active_worker = None
        self.stop_flag = threading.Event()
        self.lock = threading.Lock()
        self.fw_version = None          # (high, low) from the 60 02 status reply
        self.last_action = None         # (method name, args, kwargs) of the last thing shown on the cube
        self.on_user_action = None      # called with (method, args) whenever something new is shown
        self.last_on_action = None      # the last thing shown that wasn't "off" (for the tray's On)
        self.last_anim_id = None        # ID of the animation this app last started (to spot schedule-started ones)
        # "chunks" (0x21-0x23) is what worked on Ben's cube in the first real test (2026-11-08).
        # "compressed" (0x25) is what the official app uses on firmware 2.5+; "auto" follows the app's rule.
        self.upload_format = read_config(config_file).get("upload_format", "chunks")
        self.load_saved_ip()

    def log(self, text):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log_callback(f"[{timestamp}] {text}")

    def _tx(self, s, data):
        self.log(f"TX ──▶ {hexdump(data[:24])}{' …' if len(data) > 24 else ''} ({len(data)} bytes)")
        s.sendall(data)

    def _rx(self, s, size=64, quiet=False):
        try:
            data = s.recv(size)
        except socket.timeout:
            if not quiet:
                self.log("RX ◀── [No response / timeout]")
            return None
        if data and not quiet:
            self.log(f"RX ◀── {hexdump(data)}")
        return data

    def load_saved_ip(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r") as f:
                    cfg = json.load(f)
                    self.ip = cfg.get("last_ip")
            except Exception:
                pass

    def save_ip(self, ip):
        self.ip = ip
        update_config(self.config_file, last_ip=ip, last_seen=datetime.now().isoformat())

    @staticmethod
    def get_local_ip():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"
        finally:
            s.close()

    def _set_connected(self, ip):
        self.ip = ip
        self.save_ip(ip)
        self.is_connected = True
        self.status_callback(f"Connected to {self.ip}", "#10b981")

    def sniff_and_connect(self, forced_ip=None):
        self.status_callback("Searching for Tittle Light on local network...", "#f59e0b")

        if forced_ip and forced_ip.strip():
            target = forced_ip.strip()
            self.log(f"Attempting manual IP: {target}...")
            if self.test_ip(target):
                self._set_connected(target)
                self.log(f"[+] Connected to manual IP {target}")
                return True
            self.log(f"[-] No answer from {target}, falling back to auto-discovery")

        if self.ip:
            self.log(f"[Tier 1] Checking cached IP: {self.ip}...")
            if self.test_ip(self.ip):
                self._set_connected(self.ip)
                self.log(f"[Tier 1] Successfully reconnected to cached IP {self.ip}")
                return True

        self.log("[Tier 2] Broadcasting UDP discovery ping to 255.255.255.255:10000...")
        discovered_ip = self._udp_broadcast_discover(timeout=3)
        if discovered_ip and self.test_ip(discovered_ip):
            self._set_connected(discovered_ip)
            self.log(f"[Tier 2] Discovered via UDP broadcast at {self.ip}")
            return True

        self.log("[Tier 3] Sniffing local subnet for Tittle Light port 9999...")
        sniffed_ip = self._subnet_port_sniffer()
        if sniffed_ip:
            self._set_connected(sniffed_ip)
            self.log(f"[Tier 3] Found via subnet port sniffer at {self.ip}")
            return True

        self.is_connected = False
        self.status_callback("Light not found (Ensure it is powered on & on same Wi-Fi)", "#ef4444")
        self.log("[-] Light not found. If this is a brand new light, connect it to your Wi-Fi first.")
        self.log("    Tier 4: type the cube's IP (see your router's client list) and press Connect.")
        return False

    def test_ip(self, ip, timeout=1.5):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((ip, self.TCP_PORT))
            s.send(bytes([0x60, 0x01, 0x00, 0x0D, 0x0A]))
            res = s.recv(32)
            s.close()
            return res is not None and len(res) > 0
        except Exception:
            return False

    def _udp_broadcast_discover(self, timeout=3):
        local_ip = self.get_local_ip()
        parts = [int(p) for p in local_ip.split(".")]
        if len(parts) != 4:
            return None

        tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            tcp_server.bind(("0.0.0.0", self.TCP_PORT))
            tcp_server.listen(1)
            tcp_server.settimeout(timeout)

            pkt = bytes([0x70, 0x04, parts[0], parts[1], parts[2], parts[3], 0x0D, 0x0A])
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            udp.sendto(pkt, ("255.255.255.255", self.UDP_DISCOVERY_PORT))
            udp.close()

            client_sock, addr = tcp_server.accept()
            client_sock.settimeout(1.0)
            try:
                client_sock.recv(128)
            except Exception:
                pass
            client_sock.close()
            return addr[0]
        except Exception:
            return None
        finally:
            tcp_server.close()

    def _subnet_port_sniffer(self):
        local_ip = self.get_local_ip()
        prefix = ".".join(local_ip.split(".")[:3])
        found_ip = []

        def probe(host_num):
            ip = f"{prefix}.{host_num}"
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.25)
                res = s.connect_ex((ip, self.TCP_PORT))
                if res == 0:
                    s.send(bytes([0x60, 0x01, 0x00, 0x0D, 0x0A]))
                    resp = s.recv(16)
                    if resp:
                        found_ip.append(ip)
                s.close()
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=30) as executor:
            executor.map(probe, range(1, 255))

        return found_ip[0] if found_ip else None

    def wake_cube(self, test=False):
        """During a scheduled off period Ben's cube stops answering (connections time out). Try to get it back:
        the discovery packet (which also finds it if its IP changed), then keep trying to connect for ~15 s."""
        if not self.ip:
            return False
        self.log("[*] " + ("Wake test: " if test else "No answer from the cube. It may be in a scheduled off period; ")
                 + "trying to wake it…")
        self.status_callback("Waking the cube…", "#3b82f6")
        started = time.time()
        try:
            parts = [int(p) for p in self.get_local_ip().split(".")]
            pkt = bytes([0x70, 0x04] + parts + [0x0D, 0x0A])
            u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            u.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            u.sendto(pkt, (self.ip, self.UDP_DISCOVERY_PORT))
            u.close()
            self.log(f"TX ──▶ UDP {hexdump(pkt)} to {self.ip}:{self.UDP_DISCOVERY_PORT}")
        except Exception as e:
            self.log(f"[i] UDP to the cube failed: {e}")
        found = self._udp_broadcast_discover(timeout=3)
        if found:
            self.log(f"[+] The cube answered the discovery broadcast from {found} after {time.time() - started:.1f} s")
            if found != self.ip:
                self.log(f"[!] The cube's address changed from {self.ip} to {found}; using the new one.")
                self.ip = found
                self.save_ip(found)
        else:
            self.log("[-] No answer to the discovery broadcast")
        attempt = 0
        while time.time() - started < 18:
            attempt += 1
            t0 = time.time()
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(5)
                s.connect((self.ip, self.TCP_PORT))
                self.log(f"[+] Connected on attempt {attempt}, {time.time() - started:.1f} s after the first try")
                self._tx(s, bytes([0x60, 0x01, 0x00, 0x0D, 0x0A]))
                s.settimeout(6)
                r = self._rx(s, 64)
                s.close()
                self.log("[+] The cube is awake and answering." if r else
                         "[i] The cube accepted the connection but didn't answer the status question.")
                return True
            except Exception as e:
                self.log(f"[-] Connect attempt {attempt}: {e}")
                time.sleep(max(0.0, 1.0 - (time.time() - t0)))
        self.log("[-] Couldn't wake the cube. If it's in a scheduled off period, press 🔍 Debug → 🧪 Wake test "
                 "and send the log to Chad.")
        self.status_callback("The cube isn't answering (scheduled off?)", "#f59e0b")
        return False

    def send_packet(self, packet_bytes, expect_response=True, _retry=True):
        if not self.ip:
            self.log("[ERROR] Cannot send packet: No IP address configured.")
            return None

        self.stop_current_action()

        with self.lock:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(3.0)
                s.connect((self.ip, self.TCP_PORT))
                self._tx(s, packet_bytes)
                response = self._rx(s, 128) if expect_response else None
                s.close()
                self.is_connected = True
                self.status_callback(f"Connected to {self.ip}", "#10b981")
                return response
            except Exception as e:          # can't connect: the cube may be in a scheduled off period
                failed = str(e)
        if _retry and self.wake_cube():
            return self.send_packet(packet_bytes, expect_response, _retry=False)
        self.log(f"[ERROR] Socket communication failed: {failed}")
        self.is_connected = False
        self.status_callback("Connection lost", "#ef4444")
        return None

    def _note_cached(self, anim_id):
        """Remember (in the config file) which animation IDs the cube has confirmed, across app sessions."""
        try:
            cache = read_config(self.config_file).get("cube_cache", {})
            cache[str(anim_id)] = datetime.now().strftime("%Y-%m-%d %H:%M")
            if len(cache) > 300:
                cache = dict(sorted(cache.items(), key=lambda kv: kv[1])[-300:])
            update_config(self.config_file, cube_cache=cache)
        except Exception:
            pass

    def peek_mode(self, timeout=1.5):
        """Quietly ask what the cube is showing (70 05): no wake-up, no log spam, skipped if the app is busy."""
        if not self.ip or not self.lock.acquire(blocking=False):
            return False
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((self.ip, self.TCP_PORT))
            s.sendall(bytes([0x70, 0x05, 0x0D, 0x0A]))
            resp = s.recv(64)
            s.close()
        except Exception:
            return None
        finally:
            self.lock.release()
        if resp and len(resp) >= 12 and resp[0] == 0x70 and resp[1] == 0x05:
            return {"mode": resp[2], "mode_name": MODE_NAMES.get(resp[2], f"0x{resp[2]:02X}"),
                    "anim_id": int.from_bytes(bytes(resp[6:10]), "big"), "version": resp[10] or 1,
                    "intensity": resp[11], "raw": bytes(resp)}
        return None

    def _remember(self, method, *args, **kwargs):
        self.last_action = (method, args, kwargs)
        if method == "play_stored":
            self.last_anim_id = args[0]
        elif method == "sync_clock":
            self.last_anim_id = args[0]
        elif method != "upload_frames":
            self.last_anim_id = None
        if not (method == "set_solid_color" and (not any(args[:3]) or (len(args) > 3 and not args[3]))):
            self.last_on_action = self.last_action
        if self.on_user_action:
            try:
                self.on_user_action(method, args)
            except Exception:
                pass

    def restore_last(self):
        """Replay whatever was last shown (used after the pizza easter egg)."""
        if not self.last_action:
            return False
        method, args, kwargs = self.last_action
        getattr(self, method)(*args, **kwargs)
        return True

    def set_solid_color(self, r, g, b, intensity=255):
        self._remember("set_solid_color", r, g, b, intensity)
        pkt = bytes([0x10, clamp(r), clamp(g), clamp(b), clamp(intensity), 0x0D, 0x0A])
        return self.send_packet(pkt)

    def turn_off(self):
        return self.set_solid_color(0, 0, 0, 0)

    def _parse_status(self, resp):
        """60 reply: [1]/[2] = firmware version (as the app reads it), device name from byte 6 to the first 00."""
        if resp and len(resp) > 3 and resp[0] == 0x60:
            self.fw_version = (resp[1], resp[2])
            name = bytes(resp[6:]).split(b"\x00")[0].decode("ascii", "replace") if len(resp) > 6 else ""
            self.log(f"[+] Firmware {resp[1]}.{resp[2]}" + (f", device name '{name}'" if name else "") +
                     f" -> uploads use the {'compressed 0x25' if self.fw_version >= (2, 5) else 'raw 0x21-0x23'} format")
            return True
        return False

    def query_status(self):
        resp = self.send_packet(bytes([0x60, 0x01, 0x00, 0x0D, 0x0A]))
        self._parse_status(resp)
        return resp

    def _probe_version(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect((self.ip, self.TCP_PORT))
            s.sendall(bytes([0x60, 0x01, 0x00, 0x0D, 0x0A]))
            resp = s.recv(64)
            s.close()
            self._parse_status(resp)
        except Exception:
            pass

    def pick_upload_format(self):
        """Same rule as the official app: raw chunks only for firmware older than 2.5."""
        if self.upload_format in ("compressed", "chunks"):
            return self.upload_format
        if self.fw_version is None:
            self._probe_version()
        if self.fw_version is not None and self.fw_version < (2, 5):
            return "chunks"
        return "compressed"

    def query_mode(self):
        """70 05: reply [70 05 mode ? ? ? id(4) ? intensity ...] tells what the cube is playing."""
        resp = self.send_packet(bytes([0x70, 0x05, 0x0D, 0x0A]))
        if resp and len(resp) >= 12 and resp[0] == 0x70 and resp[1] == 0x05:
            info = {"mode": resp[2], "mode_name": MODE_NAMES.get(resp[2], f"0x{resp[2]:02X}"),
                    "anim_id": int.from_bytes(bytes(resp[6:10]), "big"), "intensity": resp[11],
                    "raw": bytes(resp)}
            self.log(f"[+] Cube is in {info['mode_name']} mode, animation ID {info['anim_id']}, "
                     f"intensity {info['intensity']} (byte 10 = {resp[10]}, maybe the version)")
            return info
        return None

    def stop_current_action(self):
        if self.active_worker and self.active_worker.is_alive() \
                and self.active_worker is not threading.current_thread():
            self.stop_flag.set()
            self.active_worker.join(timeout=1.0)
        self.stop_flag.clear()

    @staticmethod
    def interval_byte(opcode, interval_ms):
        if opcode == OPCODE_PAINT:
            return max(1, min(255, interval_ms // 8))    # 8 ms units (app: time_interval / 8)
        return max(1, min(255, interval_ms // 10))       # 10 ms units (Gemini's reading for 0x50)

    @staticmethod
    def build_anim_header(opcode, num_frames, interval_ms, anim_id, intensity, version=1, raw_interval=None, sf_opt=0):
        id_bytes = [(anim_id >> 24) & 0xFF, (anim_id >> 16) & 0xFF, (anim_id >> 8) & 0xFF, anim_id & 0xFF]
        nf = [(num_frames >> 8) & 0xFF, num_frames & 0xFF]
        interval_param = (raw_interval & 0xFF) if raw_interval is not None \
            else TittleClient.interval_byte(opcode, interval_ms)
        opt = (sf_opt & 0xFF) if opcode == OPCODE_PAINT else 0
        return bytes([opcode & 0xFF] + nf + [interval_param] + id_bytes +
                     [version & 0xFF, clamp(intensity), opt, 0, 0, 0, 0, 0, 0x0D, 0x0A])

    def _stream_frames(self, s, frames, fmt, name, raw_binary=None, progress=True):
        """Send animation data on an open socket after the cube said 'data not ready'."""
        if frames and not raw_binary:
            frames = [to_device(normalize_frame(f)) for f in frames]
        if raw_binary:
            packets, label = binary_packets(raw_binary), "0x24 binary"
        elif fmt == "compressed":
            packets, label = compressed_packets(frames), "0x25 compressed"
        else:
            packets, label = [c for i, f in enumerate(frames) for c in frame_chunks(f, i)], "0x21-0x23 chunk"
        self.log(f"[*] Streaming {len(packets)} {label} packets for '{name}'")
        s.settimeout(1.0)
        started, silent = time.time(), 0
        for i, pkt in enumerate(packets):
            if self.stop_flag.is_set():
                self.log(f"[!] Upload of '{name}' cancelled.")
                return False
            if i < 3 or i == len(packets) - 1:
                self._tx(s, pkt)
                reply = self._rx(s, 32)
            else:
                s.sendall(pkt)
                reply = self._rx(s, 32, quiet=True)
            if reply is None:
                silent += 1
                if silent == 3:      # the cube is not answering each packet: stop waiting a full second each
                    self.log("[i] The cube is not replying to each packet, sending the rest without waiting.")
                    s.settimeout(0.03)
            else:
                silent = 0
            if pkt[0] in (0x24, 0x25):
                time.sleep(0.05)              # the app waits 50 ms between binary packets
            if progress:
                pct = int((i + 1) / len(packets) * 100)
                self.status_callback(f"Loading '{name}' ({pct}%)...", "#3b82f6")
        self.log(f"[i] Sent {len(frames)} frames in {time.time() - started:.1f} s")
        return True

    def upload_frames(self, name, frames, interval_ms=100, intensity=255, anim_id=None, opcode=OPCODE_PAINT,
                      version=1, raw_interval=None, raw_binary=None, remember=True, on_done=None):
        """Upload pre-rendered frames (list of 1536-byte buffers) and start them looping."""
        if remember:
            self._remember("upload_frames", name, frames, interval_ms, intensity, anim_id, opcode,
                           version, raw_interval, raw_binary)
        self.stop_current_action()
        if not self.ip:
            self.log("[ERROR] Cannot upload: No IP configured.")
            return
        frames = [normalize_frame(f) for f in frames]
        if not frames:
            self.log("[ERROR] Nothing to upload: animation has no frames.")
            return
        num_frames = len(frames)
        if anim_id is None:      # hash what the cube will store, so a change of orientation gets a new ID
            anim_id = content_anim_id([to_device(f) for f in frames]) if not raw_binary else content_anim_id(frames)
        if remember:
            self.last_anim_id = anim_id

        def _worker(retry=True):
            failed = []
            self.log(f"[*] Connecting to {self.ip} to upload '{name}' "
                     f"({num_frames} frames @ {interval_ms}ms, ID {anim_id}, version {version})...")
            self.status_callback(f"Loading '{name}' (0%)...", "#3b82f6")

            with self.lock:
                try:
                    fmt = self.pick_upload_format()
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(4.0)
                    s.connect((self.ip, self.TCP_PORT))

                    # 1. Header
                    self._tx(s, self.build_anim_header(opcode, num_frames, interval_ms, anim_id, intensity,
                                                       version, raw_interval))

                    # 2. Handshake: 01 01 = data not ready (send frames), 00 00 = cached
                    resp = self._rx(s, 64)
                    if resp is None and retry:
                        s.close()
                        raise TimeoutError("no reply to the header")
                    known = read_config(self.config_file).get("cube_cache", {})
                    if is_cached_ack(resp):
                        self._note_cached(anim_id)
                        s.close()
                        self.log(f"[+] Cube already has animation ID 0x{anim_id:08X}, playing it without re-uploading.")
                        self.status_callback(f"Playing '{name}' (cached on cube)", "#10b981")
                        if on_done:
                            on_done()
                        return

                    if str(anim_id) in known:
                        self.log(f"[i] This app saw the cube store ID {anim_id} on {known[str(anim_id)]}, but the cube "
                                 f"asked for the data again, so it has dropped it since (it keeps only a limited number "
                                 f"of animations, or it was switched off). Uploading again.")
                    # 3. Stream the frames on the same persistent socket
                    if not self._stream_frames(s, frames, fmt, name, raw_binary):
                        s.close()
                        return
                    time.sleep(0.2)
                    s.close()
                    self.is_connected = True
                    self._note_cached(anim_id)
                    self.status_callback(f"Playing '{name}' (Looping on Cube)", "#10b981")
                    self.log(f"[+] '{name}' loaded into the cube ({num_frames} frames). "
                             f"Animation is now looping in hardware.")
                    if on_done:
                        on_done()

                except Exception as e:
                    if retry and not self.stop_flag.is_set():
                        failed.append(e)
                    else:
                        self.log(f"[ERROR] Failed to upload '{name}': {e}")
                        self.status_callback("Animation upload failed - check connection", "#ef4444")
            if failed:
                if self.wake_cube():
                    return _worker(retry=False)
                self.log(f"[ERROR] Failed to upload '{name}': {failed[0]}")

        self.active_worker = threading.Thread(target=_worker, daemon=True)
        self.active_worker.start()

    def upload_and_play_animation(self, name, generator_func, num_frames=16, interval_ms=100,
                                  intensity=255, anim_id=None, opcode=OPCODE_PAINT):
        frames = render_frames(generator_func, num_frames, interval_ms)
        self.upload_frames(name, frames, interval_ms, intensity, anim_id, opcode)

    def _header_expect_stored(self, label, header, done_text, done_color):
        """Clock (0x34) and music (0x41) play a face / visualizer the cube must already have.
        In the official app that data came from the Tittle server; we have nothing to stream,
        so if the cube asks for it ("data not ready") we stop and explain instead of hanging."""
        if not self.ip:
            self.log(f"[ERROR] Cannot start {label}: No IP configured.")
            return
        self.stop_current_action()

        def _worker(retry=True):
            with self.lock:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(3.5)
                    s.connect((self.ip, self.TCP_PORT))
                    self._tx(s, header)
                    res = self._rx(s, 64)
                    s.close()
                except Exception as e:
                    res, err = None, e
                else:
                    err = None
            if res is None and retry and self.wake_cube():
                return _worker(retry=False)
            with self.lock:
                try:
                    if err:
                        raise err
                    if res and len(res) > 5 and res[4] == 1:
                        self.log(f"[-] {label}: the cube does not have this ID/version stored and asked for its data. "
                                 f"That data came from the old Tittle server, so it can't be sent. Put the cube in "
                                 f"this mode with any old setup, then use ♻️ Originals > 'What is it playing now?' "
                                 f"to read the right ID and version.")
                        self.status_callback(f"{label}: ID not stored on cube (see Debug)", "#f59e0b")
                        return
                    if res is None:
                        self.log(f"[-] {label}: no answer from the cube.")
                        self.status_callback(f"{label}: no answer", "#f59e0b")
                        return
                    self.is_connected = True
                    self.status_callback(done_text, done_color)
                except Exception as e:
                    self.log(f"[ERROR] {label} failed: {e}")
                    self.status_callback(f"{label} failed - check connection", "#ef4444")

        self.active_worker = threading.Thread(target=_worker, daemon=True)
        self.active_worker.start()

    # ---------------- recovering animations the cube already stores ----------------
    def _try_stored(self, anim_id, version, opcode, intensity, frames=1):
        """Send a header for an ID the cube may already have. True if it starts playing (cached ACK)."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        try:
            s.connect((self.ip, self.TCP_PORT))
            if opcode == 0x34:     # clock face: the cube's own clock, if it still has this face stored
                s.sendall(self.build_clock_header(anim_id, intensity, version))
            else:
                s.sendall(self.build_anim_header(opcode, frames, 100, anim_id, intensity, version, raw_interval=10))
            resp = s.recv(64)
            return is_cached_ack(resp), resp
        except Exception:
            return False, None
        finally:
            s.close()

    def play_stored(self, anim_id, version=1, opcode=OPCODE_MOOD, intensity=255, name=None):
        self._remember("play_stored", anim_id, version, opcode, intensity, name=name)
        if not self.ip:
            self.log("[ERROR] No IP configured.")
            return
        self.stop_current_action()

        def _worker():
            with self.lock:
                hdr = self.build_anim_header(opcode, 1, 100, anim_id, intensity, version, raw_interval=10)
                self.log(f"TX ──▶ {hexdump(hdr)} (try stored ID {anim_id} v{version})")
                ok, resp = self._try_stored(anim_id, version, opcode, intensity)
                self.log(f"RX ◀── {hexdump(resp) if resp else '[no reply]'}")
            if resp is None and self.wake_cube():
                with self.lock:
                    ok, resp = self._try_stored(anim_id, version, opcode, intensity)
                    self.log(f"RX ◀── {hexdump(resp) if resp else '[no reply]'}")
            with self.lock:
                if resp is None:
                    self.log(f"[-] No answer from the cube, so ID {anim_id} v{version} wasn't tried.")
                    self.status_callback("The cube isn't answering", "#f59e0b")
                elif ok:
                    self.log(f"[+] The cube has ID {anim_id} v{version} stored and is playing it.")
                    self.status_callback(f"Playing '{name}'" if name else f"Playing stored animation {anim_id}", "#10b981")
                else:
                    self.log(f"[-] ID {anim_id} v{version} is not stored on the cube (it asked for the data).")
                    self.status_callback(f"ID {anim_id} not stored on cube", "#f59e0b")

        self.active_worker = threading.Thread(target=_worker, daemon=True)
        self.active_worker.start()

    def scan_stored(self, first, last, versions=(1,), opcode=OPCODE_MOOD, intensity=255, on_hit=None):
        """Walk a range of IDs looking for animations still kept in the cube's memory."""
        if not self.ip:
            self.log("[ERROR] No IP configured.")
            return
        self.stop_current_action()

        def _worker():
            hits = []
            total = (last - first + 1) * len(versions)
            self.log(f"[*] Scanning IDs {first}-{last}, versions {list(versions)}, type 0x{opcode:02X} ({total} probes)")
            started = time.time()
            with self.lock:
                n = 0
                for anim_id in range(first, last + 1):
                    for ver in versions:
                        if self.stop_flag.is_set():
                            self.log("[!] Scan stopped.")
                            self.status_callback(f"Scan stopped, {len(hits)} found", "#f59e0b")
                            return
                        ok, _ = self._try_stored(anim_id, ver, opcode, intensity)
                        n += 1
                        if ok:
                            hits.append((anim_id, ver))
                            self.log(f"[+] Found stored animation: ID {anim_id}, version {ver}")
                            if on_hit:
                                on_hit(anim_id, ver)
                        if n % 25 == 0:
                            rate = n / max(0.1, time.time() - started)
                            left = int((total - n) / max(rate, 0.1))
                            self.status_callback(f"Scanning stored IDs... ID {anim_id} ({n}/{total}), {len(hits)} found, "
                                                 f"about {left // 60} min {left % 60} s left", "#3b82f6")
                        time.sleep(0.01)
            self.log(f"[+] Scan finished: {len(hits)} stored animation(s) found: {hits}")
            self.status_callback(f"Scan finished, {len(hits)} found", "#10b981")

        self.active_worker = threading.Thread(target=_worker, daemon=True)
        self.active_worker.start()

    # ---------------- hardware timers (0x80) ----------------
    @staticmethod
    def _next_at(hh, mm, after=None):
        base = after or datetime.now()
        t = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if t <= base:
            t = t + timedelta(days=1)
        return t

    @staticmethod
    def _timer_prefix(timer_id, when, enabled, action, weekday):
        now = datetime.now()
        return [0x80, (timer_id >> 24) & 0xFF, (timer_id >> 16) & 0xFF, (timer_id >> 8) & 0xFF, timer_id & 0xFF,
                (now.year - 2000) & 0xFF, now.month, now.day, now.hour, now.minute, now.second,
                when.month, when.day, when.hour, when.minute,
                1 if enabled else 0, action & 0xFF, weekday & 0xFF]

    @classmethod
    def build_switch_off_timer(cls, timer_id, hh, mm, weekday=EVERY_DAY, enabled=True):
        """22-byte packet: switch the cube off at hh:mm (onCreateScheduleSwitchOffPacket)."""
        pkt = cls._timer_prefix(timer_id, cls._next_at(hh, mm), enabled, 0, weekday) + [0, 0, 0x0D, 0x0A]
        return bytes(pkt)

    @classmethod
    def build_lamp_timer(cls, timer_id, start, end, rgb, intensity, weekday=EVERY_DAY, enabled=True):
        """30-byte packet: solid colour from start to end (onCreateScheduleLampOnPacket)."""
        st = cls._next_at(*start)
        en = cls._next_at(*end, after=st)
        pkt = cls._timer_prefix(timer_id, st, enabled, 1, weekday) + \
            [clamp(rgb[0]), clamp(rgb[1]), clamp(rgb[2]), clamp(intensity),
             en.month, en.day, en.hour, en.minute, 0, 0, 0x0D, 0x0A]
        return bytes(pkt)

    @classmethod
    def build_animation_timer(cls, timer_id, start, end, num_frames, interval_param, anim_id, version,
                              intensity, weekday=EVERY_DAY, enabled=True, action=2):
        """38-byte packet: play a mood animation from start to end (onCreateScheduleAnimationOnPacket)."""
        st = cls._next_at(*start)
        en = cls._next_at(*end, after=st)
        pkt = cls._timer_prefix(timer_id, st, enabled, action, weekday) + \
            [(num_frames >> 8) & 0xFF, num_frames & 0xFF, interval_param & 0xFF,
             (anim_id >> 24) & 0xFF, (anim_id >> 16) & 0xFF, (anim_id >> 8) & 0xFF, anim_id & 0xFF,
             version & 0xFF, clamp(intensity), en.month, en.day, en.hour, en.minute,
             0, 0, 0, 0, 0, 0x0D, 0x0A]
        return bytes(pkt)

    def send_timer(self, packet, label, frames=None, raw_binary=None):
        """Send a timer packet; an animation timer streams its frames if the cube asks."""
        if not self.ip:
            self.log("[ERROR] No IP configured.")
            return
        self.stop_current_action()

        def _worker():
            with self.lock:
                try:
                    fmt = self.pick_upload_format() if (frames or raw_binary) else None
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(3.5)
                    s.connect((self.ip, self.TCP_PORT))
                    self._tx(s, packet)
                    resp = self._rx(s, 64)
                    if (frames or raw_binary) and resp and len(resp) > 5 and resp[4] == 1:
                        self._stream_frames(s, frames, fmt, label, raw_binary, progress=False)
                    time.sleep(0.2)
                    s.close()
                    positive = bool(resp) and (resp[3] == 0 if len(resp) > 4 else resp[1] == 0)
                    if positive or (frames and resp and len(resp) > 5 and resp[4] == 1):
                        self.log(f"[+] Timer saved on the cube: {label}")
                        self.status_callback(f"Timer saved: {label}", "#10b981")
                    else:
                        self.log(f"[-] Cube did not confirm the timer ({label}); see the RX bytes above.")
                        self.status_callback("Timer not confirmed - see the Debug tab", "#f59e0b")
                except Exception as e:
                    self.log(f"[ERROR] Timer failed: {e}")
                    self.status_callback("Timer failed - check connection", "#ef4444")

        self.active_worker = threading.Thread(target=_worker, daemon=True)
        self.active_worker.start()

    SETUP_AP_SSID = "Tittle-AP"
    SETUP_AP_IP = "192.168.1.1"
    SETUP_CALLBACK_PORT = None          # the cube calls back on 9999 (tests use another port)

    @staticmethod
    def build_wifi_config_packet(ssid, password, my_ip):
        """70 07 <ssid> 00 <password> 00 <this computer's IP, 4 bytes> 0D 0A (official app: onCreateStandardConfigPacket)."""
        ip = bytes(int(p) & 0xFF for p in my_ip.split("."))
        return bytes([0x70, 0x07]) + ssid.encode("utf-8") + b"\x00" + password.encode("utf-8") + b"\x00" + ip + b"\r\n"

    def wifi_setup(self, ssid, password, wait_s=40):
        """Give a reset cube the home Wi-Fi. This computer must be connected to the cube's own open network
        'Tittle-AP' first. Same steps as the official app: send the Wi-Fi name and password to 192.168.1.1, wait
        for the cube to call back with its details, then tell it setup is finished (70 09)."""
        ap, port = self.SETUP_AP_IP, self.TCP_PORT
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect((ap, port))
            my_ip = probe.getsockname()[0]
            probe.close()
        except Exception as e:
            self.log(f"[ERROR] Can't reach {ap}: {e}")
            return False, "This computer isn't connected to the cube's 'Tittle-AP' Wi-Fi."
        self.log(f"[*] Wi-Fi setup: this computer is {my_ip}, sending '{ssid}' to the cube at {ap}")
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(("0.0.0.0", self.SETUP_CALLBACK_PORT or port))
            server.listen(1)
        except Exception as e:
            self.log(f"[!] Couldn't listen on port {port} for the cube's reply ({e}); carrying on anyway")
            server.close()
            server = None
        try:
            ack = None
            for attempt in range(3):
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(10)
                    s.connect((ap, port))
                    self.log(f"TX ──▶ 70 07 <Wi-Fi name> 00 <password hidden> 00 <{my_ip}> 0D 0A")
                    s.sendall(self.build_wifi_config_packet(ssid, password, my_ip))
                    ack = self._rx(s, 64)
                    s.close()
                    break
                except Exception as e:
                    self.log(f"[!] Attempt {attempt + 1}: {e}")
                    time.sleep(1)
            if not ack:
                return False, "The cube didn't answer. Check this computer is on 'Tittle-AP' and try again."
            if not (ack[3] == 0 if len(ack) > 4 else len(ack) > 1 and ack[1] == 0):
                return False, "The cube refused the Wi-Fi details. Check the name and password."
            info = None
            if server:
                server.settimeout(wait_s)
                try:
                    c, addr = server.accept()
                    c.settimeout(3)
                    info = self._rx(c, 1024)
                    c.close()
                    self.log(f"[+] The cube called back from {addr[0]}")
                except socket.timeout:
                    self.log("[!] The cube didn't call back; finishing setup anyway")
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(5)
                s.connect((ap, port))
                self._tx(s, bytes([0x70, 0x09, 0x0D, 0x0A]))
                self._rx(s, 64)
                s.close()
            except Exception as e:
                self.log(f"[i] Finish message not answered ({e}); the cube may already be joining your Wi-Fi")
            name = ""
            if info and len(info) > 6:
                name = info[6:26].split(b"\x00")[0].decode("utf-8", "replace").strip()
            if info is None:
                return True, f"The cube accepted the details for '{ssid}' but didn't confirm; give it a minute."
            return True, (f"'{name}' " if name else "The cube ") + f"has your Wi-Fi details and is joining '{ssid}'."
        finally:
            if server:
                server.close()

    def query_timers(self, count=10):
        """81 00 idx asks for stored timers. The official app has this packet but never sends it, and
        Ben's cube does not answer it, so: ask slot 0 with a short wait and stop at the first silence."""
        found = []
        if not self.ip:
            self.log("[ERROR] No IP configured.")
            return found
        for idx in range(count):
            resp = None
            with self.lock:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(1.5)
                    s.connect((self.ip, self.TCP_PORT))
                    self._tx(s, bytes([0x81, 0x00, idx & 0xFF, 0x0D, 0x0A]))
                    resp = self._rx(s, 64)
                    s.close()
                except Exception as e:
                    self.log(f"[ERROR] {e}")
            if not resp or resp[0] != 0x81:
                break
            if len(resp) >= 15:
                tid = int.from_bytes(bytes(resp[3:7]), "big")
                desc = f"slot {idx}: ID {tid}, {resp[7]:02d}-{resp[8]:02d} {resp[9]:02d}:{resp[10]:02d}, enabled {resp[11]}"
                if len(resp) >= 29:
                    desc += (f", type {resp[12]}, repeat 0x{resp[13]:02X}, intensity {resp[14]}, "
                             f"RGB {resp[15]},{resp[16]},{resp[17]}, until {resp[19]:02d}-{resp[20]:02d} "
                             f"{resp[21]:02d}:{resp[22]:02d}")
                self.log(f"[+] Timer {desc}")
                found.append(desc)
        if not found:
            self.log("[i] This cube doesn't report its timers (the official app never asked either). That does not "
                     "mean the timers are missing: the cube said OK when they were saved, and they keep working "
                     "with the app closed.")
            sched = read_config(self.config_file).get("schedule")
            if sched:
                self.log(f"[i] What this app last saved to the cube: {sched}")
        return found

    @staticmethod
    def build_clock_header(face_id, intensity=255, version=1, now=None):
        """0x34: current time + the clock face ID/version. The cube then keeps time on its own."""
        now = now or datetime.now()
        return bytes([0x34, now.hour, now.minute, now.second,
                      (face_id >> 24) & 0xFF, (face_id >> 16) & 0xFF, (face_id >> 8) & 0xFF, face_id & 0xFF,
                      clamp(intensity), version & 0xFF,
                      (now.year - 2000) & 0xFF, now.month, now.day,
                      0, 0, 0, 0, 0, 0x0D, 0x0A])

    def sync_clock(self, face_id=1, intensity=255, version=1):
        """0x34: set the time and show a clock face the cube has stored (ID + version)."""
        self._remember("sync_clock", face_id, intensity, version)
        now = datetime.now()
        header = self.build_clock_header(face_id, intensity, version, now)
        self._header_expect_stored("Clock", header, f"Clock synced: {now.strftime('%H:%M:%S')}", "#10b981")

    def set_music_mode(self, style_id=1, sensitivity=60, intensity=255, version=1):
        """0x41: start the cube's microphone visualizer with a stored visualizer ID + version."""
        self._remember("set_music_mode", style_id, sensitivity, intensity, version)
        header = bytes([0x41, (style_id >> 24) & 0xFF, (style_id >> 16) & 0xFF, (style_id >> 8) & 0xFF, style_id & 0xFF,
                        clamp(intensity), clamp(sensitivity), version & 0xFF,
                        0, 0, 0, 0, 0, 0x0D, 0x0A])
        self._header_expect_stored("Music mode", header, "Music visualizer active (listening)", "#8b5cf6")


class All3DGenerators:

    # 9. JELLYFISH (Pulsing dome locomotion)
    @staticmethod
    def jellyfish(t, f, total):
        buf = bytearray(512 * 3)
        phase = (f / total) * 2 * math.pi
        jz = 2.0 + 3.5 * (1.0 + math.sin(phase)) * 0.5
        for z in range(8):
            for y in range(8):
                for x in range(8):
                    dx = x - 3.5; dy = y - 3.5
                    r = math.hypot(dx, dy)
                    idx = (z * 64) + (y * 8) + x
                    if abs(z - jz) < 1.1 and r < 3.2:
                        buf[idx*3] = 40; buf[idx*3 + 1] = 210; buf[idx*3 + 2] = 255
                    elif z < jz and 1.5 < r < 3.2 and (x % 2 == 0 or y % 2 == 0):
                        fade = max(0.0, 1.0 - (jz - z) / 4.0)
                        buf[idx*3] = clamp(160 * fade); buf[idx*3 + 1] = clamp(60 * fade); buf[idx*3 + 2] = clamp(240 * fade)
        return buf

    # 13. CLOCK FACE (Static 3D Digits)
    @staticmethod
    def clock_face(hh, mm):
        """Analog clock on the front face (y = 0..1): rim, 12 o'clock marker, hour and minute hands."""
        buf = bytearray(512 * 3)

        def put(x, z, c, depth=(0,)):
            if 0 <= x < 8 and 0 <= z < 8:
                for y in depth:
                    idx = voxel_index(x, y, z)
                    buf[idx*3], buf[idx*3 + 1], buf[idx*3 + 2] = c

        for i in range(8):
            for x, z in ((i, 0), (i, 7), (0, i), (7, i)):
                put(x, z, (10, 60, 140))
        put(3, 7, (255, 255, 255)); put(4, 7, (255, 255, 255))

        def hand(angle, length, color):
            steps = int(length / 0.2)
            for s in range(steps + 1):
                d = s * 0.2
                put(int(math.floor(3.5 + d * math.sin(angle) + 0.5)),
                    int(math.floor(3.5 + d * math.cos(angle) + 0.5)), color, depth=(0, 1))

        hour_angle = ((hh % 12) + mm / 60.0) / 12.0 * 2 * math.pi
        min_angle = (mm / 60.0) * 2 * math.pi
        hand(min_angle, 3.4, (40, 255, 160))
        hand(hour_angle, 2.0, (255, 150, 30))
        put(3, 3, (255, 220, 50), depth=(0, 1))
        return buf

    # ------------------------------------------------------------------
    # New presets (designed with Claude). Each loops seamlessly over `total` frames.
    # ------------------------------------------------------------------
    @staticmethod
    def _put(buf, x, y, z, rgb, scale=1.0):
        if 0 <= x < 8 and 0 <= y < 8 and 0 <= z < 8:
            i = ((z * 64) + (y * 8) + x) * 3
            buf[i] = clamp(rgb[0] * scale); buf[i + 1] = clamp(rgb[1] * scale); buf[i + 2] = clamp(rgb[2] * scale)

    @staticmethod
    def _hsv(h, s=1.0, v=1.0):
        r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
        return (r * 255, g * 255, b * 255)

    @staticmethod
    def vortex(t, f, total):
        """Swirling plasma funnel: three spiral arms widening towards the top."""
        buf = bytearray(512 * 3)
        phase = (f / total) * 2 * math.pi
        for z in range(8):
            rz = 0.8 + z * 0.42
            for y in range(8):
                for x in range(8):
                    dx, dy = x - 3.5, y - 3.5
                    r = math.hypot(dx, dy)
                    if abs(r - rz) < 0.75:
                        arm = math.sin(3 * math.atan2(dy, dx) - z * 0.7 - phase)
                        if arm > 0.1:
                            hue = 0.75 - 0.25 * (z / 7.0)
                            All3DGenerators._put(buf, x, y, z, All3DGenerators._hsv(hue, 0.85, 1.0), arm)
        return buf

    @staticmethod
    def aurora(t, f, total):
        """Northern lights: green-to-violet curtains rippling across the top half."""
        buf = bytearray(512 * 3)
        phase = (f / total) * 2 * math.pi
        for y in range(8):
            for x in range(8):
                h = 5.0 + 1.3 * math.sin(x * 0.7 + phase) + 0.9 * math.cos(y * 0.55 - phase)
                curtain = 0.55 + 0.45 * math.sin(x * 0.9 - y * 0.4 + 2 * phase)
                for z in range(8):
                    v = math.exp(-((z - h) ** 2) / 1.6) * curtain
                    if v > 0.08:
                        hue = 0.36 + 0.42 * max(0.0, (z - 3) / 4.0)
                        All3DGenerators._put(buf, x, y, z, All3DGenerators._hsv(hue, 0.9, 1.0), v)
        return buf

    @staticmethod
    def lava_lamp(t, f, total):
        """Three warm blobs rising, merging and sinking inside a dim purple glass."""
        buf = bytearray(512 * 3)
        phase = (f / total) * 2 * math.pi
        blobs = [(3.5 + 1.6 * math.sin(phase), 3.5 + 1.2 * math.cos(phase), 3.5 + 3.0 * math.sin(phase), 2.2),
                 (3.0 + 1.4 * math.cos(phase), 4.0 + 1.5 * math.sin(phase), 3.5 - 3.0 * math.sin(phase), 1.9),
                 (4.5 + 1.0 * math.sin(2 * phase), 3.0, 3.5 + 2.5 * math.cos(phase), 1.6)]
        for z in range(8):
            for y in range(8):
                for x in range(8):
                    field = sum(r * r / ((x - bx) ** 2 + (y - by) ** 2 + (z - bz) ** 2 + 0.3) for bx, by, bz, r in blobs)
                    if field > 1.0:
                        heat = min(1.0, (field - 1.0) / 2.0)
                        All3DGenerators._put(buf, x, y, z, (255, 60 + 150 * heat, 20 * heat))
                    elif x in (0, 7) or y in (0, 7):
                        All3DGenerators._put(buf, x, y, z, (25, 0, 35))
        return buf

    @staticmethod
    def galaxy(t, f, total):
        """A spiral galaxy turning slowly, thin at the edges with a bright core."""
        buf = bytearray(512 * 3)
        phase = (f / total) * 2 * math.pi
        for z in range(8):
            for y in range(8):
                for x in range(8):
                    dx, dy, dz = x - 3.5, y - 3.5, z - 3.5
                    r = math.hypot(dx, dy)
                    thickness = 1.6 - r * 0.25
                    if abs(dz) > max(0.45, thickness):
                        continue
                    if r < 1.0:
                        All3DGenerators._put(buf, x, y, z, (255, 230, 170))
                        continue
                    arm = math.cos(2 * math.atan2(dy, dx) - r * 1.3 + phase)
                    if arm > 0.35:
                        All3DGenerators._put(buf, x, y, z, All3DGenerators._hsv(0.62 + 0.1 * arm, 0.7, 1.0), arm)
        return buf

    @staticmethod
    def heartbeat(t, f, total):
        """A 3D heart that beats twice, then rests."""
        buf = bytearray(512 * 3)
        p = f / total
        beat = math.exp(-((p - 0.1) ** 2) / 0.002) + 0.7 * math.exp(-((p - 0.28) ** 2) / 0.002)
        scale = 3.0 * (1.0 + 0.18 * beat)
        for z in range(8):
            for y in range(8):
                for x in range(8):
                    X, Y, Z = (x - 3.5) / scale * 1.2, (y - 3.5) / scale * 1.2, (z - 3.2) / scale * 1.2
                    v = (X * X + 2.25 * Y * Y + Z * Z - 1) ** 3 - X * X * Z ** 3 - 0.1125 * Y * Y * Z ** 3
                    if v < 0:
                        All3DGenerators._put(buf, x, y, z, (255, 20, 60), 0.55 + 0.45 * beat)
        return buf

    @staticmethod
    def plasma(t, f, total):
        """Soft rainbow plasma flowing through the whole cube."""
        buf = bytearray(512 * 3)
        phase = (f / total) * 2 * math.pi
        for z in range(8):
            for y in range(8):
                for x in range(8):
                    v = (math.sin(x * 0.6 + phase) + math.sin(y * 0.5 - phase) +
                         math.sin((x + y + z) * 0.35 + phase) + math.sin(math.hypot(x - 3.5, z - 3.5) * 0.8 - phase))
                    All3DGenerators._put(buf, x, y, z, All3DGenerators._hsv(v / 8.0 + f / total, 1.0, 0.9))
        return buf

    @staticmethod
    def rainbow_spin(t, f, total):
        """A rainbow wheel spinning around the vertical axis, hue rising with height."""
        buf = bytearray(512 * 3)
        phase = f / total
        for z in range(8):
            for y in range(8):
                for x in range(8):
                    ang = math.atan2(y - 3.5, x - 3.5) / (2 * math.pi)
                    if math.hypot(x - 3.5, y - 3.5) > 1.2:
                        All3DGenerators._put(buf, x, y, z, All3DGenerators._hsv(ang + phase + z * 0.03, 1.0, 0.8))
        return buf

    # ------------------------------------------------------------------
    # Smooth rendering helpers (r5): sub-voxel splatting so moving points glide
    # between LEDs instead of jumping a whole voxel per frame.
    # ------------------------------------------------------------------
    @staticmethod
    def _splat(buf, fx, fy, fz, rgb, scale=1.0):
        """Draw a point at a fractional position, spreading it over the 8 nearest voxels."""
        x0, y0, z0 = math.floor(fx), math.floor(fy), math.floor(fz)
        ax, ay, az = fx - x0, fy - y0, fz - z0
        for dx, wx in ((0, 1 - ax), (1, ax)):
            for dy, wy in ((0, 1 - ay), (1, ay)):
                for dz, wz in ((0, 1 - az), (1, az)):
                    w = wx * wy * wz
                    x, y, z = x0 + dx, y0 + dy, z0 + dz
                    if w <= 0.02 or not (0 <= x < 8 and 0 <= y < 8 and 0 <= z < 8):
                        continue
                    w = w ** 0.8                     # LEDs look dim at low values, lift them a bit
                    i = ((z * 64) + (y * 8) + x) * 3
                    for k in range(3):
                        buf[i + k] = max(buf[i + k], clamp(rgb[k] * scale * w))

    @staticmethod
    def _smooth(v, lo, hi):
        """0 below lo, 1 above hi, a soft ramp in between (avoids voxels popping on/off)."""
        if v <= lo:
            return 0.0
        if v >= hi:
            return 1.0
        u = (v - lo) / (hi - lo)
        return u * u * (3 - 2 * u)

    # 1. FAIRIES 2.0: 8 glowing points on closed Lissajous paths, so the loop has no jump
    FAIRY_PATHS = [((255, 120, 200), (1, 2, 1), (0.0, 1.1, 2.0)), ((80, 220, 255), (2, 1, 1), (1.3, 0.2, 0.7)),
                   ((255, 230, 80), (1, 1, 2), (2.6, 1.9, 0.4)), ((180, 100, 255), (2, 1, 2), (3.9, 2.8, 1.6)),
                   ((100, 255, 180), (1, 2, 2), (5.2, 0.6, 2.9)), ((255, 160, 80), (1, 1, 1), (0.8, 4.1, 3.3)),
                   ((240, 90, 140), (2, 2, 1), (4.4, 3.5, 5.0)), ((120, 200, 255), (1, 2, 1), (3.1, 5.3, 4.2))]

    @staticmethod
    def fairies(t, f, total):
        buf = bytearray(512 * 3)
        for c, (kx, ky, kz), (ox, oy, oz) in All3DGenerators.FAIRY_PATHS:
            for back, fade in ((0.0, 1.0), (0.012, 0.45), (0.024, 0.2)):     # head + stardust trail
                ph = ((f / total) - back) * 2 * math.pi
                All3DGenerators._splat(buf, 3.5 + 3.2 * math.sin(kx * ph + ox), 3.5 + 3.2 * math.sin(ky * ph + oy),
                                       3.5 + 3.2 * math.sin(kz * ph + oz), c, fade)
        return buf

    # 2. FIRE: flames that rise and flicker, soft edges
    @staticmethod
    def fire(t, f, total):
        buf = bytearray(512 * 3)
        phase = (f / total) * 2 * math.pi
        for z in range(8):
            for y in range(8):
                for x in range(8):
                    flick = (math.sin(x * 1.1 + phase * 3 - z * 0.6) * math.cos(y * 1.1 - phase * 2 - z * 0.5)
                             + 0.5 * math.sin((x + y) * 0.7 + phase * 5))
                    heat = (8 - z) / 8.0 + 0.28 * flick
                    v = All3DGenerators._smooth(heat, 0.3, 0.55)
                    if v > 0:
                        All3DGenerators._put(buf, x, y, z, (255 * min(1.0, heat * 1.3), 170 * max(0.0, heat - 0.3),
                                                            40 * max(0.0, heat - 0.8)), v)
        return buf

    # 3. WAVE: ocean swell with a soft surface
    @staticmethod
    def wave(t, f, total):
        buf = bytearray(512 * 3)
        phase = (f / total) * 2 * math.pi
        for y in range(8):
            for x in range(8):
                h = 3.5 + 2.6 * math.sin(x * 0.7 + phase) * math.cos(y * 0.4 + phase)
                for z in range(8):
                    v = math.exp(-((z - h) ** 2) / 1.2) if z > h else (0.35 if z < h - 1 else 0.7)
                    if v > 0.05:
                        All3DGenerators._put(buf, x, y, z, (20 + z * 15, 110 + z * 16, 255), v)
        return buf

    @staticmethod
    def _drops(buf, f, total, count, seed, color, trail_color, trail=2, speeds=(2, 3), splash=False):
        """Falling drops at sub-voxel positions. Each drop falls a whole number of times per loop."""
        rnd = random.Random(seed)
        p = f / total
        for _ in range(count):
            x, y = rnd.randrange(8), rnd.randrange(8)
            k, off = rnd.choice(speeds), rnd.random()
            s = (p * k + off) % 1.0
            fz = 8.5 - s * 10.0                       # from above the top to below the floor
            All3DGenerators._splat(buf, x, y, fz, color)
            for j in range(1, trail + 1):
                All3DGenerators._splat(buf, x, y, fz + j, trail_color, 1.0 - j / (trail + 1.0))
            if splash and -1.2 < fz < 0.3:
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    All3DGenerators._put(buf, x + dx, y + dy, 0, trail_color, 0.6)

    # 4. RAIN: blue drops with a little splash on the floor
    @staticmethod
    def rain(t, f, total):
        buf = bytearray(512 * 3)
        All3DGenerators._drops(buf, f, total, 18, 7, (200, 230, 255), (30, 90, 230), trail=1, splash=True)
        return buf

    # 5. SNOW: slow flakes drifting side to side
    @staticmethod
    def snow(t, f, total):
        buf = bytearray(512 * 3)
        rnd = random.Random(11)
        p = f / total
        for _ in range(16):
            ox, oy, off, k = rnd.uniform(1, 6), rnd.uniform(1, 6), rnd.random(), rnd.choice((1, 2))
            ph = 2 * math.pi * (p * k + off)
            s = (p * k + off) % 1.0
            All3DGenerators._splat(buf, ox + 1.1 * math.sin(ph * 2), oy + 1.1 * math.cos(ph), 8.0 - s * 9.0,
                                   (235, 240, 255))
        return buf

    # 6. SPARKLE: stars that fade in and out, then reappear somewhere else
    @staticmethod
    def sparkle(t, f, total):
        buf = bytearray(512 * 3)
        p = f / total
        for i in range(22):
            k = 2 + i % 3
            u = p * k + (i * 0.618) % 1.0
            cycle, local = int(math.floor(u)) % k, u % 1.0
            pos = random.Random(i * 100 + cycle).randrange(512)
            b = math.sin(math.pi * local) ** 2
            All3DGenerators._put(buf, pos % 8, (pos // 8) % 8, pos // 64, (255, 235, 200 + 55 * (i % 2)), b)
        return buf

    # 7. FIREWORKS: two rockets per loop, each bursting into a fading shell
    @staticmethod
    def fireworks(t, f, total):
        buf = bytearray(512 * 3)
        p = f / total
        for n, (cx, cy, top, hue) in enumerate(((3.5, 3.5, 5.5, 0.85), (2.5, 4.5, 6.0, 0.12))):
            u = (p + n * 0.5) % 1.0
            if u < 0.3:                                               # rocket climbing
                z = u / 0.3 * top
                All3DGenerators._splat(buf, cx, cy, z, (255, 255, 230))
                All3DGenerators._splat(buf, cx, cy, z - 0.9, (255, 140, 30), 0.5)
            else:                                                     # burst
                b = (u - 0.3) / 0.7
                radius, fade = 0.5 + 3.6 * (1 - (1 - b) ** 2), (1 - b) ** 1.5
                col = All3DGenerators._hsv(hue + 0.08 * math.sin(b * 6), 0.8, 1.0)
                for z in range(8):
                    for y in range(8):
                        for x in range(8):
                            d = math.sqrt((x - cx) ** 2 + (y - cy) ** 2 + (z - top + b * 1.5) ** 2)
                            v = math.exp(-((d - radius) ** 2) / 0.35) * fade
                            if v > 0.05:
                                All3DGenerators._put(buf, x, y, z, col, v)
        return buf

    # 8. 4D TESSERACT: a real hypercube wireframe rotating in 4D, drawn with soft sub-voxel lines
    TESS_VERTS = [tuple((1 if (n >> k) & 1 else -1) for k in range(4)) for n in range(16)]
    TESS_EDGES = [(a, b) for a in range(16) for b in range(a + 1, 16) if bin(a ^ b).count("1") == 1]

    @staticmethod
    def tesseract(t, f, total):
        buf = bytearray(512 * 3)
        a = (f / total) * 2 * math.pi                  # one full 4D turn per loop, so the loop is seamless
        ca, sa, cb, sb = math.cos(a), math.sin(a), math.cos(a * 2), math.sin(a * 2)
        pts = []
        for x, y, z, w in All3DGenerators.TESS_VERTS:
            x, w = x * ca - w * sa, x * sa + w * ca     # rotate in the XW plane
            y, z = y * cb - z * sb, y * sb + z * cb     # and slowly in YZ
            k = 2.4 / (3.2 - w)                         # 4D -> 3D perspective
            pts.append((3.5 + x * k * 1.9, 3.5 + y * k * 1.9, 3.5 + z * k * 1.9, w))   # outer cube fills the 8x8x8
        for i, j in All3DGenerators.TESS_EDGES:
            p, q = pts[i], pts[j]
            depth = (p[3] + q[3]) / 2                   # inner (far in 4D) cube violet, outer cyan
            col = (int(110 + 70 * depth), int(110 - 60 * depth), 255)
            for s in range(9):
                u = s / 8.0
                All3DGenerators._splat(buf, p[0] + (q[0] - p[0]) * u, p[1] + (q[1] - p[1]) * u,
                                       p[2] + (q[2] - p[2]) * u, col, 0.85)
        return buf

    # 10. DNA DOUBLE HELIX
    @staticmethod
    def dna(t, f, total):
        buf = bytearray(512 * 3)
        phase = (f / total) * 2 * math.pi
        for zi in range(15):
            z = zi * 0.5
            theta = z * 0.85 + phase
            c, s_ = math.cos(theta), math.sin(theta)
            All3DGenerators._splat(buf, 3.5 + 2.8 * c, 3.5 + 2.8 * s_, z, (255, 30, 140))
            All3DGenerators._splat(buf, 3.5 - 2.8 * c, 3.5 - 2.8 * s_, z, (20, 220, 255))
            if zi % 4 == 0:
                for r in (-1.4, 0.0, 1.4):
                    All3DGenerators._splat(buf, 3.5 + r * c, 3.5 + r * s_, z, (255, 220, 80), 0.7)
        return buf

    # 11. ACOUSTIC RIPPLE
    @staticmethod
    def acoustic(t, f, total):
        buf = bytearray(512 * 3)
        phase = (f / total) * 2 * math.pi
        for z in range(8):
            for y in range(8):
                for x in range(8):
                    dist = math.sqrt((x - 3.5) ** 2 + (y - 3.5) ** 2 + (z - 3.5) ** 2)
                    v = All3DGenerators._smooth(math.sin(dist * 1.4 - phase * 2), 0.2, 0.8)
                    if v > 0.03:
                        All3DGenerators._put(buf, x, y, z, (120 + 130 * math.sin(dist + phase), 180 + 70 * math.cos(dist), 255), v)
        return buf

    # 12. MATRIX 3D RAIN: green code streams with long tails
    @staticmethod
    def matrix_rain(t, f, total):
        buf = bytearray(512 * 3)
        All3DGenerators._drops(buf, f, total, 30, 23, (220, 255, 220), (0, 230, 60), trail=3, speeds=(1, 2))
        return buf

    @staticmethod
    def warp_speed(t, f, total):
        """Starfield rushing towards the front face with streaks."""
        buf = bytearray(512 * 3)
        rnd = random.Random(42)
        for _ in range(22):
            sx, sz, off, speed = rnd.randrange(8), rnd.randrange(8), rnd.random(), rnd.choice((1, 2))
            yy = 9.0 - ((f / total * speed + off) % 1.0) * 11.0
            for k in range(3):
                All3DGenerators._splat(buf, sx, yy + k, sz, (200, 220, 255), 1.0 - 0.33 * k)
        return buf

    @staticmethod
    def bouncing_balls(t, f, total):
        """Three coloured balls bouncing off the floor, leaving short trails."""
        buf = bytearray(512 * 3)
        balls = [((255, 60, 60), 2, 0.0, 1), ((60, 255, 120), 3, 1.3, 2), ((80, 140, 255), 2, 2.6, 3)]
        for color, k, off, m in balls:
            for back, fade in ((0.03, 0.3), (0.0, 1.0)):
                ph = (f / total - back) * 2 * math.pi
                cx, cy = 3.5 + 2.6 * math.sin(m * ph + off), 3.5 + 2.6 * math.cos(ph + off * 2)
                cz = 1.0 + 5.0 * abs(math.sin(k * ph + off))
                for z in range(8):                       # a soft ball about 3 LEDs across
                    for y in range(8):
                        for x in range(8):
                            d2 = (x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2
                            if d2 < 2.6:
                                All3DGenerators._put(buf, x, y, z, color, fade * min(1.0, 1.25 - d2 / 2.6))
        return buf

    # ---- Moods named in the original Indiegogo campaign (look-alikes, not the real data) ----
    @staticmethod
    def sunset(t, f, total):
        """A warm sun sinking and rising behind a gradient sky."""
        buf = bytearray(512 * 3)
        phase = (f / total) * 2 * math.pi
        sun_z = 3.0 + 2.5 * math.cos(phase)
        for z in range(8):
            sky = All3DGenerators._hsv(0.02 + 0.07 * (z / 7.0) - 0.04 * (1 - math.cos(phase)) / 2, 0.9, 0.25 + 0.06 * z)
            for y in range(8):
                for x in range(8):
                    d = math.sqrt((x - 3.5) ** 2 + (y * 0.5) ** 2 + (z - sun_z) ** 2)
                    sun = math.exp(-(d ** 2) / 3.0)
                    All3DGenerators._put(buf, x, y, z, (sky[0] + 255 * sun, sky[1] + 170 * sun, sky[2] + 40 * sun))
        return buf

    @staticmethod
    def constellation(t, f, total):
        """Fixed stars that twinkle, with lines that slowly light up between neighbours."""
        buf = bytearray(512 * 3)
        rnd = random.Random(5)
        stars = [(rnd.randrange(8), rnd.randrange(8), rnd.randrange(8)) for _ in range(12)]
        p = f / total
        for i, (a, b) in enumerate(zip(stars, stars[1:] + stars[:1])):
            glow = math.sin(math.pi * ((p * 2 + i / len(stars)) % 1.0)) ** 4
            for s in range(1, 8):
                u = s / 8.0
                All3DGenerators._splat(buf, a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u,
                                       a[2] + (b[2] - a[2]) * u, (60, 90, 200), 0.6 * glow)
        for i, (x, y, z) in enumerate(stars):
            tw = 0.6 + 0.4 * math.sin(2 * math.pi * (p * (2 + i % 3)) + i)
            All3DGenerators._put(buf, x, y, z, (255, 250, 220), tw)
        return buf

    @staticmethod
    def snow_globe(t, f, total):
        """Snow swirling round a sphere, then settling on a white floor."""
        buf = bytearray(512 * 3)
        rnd = random.Random(19)
        p = f / total
        for _ in range(26):
            r, a0, off, k = rnd.uniform(1.2, 3.4), rnd.uniform(0, 6.28), rnd.random(), rnd.choice((1, 2))
            u = (p * k + off) % 1.0
            ang = a0 + 2 * math.pi * p * 2
            All3DGenerators._splat(buf, 3.5 + r * math.cos(ang), 3.5 + r * math.sin(ang), 7.5 - u * 7.5,
                                   (230, 240, 255), 0.9)
        for y in range(8):
            for x in range(8):
                if math.hypot(x - 3.5, y - 3.5) < 3.8:
                    All3DGenerators._put(buf, x, y, 0, (120, 140, 170))
        return buf

    @staticmethod
    def confetti(t, f, total):
        """Bright confetti pieces in every colour, tumbling down."""
        buf = bytearray(512 * 3)
        rnd = random.Random(31)
        p = f / total
        for _ in range(30):
            x0, y0, off, k, hue = rnd.uniform(0, 7), rnd.uniform(0, 7), rnd.random(), rnd.choice((1, 2)), rnd.random()
            u = (p * k + off) % 1.0
            ph = 2 * math.pi * (p * k * 3 + off)
            All3DGenerators._splat(buf, x0 + 0.8 * math.sin(ph), y0 + 0.8 * math.cos(ph), 8.0 - u * 9.0,
                                   All3DGenerators._hsv(hue, 1.0, 1.0))
        return buf

    DIGITS = {  # 3x5 font, rows top to bottom
        "0": ["111", "101", "101", "101", "111"], "1": ["010", "110", "010", "010", "111"],
        "2": ["111", "001", "111", "100", "111"], "3": ["111", "001", "011", "001", "111"],
        "4": ["101", "101", "111", "001", "001"], "5": ["111", "100", "111", "001", "111"],
        "6": ["111", "100", "111", "101", "111"], "7": ["111", "001", "010", "010", "010"],
        "8": ["111", "101", "111", "101", "111"], "9": ["111", "101", "111", "001", "111"]}

    @staticmethod
    def time_frames(hh, mm, twelve_hour=False):
        """Two frames for the app-drawn clock: hours (warm) then minutes (cool), on the front two layers.
        In 12-hour mode the hour has no leading zero and a purple bar on top means PM."""
        frames = []
        pm = hh >= 12
        hour_text = f"{(hh % 12) or 12:>2d}" if twelve_hour else f"{hh:02d}"
        for text, color, marker in ((hour_text, (255, 140, 30), (0, 6)), (f"{mm:02d}", (40, 200, 255), (7, 6))):
            buf = bytearray(512 * 3)
            for d, ch in enumerate(text):
                x0 = 0 if d == 0 else 5
                if ch == " ":
                    continue
                for row, bits in enumerate(All3DGenerators.DIGITS[ch]):
                    for col, bit in enumerate(bits):
                        if bit == "1":
                            for y in (0, 1):
                                All3DGenerators._put(buf, x0 + col, y, 6 - row, color, 1.0 if y == 0 else 0.35)
            # small marker in the top corner tells hours (left) from minutes (right)
            All3DGenerators._put(buf, marker[0], 0, 7, (255, 255, 255))
            if twelve_hour and pm:
                for x in (3, 4):
                    All3DGenerators._put(buf, x, 0, 7, (190, 60, 255))
            frames.append(buf)
        return frames

    @staticmethod
    def pizza(t, f, total):
        """Easter egg: an upright pepperoni pizza slice spinning around the vertical axis."""
        buf = bytearray(512 * 3)
        theta = (f / total) * 2 * math.pi
        c, s_ = math.cos(theta), math.sin(theta)
        pepperoni = [(-1.0, 4.6), (0.9, 3.0), (1.2, 5.6), (-0.3, 2.0)]
        for z in range(8):
            for y in range(8):
                for x in range(8):
                    dx, dy = x - 3.5, y - 3.5
                    u, w = dx * c + dy * s_, -dx * s_ + dy * c
                    if abs(w) > 0.75 or abs(u) > 0.48 * (z + 0.8):
                        continue
                    if z >= 7:
                        col = (190, 95, 20)                                  # crust
                    elif any((u - pu) ** 2 + (z - pz) ** 2 < 0.8 for pu, pz in pepperoni):
                        col = (230, 25, 10)                                  # pepperoni
                    else:
                        col = (255, 185, 30)                                 # cheese
                    All3DGenerators._put(buf, x, y, z, col)
        return buf

    @staticmethod
    def music_equalizer(t, f, total):
        buf = bytearray(512 * 3)
        phase = (f / total) * 2 * math.pi
        for col_x in range(8):
            h = int(round(3.5 + 3.0 * math.sin(col_x * 0.8 + phase * 2)))
            for z in range(clamp(h) + 1):
                idx = (z * 64) + (3 * 8) + col_x
                buf[idx*3] = clamp(40 + z * 30)
                buf[idx*3 + 1] = clamp(255 - z * 20)
                buf[idx*3 + 2] = 120
        return buf


# ==============================================================================
# TKINTER GRAPHICAL USER INTERFACE
# ==============================================================================
# Smoothness (r5). Each preset has a natural loop length; the app fits as many frames into it as the
# chosen smoothness allows. More frames = smoother motion but a longer upload.
LOOP_SECONDS = {"fairies": 8.0, "fire": 3.0, "wave": 4.0, "rain": 3.0, "snow": 8.0, "sparkle": 4.0,
                "fireworks": 4.0, "vortex": 3.0, "matrix_rain": 4.0, "tesseract": 8.0, "jellyfish": 4.0,
                "dna": 4.0, "acoustic": 3.0, "aurora": 6.0, "lava_lamp": 10.0, "galaxy": 8.0, "heartbeat": 1.6,
                "warp_speed": 2.0, "plasma": 8.0, "bouncing_balls": 6.0, "rainbow_spin": 4.0, "pizza": 2.0,
                "sunset": 10.0, "constellation": 6.0, "snow_globe": 8.0, "confetti": 5.0}
SMOOTHNESS = {"light": (100, 40), "smooth": (50, 100), "ultra": (40, 150)}   # (target ms per frame, max frames)


def motion_plan(gen_func, smoothness="smooth", speed=1.0, fallback_s=2.0):
    """Return (num_frames, interval_ms) for a generator at the chosen smoothness and speed."""
    loop_s = LOOP_SECONDS.get(getattr(gen_func, "__name__", ""), fallback_s) / max(0.25, speed)
    frame_ms, max_frames = SMOOTHNESS.get(smoothness, SMOOTHNESS["smooth"])
    frames = max(12, min(max_frames, int(round(loop_s * 1000 / frame_ms))))
    interval = max(16, int(round(loop_s * 1000 / frames / 8.0)) * 8)   # the cube counts in 8 ms steps
    return frames, interval


def render_frames(generator_func, num_frames, interval_ms=100):
    return [normalize_frame(generator_func(i * (interval_ms / 1000.0), i, num_frames))
            for i in range(num_frames)]


# ─────────────────────────── Animation files (JSON / VOX) ───────────────────────────

class Animation:
    def __init__(self, name, frames, interval_ms=100, intensity=255, opcode=OPCODE_PAINT,
                 anim_id=None, version=1, raw_interval=None, raw_binary=None):
        self.name = name
        self.frames = [normalize_frame(f) for f in frames] or [bytearray(FRAME_BYTES)]
        self.interval_ms = int(interval_ms)
        self.intensity = clamp(intensity)
        self.opcode = opcode
        # Set for animations recovered from the official app, so the cube sees the original
        # ID / version / interval byte (and, for binary ones, the original data blob).
        self.anim_id = anim_id
        self.version = version
        self.raw_interval = raw_interval
        self.raw_binary = raw_binary

    def __len__(self):
        return len(self.frames)


def _parse_color(v):
    if isinstance(v, str):
        h = v.strip().lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
            raise ValueError(f"bad colour {v!r}")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        return clamp(v[0]), clamp(v[1]), clamp(v[2])
    if isinstance(v, int):
        return (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF
    raise ValueError(f"bad colour {v!r}")


def _parse_voxel_list(voxels):
    buf = bytearray(FRAME_BYTES)
    for v in voxels:
        if isinstance(v, dict):
            x, y, z = int(v["x"]), int(v["y"]), int(v["z"])
            if "color" in v or "colour" in v or "c" in v:
                rgb = _parse_color(v.get("color", v.get("colour", v.get("c"))))
            else:
                rgb = (clamp(v.get("r", 0)), clamp(v.get("g", 0)), clamp(v.get("b", 0)))
        else:
            x, y, z = int(v[0]), int(v[1]), int(v[2])
            rgb = _parse_color(v[3]) if len(v) == 4 else (clamp(v[3]), clamp(v[4]), clamp(v[5]))
        if 0 <= x < 8 and 0 <= y < 8 and 0 <= z < 8:
            i = voxel_index(x, y, z) * 3
            buf[i:i + 3] = bytes(rgb)
    return buf


def _parse_frame(fr):
    if isinstance(fr, str):
        compact = re.sub(r"\s", "", fr)
        if re.fullmatch(r"[0-9a-fA-F]*", compact) and len(compact) == FRAME_BYTES * 2:
            return bytearray(bytes.fromhex(compact))           # hex
        try:
            data = base64.b64decode(compact, validate=True)      # base64 (web editor)
        except Exception:
            data = None
        if data is None or len(data) != FRAME_BYTES:
            raise ValueError(f"frame string is neither {FRAME_BYTES}-byte hex nor base64")
        return bytearray(data)
    if isinstance(fr, dict):
        if "voxels" in fr:
            return _parse_voxel_list(fr["voxels"])
        if "data" in fr:
            return _parse_frame(fr["data"])
        raise ValueError("frame object needs 'voxels' or 'data'")
    if isinstance(fr, list):
        if len(fr) == FRAME_BYTES and all(isinstance(b, (int, float)) for b in fr):
            return bytearray(clamp(b) for b in fr)
        if len(fr) == 512 and all(isinstance(p, (list, tuple)) and len(p) == 3 for p in fr):
            return bytearray(clamp(c) for p in fr for c in p)
        return _parse_voxel_list(fr)
    raise ValueError(f"unrecognised frame of type {type(fr).__name__}")


def _parse_opcode(v):
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("paint", "custom"):
            return OPCODE_PAINT
        if s == "mood":
            return OPCODE_MOOD
        return int(s, 0)
    return int(v)


def animation_from_json(obj, fallback_name="Imported"):
    if isinstance(obj, list):
        obj = {"frames": obj}
    if "frames" not in obj:
        raise ValueError("JSON has no 'frames' list")
    frames = [_parse_frame(fr) for fr in obj["frames"]]
    if not frames:
        raise ValueError("'frames' is empty")
    return Animation(
        name=obj.get("name", fallback_name),
        frames=frames,
        interval_ms=obj.get("interval_ms", obj.get("intervalMs", 100)),
        intensity=obj.get("intensity", 255),
        opcode=_parse_opcode(obj.get("opcode", OPCODE_PAINT)),
    )


def animation_to_json(anim):
    return {
        "format": "tittle-animation",
        "version": 1,
        "name": anim.name,
        "interval_ms": anim.interval_ms,
        "intensity": anim.intensity,
        "opcode": f"0x{anim.opcode:02X}",
        "voxel_index": "z*64 + y*8 + x",
        "encoding": "hex",
        "frames": [bytes(f).hex() for f in anim.frames],
    }


def load_json_animation(path):
    with open(path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    return animation_from_json(obj, os.path.splitext(os.path.basename(path))[0])


def load_bin_animation(path):
    """Read a raw wire dump: 18-byte 0x90/0x50 header followed by 0x21/0x22/0x23 chunks."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 18 or data[0] not in (OPCODE_PAINT, OPCODE_MOOD):
        raise ValueError("file does not start with a 0x90 or 0x50 animation header")
    opcode = data[0]
    num_frames = (data[1] << 8) | data[2]
    interval_ms = data[3] * (8 if opcode == OPCODE_PAINT else 10)
    intensity = data[9]
    frames = [bytearray(FRAME_BYTES) for _ in range(num_frames)]
    offsets = {0x21: (0, 515), 0x22: (510, 515), 0x23: (1020, 521)}
    pos = 18
    while pos < len(data):
        kind = data[pos]
        if kind not in offsets:
            raise ValueError(f"unexpected byte 0x{kind:02X} at offset {pos}")
        start, length = offsets[kind]
        fi = (data[pos + 1] << 8) | data[pos + 2]
        if fi >= num_frames:
            raise ValueError(f"chunk for frame {fi} but header says {num_frames} frames")
        payload = data[pos + 3:pos + length - 2]
        frames[fi][start:start + len(payload)] = payload
        pos += length
    name = os.path.splitext(os.path.basename(path))[0]
    return Animation(name, [from_device(f) for f in frames], interval_ms, intensity, opcode)


def save_json_animation(anim, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(animation_to_json(anim), f, indent=1)


def _magicavoxel_default_palette():
    """MagicaVoxel's built-in palette (index 0 unused), used when a .vox has no RGBA chunk."""
    pal = [(0, 0, 0)]
    steps = [0xFF, 0xCC, 0x99, 0x66, 0x33, 0x00]
    for r in steps:
        for g in steps:
            for b in steps:
                if (r, g, b) != (0, 0, 0):
                    pal.append((r, g, b))
    ramp = [0xEE, 0xDD, 0xBB, 0xAA, 0x88, 0x77, 0x55, 0x44, 0x22, 0x11]
    pal += [(v, 0, 0) for v in ramp] + [(0, v, 0) for v in ramp] + \
           [(0, 0, v) for v in ramp] + [(v, v, v) for v in ramp]
    return pal


def load_vox_animation(path, interval_ms=150):
    """Read a MagicaVoxel .vox file. Returns (Animation, warnings list)."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"VOX ":
        raise ValueError("not a MagicaVoxel .vox file")

    models, sizes, palette = [], [], None
    pos = 8
    # MAIN chunk header, then its children are a flat list of chunks
    if data[pos:pos + 4] != b"MAIN":
        raise ValueError("missing MAIN chunk")
    _, main_content, main_children = data[pos:pos + 4], *struct.unpack_from("<II", data, pos + 4)
    pos += 12 + main_content
    end = min(len(data), pos + main_children)
    while pos + 12 <= end:
        cid = data[pos:pos + 4]
        csize, chsize = struct.unpack_from("<II", data, pos + 4)
        body = data[pos + 12:pos + 12 + csize]
        if cid == b"SIZE":
            sizes.append(struct.unpack_from("<III", body, 0))
        elif cid == b"XYZI":
            (n,) = struct.unpack_from("<I", body, 0)
            models.append([tuple(body[4 + i * 4:8 + i * 4]) for i in range(n)])
        elif cid == b"RGBA":
            palette = [(0, 0, 0)] + [tuple(body[i * 4:i * 4 + 3]) for i in range(255)]
        pos += 12 + csize + chsize

    if not models:
        raise ValueError("no voxel models in file")
    if palette is None:
        palette = _magicavoxel_default_palette()

    warnings = []
    for i, size in enumerate(sizes):
        if any(s > 8 for s in size):
            warnings.append(f"model {i} is {size[0]}x{size[1]}x{size[2]}; voxels outside 8x8x8 were cropped")
    frames, dropped = [], 0
    for voxels in models:
        buf = bytearray(FRAME_BYTES)
        for x, y, z, ci in voxels:
            if x < 8 and y < 8 and z < 8:
                j = voxel_index(x, y, z) * 3
                buf[j:j + 3] = bytes(palette[ci])
            else:
                dropped += 1
        frames.append(buf)
    if dropped:
        warnings.append(f"{dropped} voxel(s) outside the cube were dropped")
    name = os.path.splitext(os.path.basename(path))[0]
    return Animation(name, frames, interval_ms=interval_ms), warnings


def load_app_database(path):
    """Read animations cached by the official Android app (tittle.db, an ORMLite SQLite file).
    Returns (animations, notes). Mood group = 1 (header 0x50), other groups are emoji (0x20)."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]

        def cols(t):
            return {r[1].lower(): r[1] for r in con.execute(f'PRAGMA table_info("{t}")')}
        anim_t = next((t for t in tables if {"time_interval", "version", "group_id"} <= set(cols(t))), None)
        frame_t = next((t for t in tables if {"frame_order", "data"} <= set(cols(t))), None)
        if not anim_t:
            con.close()
            return generic_sqlite_animations(path)
        fc = cols(frame_t) if frame_t else {}
        fk = fc.get("animation_id") or fc.get("animationid")
        animations, notes = [], []
        for row in con.execute(f'SELECT * FROM "{anim_t}" ORDER BY group_id, id'):
            keys = {k.lower(): k for k in row.keys()}
            get = lambda k, d=None: row[keys[k]] if k in keys and row[keys[k]] is not None else d
            aid, name, group = get("id", 0), get("name") or f"Animation {get('id')}", get("group_id", 0)
            opcode = OPCODE_MOOD if group == 1 else 0x20
            frames, blob = [], get("binarybyte")
            if frame_t and fk:
                for fr in con.execute(f'SELECT data FROM "{frame_t}" WHERE "{fk}" = ? ORDER BY frame_order', (aid,)):
                    buf = bytearray(FRAME_BYTES)
                    try:
                        for idx, rgb in json.loads(fr[0] or "{}").items():
                            i = int(idx)
                            if 0 <= i < 512:
                                buf[i * 3:i * 3 + 3] = bytes(clamp(c) for c in rgb[:3])
                    except Exception:
                        continue
                    frames.append(buf)
            if not frames and blob:
                frames = frames_from_blob(blob) or []
                if not frames:
                    notes.append(f"'{name}': binary data kept as-is (no preview)")
            frames = [from_device(f) for f in frames]
            if not frames and not blob:
                notes.append(f"'{name}': no frame data cached, skipped")
                continue
            ti = get("time_interval", 50)
            animations.append(Animation(
                f"{'Mood' if group == 1 else 'Emoji'} · {name}", frames,
                interval_ms=ti * 10 if opcode != OPCODE_PAINT else ti, intensity=255, opcode=opcode,
                anim_id=aid, version=get("version", 1), raw_interval=ti & 0xFF,
                raw_binary=bytes(blob) if (blob and not get("data_type") == 0) else None))
        return animations, notes
    finally:
        con.close()



# ─── Recovering the originals from other places (iPhone backups, unknown databases) ───
def _frames_from_value(value, depth=0):
    """Best-effort: turn one stored value (JSON text, binary blob, plist) into device-order frames."""
    if value is None or depth > 4:
        return []
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        if data.startswith(b"bplist") or data.startswith(b"<?xml"):
            try:
                import plistlib
                return _frames_from_value(plistlib.loads(data), depth + 1)
            except Exception:
                return []
        if b"\xff\xff\xff\xff" in data:
            fr = frames_from_blob(data)
            if fr:
                return fr
        if len(data) >= FRAME_BYTES and len(data) % FRAME_BYTES == 0:
            return [bytearray(data[i:i + FRAME_BYTES]) for i in range(0, len(data), FRAME_BYTES)]
        try:
            fr = rle_decompress(data)
            if len(fr) == FRAME_BYTES:
                return [bytearray(fr)]
        except Exception:
            pass
        try:
            value = data.decode("utf-8")
        except Exception:
            return []
    if isinstance(value, str):
        t = value.strip()
        if not t or t[0] not in "{[":
            return []
        try:
            value = json.loads(t)
        except Exception:
            return []
    if isinstance(value, dict):
        keys = list(value.keys())
        if keys and all(str(k).isdigit() for k in keys):           # {"idx": [r, g, b, ...]}
            buf = bytearray(FRAME_BYTES)
            for k, rgb in value.items():
                i = int(k)
                if 0 <= i < 512 and isinstance(rgb, (list, tuple)) and len(rgb) >= 3:
                    buf[i * 3:i * 3 + 3] = bytes(clamp(c) for c in rgb[:3])
            return [buf]
        out = []
        for v in value.values():
            out += _frames_from_value(v, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for v in value:
            out += _frames_from_value(v, depth + 1)
        return out
    return []


def generic_sqlite_animations(path):
    """Scan any SQLite database (e.g. the iPhone app's Core Data store) for animation frames."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    animations, notes = [], []
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for t in tables:
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
            low = {c.lower(): c for c in cols}
            name_c = next((low[c] for c in low if "name" in c or "title" in c), None)
            int_c = next((low[c] for c in low if "interval" in c), None)
            fk_c = next((low[c] for c in low if "anim" in c and not c.endswith("data")), None)
            ord_c = next((low[c] for c in low if "order" in c or c in ("zindex", "index", "position")), None)
            groups = {}
            try:
                rows = list(con.execute(f'SELECT * FROM "{t}"'))
            except Exception:
                continue
            for n, row in enumerate(rows):
                frames = []
                for c in cols:
                    if c in (name_c, int_c, fk_c, ord_c):
                        continue
                    frames += _frames_from_value(row[c])
                if not frames:
                    continue
                if len(frames) == 1 and fk_c and row[fk_c] is not None:       # one frame per row
                    key = (t, row[fk_c])
                    groups.setdefault(key, {"frames": [], "name": None, "interval": None})
                    groups[key]["frames"].append(((row[ord_c] if ord_c else n) or 0, frames[0]))
                    continue
                label = (row[name_c] if name_c else None) or f"{t} #{n + 1}"
                itv = row[int_c] if int_c and isinstance(row[int_c], (int, float)) else 100
                animations.append(Animation(str(label), [from_device(f) for f in frames], int(itv) or 100))
            for (tt, fk), g in groups.items():
                ordered = [f for _, f in sorted(g["frames"], key=lambda p: p[0])]
                animations.append(Animation(f"{tt} · animation {fk}", [from_device(f) for f in ordered], 100))
        if not animations:
            notes.append(f"No animation frames found in {os.path.basename(path)} (tables: {', '.join(tables)[:200]})")
        return animations, notes
    finally:
        con.close()


def animations_from_files(files):
    """Try every file (SQLite, JSON, plist, raw) for animations. files = [(label, path)]."""
    animations, notes = [], []
    for label, p in files:
        try:
            with open(p, "rb") as f:
                head = f.read(16)
            if head.startswith(b"SQLite format 3"):
                a, n = load_app_database(p)
            else:
                with open(p, "rb") as f:
                    fr = _frames_from_value(f.read())
                a, n = ([Animation(os.path.basename(label), [from_device(x) for x in fr], 100)] if fr else []), []
            for anim in a:
                anim.name = f"{anim.name}"[:60]
            animations += a
            notes += n
            if a:
                notes.append(f"{label}: {len(a)} animation(s)")
        except Exception as e:
            notes.append(f"{label}: skipped ({e})")
    return animations, notes


# ==============================================================================
# DESKTOP UI
# ==============================================================================
class TittleDesktopApp:
    def __init__(self, root, initial_ip=None):
        self.root = root
        self.root.title(f"Tittle Light Control Center {APP_VERSION}")
        try:
            self.ui_scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)   # 1.0 at 100 %, 1.5 at 150 % ...
        except Exception:
            self.ui_scale = 1.0
        sc = self.ui_scale
        self.root.geometry(f"{int(1060 * sc)}x{int(800 * sc)}")
        self.root.minsize(int(980 * sc), int(720 * sc))
        self.root.configure(bg="#0f172a")

        self.style = ttk.Style()
        self.style.theme_use("clam")
        self.style.configure(".", background="#0f172a", foreground="#e2e8f0", font=("Segoe UI", 9))
        self.style.configure("TNotebook", background="#0f172a", borderwidth=0)
        self.style.configure("TNotebook.Tab", background="#1e293b", foreground="#94a3b8", padding=[10, 6], font=("Segoe UI", 9, "bold"))
        self.style.map("TNotebook.Tab", background=[("selected", "#3b82f6")], foreground=[("selected", "#ffffff")])

        self.cur_r = 255
        self.cur_g = 180
        self.cur_b = 80
        self.cur_intensity = 220
        self.bright_var = tk.IntVar(value=self.cur_intensity)    # shared by the header, Lamp tab and tray

        # Worker threads must not touch Tk widgets, so they post events to this
        # queue and the Tk main loop drains it every 50 ms.
        self.events = queue.Queue()
        self.client = TittleClient(
            log_callback=self.append_log,
            status_callback=self.update_status
        )
        self.initial_ip = initial_ip
        cfg = read_config(self.client.config_file)
        if isinstance(cfg.get("orientation_r7"), dict):     # r6 settings are ignored: r7 changed the default
            ORIENTATION.update({k: v for k, v in cfg["orientation_r7"].items() if k in ORIENTATION})
        self.smooth_var = tk.StringVar(value=cfg.get("smoothness", "smooth"))
        self.speed_var = tk.DoubleVar(value=float(cfg.get("speed", 1.0)))

        self.client.on_user_action = lambda method, args: self.events.put(("call", lambda: self._other_action(method, args)))
        self.current = None          # Animation in the preview, used by Export / Play on cube
        self.preview_frame = 0
        self.preview_playing = True
        self.preview_job = None

        self.preset_registry = []        # (title, generator) for every preset card, used by the tray menu
        self._setup_tray_var()
        self._build_header()
        self._build_tabs()
        self._setup_tray()
        self._build_footer()
        if initial_ip:
            self.ip_entry.delete(0, tk.END)
            self.ip_entry.insert(0, initial_ip)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(50, self._drain_events)
        self._cube_seen = None
        self.root.after(60000, self._watch_cube)
        _n, _itv = motion_plan(All3DGenerators.aurora, self.smooth_var.get(), self.speed_var.get())
        self.set_current(Animation("Aurora", render_frames(All3DGenerators.aurora, _n, _itv), _itv, self.cur_intensity))
        self.client.log(f"Tittle Light Control Center {APP_VERSION} started (upload format: {self.client.upload_format})")
        threading.Thread(target=self._auto_start_sniff, daemon=True).start()

    def _auto_start_sniff(self):
        time.sleep(0.3)
        if self.client.sniff_and_connect(forced_ip=self.initial_ip):
            self.events.put(("ip", self.client.ip))

    def _drain_events(self):
        try:
            while True:
                ev = self.events.get_nowait()
                if ev[0] == "log":
                    self._insert_log(ev[1])
                elif ev[0] == "status":
                    self.status_lbl.configure(text=ev[1], fg=ev[2])
                elif ev[0] == "call":
                    ev[1]()
                elif ev[0] == "ip" and ev[1]:
                    self.ip_entry.delete(0, tk.END)
                    self.ip_entry.insert(0, ev[1])
        except queue.Empty:
            pass
        self.root.after(50, self._drain_events)

    def on_close(self):
        """The window's X hides to the system tray when a tray icon is available; Quit exits."""
        if getattr(self, "tray", None) and self.tray_var.get():
            self.root.withdraw()
            if not getattr(self, "_tray_hint_shown", False):
                self._tray_hint_shown = True
                try:
                    self.tray.notify("Still running in the system tray. Right-click the icon and choose Quit to exit.",
                                     "Tittle Light")
                except Exception:
                    pass
            return
        self.quit_app()

    def quit_app(self):
        self.client.stop_flag.set()
        if getattr(self, "tray", None):
            try:
                self.tray.stop()
            except Exception:
                pass
        self.root.destroy()

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except Exception:
            pass

    def _setup_tray_var(self):
        self.tray = None
        self.tray_var = tk.BooleanVar(value=bool(read_config(self.client.config_file).get("close_to_tray", True)))

    def turn_on(self):
        """Tray 'On': bring back whatever was showing before Off, or the lamp colour."""
        last = self.client.last_on_action
        if last:
            method, args, kwargs = last
            self._bg(lambda: getattr(self.client, method)(*args, **kwargs))
        else:
            self._bg(self.client.set_solid_color, self.cur_r, self.cur_g, self.cur_b, self.cur_intensity)

    def set_brightness(self, value):
        """Tray brightness: move the sliders and re-send what's playing."""
        self.apply_brightness(value)
        try:
            self.root.update_idletasks()
        except Exception:
            pass

    def _watch_cube(self):
        """Once a minute: ask the cube what it's showing, so a schedule turning it on/off shows up here."""
        def run():
            if self.client.active_worker and self.client.active_worker.is_alive():
                return
            info = self.client.peek_mode()
            if info is False:
                return
            self.events.put(("call", lambda: self._cube_report(info)))
        if self.client.ip:
            self._bg(run)
        self.root.after(60000, self._watch_cube)

    def _cube_report(self, info):
        cl = self.client
        if info is None:
            if self._cube_seen is not False:
                self.update_status("Cube isn't answering (scheduled off, or unplugged?)", "#f59e0b")
            self._cube_seen = False
            return
        key = (info["mode"], info["anim_id"])
        if self._cube_seen is False or (key != self._cube_seen and info["mode"] in (0x50, 0x20, 0x90)
                                         and info["anim_id"] != cl.last_anim_id):
            if info["mode"] in (0x50, 0x20, 0x90) and info["anim_id"] != cl.last_anim_id:
                names = {p["id"]: p["name"] for p in self._saved_presets()}
                what = names.get(info["anim_id"], f"ID {info['anim_id']}")
                self.update_status(f"Cube is playing '{what}' (started by the cube, e.g. its schedule)", "#10b981")
            else:
                self.update_status(f"Cube is back: {info['mode_name']} mode", "#10b981")
        self._cube_seen = key

    def _refresh_tray(self):
        if getattr(self, "tray", None):
            try:
                self.tray.update_menu()
            except Exception:
                pass

    def _setup_tray(self):
        if sys.platform != "win32":
            return
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            return
        img = None
        for base in (getattr(sys, "_MEIPASS", ""), os.path.dirname(os.path.abspath(__file__))):
            p = os.path.join(base, "icon.png")
            if base and os.path.exists(p):
                try:
                    img = Image.open(p)
                    break
                except Exception:
                    pass
        if img is None:
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            ImageDraw.Draw(img).rectangle((8, 8, 56, 56), fill=(56, 189, 248, 255))
        post = lambda fn: (lambda icon=None, item=None: self.events.put(("call", fn)))
        Item, Menu = pystray.MenuItem, pystray.Menu

        def preset_items(group):
            return [Item(title, post(lambda t=title, g=gen: self.play_generator(t, g)))
                    for grp, title, gen in self.preset_registry if grp == group]

        def bright_action(v):          # pystray actions take exactly (icon, item)
            def act(icon=None, item=None):
                self.cur_intensity = v       # so the tick moves straight away, before the window catches up
                self.events.put(("call", lambda: self.set_brightness(v)))
            return act

        def saved_items():
            items = [Item(p["name"], post(lambda p=p: self.client.play_stored(
                p["id"], p["version"], p.get("opcode", OPCODE_MOOD), self.cur_intensity, name=p["name"])))
                for p in self._saved_presets()]
            return items or [Item("(none saved yet)", None, enabled=False)]

        menu = Menu(
            Item("Show Tittle Light", post(self.show_window), default=True),
            Menu.SEPARATOR,
            Item("On", post(self.turn_on)),
            Item("Off", post(lambda: self._bg(self.client.turn_off))),
            Item("Brightness", Menu(*[
                Item(f"{pct}%", bright_action(val), radio=True,
                     checked=lambda item, v=val: abs(self.cur_intensity - v) <= 12)
                for pct, val in ((100, 255), (75, 191), (50, 128), (25, 64), (10, 26))])),
            Menu.SEPARATOR,
            Item("Look-alike moods", Menu(lambda: preset_items("look"))),
            Item("3D presets", Menu(lambda: preset_items("3d"))),
            Item("⭐ Saved presets", Menu(saved_items)),
            Menu.SEPARATOR,
            Item("Quit", post(self.quit_app)))
        try:
            self.tray = pystray.Icon("TittleLight", img, "Tittle Light", menu)
            self.tray.run_detached()
        except Exception:
            self.tray = None

    def _motion_controls(self, parent):
        """Smoothness + speed row shared by the preset tabs (saved in the config file)."""
        row = tk.Frame(parent, bg="#0f172a")
        row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(row, text="Smoothness:", font=("Segoe UI", 9, "bold"), fg="#e2e8f0", bg="#0f172a").pack(side=tk.LEFT)
        for key, label in (("light", "Light (quick upload)"), ("smooth", "Smooth"), ("ultra", "Ultra")):
            tk.Radiobutton(row, text=label, value=key, variable=self.smooth_var, command=self._save_motion,
                           font=("Segoe UI", 9), fg="#cbd5e1", bg="#0f172a", selectcolor="#1e293b",
                           activebackground="#0f172a").pack(side=tk.LEFT, padx=2)
        tk.Label(row, text="   Speed:", font=("Segoe UI", 9, "bold"), fg="#e2e8f0", bg="#0f172a").pack(side=tk.LEFT)
        tk.Scale(row, from_=0.5, to=2.0, resolution=0.25, orient=tk.HORIZONTAL, variable=self.speed_var,
                 length=120, showvalue=True, command=lambda _v: self._save_motion(), bg="#0f172a", fg="#cbd5e1",
                 highlightthickness=0, troughcolor="#1e293b").pack(side=tk.LEFT)
        tk.Label(row, text="x", font=("Segoe UI", 9), fg="#94a3b8", bg="#0f172a").pack(side=tk.LEFT)

    def _save_motion(self):
        try:
            update_config(self.client.config_file, smoothness=self.smooth_var.get(), speed=float(self.speed_var.get()))
        except Exception:
            pass

    def play_generator(self, name, gen_func, interval_ms=None, num_frames=None, opcode=OPCODE_PAINT):
        """Render a preset at the chosen smoothness/speed, show it in the preview and upload it."""
        num_frames, interval_ms = motion_plan(gen_func, self.smooth_var.get(), self.speed_var.get(),
                                              (interval_ms or 80) * (num_frames or 16) / 1000.0)
        anim = Animation(name, render_frames(gen_func, num_frames, interval_ms), interval_ms,
                         self.cur_intensity, opcode)
        self.set_current(anim)
        self.client.upload_frames(name, anim.frames, anim.interval_ms, anim.intensity, None, opcode)

    def _build_header(self):
        top_bar = tk.Frame(self.root, bg="#1e293b", height=70, padx=15, pady=10)
        top_bar.pack(fill=tk.X)

        title_frame = tk.Frame(top_bar, bg="#1e293b")
        title_frame.pack(side=tk.LEFT)

        tk.Label(title_frame, text="✨ Tittle Light Control Center", font=("Segoe UI", 13, "bold"), fg="#ffffff", bg="#1e293b").pack(anchor=tk.W)
        self.status_lbl = tk.Label(title_frame, text="Checking network...", font=("Segoe UI", 9), fg="#94a3b8", bg="#1e293b")
        self.status_lbl.pack(anchor=tk.W)

        conn_frame = tk.Frame(top_bar, bg="#1e293b")
        conn_frame.pack(side=tk.RIGHT)

        tk.Label(conn_frame, text="IP:", font=("Segoe UI", 9, "bold"), fg="#cbd5e1", bg="#1e293b").pack(side=tk.LEFT, padx=(0, 4))
        self.ip_entry = tk.Entry(conn_frame, width=15, font=("Consolas", 10), bg="#0f172a", fg="#38bdf8", insertbackground="white", bd=1)
        if self.client.ip:
            self.ip_entry.insert(0, self.client.ip)
        self.ip_entry.pack(side=tk.LEFT, padx=4)

        btn_sniff = tk.Button(conn_frame, text="🔍 Sniff / Connect", bg="#2563eb", fg="white", activebackground="#1d4ed8",
                              font=("Segoe UI", 9, "bold"), bd=0, padx=10, pady=4, cursor="hand2", command=self.on_click_sniff)
        btn_sniff.pack(side=tk.LEFT, padx=4)

        tk.Label(conn_frame, text="☀", font=("Segoe UI", 11), fg="#fbbf24", bg="#1e293b").pack(side=tk.LEFT, padx=(10, 0))
        self.bright_scale = tk.Scale(conn_frame, from_=10, to=255, orient=tk.HORIZONTAL, length=int(120 * self.ui_scale),
                                     showvalue=False, bg="#1e293b", highlightthickness=0, troughcolor="#0f172a",
                                     variable=self.bright_var)
        self.bright_scale.bind("<ButtonRelease-1>", lambda e: self.apply_brightness(self.bright_scale.get()))
        self.bright_scale.pack(side=tk.LEFT, padx=(2, 8))

        btn_off = tk.Button(conn_frame, text="⭕ Off", bg="#dc2626", fg="white", activebackground="#b91c1c",
                            font=("Segoe UI", 9, "bold"), bd=0, padx=8, pady=4, cursor="hand2", command=self.on_click_off)
        btn_off.pack(side=tk.LEFT, padx=4)

    def _build_tabs(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        # Tab 1: Solid Lamp
        self.tab_lamp = tk.Frame(self.notebook, bg="#0f172a", padx=15, pady=15)
        self.notebook.add(self.tab_lamp, text="💡 Lamp")
        self._init_lamp_tab()

        # Tab 2: Official Presets
        self.tab_presets = tk.Frame(self.notebook, bg="#0f172a", padx=15, pady=15)
        self.notebook.add(self.tab_presets, text="🎨 Look-alikes")
        self._init_official_presets_tab()

        # Tab 3: Novel 3D & Fairies
        self.tab_novel = tk.Frame(self.notebook, bg="#0f172a", padx=15, pady=15)
        self.notebook.add(self.tab_novel, text="✨ 3D Presets")
        self._init_novel_presets_tab()

        # Tab 4: 3D Clock
        self.tab_clock = tk.Frame(self.notebook, bg="#0f172a", padx=15, pady=15)
        self.notebook.add(self.tab_clock, text="🕒 Clock")
        self._init_clock_tab()

        # Tab 5: Music Visualizer
        self.tab_music = tk.Frame(self.notebook, bg="#0f172a", padx=15, pady=15)
        self.notebook.add(self.tab_music, text="🎵 Music")
        self._init_music_tab()

        # Tab 6: Custom animation files + preview
        self.tab_files = tk.Frame(self.notebook, bg="#0f172a", padx=15, pady=15)
        self.notebook.add(self.tab_files, text="📁 Files")
        self._init_files_tab()

        # Tab 7: Picture -> light mode (Tittle Image Studio built in)
        self.tab_pic = tk.Frame(self.notebook, bg="#0f172a", padx=15, pady=15)
        self.notebook.add(self.tab_pic, text="🖼 Preset Generator")
        self._init_picture_tab()

        # Tab 8: Recover the official animations
        self.tab_orig = tk.Frame(self.notebook, bg="#0f172a", padx=15, pady=15)
        self.notebook.add(self.tab_orig, text="♻ Originals")
        self._init_originals_tab()

        # Tab 8: Hardware on/off schedule
        self.tab_sched = tk.Frame(self.notebook, bg="#0f172a", padx=15, pady=15)
        self.notebook.add(self.tab_sched, text="⏰ Schedule")
        self._init_schedule_tab()

        # Settings
        self.tab_settings = tk.Frame(self.notebook, bg="#0f172a", padx=15, pady=15)
        self.notebook.add(self.tab_settings, text="⚙ Settings")
        self._init_settings_tab()

        # Tab 9: Raw Debug Log
        self.tab_debug = tk.Frame(self.notebook, bg="#0f172a", padx=15, pady=15)
        self.notebook.add(self.tab_debug, text="🔍 Debug")
        self._init_debug_tab()

    def _build_footer(self):
        footer = tk.Frame(self.root, bg="#090d16", height=24, padx=12)
        footer.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Label(footer, text="Tittle Light 8x8x8 Desktop Controller • Zero Cloud • Local Wi-Fi TCP:9999 / UDP:10000",
                 font=("Segoe UI", 8), fg="#64748b", bg="#090d16").pack(side=tk.LEFT)
        if getattr(self, "tray", None):
            tk.Checkbutton(footer, text="Close to tray", variable=self.tray_var, font=("Segoe UI", 8), fg="#64748b",
                           bg="#090d16", selectcolor="#090d16", activebackground="#090d16",
                           command=lambda: update_config(self.client.config_file, close_to_tray=bool(self.tray_var.get()))
                           ).pack(side=tk.LEFT, padx=12)
            tk.Button(footer, text="Quit", font=("Segoe UI", 8), bg="#334155", fg="white", bd=0, padx=8,
                      command=self.quit_app).pack(side=tk.LEFT)
        credit = tk.Label(footer, text="🍕 made by Claude and Chad", font=("Segoe UI", 8, "italic"),
                          fg="#64748b", bg="#090d16", cursor="hand2")
        credit.pack(side=tk.RIGHT)
        credit.bind("<Enter>", lambda e: credit.configure(fg="#fbbf24"))
        credit.bind("<Leave>", lambda e: credit.configure(fg="#64748b"))
        credit.bind("<Button-1>", lambda e: self.pizza_time())

    # Easter egg: a spinning pepperoni slice for 5 seconds, then back to what was playing.
    PIZZA_SECONDS = 5

    def pizza_time(self):
        if getattr(self, "_pizza_busy", False):
            return
        if not self.client.ip:
            self.client.log("🍕 Pizza time needs a cube! Connect first.")
            return
        self._pizza_busy = True
        pizza_n, pizza_ms = motion_plan(All3DGenerators.pizza, self.smooth_var.get())
        frames = render_frames(All3DGenerators.pizza, pizza_n, pizza_ms)

        def start():
            before = self.client.last_action
            mode = None if before else self.client.query_mode()   # nothing played from the app yet: ask the cube
            self.client.log("🍕 Pizza time!")
            self.client.upload_frames("🍕 Pizza", frames, pizza_ms, self.cur_intensity, remember=False,
                                      on_done=lambda: self.events.put(("call", lambda: self.root.after(
                                          self.PIZZA_SECONDS * 1000, lambda: self._bg(restore, before, mode)))))

        def restore(before, mode):
            try:
                if before:
                    self.client.last_action = before
                    self.client.restore_last()
                elif mode and mode["mode"] in (0x50, 0x20, 0x90) and mode["anim_id"]:
                    self.client.play_stored(mode["anim_id"], mode["raw"][10] or 1,
                                            mode["mode"] if mode["mode"] != 0x90 else 0x20, mode["intensity"])
                else:
                    self.client.turn_off()
                self.client.log("🍕 Back to what was playing before.")
            finally:
                self._pizza_busy = False

        self._bg(start)

    # --------------------------------------------------------------------------
    # TAB 1: SOLID LAMP
    # --------------------------------------------------------------------------
    def _init_lamp_tab(self):
        lbl = tk.Label(self.tab_lamp, text="Select Color and Brightness for the Cube Matrix", font=("Segoe UI", 11, "bold"), fg="#f8fafc", bg="#0f172a")
        lbl.pack(anchor=tk.W, pady=(0, 10))

        content = tk.Frame(self.tab_lamp, bg="#0f172a")
        content.pack(fill=tk.BOTH, expand=True)

        left = tk.Frame(content, bg="#1e293b", padx=15, pady=15, bd=1, relief=tk.FLAT)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        self.color_box = tk.Label(left, text="", bg=f"#{self.cur_r:02x}{self.cur_g:02x}{self.cur_b:02x}", height=4, relief=tk.GROOVE)
        self.color_box.pack(fill=tk.X, pady=(0, 12))

        btn_pick = tk.Button(left, text="🎨 Open Color Picker", font=("Segoe UI", 10, "bold"), bg="#3b82f6", fg="white",
                             bd=0, pady=6, cursor="hand2", command=self.on_pick_color)
        btn_pick.pack(fill=tk.X, pady=(0, 15))

        tk.Label(left, text="Quick Color Swatches:", font=("Segoe UI", 9, "bold"), fg="#cbd5e1", bg="#1e293b").pack(anchor=tk.W, pady=(0, 6))
        palette_frame = tk.Frame(left, bg="#1e293b")
        palette_frame.pack(fill=tk.X, pady=(0, 15))

        swatches = [
            ("Warm White", 255, 190, 120),
            ("Cool White", 220, 240, 255),
            ("Amber Glow", 255, 140, 20),
            ("Fire Orange", 255, 60, 0),
            ("Rose Pink", 255, 80, 180),
            ("Cyber Cyan", 0, 230, 255),
            ("Neon Green", 40, 255, 90),
            ("Deep Violet", 160, 40, 255)
        ]
        for idx, (name, r, g, b) in enumerate(swatches):
            btn = tk.Button(palette_frame, text=name, bg=f"#{r:02x}{g:02x}{b:02x}",
                            fg="#000000" if (r+g+b) > 400 else "#ffffff",
                            font=("Segoe UI", 8, "bold"), bd=0, padx=6, pady=4, cursor="hand2",
                            command=lambda cr=r, cg=g, cb=b: self.set_swatch(cr, cg, cb))
            btn.grid(row=idx // 2, column=idx % 2, sticky="nsew", padx=3, pady=3)
        palette_frame.columnconfigure(0, weight=1)
        palette_frame.columnconfigure(1, weight=1)

        right = tk.Frame(content, bg="#1e293b", padx=15, pady=15)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        tk.Label(right, text="Brightness / Intensity (0 - 255):", font=("Segoe UI", 9, "bold"), fg="#cbd5e1", bg="#1e293b").pack(anchor=tk.W)
        self.slider_intensity = tk.Scale(right, from_=0, to=255, orient=tk.HORIZONTAL, bg="#1e293b", fg="#e2e8f0",
                                         highlightthickness=0, command=self.on_slider_change, variable=self.bright_var)
        self.slider_intensity.pack(fill=tk.X, pady=(0, 15))

        tk.Label(right, text="Red (0 - 255):", font=("Segoe UI", 9, "bold"), fg="#f87171", bg="#1e293b").pack(anchor=tk.W)
        self.slider_r = tk.Scale(right, from_=0, to=255, orient=tk.HORIZONTAL, bg="#1e293b", fg="#f87171", highlightthickness=0, command=self.on_slider_change)
        self.slider_r.set(self.cur_r)
        self.slider_r.pack(fill=tk.X, pady=(0, 10))

        tk.Label(right, text="Green (0 - 255):", font=("Segoe UI", 9, "bold"), fg="#4ade80", bg="#1e293b").pack(anchor=tk.W)
        self.slider_g = tk.Scale(right, from_=0, to=255, orient=tk.HORIZONTAL, bg="#1e293b", fg="#4ade80", highlightthickness=0, command=self.on_slider_change)
        self.slider_g.set(self.cur_g)
        self.slider_g.pack(fill=tk.X, pady=(0, 10))

        tk.Label(right, text="Blue (0 - 255):", font=("Segoe UI", 9, "bold"), fg="#60a5fa", bg="#1e293b").pack(anchor=tk.W)
        self.slider_b = tk.Scale(right, from_=0, to=255, orient=tk.HORIZONTAL, bg="#1e293b", fg="#60a5fa", highlightthickness=0, command=self.on_slider_change)
        self.slider_b.set(self.cur_b)
        self.slider_b.pack(fill=tk.X, pady=(0, 15))

        btn_apply = tk.Button(right, text="⚡ Send Color to Cube", bg="#10b981", fg="white", font=("Segoe UI", 10, "bold"),
                              bd=0, pady=8, cursor="hand2", command=self.send_cur_lamp)
        btn_apply.pack(fill=tk.X)

    def on_pick_color(self):
        color = colorchooser.askcolor(color=f"#{self.cur_r:02x}{self.cur_g:02x}{self.cur_b:02x}", title="Choose Lamp Color")
        if color and color[0]:
            r, g, b = [int(v) for v in color[0]]
            self.set_swatch(r, g, b)

    def set_swatch(self, r, g, b):
        self.cur_r, self.cur_g, self.cur_b = r, g, b
        self.slider_r.set(r)
        self.slider_g.set(g)
        self.slider_b.set(b)
        self.color_box.configure(bg=f"#{r:02x}{g:02x}{b:02x}")
        self.send_cur_lamp()

    def on_slider_change(self, val):
        self.cur_r = self.slider_r.get()
        self.cur_g = self.slider_g.get()
        self.cur_b = self.slider_b.get()
        self.cur_intensity = int(self.bright_var.get())
        self._refresh_tray()
        self.color_box.configure(bg=f"#{self.cur_r:02x}{self.cur_g:02x}{self.cur_b:02x}")

    # Where each remembered action keeps its intensity argument
    INTENSITY_ARG = {"set_solid_color": 3, "upload_frames": 3, "play_stored": 3, "sync_clock": 1, "set_music_mode": 2}

    def apply_brightness(self, value):
        """Brightness works for everything: re-send whatever is showing with the new intensity.
        Animations the cube already has only need their header again, so this is quick. If a schedule
        started what's showing, the cube is asked what it is and that gets re-sent instead."""
        value = int(value)
        self.cur_intensity = value
        if int(self.bright_var.get()) != value:
            self.bright_var.set(value)
        self._refresh_tray()
        self._schedule_morning_follow()
        if self.client.ip:
            self._bg(self._resend_bright, value)

    def _resend_bright(self, value):
        if value != self.cur_intensity:
            return                                   # a newer change is already on its way
        cl = self.client
        info = cl.peek_mode()
        last = cl.last_action
        if info is None:
            cl.log(f"[i] Brightness {value}: the cube isn't answering right now (scheduled off?). "
                   "It will be used for the next thing you play.")
            return
        if info and info["mode"] in (0x50, 0x20, 0x90) and info["anim_id"] and info["anim_id"] != cl.last_anim_id:
            cl.log(f"[*] Brightness {value}: the cube is playing ID {info['anim_id']} v{info['version']}, which this "
                   f"app didn't start (a schedule did?), so re-sending that")
            names = {p["id"]: p["name"] for p in self._saved_presets()}
            cl.play_stored(info["anim_id"], info["version"], info["mode"], value, name=names.get(info["anim_id"]))
            return
        if info and info["mode"] == 0x10 and (not last or last[0] != "set_solid_color"):
            rgb = read_config(cl.config_file).get("schedule", {}).get("rgb")
            if rgb and any(rgb):
                cl.log(f"[*] Brightness {value}: the schedule's lamp colour is showing, re-sending it")
                cl.set_solid_color(*rgb, value)
            return
        if not last:
            return
        method, args, kwargs = last
        pos = self.INTENSITY_ARG.get(method)
        if pos is None or len(args) <= pos:
            return
        if method == "set_solid_color" and not any(args[:3]):
            return                                   # the cube is off; don't switch it on
        args = list(args)
        args[pos] = value
        cl.log(f"[*] Brightness {value}: re-sending {method}")
        getattr(cl, method)(*args, **kwargs)

    def _schedule_morning_follow(self):
        """The saved morning-on timer keeps its own brightness on the cube; update it to match (after a pause)."""
        job = getattr(self, "_morning_job", None)
        if job:
            self.root.after_cancel(job)
        self._morning_job = self.root.after(2500, self._resave_morning)

    def _resave_morning(self):
        self._morning_job = None
        sc = read_config(self.client.config_file).get("schedule", {})
        if not sc.get("morning_enabled") or sc.get("intensity") == self.cur_intensity or not self.client.ip:
            return
        start, end = tuple(sc.get("on", (7, 0))), tuple(sc.get("off", (23, 0)))
        if sc.get("kind") == "anim":
            a = self.current
            if a is None or (a.anim_id or content_anim_id(a.frames)) != sc.get("anim_id"):
                return                               # the Files preview has changed since; leave the timer alone
            self.on_save_morning(True)
            return
        rgb = sc.get("rgb") or [self.cur_r, self.cur_g, self.cur_b]
        pkt = TittleClient.build_lamp_timer(self.MORNING_TIMER_ID, start, end, tuple(rgb), self.cur_intensity, EVERY_DAY, True)
        sc["intensity"] = self.cur_intensity
        update_config(self.client.config_file, schedule=sc)
        self.client.send_timer(pkt, f"morning-on brightness now {round(self.cur_intensity / 2.55)}%")

    def send_cur_lamp(self):
        threading.Thread(target=lambda: self.client.set_solid_color(
            self.cur_r, self.cur_g, self.cur_b, self.cur_intensity
        ), daemon=True).start()

    # --------------------------------------------------------------------------
    # TAB 2: OFFICIAL PRESETS (Uploads full 3D multi-frame loops into cube RAM)
    # --------------------------------------------------------------------------
    def _init_official_presets_tab(self):
        tk.Label(self.tab_presets, text="Look-alike moods (NOT the official animations)",
                 font=("Segoe UI", 11, "bold"), fg="#f8fafc", bg="#0f172a").pack(anchor=tk.W, pady=(0, 4))
        tk.Label(self.tab_presets, text="The real official moods were downloaded from the (now dead) Tittle cloud, so these are "
                                        "look-alikes. To get the real ones back, see the ♻️ Originals tab.",
                 font=("Segoe UI", 9), fg="#38bdf8", bg="#0f172a").pack(anchor=tk.W, pady=(0, 6))
        self._motion_controls(self.tab_presets)

        grid = tk.Frame(self.tab_presets, bg="#0f172a")
        grid.pack(fill=tk.BOTH, expand=True)

        presets = [
            ("🔥 Fireplace", All3DGenerators.fire, 90, "Flickering flames rising from the base"),
            ("🌊 Wave", All3DGenerators.wave, 110, "Rolling oceanic 3D tidal swell moving across cube"),
            ("🌧️ Rain", All3DGenerators.rain, 85, "Gentle blue rain droplets falling with floor splash"),
            ("❄️ Snow", All3DGenerators.snow, 130, "Floating white snowflakes drifting with gentle air current"),
            ("✨ Sparkle", All3DGenerators.sparkle, 100, "Random twinkling celestial points in 3D volume"),
            ("🌌 Fireworks", All3DGenerators.fireworks, 90, "Rocket launch from bottom exploding into multi-color shell"),
            ("🌀 Vortex", All3DGenerators.vortex, 90, "Swirling plasma funnel inside 8x8x8 volume"),
            ("🌅 Sunset", All3DGenerators.sunset, 90, "A warm sun sinking and rising in an orange sky"),
            ("🌟 Constellation", All3DGenerators.constellation, 90, "Twinkling stars joined by glowing lines"),
            ("🔮 Snow Globe", All3DGenerators.snow_globe, 90, "Snow swirling round and settling on the floor"),
            ("🎊 Confetti", All3DGenerators.confetti, 90, "Bright confetti in every colour tumbling down"),
        ]

        self.preset_registry += [("look", n, g) for n, g, _i, _d in presets]
        for i, (name, gen_func, interval, desc) in enumerate(presets):
            card = tk.Frame(grid, bg="#1e293b", padx=10, pady=6, bd=1, relief=tk.FLAT)
            card.grid(row=i // 3, column=i % 3, sticky="nsew", padx=4, pady=3)

            tk.Label(card, text=name, font=("Segoe UI", 10, "bold"), fg="#ffffff", bg="#1e293b").pack(anchor=tk.W)
            tk.Label(card, text=desc, font=("Segoe UI", 8), fg="#94a3b8", bg="#1e293b").pack(anchor=tk.W, pady=(2, 6))

            btn = tk.Button(card, text="Play Preset", bg="#3b82f6", fg="white", font=("Segoe UI", 8, "bold"),
                            bd=0, padx=8, pady=4, cursor="hand2",
                            command=lambda n=name, g=gen_func, itv=interval: self.play_generator(n, g, itv))
            btn.pack(anchor=tk.E)

        for c in range(3):
            grid.columnconfigure(c, weight=1)

    # --------------------------------------------------------------------------
    # TAB 3: NOVEL 3D PRESETS & FAIRIES (User Favorite!)
    # --------------------------------------------------------------------------
    def _init_novel_presets_tab(self):
        tk.Label(self.tab_novel, text="✨ 3D presets (new designs, not the originals)",
                 font=("Segoe UI", 11, "bold"), fg="#f8fafc", bg="#0f172a").pack(anchor=tk.W, pady=(0, 4))
        tk.Label(self.tab_novel, text="Each one is uploaded once and then loops on the cube by itself.",
                 font=("Segoe UI", 9), fg="#38bdf8", bg="#0f172a").pack(anchor=tk.W, pady=(0, 6))
        self._motion_controls(self.tab_novel)

        # Other Novel Presets
        novel_grid = tk.Frame(self.tab_novel, bg="#0f172a")
        novel_grid.pack(fill=tk.BOTH, expand=True)

        generators = [
            ("🌀 4D Tesseract", All3DGenerators.tesseract, 85, "Projection of 4D hypercube turning inside-out", 16),
            ("🧚 Fairies", All3DGenerators.fairies, 80, "Eight glowing dots drifting with short trails", 16),
            ("🪼 Deep Abyss Jellyfish", All3DGenerators.jellyfish, 95, "Bioluminescent dome pulsing upward with tentacles", 16),
            ("🧬 DNA Double Helix", All3DGenerators.dna, 80, "Spinning magenta & cyan strands with golden base pairs", 16),
            ("🌧️ True 3D Matrix Rain", All3DGenerators.matrix_rain, 80, "Phosphor green falling code cascading across all depths", 16),
            ("🌊 Acoustic Ripple", All3DGenerators.acoustic, 90, "Concentric spherical sound shockwaves (safely clamped)", 16),
            # New presets designed with Claude
            ("🆕 🌌 Aurora", All3DGenerators.aurora, 90, "Green-to-violet northern-light curtains rippling overhead", 24),
            ("🆕 🫧 Lava Lamp", All3DGenerators.lava_lamp, 100, "Warm blobs rising, merging and sinking in purple glass", 32),
            ("🆕 🌀 Spiral Galaxy", All3DGenerators.galaxy, 90, "Two-armed galaxy turning around a glowing core", 24),
            ("🆕 ❤️ Heartbeat", All3DGenerators.heartbeat, 60, "A 3D heart that beats twice, then rests", 24),
            ("🆕 🚀 Warp Speed", All3DGenerators.warp_speed, 70, "Starfield streaking towards you", 16),
            ("🆕 🎨 Plasma", All3DGenerators.plasma, 90, "Soft rainbow plasma flowing through the whole cube", 32),
            ("🆕 🏀 Bouncing Balls", All3DGenerators.bouncing_balls, 70, "Three balls bouncing off the floor with trails", 32),
            ("🆕 🌈 Rainbow Spin", All3DGenerators.rainbow_spin, 80, "A rainbow wheel spinning round the cube", 24),
        ]

        self.preset_registry += [("3d", t.replace("🆕 ", ""), g) for t, g, _i, _d, _n in generators]
        for idx, (title, func, itv, desc, nframes) in enumerate(generators):
            card = tk.Frame(novel_grid, bg="#1e293b", padx=10, pady=3)
            card.grid(row=idx // 3, column=idx % 3, sticky="nsew", padx=4, pady=3)

            tk.Label(card, text=title, font=("Segoe UI", 10, "bold"), fg="#ffffff", bg="#1e293b").pack(anchor=tk.W)
            tk.Label(card, text=desc, font=("Segoe UI", 8), fg="#94a3b8", bg="#1e293b").pack(anchor=tk.W, pady=(0, 2))

            btn = tk.Button(card, text="Play 3D Loop", bg="#2563eb", fg="white", font=("Segoe UI", 8, "bold"),
                            bd=0, padx=8, pady=1, cursor="hand2",
                            command=lambda t=title, f=func, i=itv, n=nframes: self.play_generator(t, f, i, n))
            btn.pack(anchor=tk.E)

        for c in range(3):
            novel_grid.columnconfigure(c, weight=1)

    # --------------------------------------------------------------------------
    # TAB 4: 3D CLOCK
    # --------------------------------------------------------------------------
    def _init_clock_tab(self):
        tk.Label(self.tab_clock, text="Clock", font=("Segoe UI", 11, "bold"), fg="#f8fafc", bg="#0f172a").pack(anchor=tk.W)
        self.clock_time_lbl = tk.Label(self.tab_clock, text=datetime.now().strftime("%H:%M:%S"),
                                       font=("Consolas", 24, "bold"), fg="#38bdf8", bg="#0f172a")
        self.clock_time_lbl.pack(anchor=tk.W, pady=(4, 10))
        self._update_clock_preview()

        def btn(parent, text, cmd, bg="#334155"):
            return tk.Button(parent, text=text, bg=bg, fg="white", font=("Segoe UI", 9, "bold"), bd=0, padx=10, pady=5,
                             cursor="hand2", command=cmd)

        live = tk.Frame(self.tab_clock, bg="#1e293b", padx=15, pady=12)
        live.pack(fill=tk.X, pady=(0, 10))
        tk.Label(live, text="A · App-drawn clock (works on any cube)", font=("Segoe UI", 10, "bold"),
                 fg="#38bdf8", bg="#1e293b").pack(anchor=tk.W)
        tk.Label(live, wraplength=900, justify=tk.LEFT, font=("Segoe UI", 9), fg="#cbd5e1", bg="#1e293b",
                 text="Shows the hours (orange) and minutes (blue) in turn on the front of the cube. This uses the "
                      "computer's clock: the app redraws it every minute, so keep the app open. Playing anything else "
                      "stops it.").pack(anchor=tk.W, pady=(2, 6))
        r = tk.Frame(live, bg="#1e293b"); r.pack(anchor=tk.W)
        self.live_btn = btn(r, "▶ Start live clock", self.toggle_live_clock, "#10b981")
        self.live_btn.pack(side=tk.LEFT)
        self.clock_12h = tk.BooleanVar(value=bool(read_config(self.client.config_file).get("clock_12h", True)))
        tk.Checkbutton(r, text="12-hour (purple bar on top = PM)", variable=self.clock_12h, command=self._on_clock_fmt,
                       bg="#1e293b", fg="#e2e8f0", selectcolor="#0f172a", activebackground="#1e293b",
                       font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=12)
        self.live_clock_on, self.live_clock_last = False, None

        orig = tk.Frame(self.tab_clock, bg="#1e293b", padx=15, pady=12)
        self.clock_b_frame = orig
        orig.pack(fill=tk.X)
        tk.Label(orig, text="B · The cube's own clock faces (OpCode 0x34)", font=("Segoe UI", 10, "bold"),
                 fg="#38bdf8", bg="#1e293b").pack(anchor=tk.W)
        tk.Label(orig, wraplength=900, justify=tk.LEFT, font=("Segoe UI", 9), fg="#cbd5e1", bg="#1e293b",
                 text="The official clock faces were downloaded from the Tittle server, and each has an ID and version. "
                      "This only works if the cube still has that face stored. To find it: if the cube shows a clock "
                      "when powered on, open ♻️ Originals and press 'What is it playing now?', which fills in the ID. "
                      "The guessed IDs 1-3 in older versions of this app timed out for this reason.").pack(anchor=tk.W, pady=(2, 6))
        r = tk.Frame(orig, bg="#1e293b"); r.pack(anchor=tk.W)
        self.clock_face_var = tk.StringVar(value="1")
        self.clock_ver_var = tk.StringVar(value="1")
        for lbl, var in (("Face ID", self.clock_face_var), ("Version", self.clock_ver_var)):
            tk.Label(r, text=lbl, font=("Segoe UI", 9), fg="#cbd5e1", bg="#1e293b").pack(side=tk.LEFT)
            tk.Entry(r, textvariable=var, width=8, font=("Consolas", 10), bg="#0f172a", fg="#38bdf8",
                     insertbackground="white").pack(side=tk.LEFT, padx=(4, 12))
        btn(r, "🕒 Sync stored clock face", self.on_sync_clock, "#2563eb").pack(side=tk.LEFT)
        btn(r, "🔍 Find stored faces", self.on_find_faces).pack(side=tk.LEFT, padx=6)
        btn(r, "Stop", lambda: self.client.stop_flag.set(), "#991b1b").pack(side=tk.LEFT)

    def _update_clock_preview(self):
        twelve = getattr(self, "clock_12h", None) is not None and self.clock_12h.get()
        self.clock_time_lbl.configure(text=datetime.now().strftime("%I:%M:%S %p" if twelve else "%H:%M:%S"))
        if getattr(self, "live_clock_on", False):
            now = datetime.now()
            if (now.hour, now.minute) != self.live_clock_last and not getattr(self, "_pizza_busy", False):
                self.live_clock_last = (now.hour, now.minute)
                self.client.upload_frames(f"Clock {now:%H:%M}",
                                          All3DGenerators.time_frames(now.hour, now.minute, self.clock_12h.get()),
                                          1600, self.cur_intensity, opcode=OPCODE_PAINT)
        self.root.after(1000, self._update_clock_preview)

    def _other_action(self, method, args):
        """Anything else shown on the cube (preset, colour, Off, stored mood...) stops the live clock,
        otherwise the clock would take over again at the next minute."""
        is_clock = method == "upload_frames" and args and str(args[0]).startswith("Clock ")
        if getattr(self, "live_clock_on", False) and not is_clock:
            self.toggle_live_clock()
            self.client.log("[i] Live clock stopped because something else was played.")

    def _on_clock_fmt(self):
        update_config(self.client.config_file, clock_12h=bool(self.clock_12h.get()))
        self.live_clock_last = None          # redraw on the next tick

    def toggle_live_clock(self):
        self.live_clock_on = not self.live_clock_on
        self.live_clock_last = None
        self.live_btn.configure(text="■ Stop live clock" if self.live_clock_on else "▶ Start live clock",
                                bg="#991b1b" if self.live_clock_on else "#10b981")

    def on_find_faces(self):
        """Try clock face IDs 1-100 (versions 1-3). A hit means the cube can run its own clock."""
        if self.live_clock_on:
            self.toggle_live_clock()

        def hit(face, ver):
            self.client.stop_flag.set()          # first face found is enough
            def ui():
                self.clock_face_var.set(str(face)); self.clock_ver_var.set(str(ver))
                if self.live_clock_on:
                    self.toggle_live_clock()
            self.events.put(("call", ui))
        self.client.log("[*] Looking for clock faces stored on the cube. If one is found, the cube shows it and "
                        "keeps time by itself (no app needed).")
        self.client.scan_stored(1, 100, [1, 2, 3], 0x34, self.cur_intensity, on_hit=hit)

    def on_sync_clock(self):
        try:
            face, ver = int(self.clock_face_var.get()), int(self.clock_ver_var.get())
        except ValueError:
            messagebox.showerror("Bad ID", "Face ID and version must be numbers.")
            return
        self.client.sync_clock(face_id=face, intensity=self.cur_intensity, version=ver)

    # --------------------------------------------------------------------------
    # TAB 5: MUSIC MODE
    # --------------------------------------------------------------------------
    def _init_music_tab(self):
        tk.Label(self.tab_music, text="Music Visualizer (OpCode 0x41)", font=("Segoe UI", 11, "bold"),
                 fg="#f8fafc", bg="#0f172a").pack(anchor=tk.W, pady=(0, 6))
        box = tk.Frame(self.tab_music, bg="#1e293b", padx=20, pady=16)
        box.pack(fill=tk.X)
        tk.Label(box, wraplength=900, justify=tk.LEFT, font=("Segoe UI", 9), fg="#cbd5e1", bg="#1e293b",
                 text="The cube listens with its own microphone, but the visualizer animation itself came from the "
                      "Tittle server under an ID and version. Like the clock, this only works if the cube still has "
                      "it stored. Put the cube in music mode however you can, then read the ID with ♻️ Originals > "
                      "'What is it playing now?'.").pack(anchor=tk.W, pady=(0, 10))
        tk.Label(box, text="Microphone sensitivity (0 - 100%):", font=("Segoe UI", 9, "bold"), fg="#cbd5e1",
                 bg="#1e293b").pack(anchor=tk.W)
        self.music_sens = tk.Scale(box, from_=0, to=100, orient=tk.HORIZONTAL, bg="#1e293b", fg="#e2e8f0", highlightthickness=0)
        self.music_sens.set(50)
        self.music_sens.pack(fill=tk.X, pady=(0, 10))
        r = tk.Frame(box, bg="#1e293b"); r.pack(anchor=tk.W)
        self.music_style = tk.StringVar(value="1")
        self.music_ver = tk.StringVar(value="1")
        for lbl, var in (("Visualizer ID", self.music_style), ("Version", self.music_ver)):
            tk.Label(r, text=lbl, font=("Segoe UI", 9), fg="#cbd5e1", bg="#1e293b").pack(side=tk.LEFT)
            tk.Entry(r, textvariable=var, width=8, font=("Consolas", 10), bg="#0f172a", fg="#38bdf8",
                     insertbackground="white").pack(side=tk.LEFT, padx=(4, 12))
        tk.Button(r, text="🎵 Start music mode", bg="#8b5cf6", fg="white", font=("Segoe UI", 9, "bold"), bd=0,
                  padx=10, pady=5, cursor="hand2", command=self.on_music).pack(side=tk.LEFT)

    def on_music(self):
        try:
            sid, ver = int(self.music_style.get()), int(self.music_ver.get())
        except ValueError:
            messagebox.showerror("Bad ID", "ID and version must be numbers.")
            return
        self.client.set_music_mode(style_id=sid, sensitivity=self.music_sens.get(), intensity=self.cur_intensity, version=ver)

    # --------------------------------------------------------------------------
    # PICTURE -> LIGHT MODE (uses tittle_image.py, which needs Pillow)
    # --------------------------------------------------------------------------
    @staticmethod
    def _load_image_tool():
        here = os.path.dirname(os.path.abspath(__file__))
        for extra in (here, os.path.join(here, "..", "tittle-image"), os.path.join(here, "..", "image-tool")):
            if os.path.isfile(os.path.join(extra, "tittle_image.py")) and extra not in sys.path:
                sys.path.append(extra)
        try:
            import tittle_image
            return tittle_image, None
        except ImportError as e:
            return None, str(e)

    def _init_picture_tab(self):
        it, err = self._load_image_tool()
        self.image_tool, self.pic_result, self.pic_path = it, None, None
        tk.Label(self.tab_pic, text="Make a new preset", font=("Segoe UI", 11, "bold"),
                 fg="#f8fafc", bg="#0f172a").pack(anchor=tk.W)
        if it is None:
            tk.Label(self.tab_pic, wraplength=900, justify=tk.LEFT, font=("Segoe UI", 10), fg="#fbbf24", bg="#0f172a",
                     text="This tab needs the image tool (tittle_image.py, next to this file) and the Pillow package.\n"
                          "Install Pillow once with:   pip install pillow   then restart the app.\n"
                          f"({err})").pack(anchor=tk.W, pady=10)
            return
        tk.Label(self.tab_pic, font=("Segoe UI", 9), fg="#94a3b8", bg="#0f172a",
                 text="Convert a picture directly, or let any AI (ChatGPT, Gemini, Claude…) design one from your "
                      "idea or picture.").pack(anchor=tk.W, pady=(2, 8))
        body = tk.Frame(self.tab_pic, bg="#0f172a")
        body.pack(fill=tk.BOTH, expand=True)
        left = tk.Frame(body, bg="#0f172a")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        right = tk.Frame(body, bg="#1e293b", padx=10, pady=10)
        right.pack(side=tk.RIGHT, fill=tk.Y)

        def card(title):
            f = tk.Frame(left, bg="#1e293b", padx=12, pady=8)
            f.pack(fill=tk.X, pady=(0, 8))
            tk.Label(f, text=title, font=("Segoe UI", 10, "bold"), fg="#38bdf8", bg="#1e293b").pack(anchor=tk.W)
            return f

        def btn(parent, text, cmd, bg="#334155"):
            return tk.Button(parent, text=text, bg=bg, fg="white", font=("Segoe UI", 9, "bold"), bd=0, padx=10,
                             pady=4, cursor="hand2", command=cmd)
        lab = lambda p, t, **kw: tk.Label(p, text=t, font=("Segoe UI", 9), fg=kw.pop("fg", "#cbd5e1"), bg="#1e293b", **kw)

        c1 = card("A · From a picture (free, offline)")
        r = tk.Frame(c1, bg="#1e293b"); r.pack(fill=tk.X, pady=4)
        btn(r, "🖼️ Choose picture…", self.on_pic_choose).pack(side=tk.LEFT)
        self.pic_name = lab(r, "  no picture yet")
        self.pic_name.pack(side=tk.LEFT)
        r = tk.Frame(c1, bg="#1e293b"); r.pack(fill=tk.X)
        lab(r, "Effect").pack(side=tk.LEFT)
        self.pic_effect = tk.StringVar(value="spin")
        om = tk.OptionMenu(r, self.pic_effect, *it.EFFECTS, command=lambda v: self.pic_help.configure(text=it.EFFECTS[v]))
        om.configure(bg="#334155", fg="white", bd=0, highlightthickness=0, width=8); om.pack(side=tk.LEFT, padx=4)
        lab(r, "Background").pack(side=tk.LEFT, padx=(8, 0))
        self.pic_bg = tk.StringVar(value="sparkle")
        om = tk.OptionMenu(r, self.pic_bg, *it.BACKGROUNDS)
        om.configure(bg="#334155", fg="white", bd=0, highlightthickness=0, width=8); om.pack(side=tk.LEFT, padx=4)
        self.pic_keep = tk.BooleanVar(value=False)
        tk.Checkbutton(r, text="Keep picture background", variable=self.pic_keep, bg="#1e293b", fg="#e2e8f0",
                       selectcolor="#0f172a", activebackground="#1e293b", font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=8)
        self.pic_help = lab(c1, it.EFFECTS["spin"], fg="#94a3b8")
        self.pic_help.pack(anchor=tk.W)
        btn(c1, "✨ Make animation", self.on_pic_make, "#2563eb").pack(anchor=tk.W, pady=(6, 0))

        c2 = card("B · With any AI chat")
        r = tk.Frame(c2, bg="#1e293b"); r.pack(fill=tk.X, pady=4)
        lab(r, "Describe it in words:").pack(side=tk.LEFT)
        self.pic_idea = tk.Entry(r, width=34, bg="#0f172a", fg="#38bdf8", insertbackground="white", bd=1)
        self.pic_idea.pack(side=tk.LEFT, padx=6)
        lab(c2, "e.g. \"a jumping frog\" or \"a slow purple storm\". Text only, not a link.", fg="#94a3b8").pack(anchor=tk.W)
        r = tk.Frame(c2, bg="#1e293b"); r.pack(fill=tk.X, pady=4)
        self.pic_attach = tk.BooleanVar(value=False)
        tk.Checkbutton(r, text="I'll also attach a picture in the AI chat", variable=self.pic_attach, bg="#1e293b",
                       fg="#e2e8f0", selectcolor="#0f172a", activebackground="#1e293b",
                       font=("Segoe UI", 9)).pack(side=tk.LEFT)
        btn(r, "📋 Copy the prompt", self.on_pic_prompt).pack(side=tk.LEFT, padx=8)
        lab(c2, "Paste the prompt into any AI chat, then paste its whole answer here:",
            fg="#94a3b8").pack(anchor=tk.W)
        self.pic_answer = tk.Text(c2, height=6, bg="#020617", fg="#e2e8f0", insertbackground="white", bd=0,
                                  font=("Consolas", 9))
        self.pic_answer.pack(fill=tk.X, pady=4)
        btn(c2, "✨ Make animation from the answer", self.on_pic_answer, "#2563eb").pack(anchor=tk.W)

        r = tk.Frame(left, bg="#0f172a"); r.pack(fill=tk.X)
        btn(r, "⚡ Play on Cube", self.on_play_current, "#10b981").pack(side=tk.LEFT)
        btn(r, "💾 Save JSON…", self.on_pic_save).pack(side=tk.LEFT, padx=6)
        self.pic_status = tk.Label(r, text="", font=("Segoe UI", 9), fg="#93c5fd", bg="#0f172a",
                                   wraplength=420, justify=tk.LEFT)
        self.pic_status.pack(side=tk.LEFT, padx=6)

        tk.Label(right, text="Preview", font=("Segoe UI", 9, "bold"), fg="#38bdf8", bg="#1e293b").pack(anchor=tk.W)
        self._make_cube_view(right).pack(pady=6)

    def on_pic_choose(self):
        p = filedialog.askopenfilename(title="Choose a picture",
                                       filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"), ("All files", "*.*")])
        if p:
            self.pic_path = p
            self.pic_name.configure(text="  " + os.path.basename(p))

    def _pic_show(self, obj, msg):
        self.pic_result = obj
        self.set_current(animation_from_json(obj))
        self.pic_status.configure(text=msg)

    def on_pic_make(self):
        if not self.pic_path:
            messagebox.showinfo("Pick a picture", "Choose a picture first.")
            return
        try:
            obj, frames = self.image_tool.make_offline(self.pic_path, self.pic_effect.get(), self.pic_bg.get(),
                                                       keep_background=self.pic_keep.get())
        except Exception as e:
            messagebox.showerror("Could not convert", str(e))
            return
        self._pic_show(obj, f"Made '{obj['name']}' ({len(frames)} frames).")

    def on_pic_prompt(self):
        idea, picture = self.pic_idea.get().strip(), bool(self.pic_attach.get())
        if not idea and not picture:
            messagebox.showinfo("Describe it first", "Type an idea in words, or tick 'I'll also attach a picture'.")
            return
        try:
            prompt = self.image_tool.build_prompt(idea, picture=picture)
        except TypeError:                     # an older tittle_image.py next to this file
            prompt = self.image_tool.build_prompt(idea)
        self.root.clipboard_clear()
        self.root.clipboard_append(prompt)
        self.pic_status.configure(text="Prompt copied. Paste it into any AI chat" +
                                  (", attach your picture," if picture else "") + " and send.")

    def on_pic_answer(self):
        text = self.pic_answer.get("1.0", tk.END)
        if not text.strip():
            messagebox.showinfo("Nothing pasted", "Paste the AI's answer into the box first.")
            return
        try:
            obj, frames, recipe = self.image_tool.make_from_recipe(text)
        except Exception as e:
            messagebox.showerror("Couldn't read that answer", str(e))
            return
        self._pic_show(obj, f"'{obj['name']}': {recipe.get('description', '')}")

    def on_pic_save(self):
        if not self.pic_result:
            messagebox.showinfo("Nothing yet", "Make an animation first.")
            return
        safe = re.sub(r"[^\w\- ]", "", self.pic_result["name"]).strip().replace(" ", "_") or "animation"
        p = filedialog.asksaveasfilename(title="Save animation", defaultextension=".json", initialfile=f"{safe}.json",
                                         filetypes=[("Tittle animation", "*.json")])
        if p:
            self.image_tool.save_json(self.pic_result, p)
            self.pic_status.configure(text=f"Saved {os.path.basename(p)}.")

    # --------------------------------------------------------------------------
    # TAB 8: RECOVER THE OFFICIAL ANIMATIONS
    # --------------------------------------------------------------------------
    def _init_originals_tab(self):
        hdr = lambda t: tk.Label(self.tab_orig, text=t, font=("Segoe UI", 11, "bold"), fg="#f8fafc", bg="#0f172a")
        hdr("Get the original animations back").pack(anchor=tk.W)
        tk.Label(self.tab_orig, wraplength=980, justify=tk.LEFT, font=("Segoe UI", 9), fg="#94a3b8", bg="#0f172a",
                 text="The official app never shipped the mood/emoji animations: it downloaded them from "
                      "app.tittlelight.com, which is gone. Two places may still hold them: (1) the cube itself, which keeps "
                      "animations it has played under their original ID and plays them without a download, and (2) an old "
                      "phone (Android or iPhone) that had the app, whose app data may still hold the animations it downloaded.").pack(
            anchor=tk.W, pady=(2, 10))

        def card(parent, title):
            f = tk.Frame(parent, bg="#1e293b", padx=12, pady=10)
            tk.Label(f, text=title, font=("Segoe UI", 10, "bold"), fg="#38bdf8", bg="#1e293b").pack(anchor=tk.W, pady=(0, 6))
            return f

        def btn(parent, text, cmd, bg="#334155"):
            return tk.Button(parent, text=text, bg=bg, fg="white", font=("Segoe UI", 9, "bold"), bd=0, padx=10, pady=4,
                             cursor="hand2", command=cmd)

        def entry(parent, var, w=7):
            return tk.Entry(parent, textvariable=var, width=w, font=("Consolas", 10), bg="#0f172a", fg="#38bdf8",
                            insertbackground="white", bd=1)

        lab = lambda parent, t: tk.Label(parent, text=t, font=("Segoe UI", 9), fg="#cbd5e1", bg="#1e293b")

        cols = tk.Frame(self.tab_orig, bg="#0f172a")
        cols.pack(fill=tk.BOTH, expand=True)
        left = tk.Frame(cols, bg="#0f172a")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right = tk.Frame(cols, bg="#0f172a")
        right.grid(row=0, column=1, sticky="nsew")
        cols.columnconfigure(0, weight=1, uniform="orig")
        cols.columnconfigure(1, weight=1, uniform="orig")
        cols.rowconfigure(0, weight=1)

        def listbox(parent, height, fg):
            f = tk.Frame(parent, bg="#020617")
            lb = tk.Listbox(f, height=height, bg="#020617", fg=fg, font=("Consolas", 9), bd=0,
                            selectbackground="#2563eb", activestyle="none", exportselection=False)
            sb = tk.Scrollbar(f, orient=tk.VERTICAL, command=lb.yview)
            lb.configure(yscrollcommand=sb.set)
            sb.pack(side=tk.RIGHT, fill=tk.Y)
            lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            return f, lb

        note = lambda parent, t: tk.Label(parent, wraplength=440, justify=tk.LEFT, font=("Segoe UI", 8), fg="#94a3b8",
                                          bg="#1e293b", text=t)

        # (1) on the cube: three separate tools
        c_now = card(left, "1 · What is the cube playing now?")
        c_now.pack(fill=tk.X)
        r = tk.Frame(c_now, bg="#1e293b"); r.pack(fill=tk.X)
        btn(r, "🔎 Ask the cube", self.on_query_mode, "#2563eb").pack(side=tk.LEFT)
        self.mode_lbl = lab(r, "  (power the cube on with its last mood first)")
        self.mode_lbl.pack(side=tk.LEFT)
        note(c_now, "Fills in the ID and version below, and narrows the scan to the IDs around it.").pack(anchor=tk.W, pady=(4, 0))

        c_play = card(left, "2 · Play a stored ID")
        c_play.pack(fill=tk.X, pady=(8, 0))
        r = tk.Frame(c_play, bg="#1e293b"); r.pack(fill=tk.X)
        self.st_id, self.st_ver, self.st_type = tk.StringVar(value="1"), tk.StringVar(value="1"), tk.IntVar(value=OPCODE_MOOD)
        lab(r, "ID").pack(side=tk.LEFT); entry(r, self.st_id).pack(side=tk.LEFT, padx=4)
        lab(r, "Version").pack(side=tk.LEFT); entry(r, self.st_ver, 4).pack(side=tk.LEFT, padx=4)
        for val, txt in ((OPCODE_MOOD, "Mood"), (0x20, "Emoji")):
            tk.Radiobutton(r, text=txt, variable=self.st_type, value=val, bg="#1e293b", fg="#e2e8f0", selectcolor="#0f172a",
                           activebackground="#1e293b", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        btn(r, "▶ Play stored", self.on_play_stored, "#10b981").pack(side=tk.LEFT, padx=6)
        btn(r, "⭐ Save", self.on_save_stored, "#a855f7").pack(side=tk.LEFT)

        c_scan = card(left, "3 · Scan for stored animations")
        c_scan.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        r = tk.Frame(c_scan, bg="#1e293b"); r.pack(fill=tk.X)
        self.sc_from, self.sc_to, self.sc_vers = tk.StringVar(value="1"), tk.StringVar(value="6000"), tk.StringVar(value="1-6")
        lab(r, "IDs").pack(side=tk.LEFT); entry(r, self.sc_from, 5).pack(side=tk.LEFT, padx=2)
        lab(r, "to").pack(side=tk.LEFT); entry(r, self.sc_to, 5).pack(side=tk.LEFT, padx=2)
        lab(r, "versions").pack(side=tk.LEFT); entry(r, self.sc_vers, 5).pack(side=tk.LEFT, padx=2)
        btn(r, "Scan", self.on_scan, "#2563eb").pack(side=tk.LEFT, padx=4)
        btn(r, "Stop", lambda: self.client.stop_flag.set(), "#991b1b").pack(side=tk.LEFT)
        note(c_scan, "Every hit starts playing and is added to ⭐ Saved presets. The full default range takes a while; "
                     "the status bar shows the time left.").pack(anchor=tk.W, pady=(4, 4))
        lab(c_scan, "Found on the cube (double-click or Enter to play):").pack(anchor=tk.W, pady=(0, 2))
        f, self.hits_list = listbox(c_scan, 6, "#4ade80")
        f.pack(fill=tk.BOTH, expand=True)
        self.hits_list.bind("<Double-Button-1>", lambda e: self.on_play_hit())
        self.hits_list.bind("<Return>", lambda e: self.on_play_hit())

        # saved cube presets
        c3 = card(right, "⭐ Saved presets (stored on the cube)")
        c3.pack(fill=tk.BOTH, expand=True)
        note(c3, "One click plays an animation the cube still holds. The animation itself stays inside the cube, "
                 "so a factory reset would erase it; only an old phone's app data holds a full copy.").pack(anchor=tk.W)
        r = tk.Frame(c3, bg="#1e293b"); r.pack(fill=tk.X, pady=4)
        btn(r, "▶ Play", self.on_play_saved, "#10b981").pack(side=tk.LEFT)
        btn(r, "✏️ Rename", self.on_rename_saved).pack(side=tk.LEFT, padx=4)
        btn(r, "🗑 Delete", self.on_delete_saved, "#991b1b").pack(side=tk.LEFT)
        lab(c3, "Double-click or Enter to play:").pack(anchor=tk.W, pady=(2, 2))
        f, self.saved_list = listbox(c3, 8, "#fde68a")
        f.pack(fill=tk.BOTH, expand=True)
        self.saved_list.bind("<Double-Button-1>", lambda e: self.on_play_saved())
        self.saved_list.bind("<Return>", lambda e: self.on_play_saved())
        self._refresh_saved()

        # (2) old phone database
        c2 = card(right, "From an old phone's Tittle app")
        c2.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        note(c2, "Open a database file you copied out of the old Tittle app yourself: tittle.db from Android, or "
                 "the .sqlite file from the iPhone app's folder (see IPHONE_GUIDE.md for where to look). The file "
                 "is only read on this computer.").pack(anchor=tk.W)
        r = tk.Frame(c2, bg="#1e293b"); r.pack(fill=tk.X, pady=6)
        btn(r, "📂 Open app database…", self.on_open_db, "#2563eb").pack(side=tk.LEFT)
        btn(r, "⚡ Play", self.on_play_db, "#10b981").pack(side=tk.LEFT, padx=4)
        btn(r, "💾 Save all…", self.on_export_db).pack(side=tk.LEFT)
        f, self.db_list = listbox(c2, 5, "#e2e8f0")
        f.pack(fill=tk.BOTH, expand=True)
        self.db_list.bind("<<ListboxSelect>>", lambda e: self.on_select_db())
        self.db_list.bind("<Double-Button-1>", lambda e: self.on_play_db())
        self.db_list.bind("<Return>", lambda e: self.on_play_db())
        self.db_anims = []

    def on_query_mode(self):
        def run():
            info = self.client.query_mode()
            if info:
                self.events.put(("call", lambda: (
                    self.mode_lbl.configure(text=f"  {info['mode_name']} · ID {info['anim_id']} · byte10={info['raw'][10]}"),
                    self.st_id.set(str(info["anim_id"])),
                    self.st_ver.set(str(info["raw"][10] or 1)),
                    self.sc_from.set(str(max(1, info["anim_id"] - 500))),
                    self.sc_to.set(str(info["anim_id"] + 500)),
                    self.sc_vers.set(f"1-{max(6, (info['raw'][10] or 1) + 2)}"),
                    self._fill_mode_ids(info),
                    self.st_type.set(OPCODE_MOOD if info["mode"] == 0x50 else 0x20))))
            else:
                self.events.put(("call", lambda: self.mode_lbl.configure(text="  no mode reply (see the Debug tab)")))
        self._bg(run)

    def _fill_mode_ids(self, info):
        ver = str(info["raw"][10] or 1)
        if info["mode"] == 0x30:
            self.clock_face_var.set(str(info["anim_id"])); self.clock_ver_var.set(ver)
            self.client.log(f"[+] Clock face ID {info['anim_id']} (version {ver}) copied to the 🕒 Clock tab.")
        elif info["mode"] == 0x40:
            self.music_style.set(str(info["anim_id"])); self.music_ver.set(ver)
            self.client.log(f"[+] Visualizer ID {info['anim_id']} (version {ver}) copied to the 🎵 Music tab.")

    def _parse_versions(self, text):
        out = set()
        for part in text.replace(" ", "").split(","):
            if "-" in part:
                a, b = part.split("-", 1)
                out.update(range(int(a), int(b) + 1))
            elif part:
                out.add(int(part))
        return sorted(v for v in out if 0 <= v <= 255) or [1]

    # ---- saved cube presets (kept in the config file) ----
    def _saved_presets(self):
        return list(read_config(self.client.config_file).get("stored_presets", []))

    def _refresh_saved(self):
        self.saved_list.delete(0, tk.END)
        for p in self._saved_presets():
            kind = "Mood" if p.get("opcode") == OPCODE_MOOD else "Emoji"
            self.saved_list.insert(tk.END, f"{p['name'][:28]:<29} {kind} · ID {p['id']} v{p['version']}")

    def on_save_stored(self):
        try:
            aid, ver = int(self.st_id.get()), int(self.st_ver.get())
        except ValueError:
            messagebox.showerror("Bad ID", "ID and version must be numbers.")
            return
        from tkinter import simpledialog
        name = simpledialog.askstring("Save preset", f"Name for stored animation ID {aid} (version {ver}):",
                                      initialvalue=f"Original {aid}", parent=self.root)
        if not name:
            return
        presets = [p for p in self._saved_presets() if not (p["id"] == aid and p["version"] == ver)]
        presets.append({"name": name.strip(), "id": aid, "version": ver, "opcode": int(self.st_type.get())})
        update_config(self.client.config_file, stored_presets=presets)
        self._refresh_saved()
        self.client.log(f"[+] Saved preset '{name}' (ID {aid}, version {ver}).")

    def _selected_saved(self):
        sel = self.saved_list.curselection()
        presets = self._saved_presets()
        return (sel[0], presets) if sel and sel[0] < len(presets) else (None, presets)

    def on_play_saved(self):
        i, presets = self._selected_saved()
        if i is not None:
            p = presets[i]
            self.client.play_stored(p["id"], p["version"], p.get("opcode", OPCODE_MOOD), self.cur_intensity, name=p["name"])

    def on_rename_saved(self):
        i, presets = self._selected_saved()
        if i is None:
            return
        from tkinter import simpledialog
        name = simpledialog.askstring("Rename preset", "New name:", initialvalue=presets[i]["name"], parent=self.root)
        if name:
            presets[i]["name"] = name.strip()
            update_config(self.client.config_file, stored_presets=presets)
            self._refresh_saved()

    def on_delete_saved(self):
        i, presets = self._selected_saved()
        if i is not None and messagebox.askyesno("Delete preset", f"Forget '{presets[i]['name']}'? "
                                                 "(This only removes the shortcut, not the animation on the cube.)"):
            del presets[i]
            update_config(self.client.config_file, stored_presets=presets)
            self._refresh_saved()

    def on_play_stored(self):
        try:
            self.client.play_stored(int(self.st_id.get()), int(self.st_ver.get()), self.st_type.get(), self.cur_intensity)
        except ValueError:
            messagebox.showerror("Bad ID", "ID and version must be numbers.")

    def on_scan(self):
        try:
            first, last = int(self.sc_from.get()), int(self.sc_to.get())
            versions = self._parse_versions(self.sc_vers.get())
        except ValueError:
            messagebox.showerror("Bad range", "Use numbers, e.g. 1 to 200 and versions 1-3.")
            return
        self.hits_list.delete(0, tk.END)
        opcode = self.st_type.get()
        def hit(i, v):
            def ui():
                self.hits_list.insert(tk.END, f"ID {i:<6} version {v:<3} {'Mood' if opcode == OPCODE_MOOD else 'Emoji'}")
                presets = self._saved_presets()
                if not any(p["id"] == i and p["version"] == v for p in presets):
                    presets.append({"name": f"Found ID {i} v{v}", "id": i, "version": v, "opcode": opcode})
                    update_config(self.client.config_file, stored_presets=presets)
                    self._refresh_saved()
            self.events.put(("call", ui))
        self.client.scan_stored(first, last, versions, opcode, self.cur_intensity, on_hit=hit)

    def on_play_hit(self):
        sel = self.hits_list.curselection()
        if sel:
            parts = self.hits_list.get(sel[0]).split()
            self.st_id.set(parts[1]); self.st_ver.set(parts[3])
            self.on_play_stored()

    def on_open_db(self):
        path = filedialog.askopenfilename(title="Open the Tittle app database",
                                          filetypes=[("Tittle app database", "*.db *.sqlite *.sqlite3 *.store"),
                                                     ("All files", "*.*")])
        if not path:
            return
        try:
            anims, notes = load_app_database(path)
        except Exception as e:
            messagebox.showerror("Could not read database", str(e))
            return
        self.db_anims = anims
        self.db_list.delete(0, tk.END)
        for a in anims:
            self.db_list.insert(tk.END, f"{a.name[:34]:<35} ID {a.anim_id} v{a.version} · {len(a)}f")
        self.client.log(f"[+] {len(anims)} animation(s) recovered from {os.path.basename(path)}")
        for n in notes:
            self.client.log(f"    {n}")

    def _show_db_anims(self, anims, source):
        self.db_anims = anims
        self.db_list.delete(0, tk.END)
        for a in anims:
            self.db_list.insert(tk.END, f"{a.name[:34]:<35} ID {a.anim_id} v{a.version} · {len(a)}f")
        self.client.log(f"[+] {len(anims)} animation(s) recovered from {source}")

    def _selected_db(self):
        sel = self.db_list.curselection()
        return self.db_anims[sel[0]] if sel else None

    def on_select_db(self):
        a = self._selected_db()
        if a:
            self.set_current(a)

    def on_play_db(self):
        a = self._selected_db()
        if a:
            self.set_current(a)
            self.client.upload_frames(a.name, a.frames, a.interval_ms, self.cur_intensity, a.anim_id, a.opcode,
                                      a.version, a.raw_interval, a.raw_binary)

    def on_export_db(self):
        if not self.db_anims:
            return
        folder = filedialog.askdirectory(title="Folder for the recovered animations")
        if not folder:
            return
        for a in self.db_anims:
            safe = re.sub(r"[^\w\- ]", "", a.name).strip().replace(" ", "_") or f"anim_{a.anim_id}"
            save_json_animation(a, os.path.join(folder, f"{safe}_{a.anim_id}.json"))
        self.client.log(f"[+] Exported {len(self.db_anims)} recovered animations to {folder}")

    # --------------------------------------------------------------------------
    # TAB 8: HARDWARE ON/OFF SCHEDULE (0x80 timers, stored and run by the cube)
    # --------------------------------------------------------------------------
    NIGHT_TIMER_ID = 1001
    MORNING_TIMER_ID = 1002

    def _init_schedule_tab(self):
        cfg = read_config().get("schedule", {})
        tk.Label(self.tab_sched, text="Overnight Schedule (cube-side timers, OpCode 0x80)", font=("Segoe UI", 11, "bold"),
                 fg="#f8fafc", bg="#0f172a").pack(anchor=tk.W)
        tk.Label(self.tab_sched, wraplength=980, justify=tk.LEFT, font=("Segoe UI", 9), fg="#94a3b8", bg="#0f172a",
                 text="Timers are stored in the cube and run every day, even when this app is closed. Each Save also "
                      "sets the cube's clock from this computer.").pack(anchor=tk.W, pady=(2, 12))

        def time_row(parent, label, h, m):
            row = tk.Frame(parent, bg="#1e293b"); row.pack(anchor=tk.W, pady=4)
            tk.Label(row, text=label, font=("Segoe UI", 10, "bold"), fg="#e2e8f0", bg="#1e293b", width=26,
                     anchor="w").pack(side=tk.LEFT)
            hv, mv = tk.StringVar(value=f"{h:02d}"), tk.StringVar(value=f"{m:02d}")
            for var, top in ((hv, 23), (mv, 59)):
                tk.Spinbox(row, from_=0, to=top, textvariable=var, width=3, format="%02.0f", wrap=True,
                           font=("Consolas", 12), bg="#0f172a", fg="#38bdf8", buttonbackground="#334155").pack(side=tk.LEFT)
                if var is hv:
                    tk.Label(row, text=":", font=("Consolas", 12), fg="#e2e8f0", bg="#1e293b").pack(side=tk.LEFT)
            return row, hv, mv

        def btn(parent, text, cmd, bg="#334155"):
            return tk.Button(parent, text=text, bg=bg, fg="white", font=("Segoe UI", 9, "bold"), bd=0, padx=10, pady=4,
                             cursor="hand2", command=cmd)

        night = tk.Frame(self.tab_sched, bg="#1e293b", padx=15, pady=12)
        night.pack(fill=tk.X, pady=(0, 10))
        row, self.off_h, self.off_m = time_row(night, "🌙 Turn OFF every night at", *cfg.get("off", (23, 0)))
        btn(row, "Save", self.on_save_night, "#10b981").pack(side=tk.LEFT, padx=(16, 4))
        btn(row, "Remove", self.on_remove_night, "#991b1b").pack(side=tk.LEFT)

        morning = tk.Frame(self.tab_sched, bg="#1e293b", padx=15, pady=12)
        morning.pack(fill=tk.X)
        row, self.on_h, self.on_m = time_row(morning, "☀️ Turn back ON every morning at", *cfg.get("on", (7, 0)))
        btn(row, "Save", self.on_save_morning, "#10b981").pack(side=tk.LEFT, padx=(16, 4))
        btn(row, "Remove", self.on_remove_morning, "#991b1b").pack(side=tk.LEFT)
        self.on_kind = tk.StringVar(value=cfg.get("kind", "lamp"))
        for val, txt in (("lamp", "with the Lamp tab's colour and brightness"),
                         ("anim", "with the animation in the 📁 Files preview (e.g. a preset you just played)")):
            tk.Radiobutton(morning, text=txt, variable=self.on_kind, value=val, bg="#1e293b", fg="#e2e8f0",
                           selectcolor="#0f172a", activebackground="#1e293b", font=("Segoe UI", 9)).pack(anchor=tk.W)
        tk.Label(morning, text="It stays on until the night-off time.", font=("Segoe UI", 8), fg="#94a3b8",
                 bg="#1e293b").pack(anchor=tk.W, pady=(4, 0))

        tk.Label(self.tab_sched, text="The cube can't report which timers it holds, so this tab shows what the app last saved.",
                 font=("Segoe UI", 9), fg="#94a3b8", bg="#0f172a").pack(anchor=tk.W, pady=(12, 4))
        tk.Label(self.tab_sched, wraplength=980, justify=tk.LEFT, font=("Segoe UI", 9), fg="#94a3b8", bg="#0f172a",
                 text="The morning-on timer uses the brightness from the ☀ slider and follows it when you change it later.").pack(anchor=tk.W)

    def _hm(self, hv, mv):
        h, m = max(0, min(23, int(hv.get()))), max(0, min(59, int(mv.get())))
        return h, m

    def _save_sched(self, **extra):
        sc = read_config(self.client.config_file).get("schedule", {})
        sc.update({"off": list(self._hm(self.off_h, self.off_m)), "on": list(self._hm(self.on_h, self.on_m)),
                   "kind": self.on_kind.get()}, **extra)
        update_config(self.client.config_file, schedule=sc)

    def on_save_night(self, enabled=True):
        try:
            h, m = self._hm(self.off_h, self.off_m)
        except ValueError:
            return
        self._save_sched()
        pkt = TittleClient.build_switch_off_timer(self.NIGHT_TIMER_ID, h, m, EVERY_DAY, enabled)
        self.client.send_timer(pkt, f"off at {h:02d}:{m:02d} every day" if enabled else "night-off removed")

    def on_remove_night(self):
        self.on_save_night(enabled=False)

    def on_save_morning(self, enabled=True):
        try:
            start, end = self._hm(self.on_h, self.on_m), self._hm(self.off_h, self.off_m)
        except ValueError:
            return
        self._save_sched(morning_enabled=enabled, intensity=self.cur_intensity, rgb=[self.cur_r, self.cur_g, self.cur_b],
                         anim_id=(self.current.anim_id or content_anim_id(self.current.frames))
                         if (self.on_kind.get() == "anim" and self.current is not None) else None)
        label = f"on at {start[0]:02d}:{start[1]:02d} every day" if enabled else "morning-on removed"
        if self.on_kind.get() == "anim" and self.current is not None:
            a = self.current
            aid = a.anim_id if a.anim_id else content_anim_id(a.frames)
            interval = a.raw_interval if (a.anim_id and a.raw_interval is not None) else \
                TittleClient.interval_byte(OPCODE_MOOD, a.interval_ms)
            pkt = TittleClient.build_animation_timer(self.MORNING_TIMER_ID, start, end, len(a), interval, aid,
                                                     a.version, self.cur_intensity, EVERY_DAY, enabled)
            self.client.send_timer(pkt, f"{label} with '{a.name}'", frames=a.frames if enabled else None,
                                   raw_binary=a.raw_binary if enabled else None)
        else:
            pkt = TittleClient.build_lamp_timer(self.MORNING_TIMER_ID, start, end,
                                                (self.cur_r, self.cur_g, self.cur_b), self.cur_intensity, EVERY_DAY, enabled)
            self.client.send_timer(pkt, label)

    def on_remove_morning(self):
        self.on_save_morning(enabled=False)

    # --------------------------------------------------------------------------
    # TAB 9: RAW PACKET DEBUG LOG
    # --------------------------------------------------------------------------
    def _init_debug_tab(self):
        top = tk.Frame(self.tab_debug, bg="#0f172a")
        top.pack(fill=tk.X, pady=(0, 6))

        tk.Label(top, text="Real-Time TCP / UDP Socket Packet Inspector", font=("Segoe UI", 10, "bold"), fg="#f8fafc", bg="#0f172a").pack(side=tk.LEFT)
        btn_clear = tk.Button(top, text="Clear Log", bg="#334155", fg="white", font=("Segoe UI", 8), bd=0, padx=8, pady=2,
                              command=lambda: self.log_text.delete("1.0", tk.END))
        btn_clear.pack(side=tk.RIGHT)
        for label, cmd in (("💾 Save log…", self.save_log), ("📋 Copy log", self.copy_log),
                           ("🧪 Wake test", lambda: self._bg(self.client.wake_cube, True))):
            tk.Button(top, text=label, bg="#2563eb", fg="white", font=("Segoe UI", 8, "bold"), bd=0, padx=8, pady=2,
                      command=cmd).pack(side=tk.RIGHT, padx=(0, 6))

        fmt_row = tk.Frame(self.tab_debug, bg="#0f172a")
        fmt_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(fmt_row, text="Upload format:", font=("Segoe UI", 9, "bold"), fg="#cbd5e1", bg="#0f172a").pack(side=tk.LEFT)
        self.fmt_var = tk.StringVar(value=self.client.upload_format)
        for val, txt in (("chunks", "Raw chunks 0x21-0x23 (default, works on Ben's cube)"),
                         ("compressed", "Compressed 0x25 (official app, fw 2.5+)"), ("auto", "Auto (ask firmware)")):
            tk.Radiobutton(fmt_row, text=txt, variable=self.fmt_var, value=val, bg="#0f172a", fg="#e2e8f0",
                           selectcolor="#0f172a", activebackground="#0f172a", font=("Segoe UI", 9),
                           command=self._set_format).pack(side=tk.LEFT, padx=4)

        tk.Label(self.tab_debug, text="Cube orientation (test pattern, flips and turns) is in the ⚙️ Settings tab.",
                 font=("Segoe UI", 8), fg="#94a3b8", bg="#0f172a").pack(anchor=tk.W, pady=(0, 6))

        for label, fn in (("Mode?", self.client.query_mode), ("Status", self.client.query_status)):
            tk.Button(top, text=label, bg="#334155", fg="white", font=("Segoe UI", 8), bd=0, padx=8, pady=2,
                      command=lambda fn=fn: self._bg(fn)).pack(side=tk.RIGHT, padx=(0, 6))

        # Raw packet sender
        raw = tk.Frame(self.tab_debug, bg="#0f172a")
        raw.pack(fill=tk.X, side=tk.BOTTOM, pady=(6, 0))
        tk.Label(raw, text="Send raw hex:", font=("Segoe UI", 9, "bold"), fg="#cbd5e1", bg="#0f172a").pack(side=tk.LEFT)
        self.raw_entry = tk.Entry(raw, font=("Consolas", 10), bg="#020617", fg="#38bdf8", insertbackground="white", bd=1)
        self.raw_entry.insert(0, "60 01 00")
        self.raw_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self.raw_entry.bind("<Return>", lambda e: self.on_send_raw())
        self.raw_crlf = tk.BooleanVar(value=True)
        tk.Checkbutton(raw, text="Append 0D 0A", variable=self.raw_crlf, bg="#0f172a", fg="#e2e8f0",
                       selectcolor="#0f172a", activebackground="#0f172a", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        tk.Button(raw, text="Send", bg="#2563eb", fg="white", font=("Segoe UI", 9, "bold"), bd=0, padx=10, pady=2,
                  command=self.on_send_raw).pack(side=tk.LEFT, padx=(6, 0))

        self.log_text = tk.Text(self.tab_debug, bg="#020617", fg="#38bdf8", insertbackground="white", font=("Consolas", 9),
                                wrap=tk.NONE, bd=1, relief=tk.SOLID)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        for tag, color in (("rx", "#4ade80"), ("err", "#f87171"), ("ok", "#a3e635")):
            self.log_text.tag_configure(tag, foreground=color)

    def _init_settings_tab(self):
        tk.Label(self.tab_settings, text="Cube orientation", font=("Segoe UI", 11, "bold"), fg="#f8fafc",
                 bg="#0f172a").pack(anchor=tk.W)
        tk.Label(self.tab_settings, wraplength=950, justify=tk.LEFT, font=("Segoe UI", 9), fg="#cbd5e1", bg="#0f172a",
                 text="If animations look tipped over, upside down, turned or mirrored on your cube, fix it here once. "
                      "Every change shows the test pattern on the cube right away, and the choice is remembered. "
                      "Correct result: a blue floor, a green wall facing you, a red edge on the left, and a white dot "
                      "in the top-right corner of the green wall.").pack(anchor=tk.W, pady=(4, 10))
        card = tk.Frame(self.tab_settings, bg="#1e293b", padx=15, pady=12)
        card.pack(fill=tk.X)
        opts = dict(bg="#1e293b", fg="#e2e8f0", selectcolor="#0f172a", activebackground="#1e293b", font=("Segoe UI", 9))
        self.tilt_var = tk.IntVar(value=int(ORIENTATION.get("tilt", 0)))
        self.turn_var = tk.IntVar(value=int(ORIENTATION.get("turn", 0)))
        self.flip_var = tk.BooleanVar(value=ORIENTATION["flip_v"])
        self.mirror_var = tk.BooleanVar(value=ORIENTATION["mirror_x"])
        self.legacy_var = tk.BooleanVar(value=ORIENTATION["layout"] == "legacy")
        for label, var, hint in (("Tilt forward", self.tilt_var, "use this if the floor shows up on the front or back"),
                                 ("Turn", self.turn_var, "use this if the green wall is on the left, right or back")):
            r = tk.Frame(card, bg="#1e293b"); r.pack(anchor=tk.W, pady=2)
            tk.Label(r, text=f"{label}:", width=12, anchor="w", font=("Segoe UI", 9, "bold"), fg="#cbd5e1",
                     bg="#1e293b").pack(side=tk.LEFT)
            for deg in (0, 90, 180, 270):
                tk.Radiobutton(r, text=f"{deg}°", value=deg, variable=var, command=self._set_orientation,
                               **opts).pack(side=tk.LEFT, padx=4)
            tk.Label(r, text=f"  ({hint})", font=("Segoe UI", 8), fg="#94a3b8", bg="#1e293b").pack(side=tk.LEFT)
        r = tk.Frame(card, bg="#1e293b"); r.pack(anchor=tk.W, pady=(6, 2))
        for text, var in (("Upside down", self.flip_var), ("Mirror left/right", self.mirror_var),
                          ("Old layout (versions r5 and earlier)", self.legacy_var)):
            tk.Checkbutton(r, text=text, variable=var, command=self._set_orientation, **opts).pack(side=tk.LEFT, padx=(0, 12))
        r = tk.Frame(card, bg="#1e293b"); r.pack(anchor=tk.W, pady=(10, 0))
        tk.Button(r, text="🧭 Show test pattern", bg="#2563eb", fg="white", font=("Segoe UI", 9, "bold"), bd=0,
                  padx=10, pady=4, command=self.on_orientation_test).pack(side=tk.LEFT)
        tk.Button(r, text="↺ Reset to default", bg="#334155", fg="white", font=("Segoe UI", 9, "bold"), bd=0,
                  padx=10, pady=4, command=self._reset_orientation).pack(side=tk.LEFT, padx=8)
        self.orient_lbl = tk.Label(r, text="", font=("Segoe UI", 9), fg="#94a3b8", bg="#1e293b")
        self.orient_lbl.pack(side=tk.LEFT, padx=8)
        self._describe_orientation()
        self._init_wifi_setup(self.tab_settings)
        tk.Label(self.tab_settings, text="Old app tools", font=("Segoe UI", 11, "bold"), fg="#f8fafc",
                 bg="#0f172a").pack(anchor=tk.W, pady=(18, 0))
        self.old_tools_var = tk.BooleanVar(value=bool(read_config(self.client.config_file).get("show_old_tools", False)))
        tk.Checkbutton(self.tab_settings, variable=self.old_tools_var, command=self._apply_old_tools,
                       text="Show the 🎵 Music tab and the clock's stored faces. They only work if the cube still has "
                            "content from the old Tittle server, which Ben's cube doesn't.",
                       bg="#0f172a", fg="#cbd5e1", selectcolor="#1e293b", activebackground="#0f172a",
                       font=("Segoe UI", 9)).pack(anchor=tk.W, pady=(4, 0))
        self._apply_old_tools(save=False)

    def _apply_old_tools(self, save=True):
        on = bool(self.old_tools_var.get())
        if save:
            update_config(self.client.config_file, show_old_tools=on)
        if on:
            self.notebook.insert(self.tab_files, self.tab_music)
            self.notebook.tab(self.tab_music, state="normal")
            self.clock_b_frame.pack(fill=tk.X)
        else:
            self.notebook.hide(self.tab_music)
            self.clock_b_frame.pack_forget()

    def _init_wifi_setup(self, parent):
        tk.Label(parent, text="Connect a reset cube to Wi-Fi", font=("Segoe UI", 11, "bold"), fg="#f8fafc",
                 bg="#0f172a").pack(anchor=tk.W, pady=(18, 0))
        tk.Label(parent, wraplength=950, justify=tk.LEFT, font=("Segoe UI", 9), fg="#cbd5e1", bg="#0f172a",
                 text="Only needed for a new or reset cube that isn't on your Wi-Fi yet (the same steps as the official "
                      "app's standard setup). 1) Power the cube on: while it has no Wi-Fi it makes its own open network "
                      "called 'Tittle-AP'. 2) Connect this computer to 'Tittle-AP'. 3) Type your home Wi-Fi below and "
                      "press Send. The cube only supports 2.4 GHz Wi-Fi. 4) Reconnect this computer to your home Wi-Fi "
                      "and press 🔍 Sniff / Connect.").pack(anchor=tk.W, pady=(4, 8))
        card = tk.Frame(parent, bg="#1e293b", padx=15, pady=12)
        card.pack(fill=tk.X)
        cfg = read_config(self.client.config_file)
        self.wifi_ssid = tk.StringVar(value=cfg.get("wifi_ssid", ""))
        self.wifi_pass = tk.StringVar()
        ent = dict(font=("Consolas", 10), bg="#0f172a", fg="#38bdf8", insertbackground="white", bd=1)
        r = tk.Frame(card, bg="#1e293b"); r.pack(anchor=tk.W)
        tk.Label(r, text="Wi-Fi name", font=("Segoe UI", 9, "bold"), fg="#cbd5e1", bg="#1e293b").pack(side=tk.LEFT)
        tk.Entry(r, textvariable=self.wifi_ssid, width=24, **ent).pack(side=tk.LEFT, padx=(6, 14))
        tk.Label(r, text="Password", font=("Segoe UI", 9, "bold"), fg="#cbd5e1", bg="#1e293b").pack(side=tk.LEFT)
        pw = tk.Entry(r, textvariable=self.wifi_pass, width=22, show="•", **ent)
        pw.pack(side=tk.LEFT, padx=6)
        show = tk.BooleanVar(value=False)
        tk.Checkbutton(r, text="show", variable=show, command=lambda: pw.configure(show="" if show.get() else "•"),
                       bg="#1e293b", fg="#e2e8f0", selectcolor="#0f172a", activebackground="#1e293b",
                       font=("Segoe UI", 8)).pack(side=tk.LEFT)
        tk.Button(r, text="📶 Send to cube", bg="#2563eb", fg="white", font=("Segoe UI", 9, "bold"), bd=0,
                  padx=10, pady=4, command=self.on_wifi_setup).pack(side=tk.LEFT, padx=10)
        self.wifi_lbl = tk.Label(card, text="The password is sent only to the cube and is not saved.",
                                 font=("Segoe UI", 9), fg="#94a3b8", bg="#1e293b")
        self.wifi_lbl.pack(anchor=tk.W, pady=(8, 0))

    def on_wifi_setup(self):
        ssid, pw = self.wifi_ssid.get().strip(), self.wifi_pass.get()
        if not ssid:
            messagebox.showerror("Wi-Fi name missing", "Type the name of your home Wi-Fi.")
            return
        update_config(self.client.config_file, wifi_ssid=ssid)
        self.wifi_lbl.configure(text="Sending… (this can take up to a minute)", fg="#38bdf8")

        def run():
            ok, msg = self.client.wifi_setup(ssid, pw)
            if ok:
                msg += " Now reconnect this computer to your home Wi-Fi and press 🔍 Sniff / Connect."
            self.events.put(("call", lambda: self.wifi_lbl.configure(text=msg, fg="#10b981" if ok else "#f59e0b")))
        self._bg(run)

    def _describe_orientation(self):
        o = ORIENTATION
        parts = [f"tilt {o.get('tilt', 0)}°", f"turn {o.get('turn', 0)}°"]
        parts += [p for p, on in (("upside down", o["flip_v"]), ("mirrored", o["mirror_x"]),
                                  ("old layout", o["layout"] == "legacy")) if on]
        self.orient_lbl.configure(text="Current: " + ", ".join(parts) +
                                  ("  (default)" if parts == ["tilt 0°", "turn 0°"] else ""))

    def _reset_orientation(self):
        self.tilt_var.set(0); self.turn_var.set(0)
        self.flip_var.set(False); self.mirror_var.set(False); self.legacy_var.set(False)
        self._set_orientation()

    def _set_orientation(self):
        ORIENTATION.update(layout="legacy" if self.legacy_var.get() else "upright",
                           flip_v=bool(self.flip_var.get()), mirror_x=bool(self.mirror_var.get()),
                           tilt=int(self.tilt_var.get()), turn=int(self.turn_var.get()))
        update_config(self.client.config_file, orientation_r7=dict(ORIENTATION))
        self._describe_orientation()
        self.client.log(f"Orientation set to {ORIENTATION}.")
        if self.client.ip:
            self.on_orientation_test()

    def on_orientation_test(self):
        buf = bytearray(FRAME_BYTES)
        for a in range(8):
            for b in range(8):
                All3DGenerators._put(buf, a, b, 0, (0, 60, 255))          # floor: blue
                All3DGenerators._put(buf, a, 0, b, (0, 200, 60))          # front wall: green
            All3DGenerators._put(buf, 0, 0, a, (255, 0, 0))               # left front edge: red
            All3DGenerators._put(buf, 0, a, 0, (255, 0, 0))               # left floor edge: red
        All3DGenerators._put(buf, 7, 0, 7, (255, 255, 255))               # top right front corner: white
        anim = Animation("Orientation test", [buf], 1000, self.cur_intensity)
        self.set_current(anim)
        self.client.upload_frames(anim.name, anim.frames, 1000, self.cur_intensity)

    def _set_format(self):
        self.client.upload_format = self.fmt_var.get()
        update_config(self.client.config_file, upload_format=self.fmt_var.get())
        self.client.log(f"Upload format set to {self.fmt_var.get()}")

    def copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self._log_header() + self.log_text.get("1.0", tk.END))
        self.client.log("[+] Log copied. Paste it into a message to Chad.")

    def save_log(self):
        p = filedialog.asksaveasfilename(title="Save Debug log", defaultextension=".txt",
                                         initialfile=f"tittle-log-{datetime.now():%Y%m%d-%H%M}.txt",
                                         filetypes=[("Text", "*.txt")])
        if p:
            with open(p, "w", encoding="utf-8") as f:
                f.write(self._log_header() + self.log_text.get("1.0", tk.END))
            self.client.log(f"[+] Log saved to {p}")

    def _log_header(self):
        fw = f"{self.client.fw_version[0]}.{self.client.fw_version[1]}" if self.client.fw_version else "unknown"
        return (f"Tittle Light Control Center {APP_VERSION} | Python {sys.version.split()[0]} | {sys.platform} | "
                f"cube {self.client.ip} | firmware {fw} | upload format {self.client.upload_format}\n\n")

    def on_send_raw(self):
        try:
            data = bytes.fromhex(re.sub(r"[^0-9a-fA-F]", "", self.raw_entry.get()))
        except ValueError:
            messagebox.showerror("Bad hex", "Enter bytes as hex, for example: 60 01 00")
            return
        if not data:
            return
        if self.raw_crlf.get() and not data.endswith(b"\r\n"):
            data += b"\r\n"
        self._bg(self.client.send_packet, data)

    def _bg(self, fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    # Called from worker threads: only enqueue, never touch widgets here.
    def append_log(self, text):
        self.events.put(("log", text))

    def update_status(self, text, color):
        self.events.put(("status", text, color))

    def _insert_log(self, text):
        tag = None
        if "RX ◀──" in text:
            tag = "rx"
        elif "[ERROR]" in text or "[-]" in text:
            tag = "err"
        elif "[+]" in text:
            tag = "ok"
        self.log_text.insert(tk.END, text + "\n", tag)
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 5000:
            self.log_text.delete("1.0", f"{lines - 5000}.0")
        self.log_text.see(tk.END)

    # --------------------------------------------------------------------------
    # TAB 6: CUSTOM ANIMATION FILES (JSON / MagicaVoxel .vox) + LOCAL PREVIEW
    # --------------------------------------------------------------------------
    def _init_files_tab(self):
        tk.Label(self.tab_files, text="Custom Voxel Animations (Import / Export)", font=("Segoe UI", 11, "bold"),
                 fg="#f8fafc", bg="#0f172a").pack(anchor=tk.W, pady=(0, 4))
        tk.Label(self.tab_files, text="Import JSON (also the web editor's .tittle.json and .bin), or MagicaVoxel .vox (each model = one frame), preview it, then play it "
                                      "on the cube. Export saves whatever is in the preview, including presets.",
                 font=("Segoe UI", 9), fg="#94a3b8", bg="#0f172a", wraplength=820, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 10))

        body = tk.Frame(self.tab_files, bg="#0f172a")
        body.pack(fill=tk.BOTH, expand=True)
        left = tk.Frame(body, bg="#1e293b", padx=15, pady=15)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        right = tk.Frame(body, bg="#1e293b", padx=10, pady=10)
        right.pack(side=tk.RIGHT, fill=tk.Y)

        def btn(parent, text, cmd, bg="#334155"):
            return tk.Button(parent, text=text, bg=bg, fg="white", font=("Segoe UI", 9, "bold"), bd=0, padx=10, pady=5,
                             cursor="hand2", command=cmd)

        row = tk.Frame(left, bg="#1e293b")
        row.pack(fill=tk.X)
        btn(row, "📂 Import JSON / BIN…", self.on_import_json).pack(side=tk.LEFT)
        btn(row, "🧊 Import VOX…", self.on_import_vox).pack(side=tk.LEFT, padx=6)
        btn(row, "💾 Export JSON…", self.on_export_json).pack(side=tk.LEFT)

        self.file_info = tk.Label(left, text="Nothing imported yet. The preview shows the last preset you played.",
                                  font=("Segoe UI", 9), fg="#e2e8f0", bg="#1e293b", wraplength=420, justify=tk.LEFT)
        self.file_info.pack(anchor=tk.W, pady=12)

        form = tk.Frame(left, bg="#1e293b")
        form.pack(anchor=tk.W, pady=(0, 10))
        self.anim_interval = tk.IntVar(value=100)
        self.anim_opcode = tk.IntVar(value=OPCODE_PAINT)
        tk.Label(form, text="Frame interval (ms):", font=("Segoe UI", 9, "bold"), fg="#cbd5e1", bg="#1e293b").grid(row=0, column=0, sticky="w")
        tk.Spinbox(form, from_=8, to=2000, increment=10, textvariable=self.anim_interval, width=6,
                   bg="#0f172a", fg="#38bdf8", buttonbackground="#334155",
                   command=self._restart_preview).grid(row=0, column=1, sticky="w", padx=6)
        tk.Label(form, text="Header opcode:", font=("Segoe UI", 9, "bold"), fg="#cbd5e1", bg="#1e293b").grid(row=1, column=0, sticky="w", pady=(8, 0))
        for i, (val, txt) in enumerate(((OPCODE_PAINT, "0x90 Paint"), (OPCODE_MOOD, "0x50 Mood"))):
            tk.Radiobutton(form, text=txt, variable=self.anim_opcode, value=val, bg="#1e293b", fg="#e2e8f0",
                           selectcolor="#0f172a", activebackground="#1e293b", font=("Segoe UI", 9)).grid(
                row=1, column=1 + i, sticky="w", pady=(8, 0))
        tk.Label(left, text="Brightness comes from the Lamp tab's brightness slider.",
                 font=("Segoe UI", 8), fg="#94a3b8", bg="#1e293b").pack(anchor=tk.W)
        btn(left, "⚡ Play on Cube", self.on_play_current, bg="#10b981").pack(anchor=tk.W, pady=(14, 0))

        # Preview (oblique projection: x right, z up, depth y recedes up-right)
        self.preview_title = tk.Label(right, text="", font=("Segoe UI", 9, "bold"), fg="#38bdf8", bg="#1e293b")
        self.preview_title.pack(anchor=tk.W)
        self._make_cube_view(right).pack(pady=6)
        ctl = tk.Frame(right, bg="#1e293b")
        ctl.pack(fill=tk.X)
        self.play_btn = btn(ctl, "Pause", self.toggle_preview)
        self.play_btn.pack(side=tk.LEFT)
        btn(ctl, "◀", lambda: self.step_preview(-1)).pack(side=tk.LEFT, padx=2)
        btn(ctl, "▶", lambda: self.step_preview(1)).pack(side=tk.LEFT)
        self.frame_lbl = tk.Label(ctl, text="", font=("Segoe UI", 9), fg="#e2e8f0", bg="#1e293b")
        self.frame_lbl.pack(side=tk.LEFT, padx=8)
        lay = tk.Frame(right, bg="#1e293b")
        lay.pack(fill=tk.X, pady=(6, 0))
        tk.Label(lay, text="Show Z layer:", font=("Segoe UI", 9), fg="#cbd5e1", bg="#1e293b").pack(side=tk.LEFT)
        self.layer_var = tk.StringVar(value="All")
        om = tk.OptionMenu(lay, self.layer_var, "All", *[str(z) for z in range(8)], command=lambda _: self.render_preview())
        om.configure(bg="#334155", fg="white", activebackground="#475569", bd=0, highlightthickness=0, width=4)
        om.pack(side=tk.LEFT, padx=6)

    def _int(self, var, default, lo, hi):
        try:
            return max(lo, min(hi, int(var.get())))
        except (tk.TclError, ValueError):
            return default

    def on_import_json(self):
        path = filedialog.askopenfilename(title="Import animation (JSON or .bin wire dump)",
                                          filetypes=[("Tittle animation", "*.json *.bin"), ("All files", "*.*")])
        if path:
            try:
                loader = load_bin_animation if path.lower().endswith(".bin") else load_json_animation
                self._loaded(loader(path), path, [])
            except Exception as e:
                messagebox.showerror("Import failed", f"{os.path.basename(path)}:\n{e}")

    def on_import_vox(self):
        path = filedialog.askopenfilename(title="Import MagicaVoxel file",
                                          filetypes=[("MagicaVoxel", "*.vox"), ("All files", "*.*")])
        if path:
            try:
                anim, warnings = load_vox_animation(path, self._int(self.anim_interval, 150, 8, 2000))
                self._loaded(anim, path, warnings)
            except Exception as e:
                messagebox.showerror("Import failed", f"{os.path.basename(path)}:\n{e}")

    def _loaded(self, anim, path, warnings):
        self.set_current(anim)
        lit = sum(1 for f in anim.frames for i in range(512) if any(f[i * 3:i * 3 + 3]))
        info = (f"Loaded '{anim.name}' from {os.path.basename(path)}: {len(anim)} frame(s), "
                f"{anim.interval_ms} ms per frame, {lit} lit voxels in total.")
        if warnings:
            info += "\nNote: " + "; ".join(warnings)
        self.file_info.configure(text=info)
        self.client.log(f"[+] {info}")

    def _sync_current_settings(self):
        a = self.current
        a.interval_ms = self._int(self.anim_interval, a.interval_ms, 8, 2000)
        a.opcode = self.anim_opcode.get()
        a.intensity = clamp(self.cur_intensity)
        return a

    def on_export_json(self):
        if not self.current:
            return
        a = self._sync_current_settings()
        safe = re.sub(r"[^\w\- ]", "", a.name).strip().replace(" ", "_") or "animation"
        path = filedialog.asksaveasfilename(title="Export animation JSON", defaultextension=".json",
                                            initialfile=f"{safe}.json", filetypes=[("JSON animation", "*.json")])
        if not path:
            return
        try:
            save_json_animation(a, path)
        except Exception as e:
            messagebox.showerror("Export failed", str(e))
            return
        self.client.log(f"[+] Exported '{a.name}' ({len(a)} frames) to {path}")
        self.file_info.configure(text=f"Exported '{a.name}' to {os.path.basename(path)}")

    def on_play_current(self):
        if self.current:
            a = self._sync_current_settings()
            self.client.upload_frames(a.name, a.frames, a.interval_ms, a.intensity, a.anim_id, a.opcode,
                                      a.version, a.raw_interval if a.anim_id else None, a.raw_binary)

    # ---------- preview ----------
    def set_current(self, anim):
        self.current = anim
        self.preview_frame = 0
        self.anim_interval.set(anim.interval_ms)
        self.anim_opcode.set(anim.opcode)
        self.preview_title.configure(text=f"Preview: {anim.name} · {len(anim)} frame(s)")
        self._restart_preview()

    def _restart_preview(self):
        if self.preview_job:
            self.root.after_cancel(self.preview_job)
            self.preview_job = None
        self.render_preview()
        if self.preview_playing:
            self.preview_job = self.root.after(self._int(self.anim_interval, 100, 8, 2000), self._tick)

    def _tick(self):
        self.preview_job = None
        if self.current and self.preview_playing:
            self.preview_frame = (self.preview_frame + 1) % len(self.current)
            self.render_preview()
            self.preview_job = self.root.after(self._int(self.anim_interval, 100, 8, 2000), self._tick)

    def toggle_preview(self):
        self.preview_playing = not self.preview_playing
        self.play_btn.configure(text="Pause" if self.preview_playing else "Play")
        self._restart_preview()

    def step_preview(self, d):
        if self.current:
            if self.preview_playing:
                self.toggle_preview()
            self.preview_frame = (self.preview_frame + d) % len(self.current)
            self.render_preview()

    def _make_cube_view(self, parent, W=320, H=330):
        """A canvas drawing the 8x8x8 cube; every view shows the current preview frame."""
        sc = getattr(self, "ui_scale", 1.0)
        W, H = int(W * sc), int(H * sc)
        canvas = tk.Canvas(parent, width=W, height=H, bg="#020617", highlightthickness=0)
        items = [None] * 512
        ox, oy, step, kx, ky = 28 * sc, H - 40 * sc, 27 * sc, 13 * sc, 11 * sc
        for x, y, z in sorted(((x, y, z) for x in range(8) for y in range(8) for z in range(8)),
                              key=lambda p: (-p[1], p[2], p[0])):
            sx, sy, r = ox + x * step + y * kx, oy - z * step - y * ky, (6.0 - y * 0.4) * sc
            items[voxel_index(x, y, z)] = canvas.create_oval(sx - r, sy - r, sx + r, sy + r, fill="#1e293b", outline="")
        canvas.create_text(8 * sc, H - 10 * sc, anchor="w", fill="#64748b", font=("Segoe UI", 7),
                           text="front face nearest · x → right · y ↗ back · z ↑")
        if not hasattr(self, "cube_views"):
            self.cube_views = []
        self.cube_views.append((canvas, items))
        return canvas

    def render_preview(self):
        if not self.current or not getattr(self, "cube_views", None):
            return
        frame = self.current.frames[self.preview_frame % len(self.current)]
        layer = self.layer_var.get()
        only_z = None if layer == "All" else int(layer)
        for idx in range(512):
            r, g, b = frame[idx * 3], frame[idx * 3 + 1], frame[idx * 3 + 2]
            if only_z is not None and idx // 64 != only_z:
                fill = "#0b1220"
            elif r or g or b:
                fill = "#%02x%02x%02x" % (r, g, b)
            else:
                fill = "#1e293b" if (idx // 8) % 8 < 4 else "#162033"
            for canvas, items in self.cube_views:
                canvas.itemconfig(items[idx], fill=fill)
        self.frame_lbl.configure(text=f"frame {self.preview_frame % len(self.current) + 1}/{len(self.current)}")

    def on_click_sniff(self):
        ip = self.ip_entry.get().strip()
        threading.Thread(target=lambda: self.client.sniff_and_connect(forced_ip=ip), daemon=True).start()

    def on_click_off(self):
        threading.Thread(target=self.client.turn_off, daemon=True).start()


# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================
def show_loading_window(root):
    """A small 'Loading…' window while the main window is being built (it takes a few seconds)."""
    try:
        w = tk.Toplevel(root)
        w.overrideredirect(True)
        w.configure(bg="#0f172a", highlightthickness=1, highlightbackground="#38bdf8")
        tk.Label(w, text="✨ Tittle Light", font=("Segoe UI", 16, "bold"), fg="#f8fafc", bg="#0f172a").pack(padx=40, pady=(24, 4))
        tk.Label(w, text="Loading…", font=("Segoe UI", 10), fg="#38bdf8", bg="#0f172a").pack(pady=(0, 24))
        w.update_idletasks()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        w.geometry(f"+{(sw - w.winfo_reqwidth()) // 2}+{(sh - w.winfo_reqheight()) // 2}")
        w.update()
        return w
    except Exception:
        return None


def enable_dpi_awareness():
    """Sharp text on high-DPI Windows screens (otherwise Windows stretches a blurry 96-DPI window)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)      # system DPI aware (Windows 8.1+)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()           # Vista / 7 fallback
    except Exception:
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tittle Light 8x8x8 cube desktop control center")
    ap.add_argument("--ip", help="cube IP address to try first")
    args = ap.parse_args(argv)
    if tk is None:
        print("tkinter is not available. On Linux install it with: sudo apt install python3-tk")
        return 1
    enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()
    splash = show_loading_window(root)
    try:
        import pyi_splash                    # the Windows app's own splash (shown before Python starts)
        pyi_splash.close()
    except Exception:
        pass
    TittleDesktopApp(root, initial_ip=args.ip)
    if splash:
        splash.destroy()
    root.deiconify()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
