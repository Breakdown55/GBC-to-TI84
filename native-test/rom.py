"""Discover reachable instructions in a cartridge and emit native code and targets."""

from bisect import bisect_left, bisect_right
from collections import deque
from dataclasses import dataclass, replace
from itertools import groupby
from typing import Optional
from native_overlays import region_key

from translate import (BYTE_REGISTERS, dispatch, forward_native_flags, lower_instruction,
                       native_entries, omit_dead_flag_updates, reuse_native_registers)


BANK_SIZE = 0x4000
ENTRY = 0x0100
DEFAULT_ANALYSIS_LIMIT = 750_000
HRAM_BASE = 0xff80
HRAM_END = 0xffff
VRAM_BASE = 0x8000
VRAM_SIZE = 0x2000
CHECKPOINT = -1
UNSUPPORTED = -2
SRAM_BASE = 0x10000
WRAM_BANKS_BASE = 0x30000
VRAM_BANKS_BASE = 0x40000
UNKNOWN_SRAM_BYTE = -1
SOUND_BASE = 0xff10
SOUND_POWER = 0xff26
SOUND_READ_MASK = bytes.fromhex("80 3f 00 ff bf ff 3f 00 ff bf 7f ff 9f ff bf ff ff 00 00 bf 00 00 70")
UNKNOWN_SOUND = (None,) * len(SOUND_READ_MASK)
INITIAL_SOUND = (0,) * 20 + (0x77, 0xf3, 0x80)

IMPLIED = {
    0x00: "nop", 0x02: "ld [bc], a", 0x03: "inc bc", 0x07: "rlca", 0x09: "add hl, bc",
    0x0a: "ld a, [bc]", 0x0b: "dec bc", 0x0f: "rrca",
    0x12: "ld [de], a", 0x13: "inc de", 0x17: "rla", 0x19: "add hl, de", 0x1a: "ld a, [de]",
    0x1b: "dec de", 0x1f: "rra", 0x22: "ld [hl+], a", 0x23: "inc hl",
    0x27: "daa", 0x29: "add hl, hl", 0x2a: "ld a, [hl+]", 0x2b: "dec hl", 0x2f: "cpl",
    0x32: "ld [hl-], a", 0x33: "inc sp", 0x37: "scf", 0x39: "add hl, sp",
    0x3a: "ld a, [hl-]", 0x3b: "dec sp", 0x3f: "ccf",
    0x76: "halt",
    0xc0: "ret nz", 0xc1: "pop bc", 0xc5: "push bc", 0xc8: "ret z", 0xc9: "ret",
    0xd0: "ret nc", 0xd1: "pop de", 0xd5: "push de", 0xd8: "ret c", 0xd9: "reti",
    0xe1: "pop hl", 0xe2: "ldh [c], a", 0xe5: "push hl", 0xe9: "jp hl",
    0xf1: "pop af", 0xf2: "ldh a, [c]", 0xf3: "di", 0xf5: "push af", 0xf9: "ld sp, hl", 0xfb: "ei",
}
BYTE_OPERAND = {
    0x06: "ld b, ${:02x}", 0x0e: "ld c, ${:02x}", 0x16: "ld d, ${:02x}",
    0x1e: "ld e, ${:02x}", 0x26: "ld h, ${:02x}", 0x2e: "ld l, ${:02x}",
    0x36: "ld [hl], ${:02x}", 0x3e: "ld a, ${:02x}",
    0xe0: "ldh [$ff{:02x}], a", 0xf0: "ldh a, [$ff{:02x}]",
}
WORD_OPERAND = {
    0x08: "ld [${:04x}], sp",
    0x01: "ld bc, ${:04x}", 0x11: "ld de, ${:04x}", 0x21: "ld hl, ${:04x}",
    0x31: "ld sp, ${:04x}", 0xc2: "jp nz, ${:04x}", 0xc3: "jp ${:04x}",
    0xc4: "call nz, ${:04x}", 0xca: "jp z, ${:04x}", 0xcc: "call z, ${:04x}",
    0xcd: "call ${:04x}", 0xd2: "jp nc, ${:04x}", 0xd4: "call nc, ${:04x}",
    0xda: "jp c, ${:04x}", 0xdc: "call c, ${:04x}",
    0xea: "ld [${:04x}], a", 0xfa: "ld a, [${:04x}]",
}
RELATIVE = {0x18: "jp ${:04x}", 0x20: "jr nz, ${:04x}", 0x28: "jr z, ${:04x}",
            0x30: "jr nc, ${:04x}", 0x38: "jr c, ${:04x}"}
REGISTERS = ("b", "c", "d", "e", "h", "l", "[hl]", "a")
IMMEDIATE_LOADS = {0x06: "b", 0x0e: "c", 0x16: "d", 0x1e: "e", 0x26: "h", 0x2e: "l", 0x3e: "a"}
HL_STORES = (0x22, 0x32, 0x36, 0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x77)
HL_LOADS = (0x2a, 0x3a, 0x46, 0x4e, 0x56, 0x5e, 0x66, 0x6e, 0x7e)
MEMORY_STORES = (*HL_STORES, 0x02, 0x08, 0x12, 0xea)
MEMORY_LOADS = (*HL_LOADS, 0x0a, 0x1a, 0xf2, 0xfa)
PUSHES = (0xc5, 0xd5, 0xe5, 0xf5)
POPS = (0xc1, 0xd1, 0xe1, 0xf1)
RETURNS = (0xc0, 0xc8, 0xc9, 0xd0, 0xd8, 0xd9)
CONDITIONAL_JR = (0x20, 0x28, 0x30, 0x38)
CONDITIONAL_JP = (0xc2, 0xca, 0xd2, 0xda)
CONDITIONAL_CALL = (0xc4, 0xcc, 0xd4, 0xdc)
CONDITIONAL_RET = (0xc0, 0xc8, 0xd0, 0xd8)
CONTROL_TRANSFERS = {
    0x18, 0x76, 0xc3, 0xcd, 0xe9, *CONDITIONAL_JR, *CONDITIONAL_JP, *CONDITIONAL_CALL, *RETURNS,
}
CHECKED_HELPERS = {
    0x10: ("stop", False), 0x22: ("store_hli", False), 0x2a: ("load_hli", False),
    0x32: ("store_hld", False), 0x3a: ("load_hld", False), 0x77: ("store_hl", False),
    0xe0: ("write_high", True), 0xea: ("store_a", True), 0xfa: ("load_a", True),
}
ALU_NAMES = ("add", "adc", "sub", "sbc", "and", "xor", "or", "cp")
ALU_IMMEDIATE = (0xc6, 0xce, 0xd6, 0xde, 0xe6, 0xee, 0xf6, 0xfe)
SHIFTS = ("rlc", "rrc", "rl", "rr", "sla", "sra", "swap", "srl")


@dataclass(frozen=True)
class Instruction:
    opcode: int
    size: int
    text: str
    operand: int = 0

    def register_only(self):
        opcode = self.opcode
        if 0x40 <= opcode < 0x80:
            return opcode & 7 != 6 and (opcode >> 3) & 7 != 6
        if 0x80 <= opcode < 0xc0:
            return opcode & 7 != 6
        if opcode == 0xcb:
            return self.operand & 7 != 6
        return opcode in (*IMMEDIATE_LOADS, *ALU_IMMEDIATE, 0x00, 0x07, 0x0f, 0x17, 0x1f,
                          0x27, 0x2f, 0x37, 0x3f, 0xe8, 0xf8, 0xf9) or (
            opcode < 0x40 and (opcode & 7 in (4, 5) and opcode not in (0x34, 0x35)
                              or opcode & 15 in (1, 3, 9, 11))
        )

    def tcycles(self):
        opcode = self.opcode
        if opcode == 0x08:
            return 20
        if opcode == 0xcb:
            if self.operand & 7 == 6:
                return 12 if 0x40 <= self.operand < 0x80 else 16
            return 8
        if opcode & 0xc7 == 0xc7 or opcode in (0xc3, 0xc9, 0xd9, *PUSHES, 0xe8, 0xea, 0xfa):
            return 16
        if opcode == 0xcd:
            return 24
        if opcode in (0x18, 0xe0, 0xf0, 0xf8, *POPS, *CONDITIONAL_JP, *CONDITIONAL_CALL):
            return 12
        if opcode in (*CONDITIONAL_RET, *CONDITIONAL_JR, *ALU_IMMEDIATE, *IMMEDIATE_LOADS,
                      0x02, 0x03, 0x09, 0x0a, 0x0b, 0x12, 0x13, 0x19, 0x1a, 0x1b, 0x22,
                      0x23, 0x29, 0x2a, 0x2b, 0x32, 0x33, 0x39, 0x3a, 0x3b, 0xe2, 0xf2, 0xf9):
            return 8
        if 0x40 <= opcode < 0x80 and opcode != 0x76 and (opcode & 7 == 6 or (opcode >> 3) & 7 == 6):
            return 8
        if 0x80 <= opcode < 0xc0 and opcode & 7 == 6:
            return 8
        if opcode in (0x34, 0x35, 0x36):
            return 12
        if opcode in (0x01, 0x11, 0x21, 0x31):
            return 12
        return 4


class KnownMemory(tuple):
    """Immutable RAM ranges shared by successive analysis states."""

    def __new__(cls, ranges=()):
        memory = super().__new__(cls, ranges)
        memory.digest = tuple.__hash__(memory)
        return memory

    def __hash__(self):
        return self.digest


EMPTY_MEMORY = KnownMemory()


def common_memory(first, second):
    """Retain facts true on both paths. Missing RAM facts mean unknown."""
    result = []
    i = j = 0
    while i < len(first) and j < len(second):
        a, b, value = first[i]
        c, d, other = second[j]
        start, end = max(a, c), min(b, d)
        if start < end and value == other:
            if result and result[-1][1] == start and result[-1][2] == value:
                result[-1] = result[-1][0], end, value
            else:
                result.append((start, end, value))
        if b <= d: i += 1
        if d <= b: j += 1
    return KnownMemory(result)


