"""Regression tests and a generated cartridge fixture for the ROM recompiler."""

from pathlib import Path
import sys
import unittest

from rom import BANK_SIZE, Cartridge, compile_rom


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


class RomTest(unittest.TestCase):
    def test_reachable_code_and_banks(self):
        data = synthetic_rom()
        instructions = Cartridge(data).discover()
        self.assertIn((0, 0x168), instructions)  # Conditional failure path.
        self.assertIn((0, 0x15b), instructions)  # CALL continuation.
        self.assertIn((1, 0x4000), instructions)
        self.assertIn((2, 0x4000), instructions)
        self.assertNotIn((3, 0x4000), instructions)
        self.assertNotIn((0, 0x104), instructions)  # Cartridge header.
        assembly, targets = compile_rom(data, watch_address=0xc123)
        self.assertIn("jp _gb_call", assembly)
        self.assertIn("{0x002, 0x4000, gb_002_4000}", targets)
        self.assertIn("rom_watch_address = 0xc123", targets)

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

    def test_runtime_target_can_be_seeded(self):
        data = indirect_rom()
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


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--write":
        output = Path(sys.argv[2])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(synthetic_rom())
    else:
        unittest.main()
