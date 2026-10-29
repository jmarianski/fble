# fwpatch: putting the plugin socket into a ready firmware

Status: **a plan, plus an analysis tool and a file-only proof of concept. Nothing here has run on an FM-1, and
no patched file has been installed.** Branch `fwpatch` (on top of `fble2`). Section 11 lists exactly what was
checked.

The idea: take any FM-1 firmware as a `.fwsc` file and decide whether it has room for the plugin socket. If it
does, the patcher adds the socket to the binary:

- the socket's code, appended in the app slot;
- its RAM, in a gap nobody uses;
- hooks into boot, the main loop, MIDI in and out, and interrupts;
- a page in the options menu.

Then it writes the module (fble2) into its flash slot. The module only talks to the socket's ABI, which is the
same fext 1.1 as the source-level socket (`include/fext.h`). The planned user interface is a web page with Web
MIDI, like the fble installer, in four steps:

1. Pick the firmware.
2. Pick the plugins.
3. Read the feasibility report (flash, RAM, hooks).
4. Patch, then download the `.fwsc` or install it.

## 1. Short answer

- **Feasible for the SLOOP and Felucca lineages, as per-version recipes rather than one universal patch.**
  - Without symbols, the analysis tool finds everything a socket needs in the official releases of SLOOP
    (2.6, 2.8.1, 2.8.3) and Felucca (1.5.1, 1.6.1, 1.7.0): flash room, free RAM, the main loop, the MIDI out
    path, the page table, the menu name tables, and the PC-limit guard that a patch must widen.
  - Checked against our own builds with symbols (SLOOP 2.8.3, Felucca main, Felucca 1.7.0), all 12 checks
    agree in each. The detected end of code is within 2 bytes of the real one.
  - SLOOP 2.7.1 is too full (3.6 KB free, the socket needs about 6 KB).
- **The mechanics work, on a file.** `poc_trampoline.py` patches SLOOP 2.8.3 and Felucca 1.7.0. It reroutes one
  call in the main loop through code appended after the image, and raises the PC-limit guard's `_etext` so that
  this code may run. It then rebuilds the package with every CRC and the encryption: app.bin, the app area, the
  chip key's SFC, the UFW header and files, and the marker bytes.
  - Our reader accepts the result.
  - The app differs from the original in exactly 9 bytes.
  - Reading and rewriting the result is byte-identical.
  - The file was never flashed.
- **Signatures drift between versions.** Exact function fingerprints (instruction stream with offsets and
  addresses masked) carry over between neighbouring releases for 55–72% of functions, but only 25% from SLOOP
  2.6 to 2.8.3. The functions a socket hooks (ui_input, midi_out_event) change in almost every release. So
  generic structural finders (section 6) locate the candidates, and a recipe checked against a ground-truth
  build confirms them per release.
- **Coverage, honestly** (section 9):
  - awesome-fm-1 lists 216 open-source firmwares on GitHub. 124 of them have a `.fwsc` in a GitHub release.
  - Of those 124, the full set the source-level socket has (flash, RAM, main loop, MIDI out, a SLOOP/Felucca
    page table) shows up in **20** (about 17 credible).
  - Another **30** have everything except a recognisable page table. They could take a headless socket: the
    module without a menu page, set up over SysEx or the web page.
  - 29 have the main loop but no MIDI hook candidate. 15 are too full. In 30 a free RAM gap cannot be proven
    from the binary.
  - Realistically, the first version would cover SLOOP 2.6, 2.8.x and Felucca 1.5–1.7 and their close forks:
    10–20 firmwares, each with a recipe tested on hardware.
- **A source-level socket stays the better path** wherever the author takes a patch: it costs a few hundred
  lines, needs no recipes and survives new releases. The patcher is for firmwares whose authors do not.

## 2. What is in the branch

| Path | What |
|---|---|
| `tools/fwpatch/fm1pkg.py` | `.fwsc` reader and writer: marker bytes, UFW header and files, flash head (JLFS), chip key from isd_config, app area (SFC decryption), entries, app.bin. Round trip is byte-identical on all 7 official releases and our builds |
| `tools/fwpatch/pi32.py` | the part of the pi32v2 ISA a patcher needs: lengths, call and goto encoding, `rN = imm32`, jump tables, a linear sweep, the end of code, a call graph |
| `tools/fwpatch/pi32v2-valid.bin`, `gen_valid.py` | which of the 65536 first halfwords the toolchain's objdump decodes (8 KB), and the generator |
| `tools/fwpatch/analyze.py` | the feasibility report: room, layout, free RAM, hooks, guard, verdict. `--sym` checks every finding against `objdump -t` of the same build |
| `tools/fwpatch/poc_trampoline.py` | the file-only proof of concept (section 7.9) |

