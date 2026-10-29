# SPDX-License-Identifier: MPL-2.0
# Copyright (C) 2026 Jacek Mariański
"""The little of the pi32v2 (JieLi AC791N) instruction set the patcher needs, as observed in the disassembly of
SLOOP 2.8.3 and Felucca main built with JieLi's clang (154,150 instructions; docs/PATCHER-PLAN.md "ISA"):

  length   the first halfword's high byte b1 decides it: 0xFF -> 6 bytes, 0xE0..0xFE -> 4, else 2
           (no exception in the sample; the toolchain's objdump agrees on all 52,721 halfwords it decodes,
           gen_valid.py)
  valid    which first halfwords decode at all: pi32v2-valid.bin (gen_valid.py); the first one that does not
           ends .text (the sweep's end, code_extent)
  call     4 bytes  [b0 b1 lo hi], b1 = 0xEA, b0 = 10cccccc: target = pc + 4 + 2 * s22(c:hi:lo)
  goto     4 bytes  the same with b0 = 11cccccc
  goto     2 bytes  [b0 b1], b1 = 100xxxxx: target = pc + 2 + 2 * s9(b0[7:4]:b1[4:0])
  rN = imm32  6 bytes [cN ff i0 i1 i2 i3] (N = 0..15; sp = 0xEE, ssp = 0xED): the only way the compiler forms an
           address above 0xFFFF (no literal pools): every XIP or RAM address code uses is in one of these
  rts      [80 00]; push of rets: [.. 04] / [c8 04] forms ([--sp] = {rets, ...})
Nothing here is the toolchain's code: these are facts read off its output, enough to sweep code linearly, follow calls
and find address references. Everything else is skipped by its length.
"""
import struct

XIP = 0x02000120


def ilen(b1):
    return 6 if b1 == 0xFF else 4 if b1 >= 0xE0 else 2


