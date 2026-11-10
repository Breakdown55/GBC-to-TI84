#include "runtime.h"
#include "rom_storage.h"
#include "render.h"
#include "timing.h"
#include "vram_dma.h"
#include <stdbool.h>
#include <stddef.h>
#include <string.h>

enum {
    GB_FLAG_Z = 0x80,
    GB_FLAG_N = 0x40,
    GB_FLAG_H = 0x20,
    GB_FLAG_C = 0x10,
    SRAM_PAGE_SIZE = 64,
    SRAM_PAGE_LIMIT = 96,
    SRAM_PAGE_COUNT = 0x20000 / SRAM_PAGE_SIZE,
};

typedef struct {
    uint16_t number;
    uint8_t matches;
    uint8_t bytes[SRAM_PAGE_SIZE];
} SramPage;

const GbRomSpan *gb_rom_data;
const uint8_t *gb_rom_payload;
unsigned gb_rom_data_count;
volatile uint16_t gb_rom_bank;
uint16_t gb_rom_bank_mask;
uint8_t gb_mbc5;
uint8_t gb_ram_enabled;
uint24_t gb_sram_size;
static uint8_t sram_bank;
static uint8_t sram_page_count;
static uint8_t sram_fill[SRAM_PAGE_COUNT]; /* Encoded so zero-initialized pages read as erased. */
static SramPage sram_pages[SRAM_PAGE_LIMIT];
uint8_t gb_cgb;
volatile uint24_t gb_pc;
volatile uint24_t gb_hl;
volatile uint24_t gb_bc;
volatile uint24_t gb_de;
volatile uint24_t gb_sp;
volatile uint8_t gb_a;
volatile uint8_t gb_f;
volatile uint8_t gb_ime;
uint8_t gb_vram[2][GB_VRAM_SIZE];
uint8_t *gb_vram_page = gb_vram[0];
uint8_t gb_wram[GB_WRAM_SIZE];
uint8_t gb_wram_banks[7][GB_WRAM_SIZE];
/* The two 4 KiB WRAM pages are also visible through the echo window. */
uint8_t *gb_wram_pages[2] = {gb_wram, gb_wram_banks[0]};
/* Native reads can reuse the most recent contiguous ROM span. */
struct {
    uint24_t start, length, bias;
} gb_rom_read_cache;
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
static uint8_t gb_buttons;
uint8_t gb_sb;
uint8_t gb_sc;
uint8_t gb_tma;
uint8_t gb_tac;
uint8_t gb_nr50;
uint8_t gb_nr51;
uint8_t gb_nr52;
static uint8_t sound_channels[0x14];
static uint8_t wave_ram[0x10];
uint8_t gb_lyc;
uint8_t gb_wy;
uint8_t gb_wx;
uint8_t gb_rp;
static uint8_t gb_dma;

static uint16_t read_memory(uint24_t address);

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

uint8_t gb_step_byte(uint8_t value, uint8_t decrement)
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
    gb_a = gb_step_byte(gb_a, 0);
}

void gb_dec_b(void)
{
    uint8_t result = gb_step_byte((uint8_t)(gb_bc >> 8), 1);
    gb_bc = ((uint16_t)result << 8) | (gb_bc & 0xff);
}

void gb_inc_c(void)
{
    gb_bc = (gb_bc & 0xff00) | gb_step_byte((uint8_t)gb_bc, 0);
}

void gb_dec_c(void)
{
    gb_bc = (gb_bc & 0xff00) | gb_step_byte((uint8_t)gb_bc, 1);
}

static uint16_t add_sp_offset(uint8_t offset)
{
    uint16_t sp = gb_sp;
    gb_f = ((sp & 0x0f) + (offset & 0x0f) > 0x0f ? GB_FLAG_H : 0) |
           ((sp & 0xff) + offset > 0xff ? GB_FLAG_C : 0);
    return (uint16_t)(sp + (int8_t)offset);
}