All of it is MPL-2.0 and our own code. The package format was read from SLOOP's `tools/fm1pkg_make.py` (GPL),
but nothing was copied from it.

## 3. The `.fwsc` package (as `fm1pkg.py` reads and writes it)

1. **Marker bytes.** The first 20 blocks of 0x30 bytes each carry a marker byte at +0x2F:
   - for a product character c at block i, the byte is `(c + i + 1) & 0xFF`;
   - 0x7D means unused.
   Removing them gives the logical UFW image. The product id (e.g. `FM-1_900` for SLOOP, `FM-1_91051` for
   Melodee) is the firmware's install identity, and the patcher keeps it.
2. **UFW.** A 0x40-byte header, then one 0x50-byte entry per file, each encrypted with key 0xFFFF:
   - the header has CRC16 (poly 0x1021, init 0) over itself and a CRC16 over the entry list;
   - each file entry has the file's own CRC16;
   - the files are `flash.bin` (type 0) and `ota.bin` (type 100). `ota.bin` is Felucca's update loader
     (`FELUCCA-LOADER-1`), not the app, so a patch leaves it alone.
3. **flash.bin.**
   - The head holds JLFS entries of 32 bytes (ENC 0xFFFF): `uboot.boot`, `isd_config.ini`, `app_dir_head`
     (→ 0x4000), `key_mac`.
   - The chip key is decoded from isd_config. It is 0x980F in every release we tested.
4. **The app area** at 0x4000 is SFC-encrypted (ENC per 32-byte block with key ^ (offset >> 2)). It contains:
   - `app_area_head`;
   - the entries `app.bin` (offset 0x120, size 0x8DFBC = 581564: the app slot) and `cfg_tool.bin`;
   - the region descriptors VM 0x93000+0x56000, PRCT, BTIF 0xE9000 and USR 0xEA000+0x12000.
5. **app.bin** runs in place from XIP 0x02000120 and contains `.text` (code, then `.rodata`), then the load
   images of `.ram_text`, `.c1_text` and `.data`. `fm1_cstart` copies those at boot.

A patch only changes app.bin within its slot. `write()` recomputes:

- app.bin's CRC in its entry;
- the area head's CRC over the entries;
- the SFC encryption;
- flash.bin's CRC;
- the UFW list and header CRCs;
- the marker bytes.

