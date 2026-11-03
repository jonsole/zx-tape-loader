from dataclasses import dataclass
import json
import os
import struct
import wave
import numpy as np

SAMPLE_RATE = 44307  # Samples/sec used for pulse-width timing math
# What a bit of the fast encoding costs: a frame's worth of T-states over 13,
# as tape.s counts it. A 1 is one pulse this long, a 0 two of half it.
BIT_T = (69888 / 8) / 13
OUTPUT_RATE = 44100  # Samples/sec the WAV file itself is written/read at

TYPE_PROGRAM = 0
TYPE_NUMBER_ARRAY = 1
TYPE_CHARACTER_ARRAY = 2
TYPE_CODE = 3


def wow_flutter_speed(t, wow_freq=0.01, flutter_freq=0.2, depth=0.02):
    """How fast a tape with wow and flutter runs at time `t`, relative to its
    nominal speed: the rate of change of a play position that wanders by
    `depth` seconds at each frequency - about +/-2.6% with the defaults."""
    return 1 + depth * 2 * np.pi * (wow_freq * np.cos(2 * np.pi * wow_freq * t) +
                                    flutter_freq * np.cos(2 * np.pi * flutter_freq * t))


class TapeGenerator:

    def __init__(self, sample_rate=SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.state = 127
        self.samples = bytearray()
        self.error = 0
        self.pulses = []
        # What each pulse was part of, in order, for write_tzx: ('tap', block
        # bytes) for a standard-speed block, ('pilot', (length, count)) for a
        # run of identical leader pulses, ('bits', [0/1, ...]) for the fast
        # encoding's own bits, ('pulses', [t, ...]) for anything else. The
        # waveform is the same either way; this is only how it is written down.
        self.segments = []

    def _note(self, kind, item):
        if self.segments and self.segments[-1][0] == kind:
            self.segments[-1][1].append(item)
        else:
            self.segments.append((kind, [item]))

    def pulse(self, t_states):
        """One pulse of its own, not part of a bit or a block."""
        self._note('pulses', t_states)
        self._raw_pulse(t_states)

    def _raw_pulse(self, t_states):
        self.pulses.append(t_states)
        samples = (t_states / 3500000 * self.sample_rate)
        samples += self.error
        self.error = samples - int(samples)
        self.samples.extend([self.state] * int(samples))
        self.state ^= 127

    def render(self, speed=None):
        """The tape as samples, drawn again from the exact pulse lengths - at
        `speed(t)` times nominal speed at each moment if given, which is how
        wow and flutter go on (`render(wow_flutter_speed)`). Each pulse is
        stretched before it is cut into samples, so it is rounded to a sample
        once; stretching the finished samples instead rounds it twice, and a
        four-sample half-bit comes out as three or six."""
        out = bytearray()
        state = 127
        error = 0.0
        for t_states in self.pulses:
            if speed is not None:
                t_states = t_states / speed(len(out) / self.sample_rate)
            exact = t_states / 3500000 * self.sample_rate + error
            whole = int(exact)
            error = exact - whole
            out.extend(bytes([state]) * whole)
            state ^= 127
        return out

    def std_byte(self, b, num_bits=8):
        for i in range(num_bits, 0, -1):
            bit = 1 if b & (1 << (i - 1)) else 0
            if bit:
                self._raw_pulse(1710)
                self._raw_pulse(1718)
            else:
                self._raw_pulse(1710 / 2)
                self._raw_pulse(1710 / 2)

    def dme_byte(self, b, num_bits=8):
        for i in range(num_bits, 0, -1):
            bit = 1 if b & (1 << (i - 1)) else 0
            self._note('bits', bit)
            if bit:
                self._raw_pulse(BIT_T)
            else:
                self._raw_pulse(BIT_T / 2)
                self._raw_pulse(BIT_T / 2)

    def fast_leader(self, pulses=1024):
        """The leader and sync the fast loader waits for. tape.s's LD_SYNC only
        takes a short pulse for the sync once it has counted 256 leader edges,
        and the loader starts listening part way into this tone - after BASIC
        has run and the screen is cleared - so the tone has to be comfortably
        longer than that: 1024 pulses is about 0.63s."""
        self._note('pilot', (2168, pulses))
        for _ in range(pulses):
            self._raw_pulse(2168)
        self._note('pilot', (600, 2))
        self._raw_pulse(600)
        self._raw_pulse(600)

    def scr_header(self, offset, len):
        self.dme_byte(offset + 256, 13)
        self.dme_byte(len - 1, 5)

    def end_screen(self):
        """The end of the screen blocks - sent even when there are none, since
        tape.s always starts by reading a screen header."""
        self.dme_byte(0, 5)

    def mem_header(self, address, len):
        """One memory block's header. The address goes out one page high, so
        tape.s's DEC H gets it back; a page of $00 would arrive as 1, which is
        the end marker, and the ROM can't be loaded into anyway."""
        if not 0x100 <= address <= 0xFFFF:
            raise ValueError(f"a block can't load at ${address:04X}: page $00 is the ROM, and "
                             "its header would read as the end marker")
        if not 1 <= len <= 0x10000 - address:
            raise ValueError(f"a block of {len} bytes doesn't fit at ${address:04X}")
        self.dme_byte((address + 256) & 0xFFFF, 16)
        self.dme_byte(len, 16)

    def end_blocks(self, entry_address, tail=32):
        """The end marker: a header whose page arrives as 1 - tape.s's DEC H
        takes it to zero and jumps to the 'length', which is the entry address.

        Then a tail of pulses, so the last bit's edges are never the last edges
        on the tape: whether the edge into the silence that follows survives
        depends on which level the tape happens to stop at, and when it doesn't
        the loader times out on the final bit and jumps to a wrong address. By
        the time the tail plays the loader has already gone."""
        self.dme_byte(0x0100, 16)
        self.dme_byte(entry_address, 16)
        # The tail is a run of 1 bits: the same pulses, and a .tzx can then
        # write the whole fast part as one block of symbols.
        for _ in range(tail):
            self.dme_byte(1, 1)

    def std_block(self, block_data):
        """Emit one .tap block (leader/sync/data) at standard ROM timing."""
        self._note('tap', bytes(block_data))
        for _ in range(3184 if block_data[0] else 4096):
            self._raw_pulse(2168)
        self._raw_pulse(667)
        self._raw_pulse(735)
        for d in block_data:
            self.std_byte(d)


def encode_tap_file(gen, path, pause_between_blocks=1000000):
    """Encode every block of a .tap file onto `gen` at standard ROM timing."""
    with open(path, 'rb') as file:
        encode_tap_bytes(gen, file.read(), pause_between_blocks)


def encode_tap_bytes(gen, data, pause_between_blocks=1000000):
    """encode_tap_file for a .tap already in memory."""
    while data:
        block_size = data[0] | data[1] << 8
        data = data[2:]
        block_data = data[:block_size]
        data = data[block_size:]

        gen.std_block(block_data)

        if data:
            gen.pulse(pause_between_blocks)


@dataclass
class RawBlock:
    flag: int
    payload: bytes  # excludes the flag and checksum bytes


@dataclass
class TapeFile:
    type: int
    name: str
    length: int
    param1: int
    param2: int
    data: bytes


def _read_tap_blocks(path):
    with open(path, 'rb') as f:
        data = f.read()

    blocks = []
    while data:
        block_size = data[0] | (data[1] << 8)
        data = data[2:]
        block_data = data[:block_size]
        data = data[block_size:]
        blocks.append(RawBlock(block_data[0], block_data[1:-1]))
    return blocks


def _read_tzx_blocks(path):
    with open(path, 'rb') as f:
        data = f.read()

    if data[:8] != b'ZXTape!\x1a':
        raise ValueError(f"{path}: not a TZX file (bad signature)")

    blocks = []
    pos = 10  # 8-byte signature + major/minor version
    while pos < len(data):
        block_id = data[pos]
        pos += 1
        if block_id == 0x10:  # Standard Speed Data Block
            pos += 2  # pause after block (ms) - not needed, we generate our own gaps
            length = data[pos] | (data[pos + 1] << 8)
            pos += 2
            payload = data[pos:pos + length]
            pos += length
            blocks.append(RawBlock(payload[0], payload[1:-1]))
        else:
            raise ValueError(
                f"{path}: unsupported TZX block type 0x{block_id:02X} at offset {pos - 1} "
                "- only standard speed data blocks (0x10) are supported, so this tape's "
                "custom/turbo loader can't be generically re-encoded"
            )
    return blocks


def parse_source_files(path):
    """Parse a .tap or .tzx file into its header+data TapeFile pairs."""
    blocks = _read_tzx_blocks(path) if path.lower().endswith('.tzx') else _read_tap_blocks(path)

    files = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        i += 1
        if block.flag != 0x00 or len(block.payload) != 17:
            continue  # not a standard header block - skip until the next one

        header = block.payload
        data = b''
        if i < len(blocks):
            data = blocks[i].payload
            i += 1

        files.append(TapeFile(
            type=header[0],
            name=header[1:11].decode('ascii', errors='replace').rstrip(),
            length=header[11] | (header[12] << 8),
            param1=header[13] | (header[14] << 8),
            param2=header[15] | (header[16] << 8),
            data=data,
        ))
    return files


def find_usr_address(basic_data):
    """
    Scan a Program file's tokenized data for `USR <n>` and return `n`.

    ZX BASIC stores a numeric literal as its ASCII text, then a 0x0E marker,
    then a 5-byte value: exponent, sign, value-low, value-high, unused. USR
    arguments are always encoded as the "small integer" form (exponent 0).
    """
    USR_TOKEN = 0xC0
    idx = 0
    while True:
        idx = basic_data.find(bytes([USR_TOKEN]), idx)
        if idx == -1:
            return None

        marker = idx + 1
        while marker < len(basic_data) and 0x30 <= basic_data[marker] <= 0x39:
            marker += 1

        if (marker + 5 < len(basic_data) and basic_data[marker] == 0x0E
                and basic_data[marker + 1] == 0x00):
            return basic_data[marker + 3] | (basic_data[marker + 4] << 8)

        idx += 1


def get_pixel_address(x, y):
    y76 = y & 0b11000000  # third of screen
    y53 = y & 0b00111000
    y20 = y & 0b00000111
    return (y76 << 5) + (y20 << 8) + (y53 << 2) + x


def get_attribute_address(x, y):
    y73 = y & 0b11111000
    return (y73 << 2) + x


def gen_block(gen, data, x, y, w, h):
    """Encode an 8x8-cell-aligned rectangle of a screen dump (bitmap + attributes)."""
    h *= 8
    y *= 8
    for line in range(y, y + h):

        if line % 8 == 0:
            offset = 6144 + get_attribute_address(x, line)
            chunk = data[offset: offset + w]
            gen.scr_header(offset, len(chunk))
            for d in chunk:
                gen.dme_byte(d)
                offset += 1

        offset = get_pixel_address(x, line)
        chunk = data[offset: offset + w]

        # remove leading and trailing 0's
        while len(chunk) and chunk[0] == 0:
            chunk = chunk[1:]
            offset += 1
        while len(chunk) and chunk[-1] == 0:
            chunk = chunk[:-1]

        if len(chunk):
            gen.scr_header(offset, len(chunk))
            for d in chunk:
                gen.dme_byte(d)
                offset += 1


def analyse_screen_regions(data):
    """
    Pick a loading order for a 6912-byte screen dump: reveal it top-to-bottom,
    but skip character rows that are entirely blank (no set pixel) so empty
    borders/backgrounds don't cost tape time, and tightly bound each run of
    non-blank rows to its actual left/right content instead of the full width.

    Returns a list of (x, y, w, h) rectangles, in gen_block's units (x/w in
    bytes, y/h in 8-pixel character rows), for gen_block to load in sequence.
    """
    row_bounds = []  # (col_min, col_max) or None, one entry per character row
    for row in range(24):
        col_min, col_max = None, None
        for col in range(32):
            for line in range(row * 8, row * 8 + 8):
                if data[get_pixel_address(col, line)] != 0:
                    if col_min is None:
                        col_min = col
                    col_max = col
                    break
        row_bounds.append((col_min, col_max))

    regions = []
    run_start = run_col_min = run_col_max = None
    for row, bounds in enumerate(row_bounds + [(None, None)]):
        col_min, col_max = bounds
        if col_min is None:
            if run_start is not None:
                regions.append((run_col_min, run_start, run_col_max - run_col_min + 1, row - run_start))
                run_start = None
        elif run_start is None:
            run_start, run_col_min, run_col_max = row, col_min, col_max
        else:
            run_col_min = min(run_col_min, col_min)
            run_col_max = max(run_col_max, col_max)
    return regions


# --- building a designed tape ----------------------------------------------
#
# The ZX Spectrum emulator's tape designer (its scripts/build_tape.py) reads a
# tape designed there, turns every file into bytes, and hands the result here:
#
#   {'loader': address or None for the top of RAM,
#    'stack': the CLEAR address - RAMTOP, the stack just below it - or None
#             for loader.s's own, just above the BASIC,
#    'screen': None or {'data': 6912 bytes, 'regions': [(x, y, w, h), ...]},
#    'blocks': [{'name': ..., 'address': ..., 'data': bytes}, ...],
#    'entry': address or None}
#
# so this knows about the loader and its encoding and nothing about the files
# a design is kept in.

# What the loader needs of memory while it runs. LOADER_SIZE is loader.s's
# LOADER_SIZE (assemble_loader checks it against the symbols whenever it
# assembles the loader), and the top of RAM is where it goes by default.
#
# The stack is BASIC's, left where the loader's CLEAR put it: $5F41 by default,
# just above the BASIC (loader.s's BASIC_START + BASIC_LEN + 128), or wherever
# a design's 'stack' says, assembled with -DSTACK_AT. The ROM's USR leaves SP
# $17 below the CLEAR address while the loader runs, and LD_BITS -> DELAY_CALL
# nest two calls below that, so the four bytes under SP are live throughout;
# above SP is only BASIC's own return path, which matters only if a load fails
# and returns. `stack_spans` gives both for a CLEAR address.
LOADER_SIZE = 0x1B1
LOADER_DEFAULT = 0xFFFF - LOADER_SIZE
STACK_DEFAULT = 0x5F41
STACK_SP = 0x17          # how far below the CLEAR address SP sits while the loader runs
STACK_DEPTH = 4          # and how far below SP its two nested calls reach
BASIC_LOADER = (0x5C00, 0x5EC1)     # the system variables and loader.s's BASIC


def stack_spans(stack):
    """The loader's live stack and BASIC's return path above it, as [start,
    end) spans, for the CLEAR address `stack`."""
    sp = stack - STACK_SP
    return (sp - STACK_DEPTH, sp), (sp, stack + 1)


LIVE_STACK, BASIC_STACK = stack_spans(STACK_DEFAULT)
SCREEN_MEMORY = (0x4000, 0x5B00)


def read_pattern(path):
    """The loading order in a designer's .screen.json, as (x, y, w, h)
    rectangles for `gen_block` - for convert_tape.py --pattern."""
    with open(path, 'r', encoding='utf-8') as file:
        screen = json.load(file)
    order = screen.get('order') if isinstance(screen, dict) else None
    if not isinstance(order, list):
        raise ValueError(f"{path}: not a loading screen - expected an \"order\" list of rectangles")
    regions = []
    for region in order:
        try:
            x, y, w, h = (int(region[key]) for key in ('x', 'y', 'w', 'h'))
        except (TypeError, KeyError, ValueError):
            raise ValueError(f"{path}: every rectangle needs an x, y, w and h, in character cells")
        if not (0 <= x < 32 and 0 <= y < 24 and 1 <= w <= 32 - x and 1 <= h <= 24 - y):
            raise ValueError(f"{path}: the rectangle {x},{y} {w}x{h} is not on the screen")
        regions.append((x, y, w, h))
    return regions


def _spans_overlap(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def check_design(design, loader_address, stack=STACK_DEFAULT):
    """What would stop the designed tape loading, and what is merely worth
    knowing - as (errors, warnings). The designer's tape_model.js makes the
    same checks, word for word, and its tests hold the two together."""
    errors, warnings = [], []
    loader_span = (loader_address, loader_address + LOADER_SIZE)
    live_stack, basic_stack = stack_spans(stack)
    if loader_address < 0x8000 or loader_span[1] > 0x10000:
        errors.append(f"the loader at ${loader_address:04X} must fit in $8000-$FFFF: below $8000 "
                      "the ULA's contention would stretch its cycle-counted bit loop")
    if stack < STACK_DEFAULT:
        errors.append(f"the stack at ${stack:04X} must be at ${STACK_DEFAULT:04X} or above: any lower "
                      "and CLEAR would put it in the BASIC loader's own workspace")
    elif _spans_overlap(live_stack[0], basic_stack[1], *loader_span):
        errors.append(f"the stack at ${stack:04X} (${live_stack[0]:04X}-${basic_stack[1] - 1:04X}) "
                      f"runs into the loader (${loader_span[0]:04X}-${loader_span[1] - 1:04X})")
    for i, block in enumerate(design['blocks']):
        start, end = block['address'], block['address'] + len(block['data'])
        name = f'block "{block["name"]}" (${start:04X}-${end - 1:04X})'
        if not block['data']:
            errors.append(f"{name} is empty")
            continue
        if start < 0x4000:
            errors.append(f"{name} starts in the ROM")
        if end > 0x10000:
            errors.append(f"{name} runs past $FFFF")
        if _spans_overlap(start, end, *loader_span):
            errors.append(f"{name} would load over the loader itself "
                          f"(${loader_span[0]:04X}-${loader_span[1] - 1:04X})")
        if _spans_overlap(start, end, *live_stack):
            errors.append(f"{name} would load over the loader's stack "
                          f"(${live_stack[0]:04X}-${live_stack[1] - 1:04X})")
        elif _spans_overlap(start, end, *basic_stack):
            warnings.append(f"{name} loads over BASIC's stack, so a failed load can't return to BASIC")
        elif _spans_overlap(start, end, *BASIC_LOADER):
            warnings.append(f"{name} loads over the BASIC loader, so a failed load can't return to BASIC")
        if design['screen'] is not None and _spans_overlap(start, end, *SCREEN_MEMORY):
            warnings.append(f"{name} loads over the loading screen")
        for other in design['blocks'][:i]:
            if _spans_overlap(start, end, other['address'], other['address'] + len(other['data'])):
                warnings.append(f'{name} loads over part of block "{other["name"]}"')
    if design['entry'] is None:
        errors.append("there is no entry address to jump to once the tape has loaded")
    elif not any(b['address'] <= design['entry'] < b['address'] + len(b['data']) for b in design['blocks']):
        warnings.append(f"the entry address ${design['entry']:04X} is not in any block this tape loads")
    return errors, warnings


def assemble_loader(address, sjasmplus, source_dir=None, stack=STACK_DEFAULT):
    """loader.tap for a loader that runs from `address`, with its CLEAR at
    `stack`, assembled in a scratch folder so the committed loader.tap is left
    alone. Returns its bytes, having checked LOADER_SIZE against the
    assembler's own figure."""
    import shutil
    import subprocess
    import tempfile

    source_dir = source_dir or os.path.dirname(os.path.abspath(__file__))
    # It runs in the scratch folder, so a relative path must be made whole
    # first; a bare name is left for the PATH to find.
    if os.path.dirname(sjasmplus):
        sjasmplus = os.path.abspath(sjasmplus)
    with tempfile.TemporaryDirectory() as work:
        for name in ('loader.s', 'basic.s', 'tape.s', 'counter.s'):
            shutil.copy(os.path.join(source_dir, name), work)
        done = subprocess.run([sjasmplus, '--nologo', f'-DLOADER_AT={address}', f'-DSTACK_AT={stack}',
                               '--sym=loader.sym', 'loader.s'], cwd=work, capture_output=True, text=True)
        if done.returncode != 0:
            raise ValueError(f"sjasmplus could not build the loader at ${address:04X}, stack ${stack:04X}:\n"
                             + (done.stdout + done.stderr).strip())
        symbols = {}
        with open(os.path.join(work, 'loader.sym'), encoding='utf-8') as file:
            for line in file:
                name, _, value = line.partition(': EQU ')
                if value:
                    symbols[name.strip()] = int(value.strip(), 0)
        if symbols.get('LOADER_SIZE') != LOADER_SIZE:
            raise ValueError(f"loader.s is now {symbols.get('LOADER_SIZE')} bytes, not {LOADER_SIZE}: "
                             "update LOADER_SIZE in loader.py (and the designer's tape_model.js)")
        with open(os.path.join(work, 'loader.tap'), 'rb') as file:
            return file.read()


def build_designed_tape(design, sjasmplus=None, source_dir=None):
    """Encode a design (see the top of this section): the loader, moved or
    given another stack if the design says so, the fast leader, the screen in its designed order, the
    blocks, and the jump. Returns (generator, warnings); raises if the design
    can't load."""
    source_dir = source_dir or os.path.dirname(os.path.abspath(__file__))
    loader_address = LOADER_DEFAULT if design['loader'] is None else design['loader']
    stack = design.get('stack')
    stack = STACK_DEFAULT if stack is None else stack
    errors, warnings = check_design(design, loader_address, stack)
    if errors:
        raise ValueError('this tape would not load:\n  ' + '\n  '.join(errors))

    if loader_address == LOADER_DEFAULT and stack == STACK_DEFAULT:
        with open(os.path.join(source_dir, 'loader.tap'), 'rb') as file:
            loader_tap = file.read()
    else:
        if not sjasmplus:
            raise ValueError(f"a loader at ${loader_address:04X} with its stack at ${stack:04X} means "
                             "assembling it: pass --sjasmplus with the path to sjasmplus")
        loader_tap = assemble_loader(loader_address, sjasmplus, source_dir, stack)

    gen = TapeGenerator()
    encode_tap_bytes(gen, loader_tap)
    gen.fast_leader()
    if design['screen'] is not None:
        for x, y, w, h in design['screen']['regions']:
            gen_block(gen, design['screen']['data'], x, y, w, h)
    gen.end_screen()
    for block in design['blocks']:
        gen.mem_header(block['address'], len(block['data']))
        for byte in block['data']:
            gen.dme_byte(byte)
    gen.end_blocks(design['entry'])
    gen.pulse(1000000)
    return gen, warnings


def build_fast_tape(source_path, loader_tap='loader.tap', entry_address=None,
                     screen_address=16384, screen_length=6912, regions=None):
    """
    Parse a standard-speed .tap/.tzx dump and re-encode its payload through
    our fast loader: `loader_tap`'s BASIC bootstrap at standard speed, then
    (if a Screen$-sized CODE block is found) the screen via analyse_screen_regions,
    then the remaining CODE blocks as plain memory blocks, ending with a jump
    to `entry_address` (auto-detected from the source's own BASIC loader via
    `find_usr_address` if not given).

    `regions` replaces analyse_screen_regions' content-aware default with a
    designed loading order - a list of (x, y, w, h) rectangles, as `read_pattern`
    returns from a designer's .screen.json. The picture then appears in that order, which
    is the whole point of designing one; nothing checks that the rectangles cover
    the screen, so a designed order can deliberately leave parts of it out.

    Only usable for tapes that already use standard ROM-speed blocks - a
    source tape with its own custom/turbo loader can't be generically parsed.
    """
    files = parse_source_files(source_path)

    screen_file = next(
        (f for f in files if f.type == TYPE_CODE
         and f.param1 == screen_address and f.length == screen_length),
        None,
    )
    code_files = [f for f in files if f.type == TYPE_CODE and f is not screen_file]

    if entry_address is None:
        basic_file = next((f for f in files if f.type == TYPE_PROGRAM), None)
        if basic_file is not None:
            entry_address = find_usr_address(basic_file.data)
        if entry_address is None:
            raise ValueError(
                f"{source_path}: couldn't auto-detect a USR entry address - pass entry_address explicitly"
            )

    gen = TapeGenerator()
    encode_tap_file(gen, loader_tap)
    gen.fast_leader()

    if screen_file is not None:
        order = analyse_screen_regions(screen_file.data) if regions is None else regions
        for x, y, w, h in order:
            gen_block(gen, screen_file.data, x, y, w, h)
    gen.end_screen()

    for f in code_files:
        gen.mem_header(f.param1, len(f.data))
        for d in f.data:
            gen.dme_byte(d)

    gen.end_blocks(entry_address)
    gen.pulse(1000000)

    return gen


def write_wav(samples, path, output_rate=OUTPUT_RATE):
    with wave.open(path, "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(1)
        wav_file.setframerate(output_rate)
        wav_file.writeframes(samples)


def _tzx_block(id_byte, body):
    return bytes([id_byte]) + body


def _tzx_pulses(lengths):
    """Pulses that are not a block of anything: runs of the same length as pure
    tone (ID $12), the rest as pulse sequences (ID $13, 255 at a time)."""
    out = bytearray()
    at = 0
    while at < len(lengths):
        run = 1
        while at + run < len(lengths) and lengths[at + run] == lengths[at]:
            run += 1
        length = int(round(lengths[at]))
        if length > 0xFFFF:
            # Not a pulse anyone can hear as one: the silence between blocks,
            # which a .tzx says as a pause in milliseconds.
            for _ in range(run):
                out += _tzx_block(0x20, struct.pack('<H', min(0xFFFF, round(length / 3500))))
        elif run >= 3:
            while run:
                step = min(run, 0xFFFF)
                out += _tzx_block(0x12, struct.pack('<HH', length, step))
                run -= step
        else:
            for start in range(0, run, 255):
                chunk = min(255, run - start)
                out += _tzx_block(0x13, bytes([chunk]) + struct.pack('<H', length) * chunk)
        at += run
    return bytes(out)


def _tzx_generalized(pilot, bits):
    """The fast encoding as one generalized data block (ID $19), the .tzx block
    made for loaders whose bits are not two pulses each: it spells out its own
    alphabet - here a 1 bit is one pulse of BIT_T and a 0 two of half that -
    and then sends the bits packed, one to a symbol. `pilot` is the leader and
    sync as (pulse length, count) runs, which go in the same block's pilot
    stream. It is the whole of a tape's fast part in about a byte a byte, where
    spelling the pulses out one by one takes twenty times that."""
    tones = []
    for length, count in pilot:
        tones.append((int(round(length)), count))
    # Each distinct leader pulse length is a symbol of one pulse; the runs
    # become (symbol, repetitions) pairs.
    alphabet = []
    for length, _ in tones:
        if length not in alphabet:
            alphabet.append(length)
    body = bytearray()
    body += struct.pack('<H', 0)               # pause after the block
    body += struct.pack('<I', len(tones))      # TOTP: (symbol, repetitions) pairs
    body += bytes([1, len(alphabet)])          # NPP, ASP: one pulse a symbol
    body += struct.pack('<I', len(bits))       # TOTD: one symbol a bit
    body += bytes([2, 2])                      # NPD, ASD: up to two pulses, two symbols
    for length in alphabet:
        body += bytes([0]) + struct.pack('<H', length)
    for length, count in tones:
        body += bytes([alphabet.index(length)]) + struct.pack('<H', count)
    half = int(round(BIT_T / 2))
    body += bytes([0]) + struct.pack('<HH', half, half)            # a 0 bit
    body += bytes([0]) + struct.pack('<HH', int(round(BIT_T)), 0)  # a 1 bit, one pulse long
    packed = bytearray()
    for at in range(0, len(bits), 8):
        byte = 0
        for i in range(8):
            byte = (byte << 1) | (bits[at + i] if at + i < len(bits) else 0)
        packed.append(byte)
    body += packed
    return _tzx_block(0x19, struct.pack('<I', len(body)) + bytes(body))


def write_tzx(gen, path):
    """The tape as a .tzx: the BASIC bootstrap as standard-speed blocks, the
    fast part as one generalized data block, and the silences as pauses. Unlike
    a .wav or a .csw this is the tape as it was meant, not a recording of it --
    a few tens of kilobytes rather than tens of megabytes - and any emulator
    that knows .tzx 1.20 plays it."""
    out = bytearray(b'ZXTape!\x1a' + bytes([1, 20]))
    segments = list(gen.segments)
    at = 0
    while at < len(segments):
        kind, items = segments[at]
        if kind == 'tap':
            for block in items:
                # Pause 0: the silence that follows is a pause block of its own,
                # because that is how the generator recorded it.
                out += _tzx_block(0x10, struct.pack('<HH', 0, len(block)) + block)
        elif kind == 'pilot' and at + 1 < len(segments) and segments[at + 1][0] == 'bits':
            out += _tzx_generalized(items, segments[at + 1][1])
            at += 1
        elif kind == 'bits':
            out += _tzx_generalized([], items)
        elif kind == 'pilot':
            out += _tzx_pulses([length for length, count in items for _ in range(count)])
        else:
            out += _tzx_pulses(items)
        at += 1
    with open(path, 'wb') as file:
        file.write(bytes(out))


def write_csw(gen, path, rate=700000):
    """The tape as a CSW v2 file: an exact list of pulse lengths rather than a
    sampled waveform, which an emulator loads without having to find the edges
    again. 700kHz is five T-states a sample; the ZX Spectrum emulator's CSW
    reader refuses anything over 768kHz as a misparse. Starts high, as the
    WAV does."""
    lengths = bytearray()
    count = 0
    error = 0.0
    for t_states in gen.pulses:
        exact = t_states * rate / 3500000 + error
        whole = max(1, int(round(exact)))
        error = exact - whole
        if whole <= 255:
            lengths.append(whole)
        else:
            lengths.append(0)
            lengths.extend(struct.pack('<I', whole))
        count += 1
    header = b'Compressed Square Wave\x1a' + bytes([2, 0])
    header += struct.pack('<IIBBB', rate, count, 1, 1, 0)
    header += b'zx-tape-loader'.ljust(16, b'\0')
    with open(path, 'wb') as file:
        file.write(header + bytes(lengths))


def wow_flutter_samples(gen):
    """`gen`'s tape with the wow and flutter of a real cassette, for write_wav.

    The pulses are stretched, not the waveform: this used to resample the
    finished WAV, and interpolating across a fast-loader half-pulse - four
    samples at 44.1kHz - left thousands of pulses that were neither a half-bit
    nor a whole one, which nothing could load."""
    return gen.render(wow_flutter_speed)