void gb_ld_hl_sp(uint8_t offset)
{
    gb_hl = add_sp_offset(offset);
}

void gb_add_sp(uint8_t offset)
{
    gb_sp = add_sp_offset(offset);
}

void gb_alu(uint8_t operation, uint8_t operand)
{
    uint8_t a = gb_a;
    uint8_t carry = (operation == 1 || operation == 3) && (gb_f & GB_FLAG_C);
    uint8_t result, flags = 0;
    switch (operation) {
    case 0:
    case 1: {
        uint16_t sum = a + operand + carry;
        result = (uint8_t)sum;
        flags = (((a & 15) + (operand & 15) + carry > 15) ? GB_FLAG_H : 0) |
                (sum > 255 ? GB_FLAG_C : 0);
        break;
    }
    case 2:
    case 3:
    case 7:
        result = a - operand - carry;
        flags = GB_FLAG_N |
                ((a & 15) < (operand & 15) + carry ? GB_FLAG_H : 0) |
                (a < operand + carry ? GB_FLAG_C : 0);
        break;
    case 4:
        result = a & operand;
        flags = GB_FLAG_H;
        break;
    case 5:
        result = a ^ operand;
        break;
    default:
        result = a | operand;
        break;
    }
    if (operation != 7) {
        gb_a = result;
    }
    gb_f = flags | (result == 0 ? GB_FLAG_Z : 0);
}

static bool timed_register(uint8_t offset)
{
    return (offset >= 0x04 && offset <= 0x07) || offset == 0x40 ||
           offset == 0x41 || offset == 0x44 || offset == 0x45;
}

static uint8_t joypad_lines(void)
{
    uint8_t pressed = 0;
    if (!(gb_joyp & 0x10)) {
        pressed |= gb_buttons & 0x0f;
    }
    if (!(gb_joyp & 0x20)) {
        pressed |= gb_buttons >> 4;
    }
    return pressed ^ 0x0f;
}

static void joypad_edge(uint8_t previous)
{
    if (previous & (uint8_t)~joypad_lines()) {
        gb_if |= 0x10;
    }
}

void gb_set_buttons(uint8_t buttons)
{
    uint8_t previous = joypad_lines();
    gb_buttons = buttons;
    joypad_edge(previous);
}

