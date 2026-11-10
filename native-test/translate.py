"""Lower small Game Boy instruction blocks to eZ80 assembly."""

import argparse
from pathlib import Path
import re


ADDRESS_LABEL = r"(?:gb|jump|call|jr)_[0-9a-f]{3}_([0-9a-f]{4})"
HIGH_REGISTERS = {
    "rp1": 0xff00, "rsb": 0xff01, "rsc": 0xff02, "rtma": 0xff06,
    "rtac": 0xff07, "rif": 0xff0f, "rnr50": 0xff24, "rnr51": 0xff25,
    "rnr52": 0xff26, "rlcdc": 0xff40, "rstat": 0xff41, "rscy": 0xff42,
    "rscx": 0xff43, "rly": 0xff44, "rlyc": 0xff45, "rwy": 0xff4a,
    "rwx": 0xff4b, "rkey1": 0xff4d, "rvbk": 0xff4f, "rrp": 0xff56,
    "rsvbk": 0xff70, "rie": 0xffff,
}
BYTE_STEPS = {("inc", "a"), ("inc", "c"), ("dec", "b"), ("dec", "c")}
BYTE_REGISTERS = {
    "a": "_gb_a", "b": "_gb_bc+1", "c": "_gb_bc", "h": "_gb_hl+1", "l": "_gb_hl",
}
FAULT_IF_ZERO = ["or a, a", "jp z, _gb_unmapped"]


def operand_address(operand):
    match = re.fullmatch(r"\$([0-9a-f]{1,4})|" + ADDRESS_LABEL, operand)
    return int(match.group(1) or match.group(2), 16) if match else None


def dispatch(address):
    return [f"ld hl, 0x{address:04x}", "jp _gb_dispatch"]


