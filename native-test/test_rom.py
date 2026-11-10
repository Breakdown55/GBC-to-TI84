"""Regression tests and a generated cartridge fixture for the ROM recompiler."""

from pathlib import Path
import sys
import unittest

from rom import BANK_SIZE, CHECKPOINT, Cartridge, compile_rom


def cartridge_image():
    data = bytearray([0xd3]) * (4 * BANK_SIZE)  # Unreachable data should never be decoded.
    data[0x104:0x150] = bytes(0x4c)
    data[0x134:0x13d] = b"GBCSTEST\x00"
    data[0x143] = 0xc0
    data[0x147] = 0x19
    data[0x148] = 1
    data[0x14d] = (-sum(data[0x134:0x14d]) - 0x19) & 0xff
    return data


def synthetic_rom():
    data = cartridge_image()
    data[0x100:0x103] = bytes.fromhex("c3 50 01")
    fixed = bytes.fromhex(
        "31 00 d0"  # Set SP.
        " 3e 01 ea 00 20 cd 00 40"  # Select bank 1 and call $4000.
        " fe 3c 20 09"  # Check the returned A and branch to the failure path.
        " 3e 02 ea 00 20 21 00 40 e9"  # Select bank 2 and jump through HL.
        " 3e ff ea 23 c1 76"  # Store a failure signature.
    )
    data[0x150:0x150 + len(fixed)] = fixed
    data[BANK_SIZE:BANK_SIZE + 3] = bytes.fromhex("3e 3c c9")
    bank2 = bytes.fromhex("3e a5 fe a5 28 02 3e fe ea 23 c1 76")
    data[2 * BANK_SIZE:2 * BANK_SIZE + len(bank2)] = bank2
    return data


def indirect_rom():
    data = cartridge_image()
    code = bytes.fromhex(
        "31 00 c1 3e 02 ea 00 20"  # Select bank 2 and set SP.
        " 3e 21 ea 00 c1 3e 43 ea 01 c1"  # Build the pointer in guest RAM.
        " e1 e9"  # POP HL, then JP HL.
    )
    data[0x100:0x100 + len(code)] = code
    data[2 * BANK_SIZE + 0x321] = 0x76
    return data


def color_rom():
    data = cartridge_image()
    data[0x100:0x103] = bytes.fromhex("c3 50 01")
    code = bytearray()

    def emit(sequence):
        code.extend(bytes.fromhex(sequence))

    def expect_zero_flag():
        emit("28 06 3e ff ea 23 c1 76")

    def expect_a(value):
        emit(f"fe {value:02x}")
        expect_zero_flag()

    emit("31 01 d0")  # Stack crosses the fixed and switchable WRAM boundary.
    emit("3e 01 ea 00 20 21 67 45 2a")  # Read from bank 1.
    expect_a(0x3c)
    emit("2a")  # HL advanced to the next byte.
    expect_a(0x76)
    emit("3e 02 ea 00 20 21 67 45 2a")  # Same address in bank 2.
    expect_a(0xa5)

    emit("af e0 4f 3e 31 ea 00 80")  # Write VRAM bank 0.
    emit("3e 01 e0 4f 3e 42 ea 00 80")  # Write VRAM bank 1.
    emit("21 00 80 2a")
    expect_a(0x42)
    emit("af e0 4f 21 00 80 2a")
    expect_a(0x31)
    emit("f0 4f")
    expect_a(0xfe)

    emit("3e 01 e0 70 3e 53 ea 01 d0")  # Write WRAM bank 1.
    emit("3e 02 e0 70 3e 64 ea 01 d0")  # Write WRAM bank 2.
    emit("21 01 d0 2a")
    expect_a(0x64)
    emit("af e0 70 21 01 d0 2a")  # Selector zero also maps bank 1.
    expect_a(0x53)
    emit("f0 70")
    expect_a(0xf8)

    emit("3e 01 e0 4d 10 00 f0 4d")
    expect_a(0xfe)
    emit("f5 3e 01 b7 f1")  # Preserve A and flags on the guest stack.
    expect_zero_flag()
    expect_a(0xfe)
    emit("cd 00 03")  # Exercise both conditional return opcodes.
    emit("3e a5 ea 23 c1 76")
    data[0x150:0x150 + len(code)] = code
    data[0x300:0x307] = bytes.fromhex("3e 12 fe 12 c0 c8 d3")
    data[BANK_SIZE + 0x567] = 0x3c
    data[BANK_SIZE + 0x568] = 0x76
    data[2 * BANK_SIZE + 0x567] = 0xa5
    return data


