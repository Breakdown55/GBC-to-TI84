#include "runtime.h"

enum {
    GB_FLAG_Z = 0x80,
    GB_FLAG_N = 0x40,
    GB_FLAG_H = 0x20,
    GB_FLAG_C = 0x10,
};

volatile uint16_t gb_rom_bank;
uint16_t gb_rom_bank_mask;
uint8_t gb_mbc5;
volatile uint24_t gb_pc;
volatile uint24_t gb_hl;
volatile uint24_t gb_bc;
volatile uint16_t gb_sp;
volatile uint8_t gb_a;
volatile uint8_t gb_f;
uint8_t gb_wram[GB_WRAM_SIZE];
uint8_t gb_hram[GB_HRAM_SIZE];
uint8_t gb_lcdc;
uint8_t gb_ly;
uint8_t gb_ie;
uint8_t gb_if;

void gb_cp_a(uint8_t value)
{
    uint8_t a = gb_a;
    gb_f = GB_FLAG_N |
           (a == value ? GB_FLAG_Z : 0) |
           ((a & 0x0f) < (value & 0x0f) ? GB_FLAG_H : 0) |
           (a < value ? GB_FLAG_C : 0);
}

void gb_inc_a(void)
{
    uint8_t old_a = gb_a;
    uint8_t result = old_a + 1;
    gb_a = result;
    gb_f = (gb_f & GB_FLAG_C) |
           (result == 0 ? GB_FLAG_Z : 0) |
           ((old_a & 0x0f) == 0x0f ? GB_FLAG_H : 0);
}

void gb_dec_c(void)
{
    uint8_t old_c = (uint8_t)gb_bc;
    uint8_t result = old_c - 1;
    gb_bc = (gb_bc & 0xff00) | result;
    gb_f = (gb_f & GB_FLAG_C) | GB_FLAG_N |
           (result == 0 ? GB_FLAG_Z : 0) |
           ((old_c & 0x0f) == 0 ? GB_FLAG_H : 0);
}

uint8_t gb_read_high(uint8_t offset)
{
    switch (offset) {
    case 0x0f:
        return gb_if | 0xe0;
    case 0x40:
        return gb_lcdc;
    case 0x44: {
        uint8_t line = gb_ly;
        /* Advance on polls until the runtime has a cycle-based LCD clock. */
        if (gb_lcdc & 0x80) {
            gb_ly = line == 153 ? 0 : line + 1;
        }
        return line;
    }
    case 0xff:
        return gb_ie;
    default:
        if (offset >= 0x80) {
            return gb_hram[offset - 0x80];
        }
        gb_unmapped();
        return 0xff;
    }
}

void gb_write_high(uint8_t offset)
{
    uint8_t value = gb_a;
    switch (offset) {
    case 0x0f:
        gb_if = value & 0x1f;
        break;
    case 0x40:
        gb_lcdc = value;
        if (!(value & 0x80)) {
            gb_ly = 0;
        }
        break;
    case 0x44:
        break; /* LY is read-only. */
    case 0xff:
        gb_ie = value;
        break;
    default:
        if (offset >= 0x80) {
            gb_hram[offset - 0x80] = value;
        } else {
            gb_unmapped();
        }
    }
}

uint8_t gb_store_a(uint24_t address)
{
    if (address >= GB_WRAM_BASE && address < GB_WRAM_BASE + GB_WRAM_SIZE) {
        gb_wram[address - GB_WRAM_BASE] = gb_a;
    } else if (address >= GB_HRAM_BASE && address <= 0xffff) {
        gb_write_high((uint8_t)address);
    } else if (gb_mbc5 && address >= 0x2000 && address < 0x4000) {
        uint16_t bank = gb_rom_bank;
        if (address < 0x3000) {
            bank = (bank & 0x100) | gb_a;
        } else {
            bank = (bank & 0xff) | ((uint16_t)(gb_a & 1) << 8);
        }
        gb_rom_bank = bank & gb_rom_bank_mask;
    } else {
        return 0;
    }
    return 1;
}

uint8_t gb_store_hli(void)
{
    uint16_t address = (uint16_t)gb_hl;
    if (!gb_store_a(address)) {
        return 0;
    }
    gb_hl = (uint16_t)(address + 1);
    return 1;
}

GbNativeBlock gb_lookup(uint24_t address)
{
    if (address >= 0x8000) {
        return 0;
    }
    uint16_t bank = address < 0x4000 ? 0 : gb_rom_bank;
    for (unsigned i = 0; i < gb_target_count; ++i) {
        if (gb_targets[i].bank == bank && gb_targets[i].address == address) {
            return gb_targets[i].entry;
        }
    }
    return 0;
}

uint8_t gb_push_word(uint24_t value)
{
    uint16_t sp = gb_sp;
    if (sp < GB_WRAM_BASE + 2 || sp > GB_WRAM_BASE + GB_WRAM_SIZE) {
        return 0;
    }
    unsigned offset = sp - GB_WRAM_BASE;
    gb_wram[offset - 1] = (uint8_t)(value >> 8);
    gb_wram[offset - 2] = (uint8_t)value;
    gb_sp = sp - 2;
    return 1;
}

uint24_t gb_pop_word(void)
{
    uint16_t sp = gb_sp;
    /* The prototype maps only fixed WRAM and needs two readable bytes. */
    if (sp < GB_WRAM_BASE || sp >= GB_WRAM_BASE + GB_WRAM_SIZE - 1) {
        return 0x10000; /* Outside the guest address space. */
    }
    unsigned offset = sp - GB_WRAM_BASE;
    uint16_t value = gb_wram[offset] | ((uint16_t)gb_wram[offset + 1] << 8);
    gb_sp = sp + 2;
    return value;
}
