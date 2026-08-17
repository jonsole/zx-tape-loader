# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A ZX Spectrum 48K custom tape loader written in Z80 assembly (sjasmplus syntax), plus a Python
tool that renders the resulting tape image to a WAV file for playback on real hardware/tape. The
loader implements its own high-speed, custom-encoded tape protocol (not the ROM `LOAD ""` format)
that loads a screen image and a game payload while drawing an animated countdown counter, timed
to fit inside the bit-reading loop without disturbing its sampling windows.

`lunarjetman.s` is a separate, self-contained relocator/patcher for a specific game payload
(Lunar Jetman) that this loader delivers.

## Build

Build with sjasmplus via the VS Code task **"make (sjasmplus)"** (default build task, Ctrl+Shift+B), or directly:

```
<sjasmplus-dir>/sjasmplus.exe --sld=output/loader.sld --fullpath --lst=output/loader.lst loader.s
```

`sjasmplus-dir` and `cspect-dir` are machine-specific paths configured in [.vscode/settings.json](.vscode/settings.json) (`sjasmplus-dir`, `cspect-dir`) — update these to match a local sjasmplus/CSpect install. There is no cross-platform build script; this is a Windows/VS Code workflow.

Assembling `loader.s` produces `loader.tap` (the BASIC-header tape image) plus `output/loader.lst`
(listing) and `output/loader.sld` (source-level debug map consumed by DeZog).

`lunarjetman.s` is a separate assembly unit (own `DEVICE`/`ORG`) that is not included by
`loader.s`; it's assembled independently and produces `jetman.bin` via `SAVEBIN`, which is then
consumed by `build_lunarjetman_tape.py`.

## Debug / run

Debugging is via the DeZog VS Code extension ([.vscode/launch.json](.vscode/launch.json)):
- **"Internal Simulator"** — runs against DeZog's built-in `zsim` ZX48K simulator, no external emulator needed.
- **"CSpect"** — attaches to the CSpect emulator; its preLaunchTask ("start cspect") starts `CSpect.exe` with `-remote` before DeZog connects.

