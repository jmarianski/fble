# fble2: Bluetooth MIDI for the fext socket on an open BLE stack

Status: **plan plus a prototype that compiles and links. It has not run on an FM-1 and has not been loaded by a
host.** Branch `fble2`. The "What was tested" section below says exactly what was checked.

fble (branch `main`) is built from JieLi's binary Bluetooth libraries through fm1-base's shim. fble2 replaces all
of that with Optimist's own BLE stack (link layer, L2CAP, ATT/GATT, SMP and BLE-MIDI, plus the WL82 baseband
driver) and felucca-bsp's radio headers. The module is still an fext image in the same socket, so any firmware
with the socket (SLOOP, Felucca and their forks) gets Bluetooth MIDI without linking anything.

## 1. Short answer

- **Feasible.** The peripheral (c0), the peripheral with a central but no pairing (c3), and the full set with
  pairing (c1) all compile. Each is placed at the proposed SLOOP slot by `fext_reloc.py`, and the placed image is
  byte-identical to the same module linked there directly. Sizes: 34 KB (c0), 45 KB (c3) and 54 KB (c1). Each
  fits a 68 KiB slot with 15–35 KB to spare.
- **The calibration blocker is gone.** felucca-bsp main (606c9b2 and later, here 305c372) has `fm1_rf_cal.h`, a live
  radio calibration. fm1-base's own stack has been tested with it on an FM-1 that has no factory VM at all (its
  commit 62b1ad5: a Mac connected, MIDI both ways, 0.4% CRC errors). fble2 therefore needs neither the factory
  trims nor a host that can read them. A 1.0 host can run it.
- **What still blocks:** ABI 1.1 on the host side (interrupt levels below audio), moving the slot off the factory
  VM, and above all a hardware bring-up. Interrupt timing against audio, the radio start-up with CPU1 and XIP
  running, and the NV write blocking audio are all untested.
- **Variant (a) is recommended:** the module carries its own copy of the stack and the radio driver. Variant (b)
  saves about 25 KB in the module but costs the same in the host, and SLOOP has no room for it (section 7).
- **Estimated work:** about 2–4 weeks part-time to a usable peripheral on SLOOP and Felucca, plus 1–2 weeks for the
  central (section 11).

## 2. What is in the branch

| Path | What | Licence |
|---|---|---|
| `optimist-ble/ble/` | Optimist's `firmware/src/ble/` (33 files), w0ts/optimist `fcdc4881e878`, unchanged | GPL-3.0-only OR MPL-2.0 (used under MPL-2.0) |
| `bsp-radio/fm1_ble.h`, `fm1_ble_rf.h` | felucca-bsp `chip/` at 305c372 (main): baseband registers, RF start-up | GPL-3.0-only OR MPL-2.0 |
| `bsp-radio/fm1_rf_cal.h` | felucca-bsp `chip/` at 305c372: live calibration (from JieLi's wl_rf_common.a) | Apache-2.0 |
| `bsp-radio/ble_rf_tables_v15.h` | felucca-bsp `chip/` at 305c372: RF start-up values captured from FM-1 V15 | Apache-2.0 |
| `module2/hal/fm1_cc.h`, `fm1_irq.h`, `fm1_time.h` | the module's own few HAL lines the headers need (Optimist's are GPL-only, so they are not copied) | MPL-2.0 |
| `module2/fble2_mod.c` | the module: fext glue, rings, NV, trims or live calibration, the le_midi adapter for `module/fble_ui.c` | MPL-2.0 |
| `module2/fble2.ld` | the link (header, code in XIP; `.ram_text` in CODE; data and bss in DMA) | MPL-2.0 |
| `tools/build_fble2.py` | build, relocation check, sizes (needs only `JIELI_TOOLCHAIN`) | MPL-2.0 |
| `include/fext.h` | ABI 1.1 proposal: three members appended to `fext_host` | MPL-2.0 |
| `module/fble_mod.c` | fble (JieLi) checks the 1.0 size, so it keeps working with a 1.1 host | MPL-2.0 |

