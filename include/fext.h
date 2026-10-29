/* SPDX-License-Identifier: MPL-2.0
 * Copyright (C) 2026 Jacek Mariański */
/* fext: a module for an FM-1 firmware (ABI 1.0). The module is an image of its own, written into a flash slot the
 * firmware (the host) keeps for it, and run in place from there; the host links none of it. The module adds what
 * the firmware does not have (fble, the first: Bluetooth MIDI), with its own pages in the host's menu, its own MIDI
 * ports and its own settings, through the two tables below: the host's services (fext_host) and the module's
 * calls (fext_api). Nothing in here names a host or a module: the host knows no Bluetooth, the module no firmware.
 *
 * Where things are
 *   flash  one slot: the header first, then code and the load images of the RAM parts, run in place (XIP). The
 *          host says where (its slot's offset and size, and the CPU address it runs at)
 *   RAM    three windows the host keeps free, each of a kind (FEXT_W_*): CODE, RAM the core runs code from (code
 *          that must run while the flash is busy: interrupts, delays); DMA, RAM the peripherals reach (.data, .bss:
 *          a radio's buffers); RAM, any (.pool: stacks). The module says how much of each it uses (its header), the
 *          host how much it keeps (its info); each window begins on a 256-byte boundary
 * A module is built once and installed into any host: the installer (fext_push.py) asks the host where its slot
 * and windows are (SysEx "FB" op 10), moves the image there (its relocation table: fext_reloc.py; the table is
 * the file's, never written to the flash) and writes it. The loader then only checks (fext_load.h): an image is
 * run only where it was moved to.
 *
 * At boot the host checks the header (fext_load.h fext_check), does the header's copy steps (.ram_text and .data
 * in, .bss and .pool cleared), lets the core run code in the slot and the CODE window, and calls init with its
 * services. A module that does not answer init, or a run that crashed, is not loaded again until asked.
 *
 * The rules of the road: the main loop calls everything in fext_api (never an interrupt); the module's interrupts
 * come through irq_attach; the host's MIDI out goes into midi_out from its interrupts too (a ring, one producer, no
 * call into the module). Texts are UTF-8: the host draws what its font has (fext_text.h in SLOOP's socket folds
 * the rest). */
#pragma once
#include <stdint.h>

#define FEXT_MAGIC 0x54584546u                    /* "FEXT", little-endian */
#define FEXT_ABI_MAJOR 1u                         /* another major: not loaded */
#define FEXT_ABI_MINOR 0u                         /* more minor: backwards compatible additions (the tables' size) */

enum { FEXT_W_CODE, FEXT_W_DMA, FEXT_W_RAM, FEXT_NW };
#define FEXT_W_ALIGN 256u                         /* a window's beginning: the module is moved by multiples of it */

typedef struct { uint32_t lo, hi; } fext_win;     /* [lo, hi), CPU addresses; lo == hi: none */
typedef struct { uint32_t dst, src, n; } fext_copy;   /* n bytes from src (in the slot) to dst; src 0: zeros */
#define FEXT_NCOPY 4                              /* .ram_text, .data, .bss, .pool */

/* the header: the first bytes of the slot. The build fills in everything below name and version from the link,
 * the installer moves the addresses, both fill in the CRCs */
typedef struct {
    uint32_t magic;                               /* FEXT_MAGIC */
    uint16_t abi_major, abi_minor;
    uint32_t hdr_size;                            /* sizeof (fext_header) */
    uint32_t hdr_crc;                             /* CRC-32 (zlib's) of the header with this field 0 */
    uint32_t size;                                /* the image's bytes, the header's included */
    uint32_t crc;                                 /* CRC-32 of the image after the header */
    uint32_t xip;                                 /* the CPU address of its first byte: the host's slot */
    fext_win win[FEXT_NW];                        /* the RAM it uses, each window's */
    fext_copy copy[FEXT_NCOPY];                   /* what the loader does before init */
    uint32_t init;                                /* const fext_api *init(const fext_host *): 0 = refused */
    uint32_t caps;                                /* FEXT_C_* */
    char name[8];                                 /* its pages' title, ASCII: "BLE" (the second page "BLE 2") */
    char version[16];
} fext_header;
#define FEXT_C_MIDI 1u                            /* MIDI ports (fext_api midi_in, midi_out) */
#define FEXT_C_RADIO 2u                           /* it turns a radio on (only when the player says so) */

