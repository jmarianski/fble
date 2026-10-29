/* SPDX-License-Identifier: MPL-2.0
 * Copyright (C) 2026 Jacek Mariański */
/* fble2's interrupt calls for Optimist's BLE driver. The vector table and the priorities are the host's: attaching
 * goes through fext_host (fble2_irq_attach, fble2_mod.c). What the driver does on its own two lines (IRQ 45, 29):
 * hold them at the controller and let them go (fm1_ble_irqs_hold), with the controller's enable bit only, the
 * priority the host gave kept. cli / sti are the CPU's. */
#pragma once
#include <stdint.h>
#define FM1_ICFG(n) (*(volatile uint32_t *)(0x1EEF100u + 4u * ((n) >> 3)))
static inline uint32_t fm1_icfg(void)
{
    uint32_t v;
    __asm__ volatile("%0 = icfg" : "=r"(v));
    return v;
}
static inline void fm1_irq_off(void) { __asm__ volatile("cli" ::: "memory"); }
static inline void fm1_irq_on(void) { __asm__ volatile("csync\n\tsti" ::: "memory"); }
void fble2_irq_attach(uint32_t n, void (*h)(void), uint32_t prio);
#define fm1_irq_attach(n, h, prio) fble2_irq_attach((n), (h), (prio))
