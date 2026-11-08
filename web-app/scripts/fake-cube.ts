/**
 * A pretend Tittle cube for testing the bridge without hardware.
 *   npm run fake-cube            (listens on 127.0.0.1:9999)
 *   FAKE_CUBE_PORT=9998 npm run fake-cube   then start the bridge with CUBE_PORT=9998
 * In the web app, set the cube IP to 127.0.0.1.
 *
 * It answers 0x60 status, replies 00 00 20 00 01 01 to a new 0x90/0x50 animation id, acks every
 * 0x21/0x22/0x23 chunk, checks the chunk sizes and frame order, and remembers ids it has received
 * so a repeat upload gets the "cached" reply 00 00 20 00 00 00.
 * FAKE_CUBE_FW=2.4 pretends to be older firmware (raw chunks); the default 2.5 expects the
 * compressed 0x25 packets, checks their checksum and decodes the frames.
 */
import net from 'node:net';
import { parseCompressedStream } from '../src/tittleProtocol.ts';

const PORT = Number(process.env.FAKE_CUBE_PORT || 9999);
const HOST = process.env.FAKE_CUBE_HOST || '127.0.0.1';
const cached = new Set<number>();
const [FW_HI, FW_LO] = (process.env.FAKE_CUBE_FW || '2.5').split('.').map(Number);
const hex = (b: Uint8Array) => Array.from(b, (x) => x.toString(16).padStart(2, '0').toUpperCase()).join(' ');

const lengthFor = (op: number) =>
  ({ 0x10: 7, 0x60: 5, 0x90: 18, 0x50: 18, 0x20: 18, 0x21: 515, 0x22: 515, 0x23: 521 } as Record<number, number>)[op];

