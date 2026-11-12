"""Generates lineloader_gen.s for lineloader.s: the border rows, compiled from
a pattern, and the schedule of what every other line of the frame does. The
rows aren't in the loader the BASIC carries: lineloader.s assembles them into
lineborder.bin, which the tape brings first (see build_line_tape in loader.py).

    python make_lineloader.py [border.txt]

The pattern is text: a `background <colour>` line, then up to 48 rows of 44
cells, one per scanline from the top border's line 16, each cell 8 pixels (4
T-states) across the whole visible width - the 48 pixels of left border, the
256 above the paper and the 48 of right border. A cell is a colour digit 0-7,
or `.` for the background. `#` starts a comment.

What a row can hold is limited by what one scanline can do:
  - the five colours used most live in B', D', E', H' and L' for the whole
    border, and a change to one of them is an OUT (C),r: 12 T-states, so
    changes are at least three cells apart;
  - a change to any other colour is LD A,n and OUT ($FE),A, 18 T-states;
  - the last three cells of a row are drawn while the next line is sampling,
    so they keep the colour of the cell before them.
Each OUT is timed for its write to land in the middle of its cell's 4
T-states, where it can; then a ULA a T-state early or late draws it in the
same place. A row that can't be done is reported, with its cells.
"""

import re
import sys

LINE_T = 224
ROWS = 48                  # scanlines 16 to 63
CELLS = 44                 # 352 pixels
REGS = ['B', 'D', 'E', 'H', 'L']

# Where things are in a chunk, counted from its first T-state (its IN). These
# follow linetape.s: the head and an EXX take 40, the EXX back the last 4.
ROW_FROM = 40
ROW_TO = LINE_T - 4
OUT_T = 12

# When an OUT (C),r's write reaches the border latch, counted from the start
# of the instruction (the latch then shows it from the 4 T-state step it lands
# in). Measured on the zx-spectrum-emulator; see tests/README.md.
OUT_WRITE = 9

# Where a scanline's left border starts in a chunk: the chunk for scanline s
# starts LINE_START T-states into scanline s - 1, and the left border of s
# starts 24 T-states before its paper (224 * s - 24).
def left_border(line_start):
    return LINE_T - 24 - line_start


# ---------------------------------------------------------------------------
# The schedule

FIRST_FREE = 50            # FIFO slots 0-49 are PRE, the border and POST
FRAME = 312
PAPER_END = 241            # slot 241 is chunk 255, scanline 256: no paper
I_LINES = 32               # enough for 26 bytes a frame of all-zero data


