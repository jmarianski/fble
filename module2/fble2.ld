/* SPDX-License-Identifier: MPL-2.0
 * Copyright (C) 2026 Jacek Mariański */
/* fble2, an fext module (include/fext.h): the image and its RAM windows, as module/fble.ld, without JieLi's sections.
 * Linked at made-up bases (tools/fext_reloc.py BASES) and moved to a host's at install; tools/build_fble2.py also
 * links it at the proposed SLOOP layout (the check that the move makes the same image).
 *   FLASH  the header, code, constants (the radio's start-up tables among them), then the load images of
 *          .ram_text and .data
 *   CODE   .ram_text: the radio start-up's group-5 burst (fm1_ble_rf.h fm1_rf_burst_run), run while the flash
 *          must not be read
 *   DMA    .data, .bss: the baseband's RAM block (the engine reaches it by DMA) and the stack's state
 *   RAM    .pool: nothing (no tasks, no stacks of its own) */
MEMORY
{
    FLASH (rx)  : ORIGIN = 0x10000000, LENGTH = 0x40000
    CODE  (rwx) : ORIGIN = 0x20000000, LENGTH = 0x10000
    DMA   (rw)  : ORIGIN = 0x30000000, LENGTH = 0x10000
    RAM   (rw)  : ORIGIN = 0x40000000, LENGTH = 0x10000
}
SECTIONS
{
    .seg0 : { KEEP(*(.fext_header)) *(.text*) *(.rodata*) . = ALIGN(4); } > FLASH
    .ram_text : { _rt_start = .; KEEP(*(.ram_text*)) . = ALIGN(4); _rt_end = .; } > CODE AT > FLASH
    _rt_load = LOADADDR(.ram_text);
    .data : { . = ALIGN(4); _data_start = .; *(.data*) *(.sdata*) . = ALIGN(4); _data_end = .; } > DMA AT > FLASH
    _data_load = LOADADDR(.data);
    _fext_end = LOADADDR(.data) + SIZEOF(.data);
    .bss (NOLOAD) : { _bss_start = .; *(.bss*) *(.sbss*) *(COMMON) *(.noinit*) . = ALIGN(4); _bss_end = .; } > DMA
    .pool (NOLOAD) : { _pool_start = .; *(.pool*) . = ALIGN(4); _pool_end = .; } > RAM
    /DISCARD/ : { *(.comment) *(.note*) }
}
