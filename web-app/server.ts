/**
 * Tittle Light local bridge.
 *
 * Serves the web app and relays packets from the browser to the cube's TCP port 9999,
 * because browsers cannot open raw TCP sockets.
 *
 *   npm run dev     -> web app + bridge on http://localhost:3000 (Vite dev middleware)
 *   npm run start   -> same, serving the built app from dist/ (run `npm run build` first)
 *
 * HTTP API (JSON):
 *   GET  /api/bridge/health
 *   POST /api/cube/send       { ip, hex, expectResponse? }          one packet, one reply
 *   POST /api/cube/status     { ip }                                0x60 status query
 *   POST /api/cube/animation  { ip, frames: base64[], intervalMs, intensity, animId?, sfOpt?, format?, orientation? }
 *                             frames are z-up (index z*64 + y*8 + x); the bridge converts them to the cube's
 *                             own order (y*64 + (7-z)*8 + x). orientation: { layout: 'upright'|'legacy', flipV, mirrorX }
 *                             format: 'auto' (default, picks by firmware like the app), 'compressed', 'chunks'
 *   POST /api/cube/discover   {}                                    UDP 10000 broadcast + /24 scan
 *   POST /api/cube/pizza      { ip, seconds? }                      easter egg: spinning pizza, then restore
 *
 * WebSocket /ws: send { id, type: 'send'|'status'|'animation'|'discover', ...same fields }.
 * The server streams { id, type: 'log', line }, { id, type: 'progress', done, total } and
 * finally { id, type: 'result', ok, ... }.
 *
 * Environment: PORT (3000), HOST (127.0.0.1), CUBE_PORT (9999), BRIDGE_ALLOW_ANY_IP=1 to lift the
 * private-address restriction.
 */
import express from 'express';
import http from 'node:http';
import net from 'node:net';
import dgram from 'node:dgram';
import os from 'node:os';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { WebSocketServer, WebSocket } from 'ws';
import {
  FRAME_BYTES,
  type UploadFormat,
  base64ToBytes,
  buildCompressedPackets,
  buildFrameChunks,
  buildPaintHeader,
  classifyHandshake,
  contentAnimId,
  normalizeOrientation,
  type Orientation,
  toDevice,
  fromHex,
  parseFirmwareVersion,
  pickUploadFormat,
  pizzaFrames,
  toHex,
} from './src/tittleProtocol.ts';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT || 3000);
const HOST = process.env.HOST || '127.0.0.1';
const CUBE_PORT = Number(process.env.CUBE_PORT || 9999);
const ALLOW_ANY_IP = process.env.BRIDGE_ALLOW_ANY_IP === '1';
const MAX_FRAMES = 512;

type Log = (line: string) => void;
type Progress = (done: number, total: number) => void;

// ---------------------------------------------------------------------------
// Target validation: only LAN / loopback addresses, so the bridge is not an open relay.
// ---------------------------------------------------------------------------
function checkTarget(ip: unknown): string {
  if (typeof ip !== 'string' || net.isIPv4(ip.trim()) === false) throw new Error('ip must be an IPv4 address');
  const a = ip.trim().split('.').map(Number);
  const isPrivate =
    a[0] === 10 ||
    a[0] === 127 ||
    (a[0] === 172 && a[1] >= 16 && a[1] <= 31) ||
    (a[0] === 192 && a[1] === 168) ||
    (a[0] === 169 && a[1] === 254);
  if (!isPrivate && !ALLOW_ANY_IP) throw new Error(`${ip} is not a local network address (set BRIDGE_ALLOW_ANY_IP=1 to override)`);
  return ip.trim();
}

// ---------------------------------------------------------------------------
// One job at a time per cube. The ESP8266 handles a single client badly.
// ---------------------------------------------------------------------------
const queues = new Map<string, Promise<unknown>>();
function serialize<T>(ip: string, job: () => Promise<T>): Promise<T> {
  const prev = queues.get(ip) ?? Promise.resolve();
  const next = prev.catch(() => undefined).then(job);
  queues.set(ip, next.catch(() => undefined));
  return next;
}

