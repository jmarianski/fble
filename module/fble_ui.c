/* SPDX-License-Identifier: MPL-2.0
 * Copyright (C) 2026 Jacek Mariański
 * (the central's FIRST / CHOOSE / RECONNECT logic follows MIDIMON's app/ble.c, Copyright (C) 2026 ignis32,
 *  MPL-2.0) */
/* fble's own part of the host's menu (fext.h: columns, lines, knobs), its settings and the central's connecting.
 * Part of fble_mod.c (included after the stack's calls). Two pages, as SLOOP shows them under GLO:
 *   BLE     RADIO (on / off: a switch that matters arms first, a second detent within 1.5 s switches)
 *           MODE (OFF, FIRST: the first device the scan sees, PICK: the one chosen), LIST (the scan's devices),
 *           USE (the one under the cursor: chosen, connected; arms first too)
 *   BLE 2   PC (a computer may connect: the module advertises), RECON (the chosen one again when it goes and at
 *           power-on), OUT (the host's MIDI out to Bluetooth too)
 * The middle of both: the state, the module, the scan's devices (the cursor's bright, the chosen one starred) and
 * their signal. The settings are the host's to keep (FEXT_NV_SET); their layout is SLOOP's fble socket's of 0.3,
 * so a SLOOP that had the socket keeps them. */

enum { C_OFF, C_FIRST, C_CHOOSE };
typedef struct {
    uint8_t radio, central, pc, recon, out, have, rsv[2];
    uint8_t addr[6];                              /* CHOOSE: the device chosen, by its address */
    char name[22];
} fble_set;
static fble_set set;
static struct {
    uint8_t on;                                   /* the stack told: enabled (radio ON) */
    uint8_t ready_seen, try_, cursor, dev, pc, arm, view;
    uint32_t wait, scan_t, arm_t;
    char cname[22];                               /* the device connecting / connected */
} fb;

static void set_load(void)
{
    memset(&set, 0, sizeof set);
    if (H->nv_load(FEXT_NV_SET, &set, sizeof set) != (int)sizeof set) {
        memset(&set, 0, sizeof set);
        set.central = C_FIRST;
        set.recon = 1;
    }
    set.name[sizeof set.name - 1u] = 0;
    if (set.central > C_CHOOSE)
        set.central = C_FIRST;
}
static void set_changed(void) { H->nv_save(FEXT_NV_SET, &set, sizeof set); }   /* (the host writes it when quiet) */

static void str_cpy(char *d, const char *s, uint32_t max)
{
    uint32_t i;
    for (i = 0; i + 1u < max && s[i]; i++)
        d[i] = s[i];
    d[i] = 0;
}
static void say2(const char *a, const char *b)
{
    char t[48];
    str_cpy(t, a, sizeof t);
    put_str(t + strlen(t), b);
    H->say(t);
}

static int ready(void) { return state == FBLE_READY; }
static int found(int i, char *name, int *rssi, uint8_t *addr)
{
    char n[21];
    int r = 0;
    if (!ready() || !ble_midi_found(i, n, &r) || (addr && !ble_midi_found_addr(i, addr)))
        return 0;
    if (name)
        str_cpy(name, n, 21);
    if (rssi)
        *rssi = r;
    return 1;
}
static int count(void)
{
    int n = 0;
    while (n < 8 && found(n, 0, 0, 0))
        n++;
    return n;
}
static int peer(void)                             /* the computer's link: 0 none, 1 linked, 2 listening */
{
    if (!ready())
        return 0;
    return ble_midi_peripheral_connected() ? 2 : ble_midi_peripheral_linked() ? 1 : 0;
}
static void connect_to(int i)
{
    if (!found(i, fb.cname, 0, 0))
        return;
    ble_midi_connect(i);
    say2("BLE CONNECTING ", fb.cname);
}
static void apply(void)                           /* the settings into the stack (when READY) */
{
    ble_midi_advertise(set.pc, 1);
    if (set.central == C_OFF) {
        ble_midi_scan(0);
        if (ble_midi_central_state() != FBLE_DEV_OFF)
            ble_midi_disconnect();
        fb.try_ = 0;
    }
}

/* the central (MIDIMON app/ble.c's ble_task, ignis32): FIRST connects to the first device the scan sees, CHOOSE to
 * the one chosen (by its address), with RECONNECT again when it goes and at power-on. Every poll once READY */
