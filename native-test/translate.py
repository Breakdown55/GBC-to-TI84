"""Lower small Game Boy instruction blocks to eZ80 assembly."""

import argparse
from pathlib import Path
import re


ADDRESS_LABEL = r"(?:gb|jump|call|jr)_[0-9a-f]{3}_([0-9a-f]{4})"
HIGH_REGISTERS = {
    "rp1": 0xff00, "rsb": 0xff01, "rsc": 0xff02, "rdiv": 0xff04, "rtima": 0xff05, "rtma": 0xff06,
    "rtac": 0xff07, "rif": 0xff0f, "rnr50": 0xff24, "rnr51": 0xff25,
    "rnr52": 0xff26, "rlcdc": 0xff40, "rstat": 0xff41, "rscy": 0xff42,
    "rscx": 0xff43, "rly": 0xff44, "rlyc": 0xff45, "rdma": 0xff46,
    "rbgp": 0xff47, "robp0": 0xff48, "robp1": 0xff49, "rwy": 0xff4a,
    "rwx": 0xff4b, "rkey1": 0xff4d, "rvbk": 0xff4f,
    "rhdma1": 0xff51, "rhdma2": 0xff52, "rhdma3": 0xff53, "rhdma4": 0xff54, "rhdma5": 0xff55,
    "rrp": 0xff56,
    "rbcps": 0xff68, "rbcpd": 0xff69, "rocps": 0xff6a, "rocpd": 0xff6b, "ropri": 0xff6c,
    "rsvbk": 0xff70, "rie": 0xffff,
}
BYTE_STEPS = {("inc", "a"), ("inc", "c"), ("dec", "b"), ("dec", "c")}
BYTE_REGISTERS = {
    "a": "_gb_a", "b": "_gb_bc+1", "c": "_gb_bc", "h": "_gb_hl+1", "l": "_gb_hl",
    "d": "_gb_de+1", "e": "_gb_de",
}
FAULT_IF_ZERO = ["or a, a", "jp z, _gb_unmapped"]
ALU_OPERATIONS = ("add", "adc", "sub", "sbc", "and", "xor", "or", "cp")
ROTATES = {"rlca": "rlca", "rrca": "rrca", "rla": "rla", "rra": "rra"}


def operand_address(operand):
    match = re.fullmatch(r"\$([0-9a-f]{1,4})|" + ADDRESS_LABEL, operand)
    return int(match.group(1) or match.group(2), 16) if match else None


def dispatch(address):
    return [f"ld hl, 0x{address:04x}", "jp _gb_dispatch"]


def call_with_arg(helper, value):
    return [f"ld hl, 0x{value:02x}", "push hl", f"call _{helper}", "pop bc"]


def call_with_byte(helper, first, register):
    return [
        "ld hl, 0", f"ld a, ({BYTE_REGISTERS[register]})", "ld l, a", "push hl",
        *call_with_arg(helper, first), "pop bc",
    ]


def flag_bit(condition):
    return 7 if condition.endswith("z") else 4


def read_hl_byte():
    return [
        "ld hl, (_gb_hl)", "push hl", "call _gb_read_byte", "pop bc",
        "ld a, h", "or a, a", "jp nz, _gb_unmapped", "ld a, l",
    ]


def write_hl_byte():
    return [
        "ld hl, 0", "ld l, a", "push hl", "ld hl, (_gb_hl)", "push hl",
        "call _gb_write_byte", "pop bc", "pop bc", *FAULT_IF_ZERO,
    ]


def high_address(operand):
    if operand in HIGH_REGISTERS:
        return HIGH_REGISTERS[operand]
    if re.fullmatch(r"\$ff[0-9a-f]{2}", operand):
        address = int(operand[1:], 16)
        if address >= 0xff80 or address in HIGH_REGISTERS.values():
            return address
    return None