// ---------------------------------------------------------------------------
// Small promise wrapper around a TCP socket with buffered reads.
// ---------------------------------------------------------------------------
class CubeSocket {
  private sock: net.Socket;
  private buf: Buffer[] = [];
  private waiter: (() => void) | null = null;
  private closed = false;

  private constructor(sock: net.Socket) {
    this.sock = sock;
    sock.on('data', (d) => {
      this.buf.push(d);
      this.waiter?.();
    });
    sock.on('close', () => {
      this.closed = true;
      this.waiter?.();
    });
    sock.on('error', () => undefined);
  }

  static connect(ip: string, timeoutMs = 4000): Promise<CubeSocket> {
    return new Promise((resolve, reject) => {
      const s = net.createConnection({ host: ip, port: CUBE_PORT });
      s.setNoDelay(true);
      const t = setTimeout(() => {
        s.destroy();
        reject(new Error(`Timed out connecting to ${ip}:${CUBE_PORT}`));
      }, timeoutMs);
      s.once('connect', () => {
        clearTimeout(t);
        resolve(new CubeSocket(s));
      });
      s.once('error', (e) => {
        clearTimeout(t);
        reject(new Error(`Cannot connect to ${ip}:${CUBE_PORT}: ${e.message}`));
      });
    });
  }

  write(bytes: Uint8Array): Promise<void> {
    return new Promise((resolve, reject) => this.sock.write(bytes, (e) => (e ? reject(e) : resolve())));
  }

  /** Waits up to timeoutMs for any bytes; returns them, or null on timeout / close. */
  async read(timeoutMs: number): Promise<Uint8Array | null> {
    if (this.buf.length === 0 && !this.closed) {
      await new Promise<void>((resolve) => {
        const t = setTimeout(() => {
          this.waiter = null;
          resolve();
        }, timeoutMs);
        this.waiter = () => {
          clearTimeout(t);
          this.waiter = null;
          resolve();
        };
      });
    }
    if (this.buf.length === 0) return null;
    const out = Buffer.concat(this.buf);
    this.buf = [];
    return new Uint8Array(out);
  }

  close() {
    this.sock.end();
    setTimeout(() => this.sock.destroy(), 500);
  }
}

// ---------------------------------------------------------------------------
// Operations
// ---------------------------------------------------------------------------
// What each cube was last told to show, so the pizza easter egg can put it back afterwards.
type CubeState = { kind: 'packet'; bytes: Uint8Array } | { kind: 'animation'; req: AnimationRequest };
const lastState = new Map<string, CubeState>();
const RESTORABLE_OPS = new Set([0x10, 0x20, 0x34, 0x41, 0x50, 0x90]);

// Firmware version per cube, from the 0x60 status reply.
const firmware = new Map<string, [number, number] | null>();

function noteStatusReply(ip: string, rx: Uint8Array | null, log: Log) {
  const v = parseFirmwareVersion(rx);
  if (!v) return;
  firmware.set(ip, v);
  log(`Firmware ${v[0]}.${v[1]}: uploads use the ${pickUploadFormat(v) === 'compressed' ? 'compressed 0x25' : 'raw 0x21-0x23 chunk'} format`);
}

async function sendRaw(ip: string, bytes: Uint8Array, expectResponse: boolean, log: Log) {
  const s = await CubeSocket.connect(ip);
  try {
    log(`TX ──▶ ${toHex(bytes)} (${bytes.length} bytes)`);
    await s.write(bytes);
    if (!expectResponse) return null;
    const rx = await s.read(3000);
    log(rx ? `RX ◀── ${toHex(rx)}` : 'RX ◀── [no response / timeout]');
    return rx;
  } finally {
    s.close();
  }
}

