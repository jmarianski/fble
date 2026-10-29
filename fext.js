// SPDX-License-Identifier: MPL-2.0
// Copyright (C) 2026 Jacek Mariański
//
// fext in the browser: tools/fext_reloc.py's place() and tools/fext_push.py's protocol over Web MIDI.
//
//   readImg(bytes)            a .img (FXIM): { img, table }
//   place(img, table, slot)   the image moved to a host's slot and windows (slot: what info() answers), CRCs too
//   writeFrames(data, off)    the write frames, the header's sector last (a cut write leaves no header that passes)
//   findFext(access)          the FM-1 whose firmware has the socket: a Fext on its ports, or null
//   Fext: status(), info(), push(imgBytes, onStep), load(), remove(), reboot(noload), control(text),
//         knob(page, col, steps), log()
//
// The protocol: F0 7D 46 42 op ... F7, the firmware's fext_host.c (ops 1 status, 2 write, 3 load, 5 remove,
// 6 reboot, 7 control, 9 knob, 10 info); its log: F0 7D 4C 47 ... F7.

export const FB = [0xF0, 0x7D, 0x46, 0x42];
export const LG = [0xF0, 0x7D, 0x4C, 0x47];
export const ERR = { 0: "ok", 1: "outside the module's slot", 2: "erase failed", 3: "write failed",
                     4: "read back differs", 5: "malformed frame", 6: "the module runs" };
const MAGIC = 0x54584546;                         // "FEXT"
const HDR_SIZE = 132;                             // fext.h fext_header
const O_HDR_CRC = 12, O_SIZE = 16, O_CRC = 20, O_XIP = 24, O_WIN = 28, O_COPY = 52, O_INIT = 100;
const ALIGN = 256;                                // fext.h FEXT_W_ALIGN
const CHUNK = 256;
export const WINDOWS = ["CODE", "DMA", "RAM"];
// the FM-1's own ports only (other gear is never asked): tools/fext_push.py's NAMES
export const FM1_PORT = /fm-1|midimon|felucca|sloop/i;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function fail(msg) { return new Error(msg); }

// ---------------------------------------------------------------- the image ---
const CRC_T = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();
export function crc32(b, start = 0, end = b.length) {
  let c = 0xFFFFFFFF;
  for (let i = start; i < end; i++) c = CRC_T[(c ^ b[i]) & 0xFF] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0;
}

export function readImg(bytes) {
  const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  if (String.fromCharCode(...b.subarray(0, 4)) !== "FXIM") throw fail("not an fext image (.img: FXIM)");
  const v = new DataView(b.buffer, b.byteOffset, b.byteLength);
  const fmt = v.getUint32(4, true), n = v.getUint32(8, true), k = v.getUint32(12, true);
  if (fmt !== 1) throw fail(`image format ${fmt}: this page knows 1`);
  if (16 + n + 4 * k > b.length) throw fail("the image file is cut short");
  const img = b.slice(16, 16 + n);
  const table = new Uint32Array(k);
  for (let i = 0; i < k; i++) table[i] = v.getUint32(16 + n + 4 * i, true);
  return { img, table };
}

const dv = (img) => new DataView(img.buffer, img.byteOffset, img.byteLength);

// the header's name, version, ABI and windows as linked
export function describe(img) {
  const v = dv(img);
  if (v.getUint32(0, true) !== MAGIC) throw fail("not an fext module (no FEXT header)");
  const str = (o, n) => { let s = ""; for (let i = 0; i < n && img[o + i]; i++) s += String.fromCharCode(img[o + i]); return s; };
  return {
    abi: [v.getUint16(4, true), v.getUint16(6, true)], size: img.length,
    win: [0, 1, 2].map((k) => [v.getUint32(O_WIN + 8 * k, true), v.getUint32(O_WIN + 8 * k + 4, true)]),
    name: str(108, 8), version: str(116, 16),
  };
}

function crcs(img) {
  const v = dv(img);
  v.setUint32(O_CRC, crc32(img, HDR_SIZE), true);
  v.setUint32(O_HDR_CRC, 0, true);
  v.setUint32(O_HDR_CRC, crc32(img, 0, HDR_SIZE), true);
  return img;
}

