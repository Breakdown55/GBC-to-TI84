"""Validate cartridge AppVars without distributing cartridge fixtures."""
import hashlib
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from rom_storage import BANK_CHUNK_SIZE, CHUNK_SIZE, cartridge_chunks, storage_metadata


class RomStorageTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("gcc"), "host gcc is required for archive mapping tests")
    def test_archive_validation_and_banked_read_boundaries(self):
        root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix="storage-test-", dir=root) as directory:
            work = Path(directory)
            (work / "ti").mkdir()
            (work / "ti/vars.h").write_text(
                '#include <stdint.h>\n'
                'typedef struct {uint16_t size; uint8_t data[32808];} var_t;\n'
                'var_t *os_GetAppVarData(const char *, int *);\n')
            exe = work / "storage.exe"
            subprocess.run(["gcc", "-std=c11", "-O2", "-Duint24_t=uint32_t",
                            "-DGB_ROM_CHUNKS=3", "-DGB_ROM_SIZE=69632",
                            f"-DGB_ROM_CHUNK_SIZE={CHUNK_SIZE}",
                            "-I", str(work), "-I", str(root / "src"),
                            str(root / "tests/rom_storage.c"), str(root / "src/rom_storage.c"),
                            "-o", str(exe)], check=True)
            subprocess.run([str(exe)], check=True)

    def test_chunks_reconstruct_cartridge_and_have_valid_ti_envelopes(self):
        data = bytes(range(256)) * 257 + b"end"
        chunks = list(cartridge_chunks(data))
        self.assertEqual(len(chunks), 3)
        restored = bytearray()
        for index, (name, file) in enumerate(chunks):
            self.assertEqual(file[:11], b"**TI83F*\x1a\x0a\0")
            entry = file[55:-2]
            self.assertEqual(struct.unpack_from("<H", file, 53)[0], len(entry))
            self.assertEqual(struct.unpack_from("<H", file, len(file)-2)[0], sum(entry) & 0xffff)
            self.assertEqual(struct.unpack_from("<H", entry)[0], 13)
            self.assertEqual(entry[4], 0x15)
            self.assertEqual(entry[5:13], name.encode("ascii"))
            self.assertEqual(len(name), 8)
            self.assertEqual(entry[14], 0x80)  # Archive destination.
            variable = entry[17:]
            self.assertEqual(struct.unpack_from("<H", entry, 2)[0], len(variable))
            self.assertEqual(struct.unpack_from("<H", entry, 15)[0], len(variable))
            body = variable[2:]
            self.assertEqual(struct.unpack_from("<H", variable)[0], len(body))
            self.assertEqual(body[:4], b"GBR1")
            self.assertEqual(body[4:36], hashlib.sha256(data).digest())
            self.assertEqual(struct.unpack_from("<HH", body, 36), (index, len(body)-40))
            self.assertLessEqual(len(body)-40, CHUNK_SIZE)
            restored.extend(body[40:])
        self.assertEqual(restored, data)

    def test_metadata_matches_packaged_names_and_digest(self):
        data = b"generic cartridge data"
        name, _ = next(cartridge_chunks(data))
        metadata = storage_metadata(data)
        self.assertIn('"' + name[:5] + '"', metadata)
        self.assertIn(','.join(map(str, hashlib.sha256(data).digest())), metadata)
        self.assertEqual(list(cartridge_chunks(data)), list(cartridge_chunks(data)))

    def test_selected_banks_keep_their_original_indices(self):
        data = b"".join(bytes([bank]) * BANK_CHUNK_SIZE for bank in range(8))
        chunks = list(cartridge_chunks(data, {2, 5}))
        self.assertEqual([name[5:] for name, _ in chunks], ["002", "005"])
        for (name, file), bank in zip(chunks, (2, 5)):
            self.assertTrue(name.startswith("B"))
            self.assertEqual(struct.unpack_from("<HH", file, 74 + 36), (bank, BANK_CHUNK_SIZE))
            self.assertEqual(file[74 + 40:-2], bytes([bank]) * BANK_CHUNK_SIZE)
        self.assertIn("gb_archive_banks[] = {36};", storage_metadata(data, {2, 5}))


if __name__ == "__main__":
    unittest.main()