async function sendOnce(ip: string, bytes: Uint8Array, expectResponse: boolean, log: Log, remember = true) {
  return serialize(ip, async () => {
    const rx = await sendRaw(ip, bytes, expectResponse, log);
    if (bytes[0] === 0x60) noteStatusReply(ip, rx, log);
    if (remember && RESTORABLE_OPS.has(bytes[0])) lastState.set(ip, { kind: 'packet', bytes });
    return { rx: rx ? toHex(rx) : null };
  });
}

async function resolveFormat(ip: string, requested: UploadFormat | undefined, log: Log): Promise<'compressed' | 'chunks'> {
  if (requested === 'compressed' || requested === 'chunks') return requested;
  if (!firmware.has(ip)) {
    try {
      const rx = await sendRaw(ip, new Uint8Array([0x60, 0x01, 0x00, 0x0d, 0x0a]), true, log);
      firmware.set(ip, parseFirmwareVersion(rx));
      noteStatusReply(ip, rx, log);
    } catch (e: any) {
      log(`Status query failed (${e?.message ?? e})`);
    }
  }
  const v = firmware.get(ip) ?? null;
  if (!v) log('Firmware version unknown: using the compressed 0x25 format, as the official app does');
  return pickUploadFormat(v);
}

interface AnimationRequest {
  ip: string;
  frames: Uint8Array[];
  intervalMs: number;
  intensity: number;
  animId?: number;
  sfOpt?: number;
  format?: UploadFormat;
  /** How app (z-up) frames map onto this cube's byte order. */
  orientation?: Orientation;
}

/**
 * The persistent-socket flow: header, wait for 00 00 20 00 01 01 ("data not ready"),
 * then stream 0x21/0x22/0x23 for every frame on the same socket, then close.
 */
async function uploadAnimation(req: AnimationRequest, log: Log, progress: Progress, remember = true) {
  return serialize(req.ip, () => uploadUnlocked(req, log, progress, remember));
}

/**
 * The persistent-socket flow: header, wait for 00 00 20 00 01 01 ("data not ready"), then stream the
 * frames on the same socket and close it. Firmware 2.5+ (or unknown) gets the RLE-compressed 0x25
 * packets; older firmware gets 0x21/0x22/0x23 chunks per frame.
 */
