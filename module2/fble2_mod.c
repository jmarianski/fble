/* SPDX-License-Identifier: MPL-2.0
 * Copyright (C) 2026 Jacek Mariański */
/* fble2, a PROTOTYPE (docs/FBLE2-PLAN.md): Bluetooth MIDI for the FM-1 as an fext module (include/fext.h) on
 * Optimist's open BLE stack (optimist-ble/: its link layer, host, GATT, SMP, central and the WL82 baseband driver,
 * all "GPL-3.0-only OR MPL-2.0", used here under MPL-2.0), with no JieLi library in it. One unit: the stack's files
 * are included below with BLE_API static (as Optimist's own unity build does, ble_stack.c), then this module's glue,
 * then fble's pages (module/fble_ui.c, unchanged) over a small adapter that gives them the calls they made of
 * fm1-base's le_midi.c.
 *
 * Written from the stack's headers (ble.h, ble_ll.h, ble_vm.h, ble_scan.h, ble_store.h, ble_host.h) and
 * docs/BLE-STACK.md; Optimist's firmware glue (io/midi/midi_ble.c, ble_devices.c, ble_connect.c: GPL-3.0-only) is
 * not copied.
 *
 * What differs from fble (JieLi's controller):
 *   - ONE link at a time (Optimist's stack): the computer's (peripheral, port 0) or the device's (central, port 1),
 *     not both. While a device is connected the module does not advertise; with PC ON and MODE FIRST / PICK
 *     scanning and advertising take turns (3 s each) until one link is up;
 *   - no tasks, no heap: the stack runs in its two interrupts (IRQ 45, 29) and the main loop's poll;
 *   - the radio's trims are read from the factory VM (0xE7000-0xE9FFF) through the host (fext 1.1 flash_read), and a
 *     copy is kept in the module's NV; with neither (a 1.0 host, a VM erased) the radio measures itself at each start
 *     (felucca-bsp's fm1_rf_cal.h, as fm1-base's own stack does: its 62b1ad5), with the board's crystal and PA values;
 *     -DFBLE2_CAL_ALWAYS=1 measures always;
 *   - the signal column shows Optimist's relative bars (1..3), not dBm.
 * Not tried on an FM-1: see docs/FBLE2-PLAN.md, "What was tested". */
#include <stdint.h>
#include <stddef.h>
#include "fext.h"

#define BLE_API static
#include "ble_stack.c"                            /* (optimist-ble/ble: the stack and the WL82 driver) */
#include "ble_vm.c"                               /* the factory VM's reader, the trims' copy */

#ifndef FBLE_VERSION
#define FBLE_VERSION "2.0-proto"
#endif

/* ---- what the compiler calls (-fno-builtin; no C library in a module) */
void *memcpy(void *d, const void *s, size_t n)
{
    uint8_t *a = d;
    const uint8_t *b = s;
    while (n--)
        *a++ = *b++;
    return d;
}
void *memset(void *d, int c, size_t n)
{
    uint8_t *a = d;
    while (n--)
        *a++ = (uint8_t)c;
    return d;
}
int memcmp(const void *x, const void *y, size_t n)
{
    const uint8_t *a = x, *b = y;
    for (; n; n--, a++, b++)
        if (*a != *b)
            return *a < *b ? -1 : 1;
    return 0;
}
size_t strlen(const char *s)
{
    size_t n = 0;
    while (s[n])
        n++;
    return n;
}

enum { FBLE_OFF, FBLE_STARTING, FBLE_READY, FBLE_FAILED };
enum { FBLE_DEV_OFF, FBLE_DEV_SCANNING, FBLE_DEV_CONNECTING, FBLE_DEV_SEARCHING, FBLE_DEV_READY };   /* (fble_ui.c's) */

static const fext_host *H;
static volatile uint8_t state;                    /* FBLE_* */
static const fext_api api;

/* ---- the header: the loader's (fext.h). Addresses, sizes and CRCs: the build's and the installer's */
const fext_api *fble_init(const fext_host *h);
__attribute__((used, section(".fext_header"))) const fext_header fble2_hdr = {
    .magic = FEXT_MAGIC,
    .abi_major = FEXT_ABI_MAJOR,
    .abi_minor = 1,                               /* (it uses 1.1's flash_read and irq_attach2 when the host has them) */
    .hdr_size = sizeof(fext_header),
    .caps = FEXT_C_MIDI | FEXT_C_RADIO,
    .name = "BLE",
    .version = FBLE_VERSION,
};

