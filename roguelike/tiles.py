"""Tile vocabulary — the complete set of map cell kinds and their glyphs.

Imports nothing from :mod:`roguelike` (CONTRACT §10).
"""

from __future__ import annotations

from enum import IntEnum

__all__ = [
    "Tile",
    "TILE_CHARS",
    "WALKABLE",
    "PLAYER_CHAR",
    "DOOR_OPEN_CHAR",
    "tile_char",
    "is_walkable_tile",
]


class Tile(IntEnum):
    """The complete tile vocabulary (CONTRACT §1). Do not add members."""

    WALL = 0
    FLOOR = 1
    DOOR = 2


TILE_CHARS: dict[Tile, str] = {
    Tile.WALL: "#",
    Tile.FLOOR: ".",
    Tile.DOOR: "+",
}

WALKABLE: frozenset[Tile] = frozenset({Tile.FLOOR, Tile.DOOR})

PLAYER_CHAR: str = "@"

DOOR_OPEN_CHAR: str = "'"
"""Glyph for an open door (CONTRACT-v2 §1). ``TILE_CHARS[Tile.DOOR]`` ("+") is the
closed-door glyph; which one to draw is a runtime concern decided from ``open_doors``,
not a new :class:`Tile` member."""


def tile_char(tile: Tile) -> str:
    """Return the display glyph for ``tile``.

    Raises:
        KeyError: if ``tile`` is not a key of :data:`TILE_CHARS`.
    """
    return TILE_CHARS[tile]


def is_walkable_tile(tile: Tile) -> bool:
    """Return ``True`` iff ``tile`` may be stepped on (FLOOR and DOOR)."""
    return tile in WALKABLE