async function uploadUnlocked(req: AnimationRequest, log: Log, progress: Progress, remember: boolean) {
  // Frames arrive z-up (index z*64 + y*8 + x); the cube wants its own order (y*64 + (7-z)*8 + x).
  const orientation = normalizeOrientation(req.orientation);
  const devFrames = req.frames.map((f) => toDevice(f, orientation));
  const animId = req.animId && req.animId > 0 ? req.animId >>> 0 : contentAnimId(devFrames, req.intervalMs);
  const format = await resolveFormat(req.ip, req.format, log);
  const header = buildPaintHeader(req.frames.length, {
    intervalMs: req.intervalMs,
    animId,
    intensity: req.intensity,
    sfOpt: req.sfOpt ?? 0,
  });
  log(`Connecting to ${req.ip}:${CUBE_PORT} to upload ${req.frames.length} frame(s) @ ${header[3] * 8} ms, id 0x${animId.toString(16)}`);
  const s = await CubeSocket.connect(req.ip);
  try {
    log(`TX ──▶ Header: ${toHex(header)}`);
    await s.write(header);
    const hs = await s.read(3000);
    const kind = classifyHandshake(hs);
    log(hs ? `RX ◀── Handshake: ${toHex(hs)} (${kind.replace(/_/g, ' ')})` : 'RX ◀── [no handshake within 3 s, sending frames anyway]');
    if (remember) lastState.set(req.ip, { kind: 'animation', req: { ...req, animId } });

    if (kind === 'cached') {
      log('Cube already has this animation id cached and is playing it. No upload needed.');
      progress(req.frames.length, req.frames.length);
      return { animId, format, handshake: hs ? toHex(hs) : null, uploaded: false, packetAcks: 0 };
    }

    const packets =
      format === 'compressed' ? buildCompressedPackets(devFrames) : devFrames.flatMap((f, i) => buildFrameChunks(f, i));
    const raw = req.frames.length * FRAME_BYTES;
    const payload = packets.reduce((n, p) => n + p.length, 0);
    log(
      format === 'compressed'
        ? `Sending ${packets.length} compressed 0x25 packet(s), ${payload} bytes for ${raw} bytes of frames`
        : `Sending ${packets.length} raw chunk(s) (0x21/0x22/0x23), ${payload} bytes`,
    );
    let acks = 0;
    for (let i = 0; i < packets.length; i++) {
      await s.write(packets[i]);
      // The app waits for the cube's reply to each packet before sending the next one.
      const ack = await s.read(2000);
      if (ack) acks++;
      if (i < 3 || i === packets.length - 1)
        log(`TX ──▶ 0x${packets[i][0].toString(16)} #${i} (${packets[i].length} bytes)  RX ◀── ${ack ? toHex(ack) : '[no ack]'}`);
      if (format === 'compressed') await new Promise((r) => setTimeout(r, 50));
      progress(Math.round(((i + 1) / packets.length) * req.frames.length), req.frames.length);
    }
    log(`Done: ${acks}/${packets.length} packets acknowledged. Closing the socket so the cube starts looping.`);
    return { animId, format, handshake: hs ? toHex(hs) : null, uploaded: true, packetAcks: acks };
  } finally {
    s.close();
  }
}

/** Easter egg: spin a pepperoni slice for a few seconds, then put back whatever was playing. */
async function pizzaTime(ip: string, seconds: number, orientation: Orientation, log: Log, progress: Progress) {
  return serialize(ip, async () => {
    const before = lastState.get(ip);
    let restore: CubeState | undefined = before;
    if (!restore) {
      // Nothing sent through this bridge yet: ask the cube what it is playing (70 05).
      const rx = await sendRaw(ip, new Uint8Array([0x70, 0x05, 0x0d, 0x0a]), true, log).catch(() => null);
      if (rx && rx.length >= 12 && rx[0] === 0x70 && rx[1] === 0x05 && [0x20, 0x50, 0x90].includes(rx[2])) {
        const id = Array.from(rx.subarray(6, 10));
        const op = rx[2];
        const hdr = new Uint8Array([op, 0, op === 0x50 ? 10 : 1, op === 0x90 ? 12 : 20, ...id, 1, rx[11] || 255, 0, 0, 0, 0, 0, 0, 0x0d, 0x0a]);
        restore = { kind: 'packet', bytes: hdr };
        log(`Cube is playing mode 0x${op.toString(16)}, id ${toHex(id, '')}; will replay it from the cube's cache afterwards`);
      } else log("Couldn't tell what the cube was showing, so it will be switched off afterwards");
    }
    log('🍕 Pizza time!');
    await uploadUnlocked({ ip, frames: pizzaFrames(16), intervalMs: 96, intensity: 255, orientation }, log, progress, false);
    await new Promise((r) => setTimeout(r, seconds * 1000));
    if (restore?.kind === 'animation') {
      log('Restoring the previous animation');
      await uploadUnlocked(restore.req, log, () => undefined, false);
    } else {
      const bytes = restore?.bytes ?? new Uint8Array([0x10, 0, 0, 0, 0, 0x0d, 0x0a]);
      log('Restoring the previous setting');
      await sendRaw(ip, bytes, true, log);
    }
    return { restored: restore ? restore.kind : 'off' };
  });
}

function localIPv4s(): string[] {
  return Object.values(os.networkInterfaces())
    .flat()
    .filter((i): i is os.NetworkInterfaceInfo => !!i && i.family === 'IPv4' && !i.internal)
    .map((i) => i.address);
}

