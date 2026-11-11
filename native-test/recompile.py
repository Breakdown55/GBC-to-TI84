"""Build a TI-84 Plus CE program from a supported Game Boy cartridge image."""

import argparse
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

from rom import DEFAULT_ANALYSIS_LIMIT, compile_rom
from rom_storage import cartridge_chunks, storage_metadata, BANK_CHUNK_SIZE, CHUNK_SIZE


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


def build(ce, work, name, show_frame=False, input_after=None, frame_skip=0, archive_size=0,
          archive_banks=False, fallback=False):
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
    chunk_size = BANK_CHUNK_SIZE if archive_banks else CHUNK_SIZE
    sources = (
        ("rom_main", PROJECT / "rom_main.c"),
        ("runtime", PROJECT / "src/runtime.c"),
        ("timing", PROJECT / "src/timing.c"),
        ("render", PROJECT / "src/render.c"),
        ("vram_dma", PROJECT / "src/vram_dma.c"),
        ("rom_storage", PROJECT / "src/rom_storage.c"),
        ("fallback", PROJECT / "src/fallback.c"),
        ("targets", work / "targets.c"),
    )
    for unit, source in sources:
        run(clang, "-S", "-Oz", "-Wall", "-Wextra", "-fno-addrsig",
            "-ffunction-sections", "-fdata-sections", "-mllvm", "-z80-gas-style",
            "-D__TICE__=1", f"-DGB_SHOW_FRAME={int(show_frame)}",
            f"-DGB_LIVE={int(input_after is not None)}", f"-DGB_INPUT_AFTER={input_after or 0}", "-nostdinc",
            f"-DGB_FRAME_SKIP={frame_skip}",
            f"-DGB_FALLBACK={int(fallback)}",
            f"-DGB_ROM_CHUNKS={(archive_size + chunk_size - 1) // chunk_size}", f"-DGB_ROM_SIZE={archive_size}",
            f"-DGB_ROM_CHUNK_SIZE={chunk_size}", f"-DGB_ARCHIVE_BANKS={int(archive_banks)}",
            "-isystem", ce / "include", "-I", PROJECT / "src",
            source, "-o", f"{unit}.s")
        run(assembler, "-march=ez80+full", f"{unit}.s", "-o", f"{unit}.o")
        objects.append(f"{unit}.o")
    # GAS macro diagnostics reparse source paths; Windows backslashes become escapes.
    run(assembler, "-march=ez80+full", (PROJECT / "src/dispatch.S").as_posix(), "-o", "dispatch.o")
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
    if not parts:
        return False
    # The split launcher depends on companion AppVars. Try a single compressed program.
    for compression in ("zx7", "zx0"):
        for part in parts:
            part.unlink()
        run("bin/convbin", "-k", "8xp-compressed", "-e", compression, "-r", *convert_args)
        parts = list(work.glob("program.8xp.*.8xv"))
        if not parts:
            guard_compressed_program(run, work)
            return True
    raise ValueError("generated program exceeds the single-file TI program limit")


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

