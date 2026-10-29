#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# Copyright (C) 2026 Jacek Mariański
"""fext's images (include/fext.h): made from a link, moved to a host's slot and windows.

A module is linked once, at made-up addresses (BASES: no FM-1 has anything there), with its relocations kept
(ld -q). make() turns that link into a .img file: the image (the header first) and a table of every 32-bit word in
it that holds an address, with what the address points into (the flash, or one of the three RAM windows) and, for
a PC-relative one, where the word itself runs. place() moves the image to a host's slot and windows: each word by
how far its target moved (less how far it moved itself, PC-relative), the header's addresses, the CRCs. The table
stays in the file; the flash gets the moved image only.

  .img:  "FXIM", u32 format (1), u32 image bytes, u32 entries, the image, then the entries (u32 each):
         bits 0..22 the word's offset in the image, 23..25 the target (T_*), 26..27 where the word runs (FLASH or a
         window: PC-relative ones), 28 PC-relative

The JieLi linker (pi32v2) writes three kinds of relocation, each a whole little-endian 32-bit word: 8 (R_*_32, a
data word, at r_offset), 5 (an instruction's 32-bit immediate, at r_offset + 2) and 4 (a call or branch, PC-relative,
at r_offset + 2). Any other kind stops the build: build_module.py also links the module at a real host's addresses
and checks that place() makes exactly that image.

  fext_reloc.py info IMG                      what the image needs (its slot size, its windows)
  fext_reloc.py place IMG OUT --slot OFF,MAX,XIP --win LO,HI LO,HI LO,HI   (CODE, DMA, RAM) the image moved: OUT
"""
import struct
import sys
import zlib

MAGIC = 0x54584546                                 # "FEXT"
IMG_MAGIC = b"FXIM"
T_FLASH, T_CODE, T_DMA, T_RAM, T_NONE = range(5)   # a word's target; CODE..RAM are fext.h's FEXT_W_* + 1
NAMES = ["flash", "CODE", "DMA", "RAM"]
BASES = [0x10000000, 0x20000000, 0x30000000, 0x40000000]   # where make() expects the link (fble.ld's MEMORY)
SPAN = 0x01000000                                  # each region's room at its base
ALIGN = 256                                        # fext.h FEXT_W_ALIGN
# fext.h fext_header: magic, abi, hdr_size, hdr_crc, size, crc, xip, win[3], copy[4], init, caps, name, version
HDR = struct.Struct("<IHHIIIII6I12III8s16s")
O_HDR_CRC, O_SIZE, O_CRC, O_XIP, O_WIN, O_COPY, O_INIT = 12, 16, 20, 24, 28, 52, 100


