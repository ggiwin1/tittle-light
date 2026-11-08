import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  ChevronsDown,
  ChevronsUp,
  Copy,
  Download,
  Eraser,
  FilePlus2,
  Layers,
  PaintBucket,
  Paintbrush,
  Pause,
  Pipette,
  Play,
  Redo2,
  RotateCw,
  Send,
  Trash2,
  Undo2,
  Upload,
} from 'lucide-react';
import {
  FRAME_BYTES,
  SIZE,
  buildAnimationBinary,
  buildPaintHeader,
  bytesToBase64,
  contentAnimId,
  fromDevice,
  toDevice,
  type Orientation,
  fromAnimationJson,
  parseAnimationBinary,
  toAnimationJson,
  toHex,
  voxelIndex,
} from './tittleProtocol';
import { bridgeRequest, getOrientation, saveOrientation } from './cubeBridge';

type Frames = Uint8Array[];
type Tool = 'paint' | 'erase' | 'pick' | 'fill';

const PALETTE = [
  '#ffffff', '#ff0000', '#ff6a00', '#ffd000', '#7dff00', '#00ff40', '#00ffd0', '#00a2ff',
  '#0030ff', '#7a00ff', '#ff00d4', '#ff3366', '#ffb070', '#80ffea', '#3a3a3a', '#000000',
];
const AUTOSAVE_KEY = 'tittle.voxelEditor.v1';
const MAX_FRAMES = 256;
const MAX_HISTORY = 60;

