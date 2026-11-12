# Handoff: the line-locked loader

Where the line-locked loader stands, for picking it up in VS Code. It was built and tested in
a cloud session on branch `ccr-0f1bf890-522urz` (the commits after `ba7a080`). CLAUDE.md's
"Line-locked loader" section is the full design writeup; this page is the short version, plus
what is done, what was and wasn't tested, and what is left.

## What it is

A second loader beside `loader.s`. After one sync to the frame interrupt, every scanline is a
chunk of code exactly 224 T-states long that samples the tape in the horizontal blank, so it
stays locked to the beam with interrupts off. That leaves the beam free for effects:

- **Top border pattern** (scanlines 16-63) from `border.txt`, compiled into `OUT`s.
- **Attribute raster bars** over the screen's top 8 rows (scanlines 64-127, 14 columns) from
  `band.txt`, scrolling a scanline a frame.
- **The rest of the frame decodes**: D lines decode two samples each, I lines (only above and
  below the paper) store decoded bytes, so contention never stretches a line.

It starts small: the BASIC carries a 1.3K loader with a black border and band; the pattern
and the band's colours are the first blocks the fast loader brings, then the screen and game.

Bits are one pulse each: 1.5 lines (336T) a 0, 4.5 lines (1008T) a 1. Same average speed as
`loader.s`.

## Files

| File | What |
|---|---|
| `lineloader.s` | Top level: BASIC bootstrap (REM code), memory map, `lineborder.bin` |
| `linetape.s` | The loader: line routines, band, byte handlers, sync |
| `make_lineloader.py` | `border.txt` + `band.txt` -> `lineloader_gen.s` (rows, colours, band table, schedule) |
| `border.txt`, `band.txt` | The pattern and the band's colours (edit these) |
| `lineloader_gen.s`, `lineloader.tap`, `lineborder.bin` | Generated, committed |
| `loader.py` | Encoder: `line_byte`, `line_leader`, `line_mem_header`, `line_end_blocks`, `line_border_blocks`, `build_line_tape` |
| `build_lunarjetman_line_tape.py` | Lunar Jetman tape -> `linetape.tzx`, `linetape.wav`, `lineaudio.wav` |
| `tests/run_lineloader.py` | Timing/decoding harness on the `z80` package |
| `tests/zx_emulator_run.cpp` | Driver for the zx-spectrum-emulator core: real contention, pictures |
| `tests/README.md` | How to run both, and what they showed |

## Building in VS Code

`.vscode/tasks.json` now has three more tasks (Terminal > Run Task), using the same
`sjasmplus-dir` setting as the existing build:

1. **generate lineloader_gen.s** - `python make_lineloader.py border.txt band.txt`
2. **make lineloader (sjasmplus)** - runs 1, then assembles `lineloader.s` to `lineloader.tap`,
   `lineborder.bin` and `output/lineloader.{sld,sym,lst}`
3. **build line tape (Lunar Jetman)** - runs 2, then `python build_lunarjetman_line_tape.py`

The default build task (Ctrl+Shift+B) is still `loader.s`. Python needs `numpy`; the tests also
need `pip install z80`.

`build_lunarjetman_line_tape.py` opens `LunarJetman.scr` with its real capitalisation; the old
`build_lunarjetman_tape.py` opens `lunarjetman.scr`, which only works on Windows.

## Editing the pattern and the band

- `border.txt`: 48 rows of 44 cells (8 pixels each, the whole visible width), `0`-`7` or `.`
  for the background. Changes need 3 cells between them (4-5 next to a colour outside the five
  used most); the last 3 cells of a row can't change. `make_lineloader.py` names the row and
  cells when something doesn't fit, and warns when a crowded change can't be centred in its
  cell (then a ULA a T-state early or late may draw it a cell out).
- `band.txt`: `column` (0-17, 9 centres it) and `attributes`, one byte a scanline
  (ink + 8*paper + 64 bright), repeated down the band. The default is bright inks on black
  paper, so only pixels change colour; paper colours make visible bars everywhere in the band.
