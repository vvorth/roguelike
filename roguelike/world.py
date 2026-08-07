"""Runtime world predicates — "what is the world like *right now*" (CONTRACT-v2 §13).

``Level`` is frozen and terrain-only (CONTRACT-v2 §0.6): it can answer what the map looks
like, but not whether a door is currently open. That mutable fact lives outside ``Level``,
in the caller's ``open_doors`` set, and is threaded through explicitly as a parameter.
This module is the single place that combines the two into the two runtime predicates
every consumer (movement, field of view) needs: passability and transparency.

Passability and transparency genuinely differ and must not be collapsed into one
function: a ``FLOOR`` cell is both; a closed ``DOOR`` is neither; an open ``DOOR`` is
both; a ``WALL`` is neither — but for a different reason (terrain, not door state), and
future terrain (a window, a chasm) would separate them further. That the three tiles
that exist today make ``is_passable`` and ``is_transparent`` agree everywhere is
incidental to today's tile set, not a rule either function may rely on.

All three predicates are pure and never raise: no module-level state, no caching, no
mutation of either argument, no I/O. Out-of-bounds coordinates return ``False``.

Imports only :mod:`roguelike.level` and :mod:`roguelike.tiles` (CONTRACT-v2 §10). Never
touches curses.
"""

from __future__ import annotations

from roguelike.level import Level
from roguelike.tiles import Tile

__all__ = ["is_passable", "is_transparent", "is_closed_door"]


def is_closed_door(
    level: Level, open_doors: frozenset[tuple[int, int]], x: int, y: int
) -> bool:
    """Return ``True`` iff ``(x, y)`` is a ``DOOR`` cell not present in ``open_doors``.

    ``False`` out of bounds, for any other tile, and for an open door. Never raises.
    """
    if not level.in_bounds(x, y):
        return False
    return level.tile_at(x, y) is Tile.DOOR and (x, y) not in open_doors


def is_passable(
    level: Level, open_doors: frozenset[tuple[int, int]], x: int, y: int
) -> bool:
    """Return ``True`` iff ``(x, y)`` can be stepped onto right now.

    Terrain-walkable (``level.is_walkable``) **and** not a closed door. A ``FLOOR`` cell
    is always passable; a ``DOOR`` is passable only when open; a ``WALL`` and every
    out-of-bounds coordinate are never passable. Never raises.
    """
    if not level.is_walkable(x, y):
        return False
    return not is_closed_door(level, open_doors, x, y)


def is_transparent(
    level: Level, open_doors: frozenset[tuple[int, int]], x: int, y: int
) -> bool:
    """Return ``True`` iff sight passes through ``(x, y)`` right now.

    In bounds, tile is not ``WALL``, and not a closed door. A ``FLOOR`` cell is always
    transparent; an open ``DOOR`` is transparent, a closed one is not; a ``WALL`` and
    every out-of-bounds coordinate are never transparent. Never raises.
    """
    if not level.in_bounds(x, y):
        return False
    if level.tile_at(x, y) is Tile.WALL:
        return False
    return not is_closed_door(level, open_doors, x, y)
