#include "runtime.h"
#include <stdbool.h>

enum {
    GB_FLAG_Z = 0x80,
    GB_FLAG_N = 0x40,
    GB_FLAG_H = 0x20,
    GB_FLAG_C = 0x10,
    GB_UNMAPPED_BYTE = 0x100,
};

const GbRomByte *gb_rom_data;
unsigned gb_rom_data_count;
volatile uint16_t gb_rom_bank;
uint16_t gb_rom_bank_mask;
uint8_t gb_mbc5;
uint8_t gb_ram_enabled;
uint8_t gb_cgb;
volatile uint24_t gb_pc;
volatile uint24_t gb_hl;
volatile uint24_t gb_bc;
volatile uint24_t gb_de;
volatile uint16_t gb_sp;
volatile uint8_t gb_a;
volatile uint8_t gb_f;
volatile uint8_t gb_ime;
uint8_t gb_vram[2][GB_VRAM_SIZE];
uint8_t gb_wram[GB_WRAM_SIZE];
uint8_t gb_wram_banks[7][GB_WRAM_SIZE];
uint8_t gb_hram[GB_HRAM_SIZE];
uint8_t gb_lcdc;
uint8_t gb_ly;
uint8_t gb_stat;
uint8_t gb_scx;
uint8_t gb_scy;
uint8_t gb_ie;
uint8_t gb_if;
uint8_t gb_key1;
uint8_t gb_vbk;
uint8_t gb_svbk;
uint8_t gb_joyp;
uint8_t gb_sb;
uint8_t gb_sc;
uint8_t gb_tma;
uint8_t gb_tac;
uint8_t gb_nr50;
uint8_t gb_nr51;
uint8_t gb_nr52;
uint8_t gb_lyc;
uint8_t gb_wy;
uint8_t gb_wx;
uint8_t gb_rp;

static uint8_t subtraction_flags(uint8_t a, uint8_t value)
{
    return GB_FLAG_N |
           (a == value ? GB_FLAG_Z : 0) |
           ((a & 0x0f) < (value & 0x0f) ? GB_FLAG_H : 0) |
           (a < value ? GB_FLAG_C : 0);
}

void gb_cp_a(uint8_t value)
{
    gb_f = subtraction_flags(gb_a, value);
}

void gb_sub_l(void)
{
    uint8_t a = gb_a;
    uint8_t value = (uint8_t)gb_hl;
    gb_f = subtraction_flags(a, value);
    gb_a = a - value;
}

void gb_srl_a(void)
{
    uint8_t a = gb_a;
    uint8_t result = a >> 1;
    gb_a = result;
    gb_f = (result == 0 ? GB_FLAG_Z : 0) | (a & 1 ? GB_FLAG_C : 0);
}

void gb_add_hl_de(void)
{
    uint16_t hl = (uint16_t)gb_hl;
    uint16_t de = (uint16_t)gb_de;
    uint24_t sum = (uint24_t)hl + de;
    gb_f = (gb_f & GB_FLAG_Z) |
           ((hl & 0x0fff) + (de & 0x0fff) > 0x0fff ? GB_FLAG_H : 0) |
           (sum > 0xffff ? GB_FLAG_C : 0);
    gb_hl = (uint16_t)sum;
}

static uint8_t step_byte(uint8_t value, bool decrement)
{
    uint8_t result = decrement ? value - 1 : value + 1;
    gb_f = (gb_f & GB_FLAG_C) |
           (decrement ? GB_FLAG_N : 0) |
           (result == 0 ? GB_FLAG_Z : 0) |
           ((value & 0x0f) == (decrement ? 0 : 0x0f) ? GB_FLAG_H : 0);
    return result;
}

void gb_inc_a(void)
{
    gb_a = step_byte(gb_a, false);
}

void gb_dec_b(void)
{
    uint8_t result = step_byte((uint8_t)(gb_bc >> 8), true);
    gb_bc = ((uint16_t)result << 8) | (gb_bc & 0xff);
}

void gb_inc_c(void)
{
    gb_bc = (gb_bc & 0xff00) | step_byte((uint8_t)gb_bc, false);
}

void gb_dec_c(void)
{
    gb_bc = (gb_bc & 0xff00) | step_byte((uint8_t)gb_bc, true);
}

