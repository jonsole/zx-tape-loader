# Testing the line-locked loader

`lineloader.s` only works if every chunk of it is exactly 224 T-states on every
path, stays clear of the ULA's contention on the paper lines, and draws the
border where `border.txt` says. Two harnesses check that by running it.

## `run_lineloader.py` - timing and decoding, on the `z80` package

```
pip install z80
sjasmplus --sym=output/lineloader.sym lineloader.s
python tests/run_lineloader.py                       # a screen and 2K of code
python tests/run_lineloader.py --flutter --jitter 0.15 --bytes 4000
python tests/run_lineloader.py --speed 1.12 --jitter 0.1
```

It runs the loader from `CODE_START` on the `z80` package's CPU, with the 48K
frame modelled around it (an interrupt every 69,888 T-states, a stand-in ROM
with `$F3` at `$0000` and `$FF` from `$3900`), against a tape synthesized with
`loader.py`, and fails unless

- every sample (the `IN` at the top of each chunk) comes exactly 224 T-states
  after the one before, for the whole load;
- no `IN` and no write to `$4000-$7FFF` falls while the ULA is fetching the
  paper;
- both blocks arrive byte for byte, and it jumps to the entry address.

The tape starts with the border block (`--border none` leaves it out, for a black border
throughout), so the runs cover the blank border, the switch and the pattern's rows.
`--flutter` puts `loader.py`'s wow and flutter on the pulses, `--jitter` a random
error on each one, `--speed` runs the tape fast or slow. All pass at
`--jitter 0.15`, `--speed 0.9` and `--speed 1.12` (each with `--jitter 0.1`), and
with flutter and `--jitter 0.15` together. The fast end is also the one with the
most bytes a frame for the I lines to store.

The `z80` package has no ULA, so this says nothing about contention's delays or
where the border's colours land; that is the next harness.

## `zx_emulator_run.cpp` - the real 48K's timing, on the zx-spectrum-emulator

The ZX Spectrum emulator (github.com/jonsole/zx-spectrum-emulator) models the
48K's contention and border latch T-state by T-state (its
`docs/contention.md`). `zx_emulator_run.cpp` links its core, `zx_core`, and
plays a tape into the loader:

```
cmake -DCMAKE_BUILD_TYPE=Release <emulator>/cpp-core && make zx_core
g++ -O2 -std=c++17 -I<emulator>/cpp-core/src tests/zx_emulator_run.cpp \
    libzx_core.a libzx_miniz.a -lpthread -o zx_emulator_run
zx_emulator_run <basic.bin> <tape.tzx|.wav|.csw> <CODE_START> <entry> <frames> <outdir> \
    <PRE> [<D_LINE> <I_LINE> ...]
```

`basic.bin` is the BASIC block's data from `lineloader.tap` (the program is
started at `CODE_START` rather than by the ROM, which is a stand-in again).
It reports where in its line each address given ran (one T-state for each, if
nothing drifted) and where in the frame `PRE` ran, and writes `memory.bin` and
a picture every 25 frames (`frameN.rgb`, 352x312 RGB).

What it showed, and what `lineloader.s` and `make_lineloader.py` take from it:

- The whole of Lunar Jetman, from `linetape.tzx` and from `linetape.wav` (wow and
  flutter included, the BASIC's standard-speed blocks playing first), loads byte
  for byte; every line routine starts 144 T-states into its line on every one
  of ~3,500 frames, and `PRE` at frame T-state 3280 on every frame. The top
  border is black until the border block's switch byte arrives (about frame
  700 of the `.wav`) and matches `border.txt` from then on.
- The border, compared cell by cell with `border.txt`, is exact. `OUT_WRITE = 9`
  in `make_lineloader.py` is the timing that puts each OUT's write in the
  middle of its cell; with it the pattern is still exact with the whole loop
  moved a T-state earlier, or one or two later (a 48K whose ULA runs early or
  late).
- Without the contended read in `LOADER_INIT` the sync depends on where the
  `HALT`'s 4 T-state NOPs fall against the interrupt, which differs from run to
  run, and a T-state either way moves edges of the pattern by a cell.