Both configs load `output/loader.sna` and use `output/loader.sld` for symbols, so build first.
`topOfStack` is hardcoded to `$5B9F` (top of the BASIC loader's stack area) — keep this in sync if
the loader's memory layout changes.

## Generating the tape audio

`loader.py` (requires `numpy`, `scipy`) is a reusable module for tape/WAV synthesis (`TapeGenerator`,
generic `.tap`/screen-block encoding, wow/flutter). It has no side effects on import — all the
actual tape-building happens in payload-specific scripts.

`build_lunarjetman_tape.py` is that script for this project's payload: it reads `loader.tap`,
`lunarjetman.scr`, and `jetman.bin` from the working directory and synthesizes `audio.wav`, then
applies wow/flutter modulation to produce `tape.wav`. Run it after rebuilding `loader.tap`/
`jetman.bin` so the audio reflects the latest build:

```
python build_lunarjetman_tape.py
```

There's no dependency manifest — install `numpy`/`scipy` manually if missing.

`convert_tape.py <source.tap|.tzx> <output.wav>` is a generic alternative to
`build_lunarjetman_tape.py`, for re-encoding a *standard-speed* `.tap`/`.tzx` dump of any game
through this project's fast loader, rather than this project's own bespoke payload. See
"Generic tape conversion" below for how it works and its limits.

## Architecture

### Tape-embedded BASIC bootstrap (`loader.s`, `basic.s`)

`loader.s` builds the `.tap` file directly with sjasmplus's `TAPOUT`/`TAPEND`/`EMPTYTAP` directives:
a standard 17-byte tape header block, then a BASIC program body. The BASIC program consists of two
lines built with the `LINE`/`LINE_END` macros from `basic.s`:

1. **Line 0**: a `REM` statement whose "remark" bytes are actually the Z80 machine code
   (`CODE_START` onward) — the classic "hide machine code in a REM statement" trick. Because the
   REM's declared length must exactly match its assembled size, `basic.s`'s macros use sjasmplus's
   embedded Lua (`LUA ALLPASS` / `sj.parse_code` / `sj.parse_line`) to patch the correct length
   back into the line header after assembly — this indirection is required because the length is
   not known until the code between `LINE`/`LINE_END` is assembled.
2. **Line 1**: `CLEAR`/`RANDOMIZE USR` statements that clear memory below the REM line and jump
   into `CODE_START` via `USR`.

At runtime, `CODE_START` relocates the loader payload (`tape.s` + `counter.s`, between
`LOADER_SOURCE`/`ENT`) up to `LOADER_DEST` — the top of RAM, computed as `$FFFF - LOADER_SIZE`, i.e.
above the "uncontended"/high-memory area — via `DISP`/`ENT` (sjasmplus's load-time-vs-run-time
address split). This keeps the fast loader out of screen/variables memory and avoids ULA memory
contention affecting its cycle-critical timing. It then clears the screen, sets border/paper/ink
attributes, and jumps into `LD_BYTES` in the relocated code.

### Custom tape protocol (`tape.s`)

`LD_BYTES`/`LD_START`/`LD_LEADER`/`LD_SYNC` reimplement leader/sync detection similar to the ROM
routine, but the bit/block format afterward is custom and denser than the standard ROM format:

- **Screen blocks** (`.SCR_LOOP`/`.SCR_BLK`): a 5-bit block-address MSB (terminated by an
  all-1s/negative value, which signals "no more screen blocks" and falls through to control
  blocks), an 8-bit address LSB, and a 5-bit length — i.e. compact headers designed for the many
  small runs that make up a screen image (see `gen_block` in `loader.py`, which strips leading/
  trailing zero bytes per line and emits one header per run).
- **Memory/control blocks** (`.CTRL_LOOP`/`.MEM_BLK`): a full 16-bit address and 16-bit length per
  block. A destination page (`H`) of `$FF` (i.e. `DEC H` going negative) signals "no more blocks,
  jump to the just-loaded entry point" (`JP (IX)`) instead of loading another block.
- **Bit timing** (`LD_BITS`/`.PULSE_A`/`.PULSE_B`/`.PULSE_1`): edge-timed like the ROM loader (a
  long first pulse = a `1` bit), but with tighter constants for a faster baud rate. Every bit read
  also rotates the border-stripe pattern in `D` and calls out through `DELAY_CALL` (`JP (IY)`) —
  this is the hook the countdown-counter coroutine in `counter.s` uses to interleave its own work
  into the exact same T-state budget as a bit read, without slowing the loader down.

This protocol is the counterpart to the encoder in `loader.py` (`TapeGenerator.dme_byte`/
`scr_header`/`mem_header`) — the two must be kept in sync; changing bit timing or block header
widths on one side requires a matching change on the other.

### Interleaved countdown counter (`counter.s`)

The on-screen countdown digits are drawn by code that runs *inside* the tape-loading bit loop
rather than as a separate interrupt or polling loop — timing is too tight during loading for
either. `DELAY_CONT`/`DELAY_EXIT` (macros) and `DELAY_CALL` (`JP (IY)`) implement a manual
coroutine: each fragment of counter-drawing work self-modifies `IY` to point at its own
continuation before returning control to `tape.s`'s `LD_BITS`, which calls back into `IY` on its
next iteration. Every fragment is written to consume a specific, commented T-state budget (see the
cycle-count comments beside each instruction) so the combined loader+counter loop stays within the
tape bit-cell timing. **When editing this file, preserve exact cycle counts** — the comments
document the intended budget per fragment, and changing instructions without adjusting the
padding/counting will desync bit sampling.

`DIG_GFX` holds 8x8 pixel digit glyphs (0–9) drawn to a fixed screen location; `COUNTER_VAL` holds
the current 4-digit countdown value, decremented digit-by-digit as loading progresses.

### Payload relocator (`lunarjetman.s`)

Assembled independently at `$7000`. Copies the embedded `lunarjetman.bin` (`INCBIN`) up to `$8000`,
patches two bytes in the target game image (skips a frame-count check, installs a `JP (HL)` at a
fixed ROM/system variable address) as an in-place binary patch, then jumps into it. `SAVEBIN`
extracts the relocator+payload as `jetman.bin`, which `build_lunarjetman_tape.py` embeds as a plain
memory block in the generated tape/audio.

### Generic tape conversion (`convert_tape.py`, `loader.py`'s `build_fast_tape`)

`build_fast_tape` parses an arbitrary source `.tap` (natively) or `.tzx` (standard-speed blocks,
TZX ID `0x10`, only — anything else raises, since a source tape with its own custom/turbo loader
can't be generically decoded) into header+data `TapeFile` pairs via `parse_source_files`, then
re-encodes it through this project's fast protocol:

1. `loader_tap` (this project's own BASIC bootstrap) at standard ROM speed, unchanged.
2. If a CODE file is exactly Screen$-sized (address 16384, length 6912), its pixel data is loaded
   first via `gen_block`, in an order from `analyse_screen_regions`: top-to-bottom, skipping
   character rows with no set pixel and tightly bounding each run of non-blank rows to its actual
   left/right content. This is a content-aware default, not a re-creation of hand-curated,
   layered reveals like `build_lunarjetman_tape.py`'s explicit region list (which deliberately
   loads a narrow title box before a wider bordering pass over the same rows — a per-row bounding
   box can't reproduce that).
3. Remaining CODE files as plain memory blocks (`mem_header` + `dme_byte`), at their original
   addresses.
4. The terminal "jump" sentinel (`mem_header($0000-256, entry_address)`, matching the `H=0`-signals-
   done convention `tape.s`'s `.CTRL_LOOP` expects).

The entry address defaults to auto-detection via `find_usr_address`, which scans a Program file's
tokenized BASIC for a `USR` token followed by ZX BASIC's numeric-literal encoding (ASCII digits,
then a `0x0E` marker, then a 5-byte value: exponent/sign/lo/hi/unused — `USR` arguments are always
the "small integer" form, exponent `0`). Verified against `loader.tap`'s own `RANDOMIZE USR` line,
which correctly resolves to `CODE_START` ($5CD0). Pass `entry_address` explicitly (`--entry` on the
CLI) when a source tape doesn't follow this convention.

## Working assets

- `LunarJetman.scr` — ZX Spectrum screen dump used as a tape payload, consumed by
  `build_lunarjetman_tape.py`'s `gen_block` calls, whose x/y/w/h regions describe the screen layout
  to send.
- `loader.tap` — generated tape image (see Build, above).
- `output/` — build artifacts (`.lst` listing, `.sld` debug symbols, `.sna` snapshot for DeZog);
  regenerated by the build task except `.sna`, which is produced by saving emulator state.