The boot loader checks app.bin's CRC, which is why the patch updates it. As far as a search of their sources shows
(the app's address, `app.bin`, image CRCs), neither SLOOP nor Felucca checks its own image at run time. A fork
that does would need its check patched as well, and its recipe would say so.

## 4. Options pages in the families (task 1)

| Family | How pages and menus are made | What a patch could hook |
|---|---|---|
| **SLOOP** (2.x, its forks: quest, clipper, fmel-1, Selva1, orbit, …) | `static const page_t PAGES[]` in params.c: 12 bytes `{const char *title; u8 fam, scope, graph; u8 id[4];}`; `NPAGES` = sizeof; pages of a family must be contiguous; FAM_GLO = 6 holds GLOBAL, MASTER, SYSTEM, DRUMS, MIDI 1–3, TEXTURE 1–2. Menus: `enum MI_*`, `const char *const MI_NAME[]` / `MS_NAME[]`, values and actions in `switch`es (ui_menu.c). Drawing and knobs by `graph` (ui_draw.c, ui_input.c) | the page table (found in every release, 7–15 address loads), the draw and knob calls in the main loop |
| **Felucca** (1.x, most forks: skiff, jangada, maku, hortator, Fantasma, Mini-Tone, …) | the same `page_t PAGES[]` (45 pages; family 6 is SONG here). The settings are a separate menu: `MI_NAME[]` (37 items: COLOR, STYLE … INFO, ABOUT), `MTAB_NAME` / `MI_TAB` tabs, `switch` handlers (menu_items.c) | page table or the MI_NAME menu (found in every release); the menu's handlers are switch jump tables |
| **Optimist** | one translation unit; `screen_t SCREENS[SCR_N]` (function pointer table); SYSTEM and GLOBAL as `SYS[] {name, it[4]}` with `switch` handlers; main loop in `fm1_main`; RAM sections LZB-compressed from `_etext` | a new screen would need a new `SCREENS` entry plus SCR_N; the binary's 12-byte "page" hits are parameter label tables, not pages |
| **Dinghy** | no menu at all; `app_main` loop; `midi_in_event` / `midi_out_event` | headless only |
| **X0X / fm1-base descendants** | the GLOBAL menu is built at run time in RAM (`list_rows[64]`); `fm1_main`; `midi_in(pkt)` | headless, or a hook in the list builder (per firmware) |

Shared ground: SLOOP and Felucca have the same `page_t` record, and so does every fork that kept params.c. That
record is the patcher's cross-version signature. Everything that draws a page is per firmware: `graph` ids,
switch-based draw and knob code, column layout. Section 7.4 proposes an overlay page so that the patcher does not
have to rewrite those switches.

## 5. pi32v2 and the disassembler

The JieLi toolchain's `common/bin/objdump` is llvm-objdump and disassembles pi32v2 (`ELF32-pi32v2`). It does not
take raw binaries (`-b binary` is unknown), so `gen_valid.py` patches probes into a copy of an ELF's `.text`, with
the symbol table hidden so that data objects are not dumped as bytes. What the patcher needs, all read off the
compiler's output:

- **Length.** The first halfword's high byte b1 decides it: 0xFF → 6 bytes, 0xE0–0xFE → 4, otherwise 2. objdump
  agrees for all 52,721 first halfwords it decodes.
- **`call`.** 4 bytes `[b0 0xEA lo hi]` with b0 = 10cccccc; target = pc + 4 + 2·s22(c:hi:lo). This covers every
  direct call in both builds. `goto` (4 bytes) is the same with b0 = 11cccccc. The 2-byte goto has b1 = 100xxxxx
  and a 9-bit signed offset.
- **`rN = imm32`.** 6 bytes `[cN ff i0..i3]`, with sp = 0xEE and ssp = 0xED. There are no literal pools, so this
  is the only way the compiler forms an address above 0xFFFF. **Every XIP or RAM address the code uses can be
  found by grepping for it.** The patcher relies on this for the page table's users, free RAM and the guard
  constant.
- **Jump tables.** `tbb [rN]` / `tbh [rN]` (`[0N 01]` / `[1N 01]`) are followed by byte or halfword tables of
  forward offsets. The sweep skips them.
- **Validity.** 12,815 of the 65,536 first halfwords do not decode. 28 values of b1 never do: c0–d3, e2, e3,
  e6, e7, f2, f3, f6, f7. For 4- and 6-byte forms the probe's fixed tail affects the answer, so only those whole
  classes are used there; for 2-byte forms the table is exact. `.text` is followed by `.rodata` in every app.ld of the family, so the first
  invalid halfword marks the end of code: within 0–2 bytes of `_etext`'s code part in all three ground-truth
  builds.
  - An earlier heuristic ("opcodes never seen in our two builds") stopped too early on SLOOP 2.8.1 (`fd 76` is
    a valid `r0 = [r0-40]`) and on most forks. Section 11 has the numbers.
  - Images that put a small boot part first and the rest of `.text` at the next 4 KB (Melodee, CoralFlower,
    tessera, fm-106, ChoralRoot) have 0xFF fill in between, which the sweep steps over.

## 6. Ground truth: source against binary (task 2)

**Method.** We built SLOOP v2.8.3 (5eb24c1), Felucca main (5d9694d) and Felucca v1.7.0 in our own scratch copies
with the toolchain from local.env, took `objdump -t/-h/-d` of each, and ran `analyze.py --sym`. It compares 12
findings with the symbols:

- the stack tops;
- `fm1_cstart`;
- `.ram_text` (plus `.lowram`), `.data`, `.bss`, `.pool`;
- main (Felucca);
- the main loop's calls (it must contain ed_service and ui_input);
- PAGES;
- the menu name table;
- `midi_out_event`.

All 12 agree in all three builds.

**Our builds against the published releases:**

- SLOOP 2.8.3: our app.bin is **identical to the release except one byte**, the `__DATE__` string ("Oct 27" vs
  "Oct 29"). So the symbols of our build are the symbols of the published binary.
- Felucca 1.7.0: our build of the tag differs from the release from about 0x2000CB0 onward (513,328 vs 513,332
  bytes). The release was built from a slightly different tree or toolchain, which we have not investigated. The
  structural finders give the same answers on both; exact addresses differ by a few bytes.

**Without symbols, on the official releases:**

| Release | Free flash | Main loop | PAGES | MIDI out ring put | Free RAM (confidence) | PC guard `_etext` load | Verdict |
|---|---|---|---|---|---|---|---|
| SLOOP 2.6 | 8,512 | 35 calls | 38 pages, 15 loads | **not found** (`midi_out_event` is static and inlined into its ISR) | 0x1C71900+9,984 (high) | 0x2026716 | feasible, MIDI out needs a recipe |
| SLOOP 2.7.1 | **3,644** | 54 calls | 39, 7 loads | 0x2000F1E | 0x1C16700+39,168 (medium), 0x1C71900+9,984 (high) | 0x20103D8 | **too full** |
| SLOOP 2.8.1 | 14,000 | 56 calls | 39, 7 loads | 0x2000F16 | same as 2.7.1 | 0x2010AB2 | feasible |
| SLOOP 2.8.3 | 11,388 | 56 calls | 39, 7 loads | 0x2000F86 | 0x1C16800+38,912 (medium), 0x1C71900+9,984 (high) | 0x20116F8 | feasible |
| Felucca 1.5.1 | 85,984 | 118 calls | 45, 9 loads | 0x20033EE | 0x1C15700+18,688, 0x1C6A100+40,704 (medium) | 0x203214C | feasible |
| Felucca 1.6.1 | 70,092 | 111 calls | 45, 10 loads | 0x200383A | 0x1C16F00+12,544, 0x1C6A500+39,680 (medium) | 0x200CE18 | feasible |
| Felucca 1.7.0 | 68,232 | 112 calls | 45, 10 loads | 0x2003A06 | 0x1C17200+11,776, 0x1C69B00+42,240 (medium) | 0x2035430 | feasible |

Analysis takes 0.3–1 s per firmware in CPython.

**Stability of exact signatures.** We fingerprinted each function, masking call, goto and branch offsets and
imm32 values, and counted how many of a ground-truth build's functions appear exactly once in a release:

| Ground truth | Release | Functions found once |
|---|---|---|
| SLOOP 2.8.3 build | SLOOP 2.8.3 | 516 / 527 (98%) |
| | 2.8.1 | 65% |
| | 2.7.1 | 55% |
| | 2.6 | 25% |
| Felucca main build | Felucca 1.7.0 | 97% |
| | 1.6.1 | 72% |
| | 1.5.1 | 60% |

The hooked functions themselves (`ui_input`, `midi_out_event`, `ed_service`) do **not** carry over between
releases. Stable ones are the guard (`fm1_guard_enable`) and the page navigation (`page_first`,
`open_family`).

**Conclusion.** Structural finders are what carries across versions and forks:

- 12-byte page records with an uppercase title pointer;
- the start-up's copy-loop pattern;
- the widest backward branch in main;
- a ≤ 0x60-byte function with `& 0x3F` and a word store (the 64-entry ring put);
- `rN = 0x02000120` followed by `rN = _etext`.

Exact signatures only work per release. A recipe is therefore *the structural finders plus a confirmation list for
one release*, made against a ground-truth build where the source is open (as here).

## 7. Patch design (task 4)

### 7.1 Where things go

- **The socket's code (≈ 6 KB)** goes after the image, in the app slot's free flash. It runs in place from XIP,
  as the trampoline in the proof of concept does. The source socket (sloop-fble `fext_host.c`, 774 lines) is the
  model. Its page drawing calls the host's text and box primitives, so those must be located too (section 7.4).
- **The module (fble2)** stays where the source socket puts it: flash 0xEA000, up to 0xFB000 (FBLE2-PLAN
  section 5; SLOOP's USR4 overlap applies as there). The socket loads it the same way: header check, copies,
  `fm1_guard_pc_widen`, then `init(&host)`.
- **The socket's RAM (≈ 1 KB of its own, plus the module's windows)** goes in a proven-free gap (7.2).

### 7.2 RAM reservation and the proof that it is free

A gap is a range between the regions the start-up initialises (`.ram_text`, `.lowram`, `.data`, `.bss`,
`.pool`) and below the stacks' guard band (`_guard0`, the lowest stack constant). A gap counts as free when:

1. no `rN = imm32` loads an address inside it. Every address the code forms is such a load (section 5), so this
   catches every direct use; and
2. no word in the initialised data and rodata (after the code) points into it, which catches pointer tables.

Confidence is *high* with zero of both, *medium* with at most two stray data words (usually constants that
happen to fall in range), and *low* otherwise. It is also *low* whenever `.data`'s copy loop was not recognised
(Optimist packs its RAM sections, so its pointers cannot be scanned).

Checked against the symbols:

- SLOOP 2.8.3: 0x1C71900–0x1C74000 (between `_pool_end` 0x1C7184C and `_guard0`) and 0x1C16800–0x1C20000
  (between `.bss` and `.pool`) contain no symbol.
- Felucca main: 0x1C17200–0x1C1A000 and 0x1C69B00–0x1C74000 contain no symbol.
- `_bss_end` / `_pool_end` are loaded only as the start-up's loop bounds; there is no heap growing from them.

Recommended reservation:

- SLOOP: 0x1C71900–0x1C74000 (9,984 B, high in every SLOOP release);
- Felucca: the top of 0x1C69B00–0x1C74000.

The write-limit guard's bands begin at `_guard0`, so the reservation ends below them.

What the proof cannot see: addresses computed from a base plus a run-time offset beyond an object's end (an
overrun in the host), and DMA set up with a computed address. Both would be host bugs. A hardware soak test
covers what is left.

