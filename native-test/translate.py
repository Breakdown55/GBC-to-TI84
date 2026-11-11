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
BYTE_REGISTERS = {
    "a": "_gb_a", "b": "_gb_bc+1", "c": "_gb_bc", "h": "_gb_hl+1", "l": "_gb_hl",
    "d": "_gb_de+1", "e": "_gb_de",
}
FAULT_IF_ZERO = ["or a, a", "jp z, _gb_unmapped"]
ROTATES = {"rlca": "rlca", "rrca": "rrca", "rla": "rla", "rra": "rra"}

GUEST_LABEL = re.compile(r"_gb_[0-9a-f]{3}_[0-9a-f]{4}")
RESIDENT_REGISTERS = ("a", "bc", "de", "hl")
RESIDENT_LOADS = {f"ld {register}, (_gb_{register})": register for register in RESIDENT_REGISTERS}
RESIDENT_STORES = {f"ld (_gb_{register}), {register}": register for register in RESIDENT_REGISTERS}
STATE_SLOT = re.compile(r"\(_gb_(a|bc|de|hl)(?:\+\d+)?\)")
NATIVE_SLOT = {register: register for register in RESIDENT_REGISTERS}
NATIVE_SLOT.update({"b": "bc", "c": "bc", "d": "de", "e": "de", "h": "hl", "l": "hl"})
STATE_MOVE = re.compile(
    r"ld (bc|de|hl|[abcdehl]|\(_gb_[a-z_]+(?:\+\d+)?\)), "
    r"(0x[0-9a-f]+|bc|de|hl|[abcdehl]|\(_gb_[a-z_]+(?:\+\d+)?\))"
)
WORD_STEP = re.compile(r"(?:inc|dec)\.s (bc|de|hl)")
A_ARITHMETIC = re.compile(r"(?:add|adc|sub|sbc|and|xor|or) a, (?:[abcdehl]|0x[0-9a-f]+)")
A_COMPARE = re.compile(r"cp a, (?:[abcdehl]|0x[0-9a-f]+)")
FLAG_BRANCH = re.compile(r"(jp|jr) (z|nz), (\S+)")
NATIVE_FLAG_BITS = {
    "call _gb_native_flags": ("bit 4, a", "bit 7, a"),
    "call _gb_native_step_flags": ("bit 7, a",),
    "call _gb_native_bit_flags": ("bit 7, a",),
}


def native_entries(assembly, dispatch_entries):
    entries = set(dispatch_entries)
    for line in assembly:
        if line.startswith(("    jp ", "    jr ", "    call ")):
            entries.update(GUEST_LABEL.findall(line))
    return entries


def forward_native_flags(assembly, entries):
    """Use live host Z/C after the flag conversion helper when no entry intervenes."""
    live = ()
    for index, line in enumerate(assembly):
        operation = line.strip()
        if not operation or operation.startswith((".assume ", ".section ", ".global ")):
            continue
        if operation.endswith(":") and GUEST_LABEL.fullmatch(operation[:-1]) and operation[:-1] not in entries:
            continue
        if operation in NATIVE_FLAG_BITS:
            live = NATIVE_FLAG_BITS[operation]
        elif live and operation == "ld a, (_gb_f)" and index + 2 < len(assembly):
            test = assembly[index + 1].strip()
            branch = FLAG_BRANCH.fullmatch(assembly[index + 2].strip())
            if test in live and branch:
                jump, condition, target = branch.groups()
                native = ("c" if condition == "nz" else "nc") if test == "bit 4, a" else (
                    "z" if condition == "nz" else "nz"
                )
                assembly[index] = assembly[index + 1] = ""
                assembly[index + 2] = f"    {jump} {native}, {target}"
        elif not operation.startswith("ld ") or operation.startswith("ld (_gb_f),"):
            live = ()


def omit_dead_flag_updates(assembly):
    """Skip a full flag conversion if a later write replaces it on this path."""
    live = True
    for index in range(len(assembly) - 1, -1, -1):
        operation = assembly[index].strip()
        if operation == "call _gb_native_flags":
            if not live:
                assembly[index] = ""
            live = False
        elif operation.startswith("ld (_gb_f),"):
            live = False
        elif "(_gb_f)" in operation or operation.startswith(("jp ", "jr ", "call ", "ret", "djnz ")):
            live = True


