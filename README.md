# fble: Bluetooth MIDI for the M-VAVE FM-1

fble adds Bluetooth MIDI to an FM-1 firmware that has an **fext socket**. It is a module, not a firmware. You
install it into the flash on its own, and the firmware loads it at boot and runs it from there. The firmware links
none of it and knows nothing of Bluetooth. The module brings its own pages, settings and MIDI ports through the
interface in [`include/fext.h`](include/fext.h).

With it the FM-1:

- is a **BLE MIDI device** for a computer, phone or tablet (peripheral, port 0);
- connects to a **BLE MIDI keyboard or controller** (central, port 1). It can take the first one it finds (FIRST),
  or one you pick from a list (PICK), and it reconnects when the device comes back;
- sends the firmware's MIDI out to either link (OUT).

Firmwares with the socket so far: SLOOP (its `fext` build: `tools/build.py --fext`), with the module in USR4.

## Pages (GLO → BLE, BLE 2 in SLOOP)

| Page | Knob 1 | Knob 2 | Knob 3 | Knob 4 |
| --- | --- | --- | --- | --- |
| BLE | RADIO on / off | MODE: OFF, FIRST, PICK | LIST: the device to pick | USE: connect to it |
| BLE 2 | PC: the computer link | RECON: reconnect | OUT: MIDI out over BLE | |

The middle of the page shows the state and the devices found, with their signal strength. The radio is off until
you turn RADIO on.

## Build

You need JieLi's pi32v2 toolchain and the JieLi AC79 SDK
([gitee.com/Jieli-Tech/fw-AC79_AIoT_SDK](https://gitee.com/Jieli-Tech/fw-AC79_AIoT_SDK)). fm1-base's
`tools/get_toolchain.sh` fetches the toolchain. Either export `JIELI_TOOLCHAIN` and `AC79_SDK`, or put them in a
`local.env` beside `build.sh` (it is not in git):

```sh
git clone --recursive <this repository>
cd fble
sh build.sh            # build/fble.img, then the tests
```

`build/fble.img` holds the module and its relocation table. The module is linked at made-up addresses, and the
installer moves it to the slot and RAM windows the firmware reports, so one image fits every host. The build also
links it at SLOOP's addresses and checks that moving it gives the same bytes (`tools/fext_reloc.py`).

## Install

Connect the FM-1 over USB, running a firmware with the socket. Close any editor that holds its MIDI port.

```sh
tools/fext_push.py push               # asks the FM-1 where, moves the module there, writes it, loads it
tools/fext_push.py status             # the module's state
tools/fext_push.py log                # the firmware's log of the module
tools/fext_push.py remove             # take it out again
```

If a module is already running, `push` restarts the FM-1 without it first. See `tools/fext_push.py` for the other
commands.

## The interface (fext)

[`include/fext.h`](include/fext.h) is the contract between a firmware (the host) and a module. It is meant for other
modules too, not only Bluetooth:

- **Header:** the module's size, CRCs, the RAM it needs (code, DMA and plain RAM windows), what the loader copies,
  and its entry point.
- **Host table:** time, a log, the watchdog, interrupts, stored settings, messages on screen.
- **Module table:** a poll call, MIDI rings by port, its pages as data (columns, knobs, lines), and a command
  channel.

[`include/fext_load.h`](include/fext_load.h) is the check a host runs before it loads a module.

## Licence

MPL-2.0 for this repository's files (`LICENSES/MPL-2.0.txt`). A built module also contains JieLi's Bluetooth
libraries (Apache-2.0, binary only). See [`NOTICE`](NOTICE) for every part and its authors.