net
  .createServer((sock) => {
    let buf = Buffer.alloc(0);
    let anim: { id: number; frames: number; got: Map<number, number>; errors: string[]; lit: number[]; stream: number[]; done: boolean } | null = null;
    const who = `${sock.remoteAddress}:${sock.remotePort}`;
    console.log(`[fake-cube] connection from ${who}`);

    sock.on('data', (d) => {
      buf = Buffer.concat([buf, d]);
      while (buf.length) {
        const op = buf[0];
        let len = lengthFor(op);
        if (op === 0x25) {
          if (buf.length < 7) return;
          len = 7 + ((buf[5] << 8) | buf[6]) + 3;
        }
        if (!len) {
          const crlf = buf.indexOf('\r\n');
          if (crlf < 0) return;
          len = crlf + 2;
        }
        if (buf.length < len) return;
        const pkt = new Uint8Array(buf.subarray(0, len));
        buf = buf.subarray(len);
        const tail = pkt[len - 2] === 0x0d && pkt[len - 1] === 0x0a;
        if (!tail) console.log(`[fake-cube] WARNING packet 0x${op.toString(16)} is missing its CRLF`);

        if (op === 0x60) {
          console.log(`[fake-cube] status query ${hex(pkt)}`);
          const name = Buffer.from('FakeCube');
          sock.write(Buffer.concat([Buffer.from([0x60, FW_HI, FW_LO, 0, 0, 0]), name, Buffer.from([0x00, 0x0d, 0x0a])]));
        } else if (op === 0x10) {
          console.log(`[fake-cube] lamp rgb(${pkt[1]},${pkt[2]},${pkt[3]}) intensity ${pkt[4]}`);
          sock.write(Buffer.from([0x10, 0x00, 0x0d, 0x0a]));
        } else if (op === 0x90 || op === 0x50 || op === 0x20) {
          const frames = (pkt[1] << 8) | pkt[2];
          const id = ((pkt[4] << 24) | (pkt[5] << 16) | (pkt[6] << 8) | pkt[7]) >>> 0;
          console.log(`[fake-cube] header 0x${op.toString(16)}: ${frames} frames, interval byte ${pkt[3]}, id 0x${id.toString(16)}, intensity ${pkt[9]}`);
          if (cached.has(id)) {
            console.log('[fake-cube]   id cached -> 00 00 20 00 00 00');
            sock.write(Buffer.from([0x00, 0x00, 0x20, 0x00, 0x00, 0x00, 0x0d, 0x0a]));
          } else {
            console.log('[fake-cube]   new id -> 00 00 20 00 01 01 (data not ready)');
            anim = { id, frames, got: new Map(), errors: [], lit: [], stream: [], done: false };
            sock.write(Buffer.from([0x00, 0x00, 0x20, 0x00, 0x01, 0x01, 0x0d, 0x0a]));
          }
        } else if (op === 0x25) {
          const total = (pkt[1] << 8) | pkt[2];
          const index = (pkt[3] << 8) | pkt[4];
          if (!anim) console.log('[fake-cube] WARNING 0x25 packet without a header on this socket');
          else {
            if (index !== anim.stream.length / 512) anim.errors.push(`0x25 packet ${index} out of order`);
            anim.stream.push(...pkt.subarray(7, len - 3));
            if (index === total - 1) {
              const stream = Uint8Array.from(anim.stream);
              const sum = stream.reduce((a, b) => a + b, 0) & 0xff;
              if (sum !== pkt[len - 3]) anim.errors.push(`checksum ${pkt[len - 3]} != ${sum}`);
              try {
                const frames = parseCompressedStream(stream);
                frames.forEach((f, i) => {
                  anim!.got.set(i, 3);
                  let lit = 0;
                  for (let k = 0; k < 512; k++) if (f[k * 3] | f[k * 3 + 1] | f[k * 3 + 2]) lit++;
                  anim!.lit[i] = lit;
                });
                console.log(`[fake-cube] 0x25 stream complete: ${total} packets, ${stream.length} bytes -> ${frames.length} frames`);
                const litIdx: number[] = [];
                for (let k = 0; k < 512 && litIdx.length < 8; k++) if (frames[0][k * 3] | frames[0][k * 3 + 1] | frames[0][k * 3 + 2]) litIdx.push(k);
                console.log(`[fake-cube]   frame 0 lit device indices: ${litIdx.join(', ')}`);
              } catch (e: any) {
                anim.errors.push(`decode failed: ${e.message}`);
              }
            }
          }
          sock.write(Buffer.from([0x25, pkt[3], pkt[4], 0x0d, 0x0a]));
        } else if (op >= 0x21 && op <= 0x23) {
          if (FW_HI > 2 || (FW_HI === 2 && FW_LO >= 5)) console.log('[fake-cube] note: got raw chunks although this firmware expects 0x25');
          const order = (pkt[1] << 8) | pkt[2];
          if (!anim) console.log('[fake-cube] WARNING chunk without a header on this socket');
          else {
            const expect = op - 0x21;
            const have = anim.got.get(order) ?? 0;
            if (have !== expect) anim.errors.push(`frame ${order}: got 0x${op.toString(16)} after ${have} chunk(s)`);
            anim.got.set(order, have + 1);
            let lit = 0;
            for (let i = 3; i < len - 2; i += 3) if (pkt[i] | pkt[i + 1] | pkt[i + 2]) lit++;
            anim.lit[order] = (anim.lit[order] ?? 0) + lit;
          }
          sock.write(Buffer.from([op, pkt[1], pkt[2], 0x0d, 0x0a]));
        } else {
          console.log(`[fake-cube] other packet ${hex(pkt.subarray(0, 24))}${len > 24 ? ' …' : ''}`);
        }
      }
    });

    sock.on('close', () => {
      if (anim) {
        const complete = [...anim.got.values()].filter((n) => n === 3).length;
        const ok = complete === anim.frames && anim.errors.length === 0;
        console.log(`[fake-cube] socket closed: ${complete}/${anim.frames} complete frames, lit voxels per frame [${anim.lit.join(', ')}]`);
        anim.errors.forEach((e) => console.log(`[fake-cube]   ERROR ${e}`));
        if (ok) {
          cached.add(anim.id);
          console.log(`[fake-cube]   OK, now "looping" id 0x${anim.id.toString(16)}`);
        }
      }
    });
    sock.on('error', () => undefined);
  })
  .listen(PORT, HOST, () => console.log(`[fake-cube] firmware ${FW_HI}.${FW_LO}, listening on ${HOST}:${PORT}`));