def reuse_native_registers(assembly, dispatch_entries):
    """Forward guest A, BC, DE and HL inside straight-line native code.

    Retain the last write to each guest slot at every possible entry, exit,
    or unknown helper call. A write may be removed only when a later write
    supersedes it without an intervening read from the slot.
    """
    entries = native_entries(assembly, dispatch_entries)

    resident = set()
    stores = {}
    for index, line in enumerate(assembly):
        operation = line.strip()
        if not operation or operation.startswith((".assume ", ".section ", ".global ")):
            continue
        if operation.endswith(":") and GUEST_LABEL.fullmatch(operation[:-1]) and operation[:-1] not in entries:
            continue
        if operation in RESIDENT_LOADS:
            register = RESIDENT_LOADS[operation]
            if register in resident:
                assembly[index] = ""
            else:
                stores.pop(register, None)
                resident.add(register)
            continue
        if operation in RESIDENT_STORES:
            register = RESIDENT_STORES[operation]
            if register in stores:
                assembly[stores[register]] = ""
            stores[register] = index
            resident.add(register)
            continue
        move = STATE_MOVE.fullmatch(operation)
        if move:
            destination, source = move.groups()
            read_slot = STATE_SLOT.fullmatch(source)
            if read_slot:
                stores.pop(read_slot[1], None)
            write_slot = STATE_SLOT.fullmatch(destination)
            if write_slot:
                stores.pop(write_slot[1], None)
                resident.discard(write_slot[1])
            resident.discard(NATIVE_SLOT.get(destination))
            continue
        if A_COMPARE.fullmatch(operation) or operation in ("or a, a", "and a, a"):
            continue
        if A_ARITHMETIC.fullmatch(operation) or operation in ("inc a", "dec a", "cpl", *ROTATES):
            resident.discard("a")
            continue
        step = WORD_STEP.fullmatch(operation)
        if step:
            resident.discard(step[1])
            continue
        if operation in ("inc hl", "dec hl", "add.s hl, hl", "add.s hl, de"):
            resident.discard("hl")
            continue
        if operation == "call _gb_native_flags":
            resident.difference_update(("hl", "de"))
            continue
        if operation == "call _gb_native_step_flags":
            resident.difference_update(("hl", "de", "bc"))
            continue
        if operation == "call _gb_native_bit_flags":
            resident.discard("de")
            continue
        if operation == "call _gb_native_add16_flags":
            resident.difference_update(("de", "bc"))
            continue
        # Branches, fault paths, arbitrary memory operations and other helpers
        # may read or replace any canonical guest state.
        resident.clear()
        stores.clear()


def operand_address(operand):
    match = re.fullmatch(r"\$([0-9a-f]{1,4})|" + ADDRESS_LABEL, operand)
    return int(match.group(1) or match.group(2), 16) if match else None


def dispatch(address):
    return [f"ld hl, 0x{address:04x}", "jp _gb_dispatch"]


def call_with_arg(helper, value):
    return [f"ld hl, 0x{value:02x}", "push hl", f"call _{helper}", "pop bc"]


def flag_bit(condition):
    return 7 if condition.endswith("z") else 4


def read_indirect_byte(pair="hl"):
    return [
        f"ld hl, (_gb_{pair})", "call _gb_read_indirect", "jp c, _gb_unmapped",
    ]


def write_indirect_byte(pair="hl"):
    return [
        f"ld hl, (_gb_{pair})", "call _gb_write_indirect", "jp z, _gb_unmapped",
    ]


def high_address(operand):
    if operand in HIGH_REGISTERS:
        return HIGH_REGISTERS[operand]
    if re.fullmatch(r"\$ff[0-9a-f]{2}", operand):
        address = int(operand[1:], 16)
        if (address >= 0xff80 or 0xff10 <= address < 0xff27 or 0xff30 <= address < 0xff40
                or address in HIGH_REGISTERS.values()):
            return address
    return None


def call_sequence(address, resume, native_target, native_resume):
    target = native_target or f"0x{address:04x}"
    helper = "_gb_call_native" if native_target else "_gb_call"
    transfer = "jp"
    if native_resume:
        helper = "_gb_call_predict_native" if native_target else "_gb_call_predict"
        transfer = "call"
    return [f"ld hl, {target}", f"ld de, 0x{resume:04x}", f"{transfer} {helper}"]