Optimist's glue (`io/midi/midi_ble.c`, `ble_devices.c`, `ble_connect.c`) is GPL-3.0-only and **was not used**.
`fble2_mod.c` is written fresh against `ble.h`, Optimist's stack API (the `ble_app_*` callbacks).

## 3. Dependencies of the stack and how the module meets them

| Dependency | In Optimist | In fble2 |
|---|---|---|
| Time base | TIMER4 counter, 24 MHz (`fm1_time.h`) | The same register, read directly (`module2/hal/fm1_time.h`). The host must keep TIMER4 running at 24 MHz; both SLOOP and Felucca do. |
| Millisecond clock | the firmware's `fm1_ms` | `H->ms()` |
| Interrupts | IRQ 45 (BLE event) and 29 (BLE RX), asm wrappers `isr_ble_event` / `isr_ble_rx`, priority 2, below audio | `fm1_irq_attach` mapped to `fble2_irq_attach`. On a 1.1 host this is `irq_attach2(FEXT_IRQ_LOW)`; on a 1.0 host it is `irq_attach(..., 0)`, which SLOOP maps to 5, **above audio** (risk R1). |
| Vector table | the firmware's | the host's (the host attaches the vector) |
| Holding the BLE IRQs | `fm1_ble_irqs_hold` (the controller's enable bits) | `H->irq_hold` when present (1.1), otherwise the same ICFG bits written by the module |
| RAM-resident code | `fm1_rf_burst_run`, `.ram_text`: runs while the radio's current upsets XIP | 36 B in the CODE window. The build checks that it calls nothing in flash. |
| DMA-reachable RAM | baseband block (1508 B) and buffers | the DMA window: 5.8 KB (c0) to 7.9 KB (c1) of SLOOP's 24 KB |
| RF trims | factory VM records 106/107/108/187 (`ble_vm.c`), copied into its store | (1) the VM through `H->flash_read` (1.1), (2) the copy in the module's NV, (3) **otherwise live calibration** (`fm1_rf_cal_on = 1`) with the board's crystal and PA values (`{0x0B,0x0B}`, `{1,7,4,7,11,1,7}`, as in fm1-base 62b1ad5). `-DFBLE2_CAL_ALWAYS=1` always measures. |
| Quiet during the RF start-up | the firmware stops audio itself | `H->quiet(1)` / `quiet(0)` around `ble_hw_wl82_start` (SLOOP masks ALNK0 and TIMER5 and zeroes the buffer) |
| MIDI router | Optimist's router | the fext rings `midi_in` / `midi_out` (64 entries). Port 0 is the computer's link (peripheral), port 1 is the device's link (central). |
| Persistence | Optimist's store | `FEXT_NV_STACK` (up to 1536 B, written immediately): a `struct fble2_nv` blob ("FBN2"), with the address, the trims copy, the peripheral bond and the central's device store. Writes from interrupt context are deferred to the main loop; bonds are saved 2 s later. Settings (`FEXT_NV_SET`, deferred until quiet) use the same path as fble. |
| UI | Optimist's pages | `module/fble_ui.c` unchanged (fble's pages), through an adapter that implements fm1-base's `ble_midi_*` names on top of Optimist's API. The signal column shows Optimist's relative bars 1..3, not dBm. |
| Address | random static, generated and stored | the same: made once, kept in NV |
| Tasks and heap | none | none: the stack runs in its two interrupts and `fble_poll` |
| libc | — | the module's own memcpy, memset, memcmp and strlen |

Limits inherited from Optimist's stack:
- one link at a time (the computer **or** a device, not both);
- scanning pauses advertising; with PC ON and a central mode they alternate every 3 s until one link is up;
- AES is software only.

## 4. ABI 1.1 (proposal, `include/fext.h`)

Three members appended at the end of `fext_host`. `FEXT_ABI_MINOR` stays 0 in this branch until a host implements
them. A module detects them with `FEXT_HOST_HAS(h, member)`, which checks both `size` and a non-null pointer.

```c
int  (*flash_read)(uint32_t off, void *dst, uint32_t n); /* any flash offset: the factory VM (trims), read-only */
void (*irq_attach2)(int index, int level, void (*isr)(void)); /* level relative to the host's audio:
                                                               FEXT_IRQ_HIGH 0 = above audio, FEXT_IRQ_LOW 1 = below */
void (*irq_hold)(int index, int hold);                         /* hold or release a line at the controller */
```