def merge_frame_states(states):
    """Bound input-history duplication without merging registers or return stacks.

    SRAM facts are part of the key because an absent erased-SRAM fact means FF,
    unlike ordinary RAM where absence means unknown. VRAM and banking state
    also remain distinct. A few concrete alternatives avoid needless widening.
    """
    groups = {}
    for state in states:
        sram = KnownMemory(run for run in state.memory if SRAM_BASE <= run[0] < WRAM_BANKS_BASE)
        key = replace(state, memory=sram)
        groups.setdefault(key, []).append(state)
    result = deque()
    for alternatives in groups.values():
        if len(alternatives) <= 8:
            result.extend(alternatives)
            continue
        memory = alternatives[0].memory
        for state in alternatives[1:]:
            memory = common_memory(memory, state.memory)
        result.append(replace(alternatives[0], memory=memory))
    return result


@dataclass(frozen=True)
class SavedFlags:
    zero: Optional[bool]
    carry: Optional[bool]


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
    carry: Optional[bool] = None
    de: tuple = (None, None)
    memory: KnownMemory = EMPTY_MEMORY
    key1: Optional[int] = None
    lcdc: Optional[int] = None
    joyp: Optional[int] = None
    ly: Optional[int] = None
    hdma_source: tuple = (None, None)
    hdma_destination: tuple = (None, None)
    hdma_async: bool = False
    sram_enabled: Optional[bool] = None
    sram_bank: Optional[int] = None
    sram_erased: bool = False
    svbk: Optional[int] = None
    vbk: Optional[int] = None
    vram: KnownMemory = EMPTY_MEMORY
    preview_remaining: int = 0
    preview_interrupt: bool = False
    joypad_delay: Optional[int] = None
    sound: tuple = UNKNOWN_SOUND


def word_bytes(word):
    return (None, None) if word is None else (word & 0xff, word >> 8)


def join_word(low, high):
    return None if low is None or high is None else low | (high << 8)


def address_key(state):
    return (state.bank if BANK_SIZE <= state.pc < 0x8000 else 0, state.pc)


def ram_address(address):
    return address - 0x2000 if 0xe000 <= address < 0xfe00 else address


def is_stack_address(address):
    address = ram_address(address)
    return 0xc000 <= address < 0xe000 or 0xff80 <= address < 0xffff


def signed_byte(value):
    return value if value < 0x80 else value - 0x100


def register_byte(state, register):
    if register in ("a", "b", "c"):
        return getattr(state, register)
    pair, high = {"d": ("de", 1), "e": ("de", 0), "h": ("hl", 1), "l": ("hl", 0)}[register]
    return getattr(state, pair)[high]


def sound_after_store(sound, address, value):
    power = sound[-1]
    if address == SOUND_POWER:
        if value is None:
            return UNKNOWN_SOUND
        return (*sound[:-1], 0x80) if value & 0x80 else (0,) * len(sound)
    if power == 0:
        return sound
    index = address - SOUND_BASE
    mask = SOUND_READ_MASK[index]
    if value is not None:
        value &= ~mask
    if power is None and sound[index] != value:
        value = None
    return (*sound[:index], value, *sound[index + 1:])


def load_register(state, register, value):
    if register in ("a", "b", "c"):
        return replace(state, **{register: value})
    if register == "d":
        return replace(state, de=(state.de[0], value))
    if register == "e":
        return replace(state, de=(value, state.de[1]))
    if register == "h":
        return replace(state, hl=(state.hl[0], value))
    return replace(state, hl=(value, state.hl[1]))


def condition_value(state, opcode):
    condition = (opcode >> 3) & 3
    flag = state.carry if condition >= 2 else state.z
    return None if flag is None else flag == bool(condition & 1)


def abstract_alu(operation, a, operand, carry, same_operand=False):
    logical = operation in (4, 5, 6)
    if same_operand and operation in (2, 5, 7):
        result, carry = 0, False
    elif a is None or operand is None or (operation in (1, 3) and carry is None):
        result, carry = None, False if logical else None
    elif logical:
        result, carry = {4: a & operand, 5: a ^ operand, 6: a | operand}[operation], False
    else:
        amount = operand + (int(carry) if operation in (1, 3) else 0)
        value = a + amount if operation < 2 else a - amount
        result, carry = value & 0xff, not 0 <= value <= 0xff
    return (a if operation == 7 else result), (None if result is None else result == 0), carry


def abstract_shift(operation, byte, carry):
    if operation == 6:
        result = None if byte is None else ((byte << 4) | (byte >> 4)) & 0xff
        return result, False
    left = operation in (0, 2, 4)
    outgoing = None if byte is None else bool(byte & (0x80 if left else 1))
    incoming = {0: outgoing, 1: outgoing, 2: carry, 3: carry, 4: False, 7: False}.get(operation)
    if operation == 5:
        incoming = None if byte is None else bool(byte & 0x80)
    if byte is None or incoming is None:
        result = None
    elif left:
        result = ((byte << 1) | incoming) & 0xff
    else:
        result = (byte >> 1) | (int(incoming) << 7)
    return result, outgoing


def known_byte(memory, address):
    address = ram_address(address)
    index = bisect_right(memory, (address + 1,)) - 1
    return memory[index][2] if index >= 0 and address < memory[index][1] else None


def remember_byte(memory, address, value):
    # Runs of repeated bytes keep the state compact through RAM fill loops.
    address = ram_address(address)
    if not (0xc000 <= address < 0xd000 or 0xff80 <= address < 0xffff or address >= SRAM_BASE):
        return memory
    index = bisect_right(memory, (address + 1,)) - 1
    found = index >= 0 and address < memory[index][1]
    if (memory[index][2] if found else None) == value:
        return memory
    first = index if found else index + 1
    last = index + 1
    before = max(first - 1, 0)
    after = min(last + 1, len(memory))
    ranges = list(memory[before:first])
    if found:
        start, end, previous = memory[index]
        if start < address:
            ranges.append((start, address, previous))
    if value is not None:
        ranges.append((address, address + 1, value))
    if found and address + 1 < end:
        ranges.append((address + 1, end, previous))
    ranges.extend(memory[last:after])
    merged = []
    for start, end, byte in ranges:
        if merged and merged[-1][1:] == (start, byte):
            merged[-1] = (merged[-1][0], end, byte)
        else:
            merged.append((start, end, byte))
    return KnownMemory((*memory[:before], *merged, *memory[after:]))


def replace_range(memory, start, end, values=()):
    """Replace known bytes in one interval. Missing values leave it unknown."""
    first = max(bisect_right(memory, (start + 1,)) - 1, 0)
    last = bisect_left(memory, (end,))
    ranges = list(memory[:first])
    if first < len(memory) and memory[first][0] < start:
        low, high, value = memory[first]
        ranges.append((low, min(start, high), value))
    offset = start
    for value, run in groupby(values):
        limit = offset + sum(1 for _ in run)
        if value is not None:
            ranges.append((offset, limit, value))
        offset = limit
    if last and memory[last - 1][1] > end:
        ranges.append((end, *memory[last - 1][1:]))
    ranges.extend(memory[last:])
    merged = []
    for low, high, value in ranges:
        if merged and merged[-1][1:] == (low, value):
            merged[-1] = (merged[-1][0], high, value)
        else:
            merged.append((low, high, value))
    return KnownMemory(merged)


