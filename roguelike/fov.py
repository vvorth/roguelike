"""Permissive field of view — which cells the character can actually see right now.

The project's visibility requirement (CONTRACT-v2 §14.1, user rule #5) is:

    "a symbol is considered visible if any side or corner is in direct eye sight"

That sentence rules out recursive shadowcasting, which tests tile **centres**. Testing
sides and corners is the textbook definition of **permissive** field of view, and that is
what this module implements — eight sample points per cell, an eye at the centre of the
origin cell, and an exact segment-clearance test per sample point.

Permissive is roughly 150x the cost of centre-only shadowcasting (§14.2 F9: ~15 ms at
radius 20 on an 80x22 level). That is deliberate. The budget here is a human keypress
(~100 ms), not a frame, and the two cheap alternatives were measured and rejected:
shadowcasting leaves holes in the walls of the room you stand in, and the "reveal every
wall touching a visible floor" hybrid over-shows by 6.8%, leaking map information from
around corners the character cannot see. **Correctness is the priority; there is one
algorithm here and no approximation switch.**

Geometry, precisely
-------------------
* The **eye** is the centre of the origin cell, ``(ox + 0.5, oy + 0.5)``.
* Cell ``(x, y)`` has **eight sample points** — its four corners ``(x, y)``,
  ``(x+1, y)``, ``(x, y+1)``, ``(x+1, y+1)`` and its four side midpoints
  ``(x+0.5, y)``, ``(x+0.5, y+1)``, ``(x, y+0.5)``, ``(x+1, y+0.5)``.
* ``(x, y)`` is **visible** iff it is within ``radius`` and at least one sample point is
  reachable from the eye by a straight segment crossing **no opaque cell**, where opaque
  means ``not world.is_transparent(...)``.
* Cells crossed are judged **excluding the origin cell and the target cell itself**. That
  exclusion is what makes an opaque cell visible: you see the *face* of a wall.
* **Diagonal-corner rule:** where a segment passes *through* a grid lattice point it is
  blocked only if **both** cells diagonally flanking that point are opaque. This stops
  sight leaking through the diagonal join of two walls while still allowing corner
  peeking past a single wall corner. A segment that *terminates* at a lattice point does
  not pass through it — see :func:`_segment_is_clear` for why that distinction is forced.

Everything is decided in **doubled integer coordinates** (every value of interest —
cell edges, cell centres, side midpoints — is a multiple of 0.5, so doubling makes them
all integers). There is no floating point anywhere in the traversal, so there is no
epsilon, no tie-breaking fudge, and the answer is exact.

Opacity has exactly one source: :func:`roguelike.world.is_transparent`. This module does
not look at tiles and does not know the closed-door rule.

Pure and deterministic: no RNG, no mutation of any argument, no I/O, no module-level
mutable state, no caching across calls, no dependence on set iteration order.

Imports only :mod:`roguelike.level` and :mod:`roguelike.world` (CONTRACT-v2 §10). Never
touches curses.
"""

from __future__ import annotations

from roguelike.level import Level
from roguelike.world import is_transparent

__all__ = ["DEFAULT_RADIUS", "compute_visible"]


DEFAULT_RADIUS: int = 20
"""Default sight radius (CONTRACT-v2 §14). A parameter, never a hard-coded constant at
the call site: indoors the walls dominate long before the radius does (measured: 115
cells at radius 8 against 142 at radius 20 on the same 80x22 level)."""


_SAMPLE_OFFSETS: tuple[tuple[int, int], ...] = (
    # The eight sample points of cell (x, y), as offsets from (2x, 2y) in doubled
    # coordinates. Side midpoints come first: they are the points that see the *face* of
    # a cell, so they succeed most often and short-circuit the remaining tries soonest.
    (1, 0),  # (x + 0.5, y)      top edge midpoint
    (0, 1),  # (x, y + 0.5)      left edge midpoint
    (2, 1),  # (x + 1, y + 0.5)  right edge midpoint
    (1, 2),  # (x + 0.5, y + 1)  bottom edge midpoint
    (0, 0),  # (x, y)            top-left corner
    (2, 0),  # (x + 1, y)        top-right corner
    (0, 2),  # (x, y + 1)        bottom-left corner
    (2, 2),  # (x + 1, y + 1)    bottom-right corner
)