def s(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def call_target(img, off, base=XIP):
    """(kind, target) of a 4-byte call/goto at img[off], else None"""
    b0, b1 = img[off], img[off + 1]
    if b1 != 0xEA or not (b0 & 0x80):
        return None
    lo = img[off + 2] | img[off + 3] << 8
    v = s(((b0 & 0x3F) << 16) | lo, 22)
    return ("goto" if b0 & 0x40 else "call"), base + off + 4 + 2 * v


def goto2_target(img, off, base=XIP):
    b0, b1 = img[off], img[off + 1]
    if b1 & 0xE0 != 0x80:
        return None
    v = s(((b0 >> 4) << 5) | (b1 & 0x1F), 9)
    return base + off + 2 + 2 * v


def enc_call(pc, target, goto=False):
    """the 4 bytes of `call target` (or goto) placed at address pc"""
    d = target - (pc + 4)
    if d % 2 or not -(1 << 22) <= d < (1 << 22):
        raise ValueError(f"call {pc:#x} -> {target:#x}: out of range")
    v = (d // 2) & 0x3FFFFF
    return bytes([0x80 | (0x40 if goto else 0) | (v >> 16), 0xEA, v & 0xFF, (v >> 8) & 0xFF])


def imm32(img, off):
    """(register, value) of a 6-byte rN = imm32 at img[off], else None"""
    if img[off + 1] != 0xFF or img[off] & 0xF0 != 0xC0:
        return None
    return img[off] & 0x0F, struct.unpack_from("<I", img, off + 2)[0]


def sweep(img, start, end, base=XIP):
    """linear sweep: (address, offset, length) of each instruction in [start, end) (addresses)"""
    off = start - base
    stop = end - base
    while off + 2 <= stop:
        n = ilen(img[off + 1])
        if off + n > stop:
            break
        yield base + off, off, n
        off += n
        if n == 2 and img[off - 1] == 0x01 and img[off - 2] < 0x20:
            off = table_end(img, off, img[off - 2] >= 0x10)


def table_end(img, t, half):
    """tbb [rN] / tbh [rN] (b1 = 0x01, b0 = 0x0N / 0x1N) is followed by its table: byte (tbb) or halfword (tbh)
    entries, each a forward distance in halfwords from the table's start; the first case starts where the table
    ends, so the table runs up to the smallest target seen. -> the offset after it (even)"""
    lim, p, w = None, t, 2 if half else 1
    while lim is None or p < lim:
        if p + w > len(img):
            return len(img)
        e = img[p] | (img[p + 1] << 8 if half else 0)
        if e == 0:
            break
        x = t + 2 * e
        lim = x if lim is None else min(lim, x)
        p += w
    return p + (p & 1)


# Which first halfwords decode at all: pi32v2-valid.bin (8 KB, bit (b1 << 8 | b0)), made by gen_valid.py from the
# toolchain's objdump (all 65536 halfwords, each followed by a fixed tail).  It is exact for 2-byte instructions; for
# 4- and 6-byte ones the tail matters, so only the classes objdump never accepts (b1 below) count as invalid there.
# Against the symbols of three builds the first invalid instruction is the real end of .text to within 2 bytes.
try:
    with open(__file__.rsplit("/", 1)[0] + "/pi32v2-valid.bin", "rb") as _f:
        VALID = _f.read()
except OSError:                          # without the table: the classes objdump never accepts, nothing finer
    VALID = None
NEVER = set(range(0xC0, 0xD4)) | {0xE2, 0xE3, 0xE6, 0xE7, 0xF2, 0xF3, 0xF6, 0xF7}


def valid(img, off):
    b1 = img[off + 1]
    if b1 in NEVER:
        return False
    if b1 >= 0xE0 or VALID is None:
        return True
    v = img[off] | b1 << 8
    return bool(VALID[v >> 3] >> (v & 7) & 1)


def code_extent(img, base=XIP):
    """where the code ends -> (end, first invalid).  The sweep runs to the first instruction objdump would not
    decode, and the end is cut back to the last instruction that does not fall through (rts, a goto).  A run of
    0xFF fill (at least 32 bytes, up to an aligned start of more code) is stepped over: some images put a small
    boot part in front and the rest of .text at the next 4 KB."""
    def ends(off, n):
        b0, b1 = img[off], img[off + 1]
        return (n == 2 and b0 == 0x80 and b1 == 0x00) or (n == 2 and b1 & 0xE0 == 0x80) or \
            (n == 4 and b1 == 0xEA and b0 & 0xC0 == 0xC0)

    last, start = base, base
    while True:
        for pc, off, n in sweep(img, start, base + len(img), base):
            if valid(img, off):
                if ends(off, n):
                    last = pc + n
                continue
            k = off
            while k < len(img) and img[k] == 0xFF:
                k += 1
            if k - off >= 32 and k < len(img) and (base + k) & 0xFF == 0 and valid(img, k):
                start = base + k
                break
            return last, pc
        else:
            return last, base + len(img)


class Code:
    """one sweep over the code part of an app image: calls, gotos, address loads"""

    def __init__(self, img, end, base=XIP):
        self.img, self.base, self.end = img, base, end
        self.calls = []          # (pc, target)
        self.gotos = []          # (pc, target, length)
        self.imms = []           # (pc, reg, value)
        self.rts = []
        for pc, off, n in sweep(img, base, end, base):
            if n == 4:
                ct = call_target(img, off, base)
                if ct:
                    (self.calls if ct[0] == "call" else self.gotos).append(
                        (pc, ct[1]) if ct[0] == "call" else (pc, ct[1], 4))
            elif n == 6:
                im = imm32(img, off)
                if im:
                    self.imms.append((pc, im[0], im[1]))
            else:
                if img[off] == 0x80 and img[off + 1] == 0x00:
                    self.rts.append(pc)
                t = goto2_target(img, off, base)
                if t is not None:
                    self.gotos.append((pc, t, 2))
        self.funcs = sorted({t for _, t in self.calls if base <= t < end})

    def func_of(self, pc):
        import bisect
        i = bisect.bisect_right(self.funcs, pc) - 1
        return self.funcs[i] if i >= 0 else None

    def func_end(self, f):
        import bisect
        i = bisect.bisect_right(self.funcs, f)
        return self.funcs[i] if i < len(self.funcs) else self.end

    def refs(self, lo, hi):
        """address loads of a value in [lo, hi)"""
        return [(pc, v) for pc, _, v in self.imms if lo <= v < hi]
