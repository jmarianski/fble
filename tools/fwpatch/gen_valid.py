# SPDX-License-Identifier: MPL-2.0
# Copyright (C) 2026 Jacek Mariański
"""Regenerate pi32v2-valid.bin: which first halfwords the toolchain's objdump decodes as a pi32v2 instruction.

    gen_valid.py ELF [OUT]      (with JIELI_TOOLCHAIN set; ELF: any pi32v2 build with a .text of 512 KB or more)

All 65536 halfwords go into 8-byte slots ([b0 b1] + a fixed tail to the instruction's length + rts fill) patched over
.text of a copy of ELF, whose symbol table is hidden (objdump dumps data objects in .text as bytes). One objdump run;
a slot whose first line is an instruction (not "<unknown instruction>", not a byte dump) sets its bit. Also checks
that objdump's length agrees with pi32.ilen for every valid halfword.
"""
import os
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pi32  # noqa: E402


def main(elf_path, out):
    od = os.environ["JIELI_TOOLCHAIN"] + "/common/bin/objdump"
    elf = bytearray(open(elf_path, "rb").read())
    shoff, = struct.unpack_from("<I", elf, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", elf, 0x2E)
    secs = [struct.unpack_from("<IIIIIIIIII", elf, shoff + i * shentsize) for i in range(shnum)]
    stro = secs[shstrndx][4]
    text = [s for s in secs if elf[stro + s[0]:stro + s[0] + 6] == b".text\0"][0]
    toff, taddr, tsize = text[4], text[3], text[5]
    if tsize < 8 * 65536:
        sys.exit(".text too small for 65536 slots")
    for i, sec in enumerate(secs):
        if sec[1] == 2:                                  # SHT_SYMTAB -> SHT_NULL
            struct.pack_into("<I", elf, shoff + i * shentsize + 4, 0)
    for hw in range(65536):
        b0, b1 = hw & 0xFF, hw >> 8
        n = pi32.ilen(b1)
        elf[toff + 8 * hw:toff + 8 * hw + 8] = (bytes([b0, b1]) + b"\x12\x34\x56\x78"[:n - 2] + b"\x80\x00" * 3)[:8]
    with tempfile.NamedTemporaryFile(suffix=".elf") as f:
        f.write(elf)
        f.flush()
        r = subprocess.run([od, "-d", "--start-address=%#x" % taddr, "--stop-address=%#x" % (taddr + 8 * 65536),
                            f.name], capture_output=True, text=True, check=True)
    valid, seen, mism = bytearray(8192), 0, []
    for line in r.stdout.splitlines():
        p = line.split(":", 1)
        try:
            a = int(p[0].strip(), 16)
        except ValueError:
            continue
        if (a - taddr) % 8 or not taddr <= a < taddr + 8 * 65536:
            continue
        hw, seen = (a - taddr) // 8, seen + 1
        f = p[1].split("\t")
        nb = len([x for x in f[0].split() if len(x) == 2])
        if "unknown instruction" not in line and 2 <= nb <= 6 and "\t".join(f[1:]).strip():
            valid[hw >> 3] |= 1 << (hw & 7)
            if nb != pi32.ilen(hw >> 8):
                mism.append(hw)
    open(out, "wb").write(valid)
    print("slots", seen, "valid", sum(bin(x).count("1") for x in valid), "length disagreements", len(mism))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else
         os.path.join(os.path.dirname(os.path.abspath(__file__)), "pi32v2-valid.bin"))