/* a ring of 4-byte USB-MIDI packets, one producer and one consumer: w and r count up and wrap; the producer writes
 * buf[w % n], then w; the consumer reads buf[r % n], then r. The packet's cable is the module's port */
typedef struct {
    volatile uint32_t w, r;
    uint32_t n;
    uint32_t *buf;
} fext_ring;

/* the host's services */
#define FEXT_NV_STACK 0                           /* nv: the module's own store (a radio's address, its calibration):
                                                   * up to 1536 bytes, written at once */
#define FEXT_NV_SET 1                             /* its settings: up to 192 bytes, written when the host is quiet
                                                   * (not while it plays): src stays valid until then */
typedef struct {
    uint16_t abi_major, abi_minor;
    uint32_t size;                                /* sizeof (fext_host): later members only if it is big enough */
    uint32_t (*ms)(void);                         /* milliseconds, monotonic */
    void (*log)(const char *line);                /* a line for the host's log (up to ~40 characters) */
    void (*wdt_feed)(void);
    void (*irq_attach)(int index, int prio, void (*isr)(void));   /* JieLi's interrupt index, priority 0..2 */
    void (*stack_floor)(uint32_t lo);             /* the module's task stacks go down to lo (the RAM window) */
    void (*quiet)(int on);                        /* no flash reads from the host's interrupts meanwhile (a radio's
                                                   * calibration draws a current that corrupts them): ~1 s */
    int (*nv_load)(int id, void *dst, uint32_t max);   /* FEXT_NV_*: its length, or < 0 none */
    int (*nv_save)(int id, const void *src, uint32_t n);
    void (*reboot)(void);
    void (*say)(const char *text);                /* a message on the screen, a moment */
} fext_host;

/* the module's pages: four columns (a knob each) and the middle's lines, as the module says them. The host draws
 * them in its own style and turns its knobs into knob() */
enum { FEXT_S_NORMAL, FEXT_S_DIM, FEXT_S_BRIGHT };   /* a column's or line's look: as a value, off, the one that
                                                       * matters (connected, the cursor's) */
typedef struct {
    char label[8];                                /* "RADIO"; "" an empty column */
    char value[24];                               /* "ON" */
    char unit[4];                                 /* after the value, smaller: "/3" */
    uint8_t style;                                /* FEXT_S_* */
    uint8_t rsv[3];
} fext_col;
#define FEXT_L_CURSOR 1u                          /* fext_line mark: a cursor before it */
#define FEXT_L_STAR 2u                            /* a star (the chosen one) */
#define FEXT_L_ITEM 4u                            /* an item of a list: indented, room for the two above */
typedef struct {
    char text[40];
    char right[8];                                /* right-aligned: "-62" */
    uint8_t style, mark;                          /* FEXT_S_*, FEXT_L_* */
    uint8_t gap;                                  /* 1: a gap above it (a list after the state) */
    uint8_t rsv;
} fext_line;

/* the module's calls */
typedef struct {
    uint16_t abi_major, abi_minor;
    uint32_t size;
    void (*poll)(int view);                       /* every main-loop pass; view: the module's page shown, -1 none */
    void (*stop)(void);                           /* before the host restarts or rewrites it: links down */
    fext_ring *midi_in, *midi_out;                /* MIDI from its ports, to them (the host's MIDI out) */
    volatile uint32_t *ports_out;                 /* bit p: the host's MIDI out goes to port p too (the module's
                                                   * setting, its links) */
    volatile uint32_t *ports_live;                /* bit p: port p has a link; when a bit goes, the host ends the
                                                   * notes that came from it */
    uint32_t pages;                               /* its pages in the host's menu, 1..2 */
    void (*column)(uint32_t page, uint32_t col, fext_col *c);
    void (*knob)(uint32_t page, uint32_t col, int32_t steps);
    int (*line)(uint32_t page, uint32_t i, fext_line *l);   /* the middle's line i: 0 = no more. Line 0 of page 0
                                                             * is the module's state (the host's status says it) */
    int (*control)(const uint8_t *in, uint32_t n, char *out, uint32_t max);   /* the module's own commands (SysEx
                                                   * "FB" op 7, 7-bit bytes): a text answer, its length */
} fext_api;

typedef const fext_api *(*fext_init_fn)(const fext_host *h);