// slot = { off, max, xip, win: [[lo, hi] x 3] }
export function place(img0, table, slot) {
  const img = img0.slice(), v = dv(img);
  const wins = describe(img).win;
  if (img.length > slot.max) throw fail(`the module is ${img.length} B, the firmware's slot ${slot.max} B`);
  const old = [v.getUint32(O_XIP, true), ...wins.map((w) => w[0])];
  const nu = [slot.xip];
  wins.forEach(([lo, hi], k) => {
    const [hlo, hhi] = slot.win[k];
    if (lo === hi) { nu.push(lo); return; }
    if (hlo % ALIGN || hi - lo > hhi - hlo)
      throw fail(`the module's ${WINDOWS[k]} window is ${hi - lo} B, the firmware keeps ${hhi - hlo} B` +
                 (hlo % ALIGN ? " (not 256-aligned)" : ""));
    nu.push(hlo);
  });
  const d = [...nu.map((n, i) => (n - old[i]) >>> 0), 0];
  for (const e of table) {                        // the words that hold addresses
    const at = e & 0x7FFFFF, t = (e >>> 23) & 7, site = (e >>> 26) & 3, pcrel = (e >>> 28) & 1;
    v.setUint32(at, (v.getUint32(at, true) + d[t] - (pcrel ? d[site] : 0)) >>> 0, true);
  }
  v.setUint32(O_XIP, slot.xip, true);
  wins.forEach(([lo, hi], k) => {
    v.setUint32(O_WIN + 8 * k, (lo + d[k + 1]) >>> 0, true);
    v.setUint32(O_WIN + 8 * k + 4, (hi + d[k + 1]) >>> 0, true);
  });
  for (let i = 0; i < 4; i++) {
    const o = O_COPY + 12 * i, dst = v.getUint32(o, true), src = v.getUint32(o + 4, true), n = v.getUint32(o + 8, true);
    if (!n) continue;
    const k = wins.findIndex(([lo, hi]) => lo <= dst && dst + n <= hi);
    if (k < 0) throw fail(`load step ${i} is outside the module's windows`);
    v.setUint32(o, (dst + d[k + 1]) >>> 0, true);
    v.setUint32(o + 4, src ? (src + d[0]) >>> 0 : 0, true);
  }
  v.setUint32(O_INIT, (v.getUint32(O_INIT, true) + d[0]) >>> 0, true);
  return crcs(img);
}

// fext_push.py's 7-bit packing: a byte of the seven's high bits, then the seven
export function pack7(b) {
  const out = [];
  for (let i = 0; i < b.length; i += 7) {
    const g = b.subarray ? b.subarray(i, i + 7) : b.slice(i, i + 7);
    let hi = 0;
    g.forEach((x, k) => { hi |= ((x >> 7) & 1) << k; });
    out.push(hi, ...Array.from(g, (x) => x & 0x7F));
  }
  return out;
}
export function unpack7(b) {
  const out = [];
  for (let i = 0; i < b.length; i += 8) {
    const hi = b[i];
    for (let k = 0; k < 7 && i + 1 + k < b.length; k++) out.push(b[i + 1 + k] | (((hi >> k) & 1) << 7));
  }
  return out;
}

export function writeFrames(data, off) {
  const chunks = [];
  for (let k = 0; k < data.length; k += CHUNK) chunks.push([off + k, data.subarray(k, k + CHUNK)]);
  const order = [...chunks.filter((c) => c[0] >= off + 0x1000), ...chunks.filter((c) => c[0] < off + 0x1000)];
  return order.map(([o, c]) => [o, Uint8Array.from([...FB, 2, o & 0x7F, (o >> 7) & 0x7F, (o >> 14) & 0x7F,
                                                    ...pack7(c), 0xF7])]);
}

// ---------------------------------------------------------------- the link ---
const starts = (f, p) => p.every((x, i) => f[i] === x);
const text = (f, skip) => String.fromCharCode(...f.slice(skip, -1));

class Link {
  constructor(input, output) {
    this.input = input; this.output = output; this.q = []; this.waiters = [];
    input.onmidimessage = (e) => {
      if (e.data[0] !== 0xF0) return;
      this.q.push(Array.from(e.data));
      const w = this.waiters.shift();
      if (w) w();
    };
  }
  send(b) { this.output.send(b instanceof Uint8Array ? b : Uint8Array.from(b)); }
  async next(ms) {                                // the next SysEx frame, or null after ms
    if (this.q.length) return this.q.shift();
    await new Promise((r) => {
      const t = setTimeout(() => { this.waiters = this.waiters.filter((x) => x !== go); r(); }, ms);
      const go = () => { clearTimeout(t); r(); };
      this.waiters.push(go);
    });
    return this.q.length ? this.q.shift() : null;
  }
  async ask(frame, want, ms = 2000) {
    this.q.length = 0;
    this.send(frame);
    const end = Date.now() + ms;
    for (;;) {
      const f = await this.next(Math.max(0, end - Date.now()));
      if (!f) return null;
      if (starts(f, want)) return f;
    }
  }
  close() { this.input.onmidimessage = null; }
}

