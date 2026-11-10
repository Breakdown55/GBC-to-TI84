"""Discover reachable instructions in a cartridge and emit native code and targets."""

from collections import deque
from dataclasses import dataclass, replace
from typing import Optional

from translate import dispatch, lower_instruction


BANK_SIZE = 0x4000
ENTRY = 0x0100
CHECKPOINT = -1

IMPLIED = {
    0x00: "nop", 0x05: "dec b", 0x0b: "dec bc", 0x0c: "inc c", 0x0d: "dec c",
    0x19: "add hl, de", 0x22: "ld [hl+], a", 0x2a: "ld a, [hl+]",
    0x32: "ld [hl-], a", 0x3c: "inc a", 0x4f: "ld c, a", 0x67: "ld h, a",
    0x6f: "ld l, a", 0x76: "halt", 0x77: "ld [hl], a", 0x78: "ld a, b", 0x79: "ld a, c",
    0x95: "sub l", 0xa7: "and a", 0xaf: "xor a", 0xb1: "or c", 0xb7: "or a",
    0xc0: "ret nz", 0xc1: "pop bc", 0xc5: "push bc", 0xc8: "ret z", 0xc9: "ret",
    0xe1: "pop hl", 0xe2: "ldh [c], a", 0xe5: "push hl", 0xe9: "jp hl",
    0xf1: "pop af", 0xf3: "di", 0xf5: "push af",
}
BYTE_OPERAND = {
    0x06: "ld b, ${:02x}", 0x0e: "ld c, ${:02x}", 0x3e: "ld a, ${:02x}",
    0xe0: "ldh [$ff{:02x}], a", 0xe6: "and ${:02x}", 0xee: "xor ${:02x}",
    0xf0: "ldh a, [$ff{:02x}]", 0xf6: "or ${:02x}", 0xfe: "cp ${:02x}",
}
WORD_OPERAND = {
    0x01: "ld bc, ${:04x}", 0x11: "ld de, ${:04x}", 0x21: "ld hl, ${:04x}",
    0x31: "ld sp, ${:04x}", 0xc2: "jp nz, ${:04x}", 0xc3: "jp ${:04x}",
    0xca: "jp z, ${:04x}", 0xcd: "call ${:04x}",
    0xea: "ld [${:04x}], a", 0xfa: "ld a, [${:04x}]",
}
RELATIVE = {0x18: "jp ${:04x}", 0x20: "jr nz, ${:04x}", 0x28: "jr z, ${:04x}"}
HL_STORES = (0x22, 0x32, 0x77)
PUSHES = (0xc5, 0xe5, 0xf5)
POPS = (0xc1, 0xe1, 0xf1)
RETURNS = (0xc0, 0xc8, 0xc9)


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
    hl: tuple = (None, None)
    stack: tuple = ()
    sp: Optional[int] = None
    b: Optional[int] = None
    c: Optional[int] = None
    z: Optional[bool] = None
    de: Optional[int] = None
    memory: tuple = ()
    key1: Optional[int] = None


def word_bytes(word):
    return (None, None) if word is None else (word & 0xff, word >> 8)


def join_word(low, high):
    return None if low is None or high is None else low | (high << 8)


def known_byte(memory, address):
    return next((value for start, end, value in memory if start <= address < end), None)


