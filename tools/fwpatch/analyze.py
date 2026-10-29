#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# Copyright (C) 2026 Jacek Mariański
"""fwpatch analyze: can a ready FM-1 firmware (.fwsc, or its app.bin) take the plugin socket? A prototype
(docs/PATCHER-PLAN.md). It reads the package, sweeps the app's code (pi32.py) and looks for what the socket needs:

  room      the app slot's free flash (the socket's code is appended after the image, run in place)
  layout    the entry (crt0), the start-up (fm1_cstart's copy and zero loops: .data .bss .pool, .ram_text),
            the stacks; the RAM nothing points at (the gaps between them, with no address load into them)
  hooks     the main loop (the last call of the start-up, its widest backward branch, the calls in it),
            the page table (12-byte records: title pointer, family, scope, graph, four parameter ids),
            the menu's name tables, the MIDI out ring (a 64-entry ring put), the interrupt controller (ICFG),
            the flash areas the firmware addresses, the PC-limit guard (its _etext constant)
  verdict   each with a confidence; the whole: feasible, maybe, or not

  analyze.py FILE.fwsc|app.bin [--sym SYMS]   SYMS: `objdump -t` of the same build: every finding is checked
                                               against the symbols (the ground truth) and marked OK / WRONG
Read-only: it writes nothing.
"""
import argparse
import bisect
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fm1pkg  # noqa: E402
import pi32  # noqa: E402

XIP = pi32.XIP
RAM_LO, RAM_HI = 0x01C00000, 0x01C80000
ICFG_LO, ICFG_HI = 0x01EEF100, 0x01EEF140          # the interrupt controller's priority words (fm1_irq.h)
VEC_LO, VEC_HI = 0x01C7FD50, 0x01C80000            # boot info, mailbox, the vector table (the SPL's)
DBG_LO, DBG_HI = 0x01EEE000, 0x01EEE400            # the debug unit: write and PC limits (hal/fm1_guard.h;
                                                   # the compiler loads a base in it, stores at offsets)
SOCKET_FLASH = 6 * 1024                            # the socket's own code, estimated (docs: "Sizes")
SOCKET_RAM = 1024 + 8 * 1024                       # its variables + fble2's DMA/RAM windows (7.9 KB at c1)


class Image:
    def __init__(self, app, slot):
        self.app = app
        self.slot = slot
        n = len(app)
        while n and app[n - 1] == 0xFF:
            n -= 1
        self.used = (n + 3) & ~3
        self.end, self.upper = pi32.code_extent(app[:self.used])
        self.code = pi32.Code(app, self.end)

    def at(self, a, n):
        o = a - XIP
        return self.app[o:o + n] if 0 <= o and o + n <= len(self.app) else b""

    def cstr(self, a, maxlen=40):
        b = self.at(a, maxlen)
        z = b.find(b"\0")
        if z < 1:
            return None
        s = b[:z]
        if all(0x20 <= c < 0x7F for c in s):
            return s.decode()
        return None

    def words(self, lo, hi):
        lo = max(lo, XIP)
        lo += (-lo) % 4
        for a in range(lo, min(hi, XIP + self.used) - 3, 4):
            yield a, struct.unpack_from("<I", self.app, a - XIP)[0]


# ---------------------------------------------------------------- findings
class Finding:
    def __init__(self, key, what, value, conf, detail="", gt=None):
        self.key, self.what, self.value, self.conf, self.detail, self.gt = key, what, value, conf, detail, gt
        self.check = None                              # (ok, text) after the ground truth


def entry(im):
    f = []
    ok = im.app[:4] == bytes.fromhex("04818000")
    f.append(Finding("entry", "entry stub (goto +2; rts) at 0x02000120", ok, 1.0 if ok else 0.0))
    sp = ssp = cst = None
    for pc, off, n in pi32.sweep(im.app, XIP, XIP + 0x60):
        if n == 6 and im.app[off + 1] == 0xFF:
            v = struct.unpack_from("<I", im.app, off + 2)[0]
            if im.app[off] == 0xEE:
                sp = v
            elif im.app[off] == 0xED:
                ssp = v
        if n == 4:
            ct = pi32.call_target(im.app, off)
            if ct and ct[0] == "call" and cst is None:
                cst = ct[1]
    f.append(Finding("sp", "user stack top (crt0 sp =)", sp, 1.0 if sp else 0.0, gt="_ustack_top"))
    f.append(Finding("ssp", "system stack top (crt0 ssp =)", ssp, 1.0 if ssp else 0.0, gt="_sstack_top"))
    f.append(Finding("cstart", "start-up (crt0's call)", cst, 1.0 if cst else 0.0, gt="fm1_cstart"))
    return f, cst