def make_schedule():
    """What each line from slot 50 to 311 is: 'D' (two samples), 'Z' (none)
    or 'I' (one queued byte). Exactly 156 D lines, so the 312 samples of a
    frame are all decoded by its end; I lines only off the paper."""
    total_d = FRAME // 2
    off = FRAME - PAPER_END
    off_d = off - I_LINES
    paper = PAPER_END - FIRST_FREE
    paper_d = total_d - off_d
    kinds = []
    for n in range(paper):
        # D lines as evenly as they will go
        kinds.append('D' if (n + 1) * paper_d // paper > n * paper_d // paper else 'Z')
    for n in range(off):
        kinds.append('D' if (n + 1) * off_d // off > n * off_d // off else 'I')
    # Check the FIFO never runs dry: the line at slot k has written slot k.
    consumed = 0
    for at, kind in enumerate(kinds):
        slot = FIRST_FREE + at
        if kind == 'D':
            consumed += 2
            assert consumed <= slot + 1, f"the FIFO runs dry at slot {slot}"
    assert consumed == FRAME, consumed
    return kinds


# ---------------------------------------------------------------------------
# The border

class PatternError(Exception):
    pass


def read_pattern(path):
    background = 0
    rows = []
    with open(path) as f:
        for number, line in enumerate(f, 1):
            line = line.split('#', 1)[0].rstrip()
            if not line.strip():
                continue
            m = re.fullmatch(r'\s*background\s+([0-7])\s*', line)
            if m:
                background = int(m.group(1))
                continue
            if not re.fullmatch(r'[0-7.]{%d}' % CELLS, line):
                raise PatternError(f"{path}:{number}: a row is {CELLS} cells of 0-7 or '.'")
            rows.append(line)
    if len(rows) > ROWS:
        raise PatternError(f"{path}: {len(rows)} rows, at most {ROWS}")
    rows += ['.' * CELLS] * (ROWS - len(rows))
    return background, [[background if c == '.' else int(c) for c in row] for row in rows]


def fillable(gap):
    """PAD can make it: NOP 4, CP 7, LD A,I 9, JP 10, and anything from 7."""
    return gap == 0 or gap == 4 or gap >= 7


# The ways to change colour, as (T-states, T-states to the write, needs the
# colour in a register): OUT (C),r; LD A,r and OUT ($FE),A; LD A,n and OUT
# ($FE),A. OUT ($FE),A reaches its I/O cycle a T-state sooner than OUT (C),r.
FORMS = {
    'C': (12, 0, True),
    'R': (15, 4 - 1, True),
    'I': (18, 7 - 1, False),
}


def compile_row(index, changes, regs, origin, row_to=ROW_TO):
    """Lays one row's changes out: each OUT's write as near the middle of its
    4 T-state step as the gaps allow, the row ending with its EXX at `row_to`.
    Returns the code, or None if the row can't be done."""
    from functools import lru_cache
    ROW_TO = row_to
    reg_of = {c: g for g, c in regs.items()}

    @lru_cache(maxsize=None)
    def best(i, end):
        if i == len(changes):
            return (0, ()) if fillable(ROW_TO - end) else None
        j, colour = changes[i]
        found = None
        for form, (length, offset, needs_reg) in FORMS.items():
            if needs_reg and colour not in reg_of:
                continue
            for phase in (1, 2, 0, 3):
                s = origin + 4 * j + phase - OUT_WRITE - offset
                if s < end or not fillable(s - end) or s + length > ROW_TO:
                    continue
                rest = best(i + 1, s + length)
                if rest is None:
                    continue
                cost = rest[0] + (phase in (0, 3)) * 10 + length
                if found is None or cost < found[0]:
                    found = (cost, ((s, form, colour),) + rest[1])
        return found

    plan = best(0, ROW_FROM)
    if plan is None:
        return None
    edges = sum(1 for s, form, colour in plan[1]
                if (s + OUT_WRITE + FORMS[form][1] - origin) % 4 in (0, 3))
    code = [f"\t\tHEAD_AT FIFO + {1 + index}", "\t\tEXX"]
    at = ROW_FROM
    for s, form, colour in plan[1]:
        if s > at:
            code.append(f"\t\tPAD {s - at}")
        if form == 'C':
            code.append(f"\t\tOUT (C),{reg_of[colour]}\t\t; {s}")
        elif form == 'R':
            code.append(f"\t\tLD A,{reg_of[colour]}\t\t; {s}")
            code.append("\t\tOUT ($FE),A")
        else:
            code.append(f"\t\tLD A,{colour}\t\t; {s}")
            code.append("\t\tOUT ($FE),A")
        at = s + FORMS[form][0]
    if ROW_TO > at:
        code.append(f"\t\tPAD {ROW_TO - at}")
    code.append("\t\tEXX")
    return code, edges


def last_cell(origin, row_to):
    """The last cell a row ending at `row_to` can change colour in."""
    return (row_to - OUT_T - origin + OUT_WRITE) // 4


def compile_border(background, rows, line_start=144):
    """The rows as code, and the colours PRE puts in B', D', E', H' and L'
    for the whole border: the five used most. The last row ends 10 T-states
    early, with JP POST."""
    origin = left_border(line_start)
    ends = [ROW_TO] * (len(rows) - 1) + [ROW_TO - 10]
    per_row = []
    uses = {}
    colour = background
    for r, row in enumerate(rows):
        changes = []
        for j, c in enumerate(row):
            if c != colour:
                limit = last_cell(origin, ends[r])
                if j > limit:
                    raise PatternError(f"row {r}, cell {j}: the last {CELLS - 1 - limit} cells "
                                       f"of this row can't change colour (they're drawn while the "
                                       f"next line samples the tape) - make them {colour}")
                changes.append((j, c))
                uses[c] = uses.get(c, 0) + 1
                colour = c
        per_row.append(changes)
    kept = sorted(uses, key=lambda c: -uses[c])[:len(REGS)]
    regs = {REGS[n]: c for n, c in enumerate(kept)}
    code = []
    edges = 0
    for r, changes in enumerate(per_row):
        row_code = compile_row(r, tuple(changes), regs, origin, ends[r])
        if row_code is None:
            cells = ", ".join(str(j) for j, _ in changes)
            raise PatternError(f"row {r}: its changes (at cells {cells}) are too close together - "
                               f"they need three cells between them, four or five next to a "
                               f"colour that isn't one of the five most used ({kept})")
        code += row_code[0]
        edges += row_code[1]
    code.append("\t\tJP POST")
    return regs, code, edges


def write_gen(path, background, regs, rows_code, schedule):
    colours = [regs.get(g, 0) for g in ('L', 'H', 'E', 'D', 'B')] + [background]
    lines = [
        ";; Generated by make_lineloader.py - edit border.txt and run it again.",
        "",
        ";; COLOURS as PRE loads them: L', H', E', D', B', the background",
        "BORDER_COLOURS\tMACRO",
        "\t\tDB " + ", ".join(str(c) for c in colours),
        "\t\tENDM",
        "",
        "BORDER_ROWS\tMACRO",
    ]
    lines += rows_code
    lines += ["\t\tENDM", "", "SCHEDULE_TABLE\tMACRO"]
    names = {'D': 'D_LINE', 'Z': 'Z_LINE', 'I': 'I_LINE'}
    table = ['0'] * FIRST_FREE + [f"LOW {names[k]}" for k in schedule] + ['LOW PRE']
    for at in range(0, len(table), 8):
        lines.append("\t\tDB " + ",".join(table[at:at + 8]))
    lines += ["\t\tENDM", ""]
    with open(path, 'w') as f:
        f.write("\n".join(lines))


def line_start_from(source='lineloader.s'):
    with open(source) as f:
        m = re.search(r'^LINE_START\s+EQU\s+(\d+)', f.read(), re.M)
    return int(m.group(1))


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else 'border.txt'
    background, rows = read_pattern(pattern)
    try:
        init, code, edges = compile_border(background, rows, line_start_from())
    except PatternError as e:
        sys.exit(f"{pattern}: {e}")
    write_gen('lineloader_gen.s', background, init, code, make_schedule())
    print(f"wrote lineloader_gen.s from {pattern}")
    if edges:
        print(f"note: {edges} colour change(s) are too crowded to be timed for the middle of "
              f"their cell, so a 48K whose ULA runs a T-state early or late may draw them a "
              f"cell out")


if __name__ == '__main__':
    main()
