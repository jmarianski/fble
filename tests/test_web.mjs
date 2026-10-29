// SPDX-License-Identifier: MPL-2.0
// web/fext.js against tools/fext_reloc.py and tools/fext_push.py: the same placed image, the same frames.
//   node tests/test_web.mjs [IMG]      (IMG: build/fble.img; python3 makes the reference)
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { readImg, place, writeFrames, pack7, unpack7 } from "../web/fext.js";

const path = process.argv[2] || "build/fble.img";
const slot = { off: 0xE8000, max: 0x14000, xip: 0x020E4000,     // Felucca's (fext_host.h)
               win: [[0x01C06F00, 0x01C07F00], [0x01C08000, 0x01C0E000], [0x01C0E000, 0x01C10000]] };
const py = JSON.parse(execFileSync("python3", ["-c", `
import sys, json; sys.path.insert(0, "tools")
import fext_reloc as R, fext_push as P
s = json.loads(sys.argv[2]); s["win"] = [tuple(w) for w in s["win"]]
img, table = R.read(sys.argv[1])
d = bytes(R.place(img, table, s))
print(json.dumps({"data": d.hex(), "frames": [f.hex() for o, f in P.write_frames(d, s["off"])]}))
`, path, JSON.stringify(slot)], { maxBuffer: 1 << 26 }).toString());

const hex = (b) => Buffer.from(b).toString("hex");
let bad = 0;
const check = (ok, what) => { console.log((ok ? "ok   " : "FAIL ") + what); bad += !ok; };
const r = readImg(new Uint8Array(readFileSync(path)));
const got = place(r.img, r.table, slot);
check(hex(got) === py.data, `place: ${got.length} B as python's`);
const fr = writeFrames(got, slot.off);
check(fr.length === py.frames.length && fr.every(([, f], i) => hex(f) === py.frames[i]), `frames: ${fr.length} as python's`);
const x = Uint8Array.from({ length: 300 }, (_, i) => (i * 37) & 0xFF);
check(hex(unpack7(pack7(x))) === hex(x), "pack7 / unpack7");
process.exit(bad ? 1 : 0);