### 7.3 Hooks as trampolines

Rule: **redirect call sites; do not move instructions.** A 4-byte `call X` at a known site is replaced by `call T`,
where T is socket code that does its work and then `goto X` (or calls X first and does its work afterwards).
Nothing PC-relative has to be relocated, and the call's return address stays the same.

| Hook | Where (found how) | Trampoline |
|---|---|---|
| boot | the start-up's last call (`call main`) in Felucca; in SLOOP main is inlined into `fm1_cstart`, so use the first call of the main loop, guarded by a "first pass" flag in socket RAM | `fext_boot` (the module loader) once, then the original |
| main loop | a call in the loop found by the widest backward branch (SLOOP 2.8.3: 56 calls in 0x2029E3C–0x202A6D4; Felucca 1.7.0: 112) | `fext_service` + `fext_sysex` every pass, then `goto` the original. The proof of concept does exactly this with an empty body |
| MIDI out | each call site of the 64-entry ring put (`midi_out_event`, 6 sites in SLOOP 2.8.3, 3 in Felucca) | call the original, then put the same packet into the module's out ring (`fext_midi_out`). SLOOP 2.6 inlines it, so its recipe hooks the ISR's store |
| MIDI in | the main loop drains the host's in queue; the socket injects from its main-loop hook | the module's in-ring packets go through the host's own enqueue (`midi_enqueue(p, 2)` in the source socket), **with the USB interrupt held** (`irq_hold`) because the queue has one producer, the USB ISR |
| interrupts | the vector table at 0x01C7FD50+ and the ICFG priority words 0x01EEF100+ (found: one ICFG site in cstart / fm1_main) | the socket writes the module's vectors itself (`irq_attach2`: LOW below audio 3 and TIMER5 4, HIGH above), after `fm1_guard_unlock_top`. The host locks the top window once all vectors are attached (`fm1_guard_lock_top`), so the socket unlocks and relocks around its own attach |
| quiet | `fext_quiet` masks ALNK0 and TIMER5 during radio calibration (source socket) | the same register writes in the socket; no host code involved |

