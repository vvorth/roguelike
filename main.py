"""Entry point for the terminal roguelike engine.

Usage:
    python main.py [--seed N] [--width W] [--height H]

The map occupies ``height`` rows and the status bar takes one more, so a level of
height 22 needs a 23-row terminal (CONTRACT §4, §7).
"""

from __future__ import annotations

import argparse
import random
import sys

from roguelike.game import play

DEFAULT_WIDTH = 80
DEFAULT_HEIGHT = 22


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line. Kept separate from :func:`main` so it is testable."""
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="An interactive terminal roguelike. Move with the arrow keys, "
        "hjkl (yubn for diagonals), or the number keys 1-9. Press q to quit.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="dungeon seed; the same seed always produces the same level "
        "(default: a randomly chosen one, reported on exit)",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=DEFAULT_WIDTH,
        help=f"map width in columns (default: {DEFAULT_WIDTH})",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=DEFAULT_HEIGHT,
        help=f"map height in rows; the status bar adds one more "
        f"(default: {DEFAULT_HEIGHT})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the game. Returns a process exit status."""
    args = parse_args(argv)
    seed = args.seed if args.seed is not None else random.randrange(2**31)

    try:
        play(seed, args.width, args.height)
    except KeyboardInterrupt:
        # curses.wrapper has already restored the terminal by this point.
        print(f"interrupted (seed: {seed})", file=sys.stderr)
        return 130
    except (ValueError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"seed: {seed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
