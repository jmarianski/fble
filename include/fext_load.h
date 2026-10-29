/* SPDX-License-Identifier: MPL-2.0
 * Copyright (C) 2026 Jacek Mariański */
/* fext's loader, the part with no hardware in it (fext.h): is there a module in the slot, and may it run? Shared
 * by the host (its fext_host.c) and the tests on the computer, so the check that runs on the FM-1 is the one
 * tested.
 *
 *   fext_check(fl, &slot, &h)   0, or why not (a short text). fl(off, n): n bytes of the flash at offset off, 0
 *                               when not readable; slot: where the host keeps the module (its flash offset, size,
 *                               the CPU address it runs at, its RAM windows). On success h holds the header
 * Checked: the magic, the ABI's major, the header's size and CRC, the image's size and CRC, that it was moved to
 * this slot (xip), each RAM window inside the host's of its kind and on a 256-byte boundary, each copy step's
 * source inside the image and destination inside one of the module's windows, init inside the code.
 *   fext_unpack(p, n, &off, raw, max)   a write frame's payload (SysEx "FB" op 2): the flash offset (three 7-bit
 *                               bytes, low first), then groups of a byte of high bits and up to 7 bytes; the bytes
 *                               into raw, their number returned, -1 when malformed or longer than max
 *   fext_pack(raw, n, out)      the other way (the host's info, op 10), without the offset: the 7-bit bytes'
 *                               number */
#pragma once
#include <stdint.h>
#include "fext.h"

typedef const uint8_t *(*fext_flash_fn)(uint32_t off, uint32_t n);
typedef struct {
    uint32_t off, max;                            /* the slot in the flash */
    uint32_t xip;                                 /* the CPU address of its first byte */
    fext_win win[FEXT_NW];                        /* the RAM the host keeps free, each kind's */
} fext_slot;

static uint32_t fext_crc32(uint32_t crc, const uint8_t *b, uint32_t n)   /* zlib's CRC-32, 4 bits a step */
{
    static const uint32_t T[16] = {
        0x00000000u, 0x1DB71064u, 0x3B6E20C8u, 0x26D930ACu, 0x76DC4190u, 0x6B6B51F4u, 0x4DB26158u, 0x5005713Cu,
        0xEDB88320u, 0xF00F9344u, 0xD6D6A3E8u, 0xCB61B38Cu, 0x9B64C2B0u, 0x86D3D2D4u, 0xA00AE278u, 0xBDBDF21Cu};
    crc = ~crc;
    while (n--) {
        crc ^= *b++;
        crc = (crc >> 4) ^ T[crc & 15u];
        crc = (crc >> 4) ^ T[crc & 15u];
    }
    return ~crc;
}

/* [lo, lo + n) inside [wlo, whi), without wrapping */
static int fext_in(uint32_t lo, uint32_t n, uint32_t wlo, uint32_t whi)
{
    return lo >= wlo && lo <= whi && n <= whi - lo;
}

static const char *fext_check(fext_flash_fn fl, const fext_slot *s, fext_header *h)
{
    const uint8_t *p = fl(s->off, sizeof *h);
    uint32_t crc, i, k, n;
    uint8_t *d = (uint8_t *)h;
    if (!p)
        return "no flash";
    for (i = 0; i < sizeof *h; i++)               /* (a copy: the header's CRC is taken with its field 0) */
        d[i] = p[i];
    if (h->magic != FEXT_MAGIC)
        return "no module";
    if (h->abi_major != FEXT_ABI_MAJOR)
        return "abi";
    if (h->hdr_size != sizeof *h)
        return "header size";
    crc = h->hdr_crc;
    h->hdr_crc = 0;
    if (fext_crc32(0, d, sizeof *h) != crc)
        return "header crc";
    h->hdr_crc = crc;
    n = h->size;
    if (n < sizeof *h || n > s->max)
        return "size";
    if (!(p = fl(s->off + sizeof *h, n - (uint32_t)sizeof *h)) || fext_crc32(0, p, n - (uint32_t)sizeof *h) != h->crc)
        return "crc";
    if (h->xip != s->xip)
        return "not moved here";                  /* (the installer moves it: fext_push.py) */
    if (!fext_in(h->init, 2, s->xip + (uint32_t)sizeof *h, s->xip + n))
        return "init";
    for (k = 0; k < FEXT_NW; k++) {
        const fext_win *w = &h->win[k];
        if (w->lo == w->hi)
            continue;
        if (w->lo > w->hi || (w->lo & (FEXT_W_ALIGN - 1u)) || !fext_in(w->lo, w->hi - w->lo, s->win[k].lo, s->win[k].hi))
            return "RAM windows";
    }
    for (i = 0; i < FEXT_NCOPY; i++) {
        const fext_copy *c = &h->copy[i];
        if (!c->n)
            continue;
        if ((c->dst | c->n | c->src) & 3u)
            return "load: alignment";
        for (k = 0; k < FEXT_NW && !fext_in(c->dst, c->n, h->win[k].lo, h->win[k].hi); k++)
            ;
        if (k == FEXT_NW)
            return "load: destination";
        if (c->src && !fext_in(c->src, c->n, s->xip + (uint32_t)sizeof *h, s->xip + n))
            return "load: source";
    }
    return 0;
}

static int fext_unpack(const uint8_t *p, uint32_t n, uint32_t *off, uint8_t *raw, uint32_t max)
{
    uint32_t len = 0, i;
    if (n < 3u || ((p[0] | p[1] | p[2]) & 0x80u))
        return -1;
    *off = (uint32_t)p[0] | (uint32_t)p[1] << 7 | (uint32_t)p[2] << 14;
    p += 3;
    n -= 3;
    while (n) {
        uint8_t hi = *p++;
        n--;
        if (hi & 0x80u)
            return -1;
        for (i = 0; i < 7u && n; i++, n--) {
            if (len >= max || (*p & 0x80u))
                return -1;
            raw[len++] = (uint8_t)(*p++ | ((hi >> i) & 1u) << 7);
        }
    }
    return (int)len;
}

static uint32_t fext_pack(const uint8_t *raw, uint32_t n, uint8_t *out)
{
    uint32_t o = 0, i, k;
    for (i = 0; i < n; i += 7u) {
        uint8_t *hi = &out[o++];
        *hi = 0;
        for (k = 0; k < 7u && i + k < n; k++) {
            *hi |= (uint8_t)((raw[i + k] >> 7) << k);
            out[o++] = raw[i + k] & 0x7Fu;
        }
    }
    return o;
}