async function probe(ip: string, timeoutMs: number): Promise<boolean> {
  try {
    const s = await CubeSocket.connect(ip, timeoutMs);
    try {
      await s.write(new Uint8Array([0x60, 0x01, 0x00, 0x0d, 0x0a]));
      return !!(await s.read(timeoutMs * 2));
    } finally {
      s.close();
    }
  } catch {
    return false;
  }
}

/** Tier 1: 0x70 0x04 <our IP> broadcast to UDP 10000; the cube connects back to our TCP 9999. Tier 2: /24 scan. */
async function discover(log: Log): Promise<string[]> {
  const found = new Set<string>();
  const locals = localIPv4s();
  log(`Local addresses: ${locals.join(', ') || 'none'}`);

  for (const local of locals) {
    const listener = net.createServer((c) => {
      const addr = c.remoteAddress?.replace(/^::ffff:/, '');
      if (addr) {
        found.add(addr);
        log(`Cube called back from ${addr}`);
      }
      c.destroy();
    });
    const listening = await new Promise<boolean>((resolve) => {
      listener.once('error', () => resolve(false));
      listener.listen(CUBE_PORT, local, () => resolve(true));
    });
    if (!listening) log(`Could not listen on ${local}:${CUBE_PORT} for the callback (port busy?), skipping UDP tier`);
    const udp = dgram.createSocket('udp4');
    await new Promise<void>((resolve) => {
      udp.bind(0, local, () => {
        udp.setBroadcast(true);
        const pkt = Buffer.from([0x70, 0x04, ...local.split('.').map(Number), 0x0d, 0x0a]);
        log(`UDP ──▶ 255.255.255.255:10000 ${toHex(pkt)}`);
        udp.send(pkt, 10000, '255.255.255.255', () => resolve());
      });
      udp.once('error', () => resolve());
    });
    await new Promise((r) => setTimeout(r, 2500));
    udp.close();
    listener.close();
  }
  if (found.size) return [...found];

  for (const local of locals) {
    const prefix = local.split('.').slice(0, 3).join('.');
    log(`Scanning ${prefix}.1-254 for port ${CUBE_PORT}...`);
    const hosts = Array.from({ length: 254 }, (_, i) => `${prefix}.${i + 1}`).filter((h) => h !== local);
    for (let i = 0; i < hosts.length; i += 32) {
      const batch = hosts.slice(i, i + 32);
      const res = await Promise.all(batch.map((h) => probe(h, 300)));
      res.forEach((ok, k) => ok && (found.add(batch[k]), log(`Found a device answering 0x60 at ${batch[k]}`)));
    }
  }
  return [...found];
}

// ---------------------------------------------------------------------------
// Request handling shared by HTTP and WebSocket
// ---------------------------------------------------------------------------
async function handle(type: string, body: any, log: Log, progress: Progress): Promise<Record<string, unknown>> {
  switch (type) {
    case 'send': {
      const ip = checkTarget(body.ip);
      const bytes = fromHex(String(body.hex ?? ''));
      if (bytes.length === 0 || bytes.length > 4096) throw new Error('hex must be 1..4096 bytes');
      return sendOnce(ip, bytes, body.expectResponse !== false, log);
    }
    case 'status': {
      const ip = checkTarget(body.ip);
      return sendOnce(ip, new Uint8Array([0x60, 0x01, 0x00, 0x0d, 0x0a]), true, log);
    }
    case 'animation': {
      const ip = checkTarget(body.ip);
      if (!Array.isArray(body.frames) || body.frames.length === 0) throw new Error('frames must be a non-empty array');
      if (body.frames.length > MAX_FRAMES) throw new Error(`At most ${MAX_FRAMES} frames`);
      const frames = body.frames.map((f: unknown, i: number) => {
        const b = base64ToBytes(String(f));
        if (b.length !== FRAME_BYTES) throw new Error(`Frame ${i + 1} is ${b.length} bytes, expected ${FRAME_BYTES}`);
        return b;
      });
      return uploadAnimation(
        {
          ip,
          frames,
          intervalMs: Math.max(8, Math.min(2040, Number(body.intervalMs) || 100)),
          intensity: Math.max(0, Math.min(255, Number(body.intensity ?? 255))),
          animId: body.animId ? Number(body.animId) : undefined,
          sfOpt: Number(body.sfOpt) || 0,
          format: ['auto', 'compressed', 'chunks'].includes(body.format) ? body.format : 'auto',
          orientation: normalizeOrientation(body.orientation),
        },
        log,
        progress,
      );
    }
    case 'discover':
      return { ips: await discover(log) };
    case 'pizza': {
      const ip = checkTarget(body.ip);
      return pizzaTime(ip, Math.max(1, Math.min(30, Number(body.seconds) || 5)), normalizeOrientation(body.orientation), log, progress);
    }
    default:
      throw new Error(`Unknown request type "${type}"`);
  }
}