- `irq_attach2` is the one that matters. A 1.0 host can only attach JieLi's priorities 0..2, which SLOOP maps above
  audio (5..7). Optimist runs the BLE ISRs below audio, so audio is never blocked by the radio. The level is relative:
  only the host knows its own priorities.
- `flash_read` becomes optional because of live calibration. It is still useful: a unit with an intact VM then
  starts with the factory trims (the same values the stock firmware uses) and skips measuring.
- `irq_hold` lets the host track the line state. Without it, the module writes the ICFG enable bits directly.

Sketch of the host side (SLOOP `firmware/src/fext_host.c`; not applied anywhere):

```c
static int fh_flash_read(uint32_t off, void *dst, uint32_t n)
{
    if (off >= 0x100000u || n > 0x100000u - off) return -1;
    memcpy(dst, fm1_xip_ptr(off), n);            /* the plain XIP window covers 0x93000 and up */
    return 0;
}
static void fh_irq_attach2(int index, int level, void (*isr)(void))
{   /* audio (ALNK0) and TIMER5 sit at 4: LOW -> 2 (as Optimist), HIGH -> 5 */
    ... as fh_irq_attach, with (level == FEXT_IRQ_LOW ? 2u : 5u)
}
static void fh_irq_hold(int index, int hold) { hold ? fm1_irq_mask(index) : fm1_irq_unmask(index); }
/* fext_host: .size = sizeof(fext_host), .abi_minor = 1, ..., .flash_read = fh_flash_read,
   .irq_attach2 = fh_irq_attach2, .irq_hold = fh_irq_hold */
/* fext_host.h: FEXT_SLOT_OFF 0xEA000u, FEXT_SLOT_MAX 0x11000u (section 5) */
```

Felucca's host would get the same three functions with its own priorities, and `FEXT_SLOT_OFF 0xEA000u`,
`FEXT_SLOT_MAX 0x12000u`.

## 5. Slot layout

Factory data in the FM-1 flash (measured in Optimist, cross-checked against m-flasher):
- the VM (where the RF trims live) at 0xE8000, with 0xE7000 as a candidate;
- BTIF (the Bluetooth MAC) at 0xE9000;
- KEY_MAC at 0xFF000–0x100000.

**0xE7000–0xE9FFF must be treated as factory.** The stock partition table puts USR at 0xEA000.

| Host | Slot today | Problem | Proposed |
|---|---|---|---|
| SLOOP (sloop-fble) | USR4 0xE7000, 0x14000 (80 KiB) | covers the VM and BTIF; erasing the slot erases the trims | **0xEA000, 0x11000 (68 KiB)**, up to FEXT_NV at 0xFB000 |
| Felucca (felucca-fext) | 0xE8000, 0x14000 | the same | **0xEA000, 0x12000 (72 KiB)**, up to the globals at 0xFC000 |

SLOOP's own USR4 sample slot starts at 0xE7000 and overlaps the factory VM too. That is a SLOOP issue
independent of the module, and should be reported to SLOOP separately. Live calibration makes fble2 work on units
whose VM has already been erased (for example by an fble write into the old slot).

The RAM windows stay as they are. In SLOOP: CODE 4096 B, DMA 24576 B, RAM 8192 B. fble2 uses 36 B, 5.8–7.9 KB
and 0 B of them.

## 6. Sizes (measured, `tools/build_fble2.py`, `-Os`, pi32v2 clang from JIELI_TOOLCHAIN)

With felucca-bsp main's radio headers and live calibration:

| Level | Image | Relocations | Free in SLOOP 68 KiB | Free in Felucca 72 KiB | DMA | CODE |
|---|---|---|---|---|---|---|
| c0: peripheral | 34,344 B | 378 | 35,288 B | 39,384 B | 5,800 B | 36 B |
| c3: + central, no pairing | 45,812 B | 469 | 23,820 B | 27,916 B | 7,180 B | 36 B |
| c1: + pairing (SMP initiator, LL encryption, passkey) | 53,924 B | 590 | 15,708 B | 19,804 B | 7,868 B | 36 B |