static void ui_task(int view)
{
    uint32_t now = shim_ms();
    int dev, pc, i;
    fb.view = (uint8_t)(view >= 0);
    if (fb.arm && now - fb.arm_t > 1500u)
        fb.arm = 0;
    if (!fb.ready_seen) {                         /* the stack is up: the settings in */
        fb.ready_seen = fb.on = 1;
        fb.try_ = set.central == C_CHOOSE && set.recon && set.have;
        apply();
        H->say("BLE: READY");
    }
    if (fb.on != set.radio) {                     /* RADIO turned while the stack is up: links down / up */
        fb.on = set.radio;
        if (!fb.on) {
            ble_midi_scan(0);
            ble_midi_disconnect();
            ble_midi_advertise(0, 1);
        }
        ble_midi_enable(fb.on);
        if (fb.on)
            apply();
    }
    dev = ble_midi_central_state();
    pc = peer() == 2;
    if (pc != fb.pc) {
        fb.pc = (uint8_t)pc;
        say2("BLE: ", pc ? "COMPUTER CONNECTED" : "COMPUTER GONE");
    }
    if (dev != fb.dev) {
        if (dev == FBLE_DEV_READY) {
            say2("BLE CONNECTED ", fb.cname);
            fb.try_ = 0;
        } else if (fb.dev == FBLE_DEV_READY) {    /* it went: FIRST looks again; CHOOSE does with RECONNECT on */
            say2("BLE: ", "LOST");
            fb.try_ = fb.try_ || (set.central == C_CHOOSE && set.recon && set.have);
            fb.wait = now + 1000u;
        } else if ((fb.dev == FBLE_DEV_CONNECTING || fb.dev == FBLE_DEV_SEARCHING) && dev == FBLE_DEV_OFF) {
            say2("BLE: ", "NO ANSWER");
            fb.wait = now + 3000u;
        }
        fb.dev = (uint8_t)dev;
    }
    ports_live = (dev == FBLE_DEV_READY ? 2u : 0u) | (pc ? 1u : 0u);   /* (a bit gone: the host ends its notes) */
    ports_out = set.out && fb.on ? ports_live : 0u;
    if (!fb.on || set.central == C_OFF)
        return;
    if (dev == FBLE_DEV_CONNECTING || dev == FBLE_DEV_SEARCHING || dev == FBLE_DEV_READY)
        return;
    if (!(fb.view && set.central == C_CHOOSE) && set.central != C_FIRST && !fb.try_) {
        if (dev == FBLE_DEV_SCANNING && now - fb.scan_t > 300u) {   /* nothing to look for */
            fb.scan_t = now;
            ble_midi_scan(0);
        }
        return;
    }
    if (dev == FBLE_DEV_OFF) {
        if (now - fb.scan_t > 300u) {
            fb.scan_t = now;
            ble_midi_scan(1);
        }
        return;
    }
    if ((int32_t)(now - fb.wait) < 0)
        return;                                   /* (after a failure, a pause) */
    if (set.central == C_FIRST) {
        if (found(0, 0, 0, 0))
            connect_to(0);
        return;
    }
    for (i = 0; fb.try_ && i < 8; i++) {          /* CHOOSE: the chosen one, by its address */
        uint8_t a[6];
        if (!found(i, 0, 0, a))
            break;
        if (!memcmp(a, set.addr, 6)) {
            connect_to(i);
            return;
        }
    }
}

