/* SPDX-License-Identifier: MPL-2.0
 * Copyright (C) 2026 Jacek Mariański */
/* fble, an fext module (include/fext.h): the image and its three RAM windows. Linked at made-up bases (FLASH
 * 0x10000000, CODE 0x20000000, DMA 0x30000000, RAM 0x40000000: tools/fext_reloc.py BASES) and moved to a host's at
 * install; build_module.py also links it at SLOOP's (the check that the move makes the same image). Each window's
 * part starts at its origin.
 *   FLASH  the header, code, constants, then the load images of .ram_text and .data
 *   CODE   .ram_text: what runs while the flash is busy (interrupts, delays)
 *   DMA    .data, .bss (the BT heap: the radio's DMA must reach it)
 *   RAM    .pool (the BT tasks' stacks) */
MEMORY
{
    FLASH (rx)  : ORIGIN = 0x10000000, LENGTH = 0x40000
    CODE  (rwx) : ORIGIN = 0x20000000, LENGTH = 0x10000
    DMA   (rw)  : ORIGIN = 0x30000000, LENGTH = 0x10000
    RAM   (rw)  : ORIGIN = 0x40000000, LENGTH = 0x10000
}
SECTIONS
{
    .seg0 : { KEEP(*(.fext_header))
              *(.text*) *(.rodata*)
              *(.hci_controller_code .ble_ll_code .ble_rf_code .bt_rf_code .hci_interface_code)
              *(.bt_stack_code .ble_sm_code .ble_att_code .ble_db_code .ble_gatt_code .ble_hci_code
                .classic_lmp_code .link_bulk_code .lmp_irq_code .classic_rf_code
                .vendor_manager_code .device_manager_code)
              *(.*_const)
              . = ALIGN(4); } > FLASH
    .ram_text : { _rt_start = .; KEEP(*(.ram_text*)) *(.volatile_ram_code) *(*.rf.text.cache.L2.rf_trim)
                  . = ALIGN(4); _rt_end = .; } > CODE AT > FLASH
    _rt_load = LOADADDR(.ram_text);
    .data : { . = ALIGN(4); _data_start = .; *(.data*) *(.sdata*) *(.*_data) . = ALIGN(4); _data_end = .; } > DMA AT > FLASH
    _data_load = LOADADDR(.data);
    _fext_end = LOADADDR(.data) + SIZEOF(.data);
    .bss (NOLOAD) : { _bss_start = .; *(.bss*) *(.sbss*) *(COMMON) *(.*_bss) *(.bd_base) *(.noinit*)
                      . = ALIGN(4); _bss_end = .; } > DMA
    .pool (NOLOAD) : { _pool_start = .; *(.pool*) . = ALIGN(4); _pool_end = .; } > RAM
    /DISCARD/ : { *(.comment) *(.note*) }
}
