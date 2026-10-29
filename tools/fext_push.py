#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# Copyright (C) 2026 Jacek Mariański
"""Install an fext module (include/fext.h) into an FM-1 firmware with the socket, over USB-MIDI, and talk to it.
No Python packages: the ALSA raw MIDI device (/dev/snd/midiC<card>D0), found by its name in /proc/asound.

  fext_push.py status                   the module's state line, or why there is none
  fext_push.py info                     where the host keeps a module: its slot and RAM windows (op 10)
  fext_push.py push [build/fble.img]    ask the host where (info), move the module there (fext_reloc.place), write it
                                        in 256-byte frames, each answered and read back by the FM-1 (the header's
                                        sector last), then load it. A module that runs on its own (its interrupts)
                                        is not rewritten under it: the FM-1 restarts without it first (reboot --noload)
  fext_push.py load                     load it now (the host does at every start anyway)
  fext_push.py remove                   erase the header's sector: no module any more
  fext_push.py reboot [--noload]        restart the FM-1 (--noload: without loading the module)
  fext_push.py control TEXT             a command for the module (its own: fble's S = state, G = settings, R1 / R0 = radio on / off)
  fext_push.py knob PAGE COL STEPS      turn a knob of the module's pages (0 / 1, column 0..3, steps -63..63)
  fext_push.py log                      the host's log (F0 7D 4C 47: the last crash, the clocks, the module's lines)
  fext_push.py frames IMG.bin OFF OUT   (no FM-1) the write frames for a module already moved to the slot at OFF
Options: --port N (the card number, from amidi -l: hw:N,0,0), -v (every frame).
The protocol: F0 7D 46 42 op ... F7 (SLOOP's firmware/src/fext_host.c).
"""
import os
import select
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fext_reloc as R  # noqa: E402

HDR = bytes([0xF0, 0x7D, 0x46, 0x42])
LOG = bytes([0xF0, 0x7D, 0x4C, 0x47])
ERR = {0: "ok", 1: "outside the module's slot", 2: "erase failed", 3: "write failed", 4: "read back differs",
       5: "malformed frame", 6: "the module runs"}
CHUNK = 256
NAMES = ("FM-1", "MIDIMON", "Felucca", "SLOOP")


def pack7(b):
    out = bytearray()
    for i in range(0, len(b), 7):
        g = b[i:i + 7]
        out.append(sum(((x >> 7) & 1) << k for k, x in enumerate(g)))
        out += bytes(x & 0x7F for x in g)
    return bytes(out)


def unpack7(b):
    out = bytearray()
    for i in range(0, len(b), 8):
        hi, g = b[i], b[i + 1:i + 8]
        out += bytes(x | ((hi >> k) & 1) << 7 for k, x in enumerate(g))
    return bytes(out)


def write_frames(data, off):
    """[(offset, frame)] for the moved module at off: the header's sector last (its first frame erases it, so a
    write cut short leaves no header that passes, rather than one over a half-written module)"""
    chunks = [(off + k, data[k:k + CHUNK]) for k in range(0, len(data), CHUNK)]
    chunks = [c for c in chunks if c[0] >= off + 0x1000] + [c for c in chunks if c[0] < off + 0x1000]
    return [(o, HDR + bytes([2, o & 0x7F, (o >> 7) & 0x7F, (o >> 14) & 0x7F]) + pack7(c) + b"\xF7")
            for o, c in chunks]


def find_card(card=None, wait=0.0):
    end = time.time() + wait
    while True:
        if card is not None:
            if os.path.exists(f"/dev/snd/midiC{card}D0"):
                return card
        else:
            for line in open("/proc/asound/cards"):
                p = line.split()
                if p and p[0].isdigit() and any(k in line for k in NAMES):
                    return int(p[0])
        if time.time() >= end:
            sys.exit("fext_push: no FM-1 among /proc/asound/cards (--port N)")
        time.sleep(0.25)


class Midi:
    def __init__(self, card, verbose=False):
        self.fd = os.open(f"/dev/snd/midiC{card}D0", os.O_RDWR | os.O_NONBLOCK)
        self.buf, self.verbose = bytearray(), verbose

    def close(self):
        os.close(self.fd)

    def send(self, b):
        if self.verbose:
            print("->", b[:12].hex(" "), "..." if len(b) > 12 else "")
        view = memoryview(b)
        while view:
            try:
                n = os.write(self.fd, view)
                view = view[n:]
            except BlockingIOError:
                select.select([], [self.fd], [], 0.5)

    def frames(self, timeout):
        """SysEx frames received until timeout (s) passes without one"""
        end = time.time() + timeout
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], max(0.0, end - time.time()))
            if r:
                try:
                    self.buf += os.read(self.fd, 4096)
                except OSError:                    # (the FM-1 went: a restart)
                    return
            while 0xF0 in self.buf and 0xF7 in self.buf[self.buf.index(0xF0):]:
                s = self.buf.index(0xF0)
                e = self.buf.index(0xF7, s)
                f = bytes(self.buf[s:e + 1])
                del self.buf[:e + 1]
                if self.verbose:
                    print("<-", f.hex(" "))
                yield f
                end = time.time() + timeout

    def ask(self, frame, want, timeout=2.0):
        self.send(frame)
        for f in self.frames(timeout):
            if f.startswith(want):
                return f
        return None

    def op(self, op, args=b"", timeout=2.0):
        return self.ask(HDR + bytes([op]) + bytes(args) + b"\xF7", HDR + bytes([op]), timeout)