def lower_instruction(instruction, pc=None, *, timing=False):
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
        if timing:
            return ["call _gb_di"], 1
        return ["ld a, 0", "ld (_gb_ime), a"], 1
    if parts == ["ei"]:
        if timing:
            return ["call _gb_ei"], 1
        return ["ld a, 1", "ld (_gb_ime), a"], 1
    if parts == ["stop"]:
        return ["call _gb_stop", *FAULT_IF_ZERO], 2
    if tuple(parts) in BYTE_STEPS:
        return [f"call _gb_{parts[0]}_{parts[1]}"], 1
    if len(parts) == 2 and parts[0] in ("inc", "dec"):
        decrement = int(parts[0] == "dec")
        register = parts[1]
        if register in BYTE_REGISTERS:
            return [
                f"ld hl, {decrement}", "push hl",
                "ld hl, 0", f"ld a, ({BYTE_REGISTERS[register]})", "ld l, a", "push hl",
                "call _gb_step_byte", "pop bc", "pop bc", f"ld ({BYTE_REGISTERS[register]}), a",
            ], 1
        if register == "[hl]":
            return [*call_with_arg("gb_step_hl", decrement), *FAULT_IF_ZERO], 1
    if len(parts) == 2 and parts[0] in ("inc", "dec") and parts[1] in ("bc", "de", "hl", "sp"):
        operation, register = parts
        if register == "sp":
            return [
                "ld hl, 0", "ld a, (_gb_sp)", "ld l, a", "ld a, (_gb_sp+1)", "ld h, a",
                f"{operation} hl", "ld a, l", "ld (_gb_sp), a", "ld a, h", "ld (_gb_sp+1), a",
            ], 1
        return [
            f"ld hl, (_gb_{register})", f"{operation} hl", f"ld (_gb_{register}), hl",
            "xor a, a", f"ld (_gb_{register}+2), a",
        ], 1
    sp_offset = re.fullmatch(r"ld\s+hl\s*,\s*sp([+-])\$([0-9a-f]{1,2})", normalized)
    if sp_offset:
        offset = int(sp_offset[2], 16) * (1 if sp_offset[1] == "+" else -1)
        if -128 <= offset <= 127:
            return call_with_arg("gb_ld_hl_sp", offset & 0xff), 2
    sp_add = re.fullmatch(r"add\s+sp\s*,\s*(-?)\$([0-9a-f]{1,2})", normalized)
    if sp_add:
        offset = int(sp_add[2], 16) * (-1 if sp_add[1] else 1)
        if -128 <= offset <= 127:
            return call_with_arg("gb_add_sp", offset & 0xff), 2
    if parts == ["sub", "l"]:
        return ["call _gb_sub_l"], 1
    if parts == ["srl", "a"]:
        return ["call _gb_srl_a"], 2
    if len(parts) == 1 and parts[0] in ROTATES:
        operation = ROTATES[parts[0]]
        carry = ["ld a, (_gb_f)", "and a, 0x10", "add a, 0xf0"] if operation in ("rla", "rra") else []
        return [
            *carry, "ld a, (_gb_a)", operation, "ld (_gb_a), a",
            "ld a, 0", "jr nc, 1f", "ld a, 0x10", "1:", "ld (_gb_f), a",
        ], 1
    if parts == ["cpl"]:
        return ["ld a, (_gb_a)", "cpl", "ld (_gb_a), a", "ld a, (_gb_f)", "or a, 0x60", "ld (_gb_f), a"], 1
    if parts in (["scf"], ["ccf"]):
        change = ["and a, 0x80", "or a, 0x10"] if parts[0] == "scf" else ["and a, 0x90", "xor a, 0x10"]
        return ["ld a, (_gb_f)", *change, "ld (_gb_f), a"], 1
    if re.fullmatch(r"add\s+hl\s*,\s*de", normalized):
        return ["call _gb_add_hl_de"], 1
    add_hl = re.fullmatch(r"add\s+hl\s*,\s*(bc|hl|sp)", normalized)
    if add_hl:
        register = add_hl[1]
        operand = (["ld hl, 0", "ld a, (_gb_sp)", "ld l, a", "ld a, (_gb_sp+1)", "ld h, a"]
                   if register == "sp" else [f"ld hl, (_gb_{register})"])
        return [*operand, "push hl", "call _gb_add_hl", "pop bc"], 1
    set_hl = re.fullmatch(r"set\s+([0-7])\s*,\s*\[hl\]", normalized)
    if set_hl:
        return [*call_with_arg("gb_set_hl_bit", int(set_hl[1])), *FAULT_IF_ZERO], 2
    bit_op = re.fullmatch(r"(bit|res|set)\s+([0-7])\s*,\s*([abcdehl]|\[hl\])", normalized)
    if bit_op:
        operation, bit, register = bit_op.groups()
        load = read_hl_byte() if register == "[hl]" else [f"ld a, ({BYTE_REGISTERS[register]})"]
        if operation != "bit":
            store = write_hl_byte() if register == "[hl]" else [f"ld ({BYTE_REGISTERS[register]}), a"]
            return [*load, f"{operation} {bit}, a", *store], 2
        return [
            *load, f"bit {bit}, a",
            "ld a, 0x20", "jr nz, 1f", "ld a, 0xa0", "1:",
            "ld b, a", "ld a, (_gb_f)", "and a, 0x10",
            "or a, b", "ld (_gb_f), a",
        ], 2
    shift = re.fullmatch(r"(rlc|rrc|rl|rr|sla|sra|swap|srl)\s+([abcdehl]|\[hl\])", normalized)
    if shift:
        operation, register = shift.groups()
        load = read_hl_byte() if register == "[hl]" else [f"ld a, ({BYTE_REGISTERS[register]})"]
        store = write_hl_byte() if register == "[hl]" else [f"ld ({BYTE_REGISTERS[register]}), a"]
        carry = []
        if operation in ("rl", "rr"):
            carry = ["ld c, a", "ld a, (_gb_f)", "and a, 0x10", "add a, 0xf0", "ld a, c"]
        native = ["rlca", "rlca", "rlca", "rlca", "or a, a"] if operation == "swap" else [f"{operation} a"]
        return [
            *load, *carry, *native, "ld b, 0", "jr nz, 1f", "set 7, b", "1:",
            "jr nc, 2f", "set 4, b", "2:", "ld c, a", "ld a, b", "ld (_gb_f), a", "ld a, c", *store,
        ], 2
    if len(parts) == 2 and parts[0] == "cp":
        immediate = re.fullmatch(r"\$([0-9a-f]{1,2})", parts[1])
        if immediate:
            value = int(immediate.group(1), 16)
            return call_with_arg("gb_cp_a", value), 2
    alu = re.fullmatch(r"(add|adc|sub|sbc|and|xor|or|cp)\s+(?:a\s*,\s*)?([abcdehl]|\[hl\]|\$[0-9a-f]{1,2})", normalized)
    if alu:
        operation = ALU_OPERATIONS.index(alu[1])
        operand = alu[2]
        if operand == "[hl]":
            return [*call_with_arg("gb_alu_hl", operation), *FAULT_IF_ZERO], 1
        if operand.startswith("$"):
            return [
                f"ld hl, 0x{int(operand[1:], 16):02x}", "push hl",
                *call_with_arg("gb_alu", operation), "pop bc",
            ], 2
        return call_with_byte("gb_alu", operation, operand), 1
    register_load = re.fullmatch(r"ld\s+([abcdehl])\s*,\s*([abcdehl])", normalized)
    if register_load:
        dest, source = (BYTE_REGISTERS[register] for register in register_load.groups())
        return [f"ld a, ({source})", f"ld ({dest}), a"], 1
    if re.fullmatch(r"ld\s+sp\s*,\s*hl", normalized):
        return [
            "ld hl, (_gb_hl)", "ld a, l", "ld (_gb_sp), a", "ld a, h", "ld (_gb_sp+1), a",
        ], 1
    load = re.fullmatch(r"ld\s+(a|b|c|d|e|h|l|bc|de|hl|sp)\s*,\s*\$([0-9a-f]{1,4})", normalized)
    if load:
        register, digits = load.groups()
        value = int(digits, 16)
        if register in BYTE_REGISTERS and len(digits) <= 2:
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
    hl_load = re.fullmatch(r"ld\s+a\s*,\s*\[hl([+-])\]", normalized)
    if hl_load:
        helper = "_gb_load_hli" if hl_load[1] == "+" else "_gb_load_hld"
        return [f"call {helper}", *FAULT_IF_ZERO], 1
    register_load = re.fullmatch(r"ld\s+([abcdehl])\s*,\s*\[(hl|de|bc)\]", normalized)
    if register_load:
        return [
            f"ld hl, (_gb_{register_load[2]})", "push hl", "call _gb_read_byte", "pop bc",
            "ld a, h", "or a, a", "jp nz, _gb_unmapped",
            "ld a, l", f"ld ({BYTE_REGISTERS[register_load[1]]}), a",
        ], 1
    register_store = re.fullmatch(r"ld\s+\[(hl|de|bc)\]\s*,\s*([abcdehl])", normalized)
    if register_store:
        return [
            "ld hl, 0", f"ld a, ({BYTE_REGISTERS[register_store[2]]})", "ld l, a", "push hl",
            f"ld hl, (_gb_{register_store[1]})", "push hl", "call _gb_write_byte", "pop bc",
            "pop bc", *FAULT_IF_ZERO,
        ], 1
    immediate_store = re.fullmatch(r"ld\s+\[hl\]\s*,\s*\$([0-9a-f]{1,2})", normalized)
    if immediate_store:
        return [
            f"ld hl, 0x{int(immediate_store[1], 16):02x}", "push hl",
            "ld hl, (_gb_hl)", "push hl", "call _gb_write_byte", "pop bc", "pop bc", *FAULT_IF_ZERO,
        ], 2
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
        if read[1] == "c":
            return [
                "ld hl, 0xff00", "ld a, (_gb_bc)", "ld l, a", "push hl",
                "call _gb_load_a", "pop bc", *FAULT_IF_ZERO,
            ], 1
        address = high_address(read.group(1))
        if address is not None:
            return [*call_with_arg("gb_read_high", address & 0xff), "ld (_gb_a), a"], 2
    if len(parts) == 2 and parts[1] in ("hl", "bc", "de", "af"):
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
    if parts == ["reti"]:
        if timing:
            return ["jp _gb_reti"], None
        return ["ld a, 1", "ld (_gb_ime), a", "jp _gb_ret"], None
    if len(parts) == 2 and parts[0] == "ret" and parts[1] in ("z", "nz", "c", "nc"):
        # Native BIT reports zero when the Game Boy flag is clear.
        condition = "nz" if parts[1] in ("z", "c") else "z"
        bit = flag_bit(parts[1])
        if timing:
            opposite = "z" if condition == "nz" else "nz"
            return [
                "ld a, (_gb_f)", f"bit {bit}, a", f"jr {opposite}, 1f",
                "ld a, 12", "ld (_gb_timing_extra), a", "jp _gb_ret", "1:",
            ], 1
        return ["ld a, (_gb_f)", f"bit {bit}, a", f"jp {condition}, _gb_ret"], 1
    if parts == ["halt"]:
        if timing:
            if pc is None:
                raise ValueError("timed HALT requires a Game Boy address")
            return [f"ld hl, 0x{(pc + 1) & 0xffff:04x}", "jp _gb_timed_halt"], None
        return ["jp _gb_halt"], None
    if parts == ["jp", "hl"]:
        return ["ld hl, (_gb_hl)", "jp _gb_dispatch"], None
    if len(parts) == 2 and parts[0] in ("jp", "call", "rst"):
        address = operand_address(parts[1])
        if address is not None:
            if parts[0] == "jp":
                return dispatch(address), None
            if parts[0] == "rst" and (address > 0x38 or address % 8):
                raise ValueError("RST needs a vector from $00 to $38 in steps of 8")
            if pc is None:
                raise ValueError(f"{parts[0].upper()} needs an address-bearing entry label like gb_000_0800")
            return_address = (pc + (1 if parts[0] == "rst" else 3)) & 0xffff
            return [
                f"ld hl, 0x{address:04x}", f"ld de, 0x{return_address:04x}", "jp _gb_call"
            ], None
    conditional_jump = re.fullmatch(r"(jr|jp|call)\s+(nz|z|nc|c)\s*,\s*(\S+)", normalized)
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
            native_condition = "z" if condition in ("z", "c") else "nz"
            bit = flag_bit(condition)
            taken_cycles = 12 if operation == "call" else 4
            taken = [f"ld a, {taken_cycles}", "ld (_gb_timing_extra), a"] if timing else []
            if operation == "call":
                return [
                    "ld a, (_gb_f)", f"bit {bit}, a", f"jr {native_condition}, 1f", *taken,
                    f"ld hl, 0x{address:04x}", f"ld de, 0x{fallthrough:04x}", "jp _gb_call", "1:",
                ], 3
            return [
                f"ld hl, 0x{fallthrough:04x}",
                "ld a, (_gb_f)",
                f"bit {bit}, a",
                # BIT clears native Z when the stored Game Boy Z bit is set.
                f"jp {native_condition}, _gb_dispatch",
                *taken, *dispatch(address),
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