static uint16_t read_high(uint8_t offset)
{
    static const uint8_t sound_read_mask[] = {
        0x80, 0x3f, 0x00, 0xff, 0xbf,
        0xff, 0x3f, 0x00, 0xff, 0xbf,
        0x7f, 0xff, 0x9f, 0xff, 0xbf,
        0xff, 0xff, 0x00, 0x00, 0xbf,
    };
    if (gb_timing_enabled && timed_register(offset)) {
        return gb_timing_read(offset);
    }
    if (offset >= 0x10 && offset < 0x24) {
        return sound_channels[offset - 0x10] | sound_read_mask[offset - 0x10];
    }
    if (offset >= 0x30 && offset < 0x40) {
        return wave_ram[offset - 0x30];
    }
    switch (offset) {
    case 0x00:
        return gb_joyp | 0xc0 | joypad_lines();
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
    case 0x46:
        return gb_dma;
    case 0x47:
    case 0x48:
    case 0x49:
    case 0x68:
    case 0x69:
    case 0x6a:
    case 0x6b:
    case 0x6c:
        return gb_video_read(offset);
    case 0x4a:
        return gb_wy;
    case 0x4b:
        return gb_wx;
    case 0x4d:
        return gb_cgb ? gb_key1 | 0x7e : 0xff;
    case 0x4f:
        return gb_cgb ? gb_vbk | 0xfe : 0xff;
    case 0x51:
    case 0x52:
    case 0x53:
    case 0x54:
    case 0x55:
        return gb_vram_dma_read(offset);
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

static uint8_t transfer_oam(uint8_t page)
{
    if (page >= 0xe0) {
        return 0;
    }
    uint16_t source = (uint16_t)page << 8;
    uint8_t bytes[GB_OAM_SIZE];
    const uint8_t *data = gb_read_contiguous(source, GB_OAM_SIZE);
    if (!data) {
        for (unsigned i = 0; i < GB_OAM_SIZE; ++i) {
            uint16_t value = read_memory(source + i);
            if (value == GB_UNMAPPED_BYTE) {
                return 0;
            }
            bytes[i] = (uint8_t)value;
        }
        data = bytes;
    }
    if (memcmp(gb_oam, data, GB_OAM_SIZE)) {
        memcpy(gb_oam, data, GB_OAM_SIZE);
        gb_oam_dirty = 1;
    }
    gb_dma = page;
    return 1;
}

static uint8_t write_high(uint8_t offset, uint8_t value)
{
    if (gb_timing_enabled && timed_register(offset)) {
        gb_timing_write(offset, value);
        return 1;
    }
    if (offset >= 0x10 && offset < 0x24) {
        if (gb_nr52) {
            sound_channels[offset - 0x10] = value;
        }
        return 1;
    }
    if (offset >= 0x30 && offset < 0x40) {
        wave_ram[offset - 0x30] = value;
        return 1;
    }
    switch (offset) {
    case 0x00: {
        uint8_t previous = joypad_lines();
        gb_joyp = value & 0x30;
        joypad_edge(previous);
        break;
    }
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
            for (unsigned i = 0; i < sizeof(sound_channels); ++i) {
                sound_channels[i] = 0;
            }
        }
        break;
    case 0x40:
        gb_video_store(&gb_lcdc, value);
        if (!(value & 0x80)) {
            gb_ly = 0;
        }
        break;
    case 0x41:
        gb_stat = value & 0x78;
        break;
    case 0x42:
        gb_video_store(&gb_scy, value);
        break;
    case 0x43:
        gb_video_store(&gb_scx, value);
        break;
    case 0x44:
        break; /* LY is read-only. */
    case 0x45:
        gb_lyc = value;
        break;
    case 0x46:
        return transfer_oam(value);
    case 0x47:
    case 0x48:
    case 0x49:
    case 0x68:
    case 0x69:
    case 0x6a:
    case 0x6b:
    case 0x6c:
        gb_video_write(offset, value);
        break;
    case 0x4a:
        gb_video_store(&gb_wy, value);
        break;
    case 0x4b:
        gb_video_store(&gb_wx, value);
        break;
    case 0x4d:
        if (gb_cgb) {
            gb_key1 = (gb_key1 & 0x80) | (value & 1);
        }
        break;
    case 0x4f:
        if (gb_cgb) {
            gb_vbk = value & 1;
            gb_vram_page = gb_vram[gb_vbk];
        }
        break;
    case 0x51:
    case 0x52:
    case 0x53:
    case 0x54:
    case 0x55:
        return gb_vram_dma_write(offset, value);
    case 0x56:
        if (gb_cgb) {
            gb_rp = value & 0xc1;
        }
        break;
    case 0x70:
        if (gb_cgb) {
            gb_svbk = value & 7;
            gb_wram_pages[1] = gb_wram_banks[gb_svbk ? gb_svbk - 1 : 0];
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
    if (gb_timing_enabled) {
        gb_timing_write(0x04, 0);
    }
    gb_key1 = (gb_key1 ^ 0x80) & 0x80;
    return 1;
}

static uint8_t *work_or_high_ram_byte(uint24_t address)
{
    if (address >= 0xe000 && address < 0xfe00) {
        address -= 0x2000;
    }
    if (address >= GB_WRAM_BASE && address < GB_WRAM_BASE + 2 * GB_WRAM_SIZE) {
        unsigned offset = address - GB_WRAM_BASE;
        if (offset < GB_WRAM_SIZE) return gb_wram + offset;
        return gb_wram_pages[1] + (offset - GB_WRAM_SIZE);
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
    if (address >= 0xfe00 && address < 0xfe00 + GB_OAM_SIZE) {
        return &gb_oam[address - 0xfe00];
    }
    return work_or_high_ram_byte(address);
}

uint8_t gb_watch_ram(uint16_t address)
{
    uint8_t *byte = ram_byte(address);
    return byte ? *byte : 0xff;
}

static void cache_rom_read(uint16_t start, uint16_t length, const uint8_t *bytes)
{
    gb_rom_read_cache.start = start;
    gb_rom_read_cache.bias = (uintptr_t)bytes - start;
    gb_rom_read_cache.length = length;
}

static const GbRomSpan *find_rom_span(uint16_t address)
{
    static const GbRomSpan *last_span;
    uint16_t bank = address < 0x4000 ? 0 : gb_rom_bank;
    if (last_span && last_span->bank == bank &&
        (uint16_t)(address - last_span->address) < last_span->length) {
        if (!gb_rom_read_cache.length || gb_rom_read_cache.start != last_span->address) {
            cache_rom_read(last_span->address, last_span->length, gb_rom_payload + last_span->offset);
        }
        return last_span;
    }
    unsigned first = 0, last = gb_rom_data_count;
    while (first < last) {
        unsigned middle = first + (last - first) / 2;
        const GbRomSpan *span = &gb_rom_data[middle];
        if (span->bank < bank || (span->bank == bank && address >= span->address + span->length)) {
            first = middle + 1;
        } else if (span->bank > bank || address < span->address) {
            last = middle;
        } else {
            cache_rom_read(span->address, span->length, gb_rom_payload + span->offset);
            return last_span = span;
        }
    }
#if GB_ROM_CHUNKS
    /* Cache the archive gap without replacing faster embedded bytes. */
    uint16_t start = address & 0x4000, end = start + 0x4000;
    if (first && gb_rom_data[first - 1].bank == bank) {
        const GbRomSpan *previous = &gb_rom_data[first - 1];
        start = previous->address + previous->length;
    }
    if (first < gb_rom_data_count && gb_rom_data[first].bank == bank) {
        end = gb_rom_data[first].address;
    }
    const uint8_t *bytes = gb_archive_pointer(start, end - start);
    if (bytes) cache_rom_read(start, end - start, bytes);
#endif
    return 0;
}

const uint8_t *gb_read_contiguous(uint16_t address, unsigned length)
{
    if (address < 0x8000 && length <= 0x4000 - (address & 0x3fff)) {
        const GbRomSpan *span = find_rom_span(address);
        if (span && length <= span->length - (address - span->address)) {
            return gb_rom_payload + span->offset + (address - span->address);
        }
#if GB_ROM_CHUNKS
        return gb_archive_pointer(address, length);
#endif
    } else if (address >= GB_WRAM_BASE && address < 0xe000 &&
               length <= GB_WRAM_SIZE - (address & (GB_WRAM_SIZE - 1))) {
        return work_or_high_ram_byte(address);
    }
    return 0;
}

static uint24_t sram_offset(uint24_t address)
{
    return (((uint24_t)sram_bank << 13) | (address & 0x1fff)) & (gb_sram_size - 1);
}

static uint16_t last_sram_page = UINT16_MAX;
static SramPage *last_sram_slot;

static SramPage *find_sram_page(uint16_t number)
{
    if (last_sram_page == number) {
        return last_sram_slot;
    }
    last_sram_page = number;
    for (unsigned i = 0; i < sram_page_count; ++i) {
        if (sram_pages[i].number == number) {
            return last_sram_slot = &sram_pages[i];
        }
    }
    return last_sram_slot = 0;
}

static uint8_t read_sram(uint24_t address)
{
    if (!gb_ram_enabled || !gb_sram_size) {
        return 0xff;
    }
    uint24_t offset = sram_offset(address);
    uint16_t number = offset / SRAM_PAGE_SIZE;
    SramPage *page = find_sram_page(number);
    return page ? page->bytes[offset % SRAM_PAGE_SIZE] : sram_fill[number] ^ 0xff;
}

static uint8_t write_sram(uint24_t address, uint8_t value)
{
    if (!gb_ram_enabled || !gb_sram_size) {
        return 1;
    }
    uint24_t offset = sram_offset(address);
    uint16_t number = offset / SRAM_PAGE_SIZE;
    SramPage *page = find_sram_page(number);
    if (!page) {
        uint8_t fill = sram_fill[number] ^ 0xff;
        if (value == fill) {
            return 1;
        }
        if (sram_page_count == SRAM_PAGE_LIMIT) {
            return 0;
        }
        page = &sram_pages[sram_page_count++];
        last_sram_slot = page;
        page->number = number;
        page->matches = SRAM_PAGE_SIZE;
        for (unsigned i = 0; i < SRAM_PAGE_SIZE; ++i) {
            page->bytes[i] = fill;
        }
    }
    unsigned index = offset % SRAM_PAGE_SIZE;
    uint8_t previous = page->bytes[index];
    if (previous == value) {
        return 1;
    }
    page->bytes[index] = value;
    if (index == 0) {
        page->matches = 1;
        for (unsigned i = 1; i < SRAM_PAGE_SIZE; ++i) {
            page->matches += page->bytes[i] == value;
        }
    } else {
        uint8_t first = page->bytes[0];
        page->matches += (value == first) - (previous == first);
    }
    if (page->matches == SRAM_PAGE_SIZE) {
        sram_fill[number] = value ^ 0xff;
        *page = sram_pages[--sram_page_count];
        last_sram_slot = 0;
    }
    return 1;
}

static uint16_t read_memory(uint24_t address)
{
    if (address < 0x8000) {
        const GbRomSpan *span = find_rom_span((uint16_t)address);
#if GB_ROM_CHUNKS
        if (!span) return gb_archive_read((uint16_t)address);
#endif
        return span ? gb_rom_payload[span->offset + address - span->address] : GB_UNMAPPED_BYTE;
    }
    uint8_t *byte = ram_byte(address);
    if (byte) {
        return *byte;
    }
    if (address >= 0xa000 && address < 0xc000) {
        return read_sram(address);
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
        if (address < 0xa000) {
            if (*byte != value) {
                *byte = value;
                gb_video_invalidate(gb_cgb ? gb_vbk : 0, address - GB_VRAM_BASE, 1);
            }
        } else if (address >= 0xfe00 && address < 0xfe00 + GB_OAM_SIZE) {
            gb_oam_store(address - 0xfe00, value);
        } else {
            *byte = value;
        }
    } else if (address >= 0xa000 && address < 0xc000) {
        return write_sram(address, value);
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
        bank &= gb_rom_bank_mask;
        if (bank != gb_rom_bank) {
            gb_rom_bank = bank;
            gb_rom_read_cache.length = 0;
        }
    } else if (gb_mbc5 && address < 0x6000) {
        sram_bank = value & 0x0f;
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

uint24_t gb_read_byte(uint24_t address)
{
    return read_memory(address);
}

uint8_t gb_write_byte(uint24_t address, uint8_t value)
{
    return write_memory(address, value);
}

uint8_t gb_step_hl(uint8_t decrement)
{
    uint16_t address = gb_hl;
    uint16_t value = read_memory(address);
    if (value == GB_UNMAPPED_BYTE) {
        return 0;
    }
    uint8_t previous_flags = gb_f;
    uint8_t result = gb_step_byte((uint8_t)value, decrement);
    if (!write_memory(address, result)) {
        gb_f = previous_flags;
        return 0;
    }
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

uint8_t gb_load_hld(void)
{
    uint16_t address = (uint16_t)gb_hl;
    if (!gb_load_a(address)) {
        return 0;
    }
    gb_hl = (uint16_t)(address - 1);
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

/* Fuse counted LD [HL+],A runs for untimed translation only.
 * Reject MMIO, cartridge RAM, and crossings before touching guest state. */
uint8_t gb_fill_hl(uint8_t layout)
{
    if (gb_timing_enabled) return 0;
    bool use_b = layout & 1;
    unsigned width = (layout >> 1) + 1;
    uint16_t original = (uint16_t)gb_hl;
    unsigned address = original;
    unsigned count = use_b ? (gb_bc >> 8) & 255 : gb_bc & 255;
    count = (count ? count : 256) * width;
    if (address >= 0xe000 && address < 0xfe00) {
        if (count > 0xfe00 - address) return 0;
        address -= 0x2000;
    }
    uint8_t *bytes;
    uint8_t video = address >= 0x8000 && address < 0xa000;
    if (video && count <= 0xa000 - address) {
        bytes = &gb_vram[gb_cgb ? gb_vbk : 0][address - 0x8000];
    } else if (address >= 0xc000 && address < 0xe000 && count <= 0x1000 - (address & 0xfff)) {
        bytes = work_or_high_ram_byte(address);
    } else if (address >= 0xff80 && address < 0xffff && count <= 0xffff - address) {
        bytes = &gb_hram[address - 0xff80];
    } else {
        return 0;
    }
    uint8_t value = gb_a;
    unsigned changed = 0;
    if (video) {
        while (changed < count && bytes[changed] == value) ++changed;
    }
    if (changed < count) {
        memset(bytes, value, count);
        if (video) gb_video_invalidate(gb_cgb ? gb_vbk : 0, address - 0x8000, count);
    }
    gb_hl = (uint16_t)(original + count);
    gb_bc &= use_b ? 0xff : 0xff00;
    gb_f = (gb_f & GB_FLAG_C) | GB_FLAG_Z | GB_FLAG_N;
    return 1;
}

static uint8_t *copy_ram_range(unsigned address, unsigned length)
{
    if (address >= 0x8000 && address < 0xa000 && length <= 0xa000 - address) {
        return &gb_vram[gb_cgb ? gb_vbk : 0][address - 0x8000];
    }
    if (address >= 0xe000 && address < 0xfe00) {
        if (length > 0xfe00 - address) return 0;
        address -= 0x2000;
    }
    if (address >= 0xc000 && address < 0xe000 && length <= 0x1000 - (address & 0xfff)) {
        return work_or_high_ram_byte(address);
    }
    if (address >= 0xff80 && address < 0xffff && length <= 0xffff - address) {
        return &gb_hram[address - 0xff80];
    }
    return 0;
}

/* Fuse a counted sequence of LD A,[HL+] / LD [DE],A / INC DE (or INC E).
 * Side effects, unsafe overlap, or an unretained source require the original loop. */
uint8_t gb_copy_hl_de(uint8_t layout)
{
    unsigned width = layout & 0x3f;
    if (gb_timing_enabled || !width) return 0;
    bool use_b = layout & 0x80;
    unsigned count = use_b ? (gb_bc >> 8) & 255 : gb_bc & 255;
    unsigned length = (count ? count : 256) * width;
    unsigned source = (uint16_t)gb_hl, destination = (uint16_t)gb_de;
    if ((layout & 0x40) && length > 256 - (destination & 255) &&
        (256 % width || (destination & 255) % width)) return 0;

    uint8_t *output = copy_ram_range(destination, length);
    if (!output) return 0;
    const uint8_t *input = source < 0x8000 ? gb_read_contiguous(source, length) : copy_ram_range(source, length);
    if (!input) return 0;
    if (input != output) {
        uintptr_t from = (uintptr_t)input, to = (uintptr_t)output;
        if (from < to + length && to < from + length) return 0;
        bool video = destination < 0xa000;
        if (!video || memcmp(output, input, length)) {
            memcpy(output, input, length);
            if (video) gb_video_invalidate(gb_cgb ? gb_vbk : 0, destination - 0x8000, length);
        }
    }
    gb_a = input[length - 1];
    gb_hl = (uint16_t)(source + length);
    gb_de = (uint16_t)(destination + length);
    gb_bc &= use_b ? 0xff : 0xff00;
    gb_f = (gb_f & GB_FLAG_C) | GB_FLAG_Z | GB_FLAG_N;
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

static GbNativeBlock lookup_address(const GbTargetBank *bank, uint16_t address)
{
    unsigned first = bank->first, last = first + bank->count;
    while (first < last) {
        unsigned middle = first + (last - first) / 2;
        const GbTarget *target = &gb_targets[middle];
        if (target->address == address) {
            return target->entry;
        }
        if (target->address < address) {
            first = middle + 1;
        } else {
            last = middle;
        }
    }
    return 0;
}

enum { LOOKUP_CACHE_SIZE = 32 };
typedef struct {
    uint16_t address, bank;
    GbNativeBlock entry;
    uint8_t padding;
} LookupCacheEntry;

/* dispatch.S probes these records directly before taking the C lookup path. */
#ifdef __TICE__
_Static_assert(sizeof(LookupCacheEntry) == 8 && offsetof(LookupCacheEntry, entry) == 4,
               "dispatch cache layout must match the assembly");
#endif
LookupCacheEntry gb_lookup_cache[LOOKUP_CACHE_SIZE] __attribute__((aligned(256)));

GbNativeBlock gb_lookup(uint24_t address)
{
    if (address >= 0x8000 && (address < GB_HRAM_BASE || address >= 0xffff)) {
        return 0;
    }
    uint16_t bank = address >= 0x4000 && address < 0x8000 ? gb_rom_bank : 0;
    unsigned slot = (address ^ (address >> 8) ^ bank) & (LOOKUP_CACHE_SIZE - 1);
    if (!gb_target_bank_count) return 0;
    static const GbTargetBank *last_switch_group;
    const GbTargetBank *group = gb_target_banks;
    if (group->bank != bank) {
        group = last_switch_group;
        if (!group || group->bank != bank) {
            unsigned first = 1, last = gb_target_bank_count;
            while (first < last) {
                unsigned middle = first + (last - first) / 2;
                if (gb_target_banks[middle].bank < bank) first = middle + 1;
                else last = middle;
            }
            if (first == gb_target_bank_count || gb_target_banks[first].bank != bank) return 0;
            last_switch_group = group = &gb_target_banks[first];
        }
    }
    GbNativeBlock entry = lookup_address(group, (uint16_t)address);
    gb_lookup_cache[slot] = (LookupCacheEntry){(uint16_t)address, bank, entry, 0};
    return entry;
}

uint8_t gb_push_word(uint24_t value)
{
    uint16_t sp = gb_sp;
    uint16_t offset = sp - 2 - GB_WRAM_BASE;
    if (offset < GB_WRAM_SIZE - 1) {
        gb_wram[offset] = (uint8_t)value;
        gb_wram[offset + 1] = (uint8_t)(value >> 8);
    } else {
        uint8_t *high = work_or_high_ram_byte((uint16_t)(sp - 1));
        uint8_t *low = work_or_high_ram_byte((uint16_t)(sp - 2));
        if (!low || !high) {
            return 0;
        }
        *high = (uint8_t)(value >> 8);
        *low = (uint8_t)value;
    }
    gb_sp = (uint16_t)(sp - 2);
    return 1;
}

uint24_t gb_pop_word(void)
{
    uint16_t sp = gb_sp;
    uint16_t offset = sp - GB_WRAM_BASE;
    uint16_t value;
    if (offset < GB_WRAM_SIZE - 1) {
        value = gb_wram[offset] | ((uint16_t)gb_wram[offset + 1] << 8);
    } else {
        uint8_t *low = work_or_high_ram_byte(sp);
        uint8_t *high = work_or_high_ram_byte((uint16_t)(sp + 1));
        if (!low || !high) {
            return 0x10000; /* Outside the guest address space. */
        }
        value = *low | ((uint16_t)*high << 8);
    }
    gb_sp = (uint16_t)(sp + 2);
    return value;
}