def startup(im, cst):
    """fm1_cstart: its loops' bounds (END loaded, then START, then if (start >= end)) and the call it ends in"""
    c = im.code
    fend = c.func_end(cst)
    imms = [(pc, r, v) for pc, r, v in c.imms if cst <= pc < fend]
    regions = []
    for i in range(len(imms) - 1):
        (p0, r0, hi), (p1, r1, lo) = imms[i], imms[i + 1]
        cmp = im.at(p1 + 6, 2)                     # then if (start >= end): b1 = 0xE9
        if p1 - p0 == 6 and RAM_LO <= lo < hi <= RAM_HI and hi - lo < 0x80000 and cmp[1:2] == b"\xe9":
            load = None
            for p2, r2, v2 in imms[i + 2:i + 5]:
                if p2 - p1 <= 20 and XIP <= v2 < XIP + im.used:
                    load = v2
                    break
            regions.append((lo, hi, load, p0))
    calls = [(pc, t) for pc, t in c.calls if cst <= pc < fend]
    main = None
    # the start-up ends in `call main; goto .` (a main that returns would fall into the loop)
    for pc, t in reversed(calls):
        g = pi32.goto2_target(im.app, pc + 4 - XIP)
        if g == pc + 4 and c.func_end(t) - t >= 0x400:   # (a noreturn helper, fm1_enter_uboot, is small)
            main = t
            break
    return regions, main, fend


def classify(regions):
    """name the start-up's regions: .data (copied, from the image), .ram_text (copied, below 0x01C08000),
    .bss and .pool (zeroed; .pool the larger, above .bss)"""
    out = {}
    zero = [(lo, hi) for lo, hi, load, _ in regions if load is None]
    copy = [(lo, hi, load) for lo, hi, load, _ in regions if load is not None]
    for lo, hi, load in copy:                      # (.ram_text and SLOOP's .c1_text: one span, as _rt_start.._rt_end)
        k = "data" if lo >= 0x01C08000 else "ram_text"
        if k in out:
            lo, hi, load = min(lo, out[k][0]), max(hi, out[k][1]), out[k][2] if out[k][0] < lo else load
        out[k] = (lo, hi, load)
    zero.sort()
    big = sorted(zero, key=lambda r: r[1] - r[0], reverse=True)
    if big:
        out["pool"] = big[0]
    rest = [r for r in zero if r != out.get("pool") and r[0] >= 0x01C08000]
    if rest:
        out["bss"] = rest[0]
    low = [r for r in zero if r[1] <= 0x01C08000]
    if low:
        out["lowram"] = low[0]
    return out


def free_ram(im, lay, sp):
    """the RAM between the regions that no address load and no pointer in the image points into"""
    used = []
    for k, v in lay.items():
        used.append((v[0], v[1], k))
    stack_lo = None
    for pc, r, v in im.code.imms:                   # the stacks' guard band: the lowest of the stack constants
        if sp and sp - 0x10000 <= v < sp and v % 0x100 == 0:
            stack_lo = v if stack_lo is None else min(stack_lo, v)
    if stack_lo:
        used.append((stack_lo, RAM_HI, "stacks, noinit, vectors"))
    used.sort()
    gaps = []
    cur = 0x01C08000 if "data" in lay else RAM_LO
    for lo, hi, k in used:
        if lo >= 0x01C08000 and lo > cur:
            gaps.append((cur, lo))
        cur = max(cur, hi)
    refs_all = [v for _, _, v in im.code.imms]
    ptrs = [v for _, v in im.words(im.end, XIP + im.used) if RAM_LO <= v < RAM_HI]   # (the data, not the code)
    out = []
    for lo, hi in gaps:
        lo2 = (lo + 0xFF) & ~0xFF
        if hi - lo2 < 256:
            continue
        nref = sum(1 for v in refs_all if lo2 <= v < hi)
        nptr = sum(1 for v in ptrs if lo2 <= v < hi)
        out.append((lo2, hi, nref, nptr))
    return out, stack_lo


