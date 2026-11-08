// Browser client for the local bridge in server.ts.
import { DEFAULT_ORIENTATION, normalizeOrientation, type Orientation } from './tittleProtocol';

const DEFAULT_BRIDGE = typeof window !== 'undefined' ? window.location.origin : 'http://localhost:3000';

export function getBridgeUrl(): string {
  try {
    return localStorage.getItem('tittle.bridgeUrl') || DEFAULT_BRIDGE;
  } catch {
    return DEFAULT_BRIDGE;
  }
}

export function setBridgeUrl(url: string) {
  try {
    if (url && url !== DEFAULT_BRIDGE) localStorage.setItem('tittle.bridgeUrl', url.replace(/\/$/, ''));
    else localStorage.removeItem('tittle.bridgeUrl');
  } catch {
    /* storage unavailable */
  }
}

export async function checkBridge(): Promise<boolean> {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 1500);
    const r = await fetch(`${getBridgeUrl()}/api/bridge/health`, { signal: ctrl.signal });
    clearTimeout(t);
    const j = await r.json();
    return j?.ok === true && j?.bridge === 'tittle';
  } catch {
    return false;
  }
}

export interface BridgeResult {
  ok: boolean;
  error?: string;
  rx?: string | null;
  [k: string]: unknown;
}

interface Callbacks {
  onLog?: (line: string) => void;
  onProgress?: (done: number, total: number) => void;
}

/** Sends a request over the bridge WebSocket (live log + progress), falling back to plain HTTP. */
export function bridgeRequest(type: 'send' | 'status' | 'animation' | 'discover' | 'pizza', body: object, cb: Callbacks = {}): Promise<BridgeResult> {
  const base = getBridgeUrl();
  const viaHttp = async (): Promise<BridgeResult> => {
    const r = await fetch(`${base}/api/cube/${type}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const j = await r.json();
    (j.log ?? []).forEach((l: string) => cb.onLog?.(l));
    return j;
  };

  return new Promise((resolve) => {
    let settled = false;
    let opened = false;
    const id = Math.random().toString(36).slice(2);
    let ws: WebSocket;
    try {
      ws = new WebSocket(base.replace(/^http/, 'ws') + '/ws');
    } catch {
      viaHttp().then(resolve, (e) => resolve({ ok: false, error: String(e?.message ?? e) }));
      return;
    }
    const fail = () => {
      if (settled) return;
      settled = true;
      if (!opened) viaHttp().then(resolve, (e) => resolve({ ok: false, error: `Bridge unreachable at ${base}: ${e?.message ?? e}` }));
      else resolve({ ok: false, error: 'Bridge connection closed before the request finished' });
    };
    ws.onopen = () => {
      opened = true;
      ws.send(JSON.stringify({ id, type, ...body }));
    };
    ws.onmessage = (ev) => {
      const m = JSON.parse(String(ev.data));
      if (m.id !== id) return;
      if (m.type === 'log') cb.onLog?.(m.line);
      else if (m.type === 'progress') cb.onProgress?.(m.done, m.total);
      else if (m.type === 'result') {
        settled = true;
        const { id: _i, type: _t, ...rest } = m;
        resolve(rest);
        ws.close();
      }
    };
    ws.onerror = fail;
    ws.onclose = fail;
  });
}

// Cube orientation (how z-up frames map onto the cube's byte order), shared by the editor and the pizza.

export function getOrientation(): Orientation {
  try {
    return normalizeOrientation(JSON.parse(localStorage.getItem('tittle.orientation') || 'null') ?? DEFAULT_ORIENTATION);
  } catch {
    return DEFAULT_ORIENTATION;
  }
}

export function saveOrientation(o: Orientation) {
  try {
    localStorage.setItem('tittle.orientation', JSON.stringify(o));
  } catch {
    /* storage unavailable */
  }
}