static uint16_t read_high(uint8_t offset)
{
    switch (offset) {
    case 0x00:
        return gb_joyp | 0xcf; /* Both button groups are released. */
    case 0x01:
        return gb_sb;
    case 0x02:
        return gb_sc | (gb_cgb ? 0x7c : 0x7e);
    case 0x06:
        return gb_tma;
    case 0x07:
        return gb_tac | 0xf8;
    case 0x0f:
        return gb_if | 0xe0;
    case 0x24:
        return gb_nr50;
    case 0x25:
        return gb_nr51;
    case 0x26:
        return gb_nr52 | 0x70; /* No sound channels are running. */
    case 0x40:
        return gb_lcdc;
    case 0x41:
        return gb_stat | 0x80;
    case 0x42:
        return gb_scy;
    case 0x43:
        return gb_scx;
    case 0x44: {
        uint8_t line = gb_ly;
        /* Advance on polls until the runtime has a cycle-based LCD clock. */
        if (gb_lcdc & 0x80) {
            gb_ly = line == 153 ? 0 : line + 1;
        }
        return line;
    }
    case 0x45:
        return gb_lyc;
    case 0x4a:
        return gb_wy;
    case 0x4b:
        return gb_wx;
    case 0x4d:
        return gb_cgb ? gb_key1 | 0x7e : 0xff;
    case 0x4f:
        return gb_cgb ? gb_vbk | 0xfe : 0xff;
    case 0x56:
        return gb_cgb ? gb_rp | 0x3e : 0xff; /* No infrared input. */
    case 0x70:
        return gb_cgb ? gb_svbk | 0xf8 : 0xff;
    case 0xff:
        return gb_ie;
    default:
        if (offset >= 0x80) {
            return gb_hram[offset - 0x80];
        }
        return GB_UNMAPPED_BYTE;
    }
}

uint8_t gb_read_high(uint8_t offset)
{
    uint16_t value = read_high(offset);
    if (value == GB_UNMAPPED_BYTE) {
        gb_unmapped();
        return 0xff;
    }
    return (uint8_t)value;
}

static uint8_t write_high(uint8_t offset, uint8_t value)
{
    switch (offset) {
    case 0x00:
        gb_joyp = value & 0x30;
        break;
    case 0x01:
        gb_sb = value;
        break;
    case 0x02:
        gb_sc = value & (gb_cgb ? 0x83 : 0x81); /* No serial transfer clock. */
        break;
    case 0x06:
        gb_tma = value;
        break;
    case 0x07:
        gb_tac = value & 0x07;
        break;
    case 0x0f:
        gb_if = value & 0x1f;
        break;
    case 0x24:
        if (gb_nr52) {
            gb_nr50 = value;
        }
        break;
    case 0x25:
        if (gb_nr52) {
            gb_nr51 = value;
        }
        break;
    case 0x26:
        gb_nr52 = value & 0x80;
        if (!gb_nr52) {
            gb_nr50 = 0;
            gb_nr51 = 0;
        }
        break;
    case 0x40:
        gb_lcdc = value;
        if (!(value & 0x80)) {
            gb_ly = 0;
        }
        break;
    case 0x41:
        gb_stat = value & 0x78;
        break;
    case 0x42:
        gb_scy = value;
        break;
    case 0x43:
        gb_scx = value;
        break;
    case 0x44:
        break; /* LY is read-only. */
    case 0x45:
        gb_lyc = value;
        break;
    case 0x4a:
        gb_wy = value;
        break;
    case 0x4b:
        gb_wx = value;
        break;
    case 0x4d:
        if (gb_cgb) {
            gb_key1 = (gb_key1 & 0x80) | (value & 1);
        }
        break;
    case 0x4f:
        if (gb_cgb) {
            gb_vbk = value & 1;
        }
        break;
    case 0x56:
        if (gb_cgb) {
            gb_rp = value & 0xc1;
        }
        break;
    case 0x70:
        if (gb_cgb) {
            gb_svbk = value & 7;
        }
        break;
    case 0xff:
        gb_ie = value;
        break;
    default:
        if (offset >= 0x80) {
            gb_hram[offset - 0x80] = value;
        } else {
            return 0;
        }
    }
    return 1;
}

uint8_t gb_write_high(uint8_t offset)
{
    return write_high(offset, gb_a);
}

uint8_t gb_stop(void)
{
    if (!gb_cgb || !(gb_key1 & 1)) {
        return 0; /* Low-power STOP is outside the hardware model. */
    }
    gb_key1 = (gb_key1 ^ 0x80) & 0x80;
    return 1;
}

static uint8_t *work_or_high_ram_byte(uint24_t address)
{
    if (address >= GB_WRAM_BASE && address < GB_WRAM_BASE + 2 * GB_WRAM_SIZE) {
        unsigned offset = address - GB_WRAM_BASE;
        if (offset < GB_WRAM_SIZE) {
            return &gb_wram[offset];
        }
        unsigned bank = gb_cgb && gb_svbk ? gb_svbk : 1;
        return &gb_wram_banks[bank - 1][offset - GB_WRAM_SIZE];
    }
    if (address >= GB_HRAM_BASE && address < GB_HRAM_BASE + GB_HRAM_SIZE) {
        return &gb_hram[address - GB_HRAM_BASE];
    }
    return 0;
}

static uint8_t *ram_byte(uint24_t address)
{
    if (address >= GB_VRAM_BASE && address < GB_VRAM_BASE + GB_VRAM_SIZE) {
        return &gb_vram[gb_cgb ? gb_vbk : 0][address - GB_VRAM_BASE];
    }
    return work_or_high_ram_byte(address);
}

