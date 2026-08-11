"""Multi-turn activities and the pure planners that decide their next step
(CONTRACT-v4 §19).

An :class:`Activity` is the small frozen value that says what is in progress —
travelling to a coordinate, exploring, or walking in a direction. The two planners here
answer "where next?" for the last two; ``advance`` itself lives in :mod:`roguelike.game`,
because it needs ``GameState``.

All coordinates are ``(x, y)`` with the origin at the top-left; ``x`` grows right and
``y`` grows down, so "up" is ``dy = -1`` (CONTRACT §0.1).

Both planners are pure: no mutation of any argument, no I/O, no module-level state, no
caching, and no dependence on set iteration order in anything that affects the result.
Neither decides more than one step — :func:`walk_step` is called once per turn by the
turn loop, never looped over here.

**The no-cheating rule.** :func:`frontier_cells` explores from the character's
perspective. It reads ``explored``, the level's bounds, and the tile identity of cells
that are *already in* ``explored`` — and nothing else. It must never consult the tile of
a cell the character has not seen, however much better the resulting coverage would
look (CONTRACT-v4 §19.1).

Imports only :mod:`roguelike.level`, :mod:`roguelike.world` and
:mod:`roguelike.pathfind` (CONTRACT-v4 §10). Never touches curses.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from roguelike.level import Level
from roguelike.pathfind import DIRECTIONS, Coord, Passable, is_wide
from roguelike.world import is_closed_door, is_planning_passable

__all__ = ["ActivityKind", "Activity", "frontier_cells", "walk_step"]


#: The four orthogonal neighbour offsets, as ``(dx, dy)``. A corridor is followed
#: orthogonally only (CONTRACT-v4 §19.2); the eight-way deltas used for frontier
#: adjacency come from :data:`roguelike.pathfind.DIRECTIONS`.
_ORTHOGONAL: tuple[Coord, ...] = ((0, -1), (1, 0), (0, 1), (-1, 0))

# The four ``reason`` strings of walk_step (CONTRACT-v4 §19.2). Deliberately module
# private: the contract fixes the *values*, and callers compare against those literals,
# so exporting the names would only add surface that has to be kept in step.
_MOVING: str = ""
_BLOCKED: str = "blocked"
_INTERSECTION: str = "intersection"
_OPENING: str = "opening"


class ActivityKind(Enum):
    """The kinds of action that span more than one turn (CONTRACT-v4 §19)."""

    TRAVEL = auto()
    AUTO_EXPLORE = auto()
    AUTO_WALK = auto()
    REST = auto()


@dataclass(frozen=True)
class Activity:
    """An action in progress. Immutable; a turn produces a new one, never a mutation.

    Only the fields a given ``kind`` needs are meaningful:

    * ``TRAVEL`` uses ``goal`` — the destination cell.
    * ``AUTO_WALK`` uses ``direction`` (the direction the walk was started in) and
      ``came_from`` (the cell stepped from last turn, ``None`` on the first step).
    * ``AUTO_EXPLORE`` uses none of them: its goals are recomputed from ``explored``
      every turn.
    * ``REST`` uses none of them either: it stands still until healed or interrupted.

    There is deliberately **no** ``path`` field. Routes are re-planned every turn — at a
    measured 0.235 ms per full-level search that is 0.2% of one turn's budget
    (CONTRACT-v4 §18.1), so caching one would buy nothing and could go stale against a
    door that opened underneath it.
    """

    kind: ActivityKind
    goal: Coord | None = None
    direction: tuple[int, int] = (0, 0)
    came_from: Coord | None = None


def frontier_cells(
    level: Level, explored: frozenset[Coord], open_doors: frozenset[Coord]
) -> frozenset[Coord]:
    """Return the cells worth walking to next in order to discover more.

    A cell is a frontier iff it is **in** ``explored``, satisfies
    :func:`roguelike.world.is_planning_passable`, and either

    * touches, 8-directionally, a cell that is **in bounds and not in** ``explored``, or
    * **is itself a closed door** — a closed door hides whatever lies beyond it no
      matter how thoroughly the cells around it have been mapped, so it stays a frontier
      until it is opened and what is behind it has been seen.

    Both clauses are load-bearing. Without the first there is nothing to walk towards;
    without the second auto-explore strolls past unopened doors and announces that the
    level is finished (RESEARCH-v4 §4).

    This function never looks at terrain the character has not seen. Every tile lookup
    it makes is on a cell drawn from ``explored``; the neighbour test asks only
    ``level.in_bounds`` and membership of ``explored``, never what the neighbour *is*.
    Out-of-bounds members of ``explored``, should a caller supply any, are skipped
    rather than raising.

    Pure: neither argument is modified, and the result is a ``frozenset``, so the
    iteration order of ``explored`` cannot leak into it. Never raises.
    """
    frontier: set[Coord] = set()
    for cell in explored:
        x, y = cell
        if not level.in_bounds(x, y):
            continue
        if not is_planning_passable(level, open_doors, x, y):
            continue
        if is_closed_door(level, open_doors, x, y):
            frontier.add(cell)
            continue
        for dx, dy in DIRECTIONS:
            neighbour = (x + dx, y + dy)
            if level.in_bounds(*neighbour) and neighbour not in explored:
                frontier.add(cell)
                break
    return frozenset(frontier)


def walk_step(
    passable: Passable,
    position: Coord,
    came_from: Coord | None,
    direction: tuple[int, int],
) -> tuple[Coord | None, str]:
    """Decide the **single** next cell of an auto-walk (CONTRACT-v4 §19.2).

    Returns ``(next_coord, reason)``. ``next_coord`` is ``None`` exactly when the walk
    must stop, and ``reason`` is then one of ``"blocked"``, ``"intersection"`` or
    ``"opening"``; while moving it is ``""``.

    The rule is local — it never reads ``level.rooms`` or any other engine knowledge,
    only the ``passable`` callable — and it has two modes:

    * **In the open** (``is_wide(passable, *position)``): step to
      ``position + direction``, or stop ``"blocked"`` if that cell is not passable. A
      wide area never follows turns and never stops for a side opening: crossing a room
      means crossing it in a straight line.
    * **In a corridor** (not wide): after the first step, the candidates are the
      passable **orthogonal** neighbours other than ``came_from`` — exactly one means
      the corridor continues (and may bend, which is why ``direction`` is not consulted
      here), more than one is a junction (``"intersection"``), none is a dead end
      (``"blocked"``). If the cell so chosen ``is_wide``, the corridor is opening into a
      room: stop ``"opening"`` and do **not** move onto it, leaving the character one
      cell short of the doorway.

    The first step of a walk has ``came_from is None`` and uses ``direction`` in both
    modes — that is the keystroke the player just pressed, so it is obeyed literally
    rather than being second-guessed by the corridor rule (a first step therefore never
    reports ``"intersection"``; it walks out of the junction the way it was told to).
    Every later corridor step follows the corridor and ignores ``direction``, which by
    then is stale.

    A zero ``direction`` stops with ``"blocked"`` wherever it occurs. The contract
    states this for the open case (CONTRACT-v4 §11); in a corridor the same answer is
    the only coherent one, since ``position + (0, 0)`` is the cell already occupied and
    "moving" onto it would be a turn that goes nowhere, for ever.

    Pure: no mutation, no I/O, no state between calls. This decides one step only; the
    turn loop calls it again next turn. Never raises — ``passable`` is asked about
    out-of-bounds cells freely and is expected to answer ``False``.
    """
    x, y = position
    dx, dy = direction

    if is_wide(passable, x, y):
        if dx == 0 and dy == 0:
            return (None, _BLOCKED)
        ahead = (x + dx, y + dy)
        if not passable(*ahead):
            return (None, _BLOCKED)
        return (ahead, _MOVING)

    if came_from is None:
        if dx == 0 and dy == 0:
            return (None, _BLOCKED)
        chosen = (x + dx, y + dy)
        if not passable(*chosen):
            return (None, _BLOCKED)
    else:
        candidates = [
            (x + ox, y + oy)
            for ox, oy in _ORTHOGONAL
            if (x + ox, y + oy) != came_from and passable(x + ox, y + oy)
        ]
        if not candidates:
            return (None, _BLOCKED)
        if len(candidates) > 1:
            return (None, _INTERSECTION)
        chosen = candidates[0]

    if is_wide(passable, *chosen):
        return (None, _OPENING)
    return (chosen, _MOVING)
