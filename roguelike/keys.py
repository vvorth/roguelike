"""Input abstraction: raw key codes in, immutable intents out.

This module is a pure lookup table. It never touches the terminal, never reads
stdin, and never blocks. `curses` (and `curses.ascii`) is imported solely to
reference constants such as ``KEY_LEFT``/``KEY_RIGHT``/``KEY_UP``/``KEY_DOWN``
and ``curses.ascii.TAB`` by name instead of hardcoding their integer values;
no terminal-mutating curses function is called here, at import time or ever
(CONTRACT §0.3, §5).

Coordinates are ``(x, y)`` with the origin at the top-left, so **up is
``dy = -1``** and down is ``dy = +1`` (CONTRACT §0.1).
"""

from __future__ import annotations

import curses
import curses.ascii
from dataclasses import dataclass
from enum import Enum, auto

__all__ = [
    "CommandKind",
    "Command",
    "QUIT_COMMAND",
    "UNKNOWN_COMMAND",
    "HELP_ENTRIES",
    "translate_key",
]


class CommandKind(Enum):
    """The twelve things a key press can mean."""

    MOVE = auto()
    QUIT = auto()
    UNKNOWN = auto()
    DESCEND = auto()  # ">"
    ASCEND = auto()  # "<"
    AUTO_EXPLORE = auto()  # "E"
    WALK_PREFIX = auto()  # "w"
    FIRE = auto()  # "f"  -- NEW in v5
    TARGET_NEXT = auto()  # Tab (curses.ascii.TAB) -- NEW in v5
    HELP = auto()  # "?"
    ATTACK = auto()  # "a" -- prefix; the next key is the direction
    LOOK = auto()  # "x" -- examine mode; a cursor, not a turn


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


# --- Binding table (CONTRACT §5.1, CONTRACT-v4 §5.1) -------------------------
#
# Each entry is a movement delta paired with the keys that request it. Keys are
# written either as the single ASCII character they produce (so the digits are
# the characters '1'-'9', i.e. ord('4') == 52, not the integers 1-9) or as a
# curses keypad constant.
#
# Remember: dy = -1 is NORTH.
#
# The diagonals gained Shift+arrow and Shift+hjkl bindings in v4, each rotated
# 45 degrees CLOCKWISE from its base direction (Shift+Up -> north-east, and so
# on); they resolve to the exact same deltas numpad 1/3/7/9 and yubn have
# always produced, so they are added onto the existing diagonal rows rather
# than as new ones.
#
# TRAP: the curses constants for Shift+Up and Shift+Down are named KEY_SR and
# KEY_SF ("scroll reverse"/"scroll forward") for historical reasons that have
# nothing to do with arrows. KEY_SR is Shift+Up (-> north-east, negative dy);
# KEY_SF is Shift+Down (-> south-west, positive dy). Swapping them is the
# single most likely bug in this table.
_MOVEMENT_BINDINGS: tuple[tuple[tuple[int, int], tuple[int | str, ...]], ...] = (
    ((-1, 0), ("h", "4", curses.KEY_LEFT)),  # west
    ((1, 0), ("l", "6", curses.KEY_RIGHT)),  # east
    ((0, -1), ("k", "8", curses.KEY_UP)),  # north
    ((0, 1), ("j", "2", curses.KEY_DOWN)),  # south
    ((-1, -1), ("y", "7", "H", curses.KEY_SLEFT)),  # north-west (Shift+Left)
    ((1, -1), ("u", "9", "K", curses.KEY_SR)),  # north-east (Shift+Up)
    ((-1, 1), ("b", "1", "J", curses.KEY_SF)),  # south-west (Shift+Down)
    ((1, 1), ("n", "3", "L", curses.KEY_SRIGHT)),  # south-east (Shift+Right)
)

_QUIT_KEYS: tuple[int | str, ...] = ("q", "Q")

# Stairs are used by an explicit command, as in ADOM (CONTRACT-v3 §5) — not by
# stepping on the tile. AUTO_EXPLORE ("E") and WALK_PREFIX ("w") are new in
# v4. FIRE ("f") and TARGET_NEXT (Tab) are new in v5, for ranged combat and
# cycling targets (CONTRACT-v5 §5). HELP ("?") and ATTACK ("a") followed. All
# eight carry dx == dy == 0.
#
# ATTACK is a *prefix*, like WALK_PREFIX: the key that follows it names the
# direction. It is "a", deliberately NOT "F": "f" already opens ranged targeting,
# and two adjacent keys doing different combat things is a transposition waiting
# to happen. "F" is unbound.
#
# Tab is referenced as `curses.ascii.TAB`, never the literal 9, matching the
# rule already followed for KEY_SR and friends.
_NO_ARG_BINDINGS: tuple[tuple[CommandKind, int | str], ...] = (
    (CommandKind.DESCEND, ">"),
    (CommandKind.ASCEND, "<"),
    (CommandKind.AUTO_EXPLORE, "E"),
    (CommandKind.WALK_PREFIX, "w"),
    (CommandKind.FIRE, "f"),
    (CommandKind.TARGET_NEXT, curses.ascii.TAB),
    (CommandKind.HELP, "?"),
    (CommandKind.ATTACK, "a"),
    (CommandKind.LOOK, "x"),
)


# --- Help text (the one description of the bindings) -------------------------
#
# This table lives here, beside the binding tables, so that a key and its
# description cannot drift apart: adding a binding without describing it is a
# visible omission in one file rather than an invisible one across two.
#
# Pure data. Paginating it, fitting it to a terminal and drawing it are not
# this module's business -- `game.py` paginates and `render.py` draws.
HELP_ENTRIES: tuple[tuple[str, str], ...] = (
    ("h j k l", "move west / south / north / east"),
    ("y u b n", "move diagonally"),
    ("1-9", "move (numpad; 5 does nothing)"),
    ("arrows", "move"),
    ("H J K L", "move diagonally"),
    ("Shift+arrows", "move diagonally"),
    ("w + direction", "walk until something interesting"),
    ("E", "explore automatically"),
    (">", "descend, or travel to a known down staircase"),
    ("<", "ascend, or travel to a known up staircase"),
    ("move into a monster", "attack it"),
    ("a + direction", "attack that way without moving"),
    ("f", "aim the bow; Tab cycles targets, f shoots"),
    ("Tab", "next target while aiming"),
    ("x", "look around; direction keys move the cursor"),
    ("?", "this help"),
    ("q", "quit"),
)
"""Every binding, paired with what it does, in the order a player should read it.

Deliberately a flat tuple of ``(keys, description)`` rather than a nested
structure: the help screen is a list, and anything richer would be a layout
decision taken in the wrong module.
"""


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
    for kind, table_key in _NO_ARG_BINDINGS:
        bindings[_keycode(table_key)] = Command(kind)
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
