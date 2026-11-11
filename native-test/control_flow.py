"""Follow direct branch alternatives beyond the simulated execution path."""
from collections import deque
from dataclasses import dataclass

from rom import (BANK_SIZE, CONDITIONAL_CALL, CONDITIONAL_JP, CONDITIONAL_JR,
                 CONDITIONAL_RET, RETURNS, UNSUPPORTED, Instruction, State,
                 MEMORY_LOADS, HL_LOADS, POPS, join_word)
from translate import lower_instruction


@dataclass(frozen=True)
class InlineCallFormat:
    length: int
    bank_offset: int = None
    index_offset: int = None
    table_base: int = None

    def __post_init__(self):
        if not 0 <= self.length <= 255:
            raise ValueError("inline call length must fit in a byte")
        fields = self.bank_offset, self.index_offset, self.table_base
        if any(value is not None for value in fields):
            if (any(value is None for value in fields)
                    or not 0 <= self.bank_offset < self.length
                    or not 0 <= self.index_offset < self.length
                    or not 0 <= self.table_base <= 0x7f00):
                raise ValueError("invalid indexed inline call format")


def inline_jump_table(cartridge, bank, vector, start):
    """Recognize a tail dispatcher that indexes words after its saved return PC."""
    if not 0 <= start < 0x7ffd:
        return None
    window = 0 if start < BANK_SIZE else bank
    limit = (start // BANK_SIZE + 1) * BANK_SIZE
    end = limit
    cursor = start
    targets = []
    while cursor + 2 <= end and len(targets) < 128:
        offset = window * BANK_SIZE + cursor % BANK_SIZE
        target = int.from_bytes(cartridge.data[offset:offset + 2], "little")
        if not 0 <= target < 0x8000 or start <= target < cursor + 2:
            break
        targets.append(target)
        cursor += 2
        if cursor <= target < end:
            end = target
    # Require a pointer to the first byte after the table. Without that bound,
    # words in the adjacent code or data cannot safely be treated as entries.
    if len(targets) < 2 or cursor != end or end == limit:
        return None
    for index in range(len(targets)):
        state = State(vector, bank, a=index, stack=(start >> 8, start & 255))
        reads = set()
        for _ in range(16):
            if not 0 <= state.pc < BANK_SIZE:
                return None
            try:
                instruction = cartridge.decode(0, state.pc)
            except ValueError:
                return None
            if instruction.opcode == 0xe9:
                expected_reads = {(window, start + index * 2), (window, start + index * 2 + 1)}
                if state.stack or join_word(*state.hl) != targets[index] or reads != expected_reads:
                    return None
                break
            if not (instruction.register_only() or instruction.opcode in (
                    0xc3, 0x18, 0xe1, *CONDITIONAL_JR, *CONDITIONAL_JP, *HL_LOADS)):
                return None
            successors = tuple(cartridge.successors(state, instruction, reads))
            if len(successors) != 1:
                return None
            state = successors[0]
        else:
            return None
    return targets


def read_entry_tables(cartridge, tables, rom_bytes):
    """Resolve declared little-endian word tables within a ROM window."""
    entries = []
    for bank, start, count in tables:
        end = start + 2 * count
        if (not 0 <= bank <= cartridge.bank_mask or not 0 <= start < end <= 0x8000
                or start // BANK_SIZE != (end - 1) // BANK_SIZE):
            raise ValueError("entry table must fit in one cartridge ROM window")
        source_bank = bank if start >= BANK_SIZE else 0
        for pc in range(start, end, 2):
            offset = source_bank * BANK_SIZE + pc % BANK_SIZE
            target = int.from_bytes(cartridge.data[offset:offset + 2], "little")
            if target >= 0x8000 and target not in cartridge.ram_code:
                raise ValueError(f"entry table {bank:03X}:{pc:04X} points outside translated memory")
            entries.append((bank, target))
        rom_bytes.update((source_bank, pc) for pc in range(start, end))
    return entries


def discover_branches(cartridge, entries, instructions, rom_bytes, dispatch_targets,
                      inline_calls=None, *, callback_calls=None, limit=100000, stop_at=None):
    """Add both outcomes of branches and the continuations of calls.

    Inline call layouts describe the cartridge's calling convention. Unresolved
    register jumps still require simulated discovery or additional entry points.
    Return the added instruction count and unresolved transfers.
    """
    inline_calls = inline_calls or {}
    callback_calls = callback_calls or {}
    entries = tuple(entries)
    explicit = {(bank if BANK_SIZE <= pc < 0x8000 else 0, pc) for bank, pc in entries}
    occupied = {}
    for (bank, pc), instruction in sorted(instructions.items()):
        for byte in range(pc, pc + instruction.size):
            occupied.setdefault((bank, byte), pc)
    unknown = (None,) * 7  # A, B, C, D, E, H, L.
    pending = deque((bank, pc, True, unknown) for bank, pc in entries)
    visited = set()
    outside = set()
    tables = {}
    added = 0

    def retain(bank, address, size):
        if not 0 <= address < address + size <= 0x8000 or address // BANK_SIZE != (address + size - 1) // BANK_SIZE:
            raise ValueError("inline call data crosses a ROM window")
        bank = 0 if address < BANK_SIZE else bank
        rom_bytes.update((bank, offset) for offset in range(address, address + size))
        position = bank * BANK_SIZE + address % BANK_SIZE
        return cartridge.data[position:position + size]

    while pending:
        bank, pc, entry, registers = pending.popleft()
        if not 0 <= bank <= cartridge.bank_mask:
            raise ValueError("branch entry bank is outside the cartridge")
        if not 0 <= pc < 0x8000 and pc not in cartridge.ram_code:
            outside.add((bank, pc))
            continue
        key = bank if BANK_SIZE <= pc < 0x8000 else 0, pc
        if dispatch_targets is not None and entry:
            dispatch_targets.add(key)
        location = bank, pc, registers
        if location in visited or key == stop_at:
            continue
        visited.add(location)
        if len(visited) > limit:
            raise ValueError(f"branch discovery exceeded {limit:,} locations")
        instruction = instructions.get(key)
        if instruction is None:
            if key in occupied and occupied[key] != pc and key not in explicit:
                outside.add(key)
                continue
            try:
                instruction = cartridge.decode(*key)
                lower_instruction(instruction.text, pc)
            except ValueError as error:
                if not str(error).startswith(("unsupported ", "instruction crosses")):
                    raise
                instruction = Instruction(UNSUPPORTED, 1, "")
            if key not in explicit and any((key[0], byte) in occupied and occupied[key[0], byte] != pc
                                           for byte in range(pc, pc + instruction.size)):
                outside.add(key)
                continue
            instructions[key] = instruction
            for byte in range(pc, pc + instruction.size):
                occupied.setdefault((key[0], byte), pc)
            added += 1
        opcode = instruction.opcode
        following = pc + instruction.size
        call = opcode in (0xcd, *CONDITIONAL_CALL) or opcode & 0xc7 == 0xc7
        if call and instruction.operand in callback_calls:
            pair = callback_calls[instruction.operand]
            high, low = {"bc": (1, 2), "de": (3, 4), "hl": (5, 6)}[pair]
            target = join_word(registers[low], registers[high])
            if target is not None:
                pending.append((bank, target, True, unknown))
        layout = inline_calls.get(instruction.operand) if call else None
        table = None
        if layout is None and opcode >= 0 and opcode & 0xc7 == 0xc7:
            table_key = bank, pc
            if table_key not in tables:
                tables[table_key] = inline_jump_table(cartridge, bank, instruction.operand, following)
            table = tables[table_key]
        if table is not None:
            retain(bank, following, len(table) * 2)
            pending.append((bank, instruction.operand, True, unknown))
            pending.extend((bank, target, True, unknown) for target in table)
        elif layout is not None:
            payload = retain(bank, following, layout.length) if layout.length else b""
            if layout.bank_offset is not None:
                destination_bank = payload[layout.bank_offset] & cartridge.bank_mask
                address = layout.table_base + payload[layout.index_offset]
                target = int.from_bytes(retain(destination_bank, address, 2), "little")
                pending.append((destination_bank, target, True, unknown))
            pending.append((bank, instruction.operand, True, unknown))
            pending.append((bank, following + layout.length, True, unknown))
            if opcode in CONDITIONAL_CALL:
                pending.append((bank, following, True, unknown))
        elif opcode in (0x18, 0xc3):
            pending.append((bank, instruction.operand, True, unknown))
        elif opcode in (*CONDITIONAL_JR, *CONDITIONAL_JP) or call:
            pending.append((bank, instruction.operand, True, unknown))
            pending.append((bank, following, True, unknown))
        elif opcode == 0xe9:
            target = join_word(registers[6], registers[5])
            if target is not None:
                pending.append((bank, target, True, unknown))
        elif opcode in (*RETURNS, UNSUPPORTED):
            if opcode in CONDITIONAL_RET:
                pending.append((bank, following, True, unknown))
        elif opcode == 0x76:
            pending.append((bank, following, True, unknown))
        else:
            if instruction.register_only() or opcode in (*MEMORY_LOADS, *HL_LOADS, *POPS, 0xf0):
                a, b, c, d, e, h, l = registers
                state = State(pc, bank, a, (l, h), b=b, c=c, de=(e, d))
                successors = tuple(cartridge.successors(state, instruction, rom_bytes))
                if len(successors) == 1:
                    state = successors[0]
                    registers = state.a, state.b, state.c, state.de[1], state.de[0], state.hl[1], state.hl[0]
                else:
                    registers = unknown
            elif opcode in (0x22, 0x32):
                # Stores through HL change the pointer, while their memory contents
                # are deliberately left unknown in this branch-only traversal.
                registers = (*registers[:5], None, None)
            pending.append((bank, following, False, registers))
    return added, sorted(outside)
