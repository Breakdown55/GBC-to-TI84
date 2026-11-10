#include "runtime.h"
#include "render.h"
#include "show_hex.h"
#include "timing.h"

#include <ti/getcsc.h>

enum { RUN_HALTED = 1, RUN_FAULT, RUN_MISSING_TARGET, RUN_CHECKPOINT };

extern const uint16_t rom_bank_mask;
extern const uint16_t rom_watch_address;
extern const uint8_t rom_mbc5;
extern const uint8_t rom_cgb;
extern const uint8_t rom_timing;
extern const uint8_t rom_ram_size_code;
extern const GbRomSpan gb_rom_spans[];
extern const uint8_t gb_rom_bytes[];
extern const unsigned gb_rom_span_count;
extern void gb_000_0100(void);

volatile uint8_t gb_run_status;

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

int main(void)
{
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
    gb_timing_enabled = rom_timing;
    if (gb_timing_enabled) {
        gb_timing_init();
    }
    gb_000_0100();
    if (gb_timing_enabled) {
        gb_timing_flush();
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
    uint16_t bank = pc < 0x4000 ? 0 : gb_rom_bank;
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
