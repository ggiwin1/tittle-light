// Tittle Light 8x8x8 protocol helpers, shared by the browser (src/) and the bridge (server.ts).
// Source of truth: decompiled_src/support/utils/CommandUtils.java and
// decompiled_src/android/animation/AnimationFrameHolder.java.

export const SIZE = 8;
export const VOXELS = 512;
export const FRAME_BYTES = VOXELS * 3; // 1536
export const CRLF = [0x0d, 0x0a];
export const OP_PAINT = 0x90;
export const OP_CHUNK1 = 0x21;
export const OP_CHUNK2 = 0x22;
export const OP_CHUNK3 = 0x23;

/** index = z*64 + y*8 + x. x: left to right, y: front to back, z: bottom (0) to top (7). */
export const voxelIndex = (x: number, y: number, z: number) => z * 64 + y * 8 + x;

const clamp8 = (n: number) => Math.max(0, Math.min(255, Math.round(Number(n) || 0)));

export interface PaintOptions {
  /** Frame interval in ms. Sent as interval/8 in one byte, so 8..2040 ms. */
  intervalMs: number;
  /** 32-bit animation id. The cube caches by id, so a new id forces a fresh upload. */
  animId: number;
  intensity: number;
  sfOpt?: number;
  version?: number;
}

/** 0x90 Paint header, 18 bytes (CommandUtils.onCreatePaintOnPacket). */
export function buildPaintHeader(frameCount: number, o: PaintOptions): Uint8Array {
  const p = new Uint8Array(18);
  p[0] = OP_PAINT;
  p[1] = (frameCount >> 8) & 0xff;
  p[2] = frameCount & 0xff;
  p[3] = Math.max(1, Math.min(255, Math.floor(o.intervalMs / 8)));
  const id = o.animId >>> 0;
  p[4] = (id >>> 24) & 0xff;
  p[5] = (id >>> 16) & 0xff;
  p[6] = (id >>> 8) & 0xff;
  p[7] = id & 0xff;
  p[8] = clamp8(o.version ?? 1);
  p[9] = clamp8(o.intensity);
  p[10] = clamp8(o.sfOpt ?? 0);
  p[16] = 0x0d;
  p[17] = 0x0a;
  return p;
}

/** Splits one 1536-byte frame into the 515/515/521-byte chunks 0x21, 0x22, 0x23. */
export function buildFrameChunks(frame: Uint8Array, frameOrder: number): [Uint8Array, Uint8Array, Uint8Array] {
  const hi = (frameOrder >> 8) & 0xff;
  const lo = frameOrder & 0xff;
  const make = (code: number, from: number, to: number) => {
    const out = new Uint8Array(3 + (to - from) * 3 + 2);
    out[0] = code;
    out[1] = hi;
    out[2] = lo;
    for (let i = from * 3; i < to * 3; i++) out[3 + i - from * 3] = frame[i] ?? 0;
    out[out.length - 2] = 0x0d;
    out[out.length - 1] = 0x0a;
    return out;
  };
  return [make(OP_CHUNK1, 0, 170), make(OP_CHUNK2, 170, 340), make(OP_CHUNK3, 340, 512)];
}

export function concatBytes(parts: Uint8Array[]): Uint8Array {
  const len = parts.reduce((n, p) => n + p.length, 0);
  const out = new Uint8Array(len);
  let off = 0;
  for (const p of parts) {
    out.set(p, off);
    off += p.length;
  }
  return out;
}

/** Exactly the bytes that go over TCP 9999: header, then 3 chunks per frame. */
export function buildAnimationBinary(frames: Uint8Array[], o: PaintOptions): Uint8Array {
  const parts: Uint8Array[] = [buildPaintHeader(frames.length, o)];
  frames.forEach((f, i) => parts.push(...buildFrameChunks(f, i)));
  return concatBytes(parts);
}

