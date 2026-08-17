# ZX Tape Loader

A custom, high-speed tape loader for the ZX Spectrum 48K, written in Z80 assembly. Instead of the
standard ROM `LOAD ""` format, it implements its own denser tape protocol and uses the spare time
in the bit-reading loop to animate an on-screen countdown counter while the payload streams in.

It boots as an ordinary BASIC tape, then hands off to hand-tuned, cycle-counted assembly for the
actual loading. Included is a small toolchain for turning the assembled loader — plus a screen and
a game payload — into a `.wav` file that can be played into a real Spectrum's tape input, complete
with simulated wow and flutter.

## How it works

- **`loader.s` / `basic.s`** — Builds the tape's BASIC header. The machine code lives inside a
  `REM` statement (the classic "hide code in a REM" trick), with the line length patched in via
  sjasmplus's embedded Lua after assembly. On run, it relocates the loader up to the top of RAM
  (out of the way of contended/screen memory) and jumps in.
- **`tape.s`** — The loader itself: leader/sync detection, then a custom bit/block format that's
  more compact than the ROM format — 5-bit headers for the many small runs that make up a screen
  image, full 16-bit headers for general memory blocks. Every bit read also drives a callback hook
  used to interleave the counter animation without slowing the loader down.
- **`counter.s`** — Draws the on-screen countdown digits *inside* the tape's bit-reading loop via a
  hand-rolled coroutine (self-modifying continuation pointer in `IY`). Each fragment of drawing
  work is budgeted to a fixed, commented T-state count so it doesn't perturb the loader's timing.
- **`lunarjetman.s`** — A small relocator/patcher for the Lunar Jetman payload: copies it into
  place, patches out a frame-count check, and jumps in.
- **`loader.py`** — Reusable module for tape-audio synthesis: pulse/bit encoding
  (`TapeGenerator`), generic `.tap`/`.tzx` parsing, screen-block encoding, WAV writing, and
  wow/flutter, plus `build_fast_tape` to re-encode a whole source tape through the fast loader.
- **`build_lunarjetman_tape.py`** — Uses `loader.py` to assemble this project's specific payload
  (`loader.tap` + `lunarjetman.scr` + `jetman.bin`) into `audio.wav`/`tape.wav`, so the loader can
  be played back like a real cassette.
- **`convert_tape.py`** — Generic CLI: re-encodes any standard-speed `.tap`/`.tzx` dump through the
  fast loader, auto-detecting the entry address and picking a screen-loading order.

See [CLAUDE.md](CLAUDE.md) for a deeper architectural writeup.

## Requirements

- [sjasmplus](https://github.com/z00m128/sjasmplus) to assemble
- [DeZog](https://marketplace.visualstudio.com/items?itemName=maziac.dezog) (VS Code extension) to debug, either against its built-in `zsim` simulator or [CSpect](https://dailly.blogspot.com/)
- Python 3 with `numpy` and `scipy` to generate the tape audio

## Building

Assemble with the VS Code task **"make (sjasmplus)"** (Ctrl+Shift+B), or directly:

```sh
sjasmplus --sld=output/loader.sld --fullpath --lst=output/loader.lst loader.s
```

Update `sjasmplus-dir` / `cspect-dir` in [.vscode/settings.json](.vscode/settings.json) to match a
local install first. This produces `loader.tap`, plus `output/loader.lst` and `output/loader.sld`
for debugging.

## Debugging

Launch configs are provided for DeZog:

- **Internal Simulator** — runs against DeZog's built-in ZX48K simulator, no emulator needed.
- **CSpect** — attaches to CSpect (auto-started via the `start cspect` task).

Both load `output/loader.sna` and `output/loader.sld`, so build first.

## Generating the tape audio

```sh
python build_lunarjetman_tape.py
```

Reads `loader.tap`, `lunarjetman.scr`, and `jetman.bin` and writes `tape.wav`, ready to play into a
real Spectrum's tape input (or a cassette deck to make an actual tape).

### Converting another tape

`convert_tape.py` re-encodes any standard-speed `.tap`/`.tzx` file through this project's fast
loader — useful for anything that isn't Lunar Jetman's bespoke pipeline above:

```sh
python convert_tape.py source.tap output.wav
```

It auto-detects the entry point from the source's own `RANDOMIZE USR n` BASIC line, and if a CODE
block is exactly Screen$-sized (address 16384, length 6912) it's loaded first, in an order picked
by `analyse_screen_regions` (top-to-bottom, skipping blank character rows). This only works for
tapes that already use standard ROM-speed blocks — a tape with its own custom/turbo loader (as
most original commercial cassettes shipped with) can't be generically parsed, since its bit
encoding isn't known ahead of time.
