import argparse

from loader import build_fast_tape, write_wav, apply_wow_flutter_to_wav


def main():
    parser = argparse.ArgumentParser(
        description="Re-encode a standard-speed .tap/.tzx dump through the fast loader in loader.s."
    )
    parser.add_argument('source', help='source .tap or .tzx file to convert')
    parser.add_argument('output', help='output .wav file')
    parser.add_argument('--loader-tap', default='loader.tap', help='this project\'s BASIC bootstrap (default: loader.tap)')
    parser.add_argument('--entry', type=lambda s: int(s, 0), default=None,
                         help='entry address to jump to after loading (default: auto-detect from the source\'s own USR call)')
    parser.add_argument('--no-flutter', action='store_true', help='skip the wow/flutter pass')
    args = parser.parse_args()

    gen = build_fast_tape(args.source, loader_tap=args.loader_tap, entry_address=args.entry)

    write_wav(gen.samples, args.output)
    if not args.no_flutter:
        apply_wow_flutter_to_wav(args.output, args.output)

    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
