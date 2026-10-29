/* SPDX-License-Identifier: MPL-2.0
 * Copyright (C) 2026 Jacek Mariański */
/* The module as a host's loader sees it (include/fext_load.h, the same check SLOOP's fext_host.c runs), on this
 * computer: a 1 MiB flash with the module moved to SLOOP's slot (USR4, 0xE7000), read through SLOOP's XIP mapping.
 *
 *   build/test/test_load IMAGE [FRAMES]
 * IMAGE: build/check/fble-sloop.bin (tools/build_module.py: the image moved by fext_reloc.place). It is accepted, its
 * load steps are listed; a flipped byte anywhere is refused (sampled: the header's fields and the image). FRAMES:
 * build/check/fble-sloop.frames (fext_push.py's write frames): every frame decodes into the slot and they give the
 * same flash, the header's sector written last. Exit 0: all as expected. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "fext.h"
#include "fext_load.h"

static uint8_t flash[0x100000], save[0x100000];
static const fext_slot SLOT = {0xE7000u, 0x14000u, 0x020E3000u,
                               {{0x01C06F00u, 0x01C07F00u}, {0x01C08000u, 0x01C0E000u}, {0x01C1E000u, 0x01C20000u}}};
static int fails;

static const uint8_t *fl(uint32_t off, uint32_t n)
{
    return off < sizeof flash && n <= sizeof flash - off ? flash + off : 0;
}

static int frames(const char *path, uint32_t size)
{
    static uint8_t buf[1u << 20];
    uint8_t raw[256];
    size_t n, i = 0;
    uint32_t count = 0, off, k, hdr_at = 0;
    FILE *f = fopen(path, "rb");
    if (!f)
        return perror(path), 1;
    n = fread(buf, 1, sizeof buf, f);
    fclose(f);
    memset(flash, 0xFF, sizeof flash);
    while (i < n) {
        size_t e = i;
        int got;
        while (e < n && buf[e] != 0xF7)
            e++;
        if (e >= n || buf[i] != 0xF0 || buf[i + 1] != 0x7D || buf[i + 2] != 0x46 || buf[i + 3] != 0x42 || buf[i + 4] != 2)
            return printf("FAIL  frame %u: not a write frame\n", count), 1;
        got = fext_unpack(buf + i + 5, (uint32_t)(e - i - 5), &off, raw, sizeof raw);
        if (got <= 0 || !fext_in(off, (uint32_t)got, SLOT.off, SLOT.off + SLOT.max))
            return printf("FAIL  frame %u: does not decode into the slot\n", count), 1;
        if (!(off & 0xFFFu))
            memset(flash + off, 0xFF, 0x1000);
        for (k = 0; k < (uint32_t)got; k++)
            flash[off + k] &= raw[k];
        if (off == SLOT.off)
            hdr_at = count;
        count++;
        i = e + 1;
    }
    if (memcmp(flash, save, sizeof flash))
        return printf("FAIL  the frames' flash differs\n"), 1;
    if (hdr_at + (size > 0x1000u ? 16u : (size + 255u) / 256u) != count)
        return printf("FAIL  the header's sector is not written last (its frame %u of %u)\n", hdr_at, count), 1;
    printf("ok    %u write frames (%zu B), all in the slot, the header's sector last: the same flash\n", count, n);
    return 0;
}

int main(int argc, char **argv)
{
    fext_header h;
    const char *why;
    uint32_t n, i, bad = 0, tried = 0;
    FILE *f;
    if (argc < 2)
        return fprintf(stderr, "test_load IMAGE [FRAMES]\n"), 2;
    if (!(f = fopen(argv[1], "rb")))
        return perror(argv[1]), 2;
    memset(flash, 0xFF, sizeof flash);
    n = (uint32_t)fread(flash + SLOT.off, 1, SLOT.max + 1u, f);
    fclose(f);
    if (n > SLOT.max)
        return printf("FAIL  %s: longer than the slot\n", argv[1]), 1;
    memcpy(save, flash, sizeof flash);
    if ((why = fext_check(fl, &SLOT, &h)))
        return printf("FAIL  %s refused: %s\n", argv[1], why), 1;
    printf("ok    %s: %u B, %.8s %.16s, abi %u.%u, caps %X, init %08X\n", argv[1], n, h.name, h.version, h.abi_major,
           h.abi_minor, h.caps, h.init);
    for (i = 0; i < FEXT_NW; i++)
        printf("      window %u: %08X..%08X (%u B)\n", i, h.win[i].lo, h.win[i].hi, h.win[i].hi - h.win[i].lo);
    for (i = 0; i < FEXT_NCOPY; i++)
        printf("      load %u: %08X <- %08X, %u B\n", i, h.copy[i].dst, h.copy[i].src, h.copy[i].n);
    for (i = 0; i < n; i += i < sizeof h ? 1u : 997u) {   /* every header byte, then a sample of the image */
        flash[SLOT.off + i] ^= 0x10u;
        bad += !fext_check(fl, &SLOT, &h);
        tried++;
        flash[SLOT.off + i] ^= 0x10u;
    }
    printf("%s  a flipped byte refused: %u of %u\n", bad ? "FAIL" : "ok  ", tried - bad, tried);
    fails += bad != 0;
    if (argc > 2)
        fails += frames(argv[2], n);
    printf(fails ? "test_load: %d FAILED\n" : "test_load: all as expected\n", fails);
    return fails != 0;
}
