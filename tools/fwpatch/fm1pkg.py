# SPDX-License-Identifier: MPL-2.0
# Copyright (C) 2026 Jacek Mariański
"""Read (and write back) an FM-1 update package (.fwsc): the app image a firmware runs, and everything around it.

Written from the format as SLOOP / Felucca's tools/fm1pkg_make.py writes it (and fble's web/fm1pkg.js reads it):
  .fwsc   the UFW container with one marker byte after each of its first 20 blocks of 0x2F bytes (the product id)
  UFW     a 0x40 header and 0x50 file entries, each ENC'd with 0xFFFF; files: flash.bin (type 0), ota.bin (100)
  flash   [0, 0x4000): the flash header and JLFS entries (ENC 0xFFFF per 32 B), the SPL, isd_config (the chip key);
          0x4000..: the app area, SFC-encrypted with the chip key: app_area_head, then the entries app.bin,
          cfg_tool.bin, the region descriptors (VM, PRCT, BTIF, USR)
  app.bin runs in place at 0x02000120 (the XIP address of app area + 0x120)
All CRCs are CRC16 (0x1021, init 0). Nothing here is the firmware's code: only the package's layout.
"""
import struct
from dataclasses import dataclass, field

APP_XIP = 0x02000120


def _table():
    t = []
    for i in range(256):
        c = i << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) if c & 0x8000 else (c << 1)
        t.append(c & 0xFFFF)
    return t


_T = _table()


def crc16(data, crc=0):
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ _T[((crc >> 8) ^ b) & 0xFF]
    return crc


def enc(buf, off, size, key=0xFFFF):
    """JieLi's ENC stream (its own inverse)"""
    for i in range(size):
        buf[off + i] ^= key & 0xFF
        key = ((key << 1) ^ (0x1021 if key & 0x8000 else 0)) & 0xFFFF


def sfc(buf, off, size, key):
    """SFC: ENC per 32-byte block, the key mixed with the block's offset in the region (its own inverse)"""
    for i in range(0, size, 32):
        enc(buf, off + i, min(size - i, 32), key ^ (i >> 2))


def chip_key(d):
    s = sum(d[:16]) & 0xFF
    s = 0xAA if s >= 0xE0 else 0x55 if s <= 0x10 else s
    return sum(1 << i for i in range(16) if (d[16 + i] ^ d[15 - i]) < s)


@dataclass
class Entry:
    crc: int
    dcrc: int
    offset: int
    size: int
    flags: int
    resvd: int
    index: int
    raw_name: bytes

    @property
    def name(self):
        return self.raw_name.split(b"\0")[0].decode("latin-1")

    @classmethod
    def parse(cls, b):
        crc, = struct.unpack_from("<H", b, 0)
        dcrc, off, size, flags, resvd, index, nm = struct.unpack_from("<HIIBBH16s", b, 2)
        return cls(crc, dcrc, off, size, flags, resvd, index, nm)

    def pack(self):
        body = struct.pack("<HIIBBH16s", self.dcrc, self.offset & 0xFFFFFFFF, self.size & 0xFFFFFFFF, self.flags,
                           self.resvd, self.index, self.raw_name)
        return struct.pack("<H", crc16(body)) + body

    def ok(self, raw32):
        return crc16(raw32[2:32]) == self.crc


@dataclass
class Package:
    product: str
    files: list                      # [(type, name, bytes)]
    flash: bytearray = None
    key: int = 0
    head: list = field(default_factory=list)       # JLFS entries in the flash head
    area_off: int = 0x4000
    area: bytearray = None                         # the app area, decrypted (app_area_head .. the cfg dir)
    area_entries: list = field(default_factory=list)
    app_entry: Entry = None
    notes: list = field(default_factory=list)

    @property
    def app(self):
        e = self.app_entry
        return bytes(self.area[e.offset:e.offset + e.size])

    def app_used(self):
        """the app image without its 0xFF fill (rounded up to 4)"""
        a = self.app
        n = len(a)
        while n and a[n - 1] == 0xFF:
            n -= 1
        return (n + 3) & ~3

    def region(self, name):
        for e in self.area_entries:
            if e.raw_name[:len(name)] == name.encode() and (len(e.raw_name) == len(name) or
                                                            e.raw_name[len(name)] in (0, 0xFF)):
                return e
        return None


def unmark(raw):
    """the .fwsc marker bytes out: the logical UFW image and the product id"""
    logical, prod = bytearray(), ""
    for i in range(20):
        logical += raw[i * 0x30:i * 0x30 + 0x2F]
        m = raw[i * 0x30 + 0x2F]
        if m != 0x7D:
            prod += chr((m - i - 1) & 0xFF)
    logical += raw[20 * 0x30:]
    return logical, prod


def mark(logical, product):
    raw = bytearray()
    for i in range(20):
        ch = product[i] if i < len(product) else None
        m = (ord(ch) + i + 1) & 0xFF if ch else 0x7D
        raw += logical[i * 0x2F:(i + 1) * 0x2F] + bytes([m])
    raw += logical[20 * 0x2F:]
    return bytes(raw)


