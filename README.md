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

`--live` presents frames and reads the keypad at VBlank. Use the arrow keys for the D-pad, `2nd` for A, `Alpha` for B, `Del` for Select, `Enter` for Start, and `Clear` to exit. Live builds default to instruction-boundary timing. With `--vblank-preview`, they use the same HALT-driven frame limit as the diagnostic. `--input-after N` starts passing buttons to the guest after the first N VBlanks.

`--vblank-preview [FRAMES]` is a graphics diagnostic. It advances to VBlank when the guest waits at `HALT` and displays the result after the requested number of handlers return. For routines copied to high RAM, `--ram-code START:BANK:SOURCE:LENGTH` supplies the ROM bytes used to translate and validate the executable copy. All four fields are hexadecimal.

For continuous HALT-driven execution, use `--live --vblank-run`. It has no runtime frame limit and exits with `Clear`. `--analysis-frames N` controls how many VBlanks the compiler explores, independently of execution. This mode uses approximate timing, like the preview; use `--live` without either VBlank option for instruction-boundary timing. Static discovery is still incomplete for arbitrary inputs and dynamic destinations, so execution can report an unsupported instruction or missing target beyond the compiled paths. A larger analysis horizon does not guarantee complete game coverage. `--analysis-progress` reports progress during long compilations.

`--frame-skip 1` presents every other guest frame while keeping input polling and guest interrupts on every frame. At full guest speed this corresponds to about 30 presentations per second. Frame skipping alone does not make guest execution real-time.

`--archive-rom` packages the complete cartridge into AppVars slightly smaller than 32 KiB in an adjacent `NAME-data` directory, allowing two chunks plus their headers to fit an archive sector. Send **all `.8xv` files to Archive**, then send the program to the destination printed by the builder. Frequently used ROM data stays in RAM; reads outside the discovered data ranges fall back to these archived chunks. The program checks that every chunk is present, archived, and belongs to its cartridge before starting. This prevents later data reads from failing just because they were outside the analysis horizon; it does not translate undiscovered code. The AppVars contain cartridge data and must not be committed or distributed without permission.

For a smaller standalone build, repeat `--rom-data BANK:ADDRESS:LENGTH` to embed particular hexadecimal ROM ranges alongside discovered data. Each range must fit within a single 16 KiB ROM window.

To keep larger data ranges in Archive, repeat `--archive-bank BANK` with the hexadecimal bank numbers. Transfer the generated bank AppVars to **Archive**. Reads outside embedded data and the selected banks can still fault.

Renderer optimizations are automatic for every cartridge the compiler supports. The calculator displays 8-bit palette indices, so palette changes can update colors without redrawing pixels. A decoded-tile cache covers both video RAM banks, and a retained background layer avoids rebuilding the background beneath moving sprites. Background updates resolve map entries once per tile rectangle. CPU stores and DMA transfers invalidate the affected tiles and map cells. Changes to scrolling or display layout rebuild the background. Sprite composition preserves transparency, priority, flips, and the ten-sprites-per-scanline limit.

The graphics buffers occupy the unused 76,800-byte half of LCD memory while the indexed display is active. During a background rebuild, the renderer also reuses tiles with their palette, priority, and horizontal flip already applied. Video remains frame-based, so mid-frame effects have limited accuracy.

The compiler also packs nearby cartridge data ranges together and shares repeated native instruction sequences in VBlank preview builds. It makes those decisions from the input ROM and generated code.

Register arithmetic uses native eZ80 operations with Game Boy flag conversion. Plain WRAM and HRAM accesses have native fast paths, while mapped devices retain their memory handlers. In untimed modes, recognized byte-fill loops can use bulk fills when the entire destination is safe; timed builds retain individual instruction boundaries. Static analysis also summarizes these bounded fills and can merge compatible RAM facts between frames during continuous discovery.

For a cartridge that waits at `HALT` for VBlank, build a live preview with:

```sh
python native-test/recompile.py input.gbc output.8xp --live --vblank-preview 60
```

Choose the frame limit and any `--entry` or `--ram-code` values for the ROM you are building. To compare speed across compiler revisions, use the same ROM, toolchain version, build options, and emulator speed for both builds. The gain depends on how often the cartridge changes its display state.

The performance target is 30 displayed frames per second during active gameplay on a TI-84 Plus CE. That gives the translated game code and renderer about 33.3 ms per displayed frame. The target assumes real-time guest execution with game timing and input sampling independent of display updates.

Audio support is limited to register readback and power control.

## Regression test

Generate and compile a synthetic cartridge to test bank switching, calls, returns, branches, and a work RAM write.

```sh
python native-test/test_rom.py --write native-test/bin/synthetic.gbc
python native-test/recompile.py native-test/bin/synthetic.gbc native-test/bin/GBCRUN.8xp --watch c123
```

The expected result is `HALT` and `RAM C123=A5`. Run `python -m unittest discover -s native-test` for the regression tests. When host GCC is available, the suite also compares retained rendering with fresh scanline composition across scrolling, VRAM changes, sprites, palettes, and display re-entry.