### 7.4 The menu page: insert into the table, or overlay

**(a) Table insertion,** close to the source socket's `GR_EXT` page:

1. Copy PAGES (39 × 12 B) to the appended area with one record added at the end of the family (pages of a family
   must stay contiguous).
2. Rewrite every `rN = imm32` that loads PAGES or an address inside it (7 in SLOOP 2.8.3: base plus offset
   forms).
3. Change every `NPAGES` / `NPAGES - 1` compare in the functions that load the table. In SLOOP 2.8.3,
   `page_first` and `studio_open` compare with 39 and `open_family` with 38. The same constants occur elsewhere
   as MIDI CC numbers, so only compares inside table users may be touched.
4. Find the draw and knob switches for the new `graph`.

The risks are serious:

- arrays sized `NPAGES` in RAM (per-page cursors) would overflow;
- page indices stored in the settings in flash would shift;
- jump tables would need a new case.

**(b) Overlay page (recommended).** PAGES and NPAGES stay untouched. The socket keeps a flag "module page shown"
in its RAM and wraps three calls in the main loop:

- **page change** (`open_family` / `page_first` / next-page): leaving the last GLO page forward opens the module's
  page instead of wrapping;
- **draw** (`ui_draw`): when the flag is set, the socket draws the module's columns and lines (`fext_col`,
  `fext_line`) with the host's primitives and skips the original;
- **input** (`ui_input`): when the flag is set, knobs go to `knob()` and the page keys clear the flag.

What a recipe must locate for each firmware:

- three functions: page change, draw, input;
- two drawing primitives: text, box;
- the frame-flush call.