uint8_t gb_watch_ram(uint16_t address)
{
    uint8_t *byte = ram_byte(address);
    return byte ? *byte : 0xff;
}

static uint16_t read_rom(uint16_t address)
{
    uint16_t bank = address < 0x4000 ? 0 : gb_rom_bank;
    unsigned first = 0, last = gb_rom_data_count;
    while (first < last) {
        unsigned middle = first + (last - first) / 2;
        const GbRomByte *entry = &gb_rom_data[middle];
        if (entry->bank == bank && entry->address == address) {
            return entry->value;
        }
        if (entry->bank < bank || (entry->bank == bank && entry->address < address)) {
            first = middle + 1;
        } else {
            last = middle;
        }
    }
    return GB_UNMAPPED_BYTE;
}

static uint16_t read_memory(uint24_t address)
{
    uint8_t *byte = ram_byte(address);
    if (byte) {
        return *byte;
    }
    if (address < 0x8000) {
        return read_rom((uint16_t)address);
    }
    if (address >= 0xff00 && address <= 0xffff) {
        return read_high((uint8_t)address);
    }
    return GB_UNMAPPED_BYTE;
}

static uint8_t write_memory(uint24_t address, uint8_t value)
{
    uint8_t *byte = ram_byte(address);
    if (byte) {
        *byte = value;
    } else if (address >= 0xff00 && address <= 0xffff) {
        return write_high((uint8_t)address, value);
    } else if (gb_mbc5 && address < 0x2000) {
        gb_ram_enabled = (value & 0x0f) == 0x0a;
    } else if (gb_mbc5 && address < 0x4000) {
        uint16_t bank = gb_rom_bank;
        if (address < 0x3000) {
            bank = (bank & 0x100) | value;
        } else {
            bank = (bank & 0xff) | ((uint16_t)(value & 1) << 8);
        }
        gb_rom_bank = bank & gb_rom_bank_mask;
    } else {
        return 0;
    }
    return 1;
}

uint8_t gb_load_a(uint24_t address)
{
    uint16_t value = read_memory(address);
    if (value == GB_UNMAPPED_BYTE) {
        return 0;
    }
    gb_a = (uint8_t)value;
    return 1;
}

uint8_t gb_load_hli(void)
{
    uint16_t address = (uint16_t)gb_hl;
    if (!gb_load_a(address)) {
        return 0;
    }
    gb_hl = (uint16_t)(address + 1);
    return 1;
}

uint8_t gb_store_a(uint24_t address)
{
    return write_memory(address, gb_a);
}

uint8_t gb_store_hl(void)
{
    return gb_store_a((uint16_t)gb_hl);
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

uint8_t gb_store_hld(void)
{
    uint16_t address = (uint16_t)gb_hl;
    if (!gb_store_a(address)) {
        return 0;
    }
    gb_hl = (uint16_t)(address - 1);
    return 1;
}

uint8_t gb_set_hl_bit(uint8_t bit)
{
    uint16_t address = (uint16_t)gb_hl;
    uint16_t value = read_memory(address);
    if (value == GB_UNMAPPED_BYTE || bit >= 8) {
        return 0;
    }
    return write_memory(address, (uint8_t)(value | (1U << bit)));
}

GbNativeBlock gb_lookup(uint24_t address)
{
    if (address >= 0x8000) {
        return 0;
    }
    uint16_t bank = address < 0x4000 ? 0 : gb_rom_bank;
    unsigned first = 0, last = gb_target_count;
    while (first < last) {
        unsigned middle = first + (last - first) / 2;
        const GbTarget *target = &gb_targets[middle];
        if (target->bank == bank && target->address == address) {
            return target->entry;
        }
        if (target->bank < bank || (target->bank == bank && target->address < address)) {
            first = middle + 1;
        } else {
            last = middle;
        }
    }
    return 0;
}

uint8_t gb_push_word(uint24_t value)
{
    uint16_t sp = gb_sp;
    uint8_t *high = work_or_high_ram_byte((uint16_t)(sp - 1));
    uint8_t *low = work_or_high_ram_byte((uint16_t)(sp - 2));
    if (!low || !high) {
        return 0;
    }
    *high = (uint8_t)(value >> 8);
    *low = (uint8_t)value;
    gb_sp = sp - 2;
    return 1;
}

uint24_t gb_pop_word(void)
{
    uint16_t sp = gb_sp;
    uint8_t *low = work_or_high_ram_byte(sp);
    uint8_t *high = work_or_high_ram_byte((uint16_t)(sp + 1));
    if (!low || !high) {
        return 0x10000; /* Outside the guest address space. */
    }
    uint16_t value = *low | ((uint16_t)*high << 8);
    gb_sp = sp + 2;
    return value;
}