/* ---- the pages */
static const char *const ONOFF[2] = {"OFF", "ON"};
static const char *const MODE[3] = {"OFF", "FIRST", "PICK"};   /* (PICK: CHOOSE, five letters at most) */
static void status_text(char *b)                  /* one line: the page's top, the host's status */
{
    if (state == FBLE_READY) {
        int d = ble_midi_central_state();
        if (!set.radio)
            put_str(b, "OFF");
        else if (d == FBLE_DEV_READY)
            put_str(put_str(b, "CONNECTED "), fb.cname);
        else if (d == FBLE_DEV_CONNECTING || d == FBLE_DEV_SEARCHING)
            put_str(b, "CONNECTING");
        else if (d == FBLE_DEV_SCANNING)
            put_str(b, "SCANNING");
        else
            put_str(b, peer() == 2 ? "READY, COMPUTER CONNECTED" : "READY");
        return;
    }
    put_str(b, state == FBLE_STARTING || (set.radio && state == FBLE_OFF) ? "STARTING"
               : state == FBLE_FAILED ? "FAILED" : "OFF");
}
/* a switch that matters: the first detent arms (and says so), a second one the same way within 1.5 s does it */
static int armed(uint8_t id, const char *what)
{
    if (fb.arm == id) {
        fb.arm = 0;
        return 1;
    }
    fb.arm = id;
    fb.arm_t = shim_ms();
    say2("AGAIN: ", what);
    return 0;
}
static void ui_knob(uint32_t page, uint32_t col, int32_t steps)
{
    int on = steps > 0;
    if (!page && col == 0u) {                     /* RADIO */
        if (on == set.radio || !armed(0xD0u, on ? "BLE RADIO ON" : "BLE RADIO OFF"))
            return;
        set.radio = (uint8_t)on;
        set_changed();
        say2("BLE RADIO ", ONOFF[on]);
        return;
    }
    if (page || col != 3u)
        fb.arm = 0;                               /* (USE's own arm kept: its second detent confirms) */
    if (!page && col == 1u) {                     /* MODE: OFF, FIRST, CHOOSE */
        int32_t v = (int32_t)set.central + (on ? 1 : -1);
        if (v < C_OFF || v > C_CHOOSE)
            return;
        set.central = (uint8_t)v;
        fb.try_ = set.central == C_CHOOSE && set.recon && set.have;
        set_changed();
        if (ready() && fb.on)
            apply();
        say2("BLE MODE ", v == C_CHOOSE ? "CHOOSE (PICK)" : MODE[v]);
    } else if (!page && col == 2u) {              /* the list's cursor */
        int n = count();
        int32_t c = (int32_t)fb.cursor + steps;
        fb.cursor = (uint8_t)(c < 0 ? 0 : c > n - 1 ? (n ? n - 1 : 0) : c);
    } else if (!page && col == 3u) {              /* USE: the device under the cursor, kept, connected (CHOOSE) */
        uint8_t a[6];
        char nm[21], t[32];
        if (!on)
            return;
        if (!found(fb.cursor, nm, 0, a)) {
            H->say(ready() ? "BLE: NO DEVICE THERE" : "BLE: RADIO NOT READY");
            return;
        }
        put_str(put_str(t, "USE "), nm);
        if (!armed(0xD3u, t))
            return;
        memcpy(set.addr, a, 6);
        str_cpy(set.name, nm, sizeof set.name);
        set.have = 1;
        set.central = C_CHOOSE;
        set_changed();
        fb.try_ = 1;
        fb.wait = 0;
        if (ble_midi_central_state() != FBLE_DEV_OFF && ble_midi_central_state() != FBLE_DEV_SCANNING)
            ble_midi_disconnect();                /* (another one connected: let it go; the new one by its address) */
        say2("BLE CHOSEN ", nm);
    } else if (page && col == 0u) {               /* PC: a computer may connect (the module advertises) */
        if (on == set.pc)
            return;
        set.pc = (uint8_t)on;
        set_changed();
        if (ready() && fb.on)
            ble_midi_advertise(set.pc, 1);
        say2("BLE PC ", ONOFF[on]);
    } else if (page && col == 1u) {               /* RECONNECT the chosen one */
        if (on == set.recon)
            return;
        set.recon = (uint8_t)on;
        set_changed();
        say2("BLE RECONNECT ", ONOFF[on]);
    } else if (page && col == 2u) {               /* OUT: the host's MIDI out to Bluetooth too */
        if (on == set.out)
            return;
        set.out = (uint8_t)on;
        set_changed();
        say2("BLE MIDI OUT ", ONOFF[on]);
    }
}
static void col_set(fext_col *c, const char *label, const char *value, uint8_t style)
{
    str_cpy(c->label, label, sizeof c->label);
    str_cpy(c->value, value, sizeof c->value);
    c->unit[0] = 0;
    c->style = style;
}
static void ui_column(uint32_t page, uint32_t col, fext_col *c)
{
    int n;
    if (page) {
        static const char *const L[3] = {"PC", "RECON", "OUT"};
        if (col < 3u)
            col_set(c, L[col], ONOFF[col == 0u ? set.pc : col == 1u ? set.recon : set.out], FEXT_S_NORMAL);
        else
            col_set(c, "", "", FEXT_S_NORMAL);
        return;
    }
    switch (col) {
    case 0: col_set(c, "RADIO", ONOFF[set.radio], FEXT_S_NORMAL); break;
    case 1: col_set(c, "MODE", MODE[set.central % 3u], FEXT_S_NORMAL); break;
    case 2:
        n = count();
        if (n) {
            char v[4];
            put_dec(v, (uint32_t)(fb.cursor < n ? fb.cursor : n - 1) + 1u);
            col_set(c, "LIST", v, FEXT_S_NORMAL);
            c->unit[0] = '/';
            c->unit[1] = (char)('0' + n);
            c->unit[2] = 0;
        } else {
            col_set(c, "LIST", "--", FEXT_S_DIM);
        }
        break;
    default: col_set(c, "USE", "--", count() ? FEXT_S_BRIGHT : FEXT_S_DIM); break;
    }
}
/* the middle (both pages): the state (bright when connected), the module, then the scan's devices (five at most,
 * around the cursor) or why there are none */