On SLOOP these are among the main loop's 56 calls. The ground-truth build names them, and the fingerprint
table names them in the matching release only.

**Felucca's settings menu** (MI_NAME, 37 items, switch handlers) can take the same overlay: one more item at the
end of the list, shown and handled by the socket. That requires the list's length compare and the switch's
default path, so it is again per release.

### 7.5 Flash writes (NV)

- The module's NV (`nv_save`, up to 1.5 KB plus 192 B settings) needs flash erase and program. On the FM-1 these
  run from RAM, because XIP stalls during a write.
- The host already has them in `.ram_text`: `fl_erase4k_ram` and `fl_prog_ram` in SLOOP; both families carry
  `fl_jedec_ram`, `fl_status_ram`, `fl_erase4k_ram`, `fl_prog_ram` and `fl_read_ram`.
- The socket should **call the host's RAM routines** rather than bring its own. The PC-limit guard's RAM window
  covers exactly `.ram_text`, so own RAM code would need that window widened too, and that is a second guard
  edit.
- Finding them without symbols: `.ram_text`'s bounds are known (start-up copy loop), and the routines are called
  as `call rN` after `rN = 0x01C00xxx`. In both builds `fl_jedec_ram` sits at 0x01C00000, which is a hint, not a
  proof, that the order is shared.
- Exact addresses come from the recipe.
- The NV area itself is the module slot's tail (`FEXT_NV` at 0xFB000 in the source socket). Nothing else in
  SLOOP or Felucca addresses 0xEA000–0xFB000 by a constant (checked in all releases), with the caveat that an
  offset can also be computed.

### 7.6 Guards the patch must respect (new finding)

Both families arm the debug unit's guards (`hal/fm1_guard.h`):

- **PC limit.**
  - Instruction fetch outside [0x02000120, `_etext`] faults, and `_etext` is the end of `.text` including
    rodata. **Any code appended after the image faults unless the patch raises the `rN = _etext` constant that
    feeds `PC_LIMIT0_H`.**
  - The analyzer finds it by its shape: `rN = 0x02000120` (PC_LIMIT0_L) followed within 24 bytes by
    `rM = _etext`. It is found in all 7 releases and in 129 fork reports. The other load of the same value is the
    start-up's `.ram_text` copy source, and it must stay as it is.
  - The proof of concept raises it to just past its trampoline. A real socket raises it over its own code and
    then widens to the module at load time, as `fext_host.c` does.
  - In SLOOP, CPU1's start reopens the guard and re-arms it through the same function (`fm1_guard_enable`), so
    one site suffices. Felucca main arms it once (main.c, inlined into `fm1_cstart`).
- **Write limits.**
  - The two stack guard bands and the top window (0x01C7FD50–0x01C7FFFF: vectors, boot info) are write-protected.
    The socket's vector writes need `fm1_guard_unlock_top`.
  - The RAM reservation stays below `_guard0`.
- **Stack window.** The EMU stack limit is [_ustack_lo, _sstack_top]. The module's own task stacks (if it has
  any) need `stack_floor`, which is a register write in the socket.

### 7.7 The ABI shared with fble2

The socket implements `fext_host` 1.1 exactly as `include/fext.h` defines it:

- `ms`, `log`, `wdt_feed`, `irq_attach`, `stack_floor`, `quiet`, `nv_load` / `nv_save`, `reboot`, `say`;
- in 1.1, `flash_read`, `irq_attach2`, `irq_hold`.

It drives `fext_api`: `poll`, `stop`, the MIDI rings, `ports_out` / `ports_live`, `pages`, `column` / `knob` /
`line`, `control`. fble2 needs no change and does not know whether its host is source-built or patched. Host
functions the socket needs that the firmware does not expose (the ms tick, the log) are either found by recipe
(the tick variable is a RAM word the TIMER5 ISR increments) or reimplemented in the socket (a log ring in its RAM).

### 7.8 Dual core, priorities