def frame_rom():
    data = cartridge_image()
    data[0x100:0x103] = bytes.fromhex("c3 50 01")
    data[0x40:0x46] = bytes.fromhex("3e a5 ea 23 c1 d9")  # VBlank handler.
    data[0x300:0x310] = bytes.fromhex("f0 00") * 8  # Each tile row is half red.
    code = bytearray.fromhex(
        "31 00 d0 f3 af e0 40 e0 0f"  # Disable LCD and clear pending interrupts.
        " 3e 01 e0 ff"  # Enable VBlank IRQ.
        " 3e 82 e0 68 3e 1f e0 69 af e0 69"  # BG palette 0, color 1 = red.
        " 3e 03 e0 51 af e0 52 e0 53 e0 54 e0 55"  # Copy one tile into VRAM with GDMA.
    )
    code.extend(bytes.fromhex("3e 22 ea 00 c0 3e c0 e0 46"))  # Copy a WRAM page into OAM.
    code.extend(bytes.fromhex("3e 91 e0 40 fb 00 76"))  # Turn on LCD, EI, NOP, HALT.
    data[0x150:0x150 + len(code)] = code
    return data, (0, 0x150 + len(code))


def restart_rom():
    data = cartridge_image()
    data[0x147:0x14a] = bytes.fromhex("1b 01 03")
    data[0x14d] = (-sum(data[0x134:0x14d]) - 0x19) & 0xff
    data[0x100:0x103] = bytes.fromhex("c3 50 01")
    code = bytes.fromhex("31 00 d0 3e 01 ea 00 20 df d3 ea 23 c1 76")
    data[0x150:0x150 + len(code)] = code
    vector = bytes.fromhex("e1 23 e5 3e 02 ea 00 20 c3 00 40")
    data[0x18:0x18 + len(vector)] = vector  # Skip the inline byte and change ROM banks.
    banked = bytes.fromhex(
        "3e 0a ea 00 00"  # Enable external RAM.
        " 21 00 a0 01 00 20 af 22 0b 78 b1 20 f9"  # Clear one SRAM bank.
        " 3e 01 ea 00 40 3e a5 ea 00 a1"  # Write bank 1.
        " 3e 02 ea 00 40 3e 5a ea 00 a1"  # Write bank 2.
        " 3e 01 ea 00 40 fa 00 a1 c9"  # Return the byte from bank 1.
    )
    data[2 * BANK_SIZE:2 * BANK_SIZE + len(banked)] = banked
    return data


def hram_rom():
    data = cartridge_image()
    data[0x100:0x103] = bytes.fromhex("c3 50 01")
    routine = bytes.fromhex("3e 00 ea 23 c1 c9")
    data[0x300:0x300 + len(routine)] = routine
    code = bytes.fromhex(
        "31 00 d0 21 00 03 0e 80 06 06"  # Copy the routine to HRAM.
        " 2a e2 0c 05 20 fa"
        " 3e a5 e0 81 cd 80 ff 76"  # Patch an immediate, call the copy, then HALT.
    )
    data[0x150:0x150 + len(code)] = code
    return data


def tilemap_rom():
    data = cartridge_image()
    data[0x100:0x103] = bytes.fromhex("c3 50 01")
    data[0x40:0x43] = bytes.fromhex("c3 00 03")
    data[0x600:0x610] = bytes.fromhex("f0 0f") * 8
    code = bytes.fromhex(
        "31 00 d0 f3 af e0 40 e0 0f 3e 01 e0 ff e0 4f"
        " 3e 06 e0 51 af e0 52 e0 53 3e 10 e0 54 af e0 55"
        " 3e 91 e0 40 fb 00 76"
    )
    data[0x150:0x150 + len(code)] = code

    handler = bytearray.fromhex(
        "3e 8a e0 68 3e ff e0 69 3e 03 e0 69 af e0 69 3e 7c e0 69"
        " af e0 4f 3e 01"
    )
    cells = [0x9800 + y * 32 + x for y in range(3, 8) for x in range(3, 9)]
    for address in cells:
        handler.extend((0xea, address & 0xff, address >> 8))
    handler.extend(bytes.fromhex("3e 01 e0 4f"))
    for address in cells:
        attributes = 0x09 | (0x20 if address & 1 else 0)
        handler.extend((0x3e, attributes, 0xea, address & 0xff, address >> 8))
    handler.append(0xd9)
    data[0x300:0x300 + len(handler)] = handler
    return data


