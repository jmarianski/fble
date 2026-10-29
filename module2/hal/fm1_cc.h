/* SPDX-License-Identifier: MPL-2.0
 * Copyright (C) 2026 Jacek Mariański */
/* fble2's stand-in for a firmware's fm1_cc.h: what the radio headers (bsp-radio/fm1_ble.h, fm1_ble_rf.h, from felucca-bsp)
 * take from it. Written for the module (the firmwares' own fm1_cc.h is GPL-3.0-only in Optimist): a module has no
 * firmware headers, only these few lines. */
#pragma once
#define FM1_INLINE static inline __attribute__((always_inline))
#define FM1_CSYNC() __asm__ volatile("csync" ::: "memory")
#define FM1_SSYNC() __asm__ volatile("ssync" ::: "memory")
/* the stack's rings: the slot is written before the index that publishes it (a compiler barrier; one core) */
#define RING_PUBLISH() __asm__ volatile("" ::: "memory")
