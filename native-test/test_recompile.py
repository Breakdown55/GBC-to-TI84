from pathlib import Path
import struct
import tempfile
import unittest

from recompile import LOAD_ADDRESS, guard_compressed_program


class PackagingTest(unittest.TestCase):
    def test_compressed_program_gets_archive_guard_and_valid_lengths(self):
        source = LOAD_ADDRESS + 8
        payload = (b"\xef\x7b\x00\x21" + source.to_bytes(3, "little") + b"\x00"
                   + b"\x21\x00\x20\x00\xe5\xcd\x2c\x07\x02")
        header = bytearray(0x4a)
        header[:8] = b"**TI83F*"
        struct.pack_into("<H", header, 0x37, 13)
        header[0x3b:0x44] = b"\x06GBCTEST\x00"
        for offset, overhead in ((0x35, 19), (0x39, 2), (0x46, 2), (0x48, 0)):
            struct.pack_into("<H", header, offset, len(payload) + overhead)

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            work = Path(directory)
            path = work / "program.8xp"
            path.write_bytes(header + payload + b"\x00\x00")

            def build_guard(tool, *args):
                if tool.endswith("objcopy"):
                    (work / "guard.bin").write_bytes(b"\xc9")

            guard_compressed_program(build_guard, work)
            program = path.read_bytes()

        patched = program[0x4a:-2]
        self.assertEqual(program[0x45], 0x80)
        self.assertEqual(patched[3:7], b"\xc3" + (LOAD_ADDRESS + len(payload)).to_bytes(3, "little"))
        self.assertEqual(patched[7:], payload[7:] + b"\xc9\x06GBCTEST\x00")
        for offset, overhead in ((0x35, 19), (0x39, 2), (0x46, 2), (0x48, 0)):
            self.assertEqual(struct.unpack_from("<H", program, offset)[0], len(patched) + overhead)
        self.assertEqual(int.from_bytes(program[-2:], "little"), sum(program[0x37:-2]) & 0xffff)
