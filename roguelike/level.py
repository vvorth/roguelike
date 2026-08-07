"""Immutable level data structures — ``Room``, ``Level``, and grid helpers.

All coordinates are ``(x, y)`` with the origin at the top-left; ``x`` grows right and
``y`` grows down, so "up" is ``dy = -1``. Grid storage is the sole inversion:
``grid[y][x]``, a sequence of rows (CONTRACT §0.1).

Imports only from :mod:`roguelike.tiles` (CONTRACT §10).
"""

from __future__ import annotations

from dataclasses import dataclass

from roguelike.tiles import Tile, is_walkable_tile

__all__ = ["Room", "Level", "freeze_grid", "blank_grid"]


@dataclass(frozen=True)
class Room:
    """An axis-aligned rectangle of FLOOR cells.

    ``x``/``y``/``width``/``height`` describe the **floor** rectangle only; the room's
    walls are the 1-cell ring immediately outside it and are not part of these fields.
    """

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width < 1:
            raise ValueError(f"Room width must be >= 1, got {self.width}")
        if self.height < 1:
            raise ValueError(f"Room height must be >= 1, got {self.height}")

    @property
    def x2(self) -> int:
        """Inclusive right edge of the floor rectangle."""
        return self.x + self.width - 1

    @property
    def y2(self) -> int:
        """Inclusive bottom edge of the floor rectangle."""
        return self.y + self.height - 1

    @property
    def center(self) -> tuple[int, int]:
        """Centre cell of the floor rectangle, as ``(x, y)``."""
        return (self.x + self.width // 2, self.y + self.height // 2)

    def contains(self, x: int, y: int) -> bool:
        """Return ``True`` iff ``(x, y)`` lies inside the floor rectangle."""
        return self.x <= x <= self.x2 and self.y <= y <= self.y2

    def on_perimeter(self, x: int, y: int) -> bool:
        """Return ``True`` iff ``(x, y)`` lies on the 1-cell wall ring around the floor
        rectangle, corners included. Mutually exclusive with :meth:`contains`."""
        in_ring_box = (
            self.x - 1 <= x <= self.x2 + 1 and self.y - 1 <= y <= self.y2 + 1
        )
        return in_ring_box and not self.contains(x, y)

    def intersects(self, other: "Room", margin: int = 1) -> bool:
        """Return ``True`` iff the two floor rectangles come within ``margin`` cells.

        ``margin=1`` (the default) means "overlapping, touching, or sharing a wall" and
        is what the generator uses for overlap rejection; ``margin=0`` is strict overlap
        only. Symmetric in ``self`` and ``other``.
        """
        return (
            self.x - margin <= other.x2 + margin
            and other.x - margin <= self.x2 + margin
            and self.y - margin <= other.y2 + margin
            and other.y - margin <= self.y2 + margin
        )


@dataclass(frozen=True)
class Level:
    """A generated dungeon level. Immutable, including its grid.

    Field order is binding — positional construction must work as written.
    """

    width: int
    height: int
    grid: tuple[tuple[Tile, ...], ...]
    rooms: tuple[Room, ...]
    player_start: tuple[int, int]
    seed: int

    def __post_init__(self) -> None:
        if self.width < 1:
            raise ValueError(f"Level width must be >= 1, got {self.width}")
        if self.height < 1:
            raise ValueError(f"Level height must be >= 1, got {self.height}")
        if len(self.grid) != self.height:
            raise ValueError(
                f"grid has {len(self.grid)} rows, expected height {self.height}"
            )
        for y, row in enumerate(self.grid):
            if len(row) != self.width:
                raise ValueError(
                    f"grid row {y} has {len(row)} cells, expected width {self.width}"
                )
        if not self.in_bounds(*self.player_start):
            raise ValueError(f"player_start {self.player_start!r} is out of bounds")

    def in_bounds(self, x: int, y: int) -> bool:
        """Return ``True`` iff ``0 <= x < width`` and ``0 <= y < height``. Never raises."""
        return 0 <= x < self.width and 0 <= y < self.height

    def tile_at(self, x: int, y: int) -> Tile:
        """Return the tile at ``(x, y)``.

        Raises:
            IndexError: if ``(x, y)`` is out of bounds. Negative coordinates raise
                rather than wrapping around.
        """
        if not self.in_bounds(x, y):
            raise IndexError(
                f"({x}, {y}) is outside the {self.width}x{self.height} level"
            )
        return self.grid[y][x]

    def is_walkable(self, x: int, y: int) -> bool:
        """Return ``True`` iff ``(x, y)`` is in bounds and its tile can be stepped on.

        Returns ``False`` off-map. Never raises.
        """
        if not self.in_bounds(x, y):
            return False
        return is_walkable_tile(self.tile_at(x, y))


def freeze_grid(grid: list[list[Tile]]) -> tuple[tuple[Tile, ...], ...]:
    """Convert a mutable list-of-rows grid into the immutable form ``Level`` needs.

    Raises:
        ValueError: if the grid has no rows, or any row has no cells.
    """
    if len(grid) < 1:
        raise ValueError("grid must have at least one row")
    for y, row in enumerate(grid):
        if len(row) < 1:
            raise ValueError(f"grid row {y} must have at least one cell")
    return tuple(tuple(row) for row in grid)


def blank_grid(
    width: int, height: int, fill: Tile = Tile.WALL
) -> list[list[Tile]]:
    """Return a mutable ``height`` x ``width`` grid of ``fill``, indexed ``grid[y][x]``.

    Every row is an independent list, so mutating one row never affects another.

    Raises:
        ValueError: if ``width`` or ``height`` is non-positive.
    """
    if width < 1:
        raise ValueError(f"width must be >= 1, got {width}")
    if height < 1:
        raise ValueError(f"height must be >= 1, got {height}")
    return [[fill for _ in range(width)] for _ in range(height)]
