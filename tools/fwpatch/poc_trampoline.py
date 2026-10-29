# SPDX-License-Identifier: MPL-2.0
# Copyright (C) 2026 Jacek Mariański
"""Proof of concept, file only: one call in the main loop goes through a trampoline appended after the image.

    poc_trampoline.py IN.fwsc OUT.fwsc [--sym SYMS]

The trampoline is a single `goto <the original target>`, so the patched firmware does exactly what the original did
(the call's return address is untouched: the callee returns to the loop). The PC-limit guard's _etext constant is
raised over the trampoline (otherwise fetching it would fault, hal/fm1_guard.h). What it shows is the mechanics a real
patch needs: code appended in the app slot's free space runs in place from XIP, a call site can be re-encoded to
reach it, and the package can be rebuilt (app.bin CRC, app area CRCs, SFC encryption with the chip key, the UFW
header and file CRCs, the .fwsc marker bytes) so that our reader accepts it.

Nothing here installs anything. The output was never flashed; flashing it is not part of this tool.
Checks after writing: the output parses with every CRC; the app differs from the original only at the 4 bytes of
the call, the trampoline and the guard's constant; the call decodes to the trampoline and the trampoline to the original target; reading
and rewriting the output gives the same bytes.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze  # noqa: E402
import fm1pkg  # noqa: E402
import pi32  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("inp")
    ap.add_argument("out")
    ap.add_argument("--sym")
    ap.add_argument("--site", type=lambda s: int(s, 0),
                    help="the call site to redirect (one of the main loop's calls; default: its last call)")
    ap.add_argument("--no-guard", action="store_true",
                    help="leave the PC-limit guard as it is (a negative control: the trampoline should fault)")
    a = ap.parse_args()
    p = fm1pkg.load(a.inp)
    app = p.app
    slot = p.app_entry.size
    im = analyze.Image(app, slot)
    fs, cst = analyze.entry(im)
    regions, main_f, _ = analyze.startup(im, cst)
    ml = analyze.main_loop(im, main_f or cst)
    if not ml:
        sys.exit("no main loop found")
    t0, t1, calls = ml
    site, target = calls[-1]                       # the loop's last call (may be conditional: pass --site)
    if a.site is not None:
        hit = [c for c in calls if c[0] == a.site]
        if not hit:
            sys.exit(f"{a.site:#x} is not one of the main loop's calls")
        site, target = hit[0]
    funcs = analyze.load_syms(a.sym)[1] if a.sym else []
    name = (" " + analyze.name_of(funcs, target)) if funcs else ""
    tramp = pi32.XIP + ((im.used + 15) & ~15)      # after the image, 16-aligned
    if tramp + 4 > pi32.XIP + slot:
        sys.exit("no room after the image")
    new = bytearray(app[:im.used])
    new += b"\xFF" * (tramp - pi32.XIP - len(new))
    new += pi32.enc_call(tramp, target, goto=True)
    off = site - pi32.XIP
    new[off:off + 4] = pi32.enc_call(site, tramp)
    etext, _, guard = analyze.pc_guard(im, analyze.classify(regions))
    if a.no_guard:
        guard = []
    for g in guard:                                # the PC-limit guard must let the core fetch the trampoline
        assert struct.unpack_from("<I", new, g - pi32.XIP + 2)[0] == etext
        struct.pack_into("<I", new, g - pi32.XIP + 2, tramp + 4)
    out = fm1pkg.write(p, bytes(new))
    open(a.out, "wb").write(out)
    print(f"main loop {t0:#x}..{t1:#x}: call at {site:#x} -> {target:#x}{name} now -> trampoline {tramp:#x} "
          f"(goto {target:#x})")
    print(f"PC-limit guard: _etext {etext:#x} -> {tramp + 4:#x} at {', '.join(f'{g:#x}' for g in guard) or 'none found'}")

    # checks
    q = fm1pkg.load(a.out)
    assert not q.notes, q.notes
    b = q.app
    diff = [i for i in range(len(app)) if app[i] != b[i]]
    allowed = set(range(off, off + 4)) | set(range(tramp - pi32.XIP, tramp - pi32.XIP + 4))
    for g in guard:
        allowed |= set(range(g - pi32.XIP + 2, g - pi32.XIP + 6))
    assert diff and set(diff) <= allowed, [hex(d) for d in diff if d not in allowed][:8]
    for g in guard:
        assert pi32.imm32(b, g - pi32.XIP)[1] == tramp + 4
    assert pi32.call_target(b, off) == ("call", tramp)
    assert pi32.call_target(b, tramp - pi32.XIP) == ("goto", target)
    assert fm1pkg.write(q, b[:tramp - pi32.XIP + 4]) == out
    other = [n for (t, n, d), (t2, n2, d2) in zip(p.files, q.files) if n != "flash.bin" and d != d2]
    assert not other, other
    print(f"checks: package parses with all CRCs; app differs in {len(diff)} bytes (call site, trampoline, guard constant); "
          f"call -> trampoline -> original target decode; rewrite is byte-identical; ota.bin untouched")
    print(f"written {a.out} ({len(out)} B). File only: not for flashing.")


if __name__ == "__main__":
    main()