- Space: the rows go from `BORDER_ROWS_AT` (about `$F74B`) to `$FFF4`, about 2.2K; the default
  pattern uses 1.86K. A busier pattern needs `-DLOADER_QUEUE=$E000` (moves everything down), which
  Lunar Jetman can't take: its image runs to `$F017`.

## What was tested

All in the cloud session; nothing has run on real hardware yet.

- `tests/run_lineloader.py`: every line exactly 224 T-states over whole loads, no contended
  access while the ULA fetches, all blocks byte for byte (bar the band's area). Passes plain,
  with wow/flutter and +-15% jitter, at 0.9x and 1.12x speed, and without the border block.
- zx-spectrum-emulator core (`zx_core`, real 48K contention and border latch):
  - The whole Lunar Jetman `linetape.wav` and `linetape.tzx` load byte for byte; the BASIC's
    standard-speed blocks are ignored while it waits.
  - Every line routine starts 144 T-states into its line on every frame (~3,500 frames), and
    `PRE` at frame T-state 3280 on every frame.
  - The border is black until the pattern's switch byte arrives (about frame 750), then
    matches `border.txt` pixel for pixel, still exact with the loop shifted 1 T-state early or
    2 late.
  - 500 frames of band: one colour per scanline, in `band.txt`'s order, one scanline a frame.
- Video of it loading, recorded from the emulator:
  https://claude.ai/artifact/EicXHrv6fZwayZSf5K74Kn

## Not tested, and assumptions

- **Real hardware.** Untested. The timings it relies on came from the emulator:
  `LINE_START = 144` (where each line's IN falls) and `OUT_WRITE = 9` (when an `OUT (C),r`
  reaches the border latch). If the pattern sits a cell out on a real 48K, adjust `OUT_WRITE`
  in `make_lineloader.py`; if lines are stretched, the contention windows are off.
- **The BASIC part with a real ROM.** The emulator runs started at `CODE_START` with a stand-in
  ROM (the sandbox couldn't fetch the 48K ROM). The bootstrap is the same as `loader.s`'s, but
  it hasn't been watched loading from `LOAD ""`.
- **The IM 2 sync** relies on the 48K ROM having `$FF` at `$3900-$3AFF` and `$F3` at `$0000`
  (vector `$FFFF`, `JR` to `$FFF4`). True of the 48K ROM; not checked on clones or other ROMs.
- **48K only.** 224 T-states a line, 312 lines; a 128K (228 x 311) would drift.
- **Contended I lines.** The I lines' byte writes are safe because they only run off the paper.
  Keep it that way if the schedule changes.

## Debugging

`.vscode/launch.json`'s DeZog configs load `output/loader.sna`/`output/loader.sld`, i.e. the old
loader. For this one point `sjasmplus` at `output/lineloader.sld` and take a snapshot after the
BASIC has run. DeZog's zsim doesn't model contention, so use it for logic, not timing. For
timing, `tests/run_lineloader.py` fails on any line that isn't 224 T-states, and
`tests/zx_emulator_run.cpp` prints the T-state each named routine starts at (one value each
means nothing drifted).

The rules for editing `linetape.s` (in its header and CLAUDE.md): every path through a chunk is
padded with `PAD` to a fixed count, the comments give T-states from the chunk's IN, and in
sjasmplus a true comparison is -1.

## Ideas not done

- **Countdown counter.** `loader.s` draws one; this loader doesn't. The 10 Z lines after the
  band are spare.
- **More band.** It's 14 columns because that's all the writes that fit outside the ULA's
  fetches. Wider means writing into the fetch window and counting the contention delays
  (deterministic, since the lines are locked).
- **Restore the picture's colours** under the band at the end: the tape could send those
  attribute bytes to a buffer and `EXIT` copy them back. You chose to leave the band's colours.
- **Tape designer.** The zx-spectrum-emulator's tape designer knows `loader.s` and `rom`
  schemes; a `lineloader` scheme would need an entry in its `tape_model.js` and a builder in
  its `scripts/build_tape.py` calling `build_line_tape`.
- **The video driver mode** (`VIDEO_FROM`/`VIDEO_TO`, raw frames to stdout) used for the
  recording is not in the committed `tests/zx_emulator_run.cpp`.
