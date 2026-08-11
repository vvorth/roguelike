"""Tests for :mod:`roguelike.fov` — permissive field of view (CONTRACT-v2 §14) and
point-to-point line of sight (CONTRACT-v5 §14 v5).

Almost every level here is built by hand from a character grid (see :func:`make_level`).
``'@'`` in a grid marks the player start and is otherwise a floor cell, so the test
levels read as pictures of what is being asserted. The generator is imported only where
a criterion demands a *generated* level — the agreement, asymmetry and performance
checks on `has_line_of_sight`, and the regression guard that pins `compute_visible`.

No curses, no TTY, no ``conftest.py``.
"""

from __future__ import annotations

import ast
import copy
import inspect
import random
import time
from fractions import Fraction
from pathlib import Path

import pytest

from roguelike import fov
from roguelike.generator import generate_level
from roguelike.level import Level
from roguelike.tiles import Tile
from roguelike.world import is_passable, is_transparent

# --------------------------------------------------------------------------------------
# Level-building helper — a picture in, a Level out.
# --------------------------------------------------------------------------------------

CHAR_TO_TILE: dict[str, Tile] = {
    "#": Tile.WALL,
    ".": Tile.FLOOR,
    "+": Tile.DOOR,
    "@": Tile.FLOOR,  # player marker; the cell underneath is floor
}


def make_level(rows: list[str], seed: int = 0) -> Level:
    """Build a ``Level`` from a list of equal-length character-grid rows.

    ``#`` wall, ``.`` floor, ``+`` door, ``@`` floor **and** the player start. If no
    ``@`` appears, ``player_start`` defaults to ``(0, 0)``.
    """
    assert rows, "a level needs at least one row"
    width = len(rows[0])
    assert all(len(r) == width for r in rows), "all rows must be the same length"
    grid = tuple(tuple(CHAR_TO_TILE[c] for c in row) for row in rows)
    start = (0, 0)
    for y, row in enumerate(rows):
        x = row.find("@")
        if x != -1:
            start = (x, y)
    return Level(width, len(rows), grid, (), start, seed)


def open_level(width: int, height: int, start: tuple[int, int]) -> Level:
    """An entirely floor level of the given size."""
    rows = ["." * width for _ in range(height)]
    sx, sy = start
    rows[sy] = rows[sy][:sx] + "@" + rows[sy][sx + 1 :]
    return make_level(rows)


def picture(level: Level, visible: frozenset[tuple[int, int]], origin) -> str:
    """Render the visible set as a picture — used only in assertion messages."""
    out = []
    for y in range(level.height):
        line = []
        for x in range(level.width):
            if (x, y) == origin:
                line.append("@")
            elif (x, y) in visible:
                line.append({Tile.WALL: "#", Tile.FLOOR: ".", Tile.DOOR: "+"}[
                    level.grid[y][x]
                ])
            else:
                line.append(" ")
        out.append("".join(line))
    return "\n" + "\n".join(out)


# --------------------------------------------------------------------------------------
# Independent centre-to-centre ray test, for the F4 superset property.
#
# Deliberately written a different way from fov.py: it enumerates every grid-line
# crossing up front as an exact `Fraction`, sorts them, and walks the run — no DDA, no
# doubled integers, no shared code.
#
# "Unobstructed centre sight" is read conservatively: the segment must not *touch* an
# opaque cell at all, so a ray that slips past only by passing exactly through the corner
# point of a wall counts as obstructed. That is the same thing centre-to-centre
# shadowcasting does (F4 names shadowcasting as the comparison), and it is the honest
# reading of "unobstructed" — a line that grazes a wall's corner is not a clear line.
# --------------------------------------------------------------------------------------


def centre_ray_is_clear(
    level: Level,
    open_doors: frozenset[tuple[int, int]],
    origin: tuple[int, int],
    target: tuple[int, int],
) -> bool:
    """``True`` iff the eye-centre to target-centre segment touches no opaque cell.

    The origin and target cells are exempt, matching CONTRACT-v2 §14.1.
    """
    ox, oy = origin
    tx, ty = target
    if (ox, oy) == (tx, ty):
        return True

    ax, ay = Fraction(2 * ox + 1, 2), Fraction(2 * oy + 1, 2)
    bx, by = Fraction(2 * tx + 1, 2), Fraction(2 * ty + 1, 2)
    dx, dy = bx - ax, by - ay

    crossings: dict[Fraction, list[bool]] = {}
    if dx != 0:
        lo, hi = sorted((ax, bx))
        for k in range(int(lo) + 1, int(hi) + 1):
            t = (Fraction(k) - ax) / dx
            if 0 < t < 1:
                crossings.setdefault(t, [False, False])[0] = True
    if dy != 0:
        lo, hi = sorted((ay, by))
        for m in range(int(lo) + 1, int(hi) + 1):
            t = (Fraction(m) - ay) / dy
            if 0 < t < 1:
                crossings.setdefault(t, [False, False])[1] = True

    step_x = 1 if dx > 0 else -1
    step_y = 1 if dy > 0 else -1
    cx, cy = ox, oy
    for t in sorted(crossings):
        vertical, horizontal = crossings[t]
        if vertical and horizontal:
            # The segment touches all four cells meeting at this lattice point.
            if not is_transparent(level, open_doors, cx + step_x, cy):
                return False
            if not is_transparent(level, open_doors, cx, cy + step_y):
                return False
            cx += step_x
            cy += step_y
        elif vertical:
            cx += step_x
        else:
            cy += step_y
        if (cx, cy) != (tx, ty) and not is_transparent(level, open_doors, cx, cy):
            return False
    return True


def centre_visible_cells(
    level: Level,
    open_doors: frozenset[tuple[int, int]],
    origin: tuple[int, int],
    radius: int,
) -> set[tuple[int, int]]:
    """Every in-bounds, in-radius cell whose centre has unobstructed sight."""
    ox, oy = origin
    found = set()
    for y in range(level.height):
        for x in range(level.width):
            if (x - ox) ** 2 + (y - oy) ** 2 > radius * radius:
                continue
            if centre_ray_is_clear(level, open_doors, origin, (x, y)):
                found.add((x, y))
    return found


# --------------------------------------------------------------------------------------
# Shared fixtures-as-constants (plain data, no pytest fixtures across task boundaries).
# --------------------------------------------------------------------------------------

ROOM_ROWS = [
    "#########",
    "#.......#",
    "#.......#",
    "#...@...#",
    "#.......#",
    "#########",
]

TWO_ROOMS_ROWS = [
    "#############",
    "#.....#.....#",
    "#.....#.....#",
    "#..@..+.....#",
    "#.....#.....#",
    "#.....#.....#",
    "#############",
]
DOOR_CELL = (6, 3)

PILLAR_ROWS = [
    ".....................",
    ".....................",
    ".....................",
    ".@...#...............",
    ".....................",
    ".....................",
    ".....................",
]
PILLAR_CELL = (5, 3)


def room_ring(level: Level) -> list[tuple[int, int]]:
    """The 1-cell wall ring of a level whose entire border is wall."""
    ring = []
    for x in range(level.width):
        ring.append((x, 0))
        ring.append((x, level.height - 1))
    for y in range(1, level.height - 1):
        ring.append((0, y))
        ring.append((level.width - 1, y))
    return ring


# ======================================================================================
# Module surface and import graph (CONTRACT-v2 §14, §10)
# ======================================================================================


def test_default_radius_is_20():
    assert fov.DEFAULT_RADIUS == 20
    assert isinstance(fov.DEFAULT_RADIUS, int)


def test_module_exports_exactly_the_contract_surface():
    # v5 adds exactly one name (CONTRACT-v5 §9 v5, baseline table) and nothing else.
    assert fov.__all__ == ["DEFAULT_RADIUS", "compute_visible", "has_line_of_sight"]


def test_compute_visible_signature_matches_the_contract():
    sig = inspect.signature(fov.compute_visible)
    assert list(sig.parameters) == ["level", "open_doors", "origin", "radius"]
    assert sig.parameters["radius"].default == fov.DEFAULT_RADIUS
    for name in ("level", "open_doors", "origin"):
        assert sig.parameters[name].default is inspect.Parameter.empty


def _fov_source() -> str:
    return Path(fov.__file__).read_text()


def _imported_modules(source: str) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                names.add(node.module)
                if node.module == "roguelike":
                    names.update(f"roguelike.{a.name}" for a in node.names)
    return names


