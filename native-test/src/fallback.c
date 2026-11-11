#include "runtime.h"
#include "timing.h"
#include <stdbool.h>

extern void gb_missing_target(void);

#if GB_FALLBACK
extern volatile uint8_t gb_run_status;
extern const uint8_t rom_preview_mode;
extern uint8_t gb_preview_begin(uint24_t resume);
extern uint8_t gb_preview_finish(void);
extern void gb_checkpoint(void);
extern void gb_halt(void);
extern void gb_user_exit(void);

enum { FLAG_Z = 0x80, FLAG_N = 0x40, FLAG_H = 0x20, FLAG_C = 0x10 };
static bool access_failed;

static uint8_t read_byte(uint16_t address)
{
    uint24_t value = gb_read_byte(address);
    if (value > 255) access_failed = true;
    return value;
}

static void write_byte(uint16_t address, uint8_t value)
{
    if (!gb_write_byte(address, value)) access_failed = true;
}

static uint16_t pair(unsigned index)
{
    switch (index) {
    case 0: return gb_bc;
    case 1: return gb_de;
    case 2: return gb_hl;
    default: return gb_sp;
    }
}

static void set_pair(unsigned index, uint16_t value)
{
    switch (index) {
    case 0: gb_bc = value; break;
    case 1: gb_de = value; break;
    case 2: gb_hl = value; break;
    default: gb_sp = value; break;
    }
}

static uint8_t reg(unsigned index)
{
    if (index == 6) return read_byte(gb_hl);
    if (index == 7) return gb_a;
    uint16_t value = pair(index >> 1);
    return index & 1 ? value : value >> 8;
}

static void set_reg(unsigned index, uint8_t value)
{
    if (index == 6) write_byte(gb_hl, value);
    else if (index == 7) gb_a = value;
    else {
        uint16_t word = pair(index >> 1);
        set_pair(index >> 1, index & 1 ? (word & 0xff00) | value
                                      : (word & 255) | ((uint16_t)value << 8));
    }
}

static bool condition(unsigned index)
{
    uint8_t flag = index < 2 ? FLAG_Z : FLAG_C;
    return !!(gb_f & flag) == !!(index & 1);
}

static void push(uint16_t value)
{
    if (!gb_push_word(value)) access_failed = true;
}

static uint16_t pop(void)
{
    uint24_t value = gb_pop_word();
    if (value > 0xffff) access_failed = true;
    return value;
}

static void cb_instruction(uint8_t opcode)
{
    unsigned group = opcode >> 6, operation = (opcode >> 3) & 7, index = opcode & 7;
    uint8_t value = reg(index), result = value, carry = 0;
    if (group == 1) {
        gb_f = (gb_f & FLAG_C) | FLAG_H | (value & (1 << operation) ? 0 : FLAG_Z);
        return;
    }
    if (group == 2) result &= ~(1 << operation);
    else if (group == 3) result |= 1 << operation;
    else {
        switch (operation) {
        case 0: carry = value >> 7; result = (value << 1) | carry; break;
        case 1: carry = value & 1; result = (value >> 1) | (carry << 7); break;
        case 2: carry = value >> 7; result = (value << 1) | !!(gb_f & FLAG_C); break;
        case 3: carry = value & 1; result = (value >> 1) | ((gb_f & FLAG_C) << 3); break;
        case 4: carry = value >> 7; result = value << 1; break;
        case 5: carry = value & 1; result = (value >> 1) | (value & 0x80); break;
        case 6: result = (value << 4) | (value >> 4); break;
        case 7: carry = value & 1; result = value >> 1; break;
        }
        gb_f = (result ? 0 : FLAG_Z) | (carry ? FLAG_C : 0);
    }
    set_reg(index, result);
}

static void accumulator(unsigned operation)
{
    uint8_t value = gb_a, carry = 0;
    switch (operation) {
    case 0: carry = value >> 7; gb_a = (value << 1) | carry; break;
    case 1: carry = value & 1; gb_a = (value >> 1) | (carry << 7); break;
    case 2: carry = value >> 7; gb_a = (value << 1) | !!(gb_f & FLAG_C); break;
    case 3: carry = value & 1; gb_a = (value >> 1) | ((gb_f & FLAG_C) << 3); break;
    case 4: gb_daa(); return;
    case 5: gb_a = ~value; gb_f |= FLAG_N | FLAG_H; return;
    case 6: gb_f = (gb_f & FLAG_Z) | FLAG_C; return;
    case 7: gb_f = (gb_f & FLAG_Z) | ((gb_f & FLAG_C) ^ FLAG_C); return;
    }
    gb_f = carry ? FLAG_C : 0;
}

/* Instruction sizes and base T-cycles use the same decoder as native code. */
#include "fallback_opcodes.h"

static bool timing_result(uint24_t result, bool halt)
{
    if (result == 1) gb_unmapped();
    else if (result == 2 && halt) gb_halt();
    else if (result == 3) gb_user_exit();
    else if (result) { gb_pc = result; return true; }
    return result != 0;
}

