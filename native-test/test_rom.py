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


class RomTest(unittest.TestCase):
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
        self.assertIn("{0x002, 0x4000, gb_002_4000}", targets)
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
        self.assertIn("call _gb_load_hli", assembly)
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


if __name__ == "__main__":
    fixtures = {"--write": synthetic_rom, "--write-color": color_rom,
                "--write-frame": lambda: frame_rom()[0], "--write-rst": restart_rom}
    if len(sys.argv) == 3 and sys.argv[1] in fixtures:
        output = Path(sys.argv[2])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(fixtures[sys.argv[1]]())
    else:
        unittest.main()
