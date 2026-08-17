
import wave
import numpy as np
from scipy import signal
from itertools import islice

def chunk(it, size):
    it = iter(it)
    return iter(lambda: tuple(islice(it, size)), ())

# Parameters for the WAV file
output_rate = 44100  # Samples per second
duration = 2.0       # Duration in seconds
frequency = 2000    # Frequency of the sine wave (Hz)
sample_rate = 44307



import numpy as np
from scipy.io import wavfile
from scipy.interpolate import interp1d

def apply_wow_flutter(audio, sample_rate, wow_freq=0.01, flutter_freq=0.2, depth=0.02):
    """
    Simulates wow and flutter effects on an audio signal.
    
    Parameters:
        audio (numpy array): Input audio signal.
        sample_rate (int): Sampling rate of the audio.
        wow_freq (float): Frequency of the wow effect (Hz).
        flutter_freq (float): Frequency of the flutter effect (Hz).
        depth (float): Depth of the pitch modulation (fraction of sample rate).
    
    Returns:
        numpy array: Audio signal with wow and flutter effects applied.
    """
    # Time array for the audio
    t = np.arange(len(audio)) / sample_rate
    
    # Generate wow and flutter modulation signals
    wow = depth * np.sin(2 * np.pi * wow_freq * t)
    flutter = depth * np.sin(2 * np.pi * flutter_freq * t)
    
    # Combine wow and flutter
    modulation = wow + flutter
    
    # Create a new time array with modulation applied
    modulated_time = t + modulation
    
    # Interpolate the audio signal to apply the modulation
    interpolator = interp1d(t, audio, kind='linear', fill_value="extrapolate")
    modulated_audio = interpolator(modulated_time)
    
    return modulated_audio



class TapeGenerator:

    def __init__(self):
        self.state = 127
        self.samples = bytearray()
        self.error = 0

    def pulse(self, t_states):
        samples = (t_states / 3500000 * sample_rate)
        samples += self.error
        self.error = samples - int(samples)
        self.samples.extend([self.state] * int(samples))
        self.state ^= 127


    def std_byte(self, b, num_bits = 8):
        for i in range(num_bits, 0, -1):
            bit = 1 if b & (1 << (i-1)) else 0
            if bit:
                self.pulse(1710)
                self.pulse(1718)
            else:
                self.pulse(1710/2)
                self.pulse(1710/2)

    def dme_byte(self, b, num_bits = 8):
        pw = (69888 / 8) / 13
        for i in range(num_bits, 0, -1):
            bit = 1 if b & (1 << (i-1)) else 0
            if bit:
                self.pulse(pw) # 1280,1248
            else:
                self.pulse(pw/2)
                self.pulse(pw/2)

    def scr_header(self, offset, len):
        self.dme_byte(offset + 256, 13)
        self.dme_byte(len - 1, 5)

    def mem_header(self, address, len):
        self.dme_byte(address + 256, 16)
        self.dme_byte(len, 16)




def chunks(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def get_pixel_address(x, y):
    y76 = y & 0b11000000 # third of screen
    y53 = y & 0b00111000
    y20 = y & 0b00000111
    address = (y76 << 5) + (y20 << 8) + (y53 << 2) + (x)
    return address

def get_attribute_address(x, y):
    y73 = y & 0b11111000
    address = (y73 << 2) + (x)
    return address


def gen_block(x,y,w,h,gen):
    h *= 8
    y *= 8
    for line in range(y,y+h):

        if line % 8 == 0:
            offset = 6144+get_attribute_address(x, line)
            chunk = data[offset : offset + w]
            gen.scr_header(offset, len(chunk))
            for d in chunk:
                gen.dme_byte(d)
                offset += 1

        offset = get_pixel_address(x, line)
        chunk = data[offset : offset + w]

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




gen = TapeGenerator()

# open tap file for basic loader
with open('loader.tap', 'rb') as file:
    data = file.read()
    while data:
        # get block size
        block_size = data[0] | data[1] << 8
        data = data[2:]

        # get block data
        block_data = data[:block_size]
        data = data[block_size:]

        # leader
        for i in range(0, 3184 if block_data[0] else 4096):
            gen.pulse(2168)

        # sync
        gen.pulse(667)
        gen.pulse(735)

        # data
        for d in block_data:
            gen.std_byte(d)

        # add pause if more blocks
        if data:
            gen.pulse(1000000)


# data
with open('lunarjetman.scr', 'rb') as file:
    data = file.read()

    # short leader
    for i in range(0, 512):
        gen.pulse(2168)

    # sync
    gen.pulse(600)
    gen.pulse(600)

    # generate screen blocks
    gen_block(11, 0, 10, 2, gen)
    gen_block(2, 2, 28, 5, gen)
    gen_block(1, 0, 30, 2, gen)
    gen_block(1, 2, 1, 5, gen)
    gen_block(30, 2, 1, 5, gen)  
    gen_block(1, 7, 30, 1, gen)
    gen_block(14, 16, 18, 6, gen)
    gen_block(0, 8, 32, 8, gen)
    gen_block(0, 16, 14, 6, gen)

    # signal end of screen blocks
    gen.dme_byte(0, 5)

with open('jetman.bin', 'rb') as file:
    data = file.read()
    gen.mem_header(0x7000, len(data))
    for d in data:
        gen.dme_byte(d)
    gen.mem_header(0x0000-256, 0x7000)

    gen.pulse(1000000)

# Create and write the WAV file
with wave.open("audio.wav", "w") as wav_file:
    wav_file.setnchannels(1)  # Mono
    wav_file.setsampwidth(1)  # 1 byte per sample (8-bit audio)
    wav_file.setframerate(output_rate)    
    wav_file.writeframes(gen.samples)

# Load a WAV file
sample_rate, audio = wavfile.read("audio.wav")

# Ensure audio is in float format for processing
if audio.dtype != np.float32:
    audio = audio / np.max(np.abs(audio), axis=0)

# Apply wow and flutter

modulated_audio = apply_wow_flutter(audio, sample_rate)

# Save the output
wavfile.write("tape.wav", sample_rate, (modulated_audio * 127).astype(np.uint8))


print("WAV file 'output.wav' created successfully!")

