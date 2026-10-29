/* SPDX-License-Identifier: MPL-2.0
 * Copyright (C) 2026 Charles Vestal (radio.c of fm1-base, from fm1-ble and fm1-ctl: the shim's platform, the start,
 *                    the MIDI drain; adapted here)
 * Copyright (C) 2026 Jacek Mariański (the module: header, init, the host's table, the rings, its pages) */
/* fble, the Bluetooth MIDI module (an fext module: include/fext.h): fm1-base's ble/ (shim, le_midi.c) and JieLi's
 * BT libraries in an image of their own, run in place from the host's slot. This file is what radio.c is to
 * fm1-base's firmware, with the host's services (fext_host) in place of felucca-bsp's calls:
 *   the header (the build fills in the rest), init, poll, stop;
 *   the shim's platform (shim_ms, the interrupt nesting, shim_idle, delays from RAM, request_irq, the log, the NV);
 *   the MIDI rings: in from the two links (cable 0 the computer's, 1 the device's), out to them;
 *   fble_ui.c: the settings, the central's connecting and the pages (GLO > BLE in SLOOP).
 * Only the CPU's own instructions (cli / sti) and two registers are touched here directly: TIMER4's counter (the
 * busy delay, from RAM, as fm1-base's) and the watchdog's P33 feed inside it (felucca-bsp fm1_sys.h, inline). */
#include <stdint.h>
#include <string.h>
#include "shim.h"
#include <stddef.h>
#include "fext.h"
#include "fm1_cc.h"
#include "fm1_time.h"
#include "fm1_sys.h"

#ifndef FBLE_VERSION
#define FBLE_VERSION "1.0"
#endif

enum { FBLE_OFF, FBLE_STARTING, FBLE_READY, FBLE_FAILED };
enum { FBLE_DEV_OFF, FBLE_DEV_SCANNING, FBLE_DEV_CONNECTING, FBLE_DEV_SEARCHING, FBLE_DEV_READY };   /* le_midi.c's */

static const fext_host *H;
static volatile uint8_t state;                    /* FBLE_* */
static const fext_api api;

/* ---- the header: the loader's (fext.h). Its addresses, sizes and CRCs are the build's (build_module.py: from the
 * link) and the installer's (fext_reloc.py: moved to the host's slot and windows) */
const fext_api *fble_init(const fext_host *h);
__attribute__((used, section(".fext_header"))) const fext_header fble_hdr = {
    .magic = FEXT_MAGIC,
    .abi_major = FEXT_ABI_MAJOR,
    .abi_minor = FEXT_ABI_MINOR,
    .hdr_size = sizeof(fext_header),
    .caps = FEXT_C_MIDI | FEXT_C_RADIO,
    .name = "BLE",
    .version = FBLE_VERSION,
};

/* ---- the log: up to ~40 characters a line, to the host's */
static void hex8(char *b, uint32_t v)
{
    int i;
    for (i = 0; i < 8; i++)
        b[i] = "0123456789ABCDEF"[(v >> (28 - 4 * i)) & 15u];
}
static char *put_dec(char *b, uint32_t v)
{
    char t[12];
    int n = 0;
    do {
        t[n++] = (char)('0' + v % 10u);
        v /= 10u;
    } while (v);
    while (n)
        *b++ = t[--n];
    *b = 0;
    return b;
}
static char *put_str(char *b, const char *s)
{
    while (*s)
        *b++ = *s++;
    *b = 0;
    return b;
}
void shim_log(const char *s)
{
    if (H)
        H->log(s);
}

/* ---- the shim's platform (radio.c's) */
uint32_t shim_ms(void) { return H->ms(); }
static volatile uint32_t shim_depth;
uint32_t shim_irq_save(void)                     /* nests: the shim calls itself */
{
    __asm__ volatile("cli" ::: "memory");
    return shim_depth++;
}
void shim_irq_restore(uint32_t f)
{
    (void)f;
    if (shim_depth && --shim_depth == 0)
        __asm__ volatile("csync\n\tsti" ::: "memory");
}
int shim_in_irq(void) { return 0; }
volatile uint32_t shim_isr_blocks;
static void radio_stuck_check(void);
void shim_idle(void)                              /* every task waits: what is due, the watchdog */
{
    static uint8_t in;
    H->wdt_feed();
    if (!in) {
        in = 1;
        shim_timers();
        radio_stuck_check();
        in = 0;
    }
}
void shim_reboot(void) { H->reboot(); }
void shim_wdt(void) { H->wdt_feed(); }
/* JieLi's delay_us and friends: from RAM, timed by TIMER4, nothing called (the radio's trim runs them while it draws
 * the current that corrupts reads from the flash) */
