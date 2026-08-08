"""Unit tests for :mod:`roguelike.world` (CONTRACT-v2 §13, task T07).

Test levels are built by hand from character rows and constructed directly as ``Level``
objects — nothing here imports the generator (two other workers are editing it).
"""

from __future__ import annotations

import ast
import copy
import pathlib

import pytest

from roguelike.level import Level, freeze_grid
from roguelike.tiles import TILE_CHARS, DOOR_OPEN_CHAR, Tile
from roguelike.world import is_closed_door, is_passable, is_transparent

CHAR_TO_TILE = {"#": Tile.WALL, ".": Tile.FLOOR, "+": Tile.DOOR}


def make_level(
    rows: list[str],
    player_start: tuple[int, int] = (1, 1),
    seed: int = 0,
) -> Level:
    """Build a ``Level`` from character rows. ``rows[y][x]``, so row 0 is the top."""
    grid = [[CHAR_TO_TILE[c] for c in row] for row in rows]
    height = len(grid)
    width = len(grid[0])
    return Level(width, height, freeze_grid(grid), (), player_start, seed)


# A 7x6 room: floor everywhere inside a solid wall border, with a door at (3, 5).
OPEN_ROWS = [
    "#######",
    "#.....#",
    "#.....#",
    "#.....#",
    "#.....#",
    "###+###",
]

DOOR_POS = (3, 5)
WALL_POS = (0, 0)
FLOOR_POS = (2, 2)

CLOSED = frozenset()
OPEN = frozenset({DOOR_POS})


def open_level() -> Level:
    return make_level(OPEN_ROWS, player_start=(1, 1), seed=7)


# --------------------------------------------------------------------------
# is_passable
# --------------------------------------------------------------------------


def test_is_passable_true_for_floor():
    level = open_level()
    assert is_passable(level, CLOSED, *FLOOR_POS) is True
    assert is_passable(level, OPEN, *FLOOR_POS) is True


def test_is_passable_true_for_door_in_open_doors():
    level = open_level()
    assert is_passable(level, OPEN, *DOOR_POS) is True


def test_is_passable_false_for_door_not_in_open_doors():
    level = open_level()
    assert is_passable(level, CLOSED, *DOOR_POS) is False


def test_is_passable_false_for_wall():
    level = open_level()
    assert is_passable(level, CLOSED, *WALL_POS) is False
    assert is_passable(level, OPEN, *WALL_POS) is False


def test_is_passable_false_out_of_bounds():
    level = open_level()
    for x, y in [(-1, -1), (-1, 0), (0, -1), (100, 2), (2, 100), (10**6, 10**6)]:
        assert is_passable(level, CLOSED, x, y) is False
        assert is_passable(level, OPEN, x, y) is False


# --------------------------------------------------------------------------
# is_transparent
# --------------------------------------------------------------------------


def test_is_transparent_true_for_floor():
    level = open_level()
    assert is_transparent(level, CLOSED, *FLOOR_POS) is True
    assert is_transparent(level, OPEN, *FLOOR_POS) is True


def test_is_transparent_true_for_open_door():
    level = open_level()
    assert is_transparent(level, OPEN, *DOOR_POS) is True


def test_is_transparent_false_for_closed_door():
    level = open_level()
    assert is_transparent(level, CLOSED, *DOOR_POS) is False


def test_is_transparent_false_for_wall():
    level = open_level()
    assert is_transparent(level, CLOSED, *WALL_POS) is False
    assert is_transparent(level, OPEN, *WALL_POS) is False


def test_is_transparent_false_out_of_bounds():
    level = open_level()
    for x, y in [(-1, -1), (-1, 0), (0, -1), (100, 2), (2, 100), (10**6, 10**6)]:
        assert is_transparent(level, CLOSED, x, y) is False
        assert is_transparent(level, OPEN, x, y) is False


# --------------------------------------------------------------------------
# is_closed_door
# --------------------------------------------------------------------------


def test_is_closed_door_true_only_for_a_door_absent_from_open_doors():
    level = open_level()
    assert is_closed_door(level, CLOSED, *DOOR_POS) is True


def test_is_closed_door_false_for_an_open_door():
    level = open_level()
    assert is_closed_door(level, OPEN, *DOOR_POS) is False


def test_is_closed_door_false_for_floor():
    level = open_level()
    assert is_closed_door(level, CLOSED, *FLOOR_POS) is False
    assert is_closed_door(level, OPEN, *FLOOR_POS) is False


def test_is_closed_door_false_for_wall():
    level = open_level()
    assert is_closed_door(level, CLOSED, *WALL_POS) is False
    assert is_closed_door(level, OPEN, *WALL_POS) is False


def test_is_closed_door_false_out_of_bounds():
    level = open_level()
    for x, y in [(-1, -1), (-1, 0), (0, -1), (100, 2), (2, 100), (10**6, 10**6)]:
        assert is_closed_door(level, CLOSED, x, y) is False
        assert is_closed_door(level, OPEN, x, y) is False


# --------------------------------------------------------------------------
# The two predicates genuinely differ
# --------------------------------------------------------------------------