def read(raw):
    logical, prod = unmark(raw)
    hdr = bytearray(logical[0:0x40])
    enc(hdr, 0, 0x40)
    if crc16(hdr[2:0x40]) != struct.unpack_from("<H", hdr, 0)[0]:
        raise ValueError("not an FM-1 package: the UFW header's CRC does not match")
    total, n = struct.unpack_from("<IH", hdr, 4)
    files = []
    for i in range(n):
        e = bytearray(logical[0x40 + 0x50 * i:0x90 + 0x50 * i])
        enc(e, 0, 0x50)
        typ, idx, dcrc, _, off, size = struct.unpack_from("<HHHHII", e, 0)
        name = bytes(e[0x40:0x50]).split(b"\0")[0].decode("latin-1")
        d = bytes(logical[off:off + size])
        if crc16(d) != dcrc:
            raise ValueError(f"{name}: CRC mismatch")
        files.append((typ, name, d))
    p = Package(prod, files)
    flash = next((d for t, nm, d in files if nm == "flash.bin"), None)
    if flash is None:
        raise ValueError("no flash.bin in the package")
    p.flash = bytearray(flash)
    head = bytearray(p.flash[0:0x400])
    for o in range(0, len(head), 32):
        enc(head, o, 32)
    for o in range(32, 0x400, 32):                 # (0: the flash header)
        e = Entry.parse(head[o:o + 32])
        if not e.ok(head[o:o + 32]):
            break
        p.head.append(e)
    isd = next((e for e in p.head if e.name.startswith("isd_config")), None)
    if isd is None:
        raise ValueError("no isd_config in the flash head: no chip key")
    p.key = chip_key(p.flash[isd.offset:isd.offset + 32])
    dirh = next((e for e in p.head if e.name == "app_dir_head"), None)
    p.area_off = dirh.offset if dirh else 0x4000
    first = bytearray(p.flash[p.area_off:p.area_off + 32])
    sfc(first, 0, 32, p.key)
    ah = Entry.parse(first)
    if not ah.ok(first) or ah.name != "app_area_head":
        raise ValueError("the app area does not decrypt to app_area_head with the chip key")
    size = ah.size
    # (the cfg directory follows the block: decrypt a little more, it is part of the same SFC stream)
    end = min(len(p.flash) - p.area_off, size + 0x1000)
    p.area = bytearray(p.flash[p.area_off:p.area_off + end])
    sfc(p.area, 0, len(p.area), p.key)
    for o in range(32, 0x120, 32):
        e = Entry.parse(p.area[o:o + 32])
        if not e.ok(p.area[o:o + 32]):
            break
        p.area_entries.append(e)
    p.app_entry = next((e for e in p.area_entries if e.name == "app.bin"), None)
    if p.app_entry is None:
        raise ValueError("no app.bin in the app area")
    if crc16(p.app) != p.app_entry.dcrc:
        p.notes.append("app.bin: CRC mismatch")
    return p


def write(p, app):
    """a new package: `app` in place of app.bin (the same slot), every CRC and the encryption redone"""
    e = p.app_entry
    if len(app) > e.size:
        raise ValueError(f"app is {len(app)} B, the slot is {e.size} B")
    app = bytes(app) + b"\xFF" * (e.size - len(app))
    area = bytearray(p.area)
    area[e.offset:e.offset + e.size] = app
    ents = []
    for x in p.area_entries:
        if x is e:
            x = Entry(0, crc16(app), x.offset, x.size, x.flags, x.resvd, x.index, x.raw_name)
        ents.append(x.pack())
    blob = b"".join(ents)
    area[32:32 + len(blob)] = blob
    ah = Entry.parse(area[0:32])
    area[0:32] = Entry(0, crc16(area[32:ah.size]), ah.offset, ah.size, ah.flags, ah.resvd, ah.index,
                       ah.raw_name).pack()
    sfc(area, 0, len(area), p.key)
    flash = bytearray(p.flash)
    flash[p.area_off:p.area_off + len(area)] = area
    files = [(t, nm, bytes(flash) if nm == "flash.bin" else d) for t, nm, d in p.files]
    off, ents, total = 0x400, [], 0
    for i, (typ, name, d) in enumerate(files):
        off = (off + 0xFF) & ~0xFF
        x = bytearray(0x50)
        struct.pack_into("<HHHHIII", x, 0, typ, i, crc16(d), 0, off, len(d), (len(d) + 31) & ~31)
        x[0x40:0x40 + len(name)] = name.encode()
        ents.append((x, off, d))
        off += len(d)
    total = off
    logical0, _ = unmark(p.raw) if hasattr(p, "raw") else (None, None)
    hdr = bytearray(0x40)
    if logical0 is not None:                       # the header's other fields as the original had them
        hdr[:] = logical0[0:0x40]
        enc(hdr, 0, 0x40)
    struct.pack_into("<IH", hdr, 4, total, len(files))
    lst = bytearray()
    for x, _, _ in ents:
        y = bytearray(x)
        enc(y, 0, 0x50)
        lst += y
    struct.pack_into("<H", hdr, 2, crc16(lst))
    struct.pack_into("<H", hdr, 0, crc16(hdr[2:0x40]))
    enc(hdr, 0, 0x40)
    logical = bytearray(b"\xFF" * total)
    logical[0:0x40] = hdr
    logical[0x40:0x40 + len(lst)] = lst
    for x, o, d in ents:
        logical[o:o + len(d)] = d
    return mark(logical, p.product)


def load(path):
    raw = open(path, "rb").read()
    p = read(raw)
    p.raw = raw
    return p
