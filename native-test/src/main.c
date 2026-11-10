#include "runtime.h"
#include "show_hex.h"

#include <ti/getcsc.h>
#include <ti/screen.h>

enum {
    TEST_SP = 0xc100, FIXTURE_FLAG = 0xffb0 - GB_HRAM_BASE,
    CLEAR_START = 0xc210 - GB_WRAM_BASE, CLEAR_LENGTH = 4,
};

volatile uint8_t jump_marker;
volatile uint8_t return_marker;
volatile uint8_t return_enabled;
volatile uint8_t dispatch_error;
volatile uint8_t bank_results[5];
volatile uint16_t return_sp;
volatile uint16_t call_sp;
volatile uint8_t branch_results[2];
volatile uint8_t branch_flags[2];
volatile uint16_t fixture_pc;
volatile uint8_t test_passed;

extern void jump_from_gb_hl(void);
extern void jump_to_gb_4800(void);
extern void jump_to_gb_0321(void);
extern void gb_000_0800(void);
extern void gb_000_0900(void);
extern void gb_000_1000(void);
extern void gb_000_1100(void);
extern void gb_000_1108(void);
extern void gb_000_110e(void);
extern void gb_000_1120(void);
extern void gb_000_1123(void);
extern void gb_000_1140(void);
extern void gb_000_1146(void);
extern void gb_000_114a(void);
extern void bank_3_4800(void);
extern void bank_4_4800(void);
extern void bank_0_0321(void);
extern void bank_0_0803(void);

static void branch_fallthrough(void)
{
    jump_marker = 0x03;
}

static void branch_taken(void)
{
    jump_marker = 0x10;
}

static void stop_after_fixture_call(void)
{
    fixture_pc = 0x100a;
}

const GbTarget gb_targets[] = {
    {0x0321, bank_0_0321},
    {0x0803, bank_0_0803},
    {0x0903, branch_fallthrough},
    {0x0910, branch_taken},
    {0x100a, stop_after_fixture_call},
    {0x1100, gb_000_1100},
    {0x1108, gb_000_1108},
    {0x110e, gb_000_110e},
    {0x1120, gb_000_1120},
    {0x1123, gb_000_1123},
    {0x1140, gb_000_1140},
    {0x1146, gb_000_1146},
    {0x114a, gb_000_114a},
    {0x4800, bank_3_4800},
    {0x4800, bank_4_4800},
};
const GbTargetBank gb_target_banks[] = {
    {0, 0, 13},
    {3, 13, 1},
    {4, 14, 1},
};
const unsigned gb_target_bank_count = sizeof(gb_target_banks) / sizeof(gb_target_banks[0]);

void gb_unmapped(void)
{
    dispatch_error = 1;
}

void gb_missing_target(void)
{
    gb_unmapped();
}

static uint8_t run_jump(uint16_t bank, GbNativeBlock jump)
{
    gb_rom_bank = bank; /* Effect of the translated write to $2000. */
    jump_marker = 0;
    jump();
    return jump_marker;
}

static void show_bytes(const char *label, const volatile uint8_t *values, unsigned count)
{
    os_PutStrFull(label);
    for (unsigned i = 0; i < count; ++i) {
        os_PutStrFull(" ");
        show_hex(values[i], 2);
    }
    os_NewLine();
}

