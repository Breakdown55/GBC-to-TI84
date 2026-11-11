#include "runtime.h"
#include "timing.h"
#include "render.h"
#include <string.h>

uint8_t test_rom[0x10000];
volatile uint8_t gb_run_status;
const unsigned rom_preview_frames = 0;
const uint8_t rom_preview_mode = 1;
static void native_marker(void) {}
const GbTarget gb_targets[] = {{0x3f, native_marker}};
const GbTargetPage gb_target_pages[] = {{0, 0, 0}};
const unsigned gb_target_page_count = 1;

const uint8_t *gb_archive_pointer(uint16_t address, unsigned length)
{
    unsigned bank = address < 0x4000 ? 0 : gb_rom_bank;
    if (address >= 0x8000 || bank >= 4 || length > 0x4000u - (address & 0x3fff)) return 0;
    return test_rom + bank * 0x4000 + (address & 0x3fff);
}

uint16_t gb_archive_read(uint16_t address)
{
    const uint8_t *byte = gb_archive_pointer(address, 1);
    return byte ? *byte : GB_UNMAPPED_BYTE;
}

const uint8_t *gb_archive_window(uint16_t address, unsigned *length)
{
    unsigned available = 0x4000u - (address & 0x3fff);
    if (*length > available) *length = available;
    return gb_archive_pointer(address, *length);
}

void gb_unmapped(void) { gb_run_status = 2; }
void gb_missing_target(void) { gb_run_status = 3; }
void gb_checkpoint(void) { gb_run_status = 4; }
void gb_halt(void) { gb_run_status = 1; }
void gb_user_exit(void) { gb_run_status = 5; }
uint8_t gb_preview_finish(void) { return 0; }
uint8_t gb_preview_begin(uint24_t resume)
{
    if (!gb_push_word(resume)) return 0;
    ++gb_frame_count;
    gb_di();
    return 1;
}

void reset_fixture(void)
{
    memset(test_rom, 0, sizeof test_rom);
    memset(gb_wram, 0, sizeof gb_wram);
    memset(gb_wram_banks, 0, sizeof gb_wram_banks);
    memset(gb_hram, 0, sizeof gb_hram);
    gb_a = gb_f = gb_run_status = 0;
    gb_pc = 0x150;
    gb_sp = 0xcff0;
    gb_hl = 0xc100;
    gb_bc = 0x1234;
    gb_de = 0xc200;
    gb_rom_bank = 1;
    gb_rom_bank_mask = 3;
    gb_mbc5 = 1;
    gb_cgb = 1;
    gb_rom_data_count = 0;
    gb_timing_enabled = 0;
    gb_video_init();
    gb_timing_init();
}
