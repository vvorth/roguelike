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
    """

    position: tuple[int, int]
    moved: bool
    blocked_by_door: tuple[int, int] | None = None


def try_move(
    level: Level,
    position: tuple[int, int],
    dx: int,
    dy: int,
    open_doors: frozenset[tuple[int, int]] = frozenset(),
) -> MoveResult:
    """Resolve a single step from ``position`` by ``(dx, dy)``.

    Pure: mutates neither ``level``, ``position`` nor ``open_doors``, performs no I/O,
    and never raises for an ordinary illegal move — walking into a wall or a closed
    door is normal input, not an error.

    Returns:
        ``MoveResult(target, True)`` if ``world.is_passable(level, open_doors, *target)``,
        otherwise ``MoveResult(position, False, blocked_by_door=...)`` with the input
        position returned unchanged (the same tuple object, not an equal copy).
        ``blocked_by_door`` is ``(tx, ty)`` when the target is a closed door, and
        ``None`` for every other rejection (wall, border, off-map). A zero delta
        (``dx == dy == 0``) is not an error; it simply does not move, and is never
        reported as door-blocked even when standing on a door.

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