def remember_byte(memory, address, value):
    # Runs of repeated bytes keep the state compact through RAM fill loops.
    if not (0xc000 <= address < 0xd000 or 0xff80 <= address < 0xffff):
        return memory
    if known_byte(memory, address) == value:
        return memory
    ranges = []
    for start, end, previous in memory:
        if not start <= address < end:
            ranges.append((start, end, previous))
        else:
            if start < address:
                ranges.append((start, address, previous))
            if address + 1 < end:
                ranges.append((address + 1, end, previous))
    if value is not None:
        ranges.append((address, address + 1, value))
    merged = []
    for start, end, byte in sorted(ranges):
        if merged and merged[-1][1:] == (start, byte):
            merged[-1] = (merged[-1][0], end, byte)
        else:
            merged.append((start, end, byte))
    return tuple(merged)


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
        if opcode == 0x10 and operand == 0:
            return Instruction(opcode, size, "stop")
        if opcode == 0xcb and operand == 0x3f:
            return Instruction(opcode, size, "srl a", operand)
        if opcode == 0xcb and operand >= 0xc0 and operand & 7 == 6:
            return Instruction(opcode, size, f"set {(operand >> 3) & 7}, [hl]", operand)
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
            return ((None, None),) * len(stack)
        offset = (address - sp) & 0xffff
        if offset >= 2 * len(stack):
            return stack
        index = len(stack) - 1 - offset // 2
        low, high = stack[index]
        updated = (low, value) if offset % 2 else (value, high)
        return (*stack[:index], updated, *stack[index + 1:])

    @staticmethod
    def stack_after_bank_write(stack, sp):
        if not stack or sp is None:
            return ((None, None),) * len(stack)
        result = []
        for index, (low, high) in enumerate(stack):
            address = (sp + 2 * (len(stack) - 1 - index)) & 0xffff
            result.append((
                None if 0xd000 <= address < 0xe000 else low,
                None if 0xd000 <= ((address + 1) & 0xffff) < 0xe000 else high,
            ))
        return tuple(result)

    @staticmethod
    def push_memory(memory, sp, word):
        if sp is None:
            return ()
        low, high = word
        memory = remember_byte(memory, (sp - 1) & 0xffff, high)
        return remember_byte(memory, (sp - 2) & 0xffff, low)

    def load_byte(self, state, address, rom_bytes):
        if address is None:
            return None
        if address < 0x8000:
            bank = 0 if address < BANK_SIZE else state.bank
            rom_bytes.add((bank, address))
            return self.data[bank * BANK_SIZE + address % BANK_SIZE]
        if address == 0xff4d:
            if not self.cgb:
                return 0xff
            return None if state.key1 is None else state.key1 | 0x7e
        return known_byte(state.memory, address)

    def successors(self, state, instruction, rom_bytes):
        opcode, value = instruction.opcode, instruction.operand
        next_pc = state.pc + instruction.size
        next_state = replace(state, pc=next_pc)
        a, hl, stack, sp = state.a, state.hl, state.stack, state.sp
        b, c, z = state.b, state.c, state.z
        de, memory, key1 = state.de, state.memory, state.key1
        hl_address = join_word(*hl)

        if opcode == 0x76:
            return
        if opcode in RETURNS:
            taken_when = opcode == 0xc8
            target = join_word(*stack[-1]) if stack else None
            if target is not None and (opcode == 0xc9 or z is None or z == taken_when):
                yield replace(next_state, pc=target, stack=stack[:-1],
                              sp=None if sp is None else (sp + 2) & 0xffff)
            if opcode != 0xc9 and (z is None or z != taken_when):
                yield next_state
            return
        if opcode == 0xe9:
            if hl_address is not None:
                yield replace(next_state, pc=hl_address)
            return
        if opcode == 0xcd:
            word = word_bytes(next_pc & 0xffff)
            yield replace(next_state, pc=value, stack=(*stack, word),
                          sp=None if sp is None else (sp - 2) & 0xffff,
                          memory=self.push_memory(memory, sp, word))
            return
        if opcode in (0x18, 0xc3):
            yield replace(next_state, pc=value)
            return
        if opcode in (0x20, 0x28, 0xc2, 0xca):
            taken_when = opcode in (0x28, 0xca)
            if z is None or z == taken_when:
                yield replace(next_state, pc=value)
            if z is None or z != taken_when:
                yield next_state
            return

        if opcode == 0x3e:
            a = value
        elif opcode == 0xf0:
            a = self.load_byte(state, 0xff00 | value, rom_bytes)
        elif opcode == 0xfa:
            a = self.load_byte(state, value, rom_bytes)
        elif opcode == 0x2a:
            a = self.load_byte(state, hl_address, rom_bytes)
            hl = word_bytes(None if hl_address is None else (hl_address + 1) & 0xffff)
        elif opcode == 0x78:
            a = b
        elif opcode == 0x79:
            a = c
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
            elif opcode == 0xb1:
                a = None if c is None else a | c
            elif opcode == 0x95:
                a = None if hl[0] is None else (a - hl[0]) & 0xff
            elif opcode == 0xcb and value == 0x3f:
                a >>= 1
            elif opcode == 0xcb and 0x80 <= value < 0xc0:
                a &= ~(1 << ((value >> 3) & 7))

        if opcode in (0x3c, 0x95, 0xa7, 0xaf, 0xb1, 0xb7, 0xe6, 0xee, 0xf6) or (
                opcode == 0xcb and value == 0x3f):
            z = None if a is None else a == 0
        elif opcode == 0xfe:
            z = None if a is None else a == value
        elif opcode == 0xcb and value < 0x80:
            z = None if a is None else not (a & (1 << ((value >> 3) & 7)))
        elif opcode == 0x01:
            c, b = word_bytes(value)
        elif opcode == 0x06:
            b = value
        elif opcode == 0x0e:
            c = value
        elif opcode == 0x4f:
            c = a
        elif opcode == 0x0b:
            bc = join_word(c, b)
            c, b = word_bytes(None if bc is None else (bc - 1) & 0xffff)
        elif opcode == 0x05:
            b = None if b is None else (b - 1) & 0xff
            z = None if b is None else b == 0
        elif opcode in (0x0c, 0x0d):
            c = None if c is None else (c + (1 if opcode == 0x0c else -1)) & 0xff
            z = None if c is None else c == 0

        banks = (state.bank,)
        set_bit = opcode == 0xcb and value >= 0xc0
        if opcode in (0xea, 0xe0, 0xe2, *HL_STORES) or set_bit:
            if opcode == 0xea:
                address = value
            elif opcode == 0xe0:
                address = 0xff00 | value
            elif opcode == 0xe2:
                address = None if c is None else 0xff00 | c
            else:
                address = hl_address
            stored = a
            if set_bit:
                loaded = self.load_byte(state, address, rom_bytes)
                stored = None if loaded is None else loaded | (1 << ((value >> 3) & 7))
            high_only = opcode in (0xe0, 0xe2)
            if not high_only:
                banks = self.banks_after_store(state.bank, address, stored)
            if address is None and high_only:
                memory = tuple(run for run in memory if run[0] < 0xff80)
            elif address is None:
                memory = ()
            else:
                memory = remember_byte(memory, address, stored)
            if address is not None or not high_only:
                stack = self.stack_after_store(stack, sp, address, stored)
            elif sp is None or any(0xff80 <= ((sp + i) & 0xffff) < 0xffff
                                   for i in range(2 * len(stack))):
                stack = ((None, None),) * len(stack)
            if self.cgb:
                if address in (None, 0xff70):
                    stack = self.stack_after_bank_write(stack, sp)
                if address is None:
                    key1 = None
                elif address == 0xff4d and key1 is not None:
                    key1 = None if stored is None else (key1 & 0x80) | (stored & 1)
            if opcode in (0x22, 0x32):
                step = 1 if opcode == 0x22 else -1
                hl = word_bytes(None if hl_address is None else (hl_address + step) & 0xffff)
        elif opcode == 0x21:
            hl = word_bytes(value)
        elif opcode == 0x11:
            de = value
        elif opcode == 0x19:
            hl = word_bytes(None if hl_address is None or de is None else (hl_address + de) & 0xffff)
        elif opcode == 0x67:
            hl = (hl[0], a)
        elif opcode == 0x6f:
            hl = (a, hl[1])
        elif opcode == 0x10 and key1 is not None and key1 & 1:
            key1 = (key1 ^ 0x80) & 0x80
        elif opcode == 0x31:
            stack, sp = (), value
        elif opcode in PUSHES:
            word = {0xc5: (c, b), 0xe5: hl, 0xf5: (None, a)}[opcode]
            stack = (*stack, word)
            memory = self.push_memory(memory, sp, word)
            sp = None if sp is None else (sp - 2) & 0xffff
        elif opcode in POPS:
            low, high = stack[-1] if stack else (None, None)
            if opcode == 0xe1:
                hl = (low, high)
            elif opcode == 0xf1:
                a, z = high, None if low is None else bool(low & 0x80)
            else:
                c, b = low, high
            stack = stack[:-1]
            sp = None if sp is None else (sp + 2) & 0xffff
        for bank in sorted(banks):
            yield replace(next_state, bank=bank, a=a, hl=hl, stack=stack, sp=sp,
                          b=b, c=c, z=z, de=de, memory=memory, key1=key1)

    def discover(self, extra_entries=(), stop_at=None, rom_bytes=None):
        entry_a = 0x11 if self.cgb else 0x01
        work = deque([State(ENTRY, 1, entry_a, sp=0xfffe, key1=0 if self.cgb else None)])
        if rom_bytes is None:
            rom_bytes = set()
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
                work.extend(self.successors(state, instructions[key], rom_bytes))
            except ValueError as error:
                raise ValueError(f"{location}: {error}") from error
        if stop_at is not None and stop_at not in instructions:
            raise ValueError(f"checkpoint ${stop_at[0]:03X}:${stop_at[1]:04X} is unreachable")
        return instructions


