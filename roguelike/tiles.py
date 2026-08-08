"""Tile vocabulary — the complete set of map cell kinds and their glyphs.

Imports nothing from :mod:`roguelike` (CONTRACT §10).
"""

from __future__ import annotations

from enum import IntEnum

__all__ = [
    "Tile",
    "TILE_CHARS",
    "WALKABLE",
    "STAIRS",
    "PLAYER_CHAR",
    "DOOR_OPEN_CHAR",
    "tile_char",
    "is_walkable_tile",
]


class Tile(IntEnum):
    """The complete tile vocabulary (CONTRACT-v3 §1). Do not add members."""

    WALL = 0
    FLOOR = 1
    DOOR = 2
    STAIRS_UP = 3
    STAIRS_DOWN = 4


TILE_CHARS: dict[Tile, str] = {
    Tile.WALL: "#",
    Tile.FLOOR: ".",
    Tile.DOOR: "+",
    Tile.STAIRS_UP: "<",
    Tile.STAIRS_DOWN: ">",
}

WALKABLE: frozenset[Tile] = frozenset(
    {Tile.FLOOR, Tile.DOOR, Tile.STAIRS_UP, Tile.STAIRS_DOWN}
)

STAIRS: frozenset[Tile] = frozenset({Tile.STAIRS_UP, Tile.STAIRS_DOWN})
"""Both staircase tiles. Both are walkable (CONTRACT-v3 §0.9) — this is what makes them
passable and transparent throughout the engine with no change to ``world.py``, ``fov.py``
or ``movement.py``, which are defined in terms of :data:`WALKABLE` and "not ``WALL``"."""

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
    """Return ``True`` iff ``tile`` may be stepped on (FLOOR, DOOR, and both stairs)."""
    return tile in WALKABLE
