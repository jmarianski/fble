#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# Copyright (C) 2026 Jacek Mariański
"""Build fble2, the PROTOTYPE of Bluetooth MIDI on Optimist's open BLE stack (module2/, optimist-ble/, bsp-radio/;
docs/FBLE2-PLAN.md). Needs only JIELI_TOOLCHAIN (the pi32v2 clang and ld): no AC79 SDK, no fm1-base.

  tools/build_fble2.py [LEVEL ...]    LEVEL: Optimist's BLE_CENTRAL, 0 (the peripheral alone), 3 (+ the central
                                      without pairing), 1 (+ pairing: SMP initiator, LL encryption, passkey). All
                                      three when none is given.
Each: build/fble2/c<LEVEL>/fble2.img (the image and its relocations, for fext_push.py), .elf, .map, .sym; the same
module linked at the proposed SLOOP layout (SLOOP2 below) and checked against the image moved there (fext_reloc.py
place), as build_module.py checks fble. Prints the image and the windows against the proposed slot.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "fble2"
sys.path.insert(0, str(ROOT / "tools"))
import fext_reloc as R  # noqa: E402

# the proposed layout (docs/FBLE2-PLAN.md "Slot"): SLOOP with the slot moved above the factory VM, its windows as now
SLOOP2 = dict(off=0xEA000, max=0x11000, xip=0x01FFC000 + 0xEA000,
              win=[(0x01C06F00, 0x01C07F00), (0x01C08000, 0x01C0E000), (0x01C1E000, 0x01C20000)])
FELUCCA2 = dict(off=0xEA000, max=0x12000)          # (Felucca: up to its globals at 0xFC000; the NV is elsewhere)

CFLAGS = ["-target", "pi32v2", "-mcpu=r3", "-Os", "-ffunction-sections", "-fdata-sections", "-fno-builtin",
          "-fno-common", "-Wall", "-Wno-unused-function", "-mllvm", "-pi32v2-large-program=true",
          "-DBLE_DIAG=0", "-DBLE_DEVICE_NAME=\"FM-1\""]
INC = [ROOT / "include", ROOT / "module2", ROOT / "module2" / "hal", ROOT / "module", ROOT / "optimist-ble" / "ble",
       ROOT / "bsp-radio"]


def toolchain():
    tc = os.environ.get("JIELI_TOOLCHAIN")
    if not tc or not (Path(tc) / "pi32v2" / "bin" / "clang").exists():
        raise SystemExit("JIELI_TOOLCHAIN must point at the JieLi toolchain (pi32v2/bin/clang): build.sh reads "
                         "local.env")
    return Path(tc)


def run(*cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        sys.stderr.write(r.stdout + r.stderr)
        raise SystemExit(f"fble2: {Path(str(cmd[0])).name} failed")
    if r.stderr.strip():
        sys.stderr.write(r.stderr)
    return r.stdout


def ld_script(path, flash, win):
    import re
    text = (ROOT / "module2" / "fble2.ld").read_text()
    for region, at in (("FLASH", flash), ("CODE", win[0]), ("DMA", win[1]), ("RAM", win[2])):
        text, n = re.subn(rf"(\b{region}\s+\([rwx]+\)\s*:\s*ORIGIN\s*=\s*)0x[0-9A-Fa-f]+", rf"\g<1>{at:#010x}", text)
        assert n == 1, region
    path.write_text(text)
    return path


def build(level):
    tc = toolchain()
    out = OUT / f"c{level}"
    out.mkdir(parents=True, exist_ok=True)
    obj = out / "fble2_mod.o"
    run(tc / "pi32v2/bin/clang", *CFLAGS, f"-DBLE_CENTRAL={level}", *(f"-I{d}" for d in INC),
        "-c", ROOT / "module2" / "fble2_mod.c", "-o", obj)
    def link(ld, elf, map_):
        run(tc / "pi32v2/bin/ld", "-q", "--gc-sections", "-e", "fble_init", "-u", "fble2_hdr", "-T", ld,
            "-Map", map_, obj, "-o", elf)
    elf = out / "fble2.elf"
    chk = out / "fble2-sloop2.elf"
    link(ld_script(out / "fble2.ld", R.BASES[0], R.BASES[1:]), elf, out / "fble2.map")
    link(ld_script(out / "fble2-sloop2.ld", SLOOP2["xip"], [w[0] for w in SLOOP2["win"]]), chk,
         out / "fble2-sloop2.map")
    (out / "fble2.sym").write_text(run(tc / "common/bin/objdump", "-t", elf))
    rt = run(tc / "common/bin/objdump", "-d", "-j", ".ram_text", elf)
    calls = [ln for ln in rt.splitlines() if "\tcall" in ln]
    if calls:                                       # (as Optimist's build: the RAM code calls nothing in flash)
        raise SystemExit(f"fble2: .ram_text calls out: {calls[:3]}")
    img, table = R.make(elf, out / "fble2.img")
    moved = R.place(img, table, dict(SLOOP2, max=1 << 24))
    there, _ = R.image(R.Elf(chk), [SLOOP2["xip"]] + [w[0] for w in SLOOP2["win"]])
    there = R.crcs(there)
    (out / "fble2-sloop2.bin").write_bytes(moved)
    if moved != there:
        diff = [i for i in range(min(len(moved), len(there))) if moved[i] != there[i]]
        raise SystemExit(f"fble2: moved to SLOOP2's, the image differs from the one linked there: {len(diff)} bytes "
                         f"(sizes {len(moved)}, {len(there)})")
    size, wins = R.needs(img)
    rt_n = wins[0][1] - wins[0][0]
    print(f"fble2 c{level}: image {size} B ({len(table)} relocations); proposed SLOOP slot {SLOOP2['max']} B: "
          f"{SLOOP2['max'] - size} B free; Felucca {FELUCCA2['max']} B: {FELUCCA2['max'] - size} B free")
    print(f"fble2 c{level}: RAM " + ", ".join(f"{R.NAMES[k + 1]} {hi - lo} B (SLOOP keeps "
                                               f"{SLOOP2['win'][k][1] - SLOOP2['win'][k][0]})"
                                               for k, (lo, hi) in enumerate(wins))
          + f"; .ram_text {rt_n} B, no calls; moved to SLOOP2: the same bytes as linked there")
    return size <= SLOOP2["max"] and all(hi - lo <= SLOOP2["win"][k][1] - SLOOP2["win"][k][0]
                                         for k, (lo, hi) in enumerate(wins))


def main():
    levels = [int(a) for a in sys.argv[1:]] or [0, 3, 1]
    ok = [build(lv) for lv in levels]
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