def symbol(bank, pc):
    return f"gb_{bank:03x}_{pc:04x}"


def compile_rom(data, extra_entries=(), watch_address=0, stop_at=None):
    if watch_address and not (0x8000 <= watch_address < 0xa000 or
                              0xc000 <= watch_address < 0xe000 or
                              0xff80 <= watch_address <= 0xfffe):
        raise ValueError("watch address must be in VRAM, WRAM or HRAM")
    cartridge = Cartridge(data)
    rom_bytes = set()
    instructions = cartridge.discover(extra_entries, stop_at, rom_bytes)
    assembly = ["    .assume ADL=1", "    .section .text"]
    targets = ['#include "runtime.h"', ""]
    entries = []
    for (bank, pc), instruction in sorted(instructions.items()):
        name = symbol(bank, pc)
        targets.append(f"extern void {name}(void);")
        entries.append(f"    {{0x{bank:03x}, 0x{pc:04x}, {name}}},")
        assembly.extend(["", f"    .global _{name}", f"_{name}:"])
        opcode = instruction.opcode
        set_bit = opcode == 0xcb and instruction.operand >= 0xc0
        remaps = opcode in (*HL_STORES, 0xea) or set_bit
        if remaps or opcode in (CHECKPOINT, 0x10, 0x2a, 0x76, 0xcd, 0xe0, 0xe2, 0xfa,
                               *PUSHES, *POPS, *RETURNS):
            assembly.extend([f"    ld hl, 0x{pc:04x}", "    ld (_gb_pc), hl"])
        if opcode == CHECKPOINT:
            assembly.append("    jp _gb_checkpoint")
            continue
        native, size = lower_instruction(instruction.text, pc)
        assembly.extend(f"    {line}" for line in native)
        if size is not None:
            next_pc = pc + size
            # Adjacent blocks fall through. A bank change needs a lookup.
            if ((bank, next_pc) not in instructions or pc // BANK_SIZE != next_pc // BANK_SIZE
                    or (pc >= BANK_SIZE and remaps)):
                assembly.extend(f"    {line}" for line in dispatch(next_pc))
    data_entries = [
        f"    {{0x{bank:03x}, 0x{address:04x}, 0x{data[bank * BANK_SIZE + address % BANK_SIZE]:02x}}},"
        for bank, address in sorted(rom_bytes)
    ]
    targets.extend([
        "", "const GbTarget gb_targets[] = {", *entries, "};",
        "const unsigned gb_target_count = sizeof(gb_targets) / sizeof(gb_targets[0]);",
        f"const uint16_t rom_bank_mask = {cartridge.bank_mask};",
        f"const uint16_t rom_watch_address = 0x{watch_address:04x};",
        f"const uint8_t rom_mbc5 = {int(cartridge.mbc5)};",
        f"const uint8_t rom_cgb = {int(cartridge.cgb)};",
        "", "const GbRomByte gb_rom_bytes[] = {", *(data_entries or ["    {0, 0, 0},"]), "};",
        f"const unsigned gb_rom_byte_count = {len(data_entries)};",
    ])
    return "\n".join(assembly) + "\n", "\n".join(targets) + "\n"
