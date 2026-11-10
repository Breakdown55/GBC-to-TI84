"""Build a TI-84 Plus CE program from a supported Game Boy cartridge image."""

import argparse
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

from rom import compile_rom


PROJECT = Path(__file__).resolve().parent
EXECUTABLE = ".exe" if os.name == "nt" else ""
LOAD_ADDRESS = 0xd1a87f
DISPLAY_RAM = 0xd40000


def toolchain_path(explicit):
    if explicit is not None:
        return explicit.resolve()
    config = shutil.which("cedev-config")
    if config is None:
        raise ValueError("set --cedev to the installed CEdev directory or use a CEdev shell")
    prefix = subprocess.check_output([config, "--prefix"], text=True).strip()
    return Path(prefix).resolve()


def guard_compressed_program(run, work):
    path = work / "program.8xp"
    program = bytearray(path.read_bytes())
    payload_start = 0x4a
    if (len(program) < payload_start + 2 or program[:8] != b"**TI83F*"
            or struct.unpack_from("<H", program, 0x37)[0] != 13
            or program[0x3b] not in (5, 6)):
        raise ValueError("expected one TI program variable")
    payload = bytearray(program[payload_start:-2])
    sizes = ((0x35, len(payload) + 19), (0x39, len(payload) + 2),
             (0x46, len(payload) + 2), (0x48, len(payload)))
    if any(struct.unpack_from("<H", program, offset)[0] != size for offset, size in sizes):
        raise ValueError("invalid TI program variable lengths")
    if payload[:4] != b"\xef\x7b\x00\x21" or len(payload) < 7:
        raise ValueError("unrecognized compressed CE launcher")
    stub_address = int.from_bytes(payload[4:7], "little")
    stub_offset = stub_address - LOAD_ADDRESS
    if (not 7 <= stub_offset <= len(payload) - 9 or payload[stub_offset] != 0x21
            or payload[stub_offset + 4:stub_offset + 9] != b"\xe5\xcd\x2c\x07\x02"):
        raise ValueError("unrecognized compressed CE launcher memory check")
    expansion = int.from_bytes(payload[stub_offset + 1:stub_offset + 4], "little")
    guard_address = LOAD_ADDRESS + len(payload)
    run("binutils/bin/z80-none-elf-as", "-march=ez80+full", PROJECT / "archive_guard.S",
        "-o", "guard.o")
    run("binutils/bin/z80-none-elf-ld", "-Ttext", f"0x{guard_address:x}", "--entry=guard",
        f"--defsym=guard_source=0x{stub_address:x}", f"--defsym=guard_resume=0x{LOAD_ADDRESS + 7:x}",
        f"--defsym=guard_required={expansion + 256}", "guard.o", "-o", "guard.elf")
    run("binutils/bin/z80-none-elf-objcopy", "-O", "binary", "guard.elf", "guard.bin")
    payload[3:7] = b"\xc3" + guard_address.to_bytes(3, "little")
    payload.extend((work / "guard.bin").read_bytes())
    payload.extend(program[0x3b:0x44])
    if len(payload) > 0xffff - 19:
        raise ValueError("guarded program exceeds the TI variable file limit")
    if LOAD_ADDRESS + len(payload) + expansion > DISPLAY_RAM:
        raise ValueError("the compressed launcher would expand into calculator display RAM")
    program[0x45] = 0x80
    for offset, size in ((0x35, len(payload) + 19), (0x39, len(payload) + 2),
                         (0x46, len(payload) + 2), (0x48, len(payload))):
        struct.pack_into("<H", program, offset, size)
    program = program[:payload_start] + payload
    program.extend((sum(program[0x37:]) & 0xffff).to_bytes(2, "little"))
    path.write_bytes(program)


