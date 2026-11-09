#include "runtime.h"
#include "show_hex.h"

#include <ti/getcsc.h>

enum { RUN_HALTED = 1, RUN_FAULT, RUN_MISSING_TARGET };

extern const uint16_t rom_bank_mask;
extern const uint16_t rom_watch_address;
extern const uint8_t rom_mbc5;
extern const uint8_t rom_cgb;
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

int main(void)
{
    os_ClrHome();
    gb_rom_bank_mask = rom_bank_mask;
    gb_rom_bank = 1;
    gb_mbc5 = rom_mbc5;
    gb_a = rom_cgb ? 0x11 : 0x01;
    gb_sp = 0xfffe;
    gb_lcdc = 0x91;
    gb_pc = 0x0100;
    gb_000_0100();

    if (gb_run_status == RUN_HALTED) {
        os_PutStrFull("HALT");
    } else {
        os_PutStrFull(gb_run_status == RUN_MISSING_TARGET ? "MISSING TARGET " : "FAULT ");
        show_hex(gb_pc < 0x4000 ? 0 : gb_rom_bank, 3);
        os_PutStrFull(":");
        show_hex(gb_pc, 4);
    }
    os_NewLine();
    if (rom_watch_address) {
        uint16_t address = rom_watch_address;
        uint8_t value = address < GB_HRAM_BASE ? gb_wram[address - GB_WRAM_BASE]
                                              : gb_hram[address - GB_HRAM_BASE];
        os_PutStrFull("RAM ");
        show_hex(address, 4);
        os_PutStrFull("=");
        show_hex(value, 2);
        os_NewLine();
    }
    os_PutStrFull("Press a key");
    while (!os_GetCSC()) {
    }
    return gb_run_status == RUN_HALTED ? 0 : 1;
}
