from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from native_overlays import CACHE_ADDRESS, CACHE_SIZE, linker_script, overlay_banks
from rom import BANK_SIZE, compile_rom


def overlay_rom():
    data = bytearray([0xd3]) * (4 * BANK_SIZE)
    data[0x104:0x150] = bytes(0x4c)
    data[0x147:0x149] = bytes((0x19, 1))
    data[0x100:0x103] = bytes.fromhex("c3 50 01")
    code = bytes.fromhex("31 00 c1 3e 01 ea 00 20 cd 00 40 76")
    data[0x150:0x150 + len(code)] = code
    helper = bytes.fromhex("3e 02 ea 00 20 cd 00 40 3e 01 ea 00 20 c9")
    data[0x300:0x300 + len(helper)] = helper
    bank1 = bytes.fromhex("cd 00 03 3e a5 ea 23 c1 c9")
    data[BANK_SIZE:BANK_SIZE + len(bank1)] = bank1
    data[2 * BANK_SIZE:2 * BANK_SIZE + 3] = bytes.fromhex("3e 5a c9")
    return data


class NativeOverlayTest(unittest.TestCase):
    def test_compiler_separates_code_and_indexes_banked_return_continuations(self):
        assembly, targets = compile_rom(overlay_rom(), stop_at=(0, 0x15b), vblank_preview=1,
                                        native_overlays=True)
        self.assertEqual(overlay_banks(assembly), [4, 8])
        self.assertIn('.section .gb_native_004,"ax",@progbits', assembly)
        self.assertIn('.section .gb_native_008,"ax",@progbits', assembly)
        self.assertIn("{0x4003, gb_001_4003}", targets)
        self.assertIn("gb_native_bank_count = 2", targets)
        self.assertIn("jp _gb_ret_predict", assembly)
        self.assertNotIn("_gb_fallback_dispatch", assembly)

    @unittest.skipUnless(shutil.which("gcc"), "host GCC is required")
    def test_loader_validates_images_and_reloads_evicted_return_bank(self):
        root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix="native-overlay-test-", dir=root) as directory:
            work = Path(directory)
            (work / "ti").mkdir()
            (work / "ti/vars.h").write_text(
                '#include <stdint.h>\n'
                'typedef struct {uint16_t size; uint8_t data[8192];} var_t;\n'
                'const var_t *os_GetAppVarData(const char *, int *);\n')
            environment = work / "environment.h"
            environment.write_text(
                '#include <stdint.h>\n#define test_cache ((uint8_t *)0xd2f000)\n'
                '#define GB_NATIVE_CACHE ((uintptr_t)test_cache)\n'
                '#define GB_NATIVE_CACHE_SIZE 8192\n#define GB_NATIVE_OVERLAYS 5\n')
            with environment.open("a") as stream:
                stream.write('#define GB_NATIVE_FIXED_PAGE_SHIFT 11\n'
                             '#define GB_NATIVE_BANK_PAGE_SHIFT 12\n'
                             '#define GB_NATIVE_FIXED_REGION 0x800\n')
            executable = work / "native-overlay.exe"
            subprocess.run(["gcc", "-std=c11", "-O2", "-D_DEFAULT_SOURCE", "-Duint24_t=uint32_t",
                            "-include", str(environment), "-I", str(work), "-I", str(root / "src"),
                            str(root / "tests/native_overlay.c"), str(root / "src/native_overlay.c"),
                            "-o", str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_linker_layout_reserves_only_one_cache_for_every_bank(self):
        original = "___data_high = .;\n    .debug_info 0 : {}\n"
        script = linker_script(original, [1, 2, 0x101])
        self.assertEqual(script.count("OVERLAY"), 1)
        self.assertEqual(script.count(".gb_native_cache"), 1)
        self.assertIn(f"OVERLAY 0x{CACHE_ADDRESS:x}", script)
        self.assertIn(f". += 0x{CACHE_SIZE - 1:x}", script)
        self.assertIn("KEEP(*(.gb_native_101))", script)


if __name__ == "__main__":
    unittest.main()