Live calibration costs about 5.2 KB of flash and 240 B of DMA. The earlier build without it measured c0 29,096 B,
c3 40,168 B and c1 48,228 B.

For comparison:
- fble (JieLi) needs the 80 KiB slot.
- Optimist's own builder reports BLE at 30.9 KB flash and 6.5 KB RAM, with CENTRAL=1 adding 20.9/2.2 KB and BOND
  adding 3.9 KB.
- fm1-base's README gives its own stack at +41–46 KB flash and +10 KB RAM with a computer link.

Breakdown of c0 and c1 by source file (symbol sizes from `fble2.sym`; inlined code counts toward the caller, so
treat these as approximate):

| Part | c0 | c1 |
|---|---|---|
| Radio driver `ble_hw_wl82.c` (+ `_central.c`) | 11.3 KB | 13.3 KB |
| RF start-up `fm1_ble_rf.h` + tables `ble_rf_tables_v15.h` | 12.4 KB | 12.4 KB |
| Live calibration `fm1_rf_cal.h` (part of it is inlined above) | 1.5 KB | 1.5 KB |
| **Radio total (what variant b would move to the host)** | **≈ 25 KB** | **≈ 27 KB** |
| LL, L2CAP/ATT, GATT client, SMP/AES, central, store | ≈ 2.9 KB | ≈ 13.6 KB |
| `fble2_mod.c` + `fble_ui.c` + VM reader | ≈ 6 KB | ≈ 12 KB |

## 7. Variants (a) and (b)

**(a) The module carries everything (the prototype).** The host offers ABI 1.1 (or just 1.0) and the slot.
- The module is 34–54 KB.
- No host needs the new felucca-bsp: the module carries its own copy of the radio headers, so Felucca's pin at
  5942e68 does not matter.
- A stack fix ships as a new module, without reflashing the firmware.

**(b) The host provides the radio.** The host (on felucca-bsp main) compiles the driver, the RF start-up, the
tables and the calibration. The module carries the LL, the host stack, MIDI and UI.
- Module: about 9 KB (c0) to 27 KB (c1).
- **Host: +25–27 KB of flash and about 1.5 KB of DMA-reachable RAM** (the baseband block), whether or not a module
  is installed.
- **The ABI has to expose the radio.** felucca-bsp contains only static-inline headers. The driver
  (`ble_hw_wl82.c`) is part of the stack, not of the bsp. Its interface to the LL (`ble_hw.h`) has 16 calls down
  (adv/scan/init/conn start-stop, conn_update, chmap, lengths, tx_kick, addr, time, rand) and 8 upcalls from the
  ISRs into the LL (adv_report, connect_ind, master_start, rx, tx, tx_pending, tx_acked, event_end). That is about
  24 function pointers plus 6 structures on the per-packet path. It is Optimist's internal interface, and nothing
  guarantees its stability across commits: the host's driver and the module's LL would have to come from the same
  Optimist commit.
- **SLOOP has no room for it:** the app slot is 573,544 of 581,564 B, so about 8 KB is free. Felucca would have to
  check its own margin.

| | (a) | (b) |
|---|---|---|
| Module | 34–54 KB | ≈ 9–27 KB |
| Host flash | +≈ 1 KB (three ABI functions) | +≈ 26 KB (driver, tables, calibration) + ≈ 1 KB vtable |
| Host RAM | 0 | +≈ 1.5 KB DMA (permanently) |
| ABI | 3 functions | 3 + a radio vtable of about 24 pointers and the upcalls, versioned with Optimist's internals |
| Fixing the stack | a new module | often the firmware too (the driver lives in the host) |
| Needs felucca-bsp ≥ 606c9b2 in the host | no | yes |
| Work beyond (a) | — | +1–2 weeks (vtable, host build, version coupling, tests on two sides) |