def call_with_arg(helper, value):
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
    logical = re.fullmatch(r"(or|and|xor)\s+(a|c|\$[0-9a-f]{1,2})", normalized)
    if logical:
        operation, operand = logical.groups()
        immediate = operand.startswith("$")
        setup = ["ld a, (_gb_bc)", "ld c, a"] if operand == "c" else []
        operand = f"0x{operand[1:]}" if immediate else operand
        flags = 0x20 if operation == "and" else 0
        return [
            *setup,
            "ld a, (_gb_a)",
            f"{operation} a, {operand}",
            "ld (_gb_a), a",
            f"ld a, 0x{flags:02x}",
            "jr nz, 1f",
            f"ld a, 0x{flags | 0x80:02x}",
            "1:",
            "ld (_gb_f), a",
        ], 2 if immediate else 1
    if parts == ["di"]:
        return ["ld a, 0", "ld (_gb_ime), a"], 1
    if parts == ["stop"]:
        return ["call _gb_stop", *FAULT_IF_ZERO], 2
    if tuple(parts) in BYTE_STEPS:
        return [f"call _gb_{parts[0]}_{parts[1]}"], 1
    if parts == ["dec", "bc"]:
        return [
            "ld hl, (_gb_bc)", "dec hl", "ld (_gb_bc), hl",
            "xor a, a", "ld (_gb_bc+2), a",
        ], 1
    if parts == ["sub", "l"]:
        return ["call _gb_sub_l"], 1
    if parts == ["srl", "a"]:
        return ["call _gb_srl_a"], 2
    if re.fullmatch(r"add\s+hl\s*,\s*de", normalized):
        return ["call _gb_add_hl_de"], 1
    set_hl = re.fullmatch(r"set\s+([0-7])\s*,\s*\[hl\]", normalized)
    if set_hl:
        return [*call_with_arg("gb_set_hl_bit", int(set_hl[1])), *FAULT_IF_ZERO], 2
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
            return call_with_arg("gb_cp_a", value), 2
    register_load = re.fullmatch(r"ld\s+([abchl])\s*,\s*([abchl])", normalized)
    if register_load:
        dest, source = (BYTE_REGISTERS[register] for register in register_load.groups())
        return [f"ld a, ({source})", f"ld ({dest}), a"], 1
    load = re.fullmatch(r"ld\s+(a|b|c|bc|de|hl|sp)\s*,\s*\$([0-9a-f]{1,4})", normalized)
    if load:
        register, digits = load.groups()
        value = int(digits, 16)
        if register in ("a", "b", "c") and len(digits) <= 2:
            return [f"ld a, 0x{value:02x}", f"ld ({BYTE_REGISTERS[register]}), a"], 2
        if register in ("bc", "de", "hl"):
            return [f"ld hl, 0x{value:04x}", f"ld (_gb_{register}), hl"], 3
        if register == "sp":
            return [
                f"ld a, 0x{value & 0xff:02x}",
                "ld (_gb_sp), a",
                f"ld a, 0x{value >> 8:02x}",
                "ld (_gb_sp+1), a",
            ], 3
    indirect_store = re.fullmatch(r"ld\s+\[hl([+-]?)\]\s*,\s*a", normalized)
    if indirect_store:
        helper = {"": "gb_store_hl", "+": "gb_store_hli", "-": "gb_store_hld"}[indirect_store[1]]
        return [f"call _{helper}", *FAULT_IF_ZERO], 1
    if re.fullmatch(r"ld\s+a\s*,\s*\[hl\+\]", normalized):
        return ["call _gb_load_hli", *FAULT_IF_ZERO], 1
    direct_load = re.fullmatch(r"ld\s+a\s*,\s*\[\$([0-9a-f]{1,4})\]", normalized)
    if direct_load:
        return [*call_with_arg("gb_load_a", int(direct_load[1], 16)), *FAULT_IF_ZERO], 3
    direct_store = re.fullmatch(r"ld\s+\[\$([0-9a-f]{1,4})\]\s*,\s*a", normalized)
    if direct_store:
        address = int(direct_store.group(1), 16)
        return [
            *call_with_arg("gb_store_a", address), "or a, a", "jp z, _gb_unmapped"
        ], 3
    store = re.fullmatch(r"ldh\s+\[([^\]]+)\]\s*,\s*a", normalized)
    if store:
        operand = store.group(1)
        if operand == "c":
            return [
                "ld hl, 0", "ld a, (_gb_bc)", "ld l, a", "push hl",
                "call _gb_write_high", "pop bc", "or a, a", "jp z, _gb_unmapped",
            ], 1
        address = high_address(operand)
        if address is not None:
            return [*call_with_arg("gb_write_high", address & 0xff),
                    "or a, a", "jp z, _gb_unmapped"], 2
    read = re.fullmatch(r"ldh\s+a\s*,\s*\[([^\]]+)\]", normalized)
    if read:
        address = high_address(read.group(1))
        if address is not None:
            return [*call_with_arg("gb_read_high", address & 0xff), "ld (_gb_a), a"], 2
    if len(parts) == 2 and parts[1] in ("hl", "bc", "af"):
        symbol = f"_gb_{parts[1]}"
        if parts[0] == "push":
            load_word = [
                "ld hl, 0", "ld a, (_gb_a)", "ld h, a", "ld a, (_gb_f)", "ld l, a",
            ] if parts[1] == "af" else [f"ld hl, ({symbol})"]
            return [*load_word, "push hl", "call _gb_push_word", "pop bc", *FAULT_IF_ZERO], 1
        if parts[0] == "pop":
            store_word = [
                "ld a, h", "ld (_gb_a), a", "ld a, l", "and a, 0xf0", "ld (_gb_f), a",
            ] if parts[1] == "af" else [f"ld ({symbol}), hl"]
            return [
                "call _gb_pop_word", "ld de, 0x10000", "or a, a",
                "sbc hl, de", "jp nc, _gb_unmapped", "add hl, de", *store_word,
            ], 1
    if parts == ["ret"]:
        return ["jp _gb_ret"], None
    if parts in (["ret", "z"], ["ret", "nz"]):
        # Native BIT reports zero when the Game Boy flag is clear.
        condition = "nz" if parts[1] == "z" else "z"
        return ["ld a, (_gb_f)", "bit 7, a", f"jp {condition}, _gb_ret"], 1
    if parts == ["halt"]:
        return ["jp _gb_halt"], None
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
    conditional_jump = re.fullmatch(r"(jr|jp)\s+(nz|z)\s*,\s*(\S+)", normalized)
    if conditional_jump:
        operation, condition, target = conditional_jump.groups()
        address = operand_address(target)
        if address is not None:
            if pc is None:
                raise ValueError(f"{operation.upper()} needs an address-bearing entry label like gb_000_0900")
            fallthrough = (pc + (2 if operation == "jr" else 3)) & 0xffff
            displacement = (address - fallthrough + 0x8000) % 0x10000 - 0x8000
            if operation == "jr" and not -128 <= displacement <= 127:
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
