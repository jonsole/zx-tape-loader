"""Lunar Jetman through the line-locked loader (lineloader.s): the screen, then
the game, with the border pattern running all the while.

Reads lineloader.tap, LunarJetman.scr and jetman.bin; writes lineaudio.wav,
linetape.wav (with wow and flutter) and linetape.tzx.
"""
from loader import build_line_tape, write_tzx, write_wav, wow_flutter_samples


def build():
    with open('LunarJetman.scr', 'rb') as file:
        screen = file.read()
    with open('jetman.bin', 'rb') as file:
        game = file.read()

    gen = build_line_tape([(0x4000, screen), (0x7000, game)], 0x7000)

    write_wav(gen.samples, 'lineaudio.wav')
    write_wav(wow_flutter_samples(gen), 'linetape.wav')
    write_tzx(gen, 'linetape.tzx')
    print("Wrote linetape.wav and linetape.tzx")


if __name__ == "__main__":
    build()