def lower_instruction(instruction, pc=None, *, timing=False, live_immediate=False,
                      vblank_preview=False, native_target=None, native_resume=None,
                      predict_returns=False):
    normalized = instruction.lower()
    if normalized == "daa":
        return ["call _gb_daa"], 1
    stack_store = re.fullmatch(r"ld\s+\[\$([0-9a-f]{1,4})\]\s*,\s*sp", normalized)
    if stack_store:
        return [*call_with_arg("gb_store_sp", int(stack_store[1], 16)), *FAULT_IF_ZERO], 3
    parts = normalized.split()
    # A byte count advances the Game Boy PC. None ends the native block.
    if parts == ["nop"]:
        return [], 1
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
    if len(parts) == 2 and parts[0] in ("inc", "dec"):
        decrement = int(parts[0] == "dec")
        register = parts[1]
        if register in BYTE_REGISTERS:
            return [
                f"ld a, ({BYTE_REGISTERS[register]})", f"{parts[0]} a",
                f"ld ({BYTE_REGISTERS[register]}), a", "call _gb_native_step_flags",
            ], 1
        if register == "[hl]":
            return [*call_with_arg("gb_step_hl", decrement), *FAULT_IF_ZERO], 1
    if len(parts) == 2 and parts[0] in ("inc", "dec") and parts[1] in ("bc", "de", "hl", "sp"):
        operation, register = parts
        native = "hl" if register == "sp" else register
        return [
            f"ld {native}, (_gb_{register})", f"{operation}.s {native}", f"ld (_gb_{register}), {native}",
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
    add_hl = re.fullmatch(r"add\s+hl\s*,\s*(bc|de|hl|sp)", normalized)
    if add_hl:
        register = add_hl[1]
        operand = [] if register == "hl" else [f"ld de, (_gb_{register})"]
        return [*operand, "ld hl, (_gb_hl)", f"add.s hl, {'hl' if register == 'hl' else 'de'}",
                "ld (_gb_hl), hl", "call _gb_native_add16_flags"], 1
    set_hl = re.fullmatch(r"set\s+([0-7])\s*,\s*\[hl\]", normalized)
    if set_hl:
        return [*call_with_arg("gb_set_hl_bit", int(set_hl[1])), *FAULT_IF_ZERO], 2
    bit_op = re.fullmatch(r"(bit|res|set)\s+([0-7])\s*,\s*([abcdehl]|\[hl\])", normalized)
    if bit_op:
        operation, bit, register = bit_op.groups()
        load = read_indirect_byte() if register == "[hl]" else [f"ld a, ({BYTE_REGISTERS[register]})"]
        if operation != "bit":
            store = write_indirect_byte() if register == "[hl]" else [f"ld ({BYTE_REGISTERS[register]}), a"]
            return [*load, f"{operation} {bit}, a", *store], 2
        return [*load, f"bit {bit}, a", "call _gb_native_bit_flags"], 2
    shift = re.fullmatch(r"(rlc|rrc|rl|rr|sla|sra|swap|srl)\s+([abcdehl]|\[hl\])", normalized)
    if shift:
        operation, register = shift.groups()
        load = read_indirect_byte() if register == "[hl]" else [f"ld a, ({BYTE_REGISTERS[register]})"]
        store = write_indirect_byte() if register == "[hl]" else [f"ld ({BYTE_REGISTERS[register]}), a"]
        carry = []
        if operation in ("rl", "rr"):
            carry = ["ld c, a", "ld a, (_gb_f)", "and a, 0x10", "add a, 0xf0", "ld a, c"]
        native = ["rlca", "rlca", "rlca", "rlca", "or a, a"] if operation == "swap" else [f"{operation} a"]
        # The eZ80's CB shifts set Z/C and clear H/N, just like the guest.
        return [*load, *carry, *native, "call _gb_native_flags", *store], 2
    alu = re.fullmatch(r"(add|adc|sub|sbc|and|xor|or|cp)\s+(?:a\s*,\s*)?([abcdehl]|\[hl\]|\$[0-9a-f]{1,2})", normalized)
    if alu:
        operation, operand = alu.groups()
        immediate = operand.startswith("$")
        if operand == "[hl]":
            setup = [*read_indirect_byte(), "ld c, a"]
        elif immediate or operand == "a":
            setup = []
        else:
            setup = [f"ld a, ({BYTE_REGISTERS[operand]})", "ld c, a"]
        carry = ["ld a, (_gb_f)", "and a, 0x10", "add a, 0xf0"] if operation in ("adc", "sbc") else []
        argument = f"0x{int(operand[1:], 16):02x}" if immediate else "a" if operand == "a" else "c"
        store = [] if operation == "cp" or (operand == "a" and operation in ("and", "or")) else ["ld (_gb_a), a"]
        load = [] if operation == "xor" and operand == "a" else ["ld a, (_gb_a)"]
        return [*setup, *carry, *load, f"{operation} a, {argument}",
                *store, "call _gb_native_flags"], 2 if immediate else 1
    register_load = re.fullmatch(r"ld\s+([abcdehl])\s*,\s*([abcdehl])", normalized)
    if register_load:
        dest, source = (BYTE_REGISTERS[register] for register in register_load.groups())
        return [f"ld a, ({source})", f"ld ({dest}), a"], 1
    if re.fullmatch(r"ld\s+sp\s*,\s*hl", normalized):
        return ["ld hl, (_gb_hl)", "ld (_gb_sp), hl"], 1
    load = re.fullmatch(r"ld\s+(a|b|c|d|e|h|l|bc|de|hl|sp)\s*,\s*\$([0-9a-f]{1,4})", normalized)
    if load:
        register, digits = load.groups()
        value = int(digits, 16)
        if register in BYTE_REGISTERS and len(digits) <= 2:
            operand = f"(_gb_hram+{pc + 1 - 0xff80})" if live_immediate else f"0x{value:02x}"
            return [f"ld a, {operand}", f"ld ({BYTE_REGISTERS[register]}), a"], 2
        if register in ("bc", "de", "hl", "sp"):
            native = "hl" if register == "sp" else register
            return [f"ld {native}, 0x{value:04x}", f"ld (_gb_{register}), {native}"], 3
    indirect_store = re.fullmatch(r"ld\s+\[hl([+-]?)\]\s*,\s*a", normalized)
    if indirect_store:
        helper = {"": "gb_fast_store_hl", "+": "gb_fast_store_hli", "-": "gb_fast_store_hld"}[indirect_store[1]]
        return [f"call _{helper}", *FAULT_IF_ZERO], 1
    hl_load = re.fullmatch(r"ld\s+a\s*,\s*\[hl([+-])\]", normalized)
    if hl_load:
        helper = "_gb_fast_load_hli" if hl_load[1] == "+" else "_gb_fast_load_hld"
        return [f"call {helper}", *FAULT_IF_ZERO], 1
    register_load = re.fullmatch(r"ld\s+([abcdehl])\s*,\s*\[(hl|de|bc)\]", normalized)
    if register_load:
        return [
            *read_indirect_byte(register_load[2]),
            f"ld ({BYTE_REGISTERS[register_load[1]]}), a",
        ], 1
    register_store = re.fullmatch(r"ld\s+\[(hl|de|bc)\]\s*,\s*([abcdehl])", normalized)
    if register_store:
        return [
            f"ld a, ({BYTE_REGISTERS[register_store[2]]})", *write_indirect_byte(register_store[1]),
        ], 1
    immediate_store = re.fullmatch(r"ld\s+\[hl\]\s*,\s*\$([0-9a-f]{1,2})", normalized)
    if immediate_store:
        return [
            f"ld a, 0x{int(immediate_store[1], 16):02x}", *write_indirect_byte(),
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
            return [*load_word, "call _gb_fast_push", *FAULT_IF_ZERO], 1
        if parts[0] == "pop":
            store_word = [
                "ld a, h", "ld (_gb_a), a", "ld a, l", "and a, 0xf0", "ld (_gb_f), a",
            ] if parts[1] == "af" else [f"ld ({symbol}), hl"]
            return [
                "call _gb_fast_pop", "jp c, _gb_unmapped", *store_word,
            ], 1
    if parts == ["ret"]:
        return ["jp _gb_ret_predict" if predict_returns else "jp _gb_ret"], None
    if parts == ["reti"]:
        if vblank_preview:
            return ["jp _gb_preview_reti"], None
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
        target = "_gb_ret_predict" if predict_returns else "_gb_ret"
        return ["ld a, (_gb_f)", f"bit {bit}, a", f"jp {condition}, {target}"], 1
    if parts == ["halt"]:
        if timing or vblank_preview:
            if pc is None:
                raise ValueError("interruptible HALT requires a Game Boy address")
            target = "_gb_preview_vblank" if vblank_preview else "_gb_timed_halt"
            return [f"ld hl, 0x{(pc + 1) & 0xffff:04x}", f"jp {target}"], None
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
            return call_sequence(address, return_address, native_target, native_resume), None
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
                    *call_sequence(address, fallthrough, native_target, native_resume), "1:",
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
