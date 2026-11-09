"""Discover reachable instructions in a cartridge and emit native code and targets."""

from collections import deque
from dataclasses import dataclass, replace
from typing import Optional

from translate import dispatch, lower_instruction


BANK_SIZE = 0x4000
ENTRY = 0x0100
CHECKPOINT = -1

IMPLIED = {
    0x00: "nop", 0x05: "dec b", 0x0c: "inc c", 0x0d: "dec c",
    0x22: "ld [hl+], a", 0x3c: "inc a",
    0x76: "halt", 0xa7: "and a", 0xaf: "xor a", 0xb7: "or a",
    0xc1: "pop bc", 0xc5: "push bc", 0xc9: "ret",
    0xe1: "pop hl", 0xe2: "ldh [c], a", 0xe5: "push hl", 0xe9: "jp hl", 0xf3: "di",
}
BYTE_OPERAND = {
    0x06: "ld b, ${:02x}", 0x0e: "ld c, ${:02x}", 0x3e: "ld a, ${:02x}",
    0xe0: "ldh [$ff{:02x}], a", 0xe6: "and ${:02x}", 0xee: "xor ${:02x}",
    0xf0: "ldh a, [$ff{:02x}]", 0xf6: "or ${:02x}", 0xfe: "cp ${:02x}",
}
WORD_OPERAND = {
    0x21: "ld hl, ${:04x}", 0x31: "ld sp, ${:04x}", 0xc3: "jp ${:04x}",
    0xcd: "call ${:04x}", 0xea: "ld [${:04x}], a",
}
RELATIVE = {0x18: "jp ${:04x}", 0x20: "jr nz, ${:04x}", 0x28: "jr z, ${:04x}"}


@dataclass(frozen=True)
class Instruction:
    opcode: int
    size: int
    text: str
    operand: int = 0


@dataclass(frozen=True)
class State:
    pc: int
    bank: int
    a: Optional[int] = None
    hl: Optional[int] = None
    stack: tuple = ()
    sp: Optional[int] = None
    b: Optional[int] = None
    c: Optional[int] = None
    z: Optional[bool] = None