- CPU1 runs audio jobs (SLOOP's `cpu1_boot`) and must not run socket code. The socket's code and RAM are not
  reachable from CPU1's job table, which the patch does not touch.
- Interrupt levels follow ABI 1.1. Audio ALNK0 is at 3 and TIMER5 at 4. The module's BLE handler attaches LOW,
  below both, as in fble2. A HIGH attach is above them and must stay short.
- The socket's main-loop work runs at main-loop level, like `fext_service` in the source socket.

### 7.9 Proof of concept (file only)

```
poc_trampoline.py sloop-2.8.3.fwsc out.fwsc --sym felucca.sym
main loop 0x2029e3c..0x202a6d4: call at 0x202a6cc -> 0x2013eee ui_message now -> trampoline 0x208b460 (goto 0x2013eee)
PC-limit guard: _etext 0x2089c5c -> 0x208b464 at 0x20116f8
checks: package parses with all CRCs; app differs in 9 bytes (call site, trampoline, guard constant);
        call -> trampoline -> original target decode; rewrite is byte-identical; ota.bin untouched
```

Felucca 1.7.0 gives the same result: call at 0x203474E, guard at 0x2035430.

### 7.10 On hardware (2026-10-29): a negative control

The default site above is the loop's last call, `ui_message`, and it is conditional: it runs only when there is a
message to show, so an image patched there boots whether the trampoline works or not. With it on an FM-1 (our
SLOOP 2.8.3 + fext build, the module removed so its own `fm1_guard_pc_widen` could not hide a fault) the unit ran
normally with the guard **not** raised: nothing had been fetched from the trampoline. Proof needs a call that runs
every frame, and a control that must fail:

| Image (`--site 0x202c3a2`: `call ui_draw`, every UI frame) | On the FM-1 |
|---|---|
| `--no-guard`: the trampoline past `_etext`, the guard as it was | crash screen at once; the main loop answers no SysEx |
| the guard constant raised over the trampoline | boots, runs; every frame goes through the trampoline |

So appended code runs in place from XIP, a re-encoded call reaches it, the rebuilt package installs, and the
PC-limit guard is the one thing that stops it (section 7.6). The crash screen still takes the download-mode SysEx,
so a failed patch is recovered over USB without keys (M-Flasher 0.7.0: `download-mode`, `firmware --restart`).

For the patcher: choose hook sites from the calls that run on every pass of the loop (no conditional branch
between the loop's head and the call), never "the last call". `poc_trampoline.py` takes `--site` and `--no-guard`
for this test.

## 8. Risks

| Risk | What it means | Mitigation |
|---|---|---|
| Signature drift | hooked functions change every release (section 6) | structural finders plus per-release recipes; refuse when a recipe does not confirm |
| PC-limit guard | appended code faults if `_etext` is not raised | found in all releases; patch it; refuse if not found |
| Too little flash | SLOOP 2.7.1 (3.6 KB), 15 of 124 forks | refuse up front (the report does) |
| RAM proof is static | computed addresses and DMA are invisible | prefer *high* gaps; soak test; refuse on *low* |
| Optimist / packed RAM sections | `.data` not scannable, pages are not `page_t` | headless at most, and only with a hand-made recipe |
| Inlined hooks | SLOOP 2.6's MIDI out is inside the ISR | per-recipe hook at the store, or no MIDI out |
| MIDI in concurrency | the host's in queue has one producer | inject with the USB interrupt held |
| Page insertion | NPAGES-sized arrays, stored page indices, switch tables | the overlay page (7.4 b) |
| Firmware self-checks | none found in SLOOP or Felucca sources; the boot loader checks app.bin's CRC | recompute every CRC (done); a recipe lists any self-check a fork adds |
| USR slot | SLOOP's USR4 samples overlap 0xE7000+ (FBLE2-PLAN section 5) | the module slot 0xEA000–0xFB000 as there; the analyzer reports any constant naming it |
| Licences | the patched file contains the host's code (GPL for SLOOP) plus MPL socket and module | distributing patched files falls under the host's licence; the web page patches on the user's machine and does not host patched images |

## 9. Coverage of awesome-fm-1 (honest estimate)

We fetched the latest GitHub release `.fwsc` of every open-source firmware in the list (216 GitHub repositories)
and ran `analyze.py` without symbols on each.

| Group | Count |
|---|---|
| Repositories | 216 |
| No `.fwsc` in a GitHub release (source only, other packaging, or no release) | 92 |
| Analysed | 124 |
| **A: full socket candidates** (flash ≥ 6 KB, RAM gap ≥ 9 KB medium or high, main loop, MIDI out ring, `page_t` table) | **20** |
| **B: headless candidates** (all of A except a page table) | **30** |
| C: main loop and room, no MIDI hook candidate | 29 |
| X: not enough flash | 15 |
| Y: no provable RAM gap | 30 |

**Group A:** hugelton/Felucca, isod89/sloop-fm1, ghoulbox, hortator, fm1-quest, jangada, Felucca_seq_per_track,
skiff, maku, niagara-drift, fmel-1, Mini-Tone, Mini-Vision, Fantasma, clipper-fm1, bunker-evolved, estampie,
carnac-os, fmini-1 and noiz_demons. Three of these look doubtful: niagara-drift (8 records), carnac-os (X0X:
parameter tables, not pages) and fmel-1 (no recognisable family). That leaves about 17 credible, all from the
SLOOP and Felucca lineages.

**By the lineage the list gives:**

| Lineage | A | B | C | X | Y |
|---|---|---|---|---|---|
| Felucca | 14 | 7 | 9 | 6 | 11 |
| SLOOP | 3 | 1 | 2 | 1 | 1 |
| X0X | 1 | 2 | | | 1 |
| fm1-base | | 8 | | 2 | 1 |
| unknown | 2 | 12 | 18 | 6 | 16 |

What this means:

- **"Candidate" is not "patchable".** Every A or B entry still needs a recipe: the hook sites confirmed, the
  overlay page's three functions and drawing primitives located, and a test on hardware. The ring-put finder
  often returns several candidates in fm1-base descendants (fm1-amb: four), so a recipe has to choose.
- **Realistic first target:** SLOOP 2.6, 2.8.1 and 2.8.3, Felucca 1.5.1–1.7.0, and the forks that kept their code
  (sloop-fm1 mirror, quest, clipper, skiff, jangada, maku, hortator, Fantasma, Mini-Tone/-Vision, …). That is
  roughly **10–20 firmwares, about 5–10% of the list.**
- **Headless BLE MIDI** (no page; enable and pair over SysEx from the web page) could add up to 30 more, at one
  recipe each.
- Most of the list ships no `.fwsc` in a release (92), and for those the question does not even arise without
  building them. Where the source is open, a source-level socket (FBLE2-PLAN section 8) is cheaper than a recipe.

## 10. Steps

1. Recipe format: the release's identity (product id, size, CRC); the expected structural finds; the hook sites;
   the overlay functions; the guard site. One recipe each for SLOOP 2.8.3 and Felucca 1.7.0, from the
   ground-truth builds (1 week).
2. The socket as a position-independent blob (fext_host.c reduced; host calls through a table the patcher fills),
   built with the toolchain, linked at the appended address per firmware (1–2 weeks).
3. Patcher: apply a recipe, append socket and table, raise the guard, write the `.fwsc`, run the analyzer on the
   result (a few days).
4. Hardware: SLOOP 2.8.3 first, by the user, on a device they can recover. Boot, main loop, MIDI both ways, the
   overlay page, NV, then fble2 loaded (1–2 weeks).
5. Web page: port `fm1pkg`, `pi32` and `analyze` to JavaScript or run them under Pyodide. The 8 KB validity table
   is shipped as is. Web MIDI install as in the fble installer.

## 11. What was tested

| Checked | How |
|---|---|
| `.fwsc` read and write | round trip byte-identical on all 7 official releases (SLOOP 2.6, 2.7.1, 2.8.1, 2.8.3; Felucca 1.5.1, 1.6.1, 1.7.0) and on our builds; every CRC checked on read |
| ISA facts | sweep agrees with objdump on every instruction of SLOOP 2.8.3 and Felucca main; call encoding fits 100% of direct calls; objdump's length agrees with the rule for all 52,721 valid first halfwords |
| End of code | first undecodable halfword: 0 / −2 / −2 bytes from the last function's end in the three builds (the earlier "unseen opcode" rule: +158 / +212 / +212, and it failed on SLOOP 2.8.1 and most forks) |
| Analyzer against symbols | 12 / 12 in SLOOP 2.8.3, Felucca main and Felucca 1.7.0 |
| Free RAM gaps | no symbol inside them in the builds; `_bss_end` / `_pool_end` used only as loop bounds |
| PC-limit guard site | matches `fm1_guard_enable` (SLOOP) and the inlined code in `fm1_cstart` (Felucca) in the builds' disassembly |
| Releases without symbols | table in section 6 |
| Forks | 124 latest-release `.fwsc` files analysed without errors (section 9) |
| Proof of concept | SLOOP 2.8.3 and Felucca 1.7.0: package rebuilt, re-read, 9 bytes changed, decodes as intended, rewrite identical |

| Hardware (section 7.10) | our SLOOP 2.8.3 + fext build, hook on `ui_draw`: crashes without the guard edit, runs with it |

**Not tested:**

- a patched official release on hardware (only our own build);
- Felucca on hardware;
- the overlay page;
- the socket blob;
- recipes for forks;
- Felucca 1.7.0's release-vs-tag difference.