**Recommendation: (a).** The module fits every proposed slot in (a), so (b) saves nothing that is short, and costs
flash where it is shortest (SLOOP's app). (b) only makes sense in a host that has the whole stack anyway, and such
a host does not need the module (next paragraph).

**charlesvestal/fm1-base, `ble_stack = "own"`.** This is a working base, tested on hardware (62b1ad5).
- It imports Optimist 3274ce5's `bt/ble` and `bt/br` under MPL (28e1428, which also fixes an empty-value read in
  `ble_att.c` that UBSan flagged).
- `device/bt_own.c` (MPL, Charles Vestal) handles the callbacks, `ble_midi_send_many`, `plat_ble_*`, and stores
  the address and bond through `plat_store`.
- `bt_own_central.c` provides the central.
- It uses felucca-bsp's radio headers and calibration.

It is not variant (b) for the socket, though. It is an application framework: its `plat_*` API is compiled into the
application, not exposed through fext. For the socket it offers two things:
1. **Code to borrow for (a).** fble2 should take `bt/ble` from fm1-base rather than from Optimist directly, because
   it is the version tested on hardware and carries the UBSan fix. Moving `optimist-ble/` to that import (same files,
   newer commit) is the first step in section 11. `bt_own.c` is worth reading as the glue, but fble2 has its own
   (`fble2_mod.c`), because the fext rings and NV differ from `plat_*`.
2. **Firmwares built on fm1-base (LOOM-8, CTL-1, fm1-radio) do not need fble2.** They get BLE MIDI natively by
   setting `ble_stack = "own"`. The socket is for families without a stack: Felucca, SLOOP and their forks.

## 8. Where the socket should live, so it reaches the most firmwares

The families (from awesome-fm-1):
- **felucca-bsp as a submodule:** Felucca (`firmware/bsp`) and its many forks (GHOULBOX, Jangada, Melodee, JIANT,
  Araminta, Felucca Salt, MAKU, fm1-chord, WaveLoop, Rainbow, FiMba-1, ...), plus fm1-base (`bsp/`) and its
  descendants (LOOM-8, CTL-1, fm1-radio). Dinghy and X0X are Felucca's platform in their own versions.
- **Their own in-tree hal (`firmware/hal/`, no submodule):** SLOOP and its forks (sloopDX, zp12, ORBIT, FM1 Quest,
  NoteSorcery, SLOOP ALG, ...) and Optimist.

Proposed split:

1. **The chip part goes in felucca-bsp** (MPL, like the rest of the bsp), for example a `fext/` directory with:
   - `fext.h` (the ABI);
   - `fext_load.h` (the header check, CRCs, copy steps, the slot info for SysEx op 10);
   - the SysEx push and status protocol as a parser with callbacks;
   - the chip helpers behind the 1.1 calls: `flash_read` from the XIP window, `irq_hold` on ICFG, `irq_attach2` with
     the priority mapping passed in by the application.

   This is roughly half of today's `fext_host.c` (about 770 lines in each of SLOOP and Felucca, mostly the same
   code twice). One copy in the bsp reaches every Felucca and fm1-base descendant with a submodule bump.
