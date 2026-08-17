import wave
import numpy as np
from scipy.io import wavfile
from scipy.interpolate import interp1d

SAMPLE_RATE = 44307  # Samples/sec used for pulse-width timing math
OUTPUT_RATE = 44100  # Samples/sec the WAV file itself is written/read at


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