/** Parses a .bin produced by buildAnimationBinary back into frames and options. */
export function parseAnimationBinary(buf: Uint8Array): { frames: Uint8Array[]; options: PaintOptions } {
  if (buf.length < 18 || (buf[0] !== OP_PAINT && buf[0] !== 0x50)) throw new Error('Not a Tittle animation binary (first byte must be 0x90 or 0x50)');
  const frameCount = (buf[1] << 8) | buf[2];
  const options: PaintOptions = {
    intervalMs: buf[3] * (buf[0] === 0x50 ? 10 : 8),
    animId: ((buf[4] << 24) | (buf[5] << 16) | (buf[6] << 8) | buf[7]) >>> 0,
    version: buf[8],
    intensity: buf[9],
    sfOpt: buf[10],
  };
  const frames: Uint8Array[] = [];
  let off = 18;
  const sizes = [515, 515, 521];
  const codes = [OP_CHUNK1, OP_CHUNK2, OP_CHUNK3];
  for (let f = 0; f < frameCount; f++) {
    const frame = new Uint8Array(FRAME_BYTES);
    let dst = 0;
    for (let c = 0; c < 3; c++) {
      if (off + sizes[c] > buf.length) throw new Error(`Binary is truncated in frame ${f + 1}`);
      if (buf[off] !== codes[c]) throw new Error(`Expected chunk 0x${codes[c].toString(16)} at byte ${off}`);
      const payload = buf.subarray(off + 3, off + sizes[c] - 2);
      frame.set(payload, dst);
      dst += payload.length;
      off += sizes[c];
    }
    frames.push(frame);
  }
  return { frames, options };
}

export type HandshakeKind = 'data_not_ready' | 'cached' | 'none' | 'unknown';

/** Classifies the cube's reply to a header. 00 00 20 00 01 01 = wants frames, 00 00 20 00 00 00 = cached. */
export function classifyHandshake(resp: Uint8Array | null | undefined): HandshakeKind {
  if (!resp || resp.length === 0) return 'none';
  if (resp.length >= 5 && resp[4] === 0x01) return 'data_not_ready';
  if (resp.length >= 6 && resp[4] === 0x00 && resp[5] === 0x00) return 'cached';
  return 'unknown';
}

/** FNV-1a over the content, so identical animations reuse the cube's cache and edited ones get a new id. */
export function contentAnimId(frames: Uint8Array[], intervalMs: number): number {
  let h = 0x811c9dc5;
  const mix = (b: number) => {
    h ^= b;
    h = Math.imul(h, 0x01000193);
  };
  mix(intervalMs & 0xff);
  mix((intervalMs >> 8) & 0xff);
  mix(frames.length & 0xff);
  for (const f of frames) for (let i = 0; i < f.length; i++) mix(f[i]);
  // Keep ids clear of the low range used by built-in moods/emoji.
  return ((h >>> 0) % 0x7fff0000) + 0x10000;
}

export const toHex = (bytes: ArrayLike<number>, sep = ' ') =>
  Array.from(bytes, (b) => (b & 0xff).toString(16).padStart(2, '0').toUpperCase()).join(sep);