/* ---- the log, and its little formatters (fble_ui.c uses them too) */
static char *put_str(char *b, const char *s)
{
    while (*s)
        *b++ = *s++;
    *b = 0;
    return b;
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
static void hex8(char *b, uint32_t v)
{
    int i;
    for (i = 0; i < 8; i++)
        b[i] = "0123456789ABCDEF"[(v >> (28 - 4 * i)) & 15u];
    b[8] = 0;
}
static void say_log(const char *s)
{
    if (H)
        H->log(s);
}
static uint32_t shim_ms(void) { return H->ms(); }   /* (fble_ui.c's name for the host's milliseconds) */

/* ---- interrupts: the host owns the vectors and the levels (hal/fm1_irq.h maps the driver's fm1_irq_attach here).
 * A 1.1 host puts them below its audio, as Optimist runs them; a 1.0 host only has irq_attach, above the audio */
void fble2_irq_attach(uint32_t n, void (*h)(void), uint32_t prio)
{
    char b[40], *q = put_str(b, "irq ");
    (void)prio;
    q = put_dec(q, n);
    if (FEXT_HOST_HAS(H, irq_attach2)) {
        H->irq_attach2((int)n, FEXT_IRQ_LOW, h);
        q = put_str(q, " low ");
    } else {
        H->irq_attach((int)n, 0, h);
        q = put_str(q, " HIGH(1.0) ");
    }
    hex8(q, (uint32_t)(uintptr_t)h);
    say_log(b);
}
static void irqs_hold(int hold)                   /* the stack's state changed from the main loop: its IRQs held */
{
    if (FEXT_HOST_HAS(H, irq_hold)) {
        H->irq_hold(FM1_IRQ_BLE_EVENT, hold);
        H->irq_hold(FM1_IRQ_BLE_RX, hold);
    } else {
        fm1_ble_irqs_hold(hold);                  /* (the controller's enable bits of its own two lines only) */
    }
}

/* ---- the rings and the ports' words (fext_api): port 0 the computer connected to the FM-1, 1 the device it
 * connected to. Now the BLE interrupts are the producer of midi_in and the consumer of midi_out */
#define RQ 64u
static uint32_t in_buf[RQ], out_buf[RQ];
static fext_ring r_in = {0, 0, RQ, in_buf}, r_out = {0, 0, RQ, out_buf};
static volatile uint32_t ports_out, ports_live;
static uint32_t rx_lost, rx_n;
static int ring_put(fext_ring *r, uint32_t v)
{
    if (r->w - r->r >= r->n)
        return 0;
    r->buf[r->w % r->n] = v;
    __asm__ volatile("" ::: "memory");
    r->w++;
    return 1;
}

/* ---- the module's NV (FEXT_NV_STACK, up to 1536 B, written at once): the address, the trims' copy, the bond a
 * computer made (peripheral), the device the module connected to and its bond (central). Optimist keeps the same
 * things in its settings record (persist_t: ble_addr, ble_rf, ble_bond, ble_dev) */
#define NV_MAGIC 0x324E4246u                      /* "FBN2" */
struct fble2_nv {
    uint32_t magic;
    uint8_t ver, has_addr, has_bond, rsv;
    uint8_t addr[6], rsv2[2];
    uint8_t rf[BLE_RF_COPY_SIZE];                 /* ble_vm.h's copy */
    uint8_t bond_rand[8], bond_ediv[2], bond_ltk[16];   /* (a computer's: ble_app_bond) */
    uint8_t dev[BLE_DEV_STORE_SIZE];              /* ble_store.h's raw octets */
};
static struct fble2_nv nv;
static uint8_t nv_dirty;
static uint32_t nv_due;
static void nv_load(void)
{
    if (H->nv_load(FEXT_NV_STACK, &nv, sizeof nv) != (int)sizeof nv || nv.magic != NV_MAGIC || nv.ver != 1u) {
        memset(&nv, 0, sizeof nv);
        nv.magic = NV_MAGIC;
        nv.ver = 1;
    }
}
static void nv_later(uint32_t ms)                 /* (a bond from inside a link: written a moment later, not in it) */
{
    nv_dirty = 1;
    nv_due = shim_ms() + ms;
}
static void nv_flush(int now)
{
    if (!nv_dirty || (!now && (int32_t)(shim_ms() - nv_due) < 0))
        return;
    nv_dirty = 0;
    if (H->nv_save(FEXT_NV_STACK, &nv, sizeof nv) < 0)
        say_log("fble2: NV write failed");
}

/* ---- the radio's trims: the factory VM through the host (1.1 flash_read), else the copy; none: measured live */
static int vm_rd(void *ctx, uint32_t off, uint8_t *dst, uint32_t n)
{
    (void)ctx;
    return H->flash_read(off, dst, n) == 0 ? 0 : -1;
}
static int trims(struct ble_rf_trims *use)
{
    struct ble_vm_info info;
    struct ble_rf_trims vm;
    int complete = 0, save = 0, src;
    char b[40], *q;
    memset(&info, 0, sizeof info);
    memset(&vm, 0, sizeof vm);
    if (FEXT_HOST_HAS(H, flash_read))
        complete = ble_vm_scan(vm_rd, 0, &info, &vm, 0, 0);
#if FBLE2_CAL_ALWAYS
    complete = 0;                                 /* (the build asked: measured at every start, as fm1-base does) */
    src = BLE_RF_NONE;
#else
    src = ble_rf_choose(complete, &vm, info.area, nv.rf, use, &save);
#endif
    if (src == BLE_RF_NONE) {                     /* no stored set: the radio measures itself (felucca-bsp's
                                                   * fm1_rf_cal.h), with the board's crystal and PA values */
        static const struct ble_rf_trims board = {{0x0B, 0x0B}, {1, 7, 4, 7, 11, 1, 7}, {0}, {0}};
        *use = board;
        fm1_rf_cal_on = 1;
        save = 0;
    } else {
        fm1_rf_cal_on = 0;                        /* (the stored set: as the factory firmware uses it) */
    }
    q = put_str(b, "trims: ");
    q = put_str(q, src == BLE_RF_FROM_VM ? "VM " : src == BLE_RF_FROM_COPY ? "copy " : "measured ");
    hex8(q, info.area);
    say_log(b);
    if (save)
        nv_later(0);                              /* (the radio is not on yet: written now, nv_flush below) */
    return src;
}

/* ---- MIDI from and to the stack: the BLE interrupts' context (ble.h). One link: its port is the role's */
static uint32_t link_port(void)
{
#if BLE_CENTRAL
    return ble_ll_central() ? 1u : 0u;
#else
    return 0u;
#endif
}
BLE_API void ble_app_midi_in(uint32_t pkt, uint16_t ts, uint16_t last_ts)
{
    (void)ts, (void)last_ts;                      /* (played as it comes: the host has no timed input queue) */
    rx_n++;
    if (!ring_put(&r_in, (pkt & ~0xF0u) | link_port() << 4))
        rx_lost++;
}
BLE_API int ble_app_midi_peek(uint32_t *pkt, uint32_t *t_ms)
{
    uint32_t p = link_port();
    while (r_out.r != r_out.w) {
        uint32_t v = r_out.buf[r_out.r % r_out.n];
        if ((v >> 4 & 0xFu) == p) {
            *pkt = v & ~0xF0u;
            *t_ms = ble_hw_time_us() / 1000u;     /* (the host's packets carry no time: now) */
            return 1;
        }
        r_out.r++;                                /* for the other port: no link there (one at a time) */
    }
    return 0;
}
BLE_API void ble_app_midi_pop(void) { r_out.r++; }
BLE_API void ble_app_state(void) {}               /* (polled: ble_midi_poll) */
#if BLE_SMP_LEGACY
static volatile uint8_t bond_new;
static uint8_t bond_tmp[26];
BLE_API void ble_app_bond(const uint8_t rand[8], uint16_t ediv, const uint8_t ltk[16])
{
    ble_cpy(bond_tmp, rand, 8);
    bond_tmp[8] = (uint8_t)ediv;
    bond_tmp[9] = (uint8_t)(ediv >> 8);
    ble_cpy(bond_tmp + 10, ltk, 16);
    bond_new = 1;
}
BLE_API void ble_app_peer_id(const uint8_t irk[16], const uint8_t addr[6], uint8_t addr_rand)
{
    (void)irk, (void)addr, (void)addr_rand;       /* (RAM only in Optimist too) */
}
#endif

/* ---- the calls fble_ui.c makes (fm1-base's le_midi.c names), on this stack. The main loop's */
static uint8_t en, adv_want, scan_want, app_en = 0xFF, app_scan = 0xFF;
#if BLE_CENTRAL
static struct ble_scan_tab tab;
static uint8_t idx[BLE_SCAN_N];
static uint32_t nidx, age_t;
static struct ble_dev_store store;
static char cname[BLE_NAME_MAX + 1u];
static uint8_t c_addr[6], c_rand, c_was;
#if BLE_CEN_PAIR
static volatile uint8_t keys_new;
static struct ble_keys keys_tmp;
static uint32_t pk_said;
BLE_API void ble_app_central_keys(const struct ble_keys *k)
{
    keys_tmp = *k;
    keys_new = 1;
}
#endif
#endif
static int ble_midi_peripheral_linked(void)
{
#if BLE_CENTRAL
    return ble_connected() && !ble_ll_central();
#else
    return ble_connected();
#endif
}
static int ble_midi_peripheral_connected(void) { return ble_midi_peripheral_linked() && ble_midi_ready(); }
static void radio_apply(void)                     /* what the settings ask, into the link layer */
{
    uint8_t s = 0, e;
#if BLE_CENTRAL
    if (ble_ll_central() || ble_ll_initiating())
        return;                                   /* (the central's link: no advertising, no scan meanwhile) */
    s = en && scan_want;
    if (s && adv_want && (shim_ms() / 3000u & 1u))
        s = 0;                                    /* (both wanted: turns, scan / advertise) */
#endif
    e = en && (adv_want || s);
    if (ble_midi_peripheral_linked()) {
        if (!en || !adv_want) {                   /* PC turned OFF (or RADIO): the computer's link goes */
            irqs_hold(1);
            ble_ll_disconnect(BLE_ERR_REMOTE_USER);
            irqs_hold(0);
        }
        return;
    }
    if (e == app_en && s == app_scan)
        return;
    app_en = e;
    app_scan = s;
    irqs_hold(1);
#if BLE_CENTRAL
    ble_ll_scan(s);
#endif
    ble_enable(e);
    irqs_hold(0);
}
static void ble_midi_enable(int on)
{
    en = (uint8_t)(on != 0);
    if (!en) {
        irqs_hold(1);
#if BLE_CENTRAL
        ble_central_cancel();
#endif
        ble_enable(0);
        app_en = app_scan = 0;
        irqs_hold(0);
    }
}
static void ble_midi_advertise(int on, int ready) { (void)ready, adv_want = (uint8_t)(on != 0); }
static void ble_midi_scan(int on) { scan_want = (uint8_t)(on != 0); }
static int ble_midi_found(int i, char *name, int *rssi)
{
#if BLE_CENTRAL
    const struct ble_found *f;
    if (i < 0 || (uint32_t)i >= nidx)
        return 0;
    f = &tab.e[idx[i]];
    if (f->name[0]) {
        uint32_t k;
        for (k = 0; k < 20u && f->name[k]; k++)
            name[k] = f->name[k];
        name[k] = 0;
    } else {
        ble_addr_text(f->addr, name);
    }
    *rssi = (int)ble_scan_bars(&tab, idx[i]);     /* (relative bars 1..3, not dBm) */
    return 1;
#else
    (void)i, (void)name, (void)rssi;
    return 0;
#endif
}
static int ble_midi_found_addr(int i, uint8_t *addr)
{
#if BLE_CENTRAL
    if (i < 0 || (uint32_t)i >= nidx)
        return 0;
    ble_cpy(addr, tab.e[idx[i]].addr, 6);
    return 1;
#else
    (void)i, (void)addr;
    return 0;
#endif
}
static int ble_midi_connect(int i)
{
#if BLE_CENTRAL
    struct ble_peer p;
    const struct ble_found *f;
    int ok;
    if (i < 0 || (uint32_t)i >= nidx || !en)
        return 0;
    f = &tab.e[idx[i]];
    memset(&p, 0, sizeof p);
    ble_cpy(p.addr, f->addr, 6);
    p.addr_rand = f->addr_rand;
    if (ble_store_has_last(&store) && !memcmp(store.dev.addr, f->addr, 6)) {   /* (the one kept: its bond) */
        if (store.dev.info & BLE_DEV_BONDED) {
            p.bonded = 1;
            ble_cpy(p.ltk, store.dev.ltk, 16);
            ble_cpy(p.rand, store.dev.rand, 8);
            p.ediv = (uint16_t)(store.dev.ediv[0] | store.dev.ediv[1] << 8);
        }
        p.sec = (uint8_t)((store.dev.sec & BLE_DEV_SEC_AUTH ? BLE_PEER_AUTH : 0u) |
                          (store.dev.sec & BLE_DEV_SEC_MITM ? BLE_PEER_MITM : 0u));
    }
    ble_cpy(c_addr, f->addr, 6);
    c_rand = f->addr_rand;
    ble_cpy((uint8_t *)cname, (const uint8_t *)f->name, sizeof cname);
    irqs_hold(1);
    if (ble_ll_scanning())
        ble_ll_scan(0);
    ok = ble_central_connect(&p);
    app_en = app_scan = 0xFF;                     /* (afterwards: what the settings ask, again) */
    irqs_hold(0);
    return ok;
#else
    (void)i;
    return 0;
#endif
}
static void ble_midi_disconnect(void)
{
#if BLE_CENTRAL
    irqs_hold(1);
    ble_central_cancel();
    app_en = app_scan = 0xFF;
    irqs_hold(0);
#endif
}
static int ble_midi_central_state(void)
{
#if BLE_CENTRAL
    if (ble_ll_central() || ble_ll_initiating()) {
        uint8_t s = ble_central_state();
        return s == BLE_CS_READY ? FBLE_DEV_READY : s == BLE_CS_SETUP ? FBLE_DEV_SEARCHING : FBLE_DEV_CONNECTING;
    }
    if (en && scan_want)
        return FBLE_DEV_SCANNING;
#endif
    return FBLE_DEV_OFF;
}
static void ble_midi_poll(void)                   /* every main-loop pass once the radio runs */
{
#if BLE_CENTRAL
    uint32_t now = shim_ms();
    uint8_t pdu[2 + 37];
    uint16_t rssi;
    uint8_t n, c;
    while ((n = ble_ll_scan_take(pdu, &rssi)) != 0)
        ble_scan_add(&tab, pdu, n, rssi, now);
    if (now - age_t > 1000u) {
        age_t = now;
        ble_scan_age(&tab, now);
    }
    nidx = ble_scan_list(&tab, idx);
    c = ble_central_state() == BLE_CS_READY && ble_ll_central();
    if (c && !c_was) {                            /* connected out: this device is the one kept */
        ble_store_set_last(&store, c_addr, c_rand, cname, BLE_KIND_MIDI);
        ble_store_save(&store, nv.dev);
        nv_later(2000u);
    }
    if (!c && c_was && ble_central_fail() == BLE_CF_KEY) {   /* it lost our bond: pair afresh next time */
        ble_store_drop_bond(&store);
        ble_store_save(&store, nv.dev);
        nv_later(2000u);
    }
    if (ble_central_fail() == BLE_CF_NEED_MITM && !(store.dev.sec & BLE_DEV_SEC_MITM) &&
        ble_store_has_last(&store) && !memcmp(store.dev.addr, c_addr, 6)) {
        ble_store_set_mitm(&store);               /* (fble_ui.c tries again after its pause: with the passkey) */
        ble_store_save(&store, nv.dev);
        nv_later(2000u);
    }
    c_was = c;
#if BLE_CEN_PAIR
    if (keys_new) {
        struct ble_keys k;
        irqs_hold(1);
        k = keys_tmp;
        keys_new = 0;
        irqs_hold(0);
        if (!ble_store_has_last(&store) || memcmp(store.dev.addr, c_addr, 6))
            ble_store_set_last(&store, c_addr, c_rand, cname, BLE_KIND_MIDI);
        if (k.has & BLE_KEYS_LTK)
            ble_store_set_bond(&store, k.ltk, k.rand, k.ediv, (k.has & BLE_KEYS_AUTH) != 0u);
        if (k.has & BLE_KEYS_ID)
            ble_store_set_id(&store, k.irk, k.id, k.id_rand);
        ble_store_save(&store, nv.dev);
        nv_later(2000u);
    }
    if (ble_central_passkey() != BLE_NO_PASSKEY && now - pk_said > 2500u) {   /* the phone's user types it */
        char b[24];
        uint32_t v = ble_central_passkey(), d = 100000u;
        char *q = put_str(b, "BLE PASSKEY ");
        for (; d; d /= 10u)
            *q++ = (char)('0' + v / d % 10u);
        *q = 0;
        pk_said = now;
        H->say(b);
        say_log(b);
    }
#endif
#endif
#if BLE_SMP_LEGACY
    if (bond_new) {                               /* a computer bonded: kept, given back after the next start */
        irqs_hold(1);
        ble_cpy(nv.bond_rand, bond_tmp, 8);
        ble_cpy(nv.bond_ediv, bond_tmp + 8, 2);
        ble_cpy(nv.bond_ltk, bond_tmp + 10, 16);
        bond_new = 0;
        irqs_hold(0);
        nv.has_bond = 1;
        nv_later(2000u);
    }
#endif
    if (!ble_midi_ready() && r_out.r != r_out.w) {   /* nobody listens: the host's MIDI out does not wait for one */
        irqs_hold(1);
        r_out.r = r_out.w;
        irqs_hold(0);
    }
    radio_apply();
}

/* ---- the start: the first time RADIO is ON (fble_poll). The trims first */
static int fble_start(void)
{
    struct ble_rf_trims use;
    uint8_t a[6];
    if (state != FBLE_OFF)
        return state == FBLE_FAILED ? -1 : 0;
    state = FBLE_STARTING;
    say_log("fble2: starting");
    trims(&use);                                  /* (never none: measured when nothing is stored) */
    nv_flush(1);                                  /* (the copy, before the radio's current is drawn) */
    if (nv.has_addr) {
        ble_cpy(a, nv.addr, 6);
    } else {                                      /* a random static address, made once and kept */
        ble_hw_rand(a, 6);
        a[5] = (uint8_t)((a[5] & 0x3Fu) | 0xC0u);
        ble_cpy(nv.addr, a, 6);
        nv.has_addr = 1;
        nv_flush(1);
    }
#if BLE_CENTRAL
    ble_store_load(&store, nv.dev);
    ble_scan_clear(&tab);
#endif
    H->quiet(1);                                  /* the start-up's current: no flash reads from the host's IRQs */
    ble_hw_wl82_attach();                         /* the two vectors, held */
    ble_init(a, 1);
#if BLE_SMP_LEGACY
    if (nv.has_bond)
        ble_host_set_key(nv.bond_rand, (uint16_t)(nv.bond_ediv[0] | nv.bond_ediv[1] << 8), nv.bond_ltk);
#endif
    ble_hw_wl82_start(&use);                      /* the radio's start-up program and the baseband */
    H->quiet(0);
    en = 1;                                       /* (RADIO is ON: fble_ui.c applies PC, MODE) */
    state = FBLE_READY;
    irqs_hold(0);
    say_log("fble2: ready");
    return 0;
}

/* ---- fble's pages (unchanged): the settings, FIRST / PICK, RECON, OUT */
#include "fble_ui.c"

static void fble_poll(int view)
{
    if (state == FBLE_OFF && set.radio)
        fble_start();
    if (state == FBLE_READY) {
        ble_midi_poll();
        ui_task(view);
    }
    nv_flush(0);
}
static void fble_stop(void)
{
    if (state == FBLE_READY)
        ble_midi_enable(0);
    ports_out = ports_live = 0;
}

static const fext_api api = {
    .abi_major = FEXT_ABI_MAJOR, .abi_minor = 1, .size = sizeof(fext_api),
    .poll = fble_poll, .stop = fble_stop,
    .midi_in = &r_in, .midi_out = &r_out, .ports_out = &ports_out, .ports_live = &ports_live,
    .pages = 2, .column = ui_column, .knob = ui_knob, .line = ui_line, .control = ui_control,
};

const fext_api *fble_init(const fext_host *h)
{
    if (!h || h->abi_major != FEXT_ABI_MAJOR || h->size < offsetof(fext_host, flash_read) || !h->log || !h->ms ||
        !h->wdt_feed || !h->irq_attach || !h->quiet || !h->nv_load || !h->nv_save || !h->reboot || !h->say)
        return 0;
    H = h;
    say_log(FEXT_HOST_HAS(h, flash_read) ? "fble2 " FBLE_VERSION ": init, host 1.1"
                                          : "fble2 " FBLE_VERSION ": init, host 1.0 (radio measured)");
    state = FBLE_OFF;
    set_load();
    nv_load();
    return &api;
}
