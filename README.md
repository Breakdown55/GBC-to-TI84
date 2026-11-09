# GBC to TI-84

This prototype translates small Game Boy assembly blocks into native eZ80 code for the TI-84 Plus CE. The runtime maps Game Boy ROM bank and address pairs to native code and keeps the Game Boy stack and registers in calculator RAM.

## Build and run

Use Python 3 and the [CE C/C++ toolchain](https://ce-programming.github.io/toolchain/) with GNU assembler support. From a CE toolchain shell, run:

```sh
make -C bank-switch-test
```

The build translates `control-flow.asm` and writes `bank-switch-test/bin/BANKTEST.8xp`. Send it to RAM in CEmu and run `BANKTEST` on an OS setup that supports native programs. The test uses TI-OS text routines and needs no separate calculator libraries.

## Current checkpoint

The test covers bank-aware jumps, Game Boy calls and returns, and conditional branches. It also runs the Mario Tennis startup from `$0100` through the first startup call and its nested memory-clear routine, then checks the return at `$2588`.

```text
BOOT 2588 FFFE=01 SP=D000
LCDC=11 WRAM CLEAR
PASS
```

The startup fixture uses a scanline model that advances when the game polls `LY`. The translator handles the instructions exercised by these fixtures and reports unsupported instructions as errors.
