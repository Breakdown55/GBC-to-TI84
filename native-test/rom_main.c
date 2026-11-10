#include "runtime.h"
#include "render.h"
#include "show_hex.h"
#include "timing.h"
#include "rom_storage.h"

#ifndef GB_FRAME_SKIP
#define GB_FRAME_SKIP 0
#endif

#include <ti/getcsc.h>
#if GB_LIVE
#include <keypadc.h>
#endif

enum { RUN_HALTED = 1, RUN_FAULT, RUN_MISSING_TARGET, RUN_CHECKPOINT, RUN_USER_EXIT };

extern const uint16_t rom_bank_mask;
extern const uint16_t rom_watch_address;
extern const uint8_t rom_mbc5;
extern const uint8_t rom_cgb;
extern const uint8_t rom_timing;
extern const unsigned rom_preview_frames;
extern const uint8_t rom_ram_size_code;
extern const GbRomSpan gb_rom_spans[];
extern const uint8_t gb_rom_bytes[];
extern const unsigned gb_rom_span_count;
extern void gb_000_0100(void);

volatile uint8_t gb_run_status;
static unsigned preview_frames_left;
static uint8_t preview_in_handler;

void gb_halt(void)
{
    gb_run_status = RUN_HALTED;
}

void gb_unmapped(void)
{
    gb_run_status = RUN_FAULT;
}

void gb_missing_target(void)
{
    gb_run_status = RUN_MISSING_TARGET;
}

void gb_checkpoint(void)
{
    gb_run_status = RUN_CHECKPOINT;
}

void gb_user_exit(void)
{
    gb_run_status = RUN_USER_EXIT;
}

#if GB_LIVE
static unsigned presentation_delay;

static void present_live_frame(void)
{
    if (presentation_delay) {
        --presentation_delay;
    } else {
        gb_present_frame();
        presentation_delay = GB_FRAME_SKIP;
    }
}

static void poll_buttons(void)
{
    kb_SetMode(MODE_2_SINGLE);
    unsigned spins = 65535;
    while (kb_GetMode() != MODE_0_IDLE && --spins) {
    }
    if (!spins) {
        return;
    }
    gb_host_quit = kb_IsDown(kb_KeyClear) != 0;
    if (gb_frame_count > GB_INPUT_AFTER) {
        uint8_t buttons = 0;
        buttons |= kb_IsDown(kb_KeyRight) ? 1 : 0;
        buttons |= kb_IsDown(kb_KeyLeft) ? 2 : 0;
        buttons |= kb_IsDown(kb_KeyUp) ? 4 : 0;
        buttons |= kb_IsDown(kb_KeyDown) ? 8 : 0;
        buttons |= kb_IsDown(kb_Key2nd) ? 0x10 : 0;
        buttons |= kb_IsDown(kb_KeyAlpha) ? 0x20 : 0;
        buttons |= kb_IsDown(kb_KeyDel) ? 0x40 : 0;
        buttons |= kb_IsDown(kb_KeyEnter) ? 0x80 : 0;
        gb_set_buttons(buttons);
    }
}

static void live_frame(void)
{
    poll_buttons();
    if (!gb_host_quit) {
        present_live_frame();
    }
}
#endif

uint8_t gb_preview_begin(uint24_t resume)
{
    if (preview_in_handler || !(gb_lcdc & 0x80) || !(gb_ie & 1) || !gb_ime) {
        return 0;
    }
    ++gb_frame_count;
#if GB_LIVE
    poll_buttons();
    if (gb_host_quit) {
        return 2;
    }
#endif
    if (!gb_push_word(resume)) {
        return 0;
    }
    preview_in_handler = 1;
    gb_ly = 144;
    gb_if &= (uint8_t)~1;
    gb_di();
    return 1;
}

uint8_t gb_preview_finish(void)
{
    if (!preview_in_handler) {
        return 0;
    }
    preview_in_handler = 0;
#if GB_LIVE
    present_live_frame();
#endif
    return preview_frames_left && --preview_frames_left == 0;
}

int main(void)
{
#if GB_ROM_CHUNKS
    /* Resolve archive addresses before guest state or graphics become active.
     * No variable allocation or archival occurs while these pointers are live. */
    if (!gb_archive_init()) {
        os_ClrHome();
        os_PutStrFull("Archive ROM data:");
        os_NewLine();
        os_PutStrFull(gb_archive_name);
        while (!os_GetCSC()) {}
        return 1;
    }
#endif
    static const uint24_t ram_sizes[] = {0, 0x800, 0x2000, 0x8000, 0x20000, 0x10000};
    gb_rom_bank_mask = rom_bank_mask;
    gb_rom_bank = 1;
    gb_mbc5 = rom_mbc5;
    if (gb_mbc5 && rom_ram_size_code < sizeof(ram_sizes) / sizeof(ram_sizes[0])) {
        gb_sram_size = ram_sizes[rom_ram_size_code];
    }
    gb_cgb = rom_cgb;
    gb_rom_data = gb_rom_spans;
    gb_rom_payload = gb_rom_bytes;
    gb_rom_data_count = gb_rom_span_count;
    gb_a = gb_cgb ? 0x11 : 0x01;
    gb_sp = 0xfffe;
    gb_lcdc = 0x91;
    gb_sc = gb_cgb ? 0x03 : 0;
    gb_nr50 = 0x77;
    gb_nr51 = 0xf3;
    gb_nr52 = 0x80;
    gb_pc = 0x0100;
    gb_video_init();
    preview_frames_left = rom_preview_frames;
    gb_timing_enabled = rom_timing;
    if (gb_timing_enabled) {
        gb_timing_init();
    }
#if GB_LIVE
    uint8_t keypad_config = kb_Config;
    gb_host_frame = live_frame;
#endif
    gb_000_0100();
#if GB_LIVE
    gb_host_frame = 0;
    kb_Config = keypad_config;
    gb_end_frame();
    if (gb_run_status == RUN_USER_EXIT) {
        os_ClrHome();
        return 0;
    }
#endif
    if (gb_timing_enabled && !gb_timing_flush()) {
        gb_unmapped();
    }

    /* TI-OS display calls can reuse scratch RAM backing the guest state. */
    uint8_t status = gb_run_status;
#if GB_SHOW_FRAME
    if (status == RUN_HALTED || status == RUN_CHECKPOINT) {
        gb_present_frame();
        while (!os_GetCSC()) {
        }
        gb_end_frame();
        os_ClrHome();
        return 0;
    }
#endif
    uint16_t pc = gb_pc;
    uint16_t bank = pc >= 0x4000 && pc < 0x8000 ? gb_rom_bank : 0;
    uint16_t address = rom_watch_address;
    uint8_t value = address ? gb_watch_ram(address) : 0;

    os_ClrHome();
    if (status == RUN_HALTED) {
        os_PutStrFull("HALT");
    } else {
        if (status == RUN_CHECKPOINT) {
            os_PutStrFull("CHECKPOINT ");
        } else {
            os_PutStrFull(status == RUN_MISSING_TARGET ? "MISSING TARGET " : "FAULT ");
        }
        show_hex(bank, 3);
        os_PutStrFull(":");
        show_hex(pc, 4);
    }
    os_NewLine();
    if (address) {
        os_PutStrFull("RAM ");
        show_hex(address, 4);
        os_PutStrFull("=");
        show_hex(value, 2);
        os_NewLine();
    }
    os_PutStrFull("Press a key");
    while (!os_GetCSC()) {
    }
    return status == RUN_HALTED || status == RUN_CHECKPOINT ? 0 : 1;
}
