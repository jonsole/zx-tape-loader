"""Runs lineloader.s on an emulated 48K against a synthesized tape and checks
what can only be checked by running it:

  - every line is exactly 224 T-states: the IN of each chunk comes 224 after
    the last one, for the whole load, whatever path each chunk took;
  - the sample sits at the same point of every line, outside the ULA's fetches;
  - nothing touches contended memory while the ULA is fetching the paper;
  - the blocks arrive byte for byte, and it jumps to the entry address.

Needs the `z80` package (pip install z80) and sjasmplus's symbol file:

    sjasmplus --sym=output/lineloader.sym lineloader.s
    python tests/run_lineloader.py [--flutter] [--jitter 0.1] [--bytes 3000]

The `z80` emulator has no ULA, so this models the 48K's frame itself: an
interrupt every 69,888 T-states, the I/O and memory contention windows (to
check nothing lands in them, rather than to delay it), and a stand-in ROM with
the two things the loader reads from it: $F3 at $0000 and $FF from $3900.
"""
import argparse
import os
import random
import sys

import z80

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from loader import TapeGenerator, wow_flutter_speed  # noqa: E402

FRAME = 69888
LINE = 224
FIRST_CONTENDED = 14335      # the first paper fetch group, frame T-states
IN_LATCH = 10                # T-states from an IN's fetch to its read


def symbols(path):
    out = {}
    with open(path) as f:
        for line in f:
            name, _, value = line.partition(': EQU ')
            if value:
                out[name.strip()] = int(value, 16)
    return out


def contended(frame_t):
    """Whether a contended access at frame T-state `frame_t` would be held:
    the 128 T-states of each paper line the ULA spends fetching."""
    line, at = divmod(frame_t - FIRST_CONTENDED, LINE)
    return 0 <= line < 192 and at < 128


def tap_body(path):
    """The BASIC's data block from a .tap, without its flag and checksum."""
    data = open(path, 'rb').read()
    blocks = []
    while data:
        n = data[0] | data[1] << 8
        blocks.append(data[2:2 + n])
        data = data[2 + n:]
    return blocks[1][1:-1]


class Tape:
    """EAR level at any T-state, from a list of pulse lengths."""

    def __init__(self, pulses, start):
        self.edges = []
        t = start
        for p in pulses:
            t += p
            self.edges.append(t)
        self.at = 0

    def level(self, t):
        while self.at < len(self.edges) and self.edges[self.at] <= t:
            self.at += 1
        return self.at & 1

    def end(self):
        return self.edges[-1]


def run(sym, basic, pulses, blocks, entry, max_frames):
    m = z80.Z80Machine()
    mem = bytearray(0x10000)
    mem[0] = 0xF3
    mem[0x3900:0x3B00] = b'\xff' * 0x200
    mem[23755:23755 + len(basic)] = basic
    m.set_memory_block(0, bytes(mem))
    m.pc = sym['CODE_START']
    m.sp = 0x6F00
    m.set_get_int_vector_callback(lambda: 0xFF)

    clock = {'base': 0, 'ft': 0}
    tape = Tape(pulses, start=FRAME)      # starts a frame in
    ins = []
    contended_hits = []
    started = [None]

    def now():
        return clock['base'] + (m.frame_tick - clock['ft']) % 100000

    def on_input(port):
        t = now()
        if port & 0xFF == 0xFE:
            ins.append(t)
            if started[0] is not None and contended(t % FRAME):
                contended_hits.append(('IN', t))
            return 0xBF | (tape.level(t) << 6)
        return 0xFF

    def on_write(addr, value):
        t = now()
        if started[0] is not None and contended(t % FRAME):
            contended_hits.append(('write', addr, t))
        mem_writes[addr] = value
        m.memory[addr] = value

    mem_writes = {}
    m.set_input_callback(on_input)
    m.set_write_callback(on_write)
    m.mark_addrs(0x4000, 0x4000, m.WRITE_MARK)
    m.set_breakpoint(entry)
    m.set_breakpoint(sym['PRE'])

    t = 0
    reached = False
    for frame in range(max_frames):
        # run to the next frame boundary, then raise the interrupt
        target = (frame + 1) * FRAME
        while t < target:
            clock['base'], clock['ft'] = t, m.frame_tick
            m.ticks_to_stop = target - t
            events = m.run()
            t = now()
            if m.pc == entry:
                reached = True
                break
            if m.pc == sym['PRE'] and started[0] is None:
                started[0] = t
                m.clear_breakpoint(sym['PRE'])
        if reached:
            break
        m.on_handle_active_int()
    return m, reached, ins, started[0], contended_hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sym', default='output/lineloader.sym')
    ap.add_argument('--tap', default='lineloader.tap')
    ap.add_argument('--bytes', type=int, default=2000)
    ap.add_argument('--flutter', action='store_true')
    ap.add_argument('--jitter', type=float, default=0.0, help='random error on each pulse, a fraction')
    ap.add_argument('--speed', type=float, default=1.0, help='tape speed, 1 is nominal')
    ap.add_argument('--seed', type=int, default=1)
    args = ap.parse_args()

    rnd = random.Random(args.seed)
    sym = symbols(args.sym)
    basic = tap_body(args.tap)

    # A screen's worth of picture and a block of code, with long runs of
    # zeros (the fastest data, so the most bytes a frame) and random bytes.
    screen = bytes(rnd.choice([0, 0, 0, rnd.randrange(256)]) for _ in range(6912))
    code = bytes([0] * (args.bytes // 2)) + bytes(rnd.randrange(256) for _ in range(args.bytes // 2))
    blocks = [(0x4000, screen), (0x8000, code)]
    entry = 0x8000

    gen = TapeGenerator()
    gen.line_leader()
    for address, data in blocks:
        gen.line_mem_header(address, len(data))
        for d in data:
            gen.line_byte(d)
    gen.line_end_blocks(entry)

    pulses = []
    t = 0.0
    for p in gen.pulses:
        if args.flutter:
            p = p / wow_flutter_speed(t / 3500000)
        p = p / args.speed * (1 + rnd.uniform(-args.jitter, args.jitter))
        t += p
        pulses.append(p)

    frames = int(sum(pulses) / FRAME) + 20
    m, reached, ins, started, hits = run(sym, basic, pulses, blocks, entry, frames)

    ok = True
    if not reached:
        print(f"FAIL: never reached the entry ${entry:04X} (PC=${m.pc:04X})")
        ok = False
    loop_ins = [t for t in ins if t >= started]
    gaps = {b - a for a, b in zip(loop_ins, loop_ins[1:])}
    phases = {(t - IN_LATCH) % LINE for t in loop_ins}
    print(f"{len(loop_ins)} lines sampled; gaps between samples: {sorted(gaps)}; "
          f"IN fetched at {sorted(phases)} T-states into the line")
    if gaps != {LINE}:
        print("FAIL: not every line is 224 T-states")
        ok = False
    if hits:
        print(f"FAIL: {len(hits)} contended accesses while the ULA fetches, first {hits[:3]}")
        ok = False
    for address, data in blocks:
        got = bytes(m.memory[address:address + len(data)])
        if got != data:
            bad = next(i for i in range(len(data)) if got[i] != data[i])
            print(f"FAIL: block at ${address:04X} differs from byte {bad}")
            ok = False
    print("PASS" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
