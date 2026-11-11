import unittest

from control_flow import InlineCallFormat, discover_branches
from rom import BANK_SIZE, Cartridge, compile_rom
from translate import lower_instruction


def cartridge():
    data = bytearray([0xd3]) * (4 * BANK_SIZE)
    data[0x104:0x150] = bytes(0x4c)
    data[0x147:0x149] = bytes((0x19, 1))
    data[0x100] = 0x76
    return data


class BranchDiscoveryTest(unittest.TestCase):
    def test_both_branch_arms_are_native_entries(self):
        data = cartridge()
        data[0x4000:0x400c] = bytes.fromhex("3e 00 fe 00 28 03 3e a5 c9 3e 5a c9")
        instructions, rom_bytes, targets = {}, set(), set()
        discover_branches(Cartridge(data), [(1, 0x4000)], instructions, rom_bytes, targets)
        self.assertIn((1, 0x4006), instructions)
        self.assertIn((1, 0x4009), instructions)
        self.assertIn((1, 0x4006), targets)
        self.assertIn((1, 0x4009), targets)
        self.assertNotIn((1, 0x4002), targets)

    def test_inline_calls_use_declared_layout_and_skip_argument_bytes(self):
        for vector, opcode, base in ((0x10, 0xd7, 0x4000), (0x28, 0xef, 0x4100)):
            data = cartridge()
            data[0x4000:0x4004] = bytes((opcode, 0x20, 2, 0xc9))
            data[vector:vector + 5] = bytes.fromhex("e1 23 23 e5 c9")
            table = 2 * BANK_SIZE + base % BANK_SIZE + 0x20
            data[table:table + 2] = bytes.fromhex("67 45")
            data[2 * BANK_SIZE + 0x567] = 0xc9
            instructions, rom_bytes, targets = {}, set(), set()
            discover_branches(Cartridge(data), [(1, 0x4000)], instructions, rom_bytes, targets,
                              {vector: InlineCallFormat(2, 1, 0, base)})
            self.assertIn((2, 0x4567), instructions)
            self.assertIn((1, 0x4003), targets)
            self.assertNotIn((1, 0x4001), instructions)
            self.assertNotIn((1, 0x4002), instructions)
            self.assertIn((1, 0x4001), rom_bytes)
            self.assertIn((2, base + 0x20), rom_bytes)

    def test_callback_arguments_work_with_different_registers_and_banks(self):
        for bank, load, register, address in ((1, 0x21, "hl", 0x4567), (2, 0x11, "de", 0x6123),
                                              (3, 0x01, "bc", 0x7010)):
            data = cartridge()
            start = bank * BANK_SIZE
            data[start:start + 7] = bytes((load, address & 255, address >> 8, 0xcd, 0, 3, 0xc9))
            data[0x300] = 0xc9
            data[start + address % BANK_SIZE] = 0xc9
            instructions = {}
            discover_branches(Cartridge(data), [(bank, 0x4000)], instructions, set(), set(),
                              callback_calls={0x300: register})
            self.assertIn((bank, address), instructions)

    def test_jump_through_constant_rom_pointer_table(self):
        data = cartridge()
        data[0x4000:0x4007] = bytes.fromhex("21 00 41 2a 66 6f e9")
        data[0x4100:0x4102] = bytes.fromhex("00 45")
        data[0x4500] = 0xc9
        instructions, rom_bytes = {}, set()
        discover_branches(Cartridge(data), [(1, 0x4000)], instructions, rom_bytes, set())
        self.assertIn((1, 0x4500), instructions)
        self.assertNotIn((1, 0x4100), instructions)
        self.assertIn((1, 0x4100), rom_bytes)
        self.assertIn((1, 0x4101), rom_bytes)

    def test_inline_jump_table_is_inferred_from_dispatcher_and_code_boundary(self):
        for vector, opcode in ((0, 0xc7), (0x18, 0xdf)):
            data = cartridge()
            data[vector:vector + 3] = bytes.fromhex("c3 00 03")
            data[0x300:0x30b] = bytes.fromhex("87 e1 85 6f 30 01 24 2a 66 6f e9")
            data[0x4000:0x4008] = bytes((opcode, 7, 0x40, 0x10, 0x40, 0x15, 0x40, 0xc9))
            data[0x4010] = data[0x4015] = 0xc9
            instructions, rom_bytes, targets = {}, set(), set()
            discover_branches(Cartridge(data), [(1, 0x4000)], instructions, rom_bytes, targets)
            self.assertTrue({(1, 0x4007), (1, 0x4010), (1, 0x4015)}.issubset(targets))
            for address in range(0x4001, 0x4007):
                self.assertNotIn((1, address), instructions)
                self.assertIn((1, address), rom_bytes)

    def test_overlapping_entries_do_not_change_normal_fallthrough(self):
        data = cartridge()
        data[0x100:0x103] = bytes.fromhex("c3 50 01")
        data[0x150:0x153] = bytes.fromhex("3e 3c 76")
        assembly, _ = compile_rom(data, branch_entries=[(0, 0x151)])
        normal = assembly.split("_gb_000_0150:")[1].split("_gb_000_0151:")[0]
        self.assertIn("ld hl, 0x0152", normal)
        self.assertIn("jp _gb_dispatch", normal)

    def test_native_lowering_covers_all_legal_base_opcodes(self):
        data = bytearray(2 * BANK_SIZE)
        cart = Cartridge(data)
        invalid = []
        for opcode in range(256):
            data[0x100:0x103] = bytes((opcode, 0, 0))
            try:
                instruction = cart.decode(0, 0x100)
                lower_instruction(instruction.text, 0x100)
            except ValueError:
                invalid.append(opcode)
        self.assertEqual(invalid, [0xd3, 0xdb, 0xdd, 0xe3, 0xe4, 0xeb, 0xec, 0xed, 0xf4, 0xfc, 0xfd])


if __name__ == "__main__":
    unittest.main()
