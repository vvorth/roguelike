"""Unit tests for roguelike.tiles and roguelike.level (T01)."""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

import roguelike.level
import roguelike.tiles
from roguelike.level import Level, Room, blank_grid, freeze_grid
from roguelike.tiles import (
    PLAYER_CHAR,
    TILE_CHARS,
    WALKABLE,
    Tile,
    is_walkable_tile,
    tile_char,
)


# --------------------------------------------------------------------------- helpers


def make_level(
    width: int = 5,
    height: int = 4,
    player_start: tuple[int, int] = (1, 1),
    rooms: tuple[Room, ...] = (),
    seed: int = 7,
) -> Level:
    """A level whose interior is all FLOOR and whose outer ring is WALL."""
    grid = blank_grid(width, height)
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            grid[y][x] = Tile.FLOOR
    return Level(width, height, freeze_grid(grid), rooms, player_start, seed)


# ----------------------------------------------------------------------------- tiles


def test_tile_members_and_values():
    assert Tile.WALL == 0
    assert Tile.FLOOR == 1
    assert Tile.DOOR == 2
    assert [t.name for t in Tile] == ["WALL", "FLOOR", "DOOR"]


def test_tile_is_int_enum():
    assert isinstance(Tile.FLOOR, int)


def test_tile_chars_mapping():
    assert TILE_CHARS == {Tile.WALL: "#", Tile.FLOOR: ".", Tile.DOOR: "+"}


def test_player_char():
    assert PLAYER_CHAR == "@"


@pytest.mark.parametrize(
    ("tile", "char"),
    [(Tile.WALL, "#"), (Tile.FLOOR, "."), (Tile.DOOR, "+")],
)
def test_tile_char(tile, char):
    assert tile_char(tile) == char


def test_tile_char_unknown_raises_key_error():
    with pytest.raises(KeyError):
        tile_char(99)  # type: ignore[arg-type]


def test_walkable_set():
    assert WALKABLE == frozenset({Tile.FLOOR, Tile.DOOR})
    assert isinstance(WALKABLE, frozenset)


@pytest.mark.parametrize(
    ("tile", "expected"),
    [(Tile.WALL, False), (Tile.FLOOR, True), (Tile.DOOR, True)],
)
def test_is_walkable_tile(tile, expected):
    assert is_walkable_tile(tile) is expected


def _imported_modules(module) -> set[str]:
    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_tiles_imports_are_stdlib_and_package_free():
    imports = _imported_modules(roguelike.tiles)
    assert "curses" not in imports
    assert not any(name.startswith("roguelike") for name in imports)
    assert imports <= {"__future__", "enum"}


def test_level_imports_only_tiles_and_stdlib():
    imports = _imported_modules(roguelike.level)
    assert "curses" not in imports
    package_imports = {n for n in imports if n.startswith("roguelike")}
    assert package_imports == {"roguelike.tiles"}
    assert imports <= {"__future__", "dataclasses", "roguelike.tiles"}


# ------------------------------------------------------------------------------ Room


def test_room_edges_are_inclusive():
    room = Room(3, 5, 4, 6)
    assert room.x2 == 3 + 4 - 1 == 6
    assert room.y2 == 5 + 6 - 1 == 10