def compute_visible(
    level: Level,
    open_doors: frozenset[tuple[int, int]],
    origin: tuple[int, int],
    radius: int = DEFAULT_RADIUS,
) -> frozenset[tuple[int, int]]:
    """Return the set of cells visible from ``origin`` right now.

    A cell is included iff it is in bounds, within Euclidean ``radius`` of ``origin``
    (``(x-ox)**2 + (y-oy)**2 <= radius**2``), and at least one of its eight sample points
    is reachable from the eye by a straight segment crossing no opaque cell — see the
    module docstring for the exact geometry.

    ``origin`` itself is **always** in the result, even when the character stands on a
    wall, on a closed door, or on any other opaque terrain, and even when ``radius`` is
    ``0``.

    Pure: neither ``level`` nor ``open_doors`` is modified, and the same arguments always
    produce the same set. Never returns ``None``.

    Args:
        level: The map. Read-only; consulted only through
            :func:`roguelike.world.is_transparent`.
        open_doors: The doors currently open. A closed door is opaque, an open one is
            not.
        origin: ``(x, y)`` of the eye's cell.
        radius: Maximum Euclidean sight distance in cells.

    Returns:
        A ``frozenset`` of ``(x, y)`` cells.

    Raises:
        ValueError: if ``radius`` is negative.
    """
    if radius < 0:
        raise ValueError(f"radius must be >= 0, got {radius}")

    ox, oy = origin

    # F1: the origin is unconditional. Even standing inside a wall you know where you
    # are, and a caller must never have to handle an empty result.
    visible: set[tuple[int, int]] = {(ox, oy)}

    if radius == 0:
        return frozenset(visible)

    x_lo = max(0, ox - radius)
    x_hi = min(level.width - 1, ox + radius)
    y_lo = max(0, oy - radius)
    y_hi = min(level.height - 1, oy + radius)
    if x_lo > x_hi or y_lo > y_hi:
        return frozenset(visible)

    # Snapshot opacity once per call, for the candidate box grown by one cell — a
    # segment towards a target in the box stays inside the eye/target bounding box, and
    # the diagonal rule looks at most one cell further out. This is a local view of
    # `world.is_transparent`, not a cache: it lives and dies inside this call, so there
    # is nothing to go stale. Cells outside the snapshot (which includes everything
    # off-map) read as opaque, exactly as `is_transparent` says they are.
    opaque: dict[tuple[int, int], bool] = {}
    for y in range(max(0, y_lo - 1), min(level.height - 1, y_hi + 1) + 1):
        for x in range(max(0, x_lo - 1), min(level.width - 1, x_hi + 1) + 1):
            opaque[(x, y)] = not is_transparent(level, open_doors, x, y)

    # The eye, doubled: the centre of the origin cell is (2*ox+1, 2*oy+1) — odd in both
    # axes, so it never sits on a grid line. That is what keeps the traversal free of
    # degenerate "starts exactly on a boundary" cases.
    ax = 2 * ox + 1
    ay = 2 * oy + 1
    r2 = radius * radius

    for ty in range(y_lo, y_hi + 1):
        dy = ty - oy
        dy2 = dy * dy
        if dy2 > r2:
            continue
        by = 2 * ty
        for tx in range(x_lo, x_hi + 1):
            dx = tx - ox
            if dx * dx + dy2 > r2:
                continue
            if tx == ox and ty == oy:
                continue
            bx = 2 * tx
            for sample_dx, sample_dy in _SAMPLE_OFFSETS:
                if _segment_is_clear(
                    ax, ay, bx + sample_dx, by + sample_dy, ox, oy, tx, ty, opaque
                ):
                    visible.add((tx, ty))
                    break

    return frozenset(visible)


def _segment_is_clear(
    ax: int,
    ay: int,
    bx: int,
    by: int,
    ocx: int,
    ocy: int,
    tcx: int,
    tcy: int,
    opaque: dict[tuple[int, int], bool],
) -> bool:
    """Return ``True`` iff the segment from the eye to one sample point crosses nothing.

    All four coordinates are **doubled** integers: ``(ax, ay)`` is the eye (odd in both
    axes, so strictly inside cell ``(ocx, ocy)``) and ``(bx, by)`` is a sample point on
    the boundary of the target cell ``(tcx, tcy)``. ``opaque`` maps a cell to whether it
    blocks sight, defaulting to blocking for anything it does not contain.

    The walk is an exact Amanatides-Woo grid traversal. Grid lines sit at even doubled
    coordinates; from an odd start the first crossing in each axis is 1 doubled unit away
    and every later one is 2 further, so the *k*-th crossing along x happens at parameter
    ``t = (2k-1)/|dx|`` and the *m*-th along y at ``t = (2m-1)/|dy|``. Ordering two such
    parameters is one integer cross-multiplication, and ``t < 1`` is one integer
    comparison — no floats, no epsilon, exact ties.

    Only crossings with ``t < 1`` are *interior*: they are the ones where the segment
    genuinely leaves one cell for another. The cell the eye starts in (``ocx, ocy``) and
    the target cell are both exempt from the opacity test — that exemption is what lets
    you see the face of a wall.

    **Why the diagonal-corner rule stops short of ``t = 1``.** The rule fires where a
    segment *passes through* a lattice point, and a segment that ends at one does not
    pass through it. That reading is also forced: standing in the middle of a room, the
    only clear line to a corner cell of the surrounding wall ring is the one that ends
    exactly on the room's inner corner, whose two flanking cells are the two wall runs
    meeting there. Applying the rule at ``t = 1`` would punch that corner out of the
    ring, which is precisely the ragged-wall artifact §14.2 F6 forbids. Sight is still
    stopped at every lattice point the segment actually crosses, so it cannot travel
    *through* a diagonal wall join to anything beyond it.
    """
    dx = bx - ax
    dy = by - ay
    adx = dx if dx >= 0 else -dx
    ady = dy if dy >= 0 else -dy
    step_x = 1 if dx > 0 else -1
    step_y = 1 if dy > 0 else -1

    cx = ocx
    cy = ocy
    # Numerators of the next crossing in each axis; t = numerator / a_.
    next_x = 1
    next_y = 1

    while True:
        has_x = adx > 0 and next_x < adx
        has_y = ady > 0 and next_y < ady

        if has_x and has_y:
            side = next_x * ady - next_y * adx
        elif has_x:
            side = -1
        elif has_y:
            side = 1
        else:
            # No crossing left before the sample point: the segment finishes inside the
            # cell we are already standing in, and that cell has been tested.
            return True

        if side < 0:
            cx += step_x
            next_x += 2
        elif side > 0:
            cy += step_y
            next_y += 2
        else:
            # Both axes cross at the same t: the segment passes exactly through a
            # lattice point, cutting the corner between the two flanking cells. Blocked
            # only if both of them are opaque.
            if opaque.get((cx + step_x, cy), True) and opaque.get(
                (cx, cy + step_y), True
            ):
                return False
            cx += step_x
            cy += step_y
            next_x += 2
            next_y += 2

        if (cx != tcx or cy != tcy) and opaque.get((cx, cy), True):
            return False
