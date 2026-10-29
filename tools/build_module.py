#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# Copyright (C) 2026 Jacek Mariański
"""Build fble, the Bluetooth MIDI module (an fext module: include/fext.h), from fm1-base's ble/ and JieLi's BT
libraries.

  tools/build_module.py      -> build/fble.img (for fext_push.py: the image and its relocations), build/fble.elf,
                                fble.map, fble.sym; build/check/: the same module linked at SLOOP's addresses, the image
                                moved there (fble-sloop.bin) and fext_push.py's write frames for it (the tests')

The BT part is compiled exactly as fm1-base's tools/build.py compiles it for an app with ble = true and
ble_central = true (its radio_units: the same SDK copy, flags, app_config.h edit and libraries); the module's own
file (module/fble_mod.c) takes radio.c's place. Linked twice, with the relocations kept (ld -q): at fext_reloc.py's
made-up bases (the image every host gets, moved at install), and at SLOOP's slot and windows. The first, moved by
fext_reloc.place to SLOOP's, must be the second byte for byte: the check that the table has every address in it.
Needs JIELI_TOOLCHAIN and AC79_SDK (build.sh reads local.env), and fm1-base's patch applied (build.sh does that).
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]           # this repository
FM1B = ROOT / "fm1-base"
OUT = ROOT / "build"
sys.path.insert(0, str(FM1B / "tools"))
sys.path.insert(0, str(ROOT / "tools"))
import build as B  # noqa: E402  (fm1-base's tools/build.py)
import fext_reloc as R  # noqa: E402
import fext_push as P  # noqa: E402

# SLOOP's (the fext build of sloop-fm1: firmware/src/fext_host.h): the check's link, and the size the slot allows
SLOOP = dict(off=0xE7000, max=0x14000, xip=0x020E3000,
             win=[(0x01C06F00, 0x01C07F00), (0x01C08000, 0x01C0E000), (0x01C1E000, 0x01C20000)])

# M5, the diet: each lever by name, all on; FBLE_OFF="name,name" builds without them (to measure one lever's worth:
# docs/BLE_MODULE.md "M5: dieta"). Each is a -D for the shim and the module, or an edit of the SDK's config sources
# (their constants, folded by LTO: the code they switch off goes), or both.
LEVERS = {
    # the libraries' log lines (printf, log_print: their format strings and the calls), not the shim's or the module's
    "liblog": {"D": ["-DSHIM_LIB_LOG=0"], "cfg_D": ["-DCONFIG_LIB_DEBUG_DISABLE"],
               "cfg": [(r"(log_tag_const_\w+\s+AT\(\.LOG_TAG_CONST\)\s*=\s*)1;", r"\g<1>0;")]},
    # BR/EDR's and the test modes' switches (no classic Bluetooth here at all: TCFG_USER_BT_CLASSIC_ENABLE 0)
    "cfg": {"cfg": [(r"(CONFIG_LMP_NAME_REQ_ENABLE\s*=\s*)1;", r"\g<1>0;"),
                    (r"(CONFIG_LMP_PASSKEY_ENABLE\s*=\s*)1;", r"\g<1>0;"),
                    (r"(CONFIG_LMP_MASTER_ESCO_ENABLE\s*=\s*)1;", r"\g<1>0;"),
                    (r"(CONFIG_TEST_DUT_CODE\s*=\s*)1;", r"\g<1>0;"),
                    (r"(CONFIG_TEST_FCC_CODE\s*=\s*)1;", r"\g<1>0;"),
                    (r"(config_multilink_support_esco_enalbe\s*=\s*)1;", r"\g<1>0;")]},
    "heap": {"D": ["-DSHIM_HEAP_BYTES=14336"]},     # measured peak 11 352 at ready (M1, with SM); log: "heap"
    "qlen": {"D": ["-DSHIM_QLEN=128"]},             # ints per task queue (512 in fm1-base); log: "q"
    "stacks": {"D": ["-DSHIM_STACK_WORDS=1024"]},   # 4 KiB per task (8 in fm1-base); log: "stk"
    "ntask": {"D": ["-DSHIM_NTASK=3"]},             # main, btctrler, btstack: btencry is BR/EDR's (lever bredr)
    "ntimer": {"D": ["-DSHIM_NTIMER=24"]},          # timers (48 in fm1-base); log: "tmr"
    "cfg_max": {"D": ["-DSHIM_CFG_MAX=512"]},       # the NV table: address, trim, crystal: ~100 B (1536 in fm1-base)
    # no Security Manager: no pairing, no bonding (BLE MIDI needs none; the central never asks: le_midi.c)
    "sm": {"D": ["-DBLE_MIDI_SM=0"], "cfg": [(r"(config_le_sm_support_enable\s*=\s*)1;", r"\g<1>0;")]},
    # the link layer's encryption (LE_ENCRYPTION in the controller's features): without SM no key is ever made
    "lenc": {"cfg": [(r"(config_btctler_le_features\s*=\s*)\(LE_ENCRYPTION\);", r"\g<1>0;")]},
    # diagnostics (off unless FBLE_ON=stats: the module does not fit SLOOP's slot with them): the high-water report
    # ("hw ..." in the log, every 5 s when anything grew: the diet's sizes checked on the device) and the tasks'
    # states when the radio's start hangs for 3 s
    "stats": {"opt_in": True, "D": ["-DSHIM_STATS"]},
    # measurement only (off unless FBLE_ON=central): the controller without the peripheral's roles. le_midi.c still
    # sets up both roles, so this image is not a working one: it prices the peripheral (docs, "M5: dieta")
    "central": {"opt_in": True, "cfg": [(r"(config_btctler_le_roles\s*=\s*)\([^)]*\);",
                                         r"\g<1>(LE_SCAN | LE_INIT | LE_MASTER);")]},
    # BR/EDR, audio, TWS, ISO, the classic profiles and their crypto: their objects out of the libraries (copies),
    # what the BLE code still names of them answered by module/fble_stubs.c (each stub's reason there)
    "bredr": {"drop": {
        "btctrler.a": r"^(au_law_codec|audio_codec|authentication|bredr_\w+|esco_codec|h4_uart_\w+|h4_usb_\w+|"
                      r"link_bulk|lmp\w*|local_media_sync|tws_\w+|ll_bis\w*|ll_cis\w*|ll_common_iso|cte_data_algorithm|"
                      r"ll_cte)\.c\.o$",
        "btstack.a": r"^(a2dp\w*|ams_client|ancs_client|avctp\w*|avdtp|avrcp|big_\w+|bnep|bt_audio|hfp\w*|hid|"
                     r"iap_profile|mesh_\w+|multi_bd|obex|opp|pan|pbg_profile|rfcomm|sdp\w*|spp_\w+|tws_host|vCard|"
                     r"wl_mic_\w+|btstack_3th_protocol_user|remote_device_db)\.c\.o$",
        "crypto_toolbox_Osize.a": r"^(bigint|uECC_new|ecdh|Crypto_hash|hmac|sha256|test_new_ecc|\w+_sample_data)\.c\.o$",
    }, "src": ["fble_stubs.c"], "D": ["-DFBLE_NO_BREDR"]},
}
OFF = {x for x in os.environ.get("FBLE_OFF", "").replace(" ", "").split(",") if x}
OPT = {x for x in os.environ.get("FBLE_ON", "").replace(" ", "").split(",") if x}
if (OFF | OPT) - set(LEVERS):
    raise SystemExit(f"fble: FBLE_OFF / FBLE_ON {sorted((OFF | OPT) - set(LEVERS))}: no such lever (of {sorted(LEVERS)})")
ON = [k for k in LEVERS if k not in OFF and (not LEVERS[k].get("opt_in") or k in OPT)]


def diet_libs(libs):
    """the libraries, copies without the objects a lever drops (llvm-ar: the copy keeps a bitcode symbol table)"""
    drop = {}
    for k in ON:
        for lib, rx in LEVERS[k].get("drop", {}).items():
            drop.setdefault(lib, []).append(rx)
    if not drop:
        return libs
    import shutil
    ar = B.toolchain() / "common" / "bin" / "llvm-ar"
    d = OUT / "ble" / "lib_diet"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    out = []
    for x in libs:
        if isinstance(x, Path) and x.name in drop:
            members = B.subprocess.run([str(ar), "t", str(x)], capture_output=True, text=True,
                                       check=True).stdout.split()
            gone = [m for m in members if any(re.search(rx, m) for rx in drop[x.name])]
            y = d / x.name
            shutil.copy(x, y)
            B.subprocess.run([str(ar), "d", str(y), *gone], check=True)
            B.subprocess.run([str(ar), "s", str(y)], check=True)
            print(f"fble     {x.name}: {len(gone)} of {len(members)} objects out")
            x = y
        out.append(x)
    return out


def diet_units(units):
    """the levers into the compile commands: -D for every BT unit, the SDK's config sources edited (copies)"""
    D = [d for k in ON for d in LEVERS[k].get("D", [])]
    cfg_D = [d for k in ON for d in LEVERS[k].get("cfg_D", [])]
    edits = [e for k in ON for e in LEVERS[k].get("cfg", [])]
    cfgdir = OUT / "ble" / "cfg"
    cfgdir.mkdir(parents=True, exist_ok=True)
    out = []
    for u in units:
        u = list(u)
        if u[0] == "cc" and "-c" in u:
            src = Path(u[u.index("-c") + 1])
            if src.suffix == ".c" and any(str(src).endswith(s) for s in B.BLE_SDK_SRC):
                text = src.read_text(errors="ignore")
                for old, new in edits:
                    text = re.sub(old, new, text)
                dst = cfgdir / src.name
                dst.write_text(text)
                u[u.index("-c") + 1] = dst
                u[1:1] = cfg_D
            u[1:1] = D
        out.append(tuple(u))
    return out