__attribute__((section(".ram_text"), noinline, used)) void shim_delay_us(uint32_t us) { fm1_busy_us_inl(us); }
void shim_request_irq(int index, int prio, void (*handler)(void))
{
    char b[32] = "irq ", *q;
    q = put_dec(b + 4, (uint32_t)index);
    *q++ = ' ';
    *q++ = (char)('0' + (prio & 7));
    *q++ = ' ';
    hex8(q, (uint32_t)(uintptr_t)handler);
    q[8] = 0;
    shim_log(b);
    H->irq_attach(index, prio, handler);
}
int shim_store_load(void *dst, uint32_t max) { return H->nv_load(FEXT_NV_STACK, dst, max); }
int shim_store_save(const void *src, uint32_t len) { return H->nv_save(FEXT_NV_STACK, src, len); }

extern int syscfg_read(uint16_t id, void *buf, uint16_t len);
extern int syscfg_write(uint16_t id, void *buf, uint16_t len);
#define CFG_BT_MAC_ADDR 1                         /* include_lib/utils/syscfg/syscfg_id.h */
const uint8_t *bt_get_mac_addr(void)              /* the one kept, or a static random one, kept */
{
    static uint8_t mac[6];
    static const uint8_t none[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
    uint32_t i, h = 2166136261u;
    if (syscfg_read(CFG_BT_MAC_ADDR, mac, 6) == 6 && memcmp(mac, none, 6))
        return mac;
    h ^= fm1_ticks();
    for (i = 0; i < 6u; i++) {
        h = (h ^ (h >> 13)) * 16777619u + fm1_ticks();
        mac[i] = (uint8_t)(h >> 8);
    }
    mac[0] = (uint8_t)((mac[0] & 0xFC) | 0xC0);
    syscfg_write(CFG_BT_MAC_ADDR, mac, 6);
    return mac;
}

/* ---- the rings, and the ports' words (fext_api): port 0 the computer connected to the FM-1, 1 the device it
 * connected to */
#define RQ 64u
static uint32_t in_buf[RQ], out_buf[RQ];
static fext_ring r_in = {0, 0, RQ, in_buf}, r_out = {0, 0, RQ, out_buf};
static volatile uint32_t ports_out, ports_live;
static int ring_put(fext_ring *r, uint32_t v)
{
    if (r->w - r->r >= r->n)
        return 0;
    r->buf[r->w % r->n] = v;
    __asm__ volatile("" ::: "memory");
    r->w++;
    return 1;
}
static int ring_get(fext_ring *r, uint32_t *v)
{
    if (r->r == r->w)
        return 0;
    *v = r->buf[r->r % r->n];
    __asm__ volatile("" ::: "memory");
    r->r++;
    return 1;
}

/* ---- the start (radio.c radio_begin, radio_settle, ble_start) */
static uint32_t radio_t0;
static uint8_t radio_starting, quiet_on, os_up;
#ifdef SHIM_STATS
static void radio_stuck_check(void)              /* (a start that hangs: the tasks' states into the log) */
{
    static uint32_t said;
    extern void shim_os_dump(void);
    if (radio_starting && shim_ms() - radio_t0 > 3000u && said != radio_t0) {
        said = radio_t0;
        shim_log("still starting after 3 s:");
        shim_os_dump();
    }
}
#else
static void radio_stuck_check(void) {}
#endif
static void radio_settle(int ready)
{
    if (ready)
        radio_starting = 0;
    else if (shim_ms() - radio_t0 <= 15000u)
        return;
    if (quiet_on && shim_ms() - radio_t0 > 1000u) {
        quiet_on = 0;
        H->quiet(0);
        shim_log(ready ? "sound back: ready" : "sound back: not ready");
    }
}

extern void btstack_init(void);
extern void bt_ble_init(void);
extern void lmp_set_sniff_disable(void);
extern void bt_max_pwr_set(uint8_t pwr, uint8_t pg_pwr, uint8_t iq_pwr, uint8_t ble_pwr);
extern void lib_make_ble_address(uint8_t *ble, uint8_t *edr);
extern int le_controller_set_mac(void *addr);
extern const char *ble_midi_name(void);
#define BT_TX_POWER 8
#define BLE_TX_POWER 11

struct bt_event_s { uint8_t event; uint8_t args[7]; uint32_t value; };
void shim_bt_event(int from, const void *event, int len)
{
    const struct bt_event_s *e = event;
    (void)len;
    if (from == 3 && e && e->event == 3 && state == FBLE_STARTING) {   /* BT_EVENT_FROM_CON, BT_STATUS_INIT_OK */
        extern uint32_t shim_heap_peak;
        extern const uint32_t shim_heap_size;
        char b[40], *q;
        put_str(put_str(b, "BT ready: "), ble_midi_name());
        shim_log(b);
        q = put_dec(put_str(b, "heap peak "), shim_heap_peak);
        put_dec(put_str(q, " of "), shim_heap_size);
        shim_log(b);
        bt_ble_init();
        state = FBLE_READY;
    }
}

static int fble_start(void)
{
    uint8_t addr[6];
    if (state != FBLE_OFF)
        return state == FBLE_FAILED ? -1 : 0;
    state = FBLE_STARTING;
    radio_t0 = shim_ms();
    radio_starting = 1;
    shim_log("BT: starting");
    quiet_on = 1;
    H->quiet(1);                                  /* the trim's current: no flash reads from interrupts meanwhile */
    if (!os_up) {
        os_up = 1;
        shim_os_init();
        H->stack_floor(shim_stacks_lo());         /* the tasks' stacks: the RAM window */
    }
#ifndef FBLE_NO_BREDR
    lmp_set_sniff_disable();                      /* (a classic link's sniff: no classic code in the diet's module) */
#endif
    bt_max_pwr_set(BT_TX_POWER, 6, 6, BLE_TX_POWER);
    lib_make_ble_address(addr, (uint8_t *)bt_get_mac_addr());
    le_controller_set_mac(addr);
    btstack_init();
    return 0;
}

/* ---- MIDI: in from the stack, out to it (radio.c's drain, one ring for both links: the packet's cable picks) */
extern int ble_midi_send_many(int link, const uint32_t *pkts, const uint16_t *ms, int n);
extern int ble_midi_connected(int link);
extern int ble_midi_peripheral_connected(void);
extern int ble_midi_peripheral_linked(void);
extern void ble_midi_enable(int on);
extern void ble_midi_advertise(int on, int ready);
extern void ble_midi_scan(int on);
extern int ble_midi_found(int i, char *name, int *rssi);
extern int ble_midi_found_addr(int i, uint8_t *addr);
extern int ble_midi_connect(int i);
extern void ble_midi_disconnect(void);
extern int ble_midi_central_state(void);
extern void ble_midi_poll(void);
static uint32_t rx_lost, rx_n;
void ble_midi_receive(uint32_t pkt)               /* the stack (in poll): cable 1 = the device's link */
{
    rx_n++;
    if (!ring_put(&r_in, pkt))
        rx_lost++;
}
void ble_midi_raw(const uint8_t *b, uint16_t len, int dev) { (void)b, (void)len, (void)dev; }   /* (a MIDI tap's) */
static uint32_t held = 0xFFFFFFFFu;               /* a packet the stack did not take yet */
static void midi_drain(void)
{
    int round;
    for (round = 0; round < 8; round++) {
        uint32_t p;
        uint16_t ms;
        int link;
        if (held == 0xFFFFFFFFu && !ring_get(&r_out, &held))
            return;
        p = held;
        link = (p >> 4 & 0xFu) == 1u;
        if (!ble_midi_connected(link)) {
            held = 0xFFFFFFFFu;                   /* nobody there: dropped */
            continue;
        }
        ms = (uint16_t)(shim_ms() & 0x1FFFu);
        p &= ~0xF0u;
        if (!ble_midi_send_many(link, &p, &ms, 1))
            return;                               /* the stack is full: again next poll */
        held = 0xFFFFFFFFu;
    }
}

/* ---- high water (M5, the diet's sizes checked on the device): every 5 s once the stack is up, a line when anything
 * went higher than the last line said:  "hw [f<full>] h<heap peak> s<stack>,<stack> q<queue>,<queue>,<queue> t<timers>"
 * (posts refused by a full queue, only when any; bytes of the heap; bytes of each BT task's stack, from its "STAK"
 * paint; ints in each task queue, main's first; timers at once). Each against what the build gave it:
 * build_module.py LEVERS */
#ifdef SHIM_STATS
extern uint32_t shim_heap_peak;
extern uint16_t shim_q_peak[], shim_q_full, shim_timers_peak;
extern int shim_stack_bytes(int task);
#ifndef SHIM_NTASK
#define SHIM_NTASK 4
#endif
static void hw_report(int force)
{
    static uint32_t last_t, said[2 * SHIM_NTASK + 3];
    uint32_t now[2 * SHIM_NTASK + 3];
    int i, n = 0, grew = force;
    char b[48], *q;
    if (!force && shim_ms() - last_t < 5000u)
        return;
    last_t = shim_ms();
    now[n++] = shim_heap_peak;
    for (i = 1; i < SHIM_NTASK; i++)
        now[n++] = (uint32_t)shim_stack_bytes(i);
    for (i = 0; i < SHIM_NTASK; i++)
        now[n++] = shim_q_peak[i];
    now[n++] = shim_timers_peak;
    now[n++] = shim_q_full;
    for (i = 0; i < n; i++)
        if (now[i] > said[i]) {
            said[i] = now[i];
            grew = 1;
        }
    if (!grew)
        return;
    q = put_str(b, "hw");                         /* (the host's lines: 39 characters; posts refused first, when any) */
    if (now[2 * SHIM_NTASK + 1])
        q = put_dec(put_str(q, " f"), now[2 * SHIM_NTASK + 1]);
    q = put_dec(put_str(q, " h"), now[0]);
    for (i = 1; i < SHIM_NTASK; i++)
        q = put_dec(put_str(q, i == 1 ? " s" : ","), now[i]);
    for (i = 0; i < SHIM_NTASK; i++)
        q = put_dec(put_str(q, i == 0 ? " q" : ","), now[SHIM_NTASK + i]);
    put_dec(put_str(q, " t"), now[2 * SHIM_NTASK]);
    shim_log(b);
}
#else
#define hw_report(force) ((void)0)
#endif

#include "fble_ui.c"                              /* the settings, the central, the pages */

static void fble_poll(int view)
{
    if (state == FBLE_OFF && set.radio)
        fble_start();                             /* (GLO > BLE > RADIO: ON, now or as kept) */
    if (state == FBLE_OFF)
        return;
    shim_timers();
    shim_run();
    if (state == FBLE_READY) {
        midi_drain();
        ble_midi_poll();
        ui_task(view);
    }
    radio_stuck_check();
    radio_settle(state == FBLE_READY);
    if (state == FBLE_READY)
        hw_report(0);
}
static void fble_stop(void)
{
    if (state == FBLE_READY)
        ble_midi_enable(0);
    ports_out = ports_live = 0;
}

static const fext_api api = {
    .abi_major = FEXT_ABI_MAJOR, .abi_minor = FEXT_ABI_MINOR, .size = sizeof(fext_api),
    .poll = fble_poll, .stop = fble_stop,
    .midi_in = &r_in, .midi_out = &r_out, .ports_out = &ports_out, .ports_live = &ports_live,
    .pages = 2, .column = ui_column, .knob = ui_knob, .line = ui_line, .control = ui_control,
};

/* ---- init: the loader has copied .ram_text and .data and cleared .bss and .pool (fext_header.copy). Nothing of the
 * radio is touched yet: that is poll's, when RADIO is ON */
extern char _data_start[], _bss_end[];
const fext_api *fble_init(const fext_host *h)
{
    char b[40], *q;
    if (!h || h->abi_major != FEXT_ABI_MAJOR || h->size < offsetof(fext_host, flash_read) || !h->log || !h->ms || !h->wdt_feed ||
        !h->irq_attach || !h->stack_floor || !h->quiet || !h->nv_load || !h->nv_save || !h->reboot || !h->say)
        return 0;
    H = h;
    q = put_str(put_str(put_str(b, "fble "), FBLE_VERSION), ": init ok, ");
    q = put_dec(q, (uint32_t)(uintptr_t)_bss_end - (uint32_t)(uintptr_t)_data_start);
    put_str(q, " B low");
    shim_log(b);
    state = FBLE_OFF;
    set_load();
    return &api;
}