def test_room_center():
    room = Room(3, 5, 4, 6)
    assert room.center == (3 + 4 // 2, 5 + 6 // 2) == (5, 8)


def test_room_center_odd_dimensions():
    assert Room(0, 0, 5, 5).center == (2, 2)


def test_room_minimal_size_is_legal():
    room = Room(1, 1, 1, 1)
    assert room.x2 == 1 and room.y2 == 1
    assert room.contains(1, 1)


@pytest.mark.parametrize(("w", "h"), [(0, 3), (3, 0), (-1, 3), (3, -2), (0, 0)])
def test_room_rejects_non_positive_dimensions(w, h):
    with pytest.raises(ValueError):
        Room(1, 1, w, h)


def test_room_contains_and_perimeter_partition():
    room = Room(2, 3, 4, 3)  # floor x 2..5, y 3..5
    for y in range(3, 6):
        for x in range(2, 6):
            assert room.contains(x, y)
            assert not room.on_perimeter(x, y)

    # The full surrounding ring, corners included.
    ring = set()
    for x in range(1, 7):
        ring.add((x, 2))
        ring.add((x, 6))
    for y in range(2, 7):
        ring.add((1, y))
        ring.add((6, y))
    assert (1, 2) in ring and (6, 2) in ring and (1, 6) in ring and (6, 6) in ring
    for x, y in ring:
        assert room.on_perimeter(x, y)
        assert not room.contains(x, y)


def test_room_two_steps_away_is_neither():
    room = Room(2, 3, 4, 3)
    for x, y in [(0, 3), (7, 3), (3, 1), (3, 7), (0, 1), (7, 7)]:
        assert not room.contains(x, y)
        assert not room.on_perimeter(x, y)


def test_contains_and_on_perimeter_are_mutually_exclusive_everywhere():
    room = Room(2, 3, 4, 3)
    for y in range(-2, 12):
        for x in range(-2, 12):
            assert not (room.contains(x, y) and room.on_perimeter(x, y))


def test_intersects_rooms_sharing_one_wall_cell():
    a = Room(1, 1, 4, 4)  # x 1..4
    b = Room(6, 1, 4, 4)  # x 6..9, gap of exactly one cell at x == 5
    assert a.intersects(b) is True
    assert a.intersects(b, margin=1) is True
    assert a.intersects(b, margin=0) is False


def test_intersects_false_when_two_or_more_cells_apart():
    a = Room(1, 1, 4, 4)  # x 1..4
    b = Room(7, 1, 4, 4)  # gap of two cells (5 and 6)
    assert a.intersects(b) is False
    c = Room(20, 20, 3, 3)
    assert a.intersects(c) is False


def test_intersects_overlapping_and_touching():
    a = Room(1, 1, 5, 5)  # x 1..5
    assert a.intersects(Room(3, 3, 5, 5), margin=0) is True  # overlapping
    assert a.intersects(Room(6, 1, 4, 4), margin=0) is False  # touching, not overlapping
    assert a.intersects(Room(6, 1, 4, 4)) is True  # touching, within one cell
    assert a.intersects(a, margin=0) is True  # a room overlaps itself


def test_intersects_requires_proximity_on_both_axes():
    a = Room(1, 1, 4, 4)
    far_in_y = Room(1, 20, 4, 4)  # same columns, far away vertically
    assert a.intersects(far_in_y) is False


def test_intersects_is_symmetric():
    rooms = [
        Room(1, 1, 4, 4),
        Room(6, 1, 4, 4),
        Room(7, 1, 4, 4),
        Room(3, 3, 5, 5),
        Room(2, 9, 3, 3),
        Room(20, 20, 2, 2),
    ]
    for a in rooms:
        for b in rooms:
            for margin in (0, 1, 2):
                assert a.intersects(b, margin) == b.intersects(a, margin)


def test_room_is_frozen():
    room = Room(1, 1, 3, 3)
    with pytest.raises(dataclasses.FrozenInstanceError):
        room.x = 5  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        room.width = 9  # type: ignore[misc]


def test_room_positional_field_order():
    room = Room(1, 2, 3, 4)
    assert (room.x, room.y, room.width, room.height) == (1, 2, 3, 4)


def test_room_equality_and_hashable():
    assert Room(1, 2, 3, 4) == Room(1, 2, 3, 4)
    assert len({Room(1, 2, 3, 4), Room(1, 2, 3, 4)}) == 1


# ----------------------------------------------------------------------------- Level


def test_level_positional_field_order():
    grid = freeze_grid(blank_grid(3, 2))
    rooms = (Room(0, 0, 1, 1),)
    level = Level(3, 2, grid, rooms, (2, 1), -5)
    assert level.width == 3
    assert level.height == 2
    assert level.grid == grid
    assert level.rooms == rooms
    assert level.player_start == (2, 1)
    assert level.seed == -5


def test_level_in_bounds():
    level = make_level(width=5, height=4)
    for y in range(4):
        for x in range(5):
            assert level.in_bounds(x, y) is True
    for x, y in [(-1, 0), (0, -1), (-1, -1), (5, 0), (0, 4), (5, 4), (100, 100)]:
        assert level.in_bounds(x, y) is False


def test_level_tile_at_in_bounds():
    level = make_level(width=5, height=4)
    assert level.tile_at(0, 0) is Tile.WALL
    assert level.tile_at(1, 1) is Tile.FLOOR
    assert level.tile_at(4, 3) is Tile.WALL


def test_level_tile_at_matches_grid_indexing():
    grid = blank_grid(4, 3)
    grid[2][1] = Tile.DOOR
    level = Level(4, 3, freeze_grid(grid), (), (1, 2), 0)
    assert level.tile_at(1, 2) is Tile.DOOR  # grid[y][x]
    assert level.tile_at(2, 1) is Tile.WALL


@pytest.mark.parametrize(
    ("x", "y"), [(-1, 0), (0, -1), (-1, -1), (5, 0), (0, 4), (5, 4), (99, 99)]
)
def test_level_tile_at_out_of_bounds_raises_index_error(x, y):
    level = make_level(width=5, height=4)
    with pytest.raises(IndexError):
        level.tile_at(x, y)


def test_level_is_walkable_matches_is_walkable_tile_in_bounds():
    grid = blank_grid(5, 4)
    grid[1][1] = Tile.FLOOR
    grid[1][2] = Tile.DOOR
    level = Level(5, 4, freeze_grid(grid), (), (1, 1), 0)
    for y in range(4):
        for x in range(5):
            assert level.is_walkable(x, y) is is_walkable_tile(level.tile_at(x, y))
    assert level.is_walkable(1, 1) is True
    assert level.is_walkable(2, 1) is True
    assert level.is_walkable(0, 0) is False


@pytest.mark.parametrize(
    ("x", "y"), [(-1, 0), (0, -1), (-1, -1), (5, 0), (0, 4), (5, 4), (-99, 99)]
)
def test_level_is_walkable_out_of_bounds_is_false_and_never_raises(x, y):
    level = make_level(width=5, height=4)
    assert level.is_walkable(x, y) is False


def test_level_is_frozen():
    level = make_level()
    with pytest.raises(dataclasses.FrozenInstanceError):
        level.width = 99  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        level.player_start = (0, 0)  # type: ignore[misc]


def test_level_grid_rejects_mutation():
    level = make_level()
    with pytest.raises(TypeError):
        level.grid[0][0] = Tile.FLOOR  # type: ignore[index]
    with pytest.raises(TypeError):
        level.grid[0] = ()  # type: ignore[index]


def test_level_rejects_row_count_mismatch():
    grid = freeze_grid(blank_grid(5, 3))
    with pytest.raises(ValueError):
        Level(5, 4, grid, (), (1, 1), 0)


def test_level_rejects_row_width_mismatch():
    rows = blank_grid(5, 3)
    rows[1] = rows[1][:-1]  # one short row
    with pytest.raises(ValueError):
        Level(5, 3, freeze_grid(rows), (), (1, 1), 0)


@pytest.mark.parametrize("bad", [(-1, 1), (1, -1), (5, 1), (1, 4), (5, 4)])
def test_level_rejects_out_of_bounds_player_start(bad):
    grid = freeze_grid(blank_grid(5, 4))
    with pytest.raises(ValueError):
        Level(5, 4, grid, (), bad, 0)


@pytest.mark.parametrize(("w", "h"), [(0, 3), (3, 0), (-2, 3)])
def test_level_rejects_non_positive_dimensions(w, h):
    with pytest.raises(ValueError):
        Level(w, h, (), (), (0, 0), 0)


def test_level_allows_zero_rooms():
    level = make_level(rooms=())
    assert level.rooms == ()


def test_level_allows_non_walkable_player_start():
    # __post_init__ must not require player_start to be walkable.
    grid = freeze_grid(blank_grid(4, 3))  # all WALL
    level = Level(4, 3, grid, (), (0, 0), 0)
    assert level.is_walkable(*level.player_start) is False


def test_level_minimal_one_by_one():
    level = Level(1, 1, freeze_grid(blank_grid(1, 1)), (), (0, 0), 0)
    assert level.tile_at(0, 0) is Tile.WALL
    assert level.in_bounds(0, 0) is True
    assert level.in_bounds(1, 0) is False


def test_level_equality():
    assert make_level() == make_level()
    assert make_level(seed=1) != make_level(seed=2)


# ---------------------------------------------------------------------- grid helpers


def test_blank_grid_shape_and_fill():
    grid = blank_grid(7, 3)
    assert len(grid) == 3
    assert all(len(row) == 7 for row in grid)
    assert all(cell is Tile.WALL for row in grid for cell in row)


def test_blank_grid_is_mutable_list_of_lists():
    grid = blank_grid(4, 3)
    assert isinstance(grid, list)
    assert all(isinstance(row, list) for row in grid)
    grid[1][2] = Tile.FLOOR
    assert grid[1][2] is Tile.FLOOR


def test_blank_grid_rows_are_independent():
    grid = blank_grid(4, 3)
    grid[0][0] = Tile.FLOOR
    assert grid[1][0] is Tile.WALL
    assert grid[2][0] is Tile.WALL
    assert len({id(row) for row in grid}) == 3


def test_blank_grid_custom_fill():
    grid = blank_grid(2, 2, Tile.FLOOR)
    assert all(cell is Tile.FLOOR for row in grid for cell in row)


@pytest.mark.parametrize(("w", "h"), [(0, 3), (3, 0), (-1, 3), (3, -1), (0, 0)])
def test_blank_grid_rejects_non_positive_dimensions(w, h):
    with pytest.raises(ValueError):
        blank_grid(w, h)


def test_freeze_grid_produces_tuples_of_tuples():
    frozen = freeze_grid(blank_grid(6, 4))
    assert isinstance(frozen, tuple)
    assert len(frozen) == 4
    assert all(isinstance(row, tuple) and len(row) == 6 for row in frozen)
    assert all(cell is Tile.WALL for row in frozen for cell in row)


def test_freeze_grid_preserves_contents_and_orientation():
    grid = blank_grid(3, 2)
    grid[1][0] = Tile.DOOR
    frozen = freeze_grid(grid)
    assert frozen[1][0] is Tile.DOOR
    assert frozen[0][0] is Tile.WALL


def test_freeze_grid_snapshot_is_decoupled_from_source():
    grid = blank_grid(3, 2)
    frozen = freeze_grid(grid)
    grid[0][0] = Tile.FLOOR
    assert frozen[0][0] is Tile.WALL


@pytest.mark.parametrize("bad", [[], [[]], [[Tile.WALL], []]])
def test_freeze_grid_rejects_non_positive_dimensions(bad):
    with pytest.raises(ValueError):
        freeze_grid(bad)


def test_blank_grid_freeze_grid_round_trip_builds_a_level():
    grid = blank_grid(9, 5)
    grid[2][3] = Tile.FLOOR
    level = Level(9, 5, freeze_grid(grid), (), (3, 2), 42)
    assert level.tile_at(3, 2) is Tile.FLOOR
    assert level.is_walkable(3, 2) is True
    assert level.seed == 42
