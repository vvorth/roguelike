"""Movement and collision — a pure, single-step move resolver (CONTRACT-v2 §6).

All coordinates are ``(x, y)`` with the origin at the top-left; ``x`` grows right and
``y`` grows down, so "up" is ``dy = -1`` (CONTRACT §0.1).

v2 changes what "passable" means. In v1, walls, the map border and off-map coordinates
all took a single uniform rejection path because ``Level.is_walkable`` returned ``False``
off-map and never raised. That is still true, but ``Level`` alone can no longer answer
the question: a closed door is terrain-walkable but currently impassable, and that state
lives outside ``Level`` (CONTRACT-v2 §0.6). ``try_move`` now asks
:func:`roguelike.world.is_passable`, which folds terrain and door state into the same
uniform rejection path — walls, borders, off-map and closed doors are all "not passable",
one branch, no re-derived bounds check. The only rejection that carries extra detail is a
closed door, which is reported back via ``MoveResult.blocked_by_door`` so a caller can
turn a bump into an open.

Diagonal moves are legal iff the destination is passable — there are no corner-cutting
rules (v1 BRIEF Q7, unchanged).

v5 adds a **third** rejection that carries detail: the destination holds another actor
(CONTRACT-v5 §7.9). ``occupied`` is a new, defaulted parameter — every v1–v4 call site
keeps working untouched — and a target cell in it is reported back through
``MoveResult.blocked_by_npc`` so the caller can turn the bump into an attack. This module
still knows nothing about monsters, combat or hit points: it is handed a set of
coordinates and reports which one it refused, exactly as it already does for doors.

**Occupancy is tested first**, before passability and before the door check, so a cell
holding both a closed door and an actor reports ``blocked_by_npc`` — you attack the thing,
not the door. That combination cannot arise while monsters stand only on passable cells,
but the precedence is defined rather than accidental.

**v6 changes nothing here, and that is a result rather than an omission.** The increment
adds items, chests, damage types, resistances and shields; none of them is a collision
rule. A chest is not an obstacle — the player stands *on* it to open it — so it never
joins ``occupied``, and there is no ``Tile.CHEST`` for the terrain half to learn about
(CONTRACT-v6 §27.3). The pack changes what a bump *does*, in ``game.py``, and nothing
about whether the step is allowed. So ``MoveResult`` keeps its four fields, ``try_move``
keeps its six parameters, and this module keeps its two imports.

Imports only from :mod:`roguelike.level` and :mod:`roguelike.world` (CONTRACT-v2 §10).
Never touches curses.
"""

from __future__ import annotations

from dataclasses import dataclass

from roguelike.level import Level
from roguelike.world import is_closed_door, is_passable

__all__ = ["MoveResult", "try_move", "is_blocked"]

_STEPS = (-1, 0, 1)


@dataclass(frozen=True)
class MoveResult:
    """The outcome of a single attempted step.

    ``position`` is the new position when ``moved`` is ``True``, and the **original,
    unchanged** position when it is ``False``. The caller increments its turn counter
    iff ``moved`` is ``True`` — this is how "a rejected move consumes no turn" is
    realised (v1 BRIEF Q13).

    ``blocked_by_door`` is the door's coordinates when — and only when — the rejection
    was caused by a closed door; ``None`` for every other outcome, including a
    successful move. It lets a caller distinguish "you bumped a door" (which can be
    turned into an open) from "you walked into a wall" (which cannot).

    ``blocked_by_npc`` is the same idea for the v5 rejection: the coordinates of the
    occupied cell the step was refused by, and ``None`` for every other outcome. A caller
    turns it into a melee attack (CONTRACT-v5 §7.9). At most one of the two detail fields
    is ever set, and ``blocked_by_npc`` wins if a cell could somehow be both.
    """

    position: tuple[int, int]
    moved: bool
    blocked_by_door: tuple[int, int] | None = None
    blocked_by_npc: tuple[int, int] | None = None


def try_move(
    level: Level,
    position: tuple[int, int],
    dx: int,
    dy: int,
    open_doors: frozenset[tuple[int, int]] = frozenset(),
    occupied: frozenset[tuple[int, int]] = frozenset(),
) -> MoveResult:
    """Resolve a single step from ``position`` by ``(dx, dy)``.

    Pure: mutates neither ``level``, ``position`` nor ``open_doors``, performs no I/O,
    and never raises for an ordinary illegal move — walking into a wall or a closed
    door is normal input, not an error.

    ``occupied`` holds every cell another actor is standing on (CONTRACT-v5 §7.9). It
    defaults to the empty set, so a caller with no monsters — every v1–v4 call site —
    behaves exactly as before. This module never asks *what* occupies a cell.

    Returns:
        ``MoveResult(target, True)`` if ``world.is_passable(level, open_doors, *target)``
        and the target is unoccupied, otherwise ``MoveResult(position, False, ...)`` with
        the input position returned unchanged (the same tuple object, not an equal copy)
        and at most one detail field set. ``blocked_by_npc`` is ``(tx, ty)`` when the
        target is in ``occupied`` — **checked first**, so it wins over a closed door on
        the same cell. ``blocked_by_door`` is ``(tx, ty)`` when the target is an
        unoccupied closed door. Both are ``None`` for every other rejection (wall,
        border, off-map). A zero delta (``dx == dy == 0``) is not an error; it simply
        does not move, and is never reported as door- or actor-blocked even when standing
        on a door or sharing a cell with something in ``occupied``.

    Raises:
        ValueError: if ``dx`` or ``dy`` is outside ``{-1, 0, 1}``. Movement is
            single-step only. Raised before any passability check, including the
            door check.
    """
    if dx not in _STEPS:
        raise ValueError(f"dx must be -1, 0 or 1, got {dx!r}")
    if dy not in _STEPS:
        raise ValueError(f"dy must be -1, 0 or 1, got {dy!r}")

    if dx == 0 and dy == 0:
        # Not an error, but not a move either: there is no wait action (v1 BRIEF Q8),
        # so a zero delta must never report moved=True and consume a turn.
        return MoveResult(position, False)

    target = (position[0] + dx, position[1] + dy)
    tx, ty = target
    # Occupancy first: a cell holding an actor is refused whether it is floor or door,
    # and the caller turns that refusal into an attack (CONTRACT-v5 §7.9).
    if target in occupied:
        return MoveResult(position, False, blocked_by_npc=target)

    if is_passable(level, open_doors, tx, ty):
        return MoveResult(target, True)

    if is_closed_door(level, open_doors, tx, ty):
        return MoveResult(position, False, blocked_by_door=target)
    return MoveResult(position, False)


def is_blocked(level: Level, x: int, y: int) -> bool:
    """Return ``True`` iff ``(x, y)`` cannot be stepped on, ignoring door state.

    Exactly ``not level.is_walkable(x, y)`` — terrain only, unaware of open/closed
    doors, so every out-of-bounds coordinate is blocked. Retained unchanged from v1
    for compatibility; new code should use :func:`roguelike.world.is_passable`
    instead, which is door-aware. Never raises.
    """
    return not level.is_walkable(x, y)
