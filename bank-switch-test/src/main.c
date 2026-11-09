#include "runtime.h"

#include <ti/getcsc.h>
#include <ti/screen.h>

enum {
    TEST_SP = 0xc100, BOOT_FLAG = 0xfffe - GB_HRAM_BASE,
    CLEAR_START = 0xc0a0 - GB_WRAM_BASE, CLEAR_LENGTH = 80,
    CLEAR_HRAM = 0xffb8 - GB_HRAM_BASE,
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
volatile uint16_t boot_pc;
volatile uint8_t test_passed;

extern void jump_from_gb_hl(void);
extern void jump_to_gb_4412(void);
extern void jump_to_gb_0175(void);
extern void gb_000_0200(void);
extern void gb_000_0100(void);
extern void gb_000_0150(void);
extern void gb_000_0346(void);
extern void gb_000_034c(void);
extern void gb_000_0354(void);
extern void gb_000_035a(void);
extern void gb_000_0367(void);
extern void gb_000_036c(void);
extern void gb_000_03af(void);
extern void gb_000_03b0(void);
extern void gb_000_03c3(void);
extern void gb_000_0465(void);
extern void gb_000_2578(void);
extern void gb_000_257f(void);
extern void gb_000_2580(void);
extern void gb_000_2585(void);
extern void gb_000_2760(void);
extern void bank_3_4412(void);
extern void bank_4_4412(void);
extern void bank_0_0175(void);
extern void bank_0_0203(void);

static void branch_fallthrough(void)
{
    jump_marker = 0x63;
}

static void branch_taken(void)
{
    jump_marker = 0x73;
}

static void stop_after_boot_call(void)
{
    boot_pc = 0x2588;
}

const GbTarget gb_targets[] = {
    {0, 0x0100, gb_000_0100},
    {0, 0x0150, gb_000_0150},
    {0, 0x0175, bank_0_0175},
    {0, 0x0203, bank_0_0203},
    {0, 0x0346, gb_000_0346},
    {0, 0x034c, gb_000_034c},
    {0, 0x0354, gb_000_0354},
    {0, 0x035a, gb_000_035a},
    {0, 0x0367, gb_000_0367},
    {0, 0x036c, gb_000_036c},
    {0, 0x03af, gb_000_03af},
    {0, 0x03b0, gb_000_03b0},
    {0, 0x03c3, gb_000_03c3},
    {0, 0x0465, gb_000_0465},
    {0, 0x2578, gb_000_2578},
    {0, 0x257f, gb_000_257f},
    {0, 0x2580, gb_000_2580},
    {0, 0x2585, gb_000_2585},
    {0, 0x2588, stop_after_boot_call},
    {0, 0x2763, branch_fallthrough},
    {0, 0x2773, branch_taken},
    {3, 0x4412, bank_3_4412},
    {4, 0x4412, bank_4_4412},
};
const unsigned gb_target_count = sizeof(gb_targets) / sizeof(gb_targets[0]);

void gb_unmapped(void)
{
    dispatch_error = 1;
}

static uint8_t run_jump(uint16_t bank, GbNativeBlock jump)
{
    gb_rom_bank = bank; /* Effect of the translated write to $2000. */
    jump_marker = 0;
    jump();
    return jump_marker;
}

static void show_hex(unsigned value, unsigned width)
{
    static const char digits[] = "0123456789ABCDEF";
    char text[5];
    text[width] = '\0';
    while (width) {
        text[--width] = digits[value & 0x0f];
        value >>= 4;
    }
    os_PutStrFull(text);
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
    gb_hl = 0x4412;
    bank_results[0] = run_jump(3, jump_from_gb_hl);
    bank_results[1] = run_jump(4, jump_from_gb_hl);
    bank_results[2] = run_jump(3, jump_from_gb_hl);
    bank_results[3] = run_jump(4, jump_to_gb_4412);
    jump_to_gb_0175();
    bank_results[4] = return_marker;

    gb_sp = TEST_SP;
    gb_wram[TEST_SP - GB_WRAM_BASE] = 0x75;
    gb_wram[TEST_SP - GB_WRAM_BASE + 1] = 0x01;
    return_marker = 0;
    return_enabled = 1;
    uint8_t return_bank_marker = run_jump(4, jump_from_gb_hl);
    return_sp = gb_sp;
    uint8_t manual_return_marker = return_marker;

    gb_sp = TEST_SP;
    return_marker = 0;
    uint8_t call_bank_marker = run_jump(4, gb_000_0200);
    call_sp = gb_sp;
    unsigned call_offset = TEST_SP - GB_WRAM_BASE - 2;

    uint8_t branch_a_preserved = 1;
    for (uint8_t a = 0; a < 2; ++a) {
        gb_a = a;
        gb_f = 0xf0; /* Verify OR overwrites all four guest flags. */
        branch_results[a] = run_jump(4, gb_000_2760);
        branch_flags[a] = gb_f;
        branch_a_preserved &= gb_a == a;
    }

    test_passed = bank_results[0] == 0x33 &&
                  bank_results[1] == 0x44 &&
                  bank_results[2] == 0x33 &&
                  bank_results[3] == 0x44 &&
                  bank_results[4] == 0x75 &&
                  return_bank_marker == 0x44 &&
                  manual_return_marker == 0x75 &&
                  return_sp == TEST_SP + 2 &&
                  call_bank_marker == 0x44 &&
                  return_marker == 0x23 &&
                  call_sp == TEST_SP &&
                  gb_wram[call_offset] == 0x03 &&
                  gb_wram[call_offset + 1] == 0x02 &&
                  branch_results[0] == 0x63 &&
                  branch_results[1] == 0x73 &&
                  branch_flags[0] == 0x80 &&
                  branch_flags[1] == 0x00 &&
                  branch_a_preserved &&
                  gb_rom_bank == 4 &&
                  gb_hl == 0x4412 &&
                  !dispatch_error;

    gb_a = 0x11; /* CGB boot ROM handoff. */
    gb_sp = 0xfffe;
    gb_lcdc = 0x91;
    gb_ly = 0x8f; /* Start below the routine's LY=$91 wait target. */
    gb_if = 1;
    gb_ie = 0x1f; /* Sentinel to check that interrupt enables are restored. */
    gb_bc = 0x1234;
    gb_hl = 0x5678;
    gb_hram[BOOT_FLAG] = 0xff;
    for (unsigned i = CLEAR_START - 1; i <= CLEAR_START + CLEAR_LENGTH; ++i) {
        gb_wram[i] = 0xa5;
    }
    for (unsigned i = CLEAR_HRAM - 1; i <= CLEAR_HRAM + 3; ++i) {
        gb_hram[i] = 0xa5;
    }
    gb_000_0100();

    uint8_t wram_cleared = gb_wram[CLEAR_START - 1] == 0xa5 &&
                           gb_wram[CLEAR_START + CLEAR_LENGTH] == 0xa5;
    for (unsigned i = CLEAR_START; i < CLEAR_START + CLEAR_LENGTH; ++i) {
        wram_cleared &= gb_wram[i] == 0;
    }
    test_passed &= boot_pc == 0x2588 && gb_hram[BOOT_FLAG] == 1 &&
                   gb_sp == 0xd000 && gb_lcdc == 0x11 && gb_ly == 0 &&
                   gb_if == 0 && gb_ie == 0x1f &&
                   gb_bc == 0x1234 && gb_hl == 0x5678 &&
                   gb_a == 0 && gb_f == 0x80 && wram_cleared &&
                   gb_hram[CLEAR_HRAM - 1] == 0xa5 &&
                   gb_hram[CLEAR_HRAM] == 0 && gb_hram[CLEAR_HRAM + 1] == 0 &&
                   gb_hram[CLEAR_HRAM + 2] == 0 &&
                   gb_hram[CLEAR_HRAM + 3] == 0xa5 && !dispatch_error;

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
    os_PutStrFull("BOOT ");
    show_hex(boot_pc, 4);
    os_PutStrFull(" FFFE=");
    show_hex(gb_hram[BOOT_FLAG], 2);
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