def parse_rom_data(text):
    if not re.fullmatch(r"[0-9a-fA-F]{1,3}:[0-9a-fA-F]{1,4}:[0-9a-fA-F]{1,4}", text):
        raise argparse.ArgumentTypeError("expected hexadecimal BANK:ADDRESS:LENGTH")
    return tuple(int(part, 16) for part in text.split(":"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    parser.add_argument("output", type=Path, help="output .8xp path")
    parser.add_argument("--cedev", type=Path, help="path to the installed CEdev directory")
    parser.add_argument("--name", default="GBCRUN", help="calculator program name")
    parser.add_argument("--entry", type=parse_seed, action="append", default=[],
                        help="additional hexadecimal BANK:ADDRESS[:SP] entry for dynamic targets")
    parser.add_argument("--fallback", action="store_true",
                        help="execute undiscovered instructions from archived ROM until native code resumes")
    parser.add_argument("--fallback-bank", action="append", default=[], type=lambda value: int(value, 16),
                        help="execute this hexadecimal bank through fallback to reduce resident native code")
    parser.add_argument("--stop-at", type=parse_entry,
                        help="stop before executing this hexadecimal BANK:ADDRESS")
    clock = parser.add_mutually_exclusive_group()
    clock.add_argument("--timing", action="store_true", help="enable guest timing and interrupts")
    clock.add_argument("--vblank-preview", nargs="?", type=int, const=1, default=None, metavar="FRAMES",
                       help="serve this many VBlank interrupts at HALT and show the result (default: 1)")
    clock.add_argument("--vblank-run", action="store_true",
                       help="run HALT-driven VBlanks without a frame limit (approximate timing)")
    parser.add_argument("--analysis-frames", type=int, default=1200,
                        help="static discovery horizon for --vblank-run, not an execution limit (default: 1200)")
    parser.add_argument("--frame-skip", type=int, default=0,
                        help="skip this many presentations between drawn frames; input and guest frames still run")
    parser.add_argument("--frame", action="store_true", help="show the guest display on a normal stop")
    parser.add_argument("--live", action="store_true", help="update the display and read keys at VBlank")
    parser.add_argument("--input-after", type=int, help="wait this many VBlanks before accepting guest buttons")
    parser.add_argument("--ram-code", type=parse_ram_code, action="append", default=[],
                        help="translate code copied into HRAM from ROM, as START:BANK:SOURCE:LENGTH")
    parser.add_argument("--trap-unsupported", action="store_true",
                        help="build with runtime faults for unsupported opcodes")
    parser.add_argument("--analysis-limit", type=int, default=DEFAULT_ANALYSIS_LIMIT,
                        help="maximum number of analysis states (default: %(default)s)")
    parser.add_argument("--analysis-progress", action="store_true", help="report static discovery progress")
    archive = parser.add_mutually_exclusive_group()
    archive.add_argument("--archive-rom", action="store_true",
                         help="package the full cartridge as archived AppVars")
    archive.add_argument("--archive-bank", action="append", type=lambda value: int(value, 16), metavar="BANK",
                         help="package one hexadecimal ROM bank as an archived AppVar; repeat for more banks")
    parser.add_argument("--offload-bank", action="append", default=[], type=lambda value: int(value, 16),
                        metavar="BANK", help="keep discovered data for an archived bank out of the native program")
    parser.add_argument("--rom-data", type=parse_rom_data, action="append", default=[],
                        help="retain a ROM data range in the program, as BANK:ADDRESS:LENGTH in hex")
    parser.add_argument("--watch", type=lambda value: int(value, 16), default=None,
                        help="hexadecimal VRAM, WRAM, or HRAM address to display after execution")
    args = parser.parse_args()
    if args.fallback and not args.archive_rom:
        parser.error("--fallback requires --archive-rom so every instruction byte is available")
    if args.fallback_bank and not args.fallback:
        parser.error("--fallback-bank requires --fallback")
    if not re.fullmatch(r"[A-Z][A-Z0-9]{0,7}", args.name):
        parser.error("--name must start with A-Z and contain at most eight letters or digits")
    output = args.output.resolve()
    if output.suffix.lower() != ".8xp" or args.rom.resolve() in (output, output.with_suffix(".map")):
        parser.error("the .8xp and .map outputs must be separate from the input")
    if args.watch == 0:
        parser.error("--watch must be a VRAM, WRAM, or HRAM address")
    if args.analysis_limit < 1:
        parser.error("--analysis-limit must be positive")
    if args.vblank_preview is not None and not 1 <= args.vblank_preview <= 0xffffff:
        parser.error("--vblank-preview expects a positive 24-bit frame count")
    if not 1 <= args.analysis_frames <= 0xffffff:
        parser.error("--analysis-frames must be from 1 to 16777215")
    if not 0 <= args.frame_skip <= 255:
        parser.error("--frame-skip must be from 0 to 255")
    if args.vblank_run and not args.live:
        parser.error("--vblank-run requires --live")
    if args.frame_skip and not args.live:
        parser.error("--frame-skip requires --live")
    preview_frames = args.analysis_frames if args.vblank_run else args.vblank_preview or 0
    if args.input_after is not None and (not args.live or not 0 <= args.input_after < 0xffffff):
        parser.error("--input-after requires --live and a frame count from 0 to 16777214")
    input_after = (args.input_after or 0) if args.live else None
    timing = args.timing or (args.live and not preview_frames)
    def progress(states, instructions, frames):
        print(f"Analysis: {states:,} states, {instructions:,} instructions, {frames} VBlanks", flush=True)
    try:
        cartridge_data = args.rom.read_bytes()
        archive_banks = set(args.archive_bank) if args.archive_bank else None
        if archive_banks is not None and any(not 0 <= bank < len(cartridge_data) // BANK_CHUNK_SIZE
                                             for bank in archive_banks):
            raise ValueError("archive bank is outside the cartridge ROM")
        use_archive = args.archive_rom or archive_banks is not None
        offload_banks = set(args.offload_bank)
        backed_banks = range(len(cartridge_data) // BANK_CHUNK_SIZE) if args.archive_rom else archive_banks or set()
        if not offload_banks.issubset(backed_banks):
            raise ValueError("--offload-bank requires the same bank in --archive-bank or --archive-rom")
        assembly, table = compile_rom(cartridge_data, args.entry, args.watch or 0, args.stop_at,
                                      timing=timing, trap_unsupported=args.trap_unsupported,
                                      ram_code=args.ram_code, vblank_preview=preview_frames,
                                      input_after=input_after, analysis_limit=args.analysis_limit,
                                      continuous=args.vblank_run,
                                      progress=progress if args.analysis_progress else None,
                                      rom_data=args.rom_data, offload_banks=offload_banks, fallback=args.fallback,
                                      fallback_banks=args.fallback_bank)
        if use_archive:
            table += storage_metadata(cartridge_data, archive_banks)
        ce = toolchain_path(args.cedev)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gbc-build-", dir=output.parent) as directory:
            work = Path(directory)
            (work / "translated.s").write_text(assembly, encoding="utf-8")
            (work / "targets.c").write_text(table, encoding="utf-8")
            archived = build(ce, work, args.name, args.frame or bool(preview_frames), input_after, args.frame_skip,
                             len(cartridge_data) if use_archive else 0, archive_banks is not None,
                             fallback=args.fallback)
            if use_archive:
                directory = output.parent / f"{args.name}-data"
                directory.mkdir(exist_ok=True)
                for name, contents in cartridge_chunks(cartridge_data, archive_banks):
                    staged = work / f"{name}.8xv"
                    staged.write_bytes(contents)
                    os.replace(staged, directory / staged.name)
            os.replace(work / "program.8xp", output)
            os.replace(work / "program.map", output.with_suffix(".map"))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")
    print(output)
    print(f"Transfer destination: {'Archive' if archived else 'RAM'}")
    if use_archive:
        print(f"Send every .8xv in {directory} to Archive before running.")


if __name__ == "__main__":
    main()
