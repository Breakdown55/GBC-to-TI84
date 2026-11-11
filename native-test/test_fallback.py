"""Compare the missing-code executor with the independent cartridge simulator."""
import ctypes
from pathlib import Path
import random
import re
import shutil
import subprocess
import tempfile
import unittest

from rom import (BANK_SIZE, Cartridge, State, compile_rom, join_word, word_bytes,
                 remember_byte, EMPTY_MEMORY)


@unittest.skipUnless(shutil.which("gcc"), "host GCC is required")
class FallbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent
        cls.temporary = tempfile.TemporaryDirectory(prefix="fallback-test-", dir=cls.root)
        library = Path(cls.temporary.name) / "fallback.dll"
        sources = [cls.root / "tests/fallback_fixture.c"] + [
            cls.root / "src" / name for name in
            ("fallback.c", "runtime.c", "timing.c", "render.c", "vram_dma.c")]
        subprocess.run(["gcc", "-shared", "-std=c11", "-O2", "-Wall", "-Wextra",
                        "-Duint24_t=uint32_t", "-DGB_FALLBACK=1", "-DGB_ROM_CHUNKS=1",
                        "-I", str(cls.root / "src"), *map(str, sources), "-o", str(library)], check=True)
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.gb_fallback_run.restype = ctypes.c_void_p
        cls.lib.gb_read_byte.restype = ctypes.c_uint32
        cls.rom = (ctypes.c_uint8 * 0x10000).in_dll(cls.lib, "test_rom")

    @classmethod
    def tearDownClass(cls):
        # Windows keeps loaded DLLs locked until explicitly released.
        if hasattr(ctypes, "windll"):
            ctypes.windll.kernel32.FreeLibrary(ctypes.c_void_p(cls.lib._handle))
        cls.temporary.cleanup()

    def get(self, name):
        kind = ctypes.c_uint8 if name in ("a", "f", "run_status", "ime", "timing_enabled") else ctypes.c_uint32
        return kind.in_dll(self.lib, "gb_" + name).value

    def put(self, name, value):
        kind = ctypes.c_uint8 if name in ("a", "f", "run_status", "ime", "timing_enabled") else ctypes.c_uint32
        kind.in_dll(self.lib, "gb_" + name).value = value

    def setUp(self):
        self.lib.reset_fixture()

    def test_banked_call_return_and_native_reentry(self):
        # Two layouts and different banks ensure there are no title-specific roots.
        for bank, address in ((2, 0x4567), (3, 0x61a0)):
            self.lib.reset_fixture()
            self.rom[0x150:0x15e] = bytes.fromhex(
                f"3e {bank:02x} ea 00 20 cd {address & 255:02x} {address >> 8:02x} c3 3f 00 00 00 00")
            offset = bank * BANK_SIZE + address % BANK_SIZE
            self.rom[offset:offset + 6] = bytes.fromhex("3e a5 ea 23 c1 c9")
            self.assertTrue(self.lib.gb_fallback_run())
            self.assertEqual(self.get("pc"), 0x3f)
            self.assertEqual(self.get("sp"), 0xcff0)
            self.assertEqual(self.lib.gb_read_byte(0xc123), 0xa5)
            self.assertEqual(self.get("run_status"), 0)

    def test_restart_can_consume_inline_data(self):
        for vector, opcode in ((0x10, 0xd7), (0x28, 0xef)):
            self.lib.reset_fixture()
            self.rom[0x150:0x155] = bytes((opcode, 0x5a, 0xc3, 0x3f, 0))
            # The callee advances the saved PC over its own inline argument.
            self.rom[vector:vector + 4] = bytes.fromhex("e1 2a e5 c9")
            self.assertTrue(self.lib.gb_fallback_run())
            self.assertEqual(self.get("pc"), 0x3f)
            self.assertEqual(self.get("sp"), 0xcff0)
            self.assertEqual(self.get("a"), 0x5a)

    def test_compiler_routes_unsupported_native_opcodes_to_fallback(self):
        data = bytearray(2 * BANK_SIZE)
        data[0x100] = 0x27  # DAA is implemented by fallback.
        assembly, table = compile_rom(data, fallback=True)
        self.assertIn("jp _gb_fallback_dispatch", assembly)
        self.assertIn("rom_preview_mode = 0", table)

    def test_compiler_can_keep_entry_bank_out_of_native_program(self):
        data = bytearray(2 * BANK_SIZE)
        data[0x100] = 0x76
        assembly, table = compile_rom(data, fallback=True, fallback_banks=(0,))
        self.assertIn("_gb_000_0100:", assembly)
        self.assertIn("jp _gb_dispatch", assembly)
        self.assertIn("gb_target_page_count = 0", table)
        with self.assertRaises(ValueError):
            compile_rom(data, fallback_banks=(0,))

    def test_all_cb_operations_against_simulator(self):
        data = bytearray(4 * BANK_SIZE)
        data[0x147:0x149] = bytes((0x19, 1))
        cart = Cartridge(data)
        for opcode in range(256):
            for value in (0, 1, 0x7f, 0x80, 0xff):
                self.lib.reset_fixture()
                self.rom[0x150:0x152] = bytes((0xcb, opcode))
                data[0x150:0x152] = bytes((0xcb, opcode))
                self.put("a", value)
                self.put("bc", value * 257)
                self.put("de", value * 257)
                self.put("hl", 0xc100)
                self.put("f", 0x10)
                self.lib.gb_write_byte(0xc100, value)
                state = State(0x150, 1, value, word_bytes(0xc100), sp=0xcff0,
                              b=value, c=value, de=word_bytes(value * 257),
                              z=False, carry=True, svbk=1, vbk=0,
                              memory=remember_byte(EMPTY_MEMORY, 0xc100, value))
                expected = list(cart.successors(state, cart.decode(0, 0x150), set()))[0]
                self.assertTrue(self.lib.gb_fallback_step(), hex(opcode))
                self.assertEqual(self.get("a"), expected.a, hex(opcode))
                self.assertEqual(self.get("bc"), join_word(expected.c, expected.b), hex(opcode))
                self.assertEqual(self.get("de"), join_word(*expected.de), hex(opcode))
                self.assertEqual(self.get("hl"), join_word(*expected.hl), hex(opcode))
                self.assertEqual(bool(self.get("f") & 0x80), expected.z, hex(opcode))
                self.assertEqual(bool(self.get("f") & 0x10), expected.carry, hex(opcode))
                self.assertEqual(self.lib.gb_read_byte(0xc100), cart.load_byte(expected, 0xc100, set()), hex(opcode))

    def test_base_operations_against_simulator(self):
        data = bytearray(4 * BANK_SIZE)
        data[0x147:0x149] = bytes((0x19, 1))
        cart = Cartridge(data)
        randomizer = random.Random(10412)
        for opcode in range(256):
            # HALT, STOP and RETI also interact with host scheduling and are tested separately.
            if opcode in (0x08, 0x10, 0x27, 0x76, 0xcb, 0xd9):
                continue
            data[0x150:0x153] = bytes((opcode, 0x10, 0xc1))
            try:
                instruction = cart.decode(0, 0x150)
            except ValueError:
                continue
            for _ in range(8):
                self.lib.reset_fixture()
                self.rom[0x150:0x153] = data[0x150:0x153]
                a, c, flags = [randomizer.randrange(256) for _ in range(3)]
                b = 0xc3  # Indirect BC accesses must use implemented guest memory.
                if opcode in (0xe2, 0xf2):
                    c = 0x90
                flags &= 0xf0
                self.put("a", a)
                self.put("bc", (b << 8) | c)
                self.put("f", flags)
                self.lib.gb_write_byte(0xc100, 0x3c)
                self.lib.gb_write_byte(0xc200, 0x42)
                self.lib.gb_write_byte(0xcff0, 0x23)
                self.lib.gb_write_byte(0xcff1, 0x01)
                memory = EMPTY_MEMORY
                for address, value in ((0xc100, 0x3c), (0xc200, 0x42), (0xcff0, 0x23), (0xcff1, 1)):
                    memory = remember_byte(memory, address, value)
                state = State(0x150, 1, a, word_bytes(0xc100), sp=0xcff0,
                              b=b, c=c, de=word_bytes(0xc200),
                              z=bool(flags & 0x80), carry=bool(flags & 0x10),
                              memory=memory, svbk=1, vbk=0)
                successors = list(cart.successors(state, instruction, set()))
                self.assertTrue(self.lib.gb_fallback_step(), hex(opcode))
                if not successors:
                    continue
                expected = successors[0]
                for name, value in (("a", expected.a), ("pc", expected.pc), ("sp", expected.sp),
                                    ("hl", join_word(*expected.hl)), ("de", join_word(*expected.de)),
                                    ("bc", join_word(expected.c, expected.b))):
                    if value is not None:
                        self.assertEqual(self.get(name), value, f"{opcode:02X} {name}")
                if expected.z is not None:
                    self.assertEqual(bool(self.get("f") & 0x80), expected.z, hex(opcode))
                if expected.carry is not None:
                    self.assertEqual(bool(self.get("f") & 0x10), expected.carry, hex(opcode))

    def test_decimal_adjust_stack_store_and_illegal_opcode(self):
        self.rom[0x150:0x156] = bytes.fromhex("08 ff cf 27 d3 00")
        self.assertTrue(self.lib.gb_fallback_step())
        self.assertEqual(self.lib.gb_read_byte(0xcfff), 0xf0)
        self.assertEqual(self.lib.gb_read_byte(0xd000), 0xcf)
        self.put("a", 0x9a)
        self.assertTrue(self.lib.gb_fallback_step())
        self.assertEqual(self.get("a"), 0)
        self.assertEqual(self.get("f"), 0x90)
        self.assertFalse(self.lib.gb_fallback_step())
        self.assertEqual(self.get("run_status"), 2)
        self.assertEqual(self.get("pc"), 0x154)

    def test_decimal_arithmetic(self):
        self.rom[0x150] = 0x27
        for first in range(100):
            for second in (0, 1, 9, 10, 49, 50, 90, 99):
                for subtraction in (False, True):
                    self.put("pc", 0x150)
                    self.put("a", ((first // 10) << 4) | (first % 10))
                    operand = ((second // 10) << 4) | (second % 10)
                    self.lib.gb_alu(2 if subtraction else 0, operand)
                    self.assertTrue(self.lib.gb_fallback_step())
                    total = first - second if subtraction else first + second
                    decimal = total % 100
                    self.assertEqual(self.get("a"), ((decimal // 10) << 4) | (decimal % 10))
                    self.assertEqual(bool(self.get("f") & 0x10), total < 0 or total >= 100)
                    self.assertEqual(bool(self.get("f") & 0x80), decimal == 0)

    def test_interrupt_delay_and_halt_reti(self):
        self.rom[0x150:0x153] = bytes.fromhex("fb 00 00")
        self.put("timing_enabled", 1)
        ctypes.c_uint8.in_dll(self.lib, "gb_ie").value = 1
        ctypes.c_uint8.in_dll(self.lib, "gb_if").value = 1
        self.assertTrue(self.lib.gb_fallback_step())
        self.assertEqual(self.get("ime"), 0)
        self.assertTrue(self.lib.gb_fallback_step())
        self.assertEqual(self.get("ime"), 0)
        self.assertTrue(self.lib.gb_fallback_step())
        self.assertEqual(self.get("pc"), 0x40)
        self.assertEqual(self.get("sp"), 0xcfee)
        self.assertEqual(self.lib.gb_read_byte(0xcfee), 0x52)
        self.assertEqual(self.lib.gb_read_byte(0xcfef), 1)

        self.lib.reset_fixture()
        self.rom[0x150] = 0x76
        self.rom[0x40] = 0xd9
        self.assertTrue(self.lib.gb_fallback_step())
        self.assertEqual(self.get("pc"), 0x40)
        self.assertEqual(self.get("sp"), 0xcfee)
        self.assertTrue(self.lib.gb_fallback_step())
        self.assertEqual(self.get("pc"), 0x151)
        self.assertEqual(self.get("sp"), 0xcff0)
        self.assertEqual(self.get("ime"), 1)

    def test_opcode_metadata_matches_native_decoder(self):
        text = (self.root / "src/fallback_opcodes.h").read_text()
        arrays = [list(map(int, re.findall(r"\d+", body)))
                  for body in re.findall(r"\[256\] = \{([^}]+)\}", text)]
        data = bytearray(2 * BANK_SIZE)
        cart = Cartridge(data)
        for opcode in range(256):
            data[0x150:0x153] = bytes((opcode, 0, 0))
            if opcode in (8, 0x27):
                continue
            try:
                instruction = cart.decode(0, 0x150)
            except ValueError:
                self.assertEqual(arrays[0][opcode], 0)
                continue
            self.assertEqual(arrays[0][opcode], instruction.size)
            self.assertEqual(arrays[1][opcode], instruction.tcycles())
