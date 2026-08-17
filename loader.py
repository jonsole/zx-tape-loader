from dataclasses import dataclass
import wave
import numpy as np
from scipy.io import wavfile
from scipy.interpolate import interp1d

SAMPLE_RATE = 44307  # Samples/sec used for pulse-width timing math
OUTPUT_RATE = 44100  # Samples/sec the WAV file itself is written/read at

TYPE_PROGRAM = 0
TYPE_NUMBER_ARRAY = 1
TYPE_CHARACTER_ARRAY = 2
TYPE_CODE = 3


def apply_wow_flutter(audio, sample_rate, wow_freq=0.01, flutter_freq=0.2, depth=0.02):
    """Simulate tape wow/flutter by resampling `audio` along a modulated time axis."""
    t = np.arange(len(audio)) / sample_rate

    wow = depth * np.sin(2 * np.pi * wow_freq * t)
    flutter = depth * np.sin(2 * np.pi * flutter_freq * t)
    modulated_time = t + wow + flutter

    interpolator = interp1d(t, audio, kind='linear', fill_value="extrapolate")
    return interpolator(modulated_time)


class TapeGenerator:

    def __init__(self, sample_rate=SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.state = 127
        self.samples = bytearray()
        self.error = 0

    def pulse(self, t_states):
        samples = (t_states / 3500000 * self.sample_rate)
        samples += self.error
        self.error = samples - int(samples)
        self.samples.extend([self.state] * int(samples))
        self.state ^= 127

    def std_byte(self, b, num_bits=8):
        for i in range(num_bits, 0, -1):
            bit = 1 if b & (1 << (i - 1)) else 0
            if bit:
                self.pulse(1710)
                self.pulse(1718)
            else:
                self.pulse(1710 / 2)
                self.pulse(1710 / 2)

    def dme_byte(self, b, num_bits=8):
        pw = (69888 / 8) / 13
        for i in range(num_bits, 0, -1):
            bit = 1 if b & (1 << (i - 1)) else 0
            if bit:
                self.pulse(pw)
            else:
                self.pulse(pw / 2)
                self.pulse(pw / 2)

    def scr_header(self, offset, len):
        self.dme_byte(offset + 256, 13)
        self.dme_byte(len - 1, 5)

    def mem_header(self, address, len):
        self.dme_byte(address + 256, 16)
        self.dme_byte(len, 16)

    def std_block(self, block_data):
        """Emit one .tap block (leader/sync/data) at standard ROM timing."""
        for _ in range(3184 if block_data[0] else 4096):
            self.pulse(2168)
        self.pulse(667)
        self.pulse(735)
        for d in block_data:
            self.std_byte(d)


def encode_tap_file(gen, path, pause_between_blocks=1000000):
    """Encode every block of a .tap file onto `gen` at standard ROM timing."""
    with open(path, 'rb') as file:
        data = file.read()

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


def build_fast_tape(source_path, loader_tap='loader.tap', entry_address=None,
                     screen_address=16384, screen_length=6912):
    """
    Parse a standard-speed .tap/.tzx dump and re-encode its payload through
    our fast loader: `loader_tap`'s BASIC bootstrap at standard speed, then
    (if a Screen$-sized CODE block is found) the screen via analyse_screen_regions,
    then the remaining CODE blocks as plain memory blocks, ending with a jump
    to `entry_address` (auto-detected from the source's own BASIC loader via
    `find_usr_address` if not given).

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

    if screen_file is not None:
        for x, y, w, h in analyse_screen_regions(screen_file.data):
            gen_block(gen, screen_file.data, x, y, w, h)
        gen.dme_byte(0, 5)  # signal end of screen blocks

    for f in code_files:
        gen.mem_header(f.param1, len(f.data))
        for d in f.data:
            gen.dme_byte(d)

    gen.mem_header(0x0000 - 256, entry_address)  # H=0 sentinel - signals "jump to (IX)" in tape.s
    gen.pulse(1000000)

    return gen


def write_wav(samples, path, output_rate=OUTPUT_RATE):
    with wave.open(path, "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(1)
        wav_file.setframerate(output_rate)
        wav_file.writeframes(samples)


def apply_wow_flutter_to_wav(in_path, out_path):
    sample_rate, audio = wavfile.read(in_path)

    if audio.dtype != np.float32:
        audio = audio / np.max(np.abs(audio), axis=0)

    modulated_audio = apply_wow_flutter(audio, sample_rate)
    wavfile.write(out_path, sample_rate, (modulated_audio * 127).astype(np.uint8))
