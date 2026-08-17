from loader import (
    TapeGenerator,
    encode_tap_file,
    gen_block,
    write_wav,
    apply_wow_flutter_to_wav,
)


def build():
    gen = TapeGenerator()

    encode_tap_file(gen, 'loader.tap')

    with open('lunarjetman.scr', 'rb') as file:
        data = file.read()

        # short leader
        for i in range(0, 512):
            gen.pulse(2168)

        # sync
        gen.pulse(600)
        gen.pulse(600)

        # generate screen blocks
        gen_block(gen, data, 11, 0, 10, 2)
        gen_block(gen, data, 2, 2, 28, 5)
        gen_block(gen, data, 1, 0, 30, 2)
        gen_block(gen, data, 1, 2, 1, 5)
        gen_block(gen, data, 30, 2, 1, 5)
        gen_block(gen, data, 1, 7, 30, 1)
        gen_block(gen, data, 14, 16, 18, 6)
        gen_block(gen, data, 0, 8, 32, 8)
        gen_block(gen, data, 0, 16, 14, 6)

        # signal end of screen blocks
        gen.dme_byte(0, 5)

    with open('jetman.bin', 'rb') as file:
        data = file.read()
        gen.mem_header(0x7000, len(data))
        for d in data:
            gen.dme_byte(d)
        gen.mem_header(0x0000 - 256, 0x7000)

        gen.pulse(1000000)

    write_wav(gen.samples, "audio.wav")
    apply_wow_flutter_to_wav("audio.wav", "tape.wav")

    print("WAV file 'tape.wav' created successfully!")


if __name__ == "__main__":
    build()
