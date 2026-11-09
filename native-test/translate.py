"""Lower small Game Boy instruction blocks to eZ80 assembly."""

import argparse
from pathlib import Path
import re


ADDRESS_LABEL = r"(?:gb|jump|call|jr)_[0-9a-f]{3}_([0-9a-f]{4})"
HIGH_REGISTERS = {"rlcdc": 0xff40, "rly": 0xff44, "rif": 0xff0f, "rie": 0xffff}


def operand_address(operand):
    match = re.fullmatch(r"\$([0-9a-f]{1,4})|" + ADDRESS_LABEL, operand)
    return int(match.group(1) or match.group(2), 16) if match else None


def dispatch(address):
    return [f"ld hl, 0x{address:04x}", "jp _gb_dispatch"]


def call_with_byte(helper, value):
    return [f"ld hl, 0x{value:02x}", "push hl", f"call _{helper}", "pop bc"]


def high_address(operand):
    if operand in HIGH_REGISTERS:
        return HIGH_REGISTERS[operand]
    if re.fullmatch(r"\$ff[0-9a-f]{2}", operand):
        address = int(operand[1:], 16)
        if address >= 0xff80 or address in HIGH_REGISTERS.values():
            return address
    return None


def lower_instruction(instruction, pc=None):
    normalized = instruction.lower()
    parts = normalized.split()
    # A byte count advances the Game Boy PC. None ends the native block.
    if parts == ["nop"]:
        return [], 1
    logical = re.fullmatch(r"(or|and|xor)\s+(a|\$[0-9a-f]{1,2})", normalized)
    if logical:
        operation, operand = logical.groups()
        immediate = operand != "a"
        operand = f"0x{operand[1:]}" if immediate else operand
        flags = 0x20 if operation == "and" else 0
        return [
            "ld a, (_gb_a)",
            f"{operation} a, {operand}",
            "ld (_gb_a), a",
            f"ld a, 0x{flags:02x}",
            "jr nz, 1f",
            f"ld a, 0x{flags | 0x80:02x}",
            "1:",
            "ld (_gb_f), a",
        ], 2 if immediate else 1
    if parts == ["inc", "a"]:
        return ["call _gb_inc_a"], 1
    if parts == ["dec", "c"]:
        return ["call _gb_dec_c"], 1
    bit_op = re.fullmatch(r"(bit|res)\s+([0-7])\s*,\s*a", normalized)
    if bit_op:
        operation, bit = bit_op.groups()
        if operation == "res":
            return ["ld a, (_gb_a)", f"res {bit}, a", "ld (_gb_a), a"], 2
        return [
            "ld a, (_gb_a)", f"bit {bit}, a",
            "ld a, 0x20", "jr nz, 1f", "ld a, 0xa0", "1:",
            "ld b, a", "ld a, (_gb_f)", "and a, 0x10",
            "or a, b", "ld (_gb_f), a",
        ], 2
    if len(parts) == 2 and parts[0] == "cp":
        immediate = re.fullmatch(r"\$([0-9a-f]{1,2})", parts[1])
        if immediate:
            value = int(immediate.group(1), 16)
            return call_with_byte("gb_cp_a", value), 2
    load = re.fullmatch(r"ld\s+(a|c|hl|sp)\s*,\s*\$([0-9a-f]{1,4})", normalized)
    if load:
        register, digits = load.groups()
        value = int(digits, 16)
        if register in ("a", "c") and len(digits) <= 2:
            symbol = "_gb_a" if register == "a" else "_gb_bc"
            return [f"ld a, 0x{value:02x}", f"ld ({symbol}), a"], 2
        if register == "hl":
            return [f"ld hl, 0x{value:04x}", "ld (_gb_hl), hl"], 3
        if register == "sp":
            return [
                f"ld a, 0x{value & 0xff:02x}",
                "ld (_gb_sp), a",
                f"ld a, 0x{value >> 8:02x}",
                "ld (_gb_sp+1), a",
            ], 3
    if re.fullmatch(r"ld\s+\[hl\+\]\s*,\s*a", normalized):
        return ["call _gb_store_hli", "or a, a", "jp z, _gb_unmapped"], 1
    store = re.fullmatch(r"ldh\s+\[([^\]]+)\]\s*,\s*a", normalized)
    if store:
        address = high_address(store.group(1))
        if address is not None:
            return call_with_byte("gb_write_high", address & 0xff), 2
    read = re.fullmatch(r"ldh\s+a\s*,\s*\[([^\]]+)\]", normalized)
    if read:
        address = high_address(read.group(1))
        if address is not None:
            return [*call_with_byte("gb_read_high", address & 0xff), "ld (_gb_a), a"], 2
    if len(parts) == 2 and parts[1] in ("hl", "bc"):
        symbol = f"_gb_{parts[1]}"
        if parts[0] == "push":
            return [
                f"ld hl, ({symbol})", "push hl", "call _gb_push_word",
                "pop bc", "or a, a", "jp z, _gb_unmapped",
            ], 1
        if parts[0] == "pop":
            return [
                "call _gb_pop_word", "ld de, 0x10000", "or a, a",
                "sbc hl, de", "jp nc, _gb_unmapped", "add hl, de", f"ld ({symbol}), hl",
            ], 1
    if parts == ["ret"]:
        return ["jp _gb_ret"], None
    if parts == ["jp", "hl"]:
        return ["ld hl, (_gb_hl)", "jp _gb_dispatch"], None
    if len(parts) == 2 and parts[0] in ("jp", "call"):
        address = operand_address(parts[1])
        if address is not None:
            if parts[0] == "jp":
                return dispatch(address), None
            if pc is None:
                raise ValueError("CALL needs an address-bearing entry label like gb_000_0800")
            return_address = (pc + 3) & 0xffff
            return [
                f"ld hl, 0x{address:04x}", f"ld de, 0x{return_address:04x}", "jp _gb_call"
            ], None
    relative = re.fullmatch(r"jr\s+(nz|z)\s*,\s*(\S+)", normalized)
    if relative:
        condition, target = relative.groups()
        address = operand_address(target)
        if address is not None:
            if pc is None:
                raise ValueError("JR needs an address-bearing entry label like gb_000_0900")
            fallthrough = (pc + 2) & 0xffff
            displacement = (address - fallthrough + 0x8000) % 0x10000 - 0x8000
            if not -128 <= displacement <= 127:
                raise ValueError("JR destination is outside the signed 8-bit range")
            return [
                f"ld hl, 0x{fallthrough:04x}",
                "ld a, (_gb_f)",
                "bit 7, a",
                # BIT clears native Z when the stored Game Boy Z bit is set.
                f"jp {condition}, _gb_dispatch",
                *dispatch(address),
            ], None
    raise ValueError(f"unsupported instruction {instruction!r}")


