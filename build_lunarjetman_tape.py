from loader import (
    TapeGenerator,
    encode_tap_file,
    gen_block,
    write_wav,
    wow_flutter_samples,
)


def build():
    gen = TapeGenerator()

    encode_tap_file(gen, 'loader.tap')

    with open('lunarjetman.scr', 'rb') as file:
        data = file.read()

        gen.fast_leader()

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

        gen.end_screen()

    with open('jetman.bin', 'rb') as file:
        data = file.read()
        gen.mem_header(0x7000, len(data))
        for d in data:
            gen.dme_byte(d)
        gen.end_blocks(0x7000)

        gen.pulse(1000000)

    write_wav(gen.samples, "audio.wav")
    write_wav(wow_flutter_samples(gen), "tape.wav")

    print("WAV file 'tape.wav' created successfully!")


if __name__ == "__main__":
    build()