const hexToRgb = (h: string): [number, number, number] => {
  const n = parseInt(h.replace('#', ''), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};
const rgbToHex = (r: number, g: number, b: number) => '#' + [r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('');
const blank = () => new Uint8Array(FRAME_BYTES);
const getRgb = (f: Uint8Array, x: number, y: number, z: number): [number, number, number] => {
  const i = voxelIndex(x, y, z) * 3;
  return [f[i], f[i + 1], f[i + 2]];
};
const isLit = (f: Uint8Array, x: number, y: number, z: number) => {
  const i = voxelIndex(x, y, z) * 3;
  return f[i] + f[i + 1] + f[i + 2] > 0;
};

function loadAutosave(): { frames: Frames; intervalMs: number; intensity: number; name: string } | null {
  try {
    const raw = localStorage.getItem(AUTOSAVE_KEY);
    if (!raw) return null;
    const parsed = fromAnimationJson(JSON.parse(raw));
    return { frames: parsed.frames, intervalMs: parsed.intervalMs, intensity: parsed.intensity, name: parsed.name ?? 'my-animation' };
  } catch {
    return null;
  }
}

function download(name: string, data: BlobPart, type: string) {
  const url = URL.createObjectURL(new Blob([data], { type }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---------------------------------------------------------------------------
// Frame transforms
// ---------------------------------------------------------------------------
function transformFrame(f: Uint8Array, map: (x: number, y: number, z: number) => [number, number, number]): Uint8Array {
  const out = blank();
  for (let z = 0; z < SIZE; z++)
    for (let y = 0; y < SIZE; y++)
      for (let x = 0; x < SIZE; x++) {
        const [nx, ny, nz] = map(x, y, z);
        const s = voxelIndex(x, y, z) * 3;
        const d = voxelIndex(nx, ny, nz) * 3;
        out[d] = f[s];
        out[d + 1] = f[s + 1];
        out[d + 2] = f[s + 2];
      }
  return out;
}
const wrap = (v: number) => (v + SIZE) % SIZE;
const shift = (f: Uint8Array, dx: number, dy: number, dz: number) => transformFrame(f, (x, y, z) => [wrap(x + dx), wrap(y + dy), wrap(z + dz)]);
const rotateZ = (f: Uint8Array) => transformFrame(f, (x, y, z) => [y, 7 - x, z]);

// ---------------------------------------------------------------------------
// 3D preview
// ---------------------------------------------------------------------------
function Cube3D({ frame, layer, showOthers }: { frame: Uint8Array; layer: number; showOthers: boolean }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const [view, setView] = useState({ yaw: -0.55, pitch: 0.5 });
  const drag = useRef<{ x: number; y: number; yaw: number; pitch: number; moved: boolean } | null>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const W = width || canvas.clientWidth;
    if (!W) return;
    canvas.width = W * dpr;
    canvas.height = W * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = '#020617';
    ctx.fillRect(0, 0, W, W);

    const cy = Math.cos(view.yaw), sy = Math.sin(view.yaw), cp = Math.cos(view.pitch), sp = Math.sin(view.pitch);
    const scale = W / 13;
    const project = (x: number, y: number, z: number) => {
      const dx = x - 3.5, dy = y - 3.5, dz = z - 3.5;
      const rx = dx * cy - dy * sy;
      const ry = dx * sy + dy * cy;
      const up = dz * cp + ry * sp;
      const depth = ry * cp - dz * sp;
      const persp = 22 / (22 + depth);
      return { sx: W / 2 + rx * scale * persp, sy: W / 2 - up * scale * persp, depth, persp };
    };

    // Active layer plane
    const corners = [[-0.5, -0.5], [7.5, -0.5], [7.5, 7.5], [-0.5, 7.5]].map(([x, y]) => project(x, y, layer));
    ctx.beginPath();
    corners.forEach((c, i) => (i ? ctx.lineTo(c.sx, c.sy) : ctx.moveTo(c.sx, c.sy)));
    ctx.closePath();
    ctx.fillStyle = 'rgba(245, 158, 11, 0.07)';
    ctx.fill();
    ctx.strokeStyle = 'rgba(245, 158, 11, 0.55)';
    ctx.lineWidth = 1.5;
    ctx.stroke();
    // "Front" marker along y = -0.5
    const f0 = project(3.5, -1.3, layer);
    ctx.fillStyle = 'rgba(148, 163, 184, 0.8)';
    ctx.font = '11px ui-sans-serif, system-ui';
    ctx.textAlign = 'center';
    ctx.fillText('front', f0.sx, f0.sy);

    const pts: { sx: number; sy: number; depth: number; r: number; color: string; lit: boolean; active: boolean }[] = [];
    for (let z = 0; z < SIZE; z++)
      for (let y = 0; y < SIZE; y++)
        for (let x = 0; x < SIZE; x++) {
          const active = z === layer;
          if (!showOthers && !active) continue;
          const p = project(x, y, z);
          const [r, g, b] = getRgb(frame, x, y, z);
          const lit = r + g + b > 0;
          const alpha = active || !showOthers ? 1 : 0.8;
          pts.push({
            sx: p.sx,
            sy: p.sy,
            depth: p.depth,
            r: (lit ? 0.27 : 0.08) * scale * p.persp,
            color: lit ? `rgba(${r},${g},${b},${alpha})` : active ? 'rgba(251, 191, 36, 0.45)' : 'rgba(100, 116, 139, 0.35)',
            lit,
            active,
          });
        }
    pts.sort((a, b) => b.depth - a.depth);
    for (const p of pts) {
      if (p.lit) {
        ctx.beginPath();
        ctx.arc(p.sx, p.sy, p.r * 1.8, 0, Math.PI * 2);
        ctx.fillStyle = p.color.replace(/[\d.]+\)$/, '0.16)');
        ctx.fill();
      }
      ctx.beginPath();
      ctx.arc(p.sx, p.sy, p.r, 0, Math.PI * 2);
      ctx.fillStyle = p.color;
      ctx.fill();
    }
  }, [frame, layer, showOthers, view, width]);

  return (
    <canvas
      ref={ref}
      className="w-full aspect-square rounded-lg cursor-grab active:cursor-grabbing touch-none"
      onPointerDown={(e) => {
        (e.target as Element).setPointerCapture(e.pointerId);
        drag.current = { x: e.clientX, y: e.clientY, yaw: view.yaw, pitch: view.pitch, moved: false };
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (!d) return;
        d.moved = true;
        setView({ yaw: d.yaw - (e.clientX - d.x) * 0.01, pitch: Math.max(-1.4, Math.min(1.4, d.pitch + (e.clientY - d.y) * 0.01)) });
      }}
      onPointerUp={() => (drag.current = null)}
      onDoubleClick={() => setView({ yaw: -0.55, pitch: 0.5 })}
      title="Drag to rotate, double-click to reset."
    />
  );
}

// Top-down thumbnail: for each column, the highest lit voxel's colour.
const FrameThumb = memo(function FrameThumb({ frame }: { frame: Uint8Array }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    const ctx = ref.current?.getContext('2d');
    if (!ctx) return;
    ctx.fillStyle = '#020617';
    ctx.fillRect(0, 0, 40, 40);
    for (let y = 0; y < SIZE; y++)
      for (let x = 0; x < SIZE; x++)
        for (let z = 7; z >= 0; z--) {
          if (!isLit(frame, x, y, z)) continue;
          const [r, g, b] = getRgb(frame, x, y, z);
          ctx.fillStyle = `rgb(${r},${g},${b})`;
          ctx.fillRect(x * 5, (7 - y) * 5, 5, 5);
          break;
        }
  }, [frame]);
  return <canvas ref={ref} width={40} height={40} className="w-10 h-10 rounded [image-rendering:pixelated]" />;
});

