"""Exercise retained rendering against fresh composition using a host LCD model."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class RenderCacheTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("gcc"), "host gcc is required for DMA tests")
    def test_dma_invalidates_changed_ranges_only(self):
        root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix="render-test-", dir=root) as directory:
            exe = Path(directory) / "dma-test.exe"
            subprocess.run(["gcc", "-std=c11", "-O2", "-Duint24_t=uintptr_t", "-I", str(root / "src"),
                            str(root / "tests/dma_invalidation.c"), str(root / "src/vram_dma.c"),
                            "-o", str(exe)], check=True)
            subprocess.run([str(exe)], check=True)

    @unittest.skipUnless(shutil.which("gcc"), "host gcc is required for renderer tests")
    def test_retained_frames_match_fresh_composition(self):
        root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix="render-test-", dir=root) as directory:
            obj = Path(directory) / "reference.o"
            exe = Path(directory) / "render-test.exe"
            flags = ["gcc", "-std=c11", "-O2", "-Duint24_t=uintptr_t", "-I", str(root / "src")]
            names = {"gb_oam": "reference_oam", "gb_video_init": "reference_init",
                     "gb_video_write": "reference_write", "gb_video_read": "reference_read",
                     "gb_render_scanline": "reference_line", "gb_video_invalidate": "reference_invalidate",
                     "gb_video_dirty": "reference_dirty", "gb_oam_dirty": "reference_oam_dirty"}
            subprocess.run(flags + [f"-D{k}={v}" for k, v in names.items()] +
                           ["-c", str(root / "src/render.c"), "-o", str(obj)], check=True)
            subprocess.run(flags + ["-D__TICE__", "-I", str(root / "tests/lcd"),
                           str(root / "tests/render_cache.c"), str(root / "src/render.c"),
                           str(obj), "-o", str(exe)], check=True)
            subprocess.run([str(exe)], check=True)
