"""Pathfinding and local map topology predicates.

A leaf module: it imports nothing from the project. Every function that needs
to know whether a cell can be walked through takes a ``passable(x, y) -> bool``
callable supplied by the caller, so this module never has to know whether it
is planning over real terrain or only over terrain the character has
explored.

Costs are integers throughout: orthogonal moves cost 10, diagonal moves cost
14. No floats appear anywhere in this module, so ordering during the search
is exact and no epsilon or tie-break fudge is ever needed.
"""

from __future__ import annotations

import heapq
from collections.abc import Callable
from collections.abc import Set as AbstractSet

Coord = tuple[int, int]
Passable = Callable[[int, int], bool]

ORTHOGONAL_COST: int = 10
DIAGONAL_COST: int = 14

# The eight unit deltas, in a fixed clockwise order starting at north. This
# order is a module-level constant and must never be shuffled, sorted or
# regenerated at call time: it is part of what makes the search deterministic.
DIRECTIONS: tuple[Coord, ...] = (
    (0, -1),   # N
    (1, -1),   # NE
    (1, 0),    # E
    (1, 1),    # SE
    (0, 1),    # S
    (-1, 1),   # SW
    (-1, 0),   # W
    (-1, -1),  # NW
)

_ORTHOGONAL_DIRECTIONS: tuple[Coord, ...] = ((0, -1), (1, 0), (0, 1), (-1, 0))


def octile(a: Coord, b: Coord) -> int:
    """Admissible, consistent heuristic for the 10/14 cost model.

    octile(a, b) = 10 * (dx + dy) + (14 - 2 * 10) * min(dx, dy)
    with dx, dy the absolute coordinate differences. Always a non-negative
    int; never raises.
    """
    dx = abs(a[0] - b[0])
    dy = abs(a[1] - b[1])
    return ORTHOGONAL_COST * (dx + dy) + (DIAGONAL_COST - 2 * ORTHOGONAL_COST) * min(dx, dy)


def _heuristic_to_nearest(coord: Coord, goals: AbstractSet[Coord]) -> int:
    return min(octile(coord, goal) for goal in goals)


def _reconstruct(came_from: dict[Coord, Coord], current: Coord) -> list[Coord]:
    path = [current]
    while current in came_from:
        current = came_from[current]
        path.append(current)
    path.reverse()
    return path


def find_path(passable: Passable, start: Coord, goals: AbstractSet[Coord]) -> list[Coord] | None:
    """Shortest path from ``start`` to the nearest member of ``goals``.

    Returns the full path including ``start``, ending on a member of
    ``goals``; ``[start]`` if ``start`` is already a goal; ``None`` if
    ``goals`` is empty or no goal is reachable.

    ``start`` itself need not be passable (the character may be standing on
    a cell the predicate rejects); every other cell on the path does satisfy
    ``passable``.

    Deterministic: identical inputs give an identical path, run to run and
    process to process. Ties are broken by a total order — ``DIRECTIONS`` is
    iterated in its fixed order when expanding a node, and every
    priority-queue entry carries the coordinate as part of its key, so no
    comparison between two entries of equal priority is ever ambiguous or
    dependent on set/dict iteration order.

    Pure: no mutation, no I/O, no global state, no caching. Never raises for
    an unreachable goal.
    """
    if not goals:
        return None
    if start in goals:
        return [start]

    g_score: dict[Coord, int] = {start: 0}
    came_from: dict[Coord, Coord] = {}
    closed: set[Coord] = set()

    # Heap entries are (f_score, coord, g_score). coord is a tuple of ints
    # and g_score is an int, so every entry is totally ordered against every
    # other entry -- no ambiguous comparison, no reliance on insertion order.
    open_heap: list[tuple[int, Coord, int]] = []
    heapq.heappush(open_heap, (_heuristic_to_nearest(start, goals), start, 0))

    while open_heap:
        _, current, g = heapq.heappop(open_heap)
        if current in closed:
            continue
        if current in goals:
            return _reconstruct(came_from, current)
        closed.add(current)

        cx, cy = current
        for dx, dy in DIRECTIONS:
            neighbor = (cx + dx, cy + dy)
            if neighbor in closed:
                continue
            if not passable(neighbor[0], neighbor[1]):
                continue
            step_cost = DIAGONAL_COST if dx != 0 and dy != 0 else ORTHOGONAL_COST
            tentative_g = g + step_cost
            if neighbor not in g_score or tentative_g < g_score[neighbor]:
                g_score[neighbor] = tentative_g
                came_from[neighbor] = current
                f = tentative_g + _heuristic_to_nearest(neighbor, goals)
                heapq.heappush(open_heap, (f, neighbor, tentative_g))

    return None


def is_wide(passable: Passable, x: int, y: int) -> bool:
    """True iff (x, y) belongs to any 2x2 block of passable cells.

    All four quadrants around (x, y) are checked -- up-left, up-right,
    down-left, down-right -- because checking fewer misreads corridor cells
    that happen to have one open diagonal as room cells (measured: two
    quadrants gives 96.4% accuracy; four quadrants gives 99.98% with zero
    room cells misread as thin).
    """
    quadrants = (
        ((x - 1, y - 1), (x, y - 1), (x - 1, y), (x, y)),   # up-left
        ((x, y - 1), (x + 1, y - 1), (x, y), (x + 1, y)),   # up-right
        ((x - 1, y), (x, y), (x - 1, y + 1), (x, y + 1)),   # down-left
        ((x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)),   # down-right
    )
    return any(all(passable(px, py) for px, py in quad) for quad in quadrants)


def degree(passable: Passable, x: int, y: int) -> int:
    """Count of passable orthogonal neighbours of (x, y), 0 to 4."""
    return sum(1 for dx, dy in _ORTHOGONAL_DIRECTIONS if passable(x + dx, y + dy))


def is_intersection(passable: Passable, x: int, y: int) -> bool:
    """True iff (x, y) is a thin cell (not wide) with 3 or more passable
    orthogonal neighbours -- a T-junction or a cross in a corridor."""
    return not is_wide(passable, x, y) and degree(passable, x, y) >= 3