def ld_script(name, flash, win):
    """module/fble.ld with these origins"""
    text = (ROOT / "module" / "fble.ld").read_text()
    for region, at in (("FLASH", flash), ("CODE", win[0]), ("DMA", win[1]), ("RAM", win[2])):
        text, n = re.subn(rf"(\b{region}\s+\([rwx]+\)\s*:\s*ORIGIN\s*=\s*)0x[0-9A-Fa-f]+", rf"\g<1>{at:#010x}", text)
        assert n == 1, region
    p = OUT / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def main():
    B.ROOT = ROOT
    B.OUT = OUT
    B.APP = ROOT
    B.CONF = {"ble": True, "ble_central": True, "ble_name": os.environ.get("FBLE_NAME", "FM1_FBLE")}
    B.BLE, B.WIFI = True, False
    OUT.mkdir(parents=True, exist_ok=True)
    units, objs, libs = B.radio_units()
    ble_inc = [u for u in units[1] if isinstance(u, str) and u.startswith("-I")]   # (the SDK's and the shim's)
    mod = OUT / "ble" / "o" / "fble_mod.o"
    units.append(("cc", *B.BLE_CF, *ble_inc, *B.inc(ROOT / "include", ROOT / "module", FM1B / "bsp" / "chip"),
                  "-c", ROOT / "module" / "fble_mod.c", "-o", mod))
    objs.append(mod)
    libc = OUT / "ble" / "o" / "fble_libc.o"           # memcpy, memset, memcmp: felucca-bsp's, not LTO (libcalls)
    units.append(("cc", *B.CFLAGS, *B.FPU, *B.inc(FM1B / "bsp" / "chip"), "-c", FM1B / "bsp" / "chip" / "libc.c",
                  "-o", libc))
    objs.append(libc)
    units = diet_units(units)
    libs = diet_libs(libs)
    for k in ON:
        for f in LEVERS[k].get("src", []):
            o = OUT / "ble" / "o" / (Path(f).stem + ".o")
            units.append(("cc", *B.BLE_CF, *ble_inc, "-c", ROOT / "module" / f, "-o", o))
            objs.append(o)
    print(f"fble     levers: {' '.join(ON) or '(none)'}" + (f"; off: {' '.join(sorted(OFF))}" if OFF else ""))
    B.tc_all(*units)
    lto = ["--plugin", str(B.toolchain() / "pi32v2/bin/LLVMgold.so"), f"--plugin-opt=mcpu={B.CPU}",
           "--plugin-opt=-pi32v2-large-program=true"]

    def link(ld, elf, map_):
        B.tc("pi32v2/bin/ld", *lto, "-q", "--gc-sections", "-e", "fble_init", "-u", "fble_hdr", "-L", OUT,
             "-T", ld, "-Map", map_, *objs, *libs, "-o", elf)
    elf = OUT / "fble.elf"
    chk = OUT / "check" / "fble-sloop.elf"
    link(ld_script("fble.ld", R.BASES[0], R.BASES[1:]), elf, OUT / "fble.map")
    link(ld_script("check/fble-sloop.ld", SLOOP["xip"], [w[0] for w in SLOOP["win"]]), chk, OUT / "check" / "fble-sloop.map")
    hdr, syms = B.tc_all(("common/bin/objdump", "-h", elf), ("common/bin/objdump", "-t", elf))
    (OUT / "fble.sym").write_text(syms)
    (OUT / "fble.sections").write_text(hdr)
    img, table = R.make(elf, OUT / "fble.img")
    # the check: the image moved to SLOOP's, and the one linked there
    moved = R.place(img, table, dict(SLOOP, max=1 << 24))   # (whether it fits: below)
    there, _ = R.image(R.Elf(chk), [SLOOP["xip"]] + [w[0] for w in SLOOP["win"]])
    there = R.crcs(there)
    (OUT / "check" / "fble-sloop.bin").write_bytes(moved)
    (OUT / "check" / "fble-sloop.frames").write_bytes(b"".join(f for _, f in P.write_frames(moved, SLOOP["off"])))
    if moved != there:
        diff = [i for i in range(min(len(moved), len(there))) if moved[i] != there[i]]
        raise SystemExit(f"fble: moved to SLOOP's, the image differs from the one linked there: {len(diff)} bytes, "
                         f"first at {diff[0] if diff else min(len(moved), len(there)):#x} (sizes {len(moved)}, "
                         f"{len(there)})")
    size, wins = R.needs(img)
    print(f"fble     image {size} B ({len(table)} relocations, in the file only); SLOOP's slot {SLOOP['max']} B: "
          f"{SLOOP['max'] - size} B free")
    print("fble     RAM: " + ", ".join(f"{R.NAMES[k + 1]} {hi - lo} B (SLOOP keeps {SLOOP['win'][k][1] - SLOOP['win'][k][0]})"
                                       for k, (lo, hi) in enumerate(wins)))
    print(f"fble     moved to SLOOP's slot and windows: the same bytes as linked there; {OUT / 'fble.img'}")
    return 0 if size <= SLOOP["max"] else 1


if __name__ == "__main__":
    sys.exit(main())