class RomTest(unittest.TestCase):
    def test_frame_join_keeps_banking_stack_and_sram_distinct(self):
        from dataclasses import replace
        from rom import State, KnownMemory, merge_frame_states, SRAM_BASE
        states = [State(0x100, 1, stack=(1,2), memory=KnownMemory(((0xc000,0xc001,n),
                  (0xc001,0xc002,7),(SRAM_BASE,SRAM_BASE+1,3)))) for n in range(9)]
        joined = list(merge_frame_states(states))
        self.assertEqual(len(joined), 1)
        self.assertEqual(joined[0].memory, ((0xc001,0xc002,7),(SRAM_BASE,SRAM_BASE+1,3)))
        self.assertEqual(len(merge_frame_states([*states, replace(states[0], bank=2)])), 2)
        self.assertEqual(len(merge_frame_states([*states, replace(states[0], stack=(3,4))])), 2)

    def test_byte_fill_loop_fuses_only_without_instruction_timing(self):
        data = cartridge_image()
        code = bytes.fromhex("21 00 c0 0e 00 3e a5 22 0d 20 fc 76")
        data[0x100:0x100+len(code)] = code
        assembly, _ = compile_rom(data)
        self.assertEqual(assembly.count("call _gb_fill_hl"), 1)
        self.assertIn("jp z, .Lfill_slow_000_0107", assembly)
        timed, _ = compile_rom(data, timing=True, stop_at=(0,0x10b))
        self.assertNotIn("call _gb_fill_hl", timed)

    def test_unknown_fill_counter_has_only_256_possible_lengths(self):
        from rom import State
        data = cartridge_image()
        data[0x100:0x105] = bytes.fromhex("22 0d 20 fc 76")
        cartridge = Cartridge(data)
        state = State(0x100, 1, a=0xa5, hl=(0, 0xc0), c=None)
        successors, _ = cartridge.fold_fill(state, cartridge.decode(0,0x100), None)
        self.assertEqual(len(successors), 256)
        self.assertEqual({s.hl for s in successors}, {(n & 255, 0xc0 + (n >> 8)) for n in range(1,257)})
        self.assertTrue(all(s.c == 0 and s.z is True for s in successors))
        crossing = State(0x100, 1, a=0xa5, hl=(0xff, 0xfd), c=2)
        self.assertIsNone(cartridge.fold_fill(crossing, cartridge.decode(0,0x100), None))

    def test_reachable_code_and_banks(self):
        data = synthetic_rom()
        instructions = Cartridge(data).discover()
        self.assertNotIn((0, 0x168), instructions)  # Known untaken branch.
        self.assertIn((0, 0x15b), instructions)  # CALL continuation.
        self.assertIn((1, 0x4000), instructions)
        self.assertIn((2, 0x4000), instructions)
        self.assertNotIn((3, 0x4000), instructions)
        self.assertNotIn((0, 0x104), instructions)  # Cartridge header.
        assembly, targets = compile_rom(data, watch_address=0xc123)
        self.assertIn("jp _gb_call", assembly)
        self.assertIn("{0x4000, gb_002_4000}", targets)
        self.assertIn("rom_watch_address = 0xc123", targets)

    def test_unknown_zero_flag_keeps_both_branches(self):
        data = cartridge_image()
        data[0x100:0x107] = bytes.fromhex("f0 80 b7 20 01 76 76")
        instructions = Cartridge(data).discover()
        self.assertIn((0, 0x105), instructions)
        self.assertIn((0, 0x106), instructions)

    def test_known_high_ram_write_prunes_branch(self):
        data = cartridge_image()
        data[0x100:0x103] = bytes.fromhex("c3 50 01")
        code = bytes.fromhex("3e 01 0e a0 e2 f0 a0 b7 20 01 d3 76")
        data[0x150:0x150 + len(code)] = code
        instructions = Cartridge(data).discover()
        self.assertNotIn((0, 0x15a), instructions)
        self.assertIn((0, 0x15b), instructions)

    def test_unknown_high_address_invalidates_known_high_ram(self):
        data = cartridge_image()
        data[0x100:0x103] = bytes.fromhex("c3 50 01")
        code = bytes.fromhex("3e 01 e0 a0 af e2 f0 a0 b7 20 01 76 76")
        data[0x150:0x150 + len(code)] = code
        instructions = Cartridge(data).discover()
        self.assertIn((0, 0x15b), instructions)
        self.assertIn((0, 0x15c), instructions)

    def test_color_fixture_includes_data_from_each_cartridge_bank(self):
        assembly, targets = compile_rom(color_rom(), watch_address=0xc123)
        self.assertIn("{0x001, 0x4567, 0x0002, 0x000000}", targets)
        self.assertIn("{0x002, 0x4567, 0x0001, 0x000002}", targets)
        self.assertIn("0x3c, 0x76, 0xa5,", targets)
        self.assertIn("call _gb_fast_load_hli", assembly)
        self.assertIn("call _gb_stop", assembly)

    def test_counted_loop_stops_before_checkpoint_instruction(self):
        data = cartridge_image()
        data[0x100:0x10b] = bytes.fromhex("f3 af 0e 80 06 04 e2 0c 05 20 fb")
        instructions = Cartridge(data).discover(stop_at=(0, 0x10b))
        self.assertEqual(instructions[0, 0x10b].opcode, CHECKPOINT)
        self.assertNotIn((0, 0x10c), instructions)
        assembly, _ = compile_rom(data, stop_at=(0, 0x10b))
        self.assertIn("jp _gb_checkpoint", assembly)

    def test_checkpoint_must_be_reachable(self):
        data = cartridge_image()
        data[0x100] = 0x76
        with self.assertRaisesRegex(ValueError, "checkpoint .* is unreachable"):
            compile_rom(data, stop_at=(0, 0x101))

    def test_bank_remap_changes_the_next_instruction(self):
        data = cartridge_image()
        data[0x100:0x103] = bytes.fromhex("c3 00 40")
        data[BANK_SIZE:BANK_SIZE + 5] = bytes.fromhex("3e 02 ea 00 20")
        data[2 * BANK_SIZE + 5] = 0x76
        instructions = Cartridge(data).discover()
        self.assertIn((2, 0x4005), instructions)
        self.assertNotIn((1, 0x4005), instructions)

    def test_return_uses_bank_selected_by_the_callee(self):
        data = cartridge_image()
        data[0x100:0x106] = bytes.fromhex("31 00 d0 c3 00 40")
        data[BANK_SIZE:BANK_SIZE + 3] = bytes.fromhex("cd 00 02")
        data[0x200:0x206] = bytes.fromhex("3e 02 ea 00 20 c9")
        data[2 * BANK_SIZE + 3] = 0x76
        instructions = Cartridge(data).discover()
        self.assertIn((2, 0x4003), instructions)
        self.assertNotIn((1, 0x4003), instructions)

    def test_runtime_target_is_discovered_from_known_stack_bytes(self):
        self.assertIn((2, 0x4321), Cartridge(indirect_rom()).discover())

    def test_unknown_runtime_target_can_be_seeded(self):
        data = indirect_rom()
        data[0x108:0x10a] = bytes.fromhex("f0 80")
        self.assertNotIn((2, 0x4321), Cartridge(data).discover())
        self.assertIn((2, 0x4321), Cartridge(data).discover([(2, 0x4321)]))

    def test_return_from_stack_word_written_by_callee(self):
        data = cartridge_image()
        data[0x100:0x106] = bytes.fromhex("31 00 d0 cd 00 02")
        data[0x200:0x206] = bytes.fromhex("3e 20 ea fe cf c9")
        data[0x120] = 0x76
        instructions = Cartridge(data).discover()
        self.assertIn((0, 0x120), instructions)
        self.assertNotIn((0, 0x106), instructions)

    def test_reachable_unsupported_opcode_reports_its_address(self):
        data = synthetic_rom()
        data[2 * BANK_SIZE] = 0xd3
        with self.assertRaisesRegex(ValueError, r"\$002:\$4000: unsupported opcode \$D3"):
            compile_rom(data)

    def test_timed_halt_keeps_resume_and_interrupt_targets(self):
        data, checkpoint = frame_rom()
        assembly, targets = compile_rom(data, [(0, 0x40)], stop_at=checkpoint, timing=True)
        self.assertIn("call _gb_boundary", assembly)
        self.assertIn("jp _gb_reti", assembly)
        self.assertIn("gb_000_0040", targets)
        self.assertIn(f"gb_000_{checkpoint[1]:04x}", targets)
        self.assertIn("gb_rom_span_count = 1", targets)
        self.assertIn("{0x000, 0x0300, 0x0010, 0x000000}", targets)

    def test_unsupported_opcodes_can_be_deferred_to_a_runtime_fault(self):
        data = cartridge_image()
        assembly, targets = compile_rom(data, trap_unsupported=True)
        self.assertIn("jp _gb_unmapped", assembly)
        self.assertIn("gb_000_0100", targets)

    def test_restart_through_banked_target_skips_inline_byte(self):
        instructions = Cartridge(restart_rom()).discover()
        self.assertIn((2, 0x4000), instructions)
        self.assertIn((0, 0x15a), instructions)
        self.assertNotIn((0, 0x159), instructions)

    def test_copied_hram_code_uses_live_immediate_and_guards_opcode(self):
        data = hram_rom()
        assembly, targets = compile_rom(data, ram_code=[(0xff80, 0, 0x300, 6)])
        self.assertIn("gb_000_ff80", targets)
        self.assertIn("ld a, (_gb_hram+1)", assembly)
        self.assertIn("ld a, (_gb_hram+0)", assembly)

    def test_vblank_preview_uses_live_state_and_stops_at_reti(self):
        data, _ = frame_rom()
        assembly, targets = compile_rom(data, vblank_preview=True)
        self.assertIn("jp _gb_preview_vblank", assembly)
        self.assertIn("jp _gb_preview_reti", assembly)
        self.assertIn("gb_000_0040", targets)

    def test_multi_frame_preview_reaches_the_next_halt(self):
        data, (_, resume) = frame_rom()
        data[resume] = 0x76
        _, single = compile_rom(data, vblank_preview=1)
        assembly, multiple = compile_rom(data, vblank_preview=2)
        self.assertNotIn(f"gb_000_{resume:04x}", single)
        self.assertIn(f"gb_000_{resume:04x}", multiple)
        self.assertEqual(assembly.count("jp _gb_preview_vblank"), 2)
        self.assertIn("rom_preview_frames = 2", multiple)

    def test_continuous_vblank_has_no_runtime_frame_limit(self):
        data, (_, resume) = frame_rom()
        data[resume:resume + 4] = bytes((0x76, 0xc3, resume & 255, resume >> 8))
        assembly, targets = compile_rom(data, vblank_preview=3, continuous=True)
        self.assertIn("rom_preview_frames = 0", targets)
        self.assertIn("jp _gb_preview_vblank", assembly)
        self.assertIn(f"gb_000_{resume:04x}", targets)
        self.assertNotIn("jp _gb_checkpoint", assembly)

    def test_native_arithmetic_keeps_carry_setup_and_compare_accumulator(self):
        from translate import lower_instruction
        adc, size = lower_instruction("adc a, b")
        self.assertEqual(size, 1)
        self.assertIn("add a, 0xf0", adc)
        self.assertIn("adc a, c", adc)
        self.assertIn("call _gb_native_flags", adc)
        compare, _ = lower_instruction("cp $ff")
        self.assertNotIn("ld (_gb_a), a", compare)
        self.assertIn("cp a, 0xff", compare)
        decrement, _ = lower_instruction("dec h")
        self.assertIn("call _gb_native_step_flags", decrement)

    def test_preview_shares_repeated_native_bodies(self):
        data = cartridge_image()
        data[0x100:0x106] = bytes.fromhex("06 05 78 78 78 76")
        assembly, _ = compile_rom(data, stop_at=(0, 0x105), vblank_preview=1)
        self.assertEqual(assembly.count("call .Lshared_"), 3)
        self.assertIn("ld a, (_gb_bc+1)\n    ld (_gb_a), a\n    ret", assembly)

    def test_preview_shared_fault_discards_the_native_return_address(self):
        data = cartridge_image()
        data[0x100:0x107] = bytes.fromhex("01 a0 fe 0a 0a 0a 76")
        assembly, _ = compile_rom(data, stop_at=(0, 0x106), vblank_preview=1)
        self.assertIn("jp nz, .Lshared_fault", assembly)
        self.assertIn(".Lshared_fault:\n    pop de\n    jp _gb_unmapped", assembly)

    def test_rom_spans_keep_small_gaps_as_cartridge_bytes(self):
        data = cartridge_image()
        data[0x100:0x107] = bytes.fromhex("fa 00 03 fa 05 03 76")
        data[0x300:0x306] = bytes(range(6))
        _, targets = compile_rom(data)
        self.assertIn("{0x000, 0x0300, 0x0006, 0x000000}", targets)
        self.assertIn("0x00, 0x01, 0x02, 0x03, 0x04, 0x05,", targets)

    def test_fixed_memory_access_keeps_timing_and_banked_lookup(self):
        data = cartridge_image()
        code = bytes.fromhex("fa 00 03 ea 23 c1 fa 23 e1 e0 80 f0 80 fa 00 40 76")
        data[0x100:0x100 + len(code)] = code
        data[0x300] = 0xa5
        assembly, _ = compile_rom(data, stop_at=(0, 0x100 + len(code)), timing=True)
        self.assertIn("call _gb_boundary\n    ld a, 0xa5\n    ld (_gb_a), a", assembly)
        self.assertIn("ld (_gb_wram+291), a", assembly)
        self.assertIn("ld a, (_gb_wram+291)", assembly)
        self.assertIn("ld (_gb_hram+0), a", assembly)
        self.assertIn("ld a, (_gb_hram+0)", assembly)
        self.assertIn("ld hl, 0x4000\n    push hl\n    call _gb_load_a", assembly)

    def test_timed_calls_only_bypass_dispatch_for_known_targets(self):
        data = cartridge_image()
        data[0x100:0x108] = bytes.fromhex("af cc 00 02 cd 00 40 76")
        data[0x200] = data[BANK_SIZE] = 0xc9
        assembly, _ = compile_rom(data, stop_at=(0, 0x108), timing=True)
        self.assertIn("ld a, 12\n    ld (_gb_timing_extra), a\n"
                      "    ld hl, _gb_000_0200\n    ld de, 0x0104\n    jp _gb_call_native", assembly)
        self.assertIn("ld hl, 0x4000\n    ld de, 0x0107\n    jp _gb_call\n", assembly)

    def test_live_input_keeps_the_pressed_branch_after_the_delay(self):
        data, (_, resume) = frame_rom()
        # Select buttons, then skip the marker write when A is released.
        data[0x40:0x50] = bytes.fromhex("3e 10 e0 00 f0 00 e6 01 20 05 3e a5 ea 23 c1 d9")
        data[resume] = 0x76
        cartridge = Cartridge(data)
        self.assertNotIn((0, 0x4a), cartridge.discover(vblank_preview=1, input_after=1))
        self.assertIn((0, 0x4a), cartridge.discover(vblank_preview=2, input_after=1))

    def test_sound_register_readback_follows_power_and_read_masks(self):
        data = cartridge_image()
        code = bytes.fromhex(
            "3e 80 e0 11 f0 11 fe bf 20 10"  # Read written bits with the mask applied.
            " af e0 26 3e 80 e0 11 f0 11 fe 3f 20 03"  # Power off resets and blocks writes.
            " 76 00 00 d3"
        )
        data[0x100:0x100 + len(code)] = code
        instructions = Cartridge(data).discover()
        self.assertIn((0, 0x117), instructions)

    def test_push_and_pop_af_preserve_known_flags(self):
        data = cartridge_image()
        # INC makes Z known while carry is still unknown. Restore Z after XOR changes it.
        code = bytes.fromhex("31 00 d0 3e 00 3c f5 af f1 28 01 76 d3")
        data[0x100:0x100 + len(code)] = code
        instructions = Cartridge(data).discover()
        self.assertIn((0, 0x10b), instructions)

    def test_tilemap_preview_retains_banked_tile_data(self):
        assembly, targets = compile_rom(tilemap_rom(), vblank_preview=1)
        self.assertIn("jp _gb_preview_reti", assembly)
        self.assertIn("{0x000, 0x0600, 0x0010, 0x000000}", targets)


if __name__ == "__main__":
    fixtures = {"--write": synthetic_rom, "--write-color": color_rom,
                "--write-frame": lambda: frame_rom()[0], "--write-rst": restart_rom,
                "--write-hram": hram_rom, "--write-tilemap": tilemap_rom}
    if len(sys.argv) == 3 and sys.argv[1] in fixtures:
        output = Path(sys.argv[2])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(fixtures[sys.argv[1]]())
    else:
        unittest.main()