// ---------------------------------------------------------------------------
// Server
// ---------------------------------------------------------------------------
async function main() {
  const app = express();
  app.use(express.json({ limit: '8mb' }));

  // Browsers on other origins (e.g. a hosted copy of the UI) may call the local bridge.
  app.use('/api', (req, res, next) => {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    if (req.method === 'OPTIONS') return res.sendStatus(204);
    next();
  });

  app.get('/api/bridge/health', (_req, res) => res.json({ ok: true, bridge: 'tittle', cubePort: CUBE_PORT, localIps: localIPv4s() }));

  for (const type of ['send', 'status', 'animation', 'discover', 'pizza']) {
    app.post(`/api/cube/${type}`, async (req, res) => {
      const log: string[] = [];
      try {
        const result = await handle(type, req.body ?? {}, (l) => log.push(l), () => undefined);
        res.json({ ok: true, ...result, log });
      } catch (e: any) {
        res.status(400).json({ ok: false, error: e?.message ?? String(e), log });
      }
    });
  }

  if (process.env.NODE_ENV === 'production' || process.argv.includes('--prod')) {
    const dist = path.join(__dirname, 'dist');
    if (!fs.existsSync(dist)) console.warn('dist/ not found. Run `npm run build` first.');
    app.use(express.static(dist));
    app.get('*', (_req, res) => res.sendFile(path.join(dist, 'index.html')));
  } else {
    const { createServer } = await import('vite');
    const vite = await createServer({ server: { middlewareMode: true }, appType: 'spa' });
    app.use(vite.middlewares);
  }

  const server = http.createServer(app);
  const wss = new WebSocketServer({ server, path: '/ws' });
  wss.on('connection', (ws: WebSocket) => {
    ws.on('message', async (raw) => {
      let msg: any;
      try {
        msg = JSON.parse(String(raw));
      } catch {
        return ws.send(JSON.stringify({ type: 'result', ok: false, error: 'Invalid JSON' }));
      }
      const id = msg.id;
      const emit = (o: object) => ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify({ id, ...o }));
      try {
        const result = await handle(
          msg.type,
          msg,
          (line) => emit({ type: 'log', line }),
          (done, total) => emit({ type: 'progress', done, total }),
        );
        emit({ type: 'result', ok: true, ...result });
      } catch (e: any) {
        emit({ type: 'result', ok: false, error: e?.message ?? String(e) });
      }
    });
  });

  server.listen(PORT, HOST, () => {
    console.log(`Tittle bridge + web app on http://${HOST === '0.0.0.0' ? 'localhost' : HOST}:${PORT}`);
    console.log(`Relaying to cube TCP port ${CUBE_PORT}. LAN addresses here: ${localIPv4s().join(', ') || 'none'}`);
  });
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