const live = (p) => p.state !== "disconnected";

// the FM-1 whose firmware answers the socket's status: { fext, name } or null
export async function findFext(access) {
  const outs = [...access.outputs.values()].filter(live);
  for (const input of [...access.inputs.values()].filter(live)) {
    const nm = input.name || "";
    if (!FM1_PORT.test(nm)) continue;
    const named = outs.filter((o) => o.name === nm || (o.name && (o.name.includes(nm) || nm.includes(o.name))));
    for (const output of named.length ? named : outs.filter((o) => FM1_PORT.test(o.name || ""))) {
      try { await input.open(); await output.open(); } catch (_) { continue; }
      const link = new Link(input, output);
      const f = await link.ask([...FB, 1, 0xF7], [...FB, 1], 1500);
      if (f) return { fext: new Fext(link, access), name: nm };
      link.close();
    }
  }
  return null;
}

export class Fext {
  constructor(link, access) { this.link = link; this.access = access; }
  async op(op, args = [], ms = 2000) { return this.link.ask([...FB, op, ...args, 0xF7], [...FB, op], ms); }
  async status() { const f = await this.op(1, [], 5000); return f ? text(f, 5) : null; }
  async info() {
    const f = await this.op(10);
    if (!f) return null;
    const raw = Uint8Array.from(unpack7(f.slice(5, -1)));
    if (raw.length < 40) throw fail("a short info answer");
    const v = dv(raw), u = (i) => v.getUint32(4 * i, true);
    return { abi: [u(0) & 0xFFFF, u(0) >>> 16], off: u(1), max: u(2), xip: u(3),
             win: [0, 1, 2].map((k) => [u(4 + 2 * k), u(5 + 2 * k)]) };
  }
  async load() { const f = await this.op(3, [], 5000); return f ? text(f, 5) : null; }
  async remove() { const f = await this.op(5, [], 5000); return f ? text(f, 5) : null; }
  async control(t) { const f = await this.op(7, Array.from(t, (c) => c.charCodeAt(0) & 0x7F)); return f ? text(f, 5) : null; }
  async knob(page, col, steps) {
    const f = await this.op(9, [page, col, Math.max(1, Math.min(127, steps + 64))]);
    return f ? text(f, 5) : null;
  }
  async log() {
    this.link.q.length = 0;
    this.link.send([...LG, 0xF7]);
    const lines = [];
    for (;;) {
      const f = await this.link.next(1500);
      if (!f) break;
      if (!starts(f, LG)) continue;
      const t = text(f, 4);
      lines.push(t);
      if (t === "end") break;
    }
    return lines;
  }
  // restart the FM-1 (noload: without the module, to rewrite it) and find it again
  async reboot(noload = false) {
    this.link.send([...FB, 6, noload ? 1 : 0, 0xF7]);
    this.link.close();
    await sleep(1500);                            // (the old ports gone first)
    const end = Date.now() + 20000;
    while (Date.now() < end) {
      const again = await findFext(this.access);
      if (again) { this.link = again.fext.link; return true; }
      await sleep(500);
    }
    throw fail("the FM-1 did not come back after the restart");
  }
  // the whole install: info, move, write (each frame answered and read back by the FM-1), load
  async push(imgBytes, onStep = () => {}) {
    const { img, table } = readImg(imgBytes);
    const s = await this.info();
    if (!s) throw fail("no answer to info: a firmware with the fext socket?");
    const m = describe(img);
    if (m.abi[0] !== s.abi[0]) throw fail(`the module's ABI ${m.abi[0]}, the firmware's ${s.abi[0]}.${s.abi[1]}`);
    const data = place(img, table, s);
    onStep("place", { size: data.length, slot: s });
    const fr = writeFrames(data, s.off);
    for (let k = 0; k < fr.length;) {
      const [off, f] = fr[k];
      const a = await this.link.ask(f, [...FB, 2], 3000);
      if (a && a[5] === 6 && k === 0) {           // its code runs from the slot: restart without it first
        onStep("reboot");
        await this.reboot(true);
        continue;
      }
      if (!a || a[5] !== 0) throw fail(`${off.toString(16)}: ` + (a ? ERR[a[5]] || `error ${a[5]}` : "no answer"));
      k++;
      onStep("write", { done: k, total: fr.length });
    }
    const r = await this.load();
    onStep("load", r);
    if (r !== "loaded") throw fail(`load: ${r || "no answer"}`);
    return r;
  }
  close() { this.link.close(); }
}
