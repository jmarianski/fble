/* SPDX-License-Identifier: MPL-2.0
 * Copyright (C) 2026 Jacek Mariański */
/* fble2's time base for Optimist's BLE driver: TIMER4, the 24 MHz free-running counter every FM-1 firmware with the
 * fext socket keeps running (SLOOP's and Felucca's fm1_time.h; fble's busy delay reads it too). The module only reads
 * it: the host set it up and owns it. */
#pragma once
#include <stdint.h>
#define FM1_T4_CNT (*(volatile uint32_t *)0x10804u)
#define FM1_TICKS_PER_US 24u
static inline __attribute__((always_inline)) uint32_t fm1_ticks(void) { return FM1_T4_CNT; }
static inline __attribute__((always_inline)) void fm1_delay_us(uint32_t us)   /* (inline: also inside .ram_text) */
{
    uint32_t t0 = FM1_T4_CNT, n = us * FM1_TICKS_PER_US;
    while (FM1_T4_CNT - t0 < n)
        ;
}