# ---------------------------------------------------------------- ELF (32-bit, little-endian), just enough ---
class Elf:
    def __init__(self, path):
        b = self.b = open(path, "rb").read()
        if b[:4] != b"\x7fELF" or b[4] != 1 or b[5] != 1:
            raise SystemExit(f"fext: {path}: not a 32-bit little-endian ELF")
        shoff, = struct.unpack_from("<I", b, 0x20)
        shentsize, shnum, shstrndx = struct.unpack_from("<HHH", b, 0x2E)
        self.sec = []
        for i in range(shnum):
            name, typ, flags, addr, off, size, link, info, align, entsize = struct.unpack_from("<10I", b,
                                                                                              shoff + i * shentsize)
            self.sec.append(dict(name=name, type=typ, addr=addr, off=off, size=size, link=link, info=info,
                                 align=align, entsize=entsize))
        names = self.sec[shstrndx]
        for s in self.sec:
            s["name"] = self.cstr(names["off"] + s["name"])
        self.by_name = {s["name"]: s for s in self.sec}
        st = self.by_name[".symtab"]
        strs = self.sec[st["link"]]
        self.syms = []
        for k in range(st["size"] // 16):
            name, value, size, info, other, shndx = struct.unpack_from("<IIIBBH", b, st["off"] + 16 * k)
            self.syms.append(dict(name=self.cstr(strs["off"] + name), value=value, shndx=shndx))
        self.sym = {s["name"]: s["value"] for s in self.syms if s["name"]}

    def cstr(self, o):
        return self.b[o:self.b.index(b"\0", o)].decode()

    def data(self, name):
        s = self.by_name[name]
        return self.b[s["off"]:s["off"] + s["size"]] if s["type"] != 8 else b""   # (SHT_NOBITS: none)

    def relocs(self, name):
        """(offset of the word, type, target address) of section name's relocations"""
        s = self.by_name.get(".rela" + name)
        if not s:
            return []
        out = []
        for k in range(s["size"] // 12):
            off, info, addend = struct.unpack_from("<IIi", self.b, s["off"] + 12 * k)
            typ, sym = info & 0xFF, info >> 8
            y = self.syms[sym]
            target = (y["value"] + addend) & 0xFFFFFFFF if y["shndx"] != 0 else None   # (undefined: a weak 0)
            out.append((off + (2 if typ in (4, 5) else 0), typ, target, y["shndx"] == 0xFFF1))
        return out


def region(a, bases=BASES):
    for k, b in enumerate(bases):
        if b <= a < b + SPAN:
            return k
    return None


# ---------------------------------------------------------------- the build's side ---
def image(elf, bases=BASES):
    """the image of a link at bases (fble.ld's sections and symbols): its bytes with the header filled in (all
    but the CRCs), and the (load address, run address, bytes) of the RAM parts"""
    S = elf.sym
    flash = bases[0]
    img = bytearray(elf.data(".seg0"))
    parts = []
    for sec, start, load in ((".ram_text", "_rt_start", "_rt_load"), (".data", "_data_start", "_data_load")):
        blob = elf.data(sec)
        if blob:
            at = S[load] - flash
            if at < len(img):
                raise SystemExit(f"fext: {sec}'s load image overlaps the code")
            img += b"\xff" * (at - len(img)) + blob
        parts.append((S[load], S[start], len(blob)))
    img += b"\xff" * (-len(img) % 4)
    if S["_fext_end"] - flash != len(img):
        raise SystemExit(f"fext: the image is {len(img)} B, the linker says {S['_fext_end'] - flash}")
    for s in elf.sec:                              # the sections may be moved by FEXT_W_ALIGN: none needs more
        if s["name"] in (".seg0", ".ram_text", ".data", ".bss", ".pool") and s["align"] > ALIGN:
            raise SystemExit(f"fext: {s['name']} wants {s['align']}-byte alignment, more than {ALIGN}")
    win = [(S["_rt_start"], S["_rt_end"]), (S["_data_start"], S["_bss_end"]), (S["_pool_start"], S["_pool_end"])]
    for k, (lo, hi) in enumerate(win):
        if lo != bases[k + 1] and lo != hi:
            raise SystemExit(f"fext: the {NAMES[k + 1]} window starts at {lo:#x}, not at its base {bases[k + 1]:#x}")
    copy = [(S["_rt_start"], S["_rt_load"], S["_rt_end"] - S["_rt_start"]),
            (S["_data_start"], S["_data_load"], S["_data_end"] - S["_data_start"]),
            (S["_bss_start"], 0, S["_bss_end"] - S["_bss_start"]),
            (S["_pool_start"], 0, S["_pool_end"] - S["_pool_start"])]
    copy = [c if c[2] else (0, 0, 0) for c in copy]
    if struct.unpack_from("<I", img, 0)[0] != MAGIC or struct.unpack_from("<I", img, 8)[0] != HDR.size:
        raise SystemExit("fext: the image does not start with an fext_header of this size")
    struct.pack_into("<III", img, O_SIZE, len(img), 0, flash)
    struct.pack_into("<6I", img, O_WIN, *[x for w in win for x in (w if w[0] != w[1] else (0, 0))])
    struct.pack_into("<12I", img, O_COPY, *[x for c in copy for x in c])
    struct.pack_into("<I", img, O_INIT, S["fble_init"])
    return img, parts


def crcs(img):
    hdr = HDR.size
    struct.pack_into("<I", img, O_CRC, zlib.crc32(bytes(img[hdr:])))
    struct.pack_into("<I", img, O_HDR_CRC, 0)
    struct.pack_into("<I", img, O_HDR_CRC, zlib.crc32(bytes(img[:hdr])))
    return img


def make(elf_path, out_path):
    """the .img of a link at BASES (ld -q): the image and its table"""
    elf = Elf(elf_path)
    img, parts = image(elf)
    flash = BASES[0]
    table = []
    sites = [(".seg0", None), (".ram_text", parts[0]), (".data", parts[1])]
    for sec, part in sites:
        base = elf.by_name[sec]["addr"] if sec in elf.by_name else 0
        for off, typ, target, absolute in elf.relocs(sec):
            if typ not in (4, 5, 8):
                raise SystemExit(f"fext: a relocation of kind {typ} in {sec} at {off:#x}: fext_reloc.py knows 4, 5, 8")
            site = region(off)
            at = off - flash if part is None else part[0] + (off - part[1]) - flash   # (its place in the image)
            t = T_NONE if target is None else region(target)
            if t is None:
                if absolute or typ == 8:          # a size, a constant: not an address
                    continue
                raise SystemExit(f"fext: {sec} at {off:#x} points at {target:#x}, outside every region")
            pcrel = typ == 4
            if (pcrel and t == site) or (not pcrel and t == T_NONE):
                continue                          # (it does not move)
            if not 0 <= at < len(img) or at >= 1 << 23:
                raise SystemExit(f"fext: a relocation outside the image ({sec} {off:#x})")
            if at < HDR.size:
                continue                          # (the header: place() moves its addresses itself)
            table.append(at | t << 23 | site << 26 | pcrel << 28)
    crcs(img)
    with open(out_path, "wb") as f:
        f.write(IMG_MAGIC + struct.pack("<III", 1, len(img), len(table)) + bytes(img) + struct.pack(f"<{len(table)}I",
                                                                                                    *table))
    return img, table


# ---------------------------------------------------------------- the installer's side ---
def read(path_or_bytes):
    b = path_or_bytes if isinstance(path_or_bytes, (bytes, bytearray)) else open(path_or_bytes, "rb").read()
    if b[:4] != IMG_MAGIC:
        raise SystemExit("fext: not an fext image (.img: FXIM)")
    fmt, n, k = struct.unpack_from("<III", b, 4)
    if fmt != 1:
        raise SystemExit(f"fext: image format {fmt}: this tool knows 1")
    img = bytearray(b[16:16 + n])
    table = list(struct.unpack_from(f"<{k}I", b, 16 + n))
    return img, table


def needs(img):
    """(the image's bytes, [(lo, hi)] of its windows as linked)"""
    v = struct.unpack_from("<6I", img, O_WIN)
    return len(img), [(v[2 * k], v[2 * k + 1]) for k in range(3)]


def place(img, table, slot):
    """img moved to slot = dict(off, max, xip, win=[(lo, hi)] * 3): the bytes to write at off"""
    img = bytearray(img)
    size, wins = needs(img)
    if size > slot["max"]:
        raise SystemExit(f"fext: the module is {size} B, the host's slot {slot['max']} B")
    old = [struct.unpack_from("<I", img, O_XIP)[0]] + [w[0] for w in wins]
    new = [slot["xip"]]
    for k, (lo, hi) in enumerate(wins):
        hlo, hhi = slot["win"][k]
        if lo == hi:
            new.append(lo)
            continue
        if hlo % ALIGN or hi - lo > hhi - hlo:
            raise SystemExit(f"fext: the module's {NAMES[k + 1]} window is {hi - lo} B, the host keeps "
                             f"{hhi - hlo} B at {hlo:#x}" + (" (not 256-aligned)" if hlo % ALIGN else ""))
        new.append(hlo)
    d = [(n - o) & 0xFFFFFFFF for n, o in zip(new, old)] + [0]
    for e in table:                                # the words that hold addresses
        at, t, site, pcrel = e & 0x7FFFFF, e >> 23 & 7, e >> 26 & 3, e >> 28 & 1
        v = struct.unpack_from("<I", img, at)[0]
        struct.pack_into("<I", img, at, (v + d[t] - (d[site] if pcrel else 0)) & 0xFFFFFFFF)
    # the header: xip, the windows, the copy steps, init
    struct.pack_into("<I", img, O_XIP, slot["xip"])
    struct.pack_into("<6I", img, O_WIN, *[x for k, (lo, hi) in enumerate(wins)
                                          for x in ((lo + d[k + 1]) & 0xFFFFFFFF, (hi + d[k + 1]) & 0xFFFFFFFF)])
    for i in range(4):
        dst, src, n = struct.unpack_from("<III", img, O_COPY + 12 * i)
        if not n:
            continue
        k = next(k for k, (lo, hi) in enumerate(wins) if lo <= dst and dst + n <= hi)
        struct.pack_into("<III", img, O_COPY + 12 * i, (dst + d[k + 1]) & 0xFFFFFFFF, (src + d[0]) & 0xFFFFFFFF if src
                         else 0, n)
    init, = struct.unpack_from("<I", img, O_INIT)
    struct.pack_into("<I", img, O_INIT, (init + d[0]) & 0xFFFFFFFF)
    return crcs(img)


def main():
    a = sys.argv[1:]
    if a and a[0] == "info":
        img, table = read(a[1])
        size, wins = needs(img)
        name, version = (x.rstrip(b"\0").decode() for x in HDR.unpack_from(img)[-2:])
        print(f"{name} {version}: {size} B, {len(table)} relocations; "
              + ", ".join(f"{NAMES[k + 1]} {hi - lo} B" for k, (lo, hi) in enumerate(wins)))
        return 0
    if a and a[0] == "place":
        img, table = read(a[1])
        s = [int(x, 0) for x in a[a.index("--slot") + 1].split(",")]
        w = [tuple(int(x, 0) for x in a[a.index("--win") + 1 + k].split(",")) for k in range(3)]
        out = place(img, table, dict(off=s[0], max=s[1], xip=s[2], win=w))
        open(a[2], "wb").write(out)
        print(f"fext: {len(out)} B for {s[0]:#x} ({s[2]:#x}) into {a[2]}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