uint8_t gb_fallback_step(void)
{
    uint16_t start = gb_pc;
    access_failed = false;
    uint8_t opcode = read_byte(start);
    uint8_t size = fallback_sizes[opcode];
    if (!size || access_failed) { gb_unmapped(); return 0; }
    uint8_t immediate = size > 1 ? read_byte((uint16_t)(start + 1)) : 0;
    uint16_t operand = immediate;
    if (size == 3) operand |= (uint16_t)read_byte((uint16_t)(start + 2)) << 8;
    if (access_failed) { gb_unmapped(); return 0; }
    unsigned cycles = fallback_cycles[opcode];
    if (opcode == 0xcb) cycles = (immediate & 7) == 6 ? (immediate >> 6 == 1 ? 12 : 16) : 8;
    if (gb_timing_enabled && timing_result(gb_timing_boundary(start, cycles), false)) return !gb_run_status;
    unsigned x = opcode >> 6, y = (opcode >> 3) & 7, z = opcode & 7;
    unsigned p = y >> 1, q = y & 1;
    uint16_t next = start + size, address, word;
    bool taken = false;
    gb_pc = next;
    if (x == 1) {
        if (opcode == 0x76) {
            if (gb_timing_enabled) timing_result(gb_timing_halt(next), true);
            else if (rom_preview_mode) {
                uint8_t result = gb_preview_begin(next);
                if (result == 1) gb_pc = 0x40;
                else if (result == 2) gb_user_exit();
                else gb_unmapped();
            } else gb_halt();
        } else set_reg(y, reg(z));
    } else if (x == 2) gb_alu(y, reg(z));
    else if (x == 0) {
        switch (z) {
        case 0:
            if (y == 1) {
                write_byte(operand, gb_sp);
                write_byte((uint16_t)(operand + 1), gb_sp >> 8);
            } else if (y == 2) {
                if (!gb_stop()) access_failed = true;
            } else if (y >= 3 && (y == 3 || condition(y - 4))) {
                gb_pc = (uint16_t)(next + (int8_t)immediate);
                taken = y != 3;
            }
            break;
        case 1:
            if (!q) set_pair(p, operand);
            else {
                word = pair(p);
                uint16_t original = gb_hl;
                uint24_t sum = (uint24_t)original + word;
                gb_hl = (uint16_t)sum;
                gb_f = (gb_f & FLAG_Z) | (((original & 0xfff) + (word & 0xfff) > 0xfff) ? FLAG_H : 0)
                       | (sum > 0xffff ? FLAG_C : 0);
            }
            break;
        case 2:
            address = pair(p < 2 ? p : 2);
            if (q) gb_a = read_byte(address);
            else write_byte(address, gb_a);
            if (p >= 2) gb_hl = (uint16_t)(address + (p == 2 ? 1 : -1));
            break;
        case 3: set_pair(p, pair(p) + (q ? -1 : 1)); break;
        case 4: set_reg(y, gb_step_byte(reg(y), 0)); break;
        case 5: set_reg(y, gb_step_byte(reg(y), 1)); break;
        case 6: set_reg(y, immediate); break;
        case 7: accumulator(y); break;
        }
    } else {
        switch (z) {
        case 0:
            if (y < 4) { if ((taken = condition(y))) gb_pc = pop(); }
            else if (y == 4) write_byte(0xff00 | immediate, gb_a);
            else if (y == 5) gb_add_sp(immediate);
            else if (y == 6) gb_a = read_byte(0xff00 | immediate);
            else gb_ld_hl_sp(immediate);
            break;
        case 1:
            if (!q) {
                word = pop();
                if (p == 3) { gb_a = word >> 8; gb_f = word & 0xf0; }
                else set_pair(p, word);
            } else if (p == 0 || p == 1) {
                gb_pc = pop();
                if (p == 1) {
                    gb_timing_enable_ime();
                    if (rom_preview_mode && gb_preview_finish()) gb_checkpoint();
                }
            } else if (p == 2) gb_pc = (uint16_t)gb_hl;
            else gb_sp = (uint16_t)gb_hl;
            break;
        case 2:
            if (y < 4) { if ((taken = condition(y))) gb_pc = operand; }
            else {
                address = y & 1 ? operand : 0xff00 | (uint8_t)gb_bc;
                if (y < 6) write_byte(address, gb_a);
                else gb_a = read_byte(address);
            }
            break;
        case 3:
            if (y == 0) gb_pc = operand;
            else if (y == 1) cb_instruction(immediate);
            else if (y == 6) gb_di();
            else if (y == 7) {
                if (gb_timing_enabled) gb_ei();
                else gb_timing_enable_ime();
            }
            break;
        case 4:
            if ((taken = condition(y))) { push(next); gb_pc = operand; }
            break;
        case 5:
            if (!q) push(p == 3 ? ((uint16_t)gb_a << 8) | (gb_f & 0xf0) : pair(p));
            else { push(next); gb_pc = operand; }
            break;
        case 6: gb_alu(y, immediate); break;
        case 7: push(next); gb_pc = y << 3; break;
        }
    }
    if (taken && gb_timing_enabled) {
        gb_timing_extra += (opcode & 0xe7) == 0xc0 || (opcode & 0xe7) == 0xc4 ? 12 : 4;
    }
    if (access_failed) { gb_pc = start; gb_unmapped(); return 0; }
    return !gb_run_status;
}
#endif

GbNativeBlock gb_fallback_run(void)
{
#if GB_FALLBACK
    while (gb_fallback_step()) {
        GbNativeBlock target = gb_lookup(gb_pc);
        if (target) return target;
    }
#else
    gb_missing_target();
#endif
    return 0;
}