int main(void)
{
    os_ClrHome();
    gb_hl = 0x4800;
    bank_results[0] = run_jump(3, jump_from_gb_hl);
    bank_results[1] = run_jump(4, jump_from_gb_hl);
    bank_results[2] = run_jump(3, jump_from_gb_hl);
    bank_results[3] = run_jump(4, jump_to_gb_4800);
    jump_to_gb_0321();
    bank_results[4] = return_marker;

    gb_sp = TEST_SP;
    gb_wram[TEST_SP - GB_WRAM_BASE] = 0x21;
    gb_wram[TEST_SP - GB_WRAM_BASE + 1] = 0x03;
    return_marker = 0;
    return_enabled = 1;
    uint8_t return_bank_marker = run_jump(4, jump_from_gb_hl);
    return_sp = gb_sp;
    uint8_t manual_return_marker = return_marker;

    gb_sp = TEST_SP;
    return_marker = 0;
    uint8_t call_bank_marker = run_jump(4, gb_000_0800);
    call_sp = gb_sp;
    unsigned call_offset = TEST_SP - GB_WRAM_BASE - 2;

    uint8_t branch_a_preserved = 1;
    for (uint8_t a = 0; a < 2; ++a) {
        gb_a = a;
        gb_f = 0xf0; /* Verify OR overwrites all four guest flags. */
        branch_results[a] = run_jump(4, gb_000_0900);
        branch_flags[a] = gb_f;
        branch_a_preserved &= gb_a == a;
    }

    test_passed = bank_results[0] == 0x33 &&
                  bank_results[1] == 0x44 &&
                  bank_results[2] == 0x33 &&
                  bank_results[3] == 0x44 &&
                  bank_results[4] == 0x21 &&
                  return_bank_marker == 0x44 &&
                  manual_return_marker == 0x21 &&
                  return_sp == TEST_SP + 2 &&
                  call_bank_marker == 0x44 &&
                  return_marker == 0x03 &&
                  call_sp == TEST_SP &&
                  gb_wram[call_offset] == 0x03 &&
                  gb_wram[call_offset + 1] == 0x08 &&
                  branch_results[0] == 0x03 &&
                  branch_results[1] == 0x10 &&
                  branch_flags[0] == 0x80 &&
                  branch_flags[1] == 0x00 &&
                  branch_a_preserved &&
                  gb_rom_bank == 4 &&
                  gb_hl == 0x4800 &&
                  !dispatch_error;

    gb_lcdc = 0x93;
    gb_ly = 0x90;
    gb_bc = 0x1234;
    gb_hl = 0x5678;
    gb_hram[FIXTURE_FLAG] = 0xff;
    for (unsigned i = CLEAR_START - 1; i <= CLEAR_START + CLEAR_LENGTH; ++i) {
        gb_wram[i] = 0xa5;
    }
    gb_000_1000();

    uint8_t wram_cleared = gb_wram[CLEAR_START - 1] == 0xa5 &&
                           gb_wram[CLEAR_START + CLEAR_LENGTH] == 0xa5;
    for (unsigned i = CLEAR_START; i < CLEAR_START + CLEAR_LENGTH; ++i) {
        wram_cleared &= gb_wram[i] == 0;
    }
    test_passed &= fixture_pc == 0x100a && gb_hram[FIXTURE_FLAG] == 4 &&
                   gb_sp == 0xd000 && gb_lcdc == 0x13 && gb_ly == 0 &&
                   gb_bc == 0x1234 && gb_hl == 0x5678 &&
                   gb_a == 0 && gb_f == 0xc0 && wram_cleared && !dispatch_error;

    const uint8_t ret_results[] = {return_bank_marker, manual_return_marker};
    const uint8_t call_results[] = {call_bank_marker, return_marker};
    show_bytes("JP HL:", bank_results, 3);
    show_bytes("JP imm:", bank_results + 3, 2);
    show_bytes("RET:", ret_results, 2);
    os_PutStrFull("GB SP: ");
    show_hex(TEST_SP, 4);
    os_PutStrFull(" -> ");
    show_hex(return_sp, 4);
    os_NewLine();
    show_bytes("CALL/RET:", call_results, 2);
    os_PutStrFull("GB SP restored: ");
    show_hex(call_sp, 4);
    os_NewLine();
    show_bytes("JR NZ:", branch_results, 2);
    os_PutStrFull("FIXTURE ");
    show_hex(fixture_pc, 4);
    os_PutStrFull(" HRAM=");
    show_hex(gb_hram[FIXTURE_FLAG], 2);
    os_PutStrFull(" SP=");
    show_hex(gb_sp, 4);
    os_NewLine();
    os_PutStrFull("LCDC=");
    show_hex(gb_lcdc, 2);
    os_PutStrFull(wram_cleared ? " WRAM CLEAR" : " WRAM FAIL");
    os_NewLine();
    os_PutStrFull(test_passed ? "PASS" : "FAIL");
    os_NewLine();
    os_PutStrFull("Press a key");
    while (!os_GetCSC()) {
    }
    return test_passed ? 0 : 1;
}
