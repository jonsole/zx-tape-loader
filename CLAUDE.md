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

`loader.py` (requires `numpy`) is a reusable module for tape/WAV synthesis (`TapeGenerator`,
generic `.tap`/screen-block encoding, wow/flutter). It has no side effects on import — all the
actual tape-building happens in payload-specific scripts.

`TapeGenerator` keeps the exact pulse lengths as well as the samples, and wow and flutter are
applied to those (`wow_flutter_samples(gen)`, i.e. `gen.render(wow_flutter_speed)`) so each pulse
is rounded to a sample once. Until 2026-09-19 they were applied by resampling the finished WAV,
which at 44.1kHz - four samples to a fast half-bit - left thousands of pulses between a half-bit
and a whole one, and `tape.wav` could not load at all.

`build_lunarjetman_tape.py` is that script for this project's payload: it reads `loader.tap`,
`lunarjetman.scr`, and `jetman.bin` from the working directory and synthesizes `audio.wav`, then
applies wow/flutter modulation to produce `tape.wav`. Run it after rebuilding `loader.tap`/
`jetman.bin` so the audio reflects the latest build:

```
python build_lunarjetman_tape.py
```

There's no dependency manifest — install `numpy` manually if missing.

`build_designed_tape(design)` encodes a tape designed in the ZX Spectrum emulator's VS Code
tape designer, whose `scripts/build_tape.py` reads the design's files and hands this bytes: the
screen's 6912 bytes and its `(x, y, w, h)` rectangles in sending order, each block's name,
address and bytes, the entry, `loader` - where it runs, None for the top of RAM - and `stack`
- the BASIC's `CLEAR` address, None for loader.s's own `$5F41` - so nothing here knows the
design's file format. `check_design` refuses what can't load, and a moved loader or stack is
assembled in a scratch folder with `sjasmplus -DLOADER_AT= -DSTACK_AT=`. SP sits `$17` under the
`CLEAR` address while the loader runs and its calls reach 4 bytes below that (`stack_spans`;
`$5F26-$5F29` by default, measured on the emulator for both a default and a moved stack).
`LOADER_SIZE`, `STACK_DEFAULT` and the rest of those constants are mirrored in the designer's
`tape_model.js` (docs/tape-designer.md there), whose tests hold its checks and costs to these
word for word; `assemble_loader` checks `LOADER_SIZE` against the symbols.
`convert_tape.py --pattern <game.screen.json>` uses just a screen's order.

`write_tzx(gen, path)` writes a generator as a `.tzx` rather than a recording of one, which is
what the designer builds by default: `TapeGenerator` remembers what each pulse was part of (its
`segments` - a `.tap` block, a run of leader pulses, the fast encoding's own bits), so the BASIC
bootstrap comes out as standard-speed blocks (ID $10) and the whole fast part as one generalized
data block (ID $19), whose alphabet says a 1 bit is one pulse of `BIT_T` and a 0 bit two of half
that. Pentagram is 41KB that way against 600KB of `.csw` and 30MB of `.wav`. The emulator's own
`.tzx` reader learned $19 for this (docs/tape.md there); anything else in the pulse list becomes
pure tone (ID $12) or pulse sequences (ID $13), and a silence longer than a pulse can be becomes
a pause (ID $20).

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
routine, but the bit/block format afterward is custom and denser than the standard ROM format.

**Leader and sync.** `LD_LEADER` counts leader edges in `HL`, and the first short pulse (under
about 1,700 T-states) goes to `LD_SYNC`, which accepts it only if at least 256 edges were counted
(`DEC H` / `JP M,LD_START`); the next edge ends the sync and data starts. The count matters twice
over: `LD_START` waits for a *level* (`C=$55`), not an edge, so if the signal is already there
when loading starts the first pulse measured is a fragment of a leader pulse and looks short; and
the loader only starts listening part way into the tone, after BASIC has run and cleared the
screen. So the encoder's leader (`TapeGenerator.fast_leader`, 1024 pulses) has to leave well over
256 edges after that. The check was `JP Z` until 2026-09-19, which rejected a good sync after
256-511 edges and took any short pulse before 256 - either way the loader then synced part way
into the data, which is the stray dotted line a 512-pulse leader leaves across the screen.

- **Screen blocks** (`.SCR_LOOP`/`.SCR_BLK`): a 5-bit block-address MSB (terminated by a value
  of 0, which `DEC E` takes negative - "no more screen blocks", on to the control blocks; real
  offsets go out one page high, so never send 0), an 8-bit address LSB, and a 5-bit length — i.e. compact headers designed for the many
  small runs that make up a screen image (see `gen_block` in `loader.py`, which strips leading/
  trailing zero bytes per line and emits one header per run).
- **Memory/control blocks** (`.CTRL_LOOP`/`.MEM_BLK`): a full 16-bit address and 16-bit length per
  block, the address sent one page high. `DEC H` takes it back, and a page that reaches zero - sent
  as 1, i.e. an address field of `$0100` (`TapeGenerator.end_blocks`) - means "no more blocks, jump
  to the entry point", which rides in the length field (`JP (IX)`). Any address from `$0100` to
  `$FFFF` loads. The test was `JP P` until 2026-09-19, which also took every page from `$80` up for
  the end and jumped to the block's length, so nothing could load above `$7FFF`.
- **The tail.** `end_blocks` follows the end marker with 32 more pulses, so the last bit's edges
  are never the last edges on the tape: whether the edge into the silence that follows survives
  depends on which level the tape stops at, and when it doesn't the final bit times out and the
  jump goes to `$xx80`.
- **Bit timing** (`LD_BITS`/`.PULSE_A`/`.PULSE_B`/`.PULSE_1`): edge-timed like the ROM loader (a
  long first pulse = a `1` bit), but with tighter constants for a faster baud rate. The long/short
  decision is `CP -10` on the loop counter `B`: measured on 44.1kHz WAVs, a 0's first half-pulse
  leaves `B` at -5 to -8 and a 1 at -13 to -17, so a bit is long from -11. It was `CP -8` until
  2026-09-19, one count from the 0s. Every bit read
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

1. `loader_tap` (this project's own BASIC bootstrap) at standard ROM speed, unchanged, then the
   fast leader and sync (`fast_leader`).
2. If a CODE file is exactly Screen$-sized (address 16384, length 6912), its pixel data is loaded
   first via `gen_block`, in an order from `analyse_screen_regions`: top-to-bottom, skipping
   character rows with no set pixel and tightly bounding each run of non-blank rows to its actual
   left/right content. This is a content-aware default, not a re-creation of hand-curated,
   layered reveals like `build_lunarjetman_tape.py`'s explicit region list (which deliberately
   loads a narrow title box before a wider bordering pass over the same rows — a per-row bounding
   box can't reproduce that).
   The end-of-screen marker (`end_screen`) follows either way - `tape.s` always starts by reading
   a screen header, so a tape without a screen still needs it.
3. Remaining CODE files as plain memory blocks (`mem_header` + `dme_byte`), at their original
   addresses.
4. The end marker and tail (`end_blocks(entry_address)`).

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