// ---------------------------------------------------------------------------
// Editor
// ---------------------------------------------------------------------------
export default function VoxelEditor({
  targetIp,
  setTargetIp,
  bridgeOnline,
  active = true,
}: {
  targetIp: string;
  setTargetIp: (ip: string) => void;
  bridgeOnline: boolean;
  active?: boolean;
}) {
  const saved = useMemo(loadAutosave, []);
  const [frames, setFrames] = useState<Frames>(() => saved?.frames ?? [blank()]);
  const [cur, setCur] = useState(0);
  const [layer, setLayer] = useState(0);
  const [color, setColor] = useState('#ff3366');
  const [tool, setTool] = useState<Tool>('paint');
  const [mirrorX, setMirrorX] = useState(false);
  const [mirrorY, setMirrorY] = useState(false);
  const [onion, setOnion] = useState(true);
  const [showOthers, setShowOthers] = useState(true);
  const [intervalMs, setIntervalMs] = useState(saved?.intervalMs ?? 120);
  const [intensity, setIntensity] = useState(saved?.intensity ?? 255);
  const [name, setName] = useState(saved?.name ?? 'my-animation');
  const [playing, setPlaying] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploadFormat, setUploadFormat] = useState<'auto' | 'compressed' | 'chunks'>('auto');
  const [orientation, setOrientationState] = useState<Orientation>(getOrientation);
  const setOrientation = (o: Orientation) => {
    setOrientationState(o);
    saveOrientation(o);
  };
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [message, setMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);
  const undoStack = useRef<Frames[]>([]);
  const redoStack = useRef<Frames[]>([]);
  const [, bump] = useState(0);
  const painting = useRef<{ erase: boolean } | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const frame = frames[Math.min(cur, frames.length - 1)];
  const prevFrame = cur > 0 ? frames[cur - 1] : frames.length > 1 ? frames[frames.length - 1] : null;

  const checkpoint = useCallback(() => {
    undoStack.current.push(frames);
    if (undoStack.current.length > MAX_HISTORY) undoStack.current.shift();
    redoStack.current = [];
    bump((n) => n + 1);
  }, [frames]);

  const commit = (next: Frames, nextCur = cur) => {
    checkpoint();
    setFrames(next);
    setCur(Math.max(0, Math.min(nextCur, next.length - 1)));
  };

  const updateFrame = (fn: (f: Uint8Array) => Uint8Array) => commit(frames.map((f, i) => (i === cur ? fn(f) : f)));

  const undo = () => {
    const prev = undoStack.current.pop();
    if (!prev) return;
    redoStack.current.push(frames);
    setFrames(prev);
    setCur((c) => Math.min(c, prev.length - 1));
    bump((n) => n + 1);
  };
  const redo = () => {
    const next = redoStack.current.pop();
    if (!next) return;
    undoStack.current.push(frames);
    setFrames(next);
    setCur((c) => Math.min(c, next.length - 1));
    bump((n) => n + 1);
  };

  // Autosave (per browser only). Debounced, and flushed when the page is hidden or the editor unmounts.
  const latest = useRef({ name, frames, intervalMs, intensity });
  latest.current = { name, frames, intervalMs, intensity };
  const saveNow = useCallback(() => {
    try {
      const l = latest.current;
      localStorage.setItem(AUTOSAVE_KEY, JSON.stringify(toAnimationJson(l.name, l.frames, l.intervalMs, l.intensity)));
    } catch {
      /* storage full or blocked */
    }
  }, []);
  useEffect(() => {
    const t = setTimeout(saveNow, 400);
    return () => clearTimeout(t);
  }, [frames, intervalMs, intensity, name, saveNow]);
  useEffect(() => {
    window.addEventListener('pagehide', saveNow);
    return () => {
      window.removeEventListener('pagehide', saveNow);
      saveNow();
    };
  }, [saveNow]);

  // Playback
  useEffect(() => {
    if (!playing || frames.length < 2) return;
    const t = setInterval(() => setCur((c) => (c + 1) % frames.length), Math.max(8, Math.floor(intervalMs / 8) * 8));
    return () => clearInterval(t);
  }, [playing, frames.length, intervalMs]);

  // ------------------------------------------------------------------ painting
  const applyAt = (x: number, y: number, erase: boolean) => {
    const rgb: [number, number, number] = erase ? [0, 0, 0] : hexToRgb(color);
    const targets = new Set<string>([`${x},${y}`]);
    if (mirrorX) targets.add(`${7 - x},${y}`);
    if (mirrorY) targets.add(`${x},${7 - y}`);
    if (mirrorX && mirrorY) targets.add(`${7 - x},${7 - y}`);
    setFrames((fs) =>
      fs.map((f, i) => {
        if (i !== cur) return f;
        const nf = new Uint8Array(f);
        targets.forEach((k) => {
          const [tx, ty] = k.split(',').map(Number);
          nf.set(rgb, voxelIndex(tx, ty, layer) * 3);
        });
        return nf;
      }),
    );
  };

  const floodFill = (sx: number, sy: number) => {
    const target = getRgb(frame, sx, sy, layer).join(',');
    const rgb = hexToRgb(color);
    if (target === rgb.join(',')) return;
    const nf = new Uint8Array(frame);
    const stack = [[sx, sy]];
    const seen = new Set<number>();
    while (stack.length) {
      const [x, y] = stack.pop()!;
      if (x < 0 || y < 0 || x > 7 || y > 7 || seen.has(y * 8 + x)) continue;
      seen.add(y * 8 + x);
      if (getRgb(frame, x, y, layer).join(',') !== target) continue;
      nf.set(rgb, voxelIndex(x, y, layer) * 3);
      stack.push([x + 1, y], [x - 1, y], [x, y + 1], [x, y - 1]);
    }
    updateFrame(() => nf);
  };

  const cellFromEvent = (e: React.PointerEvent) => {
    const el = document.elementFromPoint(e.clientX, e.clientY) as HTMLElement | null;
    const cell = el?.closest('[data-cell]') as HTMLElement | null;
    if (!cell) return null;
    return { x: Number(cell.dataset.x), y: Number(cell.dataset.y) };
  };

  const onGridDown = (e: React.PointerEvent) => {
    const c = cellFromEvent(e);
    if (!c) return;
    e.preventDefault();
    if (tool === 'pick') {
      const [r, g, b] = getRgb(frame, c.x, c.y, layer);
      setColor(rgbToHex(r, g, b));
      setTool('paint');
      return;
    }
    if (tool === 'fill' && e.button !== 2) return floodFill(c.x, c.y);
    const erase = tool === 'erase' || e.button === 2;
    checkpoint();
    painting.current = { erase };
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    applyAt(c.x, c.y, erase);
  };
  const onGridMove = (e: React.PointerEvent) => {
    if (!painting.current) return;
    const c = cellFromEvent(e);
    if (c) applyAt(c.x, c.y, painting.current.erase);
  };
  const endPaint = () => (painting.current = null);

  // ------------------------------------------------------------------ layer ops
  const layerOp = (op: 'clear' | 'fill' | 'up' | 'down' | 'all') => {
    updateFrame((f) => {
      const nf = new Uint8Array(f);
      const src = f.slice(layer * 192, layer * 192 + 192);
      if (op === 'clear') nf.fill(0, layer * 192, layer * 192 + 192);
      if (op === 'fill') for (let i = 0; i < 64; i++) nf.set(hexToRgb(color), layer * 192 + i * 3);
      if (op === 'up' && layer < 7) nf.set(src, (layer + 1) * 192);
      if (op === 'down' && layer > 0) nf.set(src, (layer - 1) * 192);
      if (op === 'all') for (let z = 0; z < 8; z++) nf.set(src, z * 192);
      return nf;
    });
    if (op === 'up' && layer < 7) setLayer(layer + 1);
    if (op === 'down' && layer > 0) setLayer(layer - 1);
  };

  // ------------------------------------------------------------------ frame ops
  const addFrame = (dup: boolean) => {
    if (frames.length >= MAX_FRAMES) return;
    const nf = dup ? new Uint8Array(frame) : blank();
    commit([...frames.slice(0, cur + 1), nf, ...frames.slice(cur + 1)], cur + 1);
  };
  const deleteFrame = () => {
    if (frames.length === 1) return commit([blank()], 0);
    commit(frames.filter((_, i) => i !== cur), cur > 0 ? cur - 1 : 0);
  };
  const moveFrame = (dir: -1 | 1) => {
    const to = cur + dir;
    if (to < 0 || to >= frames.length) return;
    const next = [...frames];
    [next[cur], next[to]] = [next[to], next[cur]];
    commit(next, to);
  };
  const shiftDupe = (dx: number, dy: number, dz: number) => {
    if (frames.length >= MAX_FRAMES) return;
    commit([...frames.slice(0, cur + 1), shift(frame, dx, dy, dz), ...frames.slice(cur + 1)], cur + 1);
  };

  // ------------------------------------------------------------------ keyboard
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'SELECT' || t.tagName === 'TEXTAREA')) return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        e.shiftKey ? redo() : undo();
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') {
        e.preventDefault();
        redo();
      } else if (e.key === ']' || e.key === 'PageUp') setLayer((l) => Math.min(7, l + 1));
      else if (e.key === '[' || e.key === 'PageDown') setLayer((l) => Math.max(0, l - 1));
      else if (e.key === '.') setCur((c) => Math.min(frames.length - 1, c + 1));
      else if (e.key === ',') setCur((c) => Math.max(0, c - 1));
      else if (e.key === 'b') setTool('paint');
      else if (e.key === 'e') setTool('erase');
      else if (e.key === 'i') setTool('pick');
      else if (e.key === 'g') setTool('fill');
      else if (e.key >= '0' && e.key <= '7' && !e.ctrlKey && !e.metaKey) setLayer(Number(e.key));
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  // ------------------------------------------------------------------ import / export / send
  // The cube's own byte order differs from the editor's z-up order; ids and .bin files use the cube's.
  const deviceFrames = useMemo(() => frames.map((f) => toDevice(f, orientation)), [frames, orientation]);
  const animId = useMemo(() => contentAnimId(deviceFrames, intervalMs), [deviceFrames, intervalMs]);
  const header = buildPaintHeader(frames.length, { intervalMs, animId, intensity });
  const totalBytes = 18 + frames.length * (515 + 515 + 521);
  const litCount = useMemo(() => {
    let n = 0;
    for (let i = 0; i < 512; i++) if (frame[i * 3] + frame[i * 3 + 1] + frame[i * 3 + 2] > 0) n++;
    return n;
  }, [frame]);
  const safeName = (name.trim() || 'animation').replace(/[^\w.-]+/g, '_');

  const exportBin = () => download(`${safeName}.tittle.bin`, buildAnimationBinary(deviceFrames, { intervalMs, animId, intensity }).buffer as ArrayBuffer, "application/octet-stream");
  const exportJson = () => download(`${safeName}.tittle.json`, JSON.stringify(toAnimationJson(name, frames, intervalMs, intensity), null, 1), 'application/json');

  const onImport = async (file: File) => {
    try {
      const buf = new Uint8Array(await file.arrayBuffer());
      let res: { frames: Frames; intervalMs: number; intensity: number; name?: string };
      if (buf[0] === 0x90 || buf[0] === 0x50) {
        const p = parseAnimationBinary(buf);
        res = { frames: p.frames.map((f) => fromDevice(f, orientation)), intervalMs: p.options.intervalMs, intensity: p.options.intensity };
      } else {
        res = fromAnimationJson(JSON.parse(new TextDecoder().decode(buf)));
      }
      if (res.frames.length > MAX_FRAMES) res.frames = res.frames.slice(0, MAX_FRAMES);
      commit(res.frames, 0);
      setIntervalMs(Math.max(8, Math.min(2040, res.intervalMs)));
      setIntensity(res.intensity);
      setName(res.name ?? file.name.replace(/\.tittle\.(json|bin)$|\.(json|bin)$/i, ''));
      setMessage({ kind: 'ok', text: `Imported ${res.frames.length} frame(s) from ${file.name}` });
    } catch (e: any) {
      setMessage({ kind: 'err', text: `Import failed: ${e?.message ?? e}` });
    }
  };

  const sendToCube = async (only?: Uint8Array) => {
    const list = only ? [only] : frames;
    setBusy(true);
    setLog([]);
    setMessage(null);
    setProgress({ done: 0, total: list.length });
    const res = await bridgeRequest(
      'animation',
      { ip: targetIp, frames: list.map(bytesToBase64), intervalMs, intensity, format: uploadFormat, orientation },
      { onLog: (l) => setLog((ls) => [...ls, l]), onProgress: (done, total) => setProgress({ done, total }) },
    );
    setBusy(false);
    if (res.ok) setMessage({ kind: 'ok', text: res.uploaded ? `Uploaded ${list.length} frame(s) (${res.format === 'compressed' ? 'compressed 0x25' : 'raw chunks'}). The cube should be looping it now.` : 'The cube already had this animation cached and is playing it.' });
    else setMessage({ kind: 'err', text: res.error ?? 'Send failed' });
  };

  // ------------------------------------------------------------------ render helpers
  const ToolBtn = ({ id, icon: Icon, label, keyHint }: { id: Tool; icon: any; label: string; keyHint: string }) => (
    <button
      onClick={() => setTool(id)}
      title={`${label} (${keyHint})`}
      className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs border transition ${
        tool === id ? 'bg-amber-500/20 border-amber-500/40 text-amber-300' : 'bg-slate-950 border-slate-800 text-slate-400 hover:text-slate-200'
      }`}
    >
      <Icon className="w-3.5 h-3.5" /> {label}
    </button>
  );
  const SmallBtn = ({ onClick, children, title, disabled }: { onClick: () => void; children: React.ReactNode; title?: string; disabled?: boolean }) => (
    <button
      onClick={onClick}
      title={title}
      disabled={disabled}
      className="flex items-center justify-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs bg-slate-950 border border-slate-800 text-slate-300 hover:bg-slate-800 disabled:opacity-40 disabled:cursor-not-allowed"
    >
      {children}
    </button>
  );
  const Toggle = ({ on, set, label }: { on: boolean; set: (v: boolean) => void; label: string }) => (
    <label className="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer select-none">
      <input type="checkbox" checked={on} onChange={(e) => set(e.target.checked)} className="accent-amber-500" /> {label}
    </label>
  );

  return (
    <div className="space-y-4">
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex flex-col lg:flex-row lg:items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-white flex items-center gap-2">
            <Paintbrush className="w-5 h-5 text-amber-400" /> 3D Voxel Editor
          </h2>
          <p className="text-xs text-slate-400">
            Paint one Z layer at a time, build frames, then export the Tittle 0x90 binary or upload it to the cube through the local bridge.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 lg:shrink-0">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="bg-slate-950 border border-slate-700 px-2.5 py-1.5 rounded-lg text-xs text-slate-200 w-40"
            placeholder="Animation name"
          />
          <SmallBtn onClick={undo} title="Undo (Ctrl+Z)" disabled={!undoStack.current.length}>
            <Undo2 className="w-3.5 h-3.5" />
          </SmallBtn>
          <SmallBtn onClick={redo} title="Redo (Ctrl+Y)" disabled={!redoStack.current.length}>
            <Redo2 className="w-3.5 h-3.5" />
          </SmallBtn>
          <SmallBtn onClick={() => fileRef.current?.click()} title="Import .tittle.json or .tittle.bin">
            <Upload className="w-3.5 h-3.5" /> Import
          </SmallBtn>
          <input
            ref={fileRef}
            type="file"
            accept=".json,.bin,application/json,application/octet-stream"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) onImport(f);
              e.target.value = '';
            }}
          />
          <SmallBtn onClick={exportJson} title="Project file, also opens in tittle_desktop.py">
            <Download className="w-3.5 h-3.5" /> JSON
          </SmallBtn>
          <SmallBtn onClick={exportBin} title="Exact bytes sent to TCP 9999: header + 0x21/0x22/0x23 chunks">
            <Download className="w-3.5 h-3.5" /> Tittle .bin
          </SmallBtn>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        {/* 3D view */}
        <div className="lg:col-span-4 bg-slate-900 border border-slate-800 rounded-xl p-3 space-y-2">
          <div className="flex items-center justify-between text-xs">
            <span className="text-slate-300 font-medium">3D preview</span>
            <span className="text-slate-500">drag to rotate · double-click resets</span>
          </div>
          <Cube3D frame={frame} layer={layer} showOthers={showOthers} />
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Toggle on={showOthers} set={setShowOthers} label="Show other layers" />
            <span className="text-[11px] text-slate-500 font-mono">{litCount}/512 lit</span>
          </div>
        </div>

        {/* Slice editor */}
        <div className="lg:col-span-5 bg-slate-900 border border-slate-800 rounded-xl p-3 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-300 font-medium flex items-center gap-1.5">
              <Layers className="w-4 h-4 text-amber-400" /> Layer Z = {layer} {layer === 0 ? '(bottom)' : layer === 7 ? '(top)' : ''}
            </span>
            <span className="text-[11px] text-slate-500">Frame {cur + 1} / {frames.length}</span>
          </div>
          <div className="flex gap-3">
            {/* Z slider column, top layer at top */}
            <div className="flex flex-col gap-1">
              {[7, 6, 5, 4, 3, 2, 1, 0].map((z) => {
                let n = 0;
                for (let i = 0; i < 64; i++) if (frame[z * 192 + i * 3] + frame[z * 192 + i * 3 + 1] + frame[z * 192 + i * 3 + 2]) n++;
                return (
                  <button
                    key={z}
                    onClick={() => setLayer(z)}
                    className={`w-9 flex-1 rounded-md text-[11px] font-mono border relative ${
                      z === layer ? 'bg-amber-500/25 border-amber-500/50 text-amber-200' : 'bg-slate-950 border-slate-800 text-slate-400 hover:text-slate-200'
                    }`}
                    title={`Layer ${z}: ${n} lit`}
                  >
                    Z{z}
                    {n > 0 && <span className="absolute top-0.5 right-0.5 w-1.5 h-1.5 rounded-full bg-emerald-400" />}
                  </button>
                );
              })}
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-center text-[10px] uppercase tracking-wider text-slate-500 mb-1">back (y=7)</div>
              <div
                className="grid grid-cols-8 gap-1 select-none touch-none aspect-square"
                onPointerDown={onGridDown}
                onPointerMove={onGridMove}
                onPointerUp={endPaint}
                onPointerCancel={endPaint}
                onContextMenu={(e) => e.preventDefault()}
              >
                {Array.from({ length: 64 }, (_, k) => {
                  const x = k % 8;
                  const y = 7 - Math.floor(k / 8);
                  const [r, g, b] = getRgb(frame, x, y, layer);
                  const lit = r + g + b > 0;
                  const ghost = onion && prevFrame && !lit ? getRgb(prevFrame, x, y, layer) : null;
                  const ghostLit = ghost && ghost[0] + ghost[1] + ghost[2] > 0;
                  const below = !lit && layer > 0 && isLit(frame, x, y, layer - 1);
                  return (
                    <div
                      key={k}
                      data-cell
                      data-x={x}
                      data-y={y}
                      className={`rounded-md border cursor-crosshair ${lit ? 'border-white/20' : 'border-slate-800 hover:border-slate-600'}`}
                      style={{
                        background: lit
                          ? `rgb(${r},${g},${b})`
                          : ghostLit
                          ? `rgba(${ghost![0]},${ghost![1]},${ghost![2]},0.22)`
                          : below
                          ? 'rgba(148,163,184,0.10)'
                          : '#020617',
                        boxShadow: lit ? `0 0 10px rgba(${r},${g},${b},0.55)` : undefined,
                      }}
                      title={`x${x} y${y} z${layer}`}
                    />
                  );
                })}
              </div>
              <div className="flex justify-between text-[10px] uppercase tracking-wider text-slate-500 mt-1">
                <span>x=0</span>
                <span>front (y=0)</span>
                <span>x=7</span>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap gap-1.5">
            <ToolBtn id="paint" icon={Paintbrush} label="Paint" keyHint="B" />
            <ToolBtn id="erase" icon={Eraser} label="Erase" keyHint="E, or right-click" />
            <ToolBtn id="fill" icon={PaintBucket} label="Fill" keyHint="G" />
            <ToolBtn id="pick" icon={Pipette} label="Pick" keyHint="I" />
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
            <Toggle on={mirrorX} set={setMirrorX} label="Mirror left/right" />
            <Toggle on={mirrorY} set={setMirrorY} label="Mirror front/back" />
            <Toggle on={onion} set={setOnion} label="Onion skin (previous frame)" />
          </div>
          <div className="grid grid-cols-3 sm:grid-cols-5 gap-1.5">
            <SmallBtn onClick={() => layerOp('clear')} title="Clear this layer">
              <Trash2 className="w-3.5 h-3.5" /> Clear
            </SmallBtn>
            <SmallBtn onClick={() => layerOp('fill')} title="Fill this layer with the current colour">
              <PaintBucket className="w-3.5 h-3.5" /> Fill all
            </SmallBtn>
            <SmallBtn onClick={() => layerOp('up')} title="Copy this layer to the one above" disabled={layer === 7}>
              <ChevronsUp className="w-3.5 h-3.5" /> Copy up
            </SmallBtn>
            <SmallBtn onClick={() => layerOp('down')} title="Copy this layer to the one below" disabled={layer === 0}>
              <ChevronsDown className="w-3.5 h-3.5" /> Copy down
            </SmallBtn>
            <SmallBtn onClick={() => layerOp('all')} title="Copy this layer to every layer (extrude)">
              <Layers className="w-3.5 h-3.5" /> Extrude
            </SmallBtn>
          </div>
        </div>

        {/* Colour + output */}
        <div className="lg:col-span-3 space-y-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-3 space-y-3">
            <div className="flex items-center gap-2">
              <input type="color" value={color} onChange={(e) => setColor(e.target.value)} className="w-10 h-10 rounded cursor-pointer bg-transparent" />
              <div className="font-mono text-xs text-slate-300">
                {color.toUpperCase()}
                <div className="text-slate-500">rgb({hexToRgb(color).join(', ')})</div>
              </div>
            </div>
            <div className="grid grid-cols-8 gap-1">
              {PALETTE.map((c) => (
                <button
                  key={c}
                  onClick={() => {
                    setColor(c);
                    if (tool === 'erase' || tool === 'pick') setTool('paint');
                  }}
                  className={`aspect-square rounded border ${color.toLowerCase() === c ? 'border-amber-400 ring-1 ring-amber-400' : 'border-slate-700'}`}
                  style={{ background: c }}
                  title={c}
                />
              ))}
            </div>
            <p className="text-[11px] text-slate-500">Black (#000000) means off, the same as erasing.</p>
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded-xl p-3 space-y-3">
            <div>
              <div className="flex justify-between text-xs text-slate-300 mb-1">
                <span>Frame interval</span>
                <span className="font-mono">
                  {Math.floor(intervalMs / 8) * 8} ms · {(1000 / (Math.floor(intervalMs / 8) * 8)).toFixed(1)} fps
                </span>
              </div>
              <input type="range" min={8} max={2040} step={8} value={intervalMs} onChange={(e) => setIntervalMs(Number(e.target.value))} className="w-full accent-amber-500" />
            </div>
            <div>
              <div className="flex justify-between text-xs text-slate-300 mb-1">
                <span>Intensity</span>
                <span className="font-mono">{intensity}</span>
              </div>
              <input type="range" min={0} max={255} value={intensity} onChange={(e) => setIntensity(Number(e.target.value))} className="w-full accent-amber-500" />
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-400">Cube IP</span>
              <input
                value={targetIp}
                onChange={(e) => setTargetIp(e.target.value)}
                className="flex-1 min-w-0 bg-slate-950 border border-slate-700 px-2.5 py-1.5 rounded-lg text-xs font-mono text-amber-300"
              />
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-400">Format</span>
              <select
                value={uploadFormat}
                onChange={(e) => setUploadFormat(e.target.value as any)}
                className="flex-1 min-w-0 bg-slate-950 border border-slate-700 px-2 py-1.5 rounded-lg text-xs text-slate-200"
                title="Firmware 2.5 and newer take compressed 0x25 packets; older firmware takes raw 0x21/0x22/0x23 chunks"
              >
                <option value="auto">Auto (by cube firmware)</option>
                <option value="compressed">Compressed 0x25 (firmware 2.5+)</option>
                <option value="chunks">Raw chunks (older firmware)</option>
              </select>
            </div>
            <details className="text-xs text-slate-400">
              <summary className="cursor-pointer select-none">Cube orientation</summary>
              <div className="mt-2 space-y-1.5">
                <select
                  value={orientation.layout}
                  onChange={(e) => setOrientation({ ...orientation, layout: e.target.value as Orientation['layout'] })}
                  className="w-full bg-slate-950 border border-slate-700 px-2 py-1.5 rounded-lg text-xs text-slate-200"
                >
                  <option value="upright">Upright (y·64 + z·8 + x, tested on Ben's cube)</option>
                  <option value="legacy">Legacy (z·64 + y·8 + x)</option>
                </select>
                <Toggle on={orientation.flipV} set={(v) => setOrientation({ ...orientation, flipV: v })} label="Upside down" />
                <Toggle on={orientation.mirrorX} set={(v) => setOrientation({ ...orientation, mirrorX: v })} label="Mirror left/right" />
                <p className="text-[11px] text-slate-500">If a drawing shows up sideways or flipped on the cube, change these. Files always stay z-up.</p>
              </div>
            </details>
            <div className="grid grid-cols-2 gap-1.5">
              <button
                onClick={() => sendToCube()}
                disabled={busy || !bridgeOnline}
                className="col-span-2 flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-semibold bg-amber-500 text-slate-950 hover:bg-amber-400 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <Send className="w-4 h-4" /> {busy ? 'Uploading…' : `Send ${frames.length} frame${frames.length > 1 ? 's' : ''} to cube`}
              </button>
              <SmallBtn onClick={() => sendToCube(frame)} disabled={busy || !bridgeOnline} title="Upload just the frame you are editing, as a still">
                <Send className="w-3.5 h-3.5" /> This frame only
              </SmallBtn>
              <SmallBtn
                onClick={() => bridgeRequest('send', { ip: targetIp, hex: '10 00 00 00 00 0D 0A' }, { onLog: (l) => setLog((ls) => [...ls.slice(-30), l]) })}
                disabled={busy || !bridgeOnline}
                title="Send 0x10 lamp off"
              >
                Lamp off
              </SmallBtn>
            </div>
            {!bridgeOnline && (
              <p className="text-[11px] text-amber-300/80">
                Bridge offline. Run <code className="font-mono">npm run dev</code> on a computer on the cube's Wi-Fi and open the app from there. Export still works without it.
              </p>
            )}
            {progress && (
              <div className="h-1.5 bg-slate-950 rounded overflow-hidden">
                <div className="h-full bg-amber-500 transition-all" style={{ width: `${(progress.done / Math.max(1, progress.total)) * 100}%` }} />
              </div>
            )}
            {message && <p className={`text-xs ${message.kind === 'ok' ? 'text-emerald-400' : 'text-red-400'}`}>{message.text}</p>}
          </div>
        </div>
      </div>

      {/* Timeline */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-3 space-y-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <SmallBtn onClick={() => setPlaying((p) => !p)} disabled={frames.length < 2} title="Preview playback at the frame interval">
            {playing ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />} {playing ? 'Pause' : 'Play'}
          </SmallBtn>
          <span className="w-px h-5 bg-slate-800 mx-1" />
          <SmallBtn onClick={() => addFrame(false)} disabled={frames.length >= MAX_FRAMES}>
            <FilePlus2 className="w-3.5 h-3.5" /> New frame
          </SmallBtn>
          <SmallBtn onClick={() => addFrame(true)} disabled={frames.length >= MAX_FRAMES}>
            <Copy className="w-3.5 h-3.5" /> Duplicate
          </SmallBtn>
          <SmallBtn onClick={() => moveFrame(-1)} disabled={cur === 0} title="Move frame earlier">
            <ArrowLeft className="w-3.5 h-3.5" />
          </SmallBtn>
          <SmallBtn onClick={() => moveFrame(1)} disabled={cur === frames.length - 1} title="Move frame later">
            <ArrowRight className="w-3.5 h-3.5" />
          </SmallBtn>
          <SmallBtn onClick={deleteFrame} title="Delete frame">
            <Trash2 className="w-3.5 h-3.5" /> Delete
          </SmallBtn>
          <span className="w-px h-5 bg-slate-800 mx-1" />
          <span className="text-[11px] text-slate-500">Next frame, shifted:</span>
          <SmallBtn onClick={() => shiftDupe(-1, 0, 0)} title="Duplicate shifted left (wraps)"><ArrowLeft className="w-3.5 h-3.5" /></SmallBtn>
          <SmallBtn onClick={() => shiftDupe(1, 0, 0)} title="Duplicate shifted right (wraps)"><ArrowRight className="w-3.5 h-3.5" /></SmallBtn>
          <SmallBtn onClick={() => shiftDupe(0, 1, 0)} title="Duplicate shifted back (wraps)"><ArrowUp className="w-3.5 h-3.5" /></SmallBtn>
          <SmallBtn onClick={() => shiftDupe(0, -1, 0)} title="Duplicate shifted forward (wraps)"><ArrowDown className="w-3.5 h-3.5" /></SmallBtn>
          <SmallBtn onClick={() => shiftDupe(0, 0, 1)} title="Duplicate shifted up a layer (wraps)"><ChevronsUp className="w-3.5 h-3.5" /></SmallBtn>
          <SmallBtn onClick={() => shiftDupe(0, 0, -1)} title="Duplicate shifted down a layer (wraps)"><ChevronsDown className="w-3.5 h-3.5" /></SmallBtn>
          <SmallBtn
            onClick={() => frames.length < MAX_FRAMES && commit([...frames.slice(0, cur + 1), rotateZ(frame), ...frames.slice(cur + 1)], cur + 1)}
            title="Duplicate rotated 90° around the vertical axis"
          >
            <RotateCw className="w-3.5 h-3.5" />
          </SmallBtn>
        </div>
        <div className="flex gap-2 overflow-x-auto pb-1">
          {frames.map((f, i) => (
            <button
              key={i}
              onClick={() => {
                setPlaying(false);
                setCur(i);
              }}
              className={`shrink-0 p-1 rounded-lg border flex flex-col items-center gap-0.5 ${
                i === cur ? 'border-amber-500/60 bg-amber-500/10' : 'border-slate-800 bg-slate-950 hover:border-slate-600'
              }`}
            >
              <FrameThumb frame={f} />
              <span className="text-[10px] font-mono text-slate-400">{i + 1}</span>
            </button>
          ))}
        </div>
        <p className="text-[11px] text-slate-500">
          Keys: 0–7 or [ ] change layer, , . change frame, B/E/G/I pick tools, Ctrl+Z / Ctrl+Y undo and redo. Your work autosaves in this browser.
        </p>
      </div>

      {/* Wire format */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-3 space-y-2">
          <div className="text-xs text-slate-300 font-medium">Tittle binary that will be sent</div>
          <div className="font-mono text-xs text-amber-300 bg-slate-950 border border-slate-800 rounded-lg p-2 break-all">{toHex(header)}</div>
          <ul className="text-[11px] text-slate-400 space-y-0.5">
            <li>0x90 paint header: {frames.length} frame(s), interval byte {header[3]} (×8 ms), id 0x{animId.toString(16).toUpperCase()}, intensity {intensity}</li>
            <li>Then, on firmware 2.5+: each frame RLE-compressed + FF FF FF FF, sent as 0x25 packets of up to 512 bytes. Older firmware: 0x21 (LEDs 0–169), 0x22 (170–339), 0x23 (340–511) per frame. The .bin export uses the raw chunk layout.</li>
            <li>Total {totalBytes.toLocaleString()} bytes. The id is a hash of the content, so an unchanged animation plays from the cube's cache.</li>
          </ul>
        </div>
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-3 space-y-2">
          <div className="text-xs text-slate-300 font-medium">Bridge log</div>
          <pre className="font-mono text-[11px] text-slate-300 bg-slate-950 border border-slate-800 rounded-lg p-2 h-36 overflow-auto whitespace-pre-wrap">
            {log.length ? log.join('\n') : 'Nothing sent yet.'}
          </pre>
        </div>
      </div>
    </div>
  );
}
