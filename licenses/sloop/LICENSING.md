# Felucca licensing

Felucca is free software. Its **code** is licensed under the GNU General Public License,
version 3 only (`GPL-3.0-only`, full text in `LICENSE`). Its **assets** are not part of
that licence: the icon atlas `assets/icons.png`, the panel image `docs/panel.jpg` and the drum sounds made by
`tools/gen_waves.py` (the Hügelton Sample Pack) are Copyright (C) 2026 Hügelton Instruments,
all rights reserved. Their licence terms will be published later. SLOOP's firmware does not contain the
Hügelton Sample Pack: its sampled drum kit is made of CC0 recordings (`assets/samples-cc0/KIT`), and
`gen_waves.py` only feeds the SLICE engine's demo loop, which SLOOP does not build.

Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments

## What is code (GPL-3.0-only)

Every file in this tree that carries an `SPDX-License-Identifier: GPL-3.0-only` header (a few source files
carry another: `firmware/src/fm6_core.c`, msfa, Apache-2.0, the USB MIDI host and the fext module socket, MPL-2.0, all below; the
firmware built with them is GPL-3.0-only as a whole):

- the firmware: `firmware/` (app, HAL, update loader)
- the build script and tools: `build.sh`, `tools/`
- the web pages (installer, editor) and their tests: `web/` (not the Fukiai font, below)
- the host tests: `tests/`

You may use, study, change and share it under the GPL. If you distribute Felucca, or
firmware derived from it, you must also give your recipients its complete corresponding
source under the same licence. That includes devices that ship with modified Felucca
inside.

## USB MIDI host (MPL-2.0)

SLOOP 2.9's USB MIDI host is Adrien Tibi's (Recursive Field Instruments) and Kerem Kilic's (Ellic Studio,
keremimo), written for [Melodee](https://github.com/keremimo/melodee) and taken from felucca-bsp's pull request
[#7](https://github.com/hugelton/felucca-bsp/pull/7) (its commits `ea3d2e3` and `4f5d2b5`), where it is MPL-2.0:

- `firmware/src/usb_host.c`, `firmware/src/usb_host_desc.h`, `tests/usb_host_test.c`: MPL-2.0 (their headers);
- the host routines in `firmware/hal/fm1_usb.h` (`fm1_usb_host_attach`, `fm1_usb_line`): MPL-2.0, in a GPL-3.0-only file.

The full text is in `LICENSES/MPL-2.0.txt`. MPL-2.0 names the GPL as a secondary licence (section 3.3) and these
files are not marked incompatible with it, so a SLOOP firmware that contains them is GPL-3.0-only as a whole, while
the files themselves stay MPL-2.0: their changes are offered under MPL-2.0 too. SLOOP's integration around them
(`usb.c`, `main.c`, `seq.c`, the GLO → USB page) is GPL-3.0-only.

Melodee's own copies are GPL-3.0-only. The authors licensed their parts of the port under MPL-2.0, in their words:
Adrien Tibi in Melodee pull request [#75](https://github.com/keremimo/melodee/pull/75#issuecomment-6384184014)
("For the licensing, of course, feel free to do so :)", 2026-10-24, answering keremimo's request to licence his
part as MPL), and Kerem Kilic in felucca-bsp pull request #7's description ("I also license my contributions to
this port under MPL-2.0."). The Melodee commits the port was made from (at `e9eab97`): `0d705bf` (Adrien's
enumeration, hub handling, input, PHY setup, descriptor parser and SIE / bus tests), `198c8fd` (Adrien's input
toggle and retry fixes), `b4380f9` (Kerem's MIDI output, endpoint checks, ACK ownership and recovery tests),
`eb57309` (Kerem's disconnect debounce), `44a311a` (Kerem's idle probe and hub connection-change fixes); felucca-bsp's
`NOTICE` has their full hashes. The register sequences follow the public JieLi AC79 SDK; no SDK source is copied.

## Module socket (FELUCCA_FEXT, MPL-2.0)

The socket for an fext module, a program written into the flash on its own and loaded at boot (`tools/build.py
--fext`; not in the default build), is Jacek Mariański's, MPL-2.0. The firmware knows nothing of what a module does:
the module brings its own pages, settings and MIDI ports through the interface in `fext.h`. The first module is fble
(Bluetooth MIDI: its own repository, a separate work under its own licences).

- `firmware/src/fext.h`, `firmware/src/fext_load.h` (the module interface and the loader's check, copies of fble's
  `include/`), `firmware/src/fext_host.c`, `firmware/src/fext_host.h` (where SLOOP keeps a module),
  `firmware/src/fext_text.h` (a module's text as the font draws it), `tests/fext_test.c`: MPL-2.0 (their headers).
  `fext_host.c`'s log and interrupt attach follow Charles Vestal's fm1-base `device/radio.c`, MPL-2.0;
- `firmware/hal/fm1_clock.h` and `firmware/hal/fm1_power.h` (in the fext build only): fm1-base's
  `bsp/chip/fm1_clock.h` (Charles Vestal) and the power setup of its `bsp/chip/fm1_sys.h`, MPL-2.0 (their headers);
- the hooks around it (`main.c`, `felucca.c`, `usb.c`, `audio.c`, `editor.c`, `params.c`, `ui_draw.c`, `ui_input.c`,
  `hal/fm1_guard.h`, `hal/fm1_flash.h`, `hal/fm1_irq.h`, `firmware/app_fext.ld.in`, `tools/build.py`): GPL-3.0-only, as the files.

As with the USB MIDI host, a firmware containing them is GPL-3.0-only as a whole. A module is not part of this tree
and is not linked into the firmware: it is built, distributed and installed on its own (its installer moves it to
the slot and windows the firmware reports), and its licence is its authors'.

## Radio

Felucca never enables the Bluetooth / Wi-Fi radio of the hardware in its default build. Only an fext build
(`tools/build.py --fext`) with a separately installed module that uses the radio (fble), and its RADIO set to ON by
the user (GLO → BLE), turns on the Bluetooth radio; the Wi-Fi radio is never enabled.
