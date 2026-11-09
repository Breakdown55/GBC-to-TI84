# GBC to TI-84

This prototype translates Game Boy and Game Boy Color assembly blocks into native eZ80 code for the TI-84 Plus CE. It currently supports a small instruction subset and a simplified hardware model.

## Build and run

Use Python 3 and the [CE C/C++ toolchain](https://ce-programming.github.io/toolchain/) with GNU assembler support. From a CE toolchain shell, run:

```sh
make -C native-test
```

Send `native-test/bin/GBCTEST.8xp` to RAM in CEmu and run `GBCTEST` on an OS setup that supports native programs.

## Regression test

The synthetic test exercises bank-aware jumps, calls and returns, conditional branches, LCD polling, and RAM writes. It displays `PASS` when all checks succeed.