export function fromHex(s: string): Uint8Array {
  const clean = s.replace(/0x/gi, '').replace(/[^0-9a-f]/gi, '');
  if (clean.length % 2) throw new Error('Hex string has an odd number of digits');
  const out = new Uint8Array(clean.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
  return out;
}

export function bytesToBase64(b: Uint8Array): string {
  let s = '';
  for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
  return btoa(s);
}

export function base64ToBytes(s: string): Uint8Array {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

// ---------------------------------------------------------------------------
// JSON animation format (.tittle.json), shared with tittle_desktop.py
// ---------------------------------------------------------------------------
export interface TittleAnimationJson {
  format: 'tittle-animation';
  version: 1;
  name: string;
  interval_ms: number;
  intensity: number;
  opcode: '0x90';
  voxel_index: 'z*64 + y*8 + x';
  encoding: 'hex';
  frames: string[]; // 3072 hex chars = 1536 RGB bytes each
}

/** Same shape tittle_desktop.py writes, so files move between the two apps. */
export function toAnimationJson(name: string, frames: Uint8Array[], intervalMs: number, intensity: number): TittleAnimationJson {
  return {
    format: 'tittle-animation',
    version: 1,
    name,
    interval_ms: intervalMs,
    intensity,
    opcode: '0x90',
    voxel_index: 'z*64 + y*8 + x',
    encoding: 'hex',
    frames: frames.map((f) => toHex(f, '')),
  };
}

function parseColor(v: any): [number, number, number] {
  if (typeof v.color === 'string') {
    const h = v.color.replace('#', '');
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }
  if (Array.isArray(v.rgb)) return [v.rgb[0], v.rgb[1], v.rgb[2]];
  return [v.r, v.g, v.b];
}

/**
 * Lenient import. Frames may be hex or base64 strings, flat 1536-int arrays, 512 [r,g,b] triples,
 * or sparse voxel lists ([x,y,z,r,g,b], {x,y,z,r,g,b}, {x,y,z,color:"#rrggbb"}, optionally {voxels:[...]}).
 */
export function fromAnimationJson(raw: any): { name?: string; frames: Uint8Array[]; intervalMs: number; intensity: number } {
  const list = Array.isArray(raw) ? raw : raw?.frames;
  if (!Array.isArray(list) || list.length === 0) throw new Error('JSON has no "frames" array');
  const encoding = typeof raw?.encoding === 'string' ? raw.encoding.toLowerCase() : '';
  const frames = list.map((fr: any, i: number) => {
    const out = new Uint8Array(FRAME_BYTES);
    if (typeof fr === 'string') {
      const compact = fr.replace(/\s/g, '');
      const isHex = encoding === 'hex' || (encoding !== 'base64' && /^[0-9a-f]+$/i.test(compact) && compact.length === FRAME_BYTES * 2);
      out.set((isHex ? fromHex(compact) : base64ToBytes(compact)).subarray(0, FRAME_BYTES));
      return out;
    }
    const voxels = Array.isArray(fr) ? fr : fr?.voxels;
    if (!Array.isArray(voxels)) throw new Error(`Frame ${i + 1} is not in a known shape`);
    if (voxels.length === 0) return out;
    if (typeof voxels[0] === 'number') {
      for (let k = 0; k < Math.min(voxels.length, FRAME_BYTES); k++) out[k] = clamp8(voxels[k]);
      return out;
    }
    if (Array.isArray(voxels[0]) && voxels[0].length === 3 && voxels.length === VOXELS) {
      voxels.forEach((t: number[], k: number) => {
        out[k * 3] = clamp8(t[0]);
        out[k * 3 + 1] = clamp8(t[1]);
        out[k * 3 + 2] = clamp8(t[2]);
      });
      return out;
    }
    for (const v of voxels) {
      const [x, y, z, ...rest] = Array.isArray(v) ? v : [v.x, v.y, v.z];
      if ([x, y, z].some((c) => !Number.isInteger(c) || c < 0 || c > 7)) continue;
      const rgb = Array.isArray(v) ? rest : parseColor(v);
      const idx = voxelIndex(x, y, z) * 3;
      out[idx] = clamp8(rgb[0]);
      out[idx + 1] = clamp8(rgb[1]);
      out[idx + 2] = clamp8(rgb[2]);
    }
    return out;
  });
  return {
    name: typeof raw?.name === 'string' ? raw.name : undefined,
    frames,
    intervalMs: Number(raw?.interval_ms ?? raw?.intervalMs) || 100,
    intensity: raw?.intensity != null ? clamp8(raw.intensity) : 255,
  };
}

// ---------------------------------------------------------------------------
// Firmware 2.5+ upload format (AnimationBinaryFrameHolder + CompressUtil)
// ---------------------------------------------------------------------------
export const OP_COMPRESSED = 0x25;
export const FRAME_SEPARATOR = [0xff, 0xff, 0xff, 0xff];
export type UploadFormat = 'auto' | 'compressed' | 'chunks';

/**
 * RLE encoder matching the app's CompressUtil (Marcus Geelnard's BCL RLE):
 * first byte is the marker (least used byte value). A run of n > 3 is [marker, n-1, symbol],
 * with n-1 >= 128 written as two bytes ((n-1)>>8 | 0x80, (n-1)&0xFF). 1 to 3 marker bytes are [marker, n-1].
 */
export function rleCompress(data: Uint8Array): Uint8Array {
  const counts = new Array(256).fill(0);
  for (const b of data) counts[b]++;
  const marker = counts.indexOf(Math.min(...counts));
  const out: number[] = [marker];
  let i = 0;
  while (i < data.length) {
    const sym = data[i];
    let run = 1;
    while (i + run < data.length && data[i + run] === sym && run < 32768) run++;
    if (run <= 3) {
      if (sym === marker) out.push(marker, run - 1);
      else for (let k = 0; k < run; k++) out.push(sym);
    } else {
      const c = run - 1;
      out.push(marker);
      if (c >= 128) out.push((c >> 8) | 0x80);
      out.push(c & 0xff, sym);
    }
    i += run;
  }
  return Uint8Array.from(out);
}

/** BCL RLE decoder. Stops after outLen bytes when given; returns the bytes and how much input it used. */
export function rleDecompress(input: Uint8Array, outLen = Infinity): { data: Uint8Array; used: number } {
  if (input.length === 0) return { data: new Uint8Array(0), used: 0 };
  const marker = input[0];
  const out: number[] = [];
  let p = 1;
  while (p < input.length && out.length < outLen) {
    const sym = input[p++];
    if (sym !== marker) {
      out.push(sym);
      continue;
    }
    let count = input[p++];
    if (count <= 2) {
      for (let k = 0; k <= count; k++) out.push(marker);
    } else {
      if (count & 0x80) count = ((count & 0x7f) << 8) + input[p++];
      const s = input[p++];
      for (let k = 0; k <= count; k++) out.push(s);
    }
  }
  return { data: Uint8Array.from(out), used: p };
}

/** Each frame RLE-compressed and followed by FF FF FF FF, as one stream. */
export function buildCompressedStream(frames: Uint8Array[]): Uint8Array {
  const parts: Uint8Array[] = [];
  for (const f of frames) parts.push(rleCompress(f), Uint8Array.from(FRAME_SEPARATOR));
  return concatBytes(parts);
}

/** 0x25 packets: [0x25, total(2), index(2), len(2), payload <= 512, checksum of the WHOLE stream, 0D 0A]. */
export function buildCompressedPackets(frames: Uint8Array[]): Uint8Array[] {
  const stream = buildCompressedStream(frames);
  const total = Math.ceil(stream.length / 512);
  let sum = 0;
  for (const b of stream) sum += b;
  const checksum = sum & 0xff;
  const packets: Uint8Array[] = [];
  for (let i = 0; i < total; i++) {
    const payload = stream.subarray(i * 512, (i + 1) * 512);
    const p = new Uint8Array(payload.length + 10);
    p.set([OP_COMPRESSED, total >> 8, total & 0xff, i >> 8, i & 0xff, payload.length >> 8, payload.length & 0xff]);
    p.set(payload, 7);
    p.set([checksum, 0x0d, 0x0a], p.length - 3);
    packets.push(p);
  }
  return packets;
}

/** Decodes a reassembled 0x25 stream back into frames (used by the fake cube and for checks). */
export function parseCompressedStream(stream: Uint8Array): Uint8Array[] {
  const frames: Uint8Array[] = [];
  let off = 0;
  while (off < stream.length) {
    const { data, used } = rleDecompress(stream.subarray(off), FRAME_BYTES);
    if (data.length !== FRAME_BYTES) throw new Error(`Frame ${frames.length + 1} decoded to ${data.length} bytes`);
    off += used;
    if (FRAME_SEPARATOR.some((b, k) => stream[off + k] !== b)) throw new Error(`Missing FF FF FF FF after frame ${frames.length + 1}`);
    off += 4;
    frames.push(data);
  }
  return frames;
}

/** Firmware version from a 0x60 status reply: (reply[1], reply[2]), as ResponseBroadcastReceiver reads it. */
export function parseFirmwareVersion(resp: Uint8Array | null | undefined): [number, number] | null {
  if (!resp || resp.length < 3 || resp[0] !== 0x60) return null;
  return [resp[1], resp[2]];
}

/** The app's rule (AnimationHolder.getInstance): raw chunks only when the version is known and below 2.5. */
export function pickUploadFormat(version: [number, number] | null): 'compressed' | 'chunks' {
  if (!version) return 'compressed';
  const [hi, lo] = version;
  return hi < 2 || (hi === 2 && lo < 5) ? 'chunks' : 'compressed';
}

// ---------------------------------------------------------------------------
// Easter egg: a spinning pepperoni pizza slice
// ---------------------------------------------------------------------------
export function pizzaFrames(count = 16): Uint8Array[] {
  const pepperoni = [
    [0, 3.2],
    [-1.4, 5.0],
    [1.3, 5.2],
  ];
  const frames: Uint8Array[] = [];
  for (let f = 0; f < count; f++) {
    const a = (f / count) * Math.PI * 2;
    const c = Math.cos(a), s = Math.sin(a);
    const fr = new Uint8Array(FRAME_BYTES);
    for (let z = 0; z < SIZE; z++)
      for (let y = 0; y < SIZE; y++)
        for (let x = 0; x < SIZE; x++) {
          const dx = x - 3.5, dy = y - 3.5;
          const u = dx * c + dy * s; // across the slice
          const w = -dx * s + dy * c; // thickness
          const v = z + 0.3; // tip at the bottom, crust at the top
          if (Math.abs(w) > 0.75 || Math.abs(u) > 0.4 + v * 0.48) continue;
          let rgb = [255, 170, 0]; // cheese
          if (z === 7) rgb = [150, 60, 5]; // crust
          else if (pepperoni.some(([pu, pv]) => Math.hypot(u - pu, v - pv) < 0.95)) rgb = [220, 0, 0];
          fr.set(rgb, voxelIndex(x, y, z) * 3);
        }
    frames.push(fr);
  }
  return frames;
}
