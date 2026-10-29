#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# Copyright (C) 2026 Jacek Mariański
"""Where fble's bytes go: each symbol of fble.elf attributed to its source file (from the DWARF), summed.

  fble/tools/fble_size.py [fble/build/fble.elf] [-s SECTION] [-n N] [-f FILE-REGEX]

Prints, per output section (.seg0 .seg1 .ram_text .data .bss .pool), the bytes per source file (the compile unit
LTO merged the symbol from: btctrler's lmp.c, btstack's sm.c, ...), largest first, then the largest symbols. Bytes
not covered by a sized symbol (padding, LTO's anonymous constants, strings) are listed as "(unattributed)".
Needs pyelftools.
"""
import argparse
import bisect
import collections
import os
import sys

from elftools.elf.elffile import ELFFile


def cu_ranges(elf):
    """[(lo, hi, file)] of every subprogram and variable with a location, from the DWARF"""
    out = []
    dw = elf.get_dwarf_info(relocate_dwarf_sections=False)   # (ld -q kept them: the values are final)
    for cu in dw.iter_CUs():
        top = cu.get_top_DIE()
        name = top.attributes.get("DW_AT_name")
        name = os.path.basename(name.value.decode(errors="replace")) if name else "?"
        for die in cu.iter_DIEs():
            if die.tag == "DW_TAG_subprogram":
                lo = die.attributes.get("DW_AT_low_pc")
                hi = die.attributes.get("DW_AT_high_pc")
                if lo and hi:
                    h = hi.value if hi.form.startswith("DW_FORM_addr") else lo.value + hi.value
                    out.append((lo.value, h, name))
            elif die.tag == "DW_TAG_variable":
                loc = die.attributes.get("DW_AT_location")
                if loc and loc.form in ("DW_FORM_exprloc", "DW_FORM_block1") and loc.value and loc.value[0] == 3:
                    addr = int.from_bytes(bytes(loc.value[1:5]), "little")
                    out.append((addr, addr + 1, name))
    out.sort()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("elf", nargs="?", default=os.path.join(os.path.dirname(__file__), "..", "build", "fble.elf"))
    ap.add_argument("-s", "--section", action="append")
    ap.add_argument("-n", type=int, default=25)
    ap.add_argument("-f", "--file", help="list this file's symbols (a regex on the source file's name)")
    a = ap.parse_args()
    import re
    with open(a.elf, "rb") as fh:
        elf = ELFFile(fh)
        secs = [(s.name, s["sh_addr"], s["sh_addr"] + s["sh_size"]) for s in elf.iter_sections()
                if s["sh_addr"] and s["sh_size"]]
        rng = cu_ranges(elf)
        los = [r[0] for r in rng]
        syms = []
        seen = set()
        for s in elf.get_section_by_name(".symtab").iter_symbols():
            if s["st_size"] and s["st_info"]["type"] in ("STT_FUNC", "STT_OBJECT") and s["st_value"] not in seen:
                seen.add(s["st_value"])
                syms.append((s["st_value"], s["st_size"], s.name))
    syms.sort()

    def where(addr):
        i = bisect.bisect_right(los, addr) - 1
        while i >= 0 and i >= bisect.bisect_right(los, addr) - 8:
            lo, hi, f = rng[i]
            if lo <= addr < max(hi, lo + 1):
                return f
            i -= 1
        return "?"
    for name, lo, hi in secs:
        if a.section and name not in a.section:
            continue
        per = collections.Counter()
        big = []
        covered = 0
        for addr, size, sname in syms:
            if lo <= addr < hi:
                f = where(addr)
                per[f] += size
                covered += size
                big.append((size, sname, f))
        per["(unattributed)"] = (hi - lo) - covered
        print(f"== {name} {hi - lo} B")
        for f, n in per.most_common(a.n):
            print(f"  {n:7} {f}")
        if a.file:
            for size, sname, f in sorted(big, reverse=True):
                if re.search(a.file, f):
                    print(f"    {size:6} {sname} ({f})")
        else:
            print("  -- largest symbols")
            for size, sname, f in sorted(big, reverse=True)[:a.n]:
                print(f"  {size:7} {sname} ({f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