class Cartridge:
    def __init__(self, data):
        self.data = data
        if len(data) < 2 * BANK_SIZE or len(data) % BANK_SIZE:
            raise ValueError("expected a cartridge image with complete 16 KiB banks")
        size_code = data[0x148]
        banks = len(data) // BANK_SIZE
        if size_code > 8 or banks != 2 << size_code:
            raise ValueError("ROM size must match a standard power-of-two cartridge header")
        mapper = data[0x147]
        if mapper not in (0, 0x19, 0x1a, 0x1b) or (mapper == 0 and banks != 2):
            raise ValueError(f"unsupported cartridge mapper ${mapper:02X}")
        self.mbc5 = mapper != 0
        self.bank_mask = banks - 1
        self.cgb = bool(data[0x143] & 0x80)

    def decode(self, bank, pc):
        offset = bank * BANK_SIZE + (pc % BANK_SIZE)
        opcode = self.data[offset]
        if opcode in IMPLIED:
            return Instruction(opcode, 1, IMPLIED[opcode])
        size = 3 if opcode in WORD_OPERAND else 2
        if pc + size > (pc // BANK_SIZE + 1) * BANK_SIZE:
            raise ValueError("instruction crosses a ROM window boundary")
        operand = int.from_bytes(self.data[offset + 1:offset + size], "little")
        if opcode in BYTE_OPERAND or opcode in WORD_OPERAND:
            template = (BYTE_OPERAND if size == 2 else WORD_OPERAND)[opcode]
            return Instruction(opcode, size, template.format(operand), operand)
        if opcode in RELATIVE:
            signed = operand if operand < 0x80 else operand - 0x100
            target = (pc + size + signed) & 0xffff
            return Instruction(opcode, size, RELATIVE[opcode].format(target), target)
        if opcode == 0xcb and 0x47 <= operand <= 0xbf and operand & 7 == 7:
            operation = "bit" if operand < 0x80 else "res"
            return Instruction(opcode, size, f"{operation} {(operand >> 3) & 7}, a", operand)
        opcode_text = f"CB {operand:02X}" if opcode == 0xcb else f"{opcode:02X}"
        raise ValueError(f"unsupported opcode ${opcode_text}")

    def banks_after_store(self, bank, address, value):
        if not self.mbc5:
            return (bank,)
        if address is None:
            return range(self.bank_mask + 1)
        if 0x2000 <= address < 0x3000:
            lows = range(256) if value is None else (value,)
            return {(bank & 0x100 | low) & self.bank_mask for low in lows}
        if 0x3000 <= address < 0x4000:
            highs = (0, 1) if value is None else (value & 1,)
            return {(bank & 0xff | high << 8) & self.bank_mask for high in highs}
        return (bank,)

    @staticmethod
    def stack_after_store(stack, sp, address, value):
        if not stack:
            return stack
        if address is None or sp is None:
            return (None,) * len(stack)
        offset = (address - sp) & 0xffff
        if offset >= 2 * len(stack):
            return stack
        index = len(stack) - 1 - offset // 2
        word = stack[index]
        if word is None or value is None:
            updated = None
        elif offset % 2:
            updated = (word & 0xff) | value << 8
        else:
            updated = (word & 0xff00) | value
        return (*stack[:index], updated, *stack[index + 1:])

    def successors(self, state, instruction):
        opcode, value = instruction.opcode, instruction.operand
        next_pc = state.pc + instruction.size
        next_state = replace(state, pc=next_pc)
        a, hl, stack, sp = state.a, state.hl, state.stack, state.sp
        b, c, z = state.b, state.c, state.z

        if opcode == 0x76:
            return
        if opcode == 0xc9:
            if stack and stack[-1] is not None:
                yield replace(next_state, pc=stack[-1], stack=stack[:-1],
                              sp=None if sp is None else (sp + 2) & 0xffff)
            return
        if opcode == 0xe9:
            if hl is not None:
                yield replace(next_state, pc=hl)
            return
        if opcode == 0xcd:
            yield replace(next_state, pc=value, stack=(*stack, next_pc),
                          sp=None if sp is None else (sp - 2) & 0xffff)
            return
        if opcode in (0x18, 0xc3):
            yield replace(next_state, pc=value)
            return
        if opcode in (0x20, 0x28):
            taken_when = opcode == 0x28
            if z is None or z == taken_when:
                yield replace(next_state, pc=value)
            if z is None or z != taken_when:
                yield next_state
            return

        if opcode == 0x3e:
            a = value
        elif opcode == 0xf0:
            a = None
        elif opcode == 0xaf:
            a = 0
        elif a is not None:
            if opcode == 0x3c:
                a = (a + 1) & 0xff
            elif opcode == 0xe6:
                a &= value
            elif opcode == 0xee:
                a ^= value
            elif opcode == 0xf6:
                a |= value
            elif opcode == 0xcb and value >= 0x80:
                a &= ~(1 << ((value >> 3) & 7))

        if opcode in (0x3c, 0xa7, 0xaf, 0xb7, 0xe6, 0xee, 0xf6):
            z = None if a is None else a == 0
        elif opcode == 0xfe:
            z = None if a is None else a == value
        elif opcode == 0xcb and value < 0x80:
            z = None if a is None else not (a & (1 << ((value >> 3) & 7)))
        elif opcode == 0x06:
            b = value
        elif opcode == 0x0e:
            c = value
        elif opcode == 0x05:
            b = None if b is None else (b - 1) & 0xff
            z = None if b is None else b == 0
        elif opcode in (0x0c, 0x0d):
            c = None if c is None else (c + (1 if opcode == 0x0c else -1)) & 0xff
            z = None if c is None else c == 0

        banks = (state.bank,)
        if opcode == 0xea:
            banks = self.banks_after_store(state.bank, value, a)
            stack = self.stack_after_store(stack, sp, value, a)
        elif opcode == 0xe0:
            stack = self.stack_after_store(stack, sp, 0xff00 | value, a)
        elif opcode == 0xe2:
            stack = self.stack_after_store(stack, sp, None if c is None else 0xff00 | c, a)
        elif opcode == 0x22:
            banks = self.banks_after_store(state.bank, hl, a)
            stack = self.stack_after_store(stack, sp, hl, a)
            hl = None if hl is None else (hl + 1) & 0xffff
        elif opcode == 0x21:
            hl = value
        elif opcode == 0x31:
            stack, sp = (), value
        elif opcode in (0xc5, 0xe5):
            bc = None if b is None or c is None else (b << 8) | c
            stack = (*stack, hl if opcode == 0xe5 else bc)
            sp = None if sp is None else (sp - 2) & 0xffff
        elif opcode in (0xc1, 0xe1):
            word = stack[-1] if stack else None
            if opcode == 0xe1:
                hl = word
            else:
                b, c = (None, None) if word is None else (word >> 8, word & 0xff)
            stack = stack[:-1]
            sp = None if sp is None else (sp + 2) & 0xffff
        for bank in sorted(banks):
            yield State(next_pc, bank, a, hl, stack, sp, b, c, z)

    def discover(self, extra_entries=(), stop_at=None):
        entry_a = 0x11 if self.cgb else 0x01
        work = deque([State(ENTRY, 1, entry_a, sp=0xfffe)])
        if stop_at is not None:
            bank, pc = stop_at
            if not 0 <= bank <= self.bank_mask or not 0 <= pc < 0x8000 or (pc < BANK_SIZE and bank):
                raise ValueError("checkpoint is outside cartridge ROM")
        for bank, pc in extra_entries:
            if not 0 <= bank <= self.bank_mask or not 0 <= pc < 0x8000:
                raise ValueError("extra entry is outside cartridge ROM")
            work.append(State(pc, bank))
        seen = set()
        instructions = {}
        occupied = {}
        while work:
            state = work.popleft()
            if state in seen:
                continue
            seen.add(state)
            bank = state.bank if state.pc >= BANK_SIZE else 0
            location = f"${bank:03X}:${state.pc:04X}"
            try:
                if len(seen) > 100_000 or len(state.stack) > 32:
                    raise ValueError("static analysis exceeded its state or call-depth limit")
                if not 0 <= state.pc < 0x8000:
                    raise ValueError("control transfer outside cartridge ROM")
                key = bank, state.pc
                if key == stop_at:
                    if occupied.setdefault(key, state.pc) != state.pc:
                        raise ValueError("checkpoint is inside an instruction")
                    instructions[key] = Instruction(CHECKPOINT, 0, "")
                    continue
                if key not in instructions:
                    instruction = self.decode(bank, state.pc)
                    lower_instruction(instruction.text, state.pc)
                    for pc in range(state.pc, state.pc + instruction.size):
                        owner = occupied.setdefault((bank, pc), state.pc)
                        if owner != state.pc:
                            raise ValueError(f"instruction overlaps code at ${owner:04X}")
                    instructions[key] = instruction
                work.extend(self.successors(state, instructions[key]))
            except ValueError as error:
                raise ValueError(f"{location}: {error}") from error
        if stop_at is not None and stop_at not in instructions:
            raise ValueError(f"checkpoint ${stop_at[0]:03X}:${stop_at[1]:04X} is unreachable")
        return instructions


def symbol(bank, pc):
    return f"gb_{bank:03x}_{pc:04x}"


def compile_rom(data, extra_entries=(), watch_address=0, stop_at=None):
    if watch_address and not (0xc000 <= watch_address < 0xd000 or 0xff80 <= watch_address <= 0xfffe):
        raise ValueError("watch address must be in fixed WRAM or HRAM")
    cartridge = Cartridge(data)
    instructions = cartridge.discover(extra_entries, stop_at)
    assembly = ["    .assume ADL=1", "    .section .text"]
    targets = ['#include "runtime.h"', ""]
    entries = []
    for (bank, pc), instruction in sorted(instructions.items()):
        name = symbol(bank, pc)
        targets.append(f"extern void {name}(void);")
        entries.append(f"    {{0x{bank:03x}, 0x{pc:04x}, {name}}},")
        assembly.extend(["", f"    .global _{name}", f"_{name}:"])
        if instruction.opcode in (CHECKPOINT, 0x22, 0x76, 0xc1, 0xc5, 0xc9, 0xcd, 0xe0, 0xe1, 0xe2, 0xe5, 0xea):
            assembly.extend([f"    ld hl, 0x{pc:04x}", "    ld (_gb_pc), hl"])
        if instruction.opcode == CHECKPOINT:
            assembly.append("    jp _gb_checkpoint")
            continue
        native, size = lower_instruction(instruction.text, pc)
        assembly.extend(f"    {line}" for line in native)
        if size is not None:
            next_pc = pc + size
            # A store can remap the active bank, and crossing $4000 needs a lookup.
            if ((bank, next_pc) in instructions and pc // BANK_SIZE == next_pc // BANK_SIZE
                    and (pc < BANK_SIZE or instruction.opcode not in (0x22, 0xea))):
                assembly.append(f"    jp _{symbol(bank, next_pc)}")
            else:
                assembly.extend(f"    {line}" for line in dispatch(next_pc))
    targets.extend([
        "", "const GbTarget gb_targets[] = {", *entries, "};",
        "const unsigned gb_target_count = sizeof(gb_targets) / sizeof(gb_targets[0]);",
        f"const uint16_t rom_bank_mask = {cartridge.bank_mask};",
        f"const uint16_t rom_watch_address = 0x{watch_address:04x};",
        f"const uint8_t rom_mbc5 = {int(cartridge.mbc5)};",
        f"const uint8_t rom_cgb = {int(cartridge.cgb)};",
    ])
    return "\n".join(assembly) + "\n", "\n".join(targets) + "\n"
