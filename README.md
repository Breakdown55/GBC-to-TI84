# GBC to TI-84

This prototype recompiles Game Boy and Game Boy Color cartridge code into native eZ80 code for the TI-84 Plus CE. It discovers code from the cartridge entry point and generates its own bank-aware jump table.

## Build and run

Use Python 3 and the [CE C/C++ toolchain](https://ce-programming.github.io/toolchain/) with GNU assembler support. From a CE toolchain shell, run:

```sh
python native-test/recompile.py input.gbc output.8xp
```

Outside a toolchain shell, pass `--cedev` with the installed CEdev directory. Send the resulting file to the transfer destination printed by the builder and run `GBCRUN` on an OS setup that supports native programs.

Large builds use a compressed launcher and must be sent to **Archive**. The launcher checks the program's archive status and available RAM before unpacking.

The prototype supports a subset of Game Boy instructions, ROM-only and MBC5 cartridges, and Color-mode video RAM and work RAM banking. MBC5 save RAM starts erased on each run and uses a bounded in-memory store.

Unsupported instructions are reported with their bank and address. Use `--entry BANK:ADDRESS` in hex to include a dynamic destination the compiler cannot discover. Use `--trap-unsupported` to defer unsupported-opcode errors to runtime and see which one the program actually reaches.

Use `--stop-at BANK:ADDRESS` to stop before executing a reachable instruction. Use `--watch ADDRESS` to show a byte of video RAM, work RAM, or high RAM when the program stops. The watch uses the bank selected at that point.

`--timing` enables instruction-boundary LCD and timer timing and interrupt delivery. Include interrupt vectors with `--entry`, optionally supplying the stack pointer as `BANK:ADDRESS:SP`. `--frame` displays a snapshot of the guest's background, window, and sprites on a normal stop.

`--vblank-preview [FRAMES]` is a graphics diagnostic. It advances to VBlank when the guest waits at `HALT` and displays the result after the requested number of handlers return. For routines copied to high RAM, `--ram-code START:BANK:SOURCE:LENGTH` supplies the ROM bytes used to translate and validate the executable copy. All four fields are hexadecimal.

Audio support is limited to register readback and power control.

## Regression test

Generate and compile a synthetic cartridge to test bank switching, calls, returns, branches, and a work RAM write.

```sh
python native-test/test_rom.py --write native-test/bin/synthetic.gbc
python native-test/recompile.py native-test/bin/synthetic.gbc native-test/bin/GBCRUN.8xp --watch c123
```

The expected result is `HALT` and `RAM C123=A5`. Run `python -m unittest discover -s native-test` for the compiler regression tests.