2. **The host part stays in the application** (about 300–400 lines):
   - the slot and windows in the linker script (`app_fext.ld`);
   - which interrupts `quiet` masks (the application's audio);
   - the MIDI router hook (where `midi_in` enters the input ring, where `midi_out` is copied);
   - the NV sector;
   - the UI pages (GLO > module name);
   - the audio priorities passed to the bsp's `irq_attach2`.
3. **Families with their own hal (SLOOP and descendants)** copy the bsp's `fext/` directory into `firmware/hal/`.
   It is MPL, so it can go into a GPL tree file by file. The cost is about what sloop-fble already paid once:
   - the host glue (the part above), plus a linker-script window (CODE/DMA/RAM in `app_fext.ld`);
   - a check of that fork's flash map (whether 0xEA000–0xFB000 is free: sample slots move between forks);
   - a test.

   For a SLOOP fork that merges sloop-fble's branch, that is 1–2 days; written from scratch, 3–5 days. Optimist
   does not need it: it has the stack natively.
4. **Per-fork flash maps** are the main risk to reach. Each host already reports its slot (SysEx op 10), and
   `fext_reloc.py place` moves the module there, so the module does not have to know any of them.

## 9. Licences and NOTICE

- The whole module can be MPL-2.0. The Optimist files and the two bsp headers are dual-licensed "GPL-3.0-only OR
  MPL-2.0" and are used under the MPL-2.0 option, with their SPDX lines kept unchanged.
- The two Apache-2.0 files are `ble_rf_tables_v15.h` (M-VAVE/JieLi) and `fm1_rf_cal.h` (JieLi, Charles Vestal).
  Apache-2.0 sits alongside MPL-2.0 file by file. `LICENSES/Apache-2.0.txt` is already in the repository, and
  NOTICE names them.
- **No GPL-only file is in the module.** Optimist's GPL-only HAL (`fm1_cc.h`, `fm1_irq.h`, `fm1_time.h`) and its
  glue were not copied; the module has its own lines instead.
- The host and the module remain separate works (as with fble): a GPL host loads an MPL module through the socket.
  No JieLi binary library is in fble2.
- NOTICE in this branch names Optimist (Henri David / hdavid, MPL-2.0 option, fcdc488) and felucca-bsp (305c372:
  MPL / dual plus the two Apache-2.0 files).

## 10. Risks

- **R1 Interrupt priority on a 1.0 host.** The BLE ISRs run above audio, and an ISR of unmeasured length can cause
  audio dropouts. Fixed by 1.1 `irq_attach2(LOW)`. It could also be clamped on a 1.0 host by attaching with prio 0
  and accepting it.
- **R2 Radio start-up versus XIP and CPU1.** `quiet` was tested only with fble (JieLi). Live calibration runs
  longer than applying stored trims. CPU1 (audio on the second core in SLOOP) may still read XIP. Optimist and
  fm1-base solve this in their own firmware, not through a socket.
- **R3 NV_STACK writes immediately and blocks audio** (cli). Bonds are deferred 2 s, but the write still lands
  while audio runs. A 1.1 `nv_save` deferred until quiet (as `FEXT_NV_SET` is) would be better.
- **R4 ISR execution time and software AES** in c1 (encryption of every packet) have not been measured on the
  FM-1 together with audio.
- **R5 One link at a time** is a regression compared with fble, which handles a computer and a device together.
- **R6 Stack version.** The Optimist copy (fcdc488) is not the version tested on hardware (fm1-base's import of
  3274ce5 with the UBSan fix).
- **R7 Constant board values for live calibration** come from one fm1-base unit. Other production batches have not
  been tested.

## 11. Steps

1. Update `optimist-ble/` to fm1-base's `bt/ble` (the hardware-tested version) and rebuild c0/c3/c1. (½ day)
2. Host ABI 1.1 and the slot at 0xEA000 on a separate branch of sloop-fble (section 4), keeping the 1.0 path.
   (1–2 days)
3. Bring up the peripheral c0 on an FM-1 that does not hold the user's working SLOOP:
   - advertising, connection, MIDI both ways;
   - live calibration versus VM trims;
   - audio during the start-up and during a connection (R1, R2, R4).

   (1–2 weeks)
4. NV: the address and bond across a reboot; NV writes during playback (R3). (2–3 days)
5. Central c3, then c1 with pairing. (1–2 weeks)
6. Felucca: the same host diff and a test. (2–3 days)
7. The bsp `fext/` split (section 8), offered upstream to felucca-bsp. (3–5 days, plus review)

## 12. What was tested

- **Tested:**
  - compiling and linking c0, c3 and c1 with the JieLi pi32v2 clang;
  - relocation: the image moved to SLOOP2 (0xEA000 and SLOOP's windows) by `fext_reloc.py place` is byte-identical
    to the module linked there directly;
  - `.ram_text` calls nothing in flash;
  - the sizes fit the windows (the script's own checks).
- **Not tested:**
  - running on an FM-1 (none was touched; no flashing, no `fext_push`);
  - loading by any host (no host was built with 1.1);
  - the UI adapter against fble_ui.c's behaviour;
  - any radio traffic;
  - `-DFBLE2_CAL_ALWAYS=1` was only compiled (c1, no warnings), not linked or sized.
- `module/fble_mod.c` (fble) only changed its size check. It was not rebuilt in this branch, because that build
  needs the AC79 SDK and fm1-base.