static int ui_line(uint32_t page, uint32_t i, fext_line *l)
{
    int n = count(), top;
    (void)page;
    memset(l, 0, sizeof *l);
    if (i == 0u) {
        status_text(l->text);
        l->style = ready() && ble_midi_central_state() == FBLE_DEV_READY ? FEXT_S_BRIGHT : FEXT_S_NORMAL;
        return 1;
    }
    if (i == 1u) {
        put_str(put_str(put_str(l->text, "MODULE "), FBLE_VERSION), state == FBLE_OFF ? "  IDLE" : "  ON");
        l->style = FEXT_S_DIM;
        return 1;
    }
    l->gap = i == 2u;
    if (!n) {
        if (i != 2u)
            return 0;
        put_str(l->text, !set.radio ? "" : set.central == C_OFF ? "MODE OFF: NO DEVICE"
                         : ready() ? "NO DEVICE FOUND YET" : "");
        l->style = FEXT_S_DIM;
        l->mark = 0;
        return l->text[0] != 0;
    }
    if (fb.cursor >= n)
        fb.cursor = (uint8_t)(n - 1);
    top = fb.cursor > 4 ? fb.cursor - 4 : 0;
    i = i - 2u + (uint32_t)top;
    if (i >= (uint32_t)n || i >= (uint32_t)top + 5u) {
        return 0;
    } else {
        int rssi = 0;
        uint8_t a[6];
        if (!found((int)i, l->text, &rssi, a))
            return 0;
        if (rssi < 0) {
            l->right[0] = '-';
            put_dec(l->right + 1, (uint32_t)-rssi);
        } else {
            put_dec(l->right, (uint32_t)rssi);
        }
        l->style = i == fb.cursor ? FEXT_S_BRIGHT : FEXT_S_NORMAL;
        l->mark = (uint8_t)(FEXT_L_ITEM | (i == fb.cursor ? FEXT_L_CURSOR : 0u) |
                            (set.have && !memcmp(a, set.addr, 6) ? FEXT_L_STAR : 0u));
    }
    return 1;
}

/* ---- the module's own commands (SysEx "FB" op 7: the host passes them on), each a letter, then its bytes:
 *   S        the state: "st <state> dev <central> pc <computer> rx <packets in> found <n> [<first name>]"
 *   R 0|1    RADIO off / on (no arming: a test's, or an installer's) */
static int ui_control(const uint8_t *in, uint32_t n, char *out, uint32_t max)
{
    char b[64], *q = b;
    (void)max;
    if (n >= 1u && in[0] == 'S') {
        char name[21];
        int k = count();
        q = put_str(q, "st ");
        *q++ = (char)('0' + state);
        q = put_str(q, " dev ");
        *q++ = (char)('0' + (ready() ? ble_midi_central_state() & 7 : 0));
        q = put_str(q, " pc ");
        *q++ = (char)('0' + peer());
        q = put_str(put_dec(put_str(q, " rx "), rx_n), " found ");
        *q++ = (char)('0' + k);
        *q = 0;
        if (k && found(0, name, 0, 0)) {
            name[12] = 0;
            put_str(put_str(q, " "), name);
        }
    } else if (n >= 2u && in[0] == 'R') {
        set.radio = in[1] != 0;
        set_changed();
        put_str(b, set.radio ? "radio on" : "radio off");
    } else {
        put_str(b, "?");
    }
    str_cpy(out, b, max);
    return (int)strlen(out);
}