def main_loop(im, main):
    c = im.code
    fend = c.func_end(main)
    back = [(pc, t) for pc, t, n in c.gotos if main <= pc < fend and main <= t < pc]
    if not back:
        return None
    best = None
    for pc, t in back:
        ncalls = sum(1 for p, _ in c.calls if t <= p < pc)
        if not best or ncalls > best[2]:
            best = (t, pc, ncalls)
    t, pc, _ = best
    calls = [(p, x) for p, x in c.calls if t <= p < pc]
    return t, pc, calls


def page_tables(im):
    """runs of 12-byte page records: {const char *title; u8 fam, scope, graph; u8 id[4]} (params.c page_t)"""
    out = []
    lo = im.end
    a = lo + (-lo) % 4
    top = XIP + im.used
    while a + 12 <= top:
        n = 0
        while a + 12 * (n + 1) <= top:
            r = im.at(a + 12 * n, 12)
            t = struct.unpack_from("<I", r, 0)[0]
            s = im.cstr(t, 24) if XIP <= t < top else None
            if not (s and 1 <= len(s) <= 16 and s.upper() == s and r[4] < 16 and r[5] < 8 and r[6] < 48):
                break
            n += 1
        if n >= 8:
            # the table proper: from the first record the code loads the address of, up to the next such record
            # (another table of the same shape; neighbours that merely look alike are left out)
            exact = sorted({(v - a) // 12 for _, v in im.code.refs(a, a + 12 * n) if (v - a) % 12 == 0})
            if exact:
                lo_r = exact[0]
                hi_r = next((r for r in exact if r > lo_r), n)
                a, n = a + 12 * lo_r, hi_r - lo_r
            fams = [im.at(a + 12 * i, 12)[4] for i in range(n)]
            ok = all(fams[i] <= fams[i + 1] or fams[i + 1] != fams[i] for i in range(n - 1))
            titles = [im.cstr(struct.unpack_from("<I", im.at(a + 12 * i, 4))[0]) for i in range(n)]
            out.append((a, n, titles, fams, ok))
            a += 12 * n
        else:
            a += 4
    return out


def name_tables(im, want=("ABOUT",)):
    """arrays of >= 4 string pointers; those naming a menu ("ABOUT")"""
    out = []
    top = XIP + im.used
    ws = list(im.words(im.end, top))
    i = 0
    while i < len(ws):
        j = i
        names = []
        while j < len(ws) and XIP <= ws[j][1] < top:
            s = im.cstr(ws[j][1], 24)
            if not s or not (1 <= len(s) <= 24):
                break
            names.append(s)
            j += 1
        if len(names) >= 4 and any(w in names for w in want):
            out.append((ws[i][0], names))
        i = max(j, i + 1)
    return out


def ring_puts(im):
    """small functions that put a word into a 64-entry ring: `x & 0x3F` and a word store [rA+rB<<2] = rC"""
    c = im.code
    out = []
    for f in c.funcs:
        e = c.func_end(f)
        if e - f > 0x60:
            continue
        b = im.at(f, e - f)
        has_and = any(b[k + 1] == 0xE1 and b[k + 2] == 0x3F and b[k] & 0xF0 == 0x60 for k in range(0, len(b) - 3, 2))
        has_st = any(b[k] == 0xD8 and b[k + 1] == 0xEC and b[k + 3] & 0x0F == 0x02 for k in range(0, len(b) - 3, 2))
        if has_and and has_st:
            ram = [v for pc, _, v in c.imms if f <= pc < e and RAM_LO <= v < RAM_HI]
            ncall = sum(1 for _, t in c.calls if t == f)
            out.append((f, e - f, ncall, ram))
    return out


FLASH_XIP = 0x01FFC000                              # flash offset + this = its plain XIP address (from 0x93000 up)


def pc_guard(im, lay):
    """the PC-limit guard (hal/fm1_guard.h) -> (_etext, every load of it, the guard's loads).  _etext is where
    .ram_text is loaded from; the guard's load comes shortly after `rN = 0x02000120` (PC_LIMIT0_L), the other one is
    the start-up's copy source and must stay as it is"""
    rt = lay.get("ram_text")
    etext = rt[2] if rt and len(rt) > 2 and rt[2] else None
    et_sites = [pc for pc, _, v in im.code.imms if etext and v == etext]
    lows = [pc for pc, _, v in im.code.imms if v == XIP]
    return etext, et_sites, [pc for pc in et_sites if any(pc - 24 <= q < pc for q in lows)]


def flash_areas(im):
    """flash offsets the code names: as numbers (0x93000..) or as plain XIP addresses beyond the image"""
    vals = {}
    for pc, _, v in im.code.imms:
        if 0x93000 <= v < 0x100000 and v % 0x100 == 0:
            vals[v] = vals.get(v, 0) + 1
        elif FLASH_XIP + 0x93000 <= v < FLASH_XIP + 0x100000 and v % 0x100 == 0:
            vals[v - FLASH_XIP] = vals.get(v - FLASH_XIP, 0) + 1
    return sorted(vals.items())


def ident(im):
    out = []
    for m in re.finditer(rb"(SLOOP|FELUCCA|Felucca|OPTIMIST|DINGHY|X0X)[\x20-\x7e]{0,24}\0", im.app[:im.used]):
        out.append(m.group(0)[:-1].decode())
    vers = re.findall(rb"\0(v?\d\.\d+(?:\.\d+)*(?:[ -][A-Z0-9]+)?)\0", im.app[:im.used])
    return sorted(set(out))[:8], [v.decode() for v in vers[:4]]


# ---------------------------------------------------------------- ground truth
def load_syms(path):
    syms = {}
    funcs = []
    for ln in open(path):
        p = ln.split()
        if len(p) < 4:
            continue
        try:
            a = int(p[0], 16)
            sz = int(p[-2], 16)
        except ValueError:
            continue
        syms.setdefault(p[-1], (a, sz))
        if " F " in ln:
            funcs.append((a, sz, p[-1]))
    funcs.sort()
    return syms, funcs


def name_of(funcs, a):
    if not funcs or a is None:
        return "?"
    i = bisect.bisect_right([f[0] for f in funcs], a) - 1
    if i < 0:
        return "?"
    fa, sz, n = funcs[i]
    return n if a == fa else f"{n}+{a - fa:#x}" if a < fa + sz else f"(after {n})"


# ---------------------------------------------------------------- report
def report(path, sym=None):
    raw = Path(path).read_bytes()
    if raw[:4] == bytes.fromhex("04818000"):
        app, slot, pkg = raw, 0x8DFBC, None
    else:
        pkg = fm1pkg.read(raw)
        app, slot = pkg.app, pkg.app_entry.size
    im = Image(app, slot)
    syms, funcs = load_syms(sym) if sym else ({}, [])
    S = lambda n: syms.get(n, (None, None))[0]  # noqa: E731
    lines = []
    P = lines.append
    P(f"# {Path(path).name}")
    if pkg:
        P(f"package: product {pkg.product}, chip key {pkg.key:#06x}, files "
          f"{', '.join(n for _, n, _ in pkg.files)}; app area entries "
          f"{', '.join(e.name for e in pkg.area_entries)}")
        for e in pkg.area_entries:
            if e.name in ("VM", "BTIF", "USR"):
                P(f"  region {e.name}: {e.offset:#x} + {e.size:#x}")
    idn, vers = ident(im)
    P(f"identity strings: {idn}  versions: {vers}")
    free = slot - im.used
    P(f"\n## Room\napp slot {slot} B, image {im.used} B, free {free} B "
      f"({'enough' if free >= SOCKET_FLASH else 'NOT enough'} for the socket's ~{SOCKET_FLASH} B)")
    P(f"code ends at {im.end:#x} (sweep; first undecodable instruction at {im.upper:#x}); {len(im.code.funcs)} call targets, "
      f"{len(im.code.calls)} calls, {len(im.code.imms)} address loads")
    if sym:
        lastf = max(a + sz for a, sz, n in funcs if XIP <= a < XIP + im.used)
        P(f"  ground truth: the last function ends at {lastf:#x} -> the sweep is "
          f"{'within 0x100 of it' if abs(im.end - lastf) < 0x100 else 'OFF by %d' % (im.end - lastf)}")
    score = {}

    def chk(key, found, want, ok=None):
        if not sym:
            return ""
        if ok is None:
            ok = found == want
        score[key] = ok
        return f"   [{'OK' if ok else 'WRONG'}: symbols say {want:#x}]" if isinstance(want, int) else \
            f"   [{'OK' if ok else 'WRONG'}: {want}]"

    fs, cst = entry(im)
    P("\n## Layout")
    for f in fs:
        v = f"{f.value:#x}" if isinstance(f.value, int) and not isinstance(f.value, bool) else f.value
        g = chk(f.key, f.value, S(f.gt)) if f.gt and S(f.gt) is not None else ""
        P(f"- {f.what}: {v}  (confidence {f.conf:.1f}){g}")
    regions, main, cend = startup(im, cst) if cst else ([], None, None)
    lay = classify(regions)
    for k, (lo, hi, *ld) in sorted(lay.items(), key=lambda kv: kv[1][0]):
        extra = f", loaded from {ld[0]:#x}" if ld and ld[0] else ""
        gt = ""
        if sym:
            names = {"data": ("_data_start", "_data_end"), "bss": ("_bss_start", "_bss_end"),
                     "pool": ("_pool_start", "_pool_end"), "ram_text": ("_rt_start", "_rt_end"),
                     "lowram": ("_lr_start", "_lr_end")}[k]
            want = (S(names[0]), S(names[1]))
            gt = chk("ram_" + k, (lo, hi), want) if None not in want else ""
            if gt:
                gt = gt.replace(f"symbols say {want}", f"symbols say {want[0]:#x}..{want[1]:#x}")
        P(f"- .{k}: {lo:#x}..{hi:#x} ({hi - lo} B){extra}{gt}")
    gaps, stack_lo = free_ram(im, lay, fs[1].value)
    P(f"- stacks from {stack_lo:#x}" if stack_lo else "- stacks: not found")
    best_gap = 0
    if "data" not in lay:
        P("- .data's copy loop not recognised (Optimist packs its RAM sections): the pointers in the initialised "
          "data cannot be scanned, so no gap can be better than low")
    for lo, hi, nref, nptr in gaps:
        conf = "high" if nref == 0 and nptr == 0 else "medium" if nref == 0 and nptr <= 2 else "low"
        if "data" not in lay:
            conf = "low"
        P(f"- free RAM candidate {lo:#x}..{hi:#x}: {hi - lo} B; address loads into it {nref}, words in the data "
          f"that would point into it {nptr} -> {conf}")
        if conf != "low":
            best_gap = max(best_gap, hi - lo)
    P("\n## Hooks")
    if main:
        P(f"- main: {main:#x} (the start-up's last call){chk('main', name_of(funcs, main), 'main-ish', name_of(funcs, main) in ('fm1_main', 'main', 'app_main')) if sym else ''}")
    else:
        P("- main: the start-up does not end in a call: main is inlined into it (the loop is searched there)")
    ml = main_loop(im, main or cst)
    if ml:
        t, pc, calls = ml
        P(f"- main loop: {t:#x}..{pc:#x}, {len(calls)} calls; first ones: " +
          ", ".join(f"{x:#x}" + (f" {name_of(funcs, x)}" if sym else "") for _, x in calls[:12]))
        if sym:
            names = {name_of(funcs, x) for _, x in calls}
            want = {"ui_input", "ui_draw", "ed_service", "ui_leds", "autosave_tick", "ota_service"}
            hit = names & want
            chk("loop", None, None, len(hit) >= 2)
            P(f"  ground truth: the loop calls {sorted(hit)} of {sorted(want)} "
              f"[{'OK' if len(hit) >= 2 else 'WRONG'}]")
    pts = page_tables(im)
    for a, n, titles, fams, ok in pts:
        refs = im.code.refs(a, a + 12 * n)
        P(f"- page table {a:#x}: {n} pages x 12 B, families grouped {ok}; {len(refs)} address loads into it; "
          f"titles {titles[:6]}..{titles[-3:]}{chk('pages', a, S('PAGES')) if S('PAGES') else ''}")
        glo = [t for t, fm in zip(titles, fams) if fm == 6]
        if glo:
            P(f"  family 6 (FAM_GLO in SLOOP; SONG in Felucca, whose settings are the MI_NAME menu): {glo}")
    if not pts:
        P("- page table: none found")
    for a, names in name_tables(im):
        tag = ""
        for nm in ("MI_NAME", "MS_NAME", "MTAB_NAME"):
            if S(nm) is not None and a <= S(nm) < a + 4 * len(names):
                tag = f"   [OK: {nm}]"
                score["menu"] = True
        P(f"- menu names {a:#x}: {len(names)}: {names[:6]}..{names[-2:]}{tag}")
    for f, size, ncall, ram in ring_puts(im):
        P(f"- 64-entry ring put {f:#x} ({size} B, {ncall} calls), RAM {', '.join(f'{v:#x}' for v in ram[:3])}" +
          (f"   [{name_of(funcs, f)}]" if sym else ""))
        if sym and name_of(funcs, f) == "midi_out_event":
            score["midi_out"] = True
    irq = im.code.refs(ICFG_LO, ICFG_HI)
    vec = im.code.refs(VEC_LO, VEC_HI)
    P(f"- interrupt controller (ICFG) loads: {len(irq)} in " +
      ", ".join(sorted({f'{im.code.func_of(pc):#x}' + (f' {name_of(funcs, im.code.func_of(pc))}' if sym else '')
                        for pc, _ in irq}))[:300])
    P(f"- vector table / boot info loads: {len(vec)}")
    # the PC-limit guard (hal/fm1_guard.h): fetches outside [0x02000120, _etext] fault. Code appended after the
    # image is past _etext, so a patch must raise the `rN = _etext` that feeds PC_LIMIT0_H. The registers are
    # reached through a base in the debug unit; _etext is where .ram_text is loaded from.
    dbg = im.code.refs(DBG_LO, DBG_HI)
    etext, et_sites, guard = pc_guard(im, lay)
    P(f"- debug unit (write/PC limits) base loads: {len(dbg)}; " +
      (f"_etext ({etext:#x}, .ram_text's load address) loaded at {len(et_sites)} site(s) "
       f"{', '.join(f'{x:#x}' for x in et_sites[:6])}" if etext else "_etext not identified"))
    if guard:
        P(f"  PC-limit guard: `rN = _etext` after `rN = 0x02000120` at {', '.join(f'{x:#x}' for x in guard)}"
          f"{' [' + name_of(funcs, im.code.func_of(guard[0])) + ']' if sym else ''}: fetches past _etext fault, so "
          f"the patch raises this constant over its appended code")
    else:
        P("  PC-limit guard: no `_etext` load after `0x02000120` found (none armed, or another form)")
    fl = flash_areas(im)
    P("- flash offsets the code names: " + ", ".join(f"{v:#x}x{n}" for v, n in fl))
    slot_used = [v for v, _ in fl if 0xEA000 <= v < 0xFB000]
    P(f"  the module slot 0xEA000..0xFB000: {'named by the firmware ' + str([hex(v) for v in slot_used]) if slot_used else 'not named by the code'}")
    P("\n## Verdict")
    room = free >= SOCKET_FLASH
    ram_ok = best_gap >= SOCKET_RAM
    hooks = bool(ml) and bool(pts)
    P(f"- flash: {'yes' if room else 'NO'} ({free} B free)")
    P(f"- RAM: {'yes' if ram_ok else 'NO'} (largest unreferenced gap {best_gap} B, need ~{SOCKET_RAM})")
    P(f"- hooks: main loop {'found' if ml else 'NOT found'}, page table {'found' if pts else 'NOT found'}, "
      f"MIDI out ring {'found' if ring_puts(im) else 'NOT found'}")
    P(f"- => {'feasible (candidates; a firmware-specific recipe still has to confirm them)' if room and ram_ok and hooks else 'not feasible as is'}")
    if sym:
        P(f"\nground truth: {sum(score.values())} of {len(score)} checks agree: " +
          ", ".join(f"{k} {'ok' if v else 'WRONG'}" for k, v in score.items()))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--sym", help="objdump -t of the same build (ground truth)")
    a = ap.parse_args()
    print(report(a.file, a.sym))
    return 0


if __name__ == "__main__":
    sys.exit(main())