def build(ce, work, name, show_frame=False):
    def run(tool, *args):
        subprocess.run([ce / f"{tool}{EXECUTABLE}", *args], cwd=work, check=True)

    clang = "bin/ez80-clang"
    assembler = "binutils/bin/z80-none-elf-as"
    linker = "binutils/bin/z80-none-elf-ld"
    lib = ce / "lib"
    core_libs = [
        lib / name for name in (
            "crt/libcrt_os.a", "ce/libce.a", "softfloat/libsoftfloat.a", "libc/libc_os.a",
        )
    ]
    objects = []
    sources = (
        ("rom_main", PROJECT / "rom_main.c"),
        ("runtime", PROJECT / "src/runtime.c"),
        ("timing", PROJECT / "src/timing.c"),
        ("render", PROJECT / "src/render.c"),
        ("vram_dma", PROJECT / "src/vram_dma.c"),
        ("targets", work / "targets.c"),
    )
    for unit, source in sources:
        run(clang, "-S", "-Oz", "-Wall", "-Wextra", "-fno-addrsig",
            "-ffunction-sections", "-fdata-sections", "-mllvm", "-z80-gas-style",
            "-D__TICE__=1", f"-DGB_SHOW_FRAME={int(show_frame)}", "-nostdinc",
            "-isystem", ce / "include", "-I", PROJECT / "src",
            source, "-o", f"{unit}.s")
        run(assembler, "-march=ez80+full", f"{unit}.s", "-o", f"{unit}.o")
        objects.append(f"{unit}.o")
    run(assembler, "-march=ez80+full", PROJECT / "src/dispatch.S", "-o", "dispatch.o")
    run(assembler, "-march=ez80+full", "translated.s", "-o", "translated.o")
    run(linker, "-i", "-static", "--entry=__start", "--build-id=none", "--gc-sections",
        "--omagic", "--defsym=__TICE__=1", "--defsym=NDEBUG=1",
        *objects, "dispatch.o", "translated.o", "--start-group", *core_libs,
        "--end-group", "-o", "program.o")
    libraries = sorted(path for path in (lib / "libload").glob("*.lib")
                       if path.name != "libload.lib")
    run("bin/cedev-obj", "--elf", "program.o", "--output", "crt.h",
        "--libs", *libraries, "--optional-libs")
    if "#define HAS_LIBLOAD 0" not in (work / "crt.h").read_text():
        raise ValueError("the generated program unexpectedly requires external calculator libraries")
    run(clang, "-I.", "-E", "-P", lib / "crt/crt0.S", "-o", "crt0.s")
    run(assembler, "-I.", "-march=ez80+full", "crt0.s", "-o", "crt0.o")
    run(linker, "-static", "--entry=__start", "--no-warn-rwx-segments", "--gc-sections",
        "--omagic", f"--defsym=LOAD_ADDR=0x{LOAD_ADDRESS:x}", "--defsym=BSSHEAP_LOW=0xD052C6",
        "--defsym=BSSHEAP_HIGH=0xD13FD8", "--defsym=STACK_HIGH=0xD1A87E", "--defsym=__TICE__=1",
        "-T", ce / "meta/linker_script.ld", "-Map=program.map", "program.o", "crt0.o",
        "--start-group", lib / "libc/allocator_standard.a", *core_libs,
        "--end-group", "-o", "program.obj")
    mapping = (work / "program.map").read_text()
    load_end = re.search(r"(?m)^\s*(0x[0-9a-fA-F]+)\s+___data_high\s*=", mapping)
    if load_end is None:
        raise ValueError("could not determine the linked program's end address")
    if int(load_end[1], 16) > DISPLAY_RAM:
        raise ValueError("the linked program would overlap calculator display RAM")
    convert_args = ("-u", "-n", name, "-j", "elf", "-i", "program.obj", "-o", "program.8xp")
    run("bin/convbin", "-k", "8xp", *convert_args)
    parts = list(work.glob("program.8xp.*.8xv"))
    if parts:
        # The split launcher depends on companion AppVars. Try a single compressed program.
        for part in parts:
            part.unlink()
        run("bin/convbin", "-k", "8xp-compressed", "-e", "zx7", "-r", *convert_args)
        if any(work.glob("program.8xp.*.8xv")):
            raise ValueError("generated program exceeds the single-file TI program limit")
        guard_compressed_program(run, work)
    return bool(parts)