def text(f, skip=5):
    return bytes(f[skip:-1]).decode("latin-1")


def info(m):
    f = m.op(10)
    if not f:
        return None
    raw = unpack7(f[5:-1])
    if len(raw) < 40:
        sys.exit("fext_push: a short info answer: " + f.hex(" "))
    v = struct.unpack_from("<10I", raw)
    return dict(abi=(v[0] & 0xFFFF, v[0] >> 16), off=v[1], max=v[2], xip=v[3],
                win=[(v[4 + 2 * k], v[5 + 2 * k]) for k in range(3)])


def reboot(m, card, noload):
    m.op(6, [1 if noload else 0])
    m.close()
    time.sleep(1.5)                                # (the old device gone first)
    card = find_card(card, wait=20.0)
    time.sleep(0.5)
    return Midi(card, m.verbose)


def push(m, card, path):
    s = info(m)
    if not s:
        sys.exit("fext_push: no answer to info (a firmware with the fext socket? the editor's MIDI port closed?)")
    img, table = R.read(path)
    major = struct.unpack_from("<H", img, 4)[0]
    if major != s["abi"][0]:
        sys.exit(f"fext_push: the module's ABI {major}, the host's {s['abi'][0]}.{s['abi'][1]}")
    data = R.place(img, table, s)
    print(f"fext_push: {len(data)} B for the slot at {s['off']:#x} ({s['xip']:#010x}), "
          + ", ".join(f"{R.NAMES[k + 1]} at {lo:#010x}" for k, (lo, hi) in enumerate(s["win"])))
    fr = write_frames(data, s["off"])
    t0, k = time.time(), 0
    while k < len(fr):
        off, f = fr[k]
        a = m.ask(f, HDR + b"\x02", timeout=3.0)
        if a and a[5] == 6 and k == 0:
            print("fext_push: the module runs: restarting the FM-1 without it")
            m = reboot(m, card, True)
            continue
        if not a or a[5] != 0:
            print(f"\nfext_push: {off:#x}: " + (ERR.get(a[5], f"error {a[5]}") if a else "no answer"))
            return m, 1
        k += 1
        if k % 16 == 0 or k == len(fr):
            print(f"\r{k}/{len(fr)} frames, {off:#x}", end="", flush=True)
    print(f"\nfext_push: {sum(len(f) for _, f in fr)} B of frames in {time.time() - t0:.1f} s")
    a = m.op(3, timeout=5.0)
    print("fext_push: load: " + (text(a) if a else "no answer"))
    return m, 0 if a and text(a) == "loaded" else 1


def main():
    args = sys.argv[1:]
    verbose = "-v" in args
    args = [a for a in args if a != "-v"]
    card = None
    if "--port" in args:
        i = args.index("--port")
        card = int(args[i + 1])
        del args[i:i + 2]
    cmd = args[0] if args else "status"
    if cmd == "frames":
        data = open(args[1], "rb").read()
        fr = write_frames(data, int(args[2], 0))
        with open(args[3], "wb") as out:
            for _, f in fr:
                out.write(f)
        print(f"fext_push: {len(fr)} frames into {args[3]}")
        return 0
    if cmd not in ("status", "info", "push", "load", "remove", "reboot", "control", "knob", "log"):
        print(__doc__)
        return 2
    card = find_card(card)
    m = Midi(card, verbose)
    if cmd == "push":
        here = os.path.dirname(os.path.abspath(__file__))
        m, r = push(m, card, args[1] if len(args) > 1 else os.path.join(here, "..", "build", "fble.img"))
        return r
    if cmd == "info":
        s = info(m)
        if not s:
            print("fext_push: no answer")
            return 1
        print(f"ABI {s['abi'][0]}.{s['abi'][1]}; slot {s['off']:#x} +{s['max']:#x} at {s['xip']:#010x}; "
              + ", ".join(f"{R.NAMES[k + 1]} {lo:#010x}..{hi:#010x}" for k, (lo, hi) in enumerate(s["win"])))
        return 0
    if cmd == "log":
        m.send(LOG + b"\xF7")
        n = 0
        for f in m.frames(1.5):
            if f.startswith(LOG):
                t = text(f, 4)
                print(t)
                n += 1
                if t == "end":
                    break
        return 0 if n else 1
    if cmd == "reboot":
        f = m.op(6, [1 if "--noload" in args else 0])
    elif cmd == "control":
        f = m.op(7, " ".join(args[1:]).encode("ascii"))
    elif cmd == "knob":
        f = m.op(9, [int(args[1]), int(args[2]), max(1, min(127, int(args[3]) + 64))])
    else:
        f = m.op({"status": 1, "load": 3, "remove": 5}[cmd], timeout=5.0)
    print(text(f) if f else "fext_push: no answer")
    return 0 if f else 1


if __name__ == "__main__":
    sys.exit(main())
