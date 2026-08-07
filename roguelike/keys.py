"""Input abstraction: raw key codes in, immutable intents out.

This module is a pure lookup table. It never touches the terminal, never reads
stdin, and never blocks. `curses` is imported solely to reference the
``KEY_LEFT``/``KEY_RIGHT``/``KEY_UP``/``KEY_DOWN`` constants by name instead of
hardcoding their integer values; no terminal-mutating curses function is called
here, at import time or ever (CONTRACT §0.3, §5).

Coordinates are ``(x, y)`` with the origin at the top-left, so **up is
``dy = -1``** and down is ``dy = +1`` (CONTRACT §0.1).
"""

from __future__ import annotations

import curses
from dataclasses import dataclass
from enum import Enum, auto

__all__ = [
    "CommandKind",
    "Command",
    "QUIT_COMMAND",
    "UNKNOWN_COMMAND",
    "translate_key",
]


class CommandKind(Enum):
    """The three things a key press can mean."""

    MOVE = auto()
    QUIT = auto()
    UNKNOWN = auto()


@dataclass(frozen=True)
class Command:
    """An immutable player intent.

    ``dx``/``dy`` are meaningful only for :attr:`CommandKind.MOVE`, where they
    are always in ``{-1, 0, 1}`` and never both zero.
    """

    kind: CommandKind
    dx: int = 0
    dy: int = 0


QUIT_COMMAND: Command = Command(CommandKind.QUIT)
UNKNOWN_COMMAND: Command = Command(CommandKind.UNKNOWN)


# --- Binding table (CONTRACT §5.1) ------------------------------------------
#
# Each entry is a movement delta paired with the keys that request it. Keys are
# written either as the single ASCII character they produce (so the digits are
# the characters '1'-'9', i.e. ord('4') == 52, not the integers 1-9) or as a
# curses keypad constant.
#
# Remember: dy = -1 is NORTH.

_MOVEMENT_BINDINGS: tuple[tuple[tuple[int, int], tuple[int | str, ...]], ...] = (
    ((-1, 0), ("h", "4", curses.KEY_LEFT)),  # west
    ((1, 0), ("l", "6", curses.KEY_RIGHT)),  # east
    ((0, -1), ("k", "8", curses.KEY_UP)),  # north
    ((0, 1), ("j", "2", curses.KEY_DOWN)),  # south
    ((-1, -1), ("y", "7")),  # north-west
    ((1, -1), ("u", "9")),  # north-east
    ((-1, 1), ("b", "1")),  # south-west
    ((1, 1), ("n", "3")),  # south-east
)

_QUIT_KEYS: tuple[int | str, ...] = ("q", "Q")


def _keycode(key: int | str) -> int:
    """Normalise a table entry to the integer code `curses.getch` would give."""
    return ord(key) if isinstance(key, str) else key


def _build_bindings() -> dict[int, Command]:
    bindings: dict[int, Command] = {}
    for (dx, dy), table_keys in _MOVEMENT_BINDINGS:
        command = Command(CommandKind.MOVE, dx, dy)
        for table_key in table_keys:
            bindings[_keycode(table_key)] = command
    for table_key in _QUIT_KEYS:
        bindings[_keycode(table_key)] = QUIT_COMMAND
    return bindings


#: Every bound key code, mapped to the command it produces. Private: the public
#: surface of this module is `translate_key` plus the types above.
_KEY_BINDINGS: dict[int, Command] = _build_bindings()


def translate_key(key: int | str) -> Command:
    """Translate a raw key code into a :class:`Command`.

    `key` is either an ``int`` (as returned by ``curses.getch()``) or a
    length-1 ``str`` (a convenience for tests), which is converted with
    ``ord()``.

    An unrecognised key is ordinary input, not an error: it returns
    :data:`UNKNOWN_COMMAND`. Only a wrong *type* raises — anything that is
    neither an ``int`` nor a length-1 ``str`` raises :class:`TypeError`.
    """
    if isinstance(key, str):
        if len(key) != 1:
            raise TypeError(
                f"translate_key expects a length-1 str, got one of length {len(key)}"
            )
        code = ord(key)
    elif isinstance(key, int):
        # bool is an int subclass; True/False simply behave as key codes 1/0,
        # neither of which is bound.
        code = int(key)
    else:
        raise TypeError(
            f"translate_key expects an int or a length-1 str, got {type(key).__name__}"
        )
    return _KEY_BINDINGS.get(code, UNKNOWN_COMMAND)