def translate(source):
    output = ["    .assume ADL=1", "    .section .text"]
    names = set()
    entry = None
    pc = None
    has_instruction = False

    def finish_block():
        if not has_instruction:
            raise ValueError(f"block {entry!r} needs an instruction")
        if pc is None:
            raise ValueError(f"block {entry!r} needs a control transfer or a Game Boy address")
        output.extend(f"    {line}" for line in dispatch(pc))

    for number, raw in enumerate(source.splitlines(), 1):
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        label = re.fullmatch(r"([a-zA-Z_][a-zA-Z_0-9]*):", line)
        if label:
            if entry is not None:
                finish_block()
            entry = label.group(1)
            has_instruction = False
            if entry in names:
                raise ValueError(f"line {number}: duplicate entry label {entry!r}")
            names.add(entry)
            location = re.fullmatch(ADDRESS_LABEL, entry.lower())
            pc = int(location.group(1), 16) if location else None
            output.extend(["", f"    .global _{entry}", f"_{entry}:"])
            continue
        if entry is None:
            raise ValueError(f"line {number}: expected an entry label")
        try:
            native, size = lower_instruction(line, pc)
        except ValueError as error:
            raise ValueError(f"line {number}: {error}") from error
        output.extend(f"    {line}" for line in native)
        has_instruction = True
        if size is None:
            entry = None
        elif pc is not None:
            pc = (pc + size) & 0xffff
    if entry is not None:
        finish_block()
    if not names:
        raise ValueError("expected at least one control-flow block")
    return "\n".join(output) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        output = translate(args.source.read_text(encoding="utf-8"))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    except (OSError, ValueError) as error:
        parser.exit(1, f"{args.source}: {error}\n")


if __name__ == "__main__":
    main()
