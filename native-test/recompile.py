"""Build a TI-84 Plus CE program from a supported Game Boy cartridge image."""

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from rom import compile_rom


PROJECT = Path(__file__).resolve().parent
EXECUTABLE = ".exe" if os.name == "nt" else ""


def toolchain_path(explicit):
    if explicit is not None:
        return explicit.resolve()
    config = shutil.which("cedev-config")
    if config is None:
        raise ValueError("set --cedev to the installed CEdev directory or use a CEdev shell")
    prefix = subprocess.check_output([config, "--prefix"], text=True).strip()
    return Path(prefix).resolve()


def build(ce, work, name):
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
        ("targets", work / "targets.c"),
    )
    for unit, source in sources:
        run(clang, "-S", "-Oz", "-Wall", "-Wextra", "-fno-addrsig",
            "-ffunction-sections", "-fdata-sections", "-mllvm", "-z80-gas-style",
            "-D__TICE__=1", "-nostdinc", "-isystem", ce / "include", "-I", PROJECT / "src",
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
        "--omagic", "--defsym=LOAD_ADDR=0xD1A87F", "--defsym=BSSHEAP_LOW=0xD052C6",
        "--defsym=BSSHEAP_HIGH=0xD13FD8", "--defsym=STACK_HIGH=0xD1A87E", "--defsym=__TICE__=1",
        "-T", ce / "meta/linker_script.ld", "-Map=program.map", "program.o", "crt0.o",
        "--start-group", lib / "libc/allocator_standard.a", *core_libs,
        "--end-group", "-o", "program.obj")
    run("bin/convbin", "-k", "8xp", "-u", "-n", name, "-j", "elf", "-i", "program.obj",
        "-o", "program.8xp")


def parse_entry(text):
    if not re.fullmatch(r"[0-9a-fA-F]{1,3}:[0-9a-fA-F]{1,4}", text):
        raise argparse.ArgumentTypeError("expected a hexadecimal BANK:ADDRESS pair")
    return tuple(int(part, 16) for part in text.split(":"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    parser.add_argument("output", type=Path, help="output .8xp path")
    parser.add_argument("--cedev", type=Path, help="path to the installed CEdev directory")
    parser.add_argument("--name", default="GBCRUN", help="calculator program name")
    parser.add_argument("--entry", type=parse_entry, action="append", default=[],
                        help="additional hexadecimal BANK:ADDRESS entry for dynamic targets")
    parser.add_argument("--stop-at", type=parse_entry,
                        help="stop before executing this hexadecimal BANK:ADDRESS")
    parser.add_argument("--watch", type=lambda value: int(value, 16), default=None,
                        help="hexadecimal WRAM or HRAM address to display after execution")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Z][A-Z0-9]{0,7}", args.name):
        parser.error("--name must start with A-Z and contain at most eight letters or digits")
    output = args.output.resolve()
    if output.suffix.lower() != ".8xp" or args.rom.resolve() in (output, output.with_suffix(".map")):
        parser.error("the .8xp and .map outputs must be separate from the input")
    if args.watch == 0:
        parser.error("--watch must be a WRAM or HRAM address")
    try:
        assembly, table = compile_rom(args.rom.read_bytes(), args.entry, args.watch or 0, args.stop_at)
        ce = toolchain_path(args.cedev)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gbc-build-", dir=output.parent) as directory:
            work = Path(directory)
            (work / "translated.s").write_text(assembly, encoding="utf-8")
            (work / "targets.c").write_text(table, encoding="utf-8")
            build(ce, work, args.name)
            os.replace(work / "program.8xp", output)
            os.replace(work / "program.map", output.with_suffix(".map"))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")
    print(output)


if __name__ == "__main__":
    main()
