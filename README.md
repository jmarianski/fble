# fble: plugins and Bluetooth MIDI for FM-1 firmwares

Work in progress, nothing released. Where this is going:

- **[fwpatch](docs/PATCHER-PLAN.md): the plugin socket put into a ready firmware, with no rebuild.** It takes a
  firmware's `.fwsc`, checks that there is room (flash, RAM), finds the places a socket hooks into (the main loop,
  MIDI out, the options pages, the PC-limit guard) and appends the socket after the image. It works per release,
  from a recipe checked once against a build with symbols: function signatures change from one release to the next.
  Feasible for the SLOOP and Felucca lineages (SLOOP 2.6, 2.8.x, Felucca 1.5-1.7 and close forks); a firmware that
  is too full is refused up front. Done so far: the `.fwsc` reader and writer, a pi32v2 decoder, the feasibility
  analyzer (`tools/fwpatch/`), and on an FM-1 a call in SLOOP 2.8.3's main loop routed through appended code
  (with a negative control: it faults unless the guard is raised).
- **[fble2](docs/FBLE2-PLAN.md): a Bluetooth MIDI plugin for that socket**, on the open BLE stack of
  [Optimist](https://github.com/w0ts/optimist) (the radio drivers as in
  [felucca-bsp](https://github.com/hugelton/felucca-bsp)), with none of JieLi's libraries: MPL-2.0, so it goes with
  GPL firmwares and others. A device for a computer or phone, a central to BLE MIDI keyboards: 34-54 KB. Done so
  far: it compiles and links (`module2/`, `tools/build_fble2.py`); not run on hardware yet.
- A firmware whose author takes the socket in source needs no patcher: our forks of
  [SLOOP](https://github.com/jmarianski/sloop-fm1) and [Felucca](https://github.com/jmarianski/Felucca) have it.

## fble 1: the module on JieLi's libraries

The first module, below, works (SLOOP with the socket, BLE MIDI both ways with a keyboard's dongle), but about three
quarters of it is JieLi's binary-only Bluetooth libraries from the AC79 SDK, so it can not be part of a GPL firmware,
only sit beside one. fble2 replaces it.

> **Warning: installing fble 1 erases the FM-1's radio calibration.** Its slot starts at `0x0E7000` (SLOOP) or
> `0x0E8000` (Felucca), where the stock firmware keeps its settings store (the JieLi VM, `55 AA AA 55`) with the
> radio's RF trims and the Bluetooth address. Optimist reads those trims and will not start the radio without them
> (it shows NO CAL). If you installed fble, put the 12 KiB at `0x0E7000`-`0x0E9FFF` back from a backup of **your own**
> unit taken before (each unit has its own trims): M-Flasher's restore writes only the sectors that differ, so a copy
> of your current flash with that range taken from the old backup changes just those three sectors. fble2's slot
> starts after it, at `0x0EA000`.

fble adds Bluetooth MIDI to an FM-1 firmware that has an **fext socket**. It is a module, not a firmware. You
install it into the flash on its own, and the firmware loads it at boot and runs it from there. The firmware links
none of it and knows nothing of Bluetooth. The module brings its own pages, settings and MIDI ports through the
interface in [`include/fext.h`](include/fext.h).

With it the FM-1:

- is a **BLE MIDI device** for a computer, phone or tablet (peripheral, port 0);
- connects to a **BLE MIDI keyboard or controller** (central, port 1). It can take the first one it finds (FIRST),
  or one you pick from a list (PICK), and it reconnects when the device comes back;
- sends the firmware's MIDI out to either link (OUT).

Firmwares with the socket so far (`fext_push.py push` asks the firmware where its slot is and moves the module
there):

- SLOOP: its `fext` build (`tools/build.py --fext`), the module in USR4 (0xE7000). Branch `fext` of
  <https://github.com/jmarianski/sloop-fm1>, meant for <https://github.com/isod89/sloop-fm1>.
- Felucca: `FELUCCA_FEXT=1 ./build.sh` (`build/felucca-fext.fwsc`), the module at 0xE8000 above the FM6 voice bank.
  Branch `fext` of <https://github.com/jmarianski/Felucca> (with felucca-bsp's branch `fext`,
  <https://github.com/jmarianski/felucca-bsp>), meant for <https://github.com/hugelton/Felucca>.

The PC setting (BLE 2, first knob) is off at first: a computer sees the FM-1 only once it is on.

## Pages (GLO → BLE, BLE 2; in Felucca after SONG)

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