def parse_entry(text):
    if not re.fullmatch(r"[0-9a-fA-F]{1,3}:[0-9a-fA-F]{1,4}", text):
        raise argparse.ArgumentTypeError("expected a hexadecimal BANK:ADDRESS pair")
    return tuple(int(part, 16) for part in text.split(":"))


def parse_seed(text):
    if not re.fullmatch(r"[0-9a-fA-F]{1,3}:[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4})?", text):
        raise argparse.ArgumentTypeError("expected hexadecimal BANK:ADDRESS or BANK:ADDRESS:SP")
    return tuple(int(part, 16) for part in text.split(":"))


def parse_ram_code(text):
    if not re.fullmatch(r"[0-9a-fA-F]{1,4}:[0-9a-fA-F]{1,3}:[0-9a-fA-F]{1,4}:[0-9a-fA-F]{1,4}", text):
        raise argparse.ArgumentTypeError("expected hexadecimal START:BANK:SOURCE:LENGTH")
    return tuple(int(part, 16) for part in text.split(":"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    parser.add_argument("output", type=Path, help="output .8xp path")
    parser.add_argument("--cedev", type=Path, help="path to the installed CEdev directory")
    parser.add_argument("--name", default="GBCRUN", help="calculator program name")
    parser.add_argument("--entry", type=parse_seed, action="append", default=[],
                        help="additional hexadecimal BANK:ADDRESS[:SP] entry for dynamic targets")
    parser.add_argument("--stop-at", type=parse_entry,
                        help="stop before executing this hexadecimal BANK:ADDRESS")
    clock = parser.add_mutually_exclusive_group()
    clock.add_argument("--timing", action="store_true", help="enable guest timing and interrupts")
    clock.add_argument("--vblank-preview", nargs="?", type=int, const=1, default=None, metavar="FRAMES",
                       help="serve this many VBlank interrupts at HALT and show the result (default: 1)")
    parser.add_argument("--frame", action="store_true", help="show the guest display on a normal stop")
    parser.add_argument("--ram-code", type=parse_ram_code, action="append", default=[],
                        help="translate code copied into HRAM from ROM, as START:BANK:SOURCE:LENGTH")
    parser.add_argument("--trap-unsupported", action="store_true",
                        help="build with runtime faults for unsupported opcodes")
    parser.add_argument("--watch", type=lambda value: int(value, 16), default=None,
                        help="hexadecimal VRAM, WRAM, or HRAM address to display after execution")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Z][A-Z0-9]{0,7}", args.name):
        parser.error("--name must start with A-Z and contain at most eight letters or digits")
    output = args.output.resolve()
    if output.suffix.lower() != ".8xp" or args.rom.resolve() in (output, output.with_suffix(".map")):
        parser.error("the .8xp and .map outputs must be separate from the input")
    if args.watch == 0:
        parser.error("--watch must be a VRAM, WRAM, or HRAM address")
    if args.vblank_preview is not None and not 1 <= args.vblank_preview <= 0xffffff:
        parser.error("--vblank-preview expects a positive 24-bit frame count")
    preview_frames = args.vblank_preview or 0
    try:
        assembly, table = compile_rom(args.rom.read_bytes(), args.entry, args.watch or 0, args.stop_at,
                                      timing=args.timing, trap_unsupported=args.trap_unsupported,
                                      ram_code=args.ram_code, vblank_preview=preview_frames)
        ce = toolchain_path(args.cedev)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gbc-build-", dir=output.parent) as directory:
            work = Path(directory)
            (work / "translated.s").write_text(assembly, encoding="utf-8")
            (work / "targets.c").write_text(table, encoding="utf-8")
            archived = build(ce, work, args.name, args.frame or bool(preview_frames))
            os.replace(work / "program.8xp", output)
            os.replace(work / "program.map", output.with_suffix(".map"))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")
    print(output)
    print(f"Transfer destination: {'Archive' if archived else 'RAM'}")


if __name__ == "__main__":
    main()