def test_fov_imports_only_level_world_and_permitted_stdlib():
    imported = _imported_modules(_fov_source())
    project = {n for n in imported if n == "roguelike" or n.startswith("roguelike.")}
    assert project <= {"roguelike.level", "roguelike.world"}, project
    stdlib = imported - project
    assert stdlib <= {"__future__", "math"}, stdlib


def test_fov_does_not_import_curses_render_game_generator_or_style():
    imported = _imported_modules(_fov_source())
    for forbidden in (
        "curses",
        "roguelike.render",
        "roguelike.game",
        "roguelike.generator",
        "roguelike.style",
        "roguelike.tiles",
    ):
        assert forbidden not in imported


def test_fov_source_never_mentions_curses_in_code():
    tree = ast.parse(_fov_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            assert node.value.id != "curses"
        if isinstance(node, ast.Name):
            assert node.id != "curses"


def test_fov_never_looks_at_tiles_itself():
    """Opacity has exactly one home: `world.is_transparent`."""
    source = _fov_source()
    tree = ast.parse(source)
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "tile_at" not in called
    assert "is_walkable" not in called
    assert "Tile" not in {
        n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
    }


def test_result_is_a_frozenset_of_int_pairs():
    level = make_level(ROOM_ROWS)
    result = fov.compute_visible(level, frozenset(), level.player_start)
    assert isinstance(result, frozenset)
    assert result
    for cell in result:
        assert isinstance(cell, tuple) and len(cell) == 2
        assert isinstance(cell[0], int) and isinstance(cell[1], int)


def test_never_returns_none():
    level = make_level(["#"])
    assert fov.compute_visible(level, frozenset(), (0, 0)) is not None


# ======================================================================================
# F1 — the origin is always in the result
# ======================================================================================


def test_f1_origin_on_floor_is_visible():
    level = make_level(ROOM_ROWS)
    result = fov.compute_visible(level, frozenset(), (4, 3))
    assert (4, 3) in result


def test_f1_origin_on_a_wall_is_visible():
    level = make_level(ROOM_ROWS)
    result = fov.compute_visible(level, frozenset(), (0, 0))
    assert (0, 0) in result


def test_f1_origin_on_a_closed_door_is_visible():
    level = make_level(TWO_ROOMS_ROWS)
    result = fov.compute_visible(level, frozenset(), DOOR_CELL)
    assert DOOR_CELL in result


def test_f1_origin_walled_in_on_every_side_is_still_visible():
    level = make_level(["###", "#@#", "###"])
    result = fov.compute_visible(level, frozenset(), (1, 1))
    assert (1, 1) in result


def test_f1_origin_survives_radius_zero():
    level = make_level(ROOM_ROWS)
    assert fov.compute_visible(level, frozenset(), (4, 3), 0) == frozenset({(4, 3)})


def test_f1_result_is_never_empty_anywhere_on_the_map():
    level = make_level(TWO_ROOMS_ROWS)
    for y in range(level.height):
        for x in range(level.width):
            for radius in (0, 1, 7):
                result = fov.compute_visible(level, frozenset(), (x, y), radius)
                assert (x, y) in result


# ======================================================================================
# F2 — in bounds, and within Euclidean radius
# ======================================================================================


def test_f2_every_cell_is_in_bounds_and_in_radius_over_several_radii():
    level = open_level(31, 17, (15, 8))
    for radius in (0, 1, 2, 3, 5, 8, 13, 20, 40):
        result = fov.compute_visible(level, frozenset(), (15, 8), radius)
        for x, y in result:
            assert level.in_bounds(x, y), (x, y, radius)
            assert (x - 15) ** 2 + (y - 8) ** 2 <= radius * radius, (x, y, radius)


def test_f2_holds_from_a_corner_where_the_disc_runs_off_the_map():
    level = open_level(20, 12, (0, 0))
    for radius in (1, 4, 9, 30):
        result = fov.compute_visible(level, frozenset(), (0, 0), radius)
        for x, y in result:
            assert 0 <= x < 20 and 0 <= y < 12
            assert x * x + y * y <= radius * radius


def test_f2_radius_boundary_is_inclusive_and_euclidean():
    level = open_level(21, 21, (10, 10))
    result = fov.compute_visible(level, frozenset(), (10, 10), 5)
    assert (13, 14) in result  # 3^2 + 4^2 == 25 == 5^2, exactly on the circle
    assert (14, 14) not in result  # 4^2 + 4^2 == 32 > 25
    assert (10, 15) in result  # straight down, exactly 5
    assert (10, 16) not in result


def test_f2_radius_far_larger_than_the_map_does_not_raise():
    level = open_level(23, 11, (11, 5))
    result = fov.compute_visible(level, frozenset(), (11, 5), 1000)
    assert result == frozenset(
        (x, y) for y in range(11) for x in range(23)
    )


def test_f2_shrinking_the_radius_never_adds_cells():
    level = make_level(PILLAR_ROWS)
    origin = level.player_start
    previous = None
    for radius in (20, 12, 7, 4, 2, 1, 0):
        result = fov.compute_visible(level, frozenset(), origin, radius)
        if previous is not None:
            assert result <= previous, radius
        previous = result


# ======================================================================================
# F3 — purity and determinism
# ======================================================================================


def test_f3_two_identical_calls_return_equal_sets():
    level = make_level(TWO_ROOMS_ROWS)
    a = fov.compute_visible(level, frozenset({DOOR_CELL}), (3, 3), 9)
    b = fov.compute_visible(level, frozenset({DOOR_CELL}), (3, 3), 9)
    assert a == b


def test_f3_the_level_is_not_mutated():
    level = make_level(TWO_ROOMS_ROWS)
    before = copy.deepcopy(level)
    fov.compute_visible(level, frozenset({DOOR_CELL}), (3, 3))
    assert level == before
    assert level.grid == before.grid


def test_f3_open_doors_is_not_mutated():
    level = make_level(TWO_ROOMS_ROWS)
    doors = frozenset({DOOR_CELL})
    before = set(doors)
    fov.compute_visible(level, doors, (3, 3))
    assert set(doors) == before
    assert doors == frozenset({DOOR_CELL})


def test_f3_result_does_not_depend_on_open_doors_iteration_order():
    rows = [
        "###########",
        "#.@.+...#.#",
        "#...#...+.#",
        "#...#...#.#",
        "###########",
    ]
    level = make_level(rows)
    cells = [(4, 1), (8, 2)]
    a = fov.compute_visible(level, frozenset(cells), (2, 1))
    b = fov.compute_visible(level, frozenset(reversed(cells)), (2, 1))
    assert a == b


def test_f3_interleaved_calls_do_not_contaminate_each_other():
    room = make_level(ROOM_ROWS)
    pillar = make_level(PILLAR_ROWS)
    a1 = fov.compute_visible(room, frozenset(), room.player_start)
    p1 = fov.compute_visible(pillar, frozenset(), pillar.player_start)
    a2 = fov.compute_visible(room, frozenset(), room.player_start)
    p2 = fov.compute_visible(pillar, frozenset(), pillar.player_start)
    assert a1 == a2
    assert p1 == p2


def test_f3_no_module_level_mutable_state():
    for name, value in vars(fov).items():
        if name.startswith("__"):
            continue
        assert not isinstance(value, (list, dict, set, bytearray)), name


# ======================================================================================
# F4 — superset property: permissive never sees less than centre-only
# ======================================================================================


def test_f4_superset_on_an_open_room():
    level = make_level(ROOM_ROWS)
    origin = level.player_start
    result = fov.compute_visible(level, frozenset(), origin, 20)
    centres = centre_visible_cells(level, frozenset(), origin, 20)
    assert centres <= result, sorted(centres - result)


def test_f4_superset_on_a_level_with_a_pillar():
    level = make_level(PILLAR_ROWS)
    origin = level.player_start
    for radius in (4, 9, 20):
        result = fov.compute_visible(level, frozenset(), origin, radius)
        centres = centre_visible_cells(level, frozenset(), origin, radius)
        assert centres <= result, (radius, sorted(centres - result))


def test_f4_superset_on_the_two_room_level_with_the_door_shut_and_open():
    level = make_level(TWO_ROOMS_ROWS)
    origin = level.player_start
    for doors in (frozenset(), frozenset({DOOR_CELL})):
        result = fov.compute_visible(level, doors, origin, 12)
        centres = centre_visible_cells(level, doors, origin, 12)
        assert centres <= result, sorted(centres - result)


def test_f4_superset_from_every_open_cell_of_a_cluttered_level():
    rows = [
        "###############",
        "#.....#.......#",
        "#.###.#.#####.#",
        "#.#...+.....#.#",
        "#.#.#######.#.#",
        "#...#.....#...#",
        "#####.###.#####",
        "#.....#.......#",
        "###############",
    ]
    level = make_level(rows)
    doors = frozenset({(6, 3)})
    for y in range(level.height):
        for x in range(level.width):
            if level.grid[y][x] is Tile.WALL:
                continue
            result = fov.compute_visible(level, doors, (x, y), 8)
            centres = centre_visible_cells(level, doors, (x, y), 8)
            assert centres <= result, ((x, y), sorted(centres - result))


def test_f4_permissive_sees_strictly_more_than_centre_only_in_a_room():
    """The 12-cell gap the research measured: wall corners centre-only misses."""
    level = make_level(ROOM_ROWS)
    origin = level.player_start
    result = fov.compute_visible(level, frozenset(), origin, 20)
    centres = centre_visible_cells(level, frozenset(), origin, 20)
    extra = result - centres
    assert extra, "permissive must find cells centre-only cannot"
    for cell in extra:
        assert not is_transparent(level, frozenset(), *cell), cell


# ======================================================================================
# F5 — a pillar casts a shadow, and the shadow widens with distance
# ======================================================================================


def test_f5_a_single_wall_cell_hides_what_is_directly_behind_it():
    level = make_level(PILLAR_ROWS)
    origin = level.player_start  # (1, 3)
    result = fov.compute_visible(level, frozenset(), origin, 20)
    assert PILLAR_CELL in result, "the pillar's own face is visible"
    for x in range(6, 21):
        assert (x, 3) not in result, (x, 3)


def test_f5_the_shadow_widens_with_distance():
    level = make_level(PILLAR_ROWS)
    origin = level.player_start
    result = fov.compute_visible(level, frozenset(), origin, 40)
    hidden = {
        x: sum(1 for y in range(level.height) if (x, y) not in result)
        for x in range(6, level.width)
    }
    assert hidden[7] >= 1
    assert hidden[13] > hidden[7]
    assert hidden[20] > hidden[13]


def test_f5_cells_just_off_the_shadow_axis_are_still_seen():
    level = make_level(PILLAR_ROWS)
    result = fov.compute_visible(level, frozenset(), level.player_start, 20)
    assert (6, 2) in result
    assert (6, 4) in result


def test_f5_a_cell_with_no_clear_segment_to_any_sample_point_is_not_visible():
    """A cell in a sealed pocket: eight blocked samples, so not visible."""
    rows = [
        "#########",
        "#.@.....#",
        "#...#####",
        "#####...#",
        "#####.X.#",
        "#########",
    ]
    rows = [r.replace("X", ".") for r in rows]
    level = make_level(rows)
    result = fov.compute_visible(level, frozenset(), (2, 1), 20)
    assert (6, 4) not in result


# ======================================================================================
# F6 — no holes in the walls of the room you stand in (the ragged-wall test)
# ======================================================================================


def test_f6_every_cell_of_the_surrounding_wall_ring_is_visible():
    level = make_level(ROOM_ROWS)
    origin = level.player_start
    result = fov.compute_visible(level, frozenset(), origin)
    missing = [cell for cell in room_ring(level) if cell not in result]
    assert not missing, f"holes in the wall ring: {missing}{picture(level, result, origin)}"


def test_f6_the_whole_room_is_visible_not_just_the_ring():
    level = make_level(ROOM_ROWS)
    origin = level.player_start
    result = fov.compute_visible(level, frozenset(), origin)
    assert result == frozenset(
        (x, y) for y in range(level.height) for x in range(level.width)
    )


@pytest.mark.parametrize(
    "floor_w,floor_h",
    [(1, 1), (2, 2), (3, 3), (5, 4), (7, 5), (12, 8), (15, 3), (3, 15)],
)
def test_f6_wall_ring_is_complete_for_many_room_sizes(floor_w, floor_h):
    width, height = floor_w + 2, floor_h + 2
    rows = ["#" * width]
    for _ in range(floor_h):
        rows.append("#" + "." * floor_w + "#")
    rows.append("#" * width)
    cx, cy = 1 + floor_w // 2, 1 + floor_h // 2
    rows[cy] = rows[cy][:cx] + "@" + rows[cy][cx + 1 :]
    level = make_level(rows)
    result = fov.compute_visible(level, frozenset(), (cx, cy))
    missing = [cell for cell in room_ring(level) if cell not in result]
    assert not missing, f"{missing}{picture(level, result, (cx, cy))}"


def test_f6_wall_ring_is_complete_from_every_cell_of_the_room():
    """Not just from the middle — a hole anywhere in the ring is the same defect."""
    rows = ["#########"] + ["#.......#"] * 4 + ["#########"]
    level = make_level(rows)
    ring = room_ring(level)
    for y in range(1, level.height - 1):
        for x in range(1, level.width - 1):
            result = fov.compute_visible(level, frozenset(), (x, y))
            missing = [cell for cell in ring if cell not in result]
            assert not missing, f"from {(x, y)}: {missing}"


def test_f6_opaque_cells_can_be_visible():
    level = make_level(ROOM_ROWS)
    result = fov.compute_visible(level, frozenset(), level.player_start)
    walls = {c for c in result if not is_transparent(level, frozenset(), *c)}
    assert len(walls) == len(room_ring(level))


# ======================================================================================
# F7 — a closed door is opaque, an open door is not (the key behavioural test)
# ======================================================================================


def far_room_cells(level: Level) -> list[tuple[int, int]]:
    return [(x, y) for y in range(1, 6) for x in range(7, 12)]


def test_f7_a_closed_door_hides_the_room_behind_it():
    level = make_level(TWO_ROOMS_ROWS)
    origin = level.player_start
    result = fov.compute_visible(level, frozenset(), origin, 20)
    leaked = [c for c in far_room_cells(level) if c in result]
    assert not leaked, f"{leaked}{picture(level, result, origin)}"


def test_f7_opening_the_door_reveals_the_room_behind_it():
    level = make_level(TWO_ROOMS_ROWS)
    origin = level.player_start
    result = fov.compute_visible(level, frozenset({DOOR_CELL}), origin, 20)
    seen = [c for c in far_room_cells(level) if c in result]
    assert seen, picture(level, result, origin)
    # Straight through the doorway, at least, must be visible.
    assert (7, 3) in result
    assert (11, 3) in result


def test_f7_the_open_door_strictly_adds_cells_and_removes_none():
    level = make_level(TWO_ROOMS_ROWS)
    origin = level.player_start
    shut = fov.compute_visible(level, frozenset(), origin, 20)
    ajar = fov.compute_visible(level, frozenset({DOOR_CELL}), origin, 20)
    assert shut < ajar


def test_f7_the_closed_door_itself_is_visible_you_see_its_face():
    level = make_level(TWO_ROOMS_ROWS)
    result = fov.compute_visible(level, frozenset(), level.player_start, 20)
    assert DOOR_CELL in result


def test_f7_only_the_opened_door_lets_sight_through():
    rows = [
        "#############",
        "#.....#.....#",
        "#..@..+.....#",
        "#.....#.....#",
        "#.....+.....#",
        "#.....#.....#",
        "#############",
    ]
    level = make_level(rows)
    origin = (3, 2)
    top, bottom = (6, 2), (6, 4)
    only_top = fov.compute_visible(level, frozenset({top}), origin, 20)
    only_bottom = fov.compute_visible(level, frozenset({bottom}), origin, 20)
    assert (7, 2) in only_top
    assert (7, 2) not in only_bottom
    assert only_top != only_bottom


def test_f7_standing_in_an_open_doorway_sees_both_rooms():
    level = make_level(TWO_ROOMS_ROWS)
    doors = frozenset({DOOR_CELL})
    result = fov.compute_visible(level, doors, DOOR_CELL, 20)
    assert (2, 3) in result
    assert (10, 3) in result


# ======================================================================================
# F8 — radius edge cases
# ======================================================================================


def test_f8_radius_zero_returns_exactly_the_origin():
    level = make_level(ROOM_ROWS)
    assert fov.compute_visible(level, frozenset(), (4, 3), 0) == frozenset({(4, 3)})
    assert fov.compute_visible(level, frozenset(), (0, 0), 0) == frozenset({(0, 0)})


def test_f8_negative_radius_raises_value_error():
    level = make_level(ROOM_ROWS)
    with pytest.raises(ValueError):
        fov.compute_visible(level, frozenset(), (4, 3), -1)


@pytest.mark.parametrize("radius", [-1, -2, -20, -1000])
def test_f8_every_negative_radius_raises_value_error(radius):
    level = make_level(ROOM_ROWS)
    with pytest.raises(ValueError):
        fov.compute_visible(level, frozenset(), (4, 3), radius)


def test_f8_radius_one_sees_at_most_the_surrounding_ring():
    level = open_level(9, 9, (4, 4))
    result = fov.compute_visible(level, frozenset(), (4, 4), 1)
    assert result == frozenset({(4, 4), (3, 4), (5, 4), (4, 3), (4, 5)})


# ======================================================================================
# A cell directly behind a long wall is never visible, at any radius
# ======================================================================================


def test_a_cell_behind_a_long_wall_is_never_visible_at_any_radius():
    rows = [
        "..............",
        "..............",
        "....@.........",
        "..............",
        "##############",
        "..............",
        "..............",
        "..............",
    ]
    level = make_level(rows)
    behind = [(x, y) for y in (5, 6, 7) for x in range(level.width)]
    for radius in (1, 2, 3, 5, 8, 13, 20, 50, 200):
        result = fov.compute_visible(level, frozenset(), (4, 2), radius)
        leaked = [c for c in behind if c in result]
        assert not leaked, (radius, leaked)


def test_the_face_of_the_long_wall_is_visible_but_nothing_past_it():
    rows = [
        "..............",
        "....@.........",
        "##############",
        "..............",
    ]
    level = make_level(rows)
    result = fov.compute_visible(level, frozenset(), (4, 1), 20)
    for x in range(level.width):
        assert (x, 2) in result, x
        assert (x, 3) not in result, x


# ======================================================================================
# The diagonal-corner rule
# ======================================================================================

DIAGONAL_JOIN_ROWS = [
    "......",
    ".@#...",
    ".#....",
    "......",
    "......",
    "......",
]
SINGLE_CORNER_ROWS = [
    "......",
    ".@#...",
    "......",
    "......",
    "......",
    "......",
]


def test_diagonal_join_of_two_walls_does_not_leak_sight_past_it():
    level = make_level(DIAGONAL_JOIN_ROWS)
    result = fov.compute_visible(level, frozenset(), (1, 1), 20)
    for cell in [(3, 3), (4, 4), (5, 5), (4, 3), (3, 4), (5, 4), (4, 5)]:
        assert cell not in result, f"{cell} leaked{picture(level, result, (1, 1))}"


def test_a_single_wall_corner_still_allows_corner_peeking():
    level = make_level(SINGLE_CORNER_ROWS)
    result = fov.compute_visible(level, frozenset(), (1, 1), 20)
    for cell in [(3, 3), (4, 4), (5, 5)]:
        assert cell in result, f"{cell} not peeked{picture(level, result, (1, 1))}"


def test_the_second_wall_is_what_makes_the_difference():
    """The two levels differ by exactly one cell; so must the leak."""
    joined = fov.compute_visible(make_level(DIAGONAL_JOIN_ROWS), frozenset(), (1, 1), 20)
    single = fov.compute_visible(make_level(SINGLE_CORNER_ROWS), frozenset(), (1, 1), 20)
    assert (3, 3) in single
    assert (3, 3) not in joined


def test_a_closed_door_completes_a_diagonal_join_and_an_open_one_breaks_it():
    rows = [
        "......",
        ".@#...",
        ".+....",
        "...X..",
        "......",
    ]
    rows = [r.replace("X", ".") for r in rows]
    level = make_level(rows)
    shut = fov.compute_visible(level, frozenset(), (1, 1), 20)
    ajar = fov.compute_visible(level, frozenset({(1, 2)}), (1, 1), 20)
    assert (3, 3) not in shut
    assert (3, 3) in ajar


# A wall on the full main diagonal. It cuts the map into two open triangles that touch
# only at the lattice points (k, k), so it is the strongest available test of whether
# sight can cross a diagonal join.
LONG_DIAGONAL_ROWS = [
    "#.......",
    ".#......",
    "..#.....",
    "...#....",
    "....#...",
    ".....#..",
    "......#.",
    ".......#",
]
# Cells on the far side whose own corner IS one of those lattice points.
CORNER_TOUCHING_BELOW = {(k - 1, k) for k in range(1, 8)}
CORNER_TOUCHING_ABOVE = {(k, k - 1) for k in range(1, 8)}


@pytest.mark.parametrize("eye", [(6, 2), (7, 1), (5, 0), (7, 4), (3, 0), (7, 6)])
def test_sight_never_crosses_a_long_diagonal_wall(eye):
    """Nothing on the far side of the diagonal is visible beyond its very edge.

    Any segment crossing the wall must pass *through* some lattice point ``(k, k)``,
    whose two diagonal flankers ``(k-1, k-1)`` and ``(k, k)`` are both wall — so §14.1's
    diagonal-corner rule blocks it. ``y - x <= 1`` for every visible cell is that
    guarantee stated exactly: the near side, the wall itself, and at most the single row
    of far cells that touch the wall at a corner.
    """
    level = make_level(LONG_DIAGONAL_ROWS)
    result = fov.compute_visible(level, frozenset(), eye, 20)
    for x, y in result:
        assert y - x <= 1, ((x, y), eye, picture(level, result, eye))
    far = {c for c in result if c[0] < c[1]}
    assert far <= CORNER_TOUCHING_BELOW, sorted(far - CORNER_TOUCHING_BELOW)


@pytest.mark.parametrize("eye", [(2, 6), (1, 7), (0, 5), (4, 7)])
def test_sight_never_crosses_a_long_diagonal_wall_from_the_other_side(eye):
    level = make_level(LONG_DIAGONAL_ROWS)
    result = fov.compute_visible(level, frozenset(), eye, 20)
    for x, y in result:
        assert x - y <= 1, ((x, y), eye, picture(level, result, eye))
    far = {c for c in result if c[0] > c[1]}
    assert far <= CORNER_TOUCHING_ABOVE, sorted(far - CORNER_TOUCHING_ABOVE)


def test_a_diagonal_wall_seen_from_its_apex_does_not_seal_the_open_region():
    """The region *beside* a diagonal wall is open ground, not something it hides.

    Guards against the mistake of reading a diagonal wall as sealing both triangles: an
    eye at the apex is adjacent to the open upper-right region and simply sees into it,
    including past the wall's first cell via the single-corner peek of §14.1.
    """
    rows = ["@......."] + LONG_DIAGONAL_ROWS[1:]
    level = make_level(rows)  # wall runs (1,1)..(7,7); (0,0) is floor
    result = fov.compute_visible(level, frozenset(), (0, 0), 20)
    # Both triangles are open ground the apex is adjacent to, so both are seen into.
    assert (7, 0) in result
    assert (4, 2) in result, picture(level, result, (0, 0))
    assert (0, 7) in result
    assert (2, 4) in result
    # What the wall does hide from the apex is the pocket along its far flank, where the
    # shallow sight lines run out of open cells to travel down.
    for cell in [(4, 3), (5, 4), (4, 4), (6, 6), (3, 3)]:
        assert cell not in result, (cell, picture(level, result, (0, 0)))


def test_the_cell_whose_corner_is_the_wall_join_tip_is_visible_and_that_is_deliberate():
    """The one thing that shows through a diagonal join, and why it must.

    A segment that *ends* on the lattice point does not *pass through* it, so §14.1's
    diagonal-corner rule does not fire. That exemption is not optional: the only clear
    line from the middle of a room to a corner cell of its wall ring ends exactly on the
    room's inner corner, whose flankers are the two wall runs meeting there. Applying the
    rule at the endpoint would punch that corner out of every room — the ragged-wall
    artifact F6 forbids. The cost is this cell, whose corner the eye genuinely does see.
    """
    level = make_level(DIAGONAL_JOIN_ROWS)
    result = fov.compute_visible(level, frozenset(), (1, 1), 20)
    assert (2, 2) in result  # its top-left corner is the visible tip of the join
    assert (3, 3) not in result  # nothing actually beyond the join


# ======================================================================================
# Sight is not blocked by the target cell itself
# ======================================================================================


def test_a_wall_adjacent_to_the_eye_is_visible():
    level = make_level(["#####", "#.#.#", "#.@.#", "#.#.#", "#####"])
    result = fov.compute_visible(level, frozenset(), (2, 2), 20)
    assert (2, 1) in result
    assert (2, 3) in result


def test_every_one_of_the_eight_neighbours_is_visible_even_when_all_are_walls():
    level = make_level(["#####", "#####", "##@##", "#####", "#####"])
    result = fov.compute_visible(level, frozenset(), (2, 2), 20)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            assert (2 + dx, 2 + dy) in result, (dx, dy)


def test_a_closed_door_adjacent_to_the_eye_is_visible():
    level = make_level(["###", "#@#", "#+#", "###"])
    result = fov.compute_visible(level, frozenset(), (1, 1), 20)
    assert (1, 2) in result


# ======================================================================================
# Degenerate levels — nothing raises
# ======================================================================================


def test_a_level_that_is_entirely_wall_returns_origin_plus_at_most_the_adjacent_ring():
    level = make_level(["#####"] * 5)
    result = fov.compute_visible(level, frozenset(), (2, 2), 20)
    allowed = {(2 + dx, 2 + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
    assert (2, 2) in result
    assert result <= allowed, sorted(result - allowed)


def test_a_one_by_one_level_returns_only_the_origin():
    for row in ("#", ".", "+"):
        level = make_level([row])
        assert fov.compute_visible(level, frozenset(), (0, 0), 20) == frozenset(
            {(0, 0)}
        )


def test_a_one_cell_wide_level_does_not_raise():
    level = make_level(["."] * 7)
    result = fov.compute_visible(level, frozenset(), (0, 3), 20)
    assert result == frozenset((0, y) for y in range(7))


def test_a_one_cell_tall_level_does_not_raise():
    level = make_level(["......."])
    result = fov.compute_visible(level, frozenset(), (3, 0), 20)
    assert result == frozenset((x, 0) for x in range(7))


@pytest.mark.parametrize("origin", [(0, 0), (0, 10), (14, 0), (14, 10), (7, 0), (0, 5)])
def test_origins_on_the_map_border_do_not_raise(origin):
    level = open_level(15, 11, (7, 5))
    result = fov.compute_visible(level, frozenset(), origin, 20)
    assert origin in result
    for x, y in result:
        assert level.in_bounds(x, y)


def test_an_open_level_is_fully_visible_from_its_middle():
    level = open_level(15, 11, (7, 5))
    result = fov.compute_visible(level, frozenset(), (7, 5), 20)
    assert result == frozenset((x, y) for y in range(11) for x in range(15))


# ======================================================================================
# Doors as an opacity source, exercised on their own
# ======================================================================================


def test_an_open_door_is_transparent_and_a_closed_one_is_not():
    level = make_level(["#####", "#@+.#", "#####"])
    assert (3, 1) not in fov.compute_visible(level, frozenset(), (1, 1), 20)
    assert (3, 1) in fov.compute_visible(level, frozenset({(2, 1)}), (1, 1), 20)


def test_an_unrelated_open_door_entry_changes_nothing():
    level = make_level(["#####", "#@+.#", "#####"])
    base = fov.compute_visible(level, frozenset(), (1, 1), 20)
    assert fov.compute_visible(level, frozenset({(9, 9)}), (1, 1), 20) == base


def test_the_door_shape_the_generator_guarantees_two_open_cells_either_side():
    """T09 point 4: a closed door always separates exactly two open cells."""
    level = make_level(["#####", "#...#", "##+##", "#...#", "#@..#", "#####"])
    origin = (1, 4)
    shut = fov.compute_visible(level, frozenset(), origin, 20)
    ajar = fov.compute_visible(level, frozenset({(2, 2)}), origin, 20)
    assert (2, 2) in shut  # the door's own face
    assert not any((x, 1) in shut for x in (1, 2, 3))
    assert (2, 1) in ajar


# ======================================================================================
# Symmetry note and general sanity over a mixed level
# ======================================================================================


def test_visibility_is_reflexive_and_the_result_always_contains_the_origin():
    level = make_level(TWO_ROOMS_ROWS)
    doors = frozenset({DOOR_CELL})
    for y in range(level.height):
        for x in range(level.width):
            assert (x, y) in fov.compute_visible(level, doors, (x, y), 6)


def test_nothing_outside_the_reachable_half_of_a_sealed_level_is_ever_seen():
    rows = [
        "###########",
        "#.@.......#",
        "#.........#",
        "###########",
        "#.........#",
        "#.........#",
        "###########",
    ]
    level = make_level(rows)
    result = fov.compute_visible(level, frozenset(), (2, 1), 50)
    for y in (4, 5):
        for x in range(level.width):
            assert (x, y) not in result, (x, y)


# ======================================================================================
# REGRESSION GUARD on `compute_visible` (CONTRACT-v5 §14 v5)
#
# v5 adds `has_line_of_sight` to this module and changes `compute_visible` **not at all**.
# `tests/test_activity.py` is frozen and drives the real `compute_visible` through a full
# auto-explore run, so a behavioural drift here breaks a test no v5 worker may repair.
# These assertions exist to make such a drift fail *here*, loudly, first: exact visible
# sets rendered as pictures, exact cell counts at three radii, the mirror symmetry of a
# symmetric room, the door open/closed difference, and exact counts over 44 origins on
# two generated levels. Every value below was captured from the pre-v5 implementation.
# ======================================================================================


def stripped_picture(
    level: Level, visible: frozenset[tuple[int, int]], origin
) -> list[str]:
    """:func:`picture` as a list of right-stripped lines, for exact comparison.

    Right-stripping both sides loses nothing: only spaces are removed, and a space means
    "not visible", which the shorter line still says.
    """
    return [line.rstrip() for line in picture(level, visible, origin).lstrip("\n").split("\n")]


CLUTTER_ROWS = [
    "#####################",
    "#...................#",
    "#..#....#......#....#",
    "#...................#",
    "#....#.......#......#",
    "#.........@.........#",
    "#...#..........#....#",
    "#...................#",
    "#.#.......#.......#.#",
    "#...................#",
    "#####################",
]
CLUTTER_ORIGIN = (10, 5)

SYMMETRIC_ROOM_ROWS = [
    "#########",
    "#.......#",
    "#.......#",
    "#.......#",
    "#...@...#",
    "#.......#",
    "#.......#",
    "#.......#",
    "#########",
]
SYMMETRIC_ROOM_ORIGIN = (4, 4)

#: Seeds of the generated levels used by the guard and by the `has_line_of_sight`
#: agreement checks. `generate_level` is deterministic and frozen, so these are stable.
GENERATED_SEEDS = (1, 2)


def generated_origins(level: Level, count: int = 22) -> list[tuple[int, int]]:
    """An even, deterministic spread of ``count`` passable cells across ``level``."""
    floors = sorted(
        (x, y)
        for y in range(level.height)
        for x in range(level.width)
        if is_passable(level, frozenset(), x, y)
    )
    step = max(1, len(floors) // count)
    return [floors[i] for i in range(0, len(floors), step)][:count]


#: Captured from the pre-v5 implementation: `len(compute_visible(level, frozenset(), o,
#: 20))` for each origin of `generated_origins(generate_level(seed))`.
GENERATED_VISIBLE_COUNTS: dict[int, tuple[int, ...]] = {
    1: (144, 144, 144, 144, 144, 90, 90, 90, 66, 56, 112,
        112, 112, 112, 112, 112, 112, 169, 169, 169, 169, 169),
    2: (48, 72, 72, 42, 54, 54, 54, 12, 80, 80, 48,
        168, 168, 168, 168, 168, 66, 66, 144, 54, 144, 144),
}


def test_regression_compute_visible_exact_picture_with_the_door_closed():
    level = make_level(TWO_ROOMS_ROWS)
    result = fov.compute_visible(level, frozenset(), (3, 3), 20)
    assert stripped_picture(level, result, (3, 3)) == [
        "#######",
        "#.....#",
        "#.....#",
        "#..@..+",
        "#.....#",
        "#.....#",
        "#######",
    ]


def test_regression_compute_visible_exact_picture_with_the_door_open():
    level = make_level(TWO_ROOMS_ROWS)
    result = fov.compute_visible(level, frozenset({DOOR_CELL}), (3, 3), 20)
    assert stripped_picture(level, result, (3, 3)) == [
        "#######",
        "#.....#",
        "#.....#.....#",
        "#..@..+.....#",
        "#.....#.....#",
        "#.....#",
        "#######",
    ]


def test_regression_compute_visible_exact_picture_on_a_cluttered_room():
    """The strongest single assertion here: every pillar shadow, cell for cell."""
    level = make_level(CLUTTER_ROWS)
    result = fov.compute_visible(level, frozenset(), CLUTTER_ORIGIN, 20)
    assert stripped_picture(level, result, CLUTTER_ORIGIN) == [
        "  ##### #########",
        "  ...............",
        "# .#....#......#",
        "  .............",
        "#....#.......#......#",
        "#.........@.........#",
        "#...#..........#....#",
        " ..................",
        "#.#.......#.......#",
        "#......... .........#",
        "########## ##########",
    ]


@pytest.mark.parametrize("radius, expected", [(3, 29), (5, 79), (20, 197)])
def test_regression_compute_visible_exact_counts_at_three_radii(radius, expected):
    level = make_level(CLUTTER_ROWS)
    result = fov.compute_visible(level, frozenset(), CLUTTER_ORIGIN, radius)
    assert len(result) == expected


def test_regression_compute_visible_is_symmetric_in_a_symmetric_room():
    """From the exact centre of a square room the visible set has the room's symmetry.

    Mirrored left-right, mirrored top-bottom and transposed — a drift that shaved one
    cell off any wall run would break at least one of the three.
    """
    level = make_level(SYMMETRIC_ROOM_ROWS)
    result = fov.compute_visible(level, frozenset(), SYMMETRIC_ROOM_ORIGIN, 20)
    n = level.width - 1
    assert {(n - x, y) for x, y in result} == set(result)
    assert {(x, n - y) for x, y in result} == set(result)
    assert {(y, x) for x, y in result} == set(result)
    assert len(result) == 81  # the whole 9x9 level, ring included


def test_regression_compute_visible_wall_ring_has_no_holes_from_every_cell():
    level = make_level(SYMMETRIC_ROOM_ROWS)
    ring = room_ring(level)
    for y in range(1, level.height - 1):
        for x in range(1, level.width - 1):
            result = fov.compute_visible(level, frozenset(), (x, y), 20)
            assert set(ring) <= result, picture(level, result, (x, y))


@pytest.mark.parametrize("seed", GENERATED_SEEDS)
def test_regression_compute_visible_exact_counts_over_generated_origins(seed):
    level = generate_level(seed)
    origins = generated_origins(level)
    counts = tuple(
        len(fov.compute_visible(level, frozenset(), origin, 20)) for origin in origins
    )
    assert counts == GENERATED_VISIBLE_COUNTS[seed], list(zip(origins, counts))


@pytest.mark.parametrize("seed", GENERATED_SEEDS)
def test_regression_compute_visible_always_contains_its_origin(seed):
    level = generate_level(seed)
    for origin in generated_origins(level, count=40):
        assert origin in fov.compute_visible(level, frozenset(), origin, 20)


def test_regression_opening_a_door_only_ever_adds_cells_on_a_generated_level():
    level = generate_level(1)
    doors = [
        (x, y)
        for y in range(level.height)
        for x in range(level.width)
        if level.grid[y][x] is Tile.DOOR
    ]
    assert doors, "the generator is expected to produce doors"
    origin = generated_origins(level)[0]
    shut = fov.compute_visible(level, frozenset(), origin, 20)
    for door in doors:
        opened = fov.compute_visible(level, frozenset({door}), origin, 20)
        assert shut <= opened


# ======================================================================================
# `has_line_of_sight` — surface (CONTRACT-v5 §14 v5)
# ======================================================================================


def test_has_line_of_sight_signature_matches_the_contract():
    sig = inspect.signature(fov.has_line_of_sight)
    assert list(sig.parameters) == ["level", "open_doors", "observer", "target"]
    for name in sig.parameters:
        assert sig.parameters[name].default is inspect.Parameter.empty


def test_has_line_of_sight_takes_no_radius_or_mode_parameter():
    """No radius, no `symmetric=`, no "fast mode" — extra surface is a defect."""
    sig = inspect.signature(fov.has_line_of_sight)
    assert "radius" not in sig.parameters
    assert "symmetric" not in sig.parameters
    for parameter in sig.parameters.values():
        assert parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD


def test_has_line_of_sight_returns_a_real_bool():
    level = open_level(9, 9, (1, 1))
    result = fov.has_line_of_sight(level, frozenset(), (1, 1), (7, 7))
    assert result is True
    assert isinstance(result, bool)


def test_fov_module_contains_no_float_literal():
    """CONTRACT-v5 §14 v5: the doubled-integer scheme, no floating point anywhere."""
    tree = ast.parse(_fov_source())
    floats = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]
    assert floats == []


def test_fov_public_functions_are_exactly_two():
    public = {
        node.name
        for node in ast.parse(_fov_source()).body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }
    assert public == {"compute_visible", "has_line_of_sight"}


# ======================================================================================
# `has_line_of_sight` — semantics
# ======================================================================================


def test_a_cell_always_sees_itself_on_floor_wall_and_closed_door():
    level = make_level(TWO_ROOMS_ROWS)
    doors = frozenset()
    for y in range(level.height):
        for x in range(level.width):
            assert fov.has_line_of_sight(level, doors, (x, y), (x, y)), (x, y)
    assert level.grid[3][6] is Tile.DOOR  # closed: not in `doors`
    assert fov.has_line_of_sight(level, doors, DOOR_CELL, DOOR_CELL)
    assert level.grid[0][0] is Tile.WALL
    assert fov.has_line_of_sight(level, doors, (0, 0), (0, 0))


def test_two_cells_in_the_same_open_room_see_each_other():
    level = make_level(ROOM_ROWS)
    doors = frozenset()
    floor = [(x, y) for y in range(1, 5) for x in range(1, 8)]
    for a in floor:
        for b in floor:
            assert fov.has_line_of_sight(level, doors, a, b), (a, b)


def test_two_cells_separated_by_a_wall_do_not_see_each_other():
    level = make_level(TWO_ROOMS_ROWS)
    doors = frozenset()
    for y in (1, 2, 4, 5):  # every row except the door's
        assert not fov.has_line_of_sight(level, doors, (3, 3), (9, y)), y
        assert not fov.has_line_of_sight(level, doors, (9, y), (3, 3)), y


def test_a_closed_door_blocks_and_the_same_door_open_does_not():
    """Identical coordinates; the only thing that varies is `open_doors`."""
    level = make_level(TWO_ROOMS_ROWS)
    observer = (3, 3)
    target = (9, 3)
    assert not fov.has_line_of_sight(level, frozenset(), observer, target)
    assert fov.has_line_of_sight(level, frozenset({DOOR_CELL}), observer, target)
    # and the door's own cell is visible either way — you see its face
    assert fov.has_line_of_sight(level, frozenset(), observer, DOOR_CELL)
    assert fov.has_line_of_sight(level, frozenset({DOOR_CELL}), observer, DOOR_CELL)


def test_an_unrelated_open_door_entry_changes_nothing():
    level = make_level(TWO_ROOMS_ROWS)
    observer, target = (3, 3), (9, 3)
    assert not fov.has_line_of_sight(level, frozenset({(0, 0), (60, 60)}), observer, target)


def test_sight_is_not_limited_by_distance():
    """No radius parameter: a clear corridor is visible end to end, however long."""
    rows = [
        "#" * 34,
        "#" + "." * 32 + "#",
        "#" * 34,
    ]
    level = make_level(rows)
    doors = frozenset()
    assert fov.has_line_of_sight(level, doors, (1, 1), (32, 1))
    assert fov.has_line_of_sight(level, doors, (32, 1), (1, 1))
    # and further than DEFAULT_RADIUS in an open room, too
    wide = open_level(60, 3, (0, 1))
    assert fov.has_line_of_sight(wide, doors, (0, 1), (59, 1))
    assert 59 > fov.DEFAULT_RADIUS


def test_a_pillar_casts_a_shadow_for_the_point_to_point_test_too():
    level = make_level(PILLAR_ROWS)
    doors = frozenset()
    assert not fov.has_line_of_sight(level, doors, (1, 3), (9, 3))
    assert fov.has_line_of_sight(level, doors, (1, 3), PILLAR_CELL)  # its face


# ======================================================================================
# Agreement with `compute_visible` — the criterion that proves the geometry was reused
# ======================================================================================


def assert_agrees_everywhere(
    level: Level, doors: frozenset[tuple[int, int]], origin: tuple[int, int], radius: int
) -> None:
    visible = fov.compute_visible(level, doors, origin, radius)
    ox, oy = origin
    for y in range(level.height):
        for x in range(level.width):
            if (x - ox) ** 2 + (y - oy) ** 2 > radius * radius:
                continue
            assert fov.has_line_of_sight(level, doors, origin, (x, y)) == (
                (x, y) in visible
            ), (origin, (x, y))


@pytest.mark.parametrize("seed", GENERATED_SEEDS)
def test_agreement_with_compute_visible_on_a_generated_level(seed):
    """For every cell within radius 20 of several origins, the two must agree.

    They can only agree if the same observer-at-cell-centre geometry is in use and the
    bounding box is big enough; a box one cell too tight shows up here first.
    """
    level = generate_level(seed)
    doors = frozenset()
    for origin in generated_origins(level, count=6):
        assert_agrees_everywhere(level, doors, origin, 20)


def test_agreement_with_compute_visible_from_every_cell_of_a_cluttered_level():
    level = make_level(CLUTTER_ROWS)
    doors = frozenset()
    for y in range(level.height):
        for x in range(level.width):
            assert_agrees_everywhere(level, doors, (x, y), 50)


@pytest.mark.parametrize("doors", [frozenset(), frozenset({DOOR_CELL})])
def test_agreement_with_compute_visible_with_the_door_shut_and_open(doors):
    level = make_level(TWO_ROOMS_ROWS)
    for y in range(level.height):
        for x in range(level.width):
            assert_agrees_everywhere(level, doors, (x, y), 50)


def test_agreement_with_compute_visible_around_a_diagonal_wall_join():
    rows = [
        ".........",
        ".#.......",
        "..#......",
        "...#.....",
        "....#....",
        ".....#...",
        "......#..",
        ".......#.",
        ".........",
    ]
    level = make_level(rows)
    for y in range(level.height):
        for x in range(level.width):
            assert_agrees_everywhere(level, frozenset(), (x, y), 50)


# ======================================================================================
# The bounding box, and the one-cell margin (CONTRACT-v5 §14 v5)
# ======================================================================================

#: A 7x7 open level with one pillar. From (0, 0) the cell (6, 2) is visible, but the
#: *only* sample point of it that has a clear line is its bottom-left corner (6, 3) —
#: which lies outside the rectangle spanned by the two cell centres, (0.5, 0.5) to
#: (6.5, 2.5). The clear line bulges below that rectangle.
BULGE_ROWS = [
    ".......",
    "....#..",
    ".......",
    ".......",
    ".......",
    ".......",
    ".......",
]
BULGE_OBSERVER = (0, 0)
BULGE_TARGET = (6, 2)


def test_the_clear_line_bulges_outside_the_centre_to_centre_rectangle():
    """The winning line leaves the rectangle spanned by the two cell centres.

    A bounding box drawn around the two *points* rather than the two *cells* would miss
    the sample point this line ends on, so this pair pins the box's extent.
    """
    level = make_level(BULGE_ROWS)
    doors = frozenset()
    ox, oy = BULGE_OBSERVER
    tx, ty = BULGE_TARGET

    # Opacity over the whole level, so nothing here depends on the module's own box.
    opaque = {
        (x, y): not is_transparent(level, doors, x, y)
        for y in range(level.height)
        for x in range(level.width)
    }
    clear = [
        (sample_dx, sample_dy)
        for sample_dx, sample_dy in fov._SAMPLE_OFFSETS
        if fov._segment_is_clear(
            2 * ox + 1, 2 * oy + 1, 2 * tx + sample_dx, 2 * ty + sample_dy,
            ox, oy, tx, ty, opaque,
        )
    ]
    # (0, 2) is the bottom-left corner: doubled y = 2*2 + 2 = 6, i.e. y = 3, strictly
    # below both cell centres, so the winning segment leaves their rectangle.
    assert clear == [(0, 2)]
    sample_y = 2 * ty + clear[0][1]
    assert sample_y > max(2 * oy + 1, 2 * ty + 1)

    assert fov.has_line_of_sight(level, doors, BULGE_OBSERVER, BULGE_TARGET)
    assert BULGE_TARGET in fov.compute_visible(level, doors, BULGE_OBSERVER, 20)


def test_the_bulge_pair_agrees_with_compute_visible_everywhere_on_that_level():
    level = make_level(BULGE_ROWS)
    for y in range(level.height):
        for x in range(level.width):
            assert_agrees_everywhere(level, frozenset(), (x, y), 50)


def consulted_cells(monkeypatch, level, doors, observer, target):
    """Every cell `has_line_of_sight` asks `world.is_transparent` about."""
    seen: set[tuple[int, int]] = set()
    real = fov.is_transparent

    def spy(lvl, open_doors, x, y):
        seen.add((x, y))
        return real(lvl, open_doors, x, y)

    monkeypatch.setattr(fov, "is_transparent", spy)
    answer = fov.has_line_of_sight(level, doors, observer, target)
    monkeypatch.undo()
    return answer, seen


def test_opacity_is_snapshotted_over_the_bounding_box_grown_by_one(monkeypatch):
    """The box is the two cells' bounding box **grown by one cell** — not tighter.

    The margin is the contract's (§14 v5): it is what keeps the diagonal-corner rule's
    flanking lookups inside the snapshot instead of letting them fall off the edge and
    default to opaque. Shrink the box to the tight rectangle and this fails.
    """
    level = open_level(40, 20, (2, 2))
    _, seen = consulted_cells(monkeypatch, level, frozenset(), (10, 8), (14, 11))
    assert seen == {(x, y) for x in range(9, 16) for y in range(7, 13)}


def test_the_margin_is_a_guarantee_with_slack_not_a_knife_edge():
    """Measured: `_segment_is_clear` never reads outside the pair's *tight* box.

    Recorded because it is the one thing a reviewer cannot see by reading the code: the
    segment stays inside the two cells' bounding rectangle, and so do the diagonal rule's
    two flanking cells, because both of their coordinates are coordinates of cells the
    segment does enter. The contract's one-cell margin is therefore a guarantee with a
    cell to spare rather than a bound that is exactly tight — which is the safe direction
    to be wrong in, and is why agreement with `compute_visible` is exact.
    """
    level = make_level(CLUTTER_ROWS)
    doors = frozenset()
    opaque_everywhere = {
        (x, y): not is_transparent(level, doors, x, y)
        for y in range(level.height)
        for x in range(level.width)
    }

    class Recording(dict):
        def __init__(self, source):
            super().__init__(source)
            self.reads: set[tuple[int, int]] = set()

        def get(self, key, default=None):
            self.reads.add(key)
            return super().get(key, default)

    cells = [(x, y) for y in range(level.height) for x in range(level.width)]
    for observer in cells:
        for target in cells:
            if observer == target:
                continue
            ox, oy = observer
            tx, ty = target
            recorder = Recording(opaque_everywhere)
            for sample_dx, sample_dy in fov._SAMPLE_OFFSETS:
                fov._segment_is_clear(
                    2 * ox + 1, 2 * oy + 1, 2 * tx + sample_dx, 2 * ty + sample_dy,
                    ox, oy, tx, ty, recorder,
                )
            for rx, ry in recorder.reads:
                assert min(ox, tx) <= rx <= max(ox, tx), (observer, target, (rx, ry))
                assert min(oy, ty) <= ry <= max(oy, ty), (observer, target, (rx, ry))


def test_the_snapshot_box_is_clipped_to_the_level(monkeypatch):
    level = open_level(40, 20, (2, 2))
    _, seen = consulted_cells(monkeypatch, level, frozenset(), (0, 0), (2, 1))
    assert seen == {(x, y) for x in range(0, 4) for y in range(0, 3)}
    _, seen = consulted_cells(monkeypatch, level, frozenset(), (39, 19), (37, 18))
    assert seen == {(x, y) for x in range(36, 40) for y in range(17, 20)}


def test_the_snapshot_is_never_the_whole_radius_disc(monkeypatch):
    """The entire optimisation: opacity is read for the pair, not for a radius-20 disc."""
    level = open_level(80, 22, (2, 2))
    _, seen = consulted_cells(monkeypatch, level, frozenset(), (10, 10), (13, 11))
    assert len(seen) == 6 * 4  # x 9..14, y 9..12 — the pair's box, grown by one
    disc = fov.compute_visible(level, frozenset(), (10, 10), 20)
    assert len(seen) < len(disc) / 10


def test_the_snapshot_grows_with_the_pair_and_no_further(monkeypatch):
    level = open_level(80, 22, (2, 2))
    for target in [(10, 10), (11, 10), (20, 10), (40, 21), (79, 0)]:
        _, seen = consulted_cells(monkeypatch, level, frozenset(), (10, 10), target)
        x_lo = max(0, min(10, target[0]) - 1)
        x_hi = min(79, max(10, target[0]) + 1)
        y_lo = max(0, min(10, target[1]) - 1)
        y_hi = min(21, max(10, target[1]) + 1)
        expected = {(x, y) for x in range(x_lo, x_hi + 1) for y in range(y_lo, y_hi + 1)}
        assert seen <= expected, target
        if target != (10, 10):  # the reflexive case short-circuits before the snapshot
            assert seen == expected, target


# ======================================================================================
# Asymmetry — a defined behaviour, pinned so a future "symmetry fix" is loud
# ======================================================================================

#: The same 7x7 level with its single wall at (4, 1): `(0, 0)` sees `(6, 2)` — the only
#: clear line ends on that cell's bottom-left corner — while `(6, 2)` does **not** see
#: `(0, 0)`. The eye is a cell *centre* and the target is eight *boundary* samples, so
#: the relation is directional by construction.
ASYMMETRIC_OBSERVER = (0, 0)
ASYMMETRIC_TARGET = (6, 2)


def test_line_of_sight_is_not_symmetric_on_a_hand_built_level():
    level = make_level(BULGE_ROWS)
    doors = frozenset()
    assert fov.has_line_of_sight(level, doors, ASYMMETRIC_OBSERVER, ASYMMETRIC_TARGET)
    assert not fov.has_line_of_sight(
        level, doors, ASYMMETRIC_TARGET, ASYMMETRIC_OBSERVER
    )


def test_the_asymmetry_is_compute_visible_s_own():
    """Not an artifact of the new function: `compute_visible` disagrees the same way."""
    level = make_level(BULGE_ROWS)
    doors = frozenset()
    assert ASYMMETRIC_TARGET in fov.compute_visible(level, doors, ASYMMETRIC_OBSERVER, 20)
    assert ASYMMETRIC_OBSERVER not in fov.compute_visible(
        level, doors, ASYMMETRIC_TARGET, 20
    )


#: Found on `generate_level(1)`: (54, 16) is a floor cell, (57, 9) a wall. The floor cell
#: sees the wall's face; the wall cell does not see the floor cell.
GENERATED_ASYMMETRIC_PAIR = ((54, 16), (57, 9))


def test_line_of_sight_is_not_symmetric_on_a_generated_level():
    level = generate_level(1)
    doors = frozenset()
    a, b = GENERATED_ASYMMETRIC_PAIR
    assert fov.has_line_of_sight(level, doors, a, b)
    assert not fov.has_line_of_sight(level, doors, b, a)
    assert fov.has_line_of_sight(level, doors, a, b) != fov.has_line_of_sight(
        level, doors, b, a
    )


def test_the_argument_order_is_observer_then_target():
    """Swapping the arguments is a behaviour change; this is what would catch it."""
    level = make_level(BULGE_ROWS)
    doors = frozenset()
    assert fov.has_line_of_sight(
        level, doors, observer=ASYMMETRIC_OBSERVER, target=ASYMMETRIC_TARGET
    )
    assert not fov.has_line_of_sight(
        level, doors, observer=ASYMMETRIC_TARGET, target=ASYMMETRIC_OBSERVER
    )


def test_asymmetric_pairs_are_rare_but_real_across_a_generated_level():
    """~0.3% of pairs disagree (RESEARCH-v5 §8) — rare enough to hide, real enough to pin."""
    level = generate_level(1)
    doors = frozenset()
    cells = [(x, y) for y in range(level.height) for x in range(level.width)]
    rng = random.Random(1)
    pairs = [(rng.choice(cells), rng.choice(cells)) for _ in range(2000)]
    disagreements = sum(
        1
        for a, b in pairs
        if a != b
        and fov.has_line_of_sight(level, doors, a, b)
        != fov.has_line_of_sight(level, doors, b, a)
    )
    assert disagreements > 0
    assert disagreements < len(pairs) // 20  # a few per thousand, not a coin flip


# ======================================================================================
# Out of bounds, purity
# ======================================================================================


@pytest.mark.parametrize(
    "observer, target",
    [
        ((-1, 0), (1, 1)),
        ((1, 1), (-1, 0)),
        ((0, -1), (1, 1)),
        ((1, 1), (0, -1)),
        ((-5, -5), (-5, -5)),
        ((9, 1), (1, 1)),
        ((1, 1), (9, 1)),
        ((1, 1), (1, 9)),
        ((1000, 1000), (1, 1)),
        ((1, 1), (1000, 1000)),
        ((-3, 4), (12, -7)),
    ],
)
def test_out_of_bounds_returns_false_and_never_raises(observer, target):
    level = make_level(ROOM_ROWS)
    assert fov.has_line_of_sight(level, frozenset(), observer, target) is False


def test_out_of_bounds_beats_the_reflexive_rule():
    """`has_line_of_sight(a, a)` is True only for a cell that exists."""
    level = make_level(ROOM_ROWS)
    assert fov.has_line_of_sight(level, frozenset(), (4, 3), (4, 3)) is True
    assert fov.has_line_of_sight(level, frozenset(), (40, 30), (40, 30)) is False


def test_every_cell_of_a_one_by_one_level_is_handled():
    level = make_level(["."])
    assert fov.has_line_of_sight(level, frozenset(), (0, 0), (0, 0)) is True
    assert fov.has_line_of_sight(level, frozenset(), (0, 0), (0, 1)) is False


def test_has_line_of_sight_does_not_mutate_its_arguments():
    level = make_level(TWO_ROOMS_ROWS)
    doors = frozenset({DOOR_CELL})
    before_level = copy.deepcopy(level)
    before_doors = set(doors)
    fov.has_line_of_sight(level, doors, (3, 3), (9, 3))
    assert level == before_level
    assert set(doors) == before_doors


def test_has_line_of_sight_is_deterministic_and_caches_nothing():
    level = make_level(TWO_ROOMS_ROWS)
    observer, target = (3, 3), (9, 3)
    shut = fov.has_line_of_sight(level, frozenset(), observer, target)
    opened = fov.has_line_of_sight(level, frozenset({DOOR_CELL}), observer, target)
    shut_again = fov.has_line_of_sight(level, frozenset(), observer, target)
    opened_again = fov.has_line_of_sight(level, frozenset({DOOR_CELL}), observer, target)
    assert (shut, opened) == (False, True)
    assert (shut_again, opened_again) == (False, True)


def test_has_line_of_sight_does_not_depend_on_open_doors_iteration_order():
    level = make_level(TWO_ROOMS_ROWS)
    entries = [DOOR_CELL, (1, 1), (11, 5), (2, 4)]
    answers = set()
    for _ in range(8):
        random.shuffle(entries)
        answers.add(fov.has_line_of_sight(level, frozenset(entries), (3, 3), (9, 3)))
    assert answers == {True}


def test_compute_visible_still_works_after_has_line_of_sight_calls():
    """No shared state between the two: interleaving them changes nothing."""
    level = make_level(CLUTTER_ROWS)
    doors = frozenset()
    expected = fov.compute_visible(level, doors, CLUTTER_ORIGIN, 20)
    for target in [(1, 1), (19, 9), (10, 1), (4, 8)]:
        fov.has_line_of_sight(level, doors, CLUTTER_ORIGIN, target)
    assert fov.compute_visible(level, doors, CLUTTER_ORIGIN, 20) == expected


# ======================================================================================
# Performance — the entire reason this function exists (CONTRACT-v5 §14 v5)
# ======================================================================================


def random_floor_pairs(level: Level, count: int, seed: int = 2024):
    floors = [
        (x, y)
        for y in range(level.height)
        for x in range(level.width)
        if is_passable(level, frozenset(), x, y)
    ]
    rng = random.Random(seed)
    return [(rng.choice(floors), rng.choice(floors)) for _ in range(count)]


def test_thirty_checks_on_an_eighty_by_twenty_two_level_are_fast():
    """Reference measurement: 5.0 ms for 30. The bound is 50 ms — a 10x margin."""
    level = generate_level(1)
    doors = frozenset()
    pairs = random_floor_pairs(level, 30)
    best = None
    for _ in range(3):
        start = time.perf_counter()
        for observer, target in pairs:
            fov.has_line_of_sight(level, doors, observer, target)
        elapsed = time.perf_counter() - start
        best = elapsed if best is None else min(best, elapsed)
    assert best < 0.050, f"30 checks took {best * 1000:.1f} ms"


def test_thirty_checks_are_substantially_cheaper_than_thirty_compute_visible_calls():
    """Measured 44x on the reference build; asserted at 5x so it cannot flake."""
    level = generate_level(1)
    doors = frozenset()
    pairs = random_floor_pairs(level, 30)

    start = time.perf_counter()
    for observer, target in pairs:
        fov.has_line_of_sight(level, doors, observer, target)
    point_to_point = time.perf_counter() - start

    start = time.perf_counter()
    for observer, target in pairs:
        target in fov.compute_visible(level, doors, observer, fov.DEFAULT_RADIUS)
    whole_disc = time.perf_counter() - start

    assert point_to_point * 5 < whole_disc, (
        f"point-to-point {point_to_point * 1000:.1f} ms vs "
        f"compute_visible {whole_disc * 1000:.1f} ms"
    )