class Cartridge:
    def __init__(self, data, ram_code=()):
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
        ram_sizes = (0, 0x800, 0x2000, 0x8000, 0x20000, 0x10000)
        sram_code = data[0x149]
        self.sram_size = ram_sizes[sram_code] if self.mbc5 and sram_code < len(ram_sizes) else 0
        self.ram_code = self.register_ram_code(ram_code)

    def register_ram_code(self, sections):
        regions = {}
        for start, bank, source, length in sections:
            end = start + length
            if not HRAM_BASE <= start < end <= HRAM_END:
                raise ValueError("RAM code destination must fit in HRAM")
            if (not 0 <= bank <= self.bank_mask or not 0 <= source < 0x8000
                    or (source < BANK_SIZE and bank)
                    or source + length > (source // BANK_SIZE + 1) * BANK_SIZE):
                raise ValueError("RAM code source must fit in one cartridge ROM window")
            offset = bank * BANK_SIZE + source % BANK_SIZE
            payload = self.data[offset:offset + length]
            for pc in range(start, end):
                if pc in regions:
                    raise ValueError(f"RAM code sections overlap at ${pc:04X}")
                regions[pc] = payload, pc - start
        return regions

    def instruction_bytes(self, bank, pc):
        if pc in self.ram_code:
            data, offset = self.ram_code[pc]
            return data, offset, len(data)
        offset = bank * BANK_SIZE + pc % BANK_SIZE
        return self.data, offset, (bank + 1) * BANK_SIZE

    def decode(self, bank, pc):
        data, offset, limit = self.instruction_bytes(bank, pc)
        opcode = data[offset]
        if opcode in IMPLIED:
            return Instruction(opcode, 1, IMPLIED[opcode])
        if 0x40 <= opcode < 0x80:
            return Instruction(opcode, 1, f"ld {REGISTERS[(opcode >> 3) & 7]}, {REGISTERS[opcode & 7]}")
        if opcode & 0xc7 == 0xc7:
            vector = opcode & 0x38
            return Instruction(opcode, 1, f"rst ${vector:02x}", vector)
        if opcode < 0x40 and opcode & 7 in (4, 5):
            return Instruction(opcode, 1, f"{'dec' if opcode & 1 else 'inc'} {REGISTERS[(opcode >> 3) & 7]}")
        if 0x80 <= opcode < 0xc0:
            return Instruction(opcode, 1, f"{ALU_NAMES[(opcode >> 3) & 7]} {REGISTERS[opcode & 7]}")
        size = 3 if opcode in WORD_OPERAND else 2
        if offset + size > limit:
            window = "RAM code section" if pc in self.ram_code else "ROM window"
            raise ValueError(f"instruction crosses a {window} boundary")
        operand = int.from_bytes(data[offset + 1:offset + size], "little")
        if opcode in ALU_IMMEDIATE:
            return Instruction(opcode, 2, f"{ALU_NAMES[(opcode >> 3) & 7]} ${operand:02x}", operand)
        if opcode in BYTE_OPERAND or opcode in WORD_OPERAND:
            template = (BYTE_OPERAND if size == 2 else WORD_OPERAND)[opcode]
            return Instruction(opcode, size, template.format(operand), operand)
        if opcode in RELATIVE:
            target = (pc + size + signed_byte(operand)) & 0xffff
            return Instruction(opcode, size, RELATIVE[opcode].format(target), target)
        if opcode in (0xe8, 0xf8):
            offset = signed_byte(operand)
            operation = "add sp, " if opcode == 0xe8 else "ld hl, sp"
            sign = "-" if offset < 0 else "+" if opcode == 0xf8 else ""
            return Instruction(opcode, size, f"{operation}{sign}${abs(offset):02x}", operand)
        if opcode == 0x10 and operand == 0:
            return Instruction(opcode, size, "stop")
        if opcode == 0xcb:
            register = REGISTERS[operand & 7]
            bit = (operand >> 3) & 7
            group = operand >> 6
            if group == 0:
                return Instruction(opcode, size, f"{SHIFTS[bit]} {register}", operand)
            operation = ("bit", "res", "set")[group - 1]
            return Instruction(opcode, size, f"{operation} {bit}, {register}", operand)
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
        if address is not None and not is_stack_address(address):
            return stack
        if address is None or sp is None:
            return (None,) * len(stack)
        canonical = ram_address(address)
        offset = (canonical - sp) & 0xffff
        if offset >= len(stack) and 0xc000 <= canonical < 0xde00:
            offset = (canonical + 0x2000 - sp) & 0xffff
        if offset >= len(stack):
            return stack
        index = len(stack) - 1 - offset
        return (*stack[:index], value, *stack[index + 1:])

    @staticmethod
    def stack_after_bank_write(stack, sp):
        if not stack or sp is None:
            return (None,) * len(stack)
        return tuple(
            None if 0xd000 <= ram_address((sp + offset) & 0xffff) < 0xe000 else value
            for offset, value in reversed(tuple(enumerate(reversed(stack))))
        )

    @staticmethod
    def push_memory(memory, sp, word, svbk):
        if sp is None:
            return KnownMemory(run for run in memory if SRAM_BASE <= run[0] < WRAM_BANKS_BASE)
        low, high = word
        memory = Cartridge.remember_ram(memory, (sp - 1) & 0xffff, high, svbk)
        return Cartridge.remember_ram(memory, (sp - 2) & 0xffff, low, svbk)

    @staticmethod
    def wram_bank_address(bank, address):
        return WRAM_BANKS_BASE + ((bank or 1) - 1) * 0x1000 + (address & 0x0fff)

    @staticmethod
    def remember_ram(memory, address, value, svbk):
        address = ram_address(address)
        if 0xd000 <= address < 0xe000:
            if svbk is None:
                return KnownMemory(run for run in memory if run[0] < WRAM_BANKS_BASE)
            address = Cartridge.wram_bank_address(svbk, address)
        return remember_byte(memory, address, value)

    def stack_byte(self, state, offset):
        if offset < len(state.stack):
            value = state.stack[-1 - offset]
            if isinstance(value, SavedFlags):
                return None
            if value is not None:
                return value
        if state.sp is None:
            return None
        address = (state.sp + offset) & 0xffff
        if not is_stack_address(address):
            return None
        address = ram_address(address)
        if 0xd000 <= address < 0xe000:
            if state.svbk is None:
                return None
            address = self.wram_bank_address(state.svbk, address)
        return known_byte(state.memory, address)

    def load_byte(self, state, address, rom_bytes):
        if address is None:
            return None
        if address < 0x8000:
            bank = 0 if address < BANK_SIZE else state.bank
            rom_bytes.add((bank, address))
            return self.data[bank * BANK_SIZE + address % BANK_SIZE]
        if address == 0xff00:
            if state.joyp is None or (state.joypad_delay == 0 and state.joyp != 0x30):
                return None
            return state.joyp | 0xcf
        if SOUND_BASE <= address <= SOUND_POWER:
            index = address - SOUND_BASE
            mask = SOUND_READ_MASK[index]
            value = state.sound[index]
            return mask if mask == 0xff else None if value is None else value | mask
        if VRAM_BASE <= address < VRAM_BASE + VRAM_SIZE:
            if state.hdma_async:
                return None
            offset = VRAM_BANKS_BASE + address - VRAM_BASE
            if state.vbk is not None:
                return known_byte(state.vram, offset + state.vbk * VRAM_SIZE)
            first = known_byte(state.vram, offset)
            return first if first == known_byte(state.vram, offset + VRAM_SIZE) else None
        if address == 0xff4d:
            if not self.cgb:
                return 0xff
            return None if state.key1 is None else state.key1 | 0x7e
        if address == 0xff40:
            return state.lcdc
        if address == 0xff44:
            return state.ly
        if address == 0xff4f:
            return 0xff if not self.cgb else None if state.vbk is None else state.vbk | 0xfe
        if address == 0xff70:
            return 0xff if not self.cgb else None if state.svbk is None else state.svbk | 0xf8
        if 0xa000 <= address < 0xc000:
            if not self.sram_size or state.sram_enabled is False:
                return 0xff
            if state.sram_enabled is None or state.sram_bank is None:
                return None
            value = known_byte(state.memory, self.sram_address(state.sram_bank, address))
            if value == UNKNOWN_SRAM_BYTE:
                return None
            return 0xff if value is None and state.sram_erased else value
        address = ram_address(address)
        if 0xd000 <= address < 0xe000:
            if state.svbk is None:
                return None
            address = self.wram_bank_address(state.svbk, address)
        return known_byte(state.memory, address)

    def read_byte(self, state, address, rom_bytes):
        """Read a byte and account for LY polling in the untimed model."""
        value = self.load_byte(state, address, rom_bytes)
        if address is None:
            return value, None
        if address != 0xff44:
            return value, state.ly
        if state.lcdc is None or state.ly is None:
            return value, None
        return value, (state.ly + 1) % 154 if state.lcdc & 0x80 else state.ly

    def sram_address(self, bank, address):
        return SRAM_BASE + (((bank << 13) | (address & 0x1fff)) & (self.sram_size - 1))

    @staticmethod
    def retain_dma_source(bank, source, command, rom_bytes):
        if None in source or command is None:
            return (None, None)
        start = (source[0] & 0xf0) | (source[1] << 8)
        length = ((command & 0x7f) + 1) * 16
        end = start + length
        if end <= 0x8000:
            rom_bytes.update((0 if address < BANK_SIZE else bank, address) for address in range(start, end))
        elif not (0xa000 <= start < end <= 0xe000):
            return (None, None)
        return (None, None) if command & 0x80 else word_bytes(end)

    def apply_vram_dma(self, state, command, rom_bytes, timing):
        source_after = self.retain_dma_source(state.bank, state.hdma_source, command, rom_bytes)
        if state.hdma_async or command is None or command & 0x80:
            async_transfer = timing and (command is None or bool(command & 0x80))
            return EMPTY_MEMORY, (None, None), (None, None), async_transfer

        vram = state.vram
        banks = (0, 1) if state.vbk is None else (state.vbk,)
        length = (command + 1) * 16
        dest_low, dest_high = state.hdma_destination
        destination = None if None in state.hdma_destination else (dest_low & 0xf0) | ((dest_high & 0x1f) << 8)
        if destination is None or destination + length > VRAM_SIZE:
            for bank in banks:
                start = VRAM_BANKS_BASE + bank * VRAM_SIZE
                vram = replace_range(vram, start, start + VRAM_SIZE)
            return vram, source_after, (None, None), False

        values = ()
        if state.vbk is not None and None not in source_after:
            source = join_word(*source_after) - length
            values = (self.load_byte(state, source + offset, rom_bytes) for offset in range(length))
        for bank in banks:
            start = VRAM_BANKS_BASE + bank * VRAM_SIZE + destination
            vram = replace_range(vram, start, start + length, values)
        return vram, source_after, word_bytes((destination + length) % VRAM_SIZE), False

    def successors(self, state, instruction, rom_bytes, *, timing=False):
        opcode, value = instruction.opcode, instruction.operand
        next_pc = state.pc + instruction.size
        next_state = replace(state, pc=next_pc)
        a, hl, stack, sp = state.a, state.hl, state.stack, state.sp
        b, c, z, carry = state.b, state.c, state.z, state.carry
        de, memory, key1, lcdc = state.de, state.memory, state.key1, state.lcdc
        joyp, ly = state.joyp, state.ly
        sound = state.sound
        hdma_source, svbk = state.hdma_source, state.svbk
        hdma_destination, hdma_async = state.hdma_destination, state.hdma_async
        vram, vbk = state.vram, state.vbk
        sram_enabled, sram_bank, sram_erased = state.sram_enabled, state.sram_bank, state.sram_erased
        hl_address = join_word(*hl)
        de_address = join_word(*de)

        if opcode == 0x08:
            states = (state,)
            for offset, byte in enumerate(word_bytes(sp)):
                write = Instruction(0xea, instruction.size, "", (value + offset) & 0xffff)
                states = tuple(successor for current in states
                               for successor in self.successors(replace(current, pc=state.pc, a=byte), write,
                                                                rom_bytes, timing=timing))
            yield from (replace(current, a=a) for current in states)
            return
        if opcode == 0x27:
            # H and N are runtime flags and are not tracked by this analysis.
            yield replace(next_state, a=None, z=None, carry=None)
            return

        if opcode == 0x76:
            if state.preview_remaining and not state.preview_interrupt:
                word = word_bytes(next_pc)
                delay = None if state.joypad_delay is None else max(0, state.joypad_delay - 1)
                yield replace(next_state, pc=0x40, preview_interrupt=True, ly=144,
                              joypad_delay=delay,
                              stack=(*stack, *reversed(word)),
                              sp=None if sp is None else (sp - 2) & 0xffff,
                              memory=self.push_memory(memory, sp, word, svbk))
            elif timing:
                yield next_state
            return
        if opcode in RETURNS:
            preview_return = opcode == 0xd9 and state.preview_interrupt
            if preview_return:
                if state.preview_remaining == 1:
                    return
                next_state = replace(next_state, preview_remaining=state.preview_remaining - 1,
                                     preview_interrupt=False)
            unconditional = opcode in (0xc9, 0xd9)
            taken = True if unconditional else condition_value(state, opcode)
            target = join_word(self.stack_byte(state, 0), self.stack_byte(state, 1))
            if target is not None and taken is not False:
                yield replace(next_state, pc=target, stack=stack[:-2],
                              sp=None if sp is None else (sp + 2) & 0xffff)
            if taken is not True:
                yield next_state
            return
        if opcode == 0xe9:
            if hl_address is not None:
                yield replace(next_state, pc=hl_address)
            return
        if opcode in (0xcd, *CONDITIONAL_CALL) or opcode & 0xc7 == 0xc7:
            taken = condition_value(state, opcode) if opcode in CONDITIONAL_CALL else True
            word = word_bytes(next_pc & 0xffff)
            if taken is not False:
                yield replace(next_state, pc=value, stack=(*stack, *reversed(word)),
                              sp=None if sp is None else (sp - 2) & 0xffff,
                              memory=self.push_memory(memory, sp, word, svbk))
            if taken is not True:
                yield next_state
            return

        if opcode in IMMEDIATE_LOADS:
            if state.pc in self.ram_code:
                value = self.load_byte(state, state.pc + 1, rom_bytes)
            yield load_register(next_state, IMMEDIATE_LOADS[opcode], value)
            return
        if 0x40 <= opcode < 0x80 and opcode not in HL_STORES:
            source = REGISTERS[opcode & 7]
            if source == "[hl]":
                loaded, ly = self.read_byte(state, hl_address, rom_bytes)
                next_state = replace(next_state, ly=ly)
            else:
                loaded = register_byte(state, source)
            yield load_register(next_state, REGISTERS[(opcode >> 3) & 7], loaded)
            return
        if 0x80 <= opcode < 0xc0 or opcode in ALU_IMMEDIATE:
            source = REGISTERS[opcode & 7] if 0x80 <= opcode < 0xc0 else None
            if source == "[hl]":
                operand, ly = self.read_byte(state, hl_address, rom_bytes)
            else:
                operand = value if source is None else register_byte(state, source)
            a, z, carry = abstract_alu((opcode >> 3) & 7, a, operand, carry, source == "a")
            yield replace(next_state, a=a, z=z, carry=carry, ly=ly)
            return
        byte_step = opcode < 0x40 and opcode & 7 in (4, 5)
        step_register = REGISTERS[(opcode >> 3) & 7] if byte_step else None
        if byte_step and step_register != "[hl]":
            original = register_byte(state, step_register)
            result = None if original is None else (original + (-1 if opcode & 1 else 1)) & 0xff
            yield load_register(replace(next_state, z=None if result is None else result == 0), step_register, result)
            return
        if opcode in (0x18, 0xc3):
            yield replace(next_state, pc=value)
            return
        if opcode in (*CONDITIONAL_JR, *CONDITIONAL_JP):
            taken = condition_value(state, opcode)
            if taken is not False:
                yield replace(next_state, pc=value)
            if taken is not True:
                yield next_state
            return

        cb_store = False
        cb_result = None
        if opcode == 0xcb:
            register = REGISTERS[value & 7]
            if register == "[hl]":
                original, ly = self.read_byte(state, hl_address, rom_bytes)
            else:
                original = register_byte(state, register)
            bit = (value >> 3) & 7
            mask = 1 << bit
            group = value >> 6
            if group == 1:
                yield replace(next_state, z=None if original is None else not bool(original & mask), ly=ly)
                return
            if group == 0:
                cb_result, carry = abstract_shift(bit, original, carry)
                z = None if cb_result is None else cb_result == 0
            elif original is not None:
                cb_result = (original & ~mask) if group == 2 else (original | mask)
            if register != "[hl]":
                yield load_register(replace(next_state, z=z, carry=carry), register, cb_result)
                return
            cb_store = True

        if opcode == 0xf0:
            a, ly = self.read_byte(state, 0xff00 | value, rom_bytes)
        elif opcode == 0xf2:
            a, ly = self.read_byte(state, None if c is None else 0xff00 | c, rom_bytes)
        elif opcode in (0x0a, 0x1a):
            a, ly = self.read_byte(state, join_word(c, b) if opcode == 0x0a else de_address, rom_bytes)
        elif opcode == 0xfa:
            a, ly = self.read_byte(state, value, rom_bytes)
        elif opcode in (0x2a, 0x3a):
            a, ly = self.read_byte(state, hl_address, rom_bytes)
            step = 1 if opcode == 0x2a else -1
            hl = word_bytes(None if hl_address is None else (hl_address + step) & 0xffff)
        elif opcode in (0x07, 0x0f, 0x17, 0x1f):
            prior_carry = carry
            left = opcode in (0x07, 0x17)
            carry = None if a is None else bool(a & (0x80 if left else 1))
            incoming = carry if opcode in (0x07, 0x0f) else prior_carry
            if a is None or incoming is None:
                a = None
            elif left:
                a = ((a << 1) | incoming) & 0xff
            else:
                a = (a >> 1) | (int(incoming) << 7)
            z = False
        elif opcode == 0x2f and a is not None:
            a ^= 0xff
        elif opcode == 0x37:
            carry = True
        elif opcode == 0x3f:
            carry = None if carry is None else not carry

        if opcode == 0x01:
            c, b = word_bytes(value)
        elif opcode in (0x03, 0x0b):
            bc = join_word(c, b)
            c, b = word_bytes(None if bc is None else (bc + (1 if opcode == 0x03 else -1)) & 0xffff)

        banks = (state.bank,)
        if opcode in (*MEMORY_STORES, 0xe0, 0xe2) or cb_store or byte_step:
            if opcode == 0xea:
                address = value
            elif opcode == 0xe0:
                address = 0xff00 | value
            elif opcode == 0xe2:
                address = None if c is None else 0xff00 | c
            elif opcode == 0x02:
                address = join_word(c, b)
            elif opcode == 0x12:
                address = de_address
            else:
                address = hl_address
            stored = register_byte(state, REGISTERS[opcode & 7]) if 0x70 <= opcode < 0x78 else a
            if opcode == 0x36:
                stored = value
            if byte_step:
                original, ly = self.read_byte(state, address, rom_bytes)
                stored = None if original is None else (original + (-1 if opcode & 1 else 1)) & 0xff
                z = None if stored is None else stored == 0
            if cb_store:
                stored = cb_result
            high_only = opcode in (0xe0, 0xe2)
            if not high_only:
                banks = self.banks_after_store(state.bank, address, stored)
            if address is None and high_only:
                memory = KnownMemory(run for run in memory if not 0xff80 <= run[0] < 0xffff)
            elif address is None:
                memory = EMPTY_MEMORY
                sram_enabled, sram_bank, sram_erased = None, None, False
            else:
                memory = self.remember_ram(memory, address, stored, svbk)
                if VRAM_BASE <= address < VRAM_BASE + VRAM_SIZE and not hdma_async:
                    offset = VRAM_BANKS_BASE + address - VRAM_BASE
                    if vbk is None:
                        vram = remember_byte(vram, offset, None)
                        vram = remember_byte(vram, offset + VRAM_SIZE, None)
                    else:
                        vram = remember_byte(vram, offset + vbk * VRAM_SIZE, stored)
                if self.sram_size:
                    if address < 0x2000:
                        sram_enabled = None if stored is None else (stored & 0x0f) == 0x0a
                    elif 0x4000 <= address < 0x6000:
                        sram_bank = None if stored is None else stored & 0x0f
                    elif 0xa000 <= address < 0xc000 and sram_enabled is not False:
                        if sram_bank is None:
                            memory = KnownMemory(run for run in memory if not SRAM_BASE <= run[0] < WRAM_BANKS_BASE)
                            sram_erased = False
                        else:
                            sram_byte = stored if sram_enabled and stored is not None else UNKNOWN_SRAM_BYTE
                            memory = remember_byte(memory, self.sram_address(sram_bank, address), sram_byte)
            if address is not None or not high_only:
                stack = self.stack_after_store(stack, sp, address, stored)
            elif sp is None or any(0xff80 <= ((sp + i) & 0xffff) < 0xffff
                                   for i in range(len(stack))):
                stack = (None,) * len(stack)
            if address is None or address == 0xff40:
                lcdc = stored if address is not None else None
                if lcdc is None:
                    ly = None
                elif not timing and not lcdc & 0x80:
                    ly = 0
            if address is None or address == 0xff00:
                joyp = None if address is None or stored is None else stored & 0x30
            if address is None:
                sound = UNKNOWN_SOUND
            elif SOUND_BASE <= address <= SOUND_POWER:
                sound = sound_after_store(sound, address, stored)
            if address is None and (self.cgb or not high_only):
                vram = EMPTY_MEMORY
            if address == 0xff46 and stored is not None and stored < 0x80:
                start = stored << 8
                bank = 0 if start < BANK_SIZE else state.bank
                rom_bytes.update((bank, source) for source in range(start, start + 160))
            if self.cgb:
                if address in (None, 0xff70):
                    stack = self.stack_after_bank_write(stack, sp)
                    svbk = None if address is None or stored is None else stored & 7
                if address is None:
                    key1 = None
                    hdma_source = (None, None)
                    hdma_destination = (None, None)
                    hdma_async = timing
                    vbk = None
                elif address == 0xff4d and key1 is not None:
                    key1 = None if stored is None else (key1 & 0x80) | (stored & 1)
                elif address == 0xff4f:
                    vbk = None if stored is None else stored & 1
                elif address == 0xff51:
                    hdma_source = (hdma_source[0], stored)
                elif address == 0xff52:
                    hdma_source = (stored, hdma_source[1])
                elif address == 0xff53:
                    hdma_destination = (hdma_destination[0], stored)
                elif address == 0xff54:
                    hdma_destination = (stored, hdma_destination[1])
                elif address == 0xff55:
                    vram, hdma_source, hdma_destination, hdma_async = self.apply_vram_dma(
                        state, stored, rom_bytes, timing
                    )
            if opcode in (0x22, 0x32):
                step = 1 if opcode == 0x22 else -1
                hl = word_bytes(None if hl_address is None else (hl_address + step) & 0xffff)
        elif opcode == 0x21:
            hl = word_bytes(value)
        elif opcode == 0x11:
            de = word_bytes(value)
        elif opcode in (0x09, 0x19, 0x29, 0x39):
            operand = {0x09: join_word(c, b), 0x19: de_address, 0x29: hl_address, 0x39: sp}[opcode]
            total = None if hl_address is None or operand is None else hl_address + operand
            carry = None if total is None else total > 0xffff
            hl = word_bytes(None if total is None else total & 0xffff)
        elif opcode in (0x13, 0x1b):
            de = word_bytes(None if de_address is None else (de_address + (1 if opcode == 0x13 else -1)) & 0xffff)
        elif opcode in (0x23, 0x2b):
            hl = word_bytes(None if hl_address is None else (hl_address + (1 if opcode == 0x23 else -1)) & 0xffff)
        elif opcode in (0xe8, 0xf8):
            offset = signed_byte(value)
            new_sp = None if sp is None else (sp + offset) & 0xffff
            if opcode == 0xf8:
                hl = word_bytes(new_sp)
            elif offset > 0:
                stack = stack[:-offset]
            elif offset < 0:
                added = (None if sp is None else known_byte(memory, (sp - i) & 0xffff)
                         for i in range(1, 1 - offset))
                stack = (*stack, *added)
            z = False
            carry = None if sp is None else (sp & 0xff) + value > 0xff
            if opcode == 0xe8:
                sp = new_sp
        elif opcode == 0x33:
            stack = stack[:-1]
            sp = None if sp is None else (sp + 1) & 0xffff
        elif opcode == 0x3b:
            stack = (*stack, None if sp is None else known_byte(memory, (sp - 1) & 0xffff))
            sp = None if sp is None else (sp - 1) & 0xffff
        elif opcode == 0x10 and key1 is not None and key1 & 1:
            key1 = (key1 ^ 0x80) & 0x80
        elif opcode in (0x31, 0xf9):
            stack, sp = (), value if opcode == 0x31 else hl_address
        elif opcode in PUSHES:
            word = {0xc5: (c, b), 0xd5: de, 0xe5: hl, 0xf5: (None, a)}[opcode]
            low, high = word
            stack = (*stack, high, SavedFlags(z, carry) if opcode == 0xf5 else low)
            memory = self.push_memory(memory, sp, word, svbk)
            sp = None if sp is None else (sp - 2) & 0xffff
        elif opcode in POPS:
            low, high = self.stack_byte(state, 0), self.stack_byte(state, 1)
            if opcode == 0xe1:
                hl = (low, high)
            elif opcode == 0xd1:
                de = (low, high)
            elif opcode == 0xf1:
                a = high
                saved = stack[-1] if stack else None
                if isinstance(saved, SavedFlags):
                    z, carry = saved.zero, saved.carry
                else:
                    z = None if low is None else bool(low & 0x80)
                    carry = None if low is None else bool(low & 0x10)
            else:
                c, b = low, high
            stack = stack[:-2]
            sp = None if sp is None else (sp + 2) & 0xffff
        for bank in sorted(banks):
            yield replace(next_state, bank=bank, a=a, hl=hl, stack=stack, sp=sp,
                          b=b, c=c, z=z, carry=carry, de=de, memory=memory, key1=key1, lcdc=lcdc,
                          joyp=joyp, ly=ly, sound=sound,
                          hdma_source=hdma_source, sram_enabled=sram_enabled, sram_bank=sram_bank,
                          sram_erased=sram_erased, svbk=svbk, vbk=vbk, vram=vram,
                          hdma_destination=hdma_destination, hdma_async=hdma_async)

    def discover(self, extra_entries=(), stop_at=None, rom_bytes=None, *, timing=False,
                 trap_unsupported=False, vblank_preview=False, dispatch_targets=None,
                 input_after=None, analysis_limit=DEFAULT_ANALYSIS_LIMIT, progress=None, merge_frames=False):
        if not isinstance(vblank_preview, int) or not 0 <= vblank_preview <= 0xffffff:
            raise ValueError("VBlank preview frame count must fit in an unsigned 24-bit integer")
        if timing and vblank_preview:
            raise ValueError("VBlank preview and timed execution are mutually exclusive")
        delay = None
        if input_after is not None:
            delay = input_after + 1 if vblank_preview else 0
        entry_a = 0x11 if self.cgb else 0x01
        work = deque([State(ENTRY, 1, entry_a, sp=0xfffe, key1=0 if self.cgb else None, lcdc=0x91,
                            sram_enabled=False, sram_bank=0, sram_erased=True, svbk=0,
                            vbk=0, hdma_source=(0, 0), hdma_destination=(0, 0),
                            joyp=0, ly=None if timing else 0, sound=INITIAL_SOUND,
                            preview_remaining=int(vblank_preview), joypad_delay=delay)])
        if rom_bytes is None:
            rom_bytes = set()
        if stop_at is not None:
            bank, pc = stop_at
            if pc in self.ram_code:
                if bank:
                    raise ValueError("RAM code checkpoints use bank 0")
            elif not 0 <= bank <= self.bank_mask or not 0 <= pc < 0x8000 or (pc < BANK_SIZE and bank):
                raise ValueError("checkpoint is outside cartridge ROM or registered RAM code")
        for bank, pc, *stack_pointer in extra_entries:
            if not 0 <= bank <= self.bank_mask or (not 0 <= pc < 0x8000 and pc not in self.ram_code):
                raise ValueError("extra entry is outside cartridge ROM or registered RAM code")
            if len(stack_pointer) > 1 or stack_pointer and not 0 <= stack_pointer[0] <= 0xffff:
                raise ValueError("extra entry stack pointer is outside the guest address space")
            entry = State(pc, bank, sp=stack_pointer[0] if stack_pointer else None,
                          vbk=None if self.cgb else 0, hdma_async=timing and self.cgb,
                          preview_remaining=int(vblank_preview),
                          joypad_delay=None if input_after is None else 0)
            if dispatch_targets is not None:
                dispatch_targets.add(address_key(entry))
            work.append(entry)
        seen = set()
        next_frame = deque()
        state_count = 0
        instructions = {}
        occupied = {}
        while work or next_frame:
            if not work:
                # Returning from VBlank can only decrease the remaining frame count.
                # Finish this frame before visiting the next so old states can be freed.
                work, next_frame = merge_frame_states(next_frame) if merge_frames else next_frame, work
                seen.clear()
            state = work.popleft()
            if state in seen:
                continue
            seen.add(state)
            state_count += 1
            if progress is not None and state_count % 100_000 == 0:
                progress(state_count, len(instructions), int(vblank_preview) - state.preview_remaining)
            bank, _ = address_key(state)
            location = f"${bank:03X}:${state.pc:04X}"
            try:
                if state_count > analysis_limit:
                    raise ValueError(f"static analysis exceeded {analysis_limit:,} states")
                if len(state.stack) > 512:
                    raise ValueError("static analysis exceeded its stack limit")
                if not 0 <= state.pc < 0x8000 and state.pc not in self.ram_code:
                    raise ValueError("control transfer outside cartridge ROM or registered RAM code")
                key = bank, state.pc
                if key == stop_at:
                    if occupied.setdefault(key, state.pc) != state.pc:
                        raise ValueError("checkpoint is inside an instruction")
                    instructions[key] = Instruction(CHECKPOINT, 0, "")
                    continue
                if key not in instructions:
                    try:
                        instruction = self.decode(bank, state.pc)
                        lower_instruction(instruction.text, state.pc)
                    except ValueError as error:
                        if not trap_unsupported or not str(error).startswith("unsupported "):
                            raise
                        instruction = Instruction(UNSUPPORTED, 1, "")
                    for pc in range(state.pc, state.pc + instruction.size):
                        owner = occupied.setdefault((bank, pc), state.pc)
                        if owner != state.pc:
                            raise ValueError(f"instruction overlaps code at ${owner:04X}")
                    instructions[key] = instruction
                instruction = instructions[key]
                opcode = instruction.opcode
                if opcode == UNSUPPORTED:
                    continue
                folded = None if timing else self.fold_fill(state, instruction, stop_at)
                if folded is not None:
                    successors, tail = folded
                    for tail_pc, tail_instruction in tail:
                        for byte_pc in range(tail_pc, tail_pc + tail_instruction.size):
                            owner = occupied.setdefault((bank, byte_pc), tail_pc)
                            if owner != tail_pc:
                                raise ValueError(f"instruction overlaps code at ${owner:04X}")
                        instructions[bank, tail_pc] = tail_instruction
                        if dispatch_targets is not None:
                            dispatch_targets.add((bank, tail_pc))
                    for successor in successors:
                        if dispatch_targets is not None:
                            dispatch_targets.add(address_key(successor))
                        work.append(successor)
                    continue
                successors = self.successors(state, instruction, rom_bytes, timing=timing)
                if dispatch_targets is not None:
                    if opcode == 0xe9 and join_word(*state.hl) is None:
                        dispatch_targets.add(None)
                    if opcode in RETURNS:
                        preview_ends = opcode == 0xd9 and state.preview_interrupt and state.preview_remaining == 1
                        can_return = opcode not in CONDITIONAL_RET or condition_value(state, opcode) is not False
                        if not preview_ends and can_return and join_word(
                                self.stack_byte(state, 0), self.stack_byte(state, 1)) is None:
                            dispatch_targets.add(None)
                transfer = opcode in CONTROL_TRANSFERS or opcode & 0xc7 == 0xc7
                for successor in successors:
                    if dispatch_targets is not None and (transfer or successor.bank != state.bank):
                        dispatch_targets.add(address_key(successor))
                    queue = next_frame if successor.preview_remaining < state.preview_remaining else work
                    queue.append(successor)
            except ValueError as error:
                raise ValueError(f"{location}: {error}") from error
        if stop_at is not None and stop_at not in instructions:
            raise ValueError(f"checkpoint ${stop_at[0]:03X}:${stop_at[1]:04X} is unreachable")
        return instructions

    def fold_fill(self, state, instruction, stop_at):
        """Summarize the same bounded fill loop that the native backend fuses."""
        pc = state.pc
        bank, _ = address_key(state)
        if instruction.opcode != 0x22 or pc in self.ram_code or pc // BANK_SIZE != (pc + 4) // BANK_SIZE:
            return None
        if stop_at in ((bank, pc + 1), (bank, pc + 2), (bank, pc + 3)):
            return None
        try:
            step, branch = self.decode(bank, pc + 1), self.decode(bank, pc + 2)
        except ValueError:
            return None
        if step.opcode not in (0x05, 0x0d) or branch.opcode != 0x20 or branch.operand != pc:
            return None
        use_b = step.opcode == 0x05
        count = state.b if use_b else state.c
        original = join_word(*state.hl)
        if original is None:
            return None
        if count is None:
            # An unknown byte counter still terminates within 256 iterations.
            # Preserve every possible final pointer/memory state rather than
            # following an abstract DEC(None) backedge indefinitely.
            successors = []
            for value in range(256):
                concrete = replace(state, **{"b" if use_b else "c": value})
                folded = self.fold_fill(concrete, instruction, stop_at)
                if folded is None:
                    return None
                successors.extend(folded[0])
            return tuple(successors), ((pc + 1, step), (pc + 2, branch))
        count = count or 256
        if 0xe000 <= original < 0xfe00 and count > 0xfe00 - original:
            return None
        address = ram_address(original)
        memory, vram = state.memory, state.vram
        if 0x8000 <= address and address + count <= 0xa000 and state.vbk is not None:
            start = VRAM_BANKS_BASE + state.vbk * VRAM_SIZE + address - 0x8000
            vram = replace_range(vram, start, start + count, (state.a,) * count)
        else:
            if 0xc000 <= address < 0xe000 and count <= 0x1000 - (address & 0xfff):
                if address >= 0xd000 and state.svbk is None:
                    return None
                start = self.wram_bank_address(state.svbk, address) if address >= 0xd000 else address
            elif 0xff80 <= address and address + count <= 0xffff:
                start = address
            else:
                return None
            if state.stack and (state.sp is None or any(
                    address <= ram_address((state.sp + i) & 0xffff) < address + count
                    for i in range(len(state.stack)))):
                return None
            memory = replace_range(memory, start, start + count, (state.a,) * count)
        successor = replace(state, pc=pc + 4, hl=word_bytes((original + count) & 0xffff),
                            b=0 if use_b else state.b, c=state.c if use_b else 0,
                            z=True, memory=memory, vram=vram)
        return (successor,), ((pc + 1, step), (pc + 2, branch))


def symbol(bank, pc):
    return f"gb_{bank:03x}_{pc:04x}"


def native_destination(bank, pc, address, instructions, native_overlays=False):
    if BANK_SIZE <= address < 2 * BANK_SIZE:
        if not BANK_SIZE <= pc < 2 * BANK_SIZE:
            return None
        key = bank, address
    else:
        key = 0, address
    if key not in instructions:
        return None
    if native_overlays:
        region = region_key(*key)
        if region is not None and region != region_key(bank, pc):
            return None
    return key


def direct_branch(bank, pc, instruction, instructions, native_overlays=False, adjacent=True):
    opcode = instruction.opcode
    if opcode not in (0x18, 0xc3, *CONDITIONAL_JR, *CONDITIONAL_JP):
        return None

    taken = native_destination(bank, pc, instruction.operand, instructions, native_overlays)
    if taken is None:
        return None
    if opcode in (0x18, 0xc3):
        return [f"jp _{symbol(*taken)}"]

    next_pc = pc + instruction.size
    fallthrough = native_destination(bank, pc, next_pc, instructions, native_overlays)
    if fallthrough is None:
        return None
    flag = 4 if opcode & 0x10 else 7
    condition = "nz" if opcode & 8 else "z"
    native = ["ld a, (_gb_f)", f"bit {flag}, a", f"jp {condition}, _{symbol(*taken)}"]
    if not adjacent or fallthrough != (bank, next_pc) or pc // BANK_SIZE != next_pc // BANK_SIZE:
        native.append(f"jp _{symbol(*fallthrough)}")
    return native


def checked_helper_stubs(helpers):
    assembly = []
    for helper, argument in sorted(helpers):
        assembly.extend(["", f"_gb_at_pc_{helper}:"])
        if argument:
            assembly.extend(["    ld (_gb_pc), de", "    push hl"])
        else:
            assembly.append("    ld (_gb_pc), hl")
        fast = "fast_" if helper in ("load_hli", "load_hld", "store_hl", "store_hli", "store_hld") else ""
        assembly.append(f"    call _gb_{fast}{helper}")
        if argument:
            assembly.append("    pop de")
        assembly.append("    jp .Lchecked_return")
    if helpers:
        # A fault returns from the guest, so discard the helper's native return address.
        assembly.extend(["", ".Lchecked_return:", "    or a, a", "    ret nz",
                         "    pop de", "    jp _gb_unmapped"])
    return assembly


OUTLINE_FAULT_BRANCHES = {f"jp {condition}, _gb_unmapped" for condition in ("z", "nz", "c", "nc")}


def outline_repeated(assembly, repeated, section_local=False):
    """Share repeated native instruction bodies that return to their caller."""
    replacements = {}
    needs_fault_exit = False
    owners = []
    section = ".text"
    for line in assembly:
        if line.strip().startswith(".section "):
            section = line.strip().split()[1].split(",")[0]
        owners.append(section)
    for body, offsets in repeated.items():
        offsets = [offset for offset in offsets
                   if all(assembly[offset + i] == f"    {line}" for i, line in enumerate(body))]
        if not offsets:
            continue
        # Absolute guest-state loads/stores and ADL calls take at least four bytes.
        minimum = sum(4 if "_gb_" in line or line.startswith("call ") else 1
                      for line in body if not line.endswith(":"))
        groups = {}
        for offset in offsets:
            groups.setdefault(owners[offset] if section_local else None, []).append(offset)
        for owner, locations in groups.items():
            if (minimum - 4) * len(locations) <= minimum + 1:
                continue
            label = f".Lshared_{locations[0]}"
            for offset in locations:
                replacements[offset] = (f"    call {label}", len(body))
            if owner is not None:
                assembly.append(f'    .section {owner},"ax",@progbits')
            assembly.extend(["", f"{label}:"])
            for line in body:
                if line in OUTLINE_FAULT_BRANCHES:
                    line = line.replace("_gb_unmapped", ".Lshared_fault")
                    needs_fault_exit = True
                assembly.append(f"    {line}")
            assembly.append("    ret")
    if needs_fault_exit:
        if section_local:
            assembly.append("    .section .text")
        assembly.extend(["", ".Lshared_fault:", "    pop de", "    jp _gb_unmapped"])
    result = []
    index = 0
    while index < len(assembly):
        line, count = replacements.get(index, (assembly[index], 1))
        result.append(line)
        index += count
    return result


def fixed_memory_access(data, instruction):
    opcode, address = instruction.opcode, instruction.operand
    if opcode in (0xe0, 0xf0):
        address |= 0xff00
    elif opcode not in (0xea, 0xfa):
        return None

    load = opcode in (0xfa, 0xf0)
    if load and address < BANK_SIZE:
        source = f"0x{data[address]:02x}"
        return [f"ld a, {source}", "ld (_gb_a), a"]

    address = ram_address(address)
    if 0xc000 <= address < 0xd000:
        cell = f"_gb_wram+{address - 0xc000}"
    elif HRAM_BASE <= address < HRAM_END:
        cell = f"_gb_hram+{address - HRAM_BASE}"
    else:
        return None
    if load:
        return [f"ld a, ({cell})", "ld (_gb_a), a"]
    return ["ld a, (_gb_a)", f"ld ({cell}), a"]


def copy_loop(bank, pc, instructions, ram_code):
    """Recognize a counted HL-to-DE copy, including unrolled page copies."""
    if pc in ram_code:
        return None
    cursor = pc
    steps = []
    for _ in range(32):
        triple = tuple(instructions.get((bank, cursor + i)) for i in range(3))
        if any(instruction is None for instruction in triple):
            break
        load, store, increment = (instruction.opcode for instruction in triple)
        if (load, store) != (0x2a, 0x12) or increment not in (0x13, 0x1c):
            break
        steps.append(increment)
        cursor += 3
    if not steps or steps[-1] != 0x13:
        return None
    page_step = steps[0] == 0x1c
    if any(step != steps[0] for step in steps[:-1]):
        return None
    step = instructions.get((bank, cursor))
    branch = instructions.get((bank, cursor + 1))
    end = cursor + 3
    if (step is None or step.opcode not in (0x05, 0x0d) or branch is None
            or branch.opcode != 0x20 or branch.operand != pc
            or pc // BANK_SIZE != end // BANK_SIZE or (bank, end) not in instructions
            or any(address in ram_code for address in range(pc, end))):
        return None
    layout = len(steps) | (0x40 if page_step else 0) | (0x80 if step.opcode == 0x05 else 0)
    return layout, cursor + 1, end


def fill_loop(bank, pc, instructions, ram_code):
    """Match up to 32 consecutive stores inside the same counted fill loop."""
    cursor = pc
    for _ in range(32):
        store = instructions.get((bank, cursor))
        if store is None or store.opcode != 0x22:
            break
        cursor += 1
    step = instructions.get((bank, cursor))
    branch = instructions.get((bank, cursor + 1))
    end = cursor + 3
    if (cursor == pc or step is None or step.opcode not in (0x05, 0x0d)
            or branch is None or branch.opcode != 0x20 or branch.operand != pc
            or pc // BANK_SIZE != end // BANK_SIZE or (bank, end) not in instructions
            or any(address in ram_code for address in range(pc, end))):
        return None
    return ((cursor - pc - 1) << 1) | int(step.opcode == 0x05), cursor + 1, end


def counter_wait(bank, pc, instruction, instructions, cartridge):
    """Finish an untimed INC/DEC byte, JR NZ spin loop, including copied HRAM."""
    opcode = instruction.opcode
    if opcode < 0 or opcode >= 0x40 or opcode & 7 not in (4, 5) or opcode in (0x34, 0x35):
        return []
    branch = instructions.get((bank, pc + 1))
    end = pc + 3
    if (branch is None or branch.opcode != 0x20 or branch.operand != pc
            or pc // BANK_SIZE != end // BANK_SIZE or (bank, end) not in instructions):
        return []
    lines = []
    slow = f".Lcounter_slow_{bank:03x}_{pc:04x}"
    hram = [address in cartridge.ram_code for address in range(pc, end)]
    if any(hram):
        if not all(hram):
            return []
        for address in range(pc, end):
            image, offset, _ = cartridge.instruction_bytes(bank, address)
            lines.extend([f"    ld a, (_gb_hram+{address - HRAM_BASE})",
                          f"    cp a, 0x{image[offset]:02x}", f"    jr nz, {slow}"])
    flags = 0xc0 if opcode & 1 else 0xa0
    register = BYTE_REGISTERS[REGISTERS[opcode >> 3]]
    lines.extend(["    ld a, (_gb_f)", "    and a, 0x10", f"    or a, 0x{flags:02x}",
                  "    ld (_gb_f), a", "    xor a, a", f"    ld ({register}), a",
                  f"    jp _{symbol(bank, end)}"])
    if any(hram):
        lines.append(slow + ":")
    return lines


def lcd_poll_rule(bank, pc, instructions, ram_code):
    """Encode a side-effect-free LY/STAT poll followed by a branch back."""
    if pc // BANK_SIZE != (pc + 5) // BANK_SIZE or any(pc + i in ram_code for i in range(6)):
        return None
    read, test, branch = (instructions.get((bank, pc + i)) for i in (0, 2, 4))
    if (read is None or read.opcode != 0xf0 or test is None or branch is None
            or branch.opcode not in CONDITIONAL_JR or branch.operand != pc):
        return None
    condition = (branch.opcode - 0x20) >> 3
    if read.operand == 0x44 and test.opcode == 0xfe:
        return (condition << 8) | test.operand
    if read.operand == 0x41 and test.opcode == 0xe6 and condition < 2:
        return 0x400 | (condition << 8) | test.operand
    if (read.operand == 0x41 and test.opcode == 0xcb and test.operand & 0xc7 == 0x47
            and condition < 2):
        return 0x800 | (condition << 8) | (1 << ((test.operand >> 3) & 7))
    return None


def compile_rom(data, extra_entries=(), watch_address=0, stop_at=None, *, timing=False,
                trap_unsupported=False, ram_code=(), vblank_preview=False, input_after=None,
                analysis_limit=DEFAULT_ANALYSIS_LIMIT, continuous=False, progress=None, rom_data=(),
                predict_returns=True, offload_banks=(), fallback=False, fallback_banks=(),
                native_overlays=False, branch_entries=(), inline_calls=None, callback_calls=None, entry_tables=()):
    if continuous and not vblank_preview:
        raise ValueError("continuous VBlank execution requires a positive analysis frame count")
    if watch_address and not (0x8000 <= watch_address < 0xa000 or
                              0xc000 <= watch_address < 0xe000 or
                              0xff80 <= watch_address <= 0xfffe):
        raise ValueError("watch address must be in VRAM, WRAM or HRAM")
    cartridge = Cartridge(data, ram_code)
    if native_overlays and (fallback or fallback_banks):
        raise ValueError("native overlays and interpreted fallback are separate build modes")
    fallback_banks = set(fallback_banks)
    if fallback_banks and (not fallback or any(not 0 <= bank <= cartridge.bank_mask for bank in fallback_banks)):
        raise ValueError("fallback banks require fallback execution and valid cartridge banks")
    rom_bytes = set()
    for bank, address, length in rom_data:
        if (not 0 <= bank <= cartridge.bank_mask or not 0 <= address < 2 * BANK_SIZE
                or (address < BANK_SIZE and bank) or length <= 0
                or address + length > (address // BANK_SIZE + 1) * BANK_SIZE):
            raise ValueError("ROM data range must fit in one cartridge ROM window")
        rom_bytes.update((bank, byte) for byte in range(address, address + length))
    dispatch_targets = set() if vblank_preview else None
    retained = rom_bytes.copy() if offload_banks else None
    instructions = cartridge.discover(extra_entries, stop_at, rom_bytes, timing=timing,
                                      trap_unsupported=trap_unsupported or fallback, vblank_preview=vblank_preview,
                                      dispatch_targets=dispatch_targets, input_after=input_after,
                                      analysis_limit=analysis_limit, progress=progress, merge_frames=continuous)
    if branch_entries or entry_tables:
        from control_flow import discover_branches, read_entry_tables
        branch_entries = (*branch_entries, *read_entry_tables(cartridge, entry_tables, rom_bytes))
        discover_branches(cartridge, branch_entries, instructions, rom_bytes, dispatch_targets,
                          inline_calls, callback_calls=callback_calls, entry_tables=entry_tables, stop_at=stop_at)
    if fallback_banks:
        instructions = {key: value for key, value in instructions.items() if key[0] not in fallback_banks}
    if retained is not None:
        retained.update(location for location in rom_bytes if location[0] not in offload_banks)
        rom_bytes = retained
    assembly = ["    .assume ADL=1", "    .section .text"]
    if (0, ENTRY) not in instructions:
        assembly.extend(["    .global _gb_000_0100", "_gb_000_0100:",
                         "    ld hl, 0x0100", "    jp _gb_dispatch"])
    targets = ['#include "runtime.h"', '#include "native_overlay.h"', ""]
    fallback_addresses = set()
    helpers = set()
    repeated = {}
    predicted_entries = set()
    predict_returns = predict_returns and bool(vblank_preview) and not timing
    ordered = sorted(instructions.items())
    return_addresses = {
        (bank, pc + instruction.size)
        for (bank, pc), instruction in ordered
        if instruction.opcode in (0xcd, *CONDITIONAL_CALL) or instruction.opcode & 0xc7 == 0xc7
    } if native_overlays else set()
    current_section = ".text"
    bulk_loops = {}
    bulk_backedges = {}
    if not timing:
        for (bank, pc), instruction in ordered:
            if instruction.opcode == 0x22:
                fill = fill_loop(bank, pc, instructions, cartridge.ram_code)
                if fill is not None and (not native_overlays or region_key(bank, pc) == region_key(bank, fill[2])):
                    layout, backedge, end = fill
                    bulk_loops[bank, pc] = "fill_hl", layout, end
                    bulk_backedges[bank, backedge] = pc
            if instruction.opcode == 0x2a:
                copy = copy_loop(bank, pc, instructions, cartridge.ram_code)
                if copy is not None and (not native_overlays or region_key(bank, pc) == region_key(bank, copy[2])):
                    layout, backedge, end = copy
                    bulk_loops[bank, pc] = "copy_hl_de", layout, end
                    bulk_backedges[bank, backedge] = pc
    for index, ((bank, pc), instruction) in enumerate(ordered):
        physical_next = ordered[index + 1][0] if index + 1 < len(ordered) else None
        if native_overlays:
            region = region_key(bank, pc)
            section = f".gb_native_{region:03x}" if region is not None else ".text"
            if section != current_section:
                assembly.append(f'    .section {section},"ax",@progbits')
                current_section = section
        name = symbol(bank, pc)
        targets.append(f"extern void {name}(void);")
        assembly.extend(["", f"    .global _{name}", f"_{name}:"])
        if (bank, pc) in bulk_loops:
            helper, argument, end = bulk_loops[bank, pc]
            assembly.extend([
                f"    ld hl, {argument}", "    push hl", f"    call _gb_{helper}",
                "    pop de", "    or a, a", f"    jp nz, _{symbol(bank, end)}",
                f".Lbulk_slow_{bank:03x}_{pc:04x}:",
            ])
        opcode = instruction.opcode
        in_ram = pc in cartridge.ram_code
        if not timing:
            wait = counter_wait(bank, pc, instruction, instructions, cartridge)
            if native_overlays and region_key(bank, pc) != region_key(bank, pc + 3):
                wait = []
            if wait:
                assembly.extend(wait)
                if not in_ram:
                    continue
        poll = lcd_poll_rule(bank, pc, instructions, cartridge.ram_code) if timing and opcode == 0xf0 else None
        if poll is not None:
            assembly.extend([f"    ld hl, 0x{poll:04x}", "    push hl",
                             "    call _gb_timing_skip_poll", "    pop de"])
        fixed = None if in_ram else fixed_memory_access(data, instruction)
        checked = CHECKED_HELPERS.get(opcode) if not timing and not in_ram and fixed is None else None
        cb_memory = opcode == 0xcb and instruction.operand & 7 == 6
        remaps = opcode in (*MEMORY_STORES, 0x34, 0x35) or (
            cb_memory and instruction.operand >> 6 != 1
        )
        if fixed is not None:
            remaps = False
        if timing and opcode not in (CHECKPOINT, UNSUPPORTED):
            boundary = "pure_boundary" if not in_ram and instruction.register_only() else "boundary"
            assembly.extend([f"    ld hl, 0x{pc:04x}", f"    ld de, {instruction.tcycles()}",
                             f"    call _gb_{boundary}"])
        elif fixed is None and checked is None and (in_ram or remaps or cb_memory or opcode & 0xc7 == 0xc7 or opcode in (
                CHECKPOINT, UNSUPPORTED, 0x10, 0x76, 0xcd, 0xe0, 0xe2, *MEMORY_LOADS, *PUSHES, *POPS,
                *RETURNS, *CONDITIONAL_CALL) or 0x80 <= opcode < 0xc0 and opcode & 7 == 6):
            assembly.extend([f"    ld hl, 0x{pc:04x}", "    ld (_gb_pc), hl"])
        if opcode == CHECKPOINT:
            assembly.append("    jp _gb_checkpoint")
            continue
        if opcode == UNSUPPORTED:
            assembly.append("    jp _gb_fallback_dispatch" if fallback else "    jp _gb_unmapped")
            continue
        live_immediate = in_ram and opcode in IMMEDIATE_LOADS
        if in_ram:
            image, offset, _ = cartridge.instruction_bytes(bank, pc)
            guard_size = 1 if live_immediate else instruction.size
            for index, byte in enumerate(image[offset:offset + guard_size]):
                mismatch = "_gb_fallback_dispatch" if fallback else "_gb_unmapped"
                assembly.extend([f"    ld a, (_gb_hram+{pc - HRAM_BASE + index})",
                                 f"    cp a, 0x{byte:02x}", f"    jp nz, {mismatch}"])
        adjacent = physical_next == (bank, pc + instruction.size)
        native = None if timing else direct_branch(bank, pc, instruction, instructions, native_overlays, adjacent)
        if native is not None and (bank, pc) in bulk_backedges:
            start = bulk_backedges[bank, pc]
            native = [line.replace(f"_{symbol(bank, start)}", f".Lbulk_slow_{bank:03x}_{start:04x}")
                      for line in native]
        if native is not None:
            assembly.extend(f"    {line}" for line in native)
            continue
        if fixed is not None:
            native, size = fixed, instruction.size
        elif checked:
            helper, argument = checked
            helpers.add(checked)
            native = [f"ld hl, 0x{instruction.operand:04x}", f"ld de, 0x{pc:04x}"] if argument else [
                f"ld hl, 0x{pc:04x}"
            ]
            native.append(f"call _gb_at_pc_{helper}")
            size = instruction.size
        else:
            callee = None
            continuation = None
            if opcode in (0xcd, *CONDITIONAL_CALL) or opcode & 0xc7 == 0xc7:
                target = native_destination(bank, pc, instruction.operand, instructions, native_overlays)
                if target is not None:
                    callee = f"_{symbol(*target)}"
                if predict_returns and not in_ram:
                    resume = native_destination(bank, pc, pc + instruction.size, instructions, native_overlays)
                    if resume is not None and physical_next == resume:
                        continuation = f"_{symbol(*resume)}"
                        predicted_entries.add(continuation)
            native, size = lower_instruction(instruction.text, pc, timing=timing,
                                             live_immediate=live_immediate, vblank_preview=vblank_preview,
                                             native_target=callee, native_resume=continuation,
                                             predict_returns=predict_returns)
        # The shared fault exit unwinds its own return address before stopping the guest.
        if (vblank_preview and size is not None and not checked and not in_ram
                and not any(line.startswith("call _gb_call_predict") for line in native) and len(native) > 1
                and all(line in OUTLINE_FAULT_BRANCHES or not line.startswith(("jp ", "ret", "djnz "))
                        for line in native)):
            repeated.setdefault(tuple(native), []).append(len(assembly))
        assembly.extend(f"    {line}" for line in native)
        if size is not None:
            next_pc = pc + size
            # Adjacent blocks fall through. A bank change needs a lookup.
            if ((bank, next_pc) != physical_next or pc // BANK_SIZE != next_pc // BANK_SIZE
                    or (native_overlays and region_key(bank, pc) != region_key(bank, next_pc))
                    or (BANK_SIZE <= pc < 0x8000 and remaps)):
                fallback_addresses.add(next_pc)
                assembly.extend(f"    {line}" for line in dispatch(next_pc))
    if native_overlays:
        assembly.append("    .section .text")
    assembly.extend(checked_helper_stubs(helpers))
    entries = []
    entry_labels = []
    target_pages = []
    overlay_entries = {}
    for (bank, pc), instruction in ordered:
        region = region_key(bank, pc) if native_overlays else None
        if (dispatch_targets is not None and None not in dispatch_targets
                and (bank, pc) not in dispatch_targets and pc not in fallback_addresses
                and (bank, pc) not in return_addresses
                and (region is None or region in overlay_entries)
                and (bank, pc) != (0, ENTRY) and instruction.opcode != CHECKPOINT):
            continue
        entry_labels.append(f"_{symbol(bank, pc)}")
        if region is not None:
            overlay_entries.setdefault(region, []).append((pc, symbol(bank, pc)))
            continue
        page = (bank << 8) | (pc >> 8)
        if not target_pages or target_pages[-1][0] != page:
            if len(entries) > 0xffff:
                raise ValueError("native target table exceeds the calculator address index")
            target_pages.append([page, len(entries), 0])
        target_pages[-1][2] += 1
        entries.append(f"    {{0x{pc & 255:02x}, {symbol(bank, pc)}}},")
    if vblank_preview and not timing:
        external_entries = predicted_entries.union(entry_labels)
        forward_native_flags(assembly, native_entries(assembly, external_entries))
        omit_dead_flag_updates(assembly)
        reuse_native_registers(assembly, external_entries)
    if repeated:
        assembly = outline_repeated(assembly, repeated, section_local=native_overlays)
    spans = []
    payload = bytearray()
    for bank, address in sorted(rom_bytes):
        offset = bank * BANK_SIZE + address % BANK_SIZE
        if spans and spans[-1][0] == bank and spans[-1][1] // BANK_SIZE == address // BANK_SIZE:
            span = spans[-1]
            gap = address - (span[1] + span[2])
            # A span record takes nine bytes, so small gaps cost less as ROM data.
            if gap < 9:
                span[2] += gap + 1
                payload.extend(data[offset - gap:offset + 1])
                continue
        spans.append([bank, address, 1, len(payload)])
        payload.append(data[offset])
    data_entries = [
        f"    {{0x{bank:03x}, 0x{address:04x}, 0x{length:04x}, 0x{offset:06x}}},"
        for bank, address, length, offset in spans
    ]
    payload_lines = [
        "    " + ", ".join(f"0x{byte:02x}" for byte in payload[index:index + 16]) + ","
        for index in range(0, len(payload), 16)
    ]
    targets.extend([
        "", "const GbTarget gb_targets[] = {", *entries, "};",
        "const GbTargetPage gb_target_pages[] = {",
        *(f"    {{0x{page:05x}, {first}, {count - 1}}}," for page, first, count in target_pages),
        "};",
        f"const unsigned gb_target_page_count = {len(target_pages)};",
        f"const uint16_t rom_bank_mask = {cartridge.bank_mask};",
        f"const uint16_t rom_watch_address = 0x{watch_address:04x};",
        f"const uint8_t rom_mbc5 = {int(cartridge.mbc5)};",
        f"const uint8_t rom_cgb = {int(cartridge.cgb)};",
        f"const uint8_t rom_timing = {int(timing)};",
        f"const uint8_t rom_preview_mode = {int(bool(vblank_preview))};",
        f"const unsigned rom_preview_frames = {0 if continuous else int(vblank_preview)};",
        f"const uint8_t rom_ram_size_code = {data[0x149]};",
        "", "const GbRomSpan gb_rom_spans[] = {", *(data_entries or ["    {0, 0, 0, 0},"]), "};",
        f"const unsigned gb_rom_span_count = {len(data_entries)};",
        "const uint8_t gb_rom_bytes[] = {", *(payload_lines or ["    0,"]), "};",
    ])
    if native_overlays and overlay_entries:
        for bank, bank_entries in sorted(overlay_entries.items()):
            targets.extend([
                f'const GbOverlayTarget gb_native_targets_{bank:03x}[] '
                f'__attribute__((section(".gb_native_{bank:03x}"), aligned(1))) = {{',
                *(f"    {{0x{pc:04x}, {label}}}," for pc, label in bank_entries),
                "};",
            ])
        targets.extend([
            "const GbNativeBank gb_native_banks[] = {",
            *(f"    {{0x{bank:03x}, gb_native_targets_{bank:03x}, {len(bank_entries)}}},"
              for bank, bank_entries in sorted(overlay_entries.items())),
            "};",
            f"const unsigned gb_native_bank_count = {len(overlay_entries)};",
            'const char gb_native_prefix[6] = "N0000";',
            "const uint8_t gb_native_digest[32] = {0};",
        ])
    return "\n".join(assembly) + "\n", "\n".join(targets) + "\n"