def test_closed_door_is_neither_passable_nor_transparent():
    level = open_level()
    assert is_passable(level, CLOSED, *DOOR_POS) is False
    assert is_transparent(level, CLOSED, *DOOR_POS) is False


def test_open_door_is_both_passable_and_transparent():
    level = open_level()
    assert is_passable(level, OPEN, *DOOR_POS) is True
    assert is_transparent(level, OPEN, *DOOR_POS) is True


def test_wall_is_neither_but_for_a_different_reason_than_a_closed_door():
    # A wall is impassable/opaque because of terrain (Level.is_walkable / Tile.WALL);
    # a closed door is impassable/opaque because of door *state*, not terrain — the
    # terrain under a door is always walkable (CONTRACT-v2 §0.6). Both predicates
    # agree the cell is unusable, but for structurally different reasons.
    level = open_level()
    assert level.is_walkable(*WALL_POS) is False
    assert level.is_walkable(*DOOR_POS) is True  # terrain-walkable even when closed
    assert is_passable(level, CLOSED, *WALL_POS) is False
    assert is_passable(level, CLOSED, *DOOR_POS) is False


def test_is_passable_and_is_transparent_agree_for_every_cell_of_todays_tile_set():
    # This equivalence is INCIDENTAL to today's three tiles (WALL / FLOOR / DOOR),
    # each of which is either "usable both ways" or "usable neither way" — it is not
    # a rule either function may rely on. Future terrain (a window: transparent but
    # not passable; a chasm: passable but not transparent — see CONTRACT-v2 §13) would
    # break it. This test documents the current fact, not a contract.
    level = open_level()
    for open_doors in (CLOSED, OPEN):
        for y in range(-2, level.height + 2):
            for x in range(-2, level.width + 2):
                assert is_passable(level, open_doors, x, y) == is_transparent(
                    level, open_doors, x, y
                )


# --------------------------------------------------------------------------
# Never raises, anywhere
# --------------------------------------------------------------------------


def test_no_predicate_raises_over_a_swept_margin_including_far_out_of_bounds():
    level = open_level()
    coords = [(x, y) for y in range(-2, level.height + 2) for x in range(-2, level.width + 2)]
    coords += [(-1, -1), (10**6, 10**6), (-(10**6), -(10**6))]
    for open_doors in (CLOSED, OPEN):
        for x, y in coords:
            is_passable(level, open_doors, x, y)
            is_transparent(level, open_doors, x, y)
            is_closed_door(level, open_doors, x, y)


# --------------------------------------------------------------------------
# Purity
# --------------------------------------------------------------------------


def test_predicates_do_not_mutate_level_or_open_doors_over_a_full_sweep():
    level = open_level()
    level_before = copy.deepcopy(level)
    doors_before = frozenset(OPEN)
    for y in range(-2, level.height + 2):
        for x in range(-2, level.width + 2):
            is_passable(level, OPEN, x, y)
            is_transparent(level, OPEN, x, y)
            is_closed_door(level, OPEN, x, y)
    assert level == level_before
    assert level.grid == level_before.grid
    assert OPEN == doors_before


def test_predicates_are_deterministic():
    level = open_level()
    for open_doors in (CLOSED, OPEN):
        for pos in (FLOOR_POS, WALL_POS, DOOR_POS, (-1, -1)):
            assert is_passable(level, open_doors, *pos) == is_passable(
                level, open_doors, *pos
            )
            assert is_transparent(level, open_doors, *pos) == is_transparent(
                level, open_doors, *pos
            )


# --------------------------------------------------------------------------
# Tiles amendment sanity (DOOR_OPEN_CHAR, unchanged closed-door glyph and vocabulary)
# --------------------------------------------------------------------------


def test_door_open_char_is_a_single_quote():
    assert DOOR_OPEN_CHAR == "'"


def test_tile_chars_door_is_still_the_closed_door_glyph():
    assert TILE_CHARS[Tile.DOOR] == "+"


def test_tile_vocabulary_is_pinned():
    """The world predicates are written against a known tile vocabulary, so a new
    tile must be a deliberate act — it has to pass through here.

    v3 added the two staircase tiles. Both are in ``WALKABLE``, which is the whole
    mechanism by which they became passable and transparent without this module
    changing at all (CONTRACT-v3 §0.9).
    """
    assert set(Tile) == {
        Tile.WALL,
        Tile.FLOOR,
        Tile.DOOR,
        Tile.STAIRS_UP,
        Tile.STAIRS_DOWN,
    }


# --------------------------------------------------------------------------
# Import hygiene (CONTRACT-v2 §10, §13)
# --------------------------------------------------------------------------


def _module_imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                names.add(node.module)
    return names


def test_world_module_imports_are_within_the_contract_graph():
    import roguelike.world

    imports = _module_imports(pathlib.Path(roguelike.world.__file__))
    assert "curses" not in imports
    roguelike_imports = {n for n in imports if n.split(".")[0] == "roguelike"}
    assert roguelike_imports <= {"roguelike.level", "roguelike.tiles"}
    assert imports <= {"__future__", "roguelike.level", "roguelike.tiles"}
