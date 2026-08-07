"""Tests for :mod:`roguelike.generator` — CONTRACT §3, guarantees G1-G12.

The single most important test here is :func:`test_g8_flood_fill_reaches_every_walkable_cell`:
a 4-directional flood fill from ``player_start`` must reach every walkable tile in the grid.
"""

from __future__ import annotations

import ast
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from roguelike.generator import (
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    MAX_ROOM_SIZE,
    MIN_ROOM_SIZE,
    generate_level,
)
from roguelike.level import Level, Room
from roguelike.tiles import Tile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GENERATOR_SOURCE = PROJECT_ROOT / "roguelike" / "generator.py"

#: A generous spread of seeds: negative, zero, small, large.
SEEDS = [-9999, -42, -1, 0, 1, 2, 3, 7, 13, 42, 99, 123, 256, 777, 1024, 1234,
         4096, 31337, 65535, 999983, 2**31, 2**63 + 17]

#: Sizes exercised by the whole-guarantee sweep: default, non-default, and minimal.
SIZES = [
    (DEFAULT_WIDTH, DEFAULT_HEIGHT),
    (40, 15),
    (30, 30),
    (MIN_ROOM_SIZE + 2, MIN_ROOM_SIZE + 2),
    (MIN_ROOM_SIZE + 2, DEFAULT_HEIGHT),
    (DEFAULT_WIDTH, MIN_ROOM_SIZE + 2),
]

_NEIGHBOURS = ((0, -1), (0, 1), (-1, 0), (1, 0))


# --------------------------------------------------------------------------------------
# Helpers — deliberately independent re-implementations, not imports of private helpers
# --------------------------------------------------------------------------------------


def flood_fill(level: Level, origin: tuple[int, int]) -> set[tuple[int, int]]:
    """Every walkable cell reachable from ``origin`` in 4 directions."""
    assert level.is_walkable(*origin)
    seen = {origin}
    stack = [origin]
    while stack:
        x, y = stack.pop()
        for dx, dy in _NEIGHBOURS:
            step = (x + dx, y + dy)
            if step not in seen and level.is_walkable(*step):
                seen.add(step)
                stack.append(step)
    return seen


def walkable_cells(level: Level) -> set[tuple[int, int]]:
    return {
        (x, y)
        for y in range(level.height)
        for x in range(level.width)
        if level.is_walkable(x, y)
    }


def tile_is_wall(level: Level, x: int, y: int) -> bool:
    """``Tile.WALL`` at ``(x, y)``, counting out of bounds as wall.

    CONTRACT-v2 §3: "Out-of-bounds neighbours count as ``Tile.WALL`` for G9b/G9c."
    """
    if not level.in_bounds(x, y):
        return True
    return level.tile_at(x, y) is Tile.WALL


def doors_in(level: Level) -> list[tuple[int, int]]:
    """Every ``Tile.DOOR`` cell, in row-major order."""
    return [
        (x, y)
        for y in range(level.height)
        for x in range(level.width)
        if level.tile_at(x, y) is Tile.DOOR
    ]


def assert_door_is_well_formed(level: Level, x: int, y: int) -> None:
    """Assert G9b and G9c for the door at ``(x, y)`` (CONTRACT-v2 §3).

    G9b — walls on both sides along one axis; G9c — passage on both sides along the other.
    Asserting both together also rules out the degenerate case where *every* neighbour is
    a wall, which would satisfy G9b twice over and G9c never.
    """
    where = f"seed {level.seed} door ({x}, {y})"
    walls_above_below = tile_is_wall(level, x, y - 1) and tile_is_wall(level, x, y + 1)
    walls_left_right = tile_is_wall(level, x - 1, y) and tile_is_wall(level, x + 1, y)

    assert walls_above_below or walls_left_right, f"G9b: {where} is not in a wall run"
    if walls_above_below:  # G9c — the east-west axis must be passage
        assert not tile_is_wall(level, x - 1, y), f"G9c: {where} walled to the west"
        assert not tile_is_wall(level, x + 1, y), f"G9c: {where} walled to the east"
    if walls_left_right:  # G9c — the north-south axis must be passage
        assert not tile_is_wall(level, x, y - 1), f"G9c: {where} walled to the north"
        assert not tile_is_wall(level, x, y + 1), f"G9c: {where} walled to the south"


def grid_digest(level: Level) -> str:
    """A stable hash of the grid that does not depend on ``PYTHONHASHSEED``."""
    payload = "\n".join("".join(str(int(t)) for t in row) for row in level.grid)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


# --------------------------------------------------------------------------------------
# Basic shape and the acceptance smoke test
# --------------------------------------------------------------------------------------


def test_generate_level_1234_basic_shape():
    level = generate_level(1234)
    assert isinstance(level, Level)
    assert level.width == 80
    assert level.height == 22
    assert level.seed == 1234
    assert isinstance(level.grid, tuple)
    assert all(isinstance(row, tuple) for row in level.grid)
    assert isinstance(level.rooms, tuple)
    assert all(isinstance(room, Room) for room in level.rooms)


def test_defaults_match_the_contract():
    assert DEFAULT_WIDTH == 80
    assert DEFAULT_HEIGHT == 22
    assert MIN_ROOM_SIZE == 4
    assert MAX_ROOM_SIZE == 12


def test_grid_contains_only_the_three_known_tiles():
    level = generate_level(5)
    kinds = {tile for row in level.grid for tile in row}
    assert kinds <= {Tile.WALL, Tile.FLOOR, Tile.DOOR}


# --------------------------------------------------------------------------------------
# G1 / G2 — determinism
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [0, 1, 1234, -7, 999983])
def test_g1_same_arguments_produce_identical_levels(seed):
    a = generate_level(seed)
    b = generate_level(seed)
    assert a.grid == b.grid
    assert a.rooms == b.rooms
    assert a.player_start == b.player_start
    assert a == b  # frozen dataclass equality: every field, including rooms order


def test_g1_determinism_holds_with_non_default_arguments():
    a = generate_level(31337, 40, 15, 5)
    b = generate_level(31337, 40, 15, 5)
    assert a == b


def test_g1_interleaved_calls_do_not_contaminate_each_other():
    """A shared global RNG would make output depend on call ordering."""
    expected = {seed: generate_level(seed) for seed in (1, 2, 3)}
    generate_level(4)
    generate_level(5, 40, 15)
    for seed, level in expected.items():
        assert generate_level(seed) == level


@pytest.mark.parametrize("seed", [-1, 0, 4242])
def test_g1_determinism_across_separate_processes(seed):
    """Hash the grid here, then recompute it in a fresh interpreter and compare.

    Both subprocesses run with a different ``PYTHONHASHSEED``, so any accidental
    dependence on string/object hashing would show up as a mismatch.
    """
    expected = grid_digest(generate_level(seed))
    script = (
        "import hashlib\n"
        "from roguelike.generator import generate_level\n"
        f"level = generate_level({seed})\n"
        "payload = '\\n'.join(''.join(str(int(t)) for t in row) for row in level.grid)\n"
        "print(hashlib.sha256(payload.encode('ascii')).hexdigest())\n"
    )
    digests = []
    for hash_seed in ("0", "1", "12345"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = hash_seed
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        digests.append(result.stdout.strip())
    assert digests == [expected, expected, expected]


def test_g2_source_uses_only_a_local_random_instance():
    source = GENERATOR_SOURCE.read_text(encoding="utf-8")
    assert "random.Random(" in source
    for forbidden in (
        "random.randint(",
        "random.choice(",
        "random.shuffle(",
        "random.random(",
        "random.randrange(",
        "random.sample(",
        "random.seed(",
    ):
        assert forbidden not in source, f"module-level RNG call {forbidden!r} in source"


def test_g2_source_avoids_nondeterministic_influences():
    source = GENERATOR_SOURCE.read_text(encoding="utf-8")
    for forbidden in ("import time", "os.urandom", "import uuid"):
        assert forbidden not in source, f"nondeterministic influence {forbidden!r}"
    # ``id(`` and ``hash(`` are substrings of ordinary identifiers (``blank_grid(``), so
    # they need a real parse rather than a text search.
    tree = ast.parse(source)
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "id" not in called
    assert "hash" not in called


def test_module_imports_only_what_the_import_graph_allows():
    """CONTRACT §10: generator imports tiles and level, and no third-party module."""
    tree = ast.parse(GENERATOR_SOURCE.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    assert modules == {"__future__", "random", "roguelike.tiles", "roguelike.level"}
    assert not any(m == "curses" or m.startswith("curses.") for m in modules)


def test_different_seeds_produce_different_grids():
    digests = {grid_digest(generate_level(seed)) for seed in (1, 2, 3, 4, 5, 6, 7)}
    assert len(digests) > 1, "a sample of seeds produced uniformly identical grids"


# --------------------------------------------------------------------------------------
# G3 — border ring is all WALL
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS[:8])
@pytest.mark.parametrize("size", SIZES)
def test_g3_every_border_cell_is_wall(seed, size):
    width, height = size
    level = generate_level(seed, width, height)
    for x in range(width):
        assert level.tile_at(x, 0) is Tile.WALL
        assert level.tile_at(x, height - 1) is Tile.WALL
    for y in range(height):
        assert level.tile_at(0, y) is Tile.WALL
        assert level.tile_at(width - 1, y) is Tile.WALL


@pytest.mark.parametrize("seed", SEEDS[:8])
def test_no_floor_or_door_cell_lies_on_the_border(seed):
    level = generate_level(seed)
    for x, y in walkable_cells(level):
        assert 1 <= x <= level.width - 2
        assert 1 <= y <= level.height - 2


# --------------------------------------------------------------------------------------
# G4 / G5 / G6 / G7 — rooms
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_g4_at_least_one_room(seed):
    assert len(generate_level(seed).rooms) >= 1


@pytest.mark.parametrize("size", SIZES)
def test_g4_holds_at_every_size(size):
    for seed in range(10):
        assert len(generate_level(seed, *size).rooms) >= 1


@pytest.mark.parametrize("seed", SEEDS)
def test_g5_rooms_are_pairwise_disjoint_at_default_margin(seed):
    rooms = generate_level(seed).rooms
    for i, room in enumerate(rooms):
        for other in rooms[i + 1 :]:
            assert not room.intersects(other)
            assert not other.intersects(room)


@pytest.mark.parametrize("seed", SEEDS)
def test_g6_rooms_lie_in_bounds_with_a_wall_margin(seed):
    level = generate_level(seed)
    for room in level.rooms:
        assert 1 <= room.x
        assert 1 <= room.y
        assert room.x2 <= level.width - 2
        assert room.y2 <= level.height - 2


@pytest.mark.parametrize("seed", SEEDS)
def test_room_sizes_respect_the_configured_bounds(seed):
    level = generate_level(seed)
    for room in level.rooms:
        assert MIN_ROOM_SIZE <= room.width <= MAX_ROOM_SIZE
        assert MIN_ROOM_SIZE <= room.height <= MAX_ROOM_SIZE


@pytest.mark.parametrize("seed", SEEDS)
def test_g7_every_room_floor_cell_is_floor(seed):
    level = generate_level(seed)
    for room in level.rooms:
        for y in range(room.y, room.y2 + 1):
            for x in range(room.x, room.x2 + 1):
                assert level.tile_at(x, y) is Tile.FLOOR


@pytest.mark.parametrize("max_rooms", [1, 2, 3, 5, 12, 40])
def test_max_rooms_is_a_ceiling(max_rooms):
    for seed in range(6):
        rooms = generate_level(seed, max_rooms=max_rooms).rooms
        assert 1 <= len(rooms) <= max_rooms


# --------------------------------------------------------------------------------------
# G8 — full connectivity. The most important test in this suite.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(30))
def test_g8_flood_fill_reaches_every_walkable_cell(seed):
    level = generate_level(seed)
    reached = flood_fill(level, level.player_start)
    every = walkable_cells(level)
    assert reached == every
    assert len(reached) == len(every)


@pytest.mark.parametrize("seed", SEEDS)
def test_g8_holds_for_the_wider_seed_spread(seed):
    level = generate_level(seed)
    assert flood_fill(level, level.player_start) == walkable_cells(level)


@pytest.mark.parametrize("size", SIZES)
def test_g8_holds_at_every_size(size):
    for seed in range(12):
        level = generate_level(seed, *size)
        assert flood_fill(level, level.player_start) == walkable_cells(level)


@pytest.mark.parametrize("max_rooms", [1, 2, 3, 7, 12, 25])
def test_g8_holds_for_every_room_ceiling(max_rooms):
    for seed in range(8):
        level = generate_level(seed, max_rooms=max_rooms)
        assert flood_fill(level, level.player_start) == walkable_cells(level)


@pytest.mark.parametrize("size", [(DEFAULT_WIDTH, DEFAULT_HEIGHT), (40, 15), (60, 40)])
def test_g8_survives_the_reroute_over_30_seeds_at_three_sizes(size):
    """Rerouting changes every corridor's shape, so connectivity is the guard that must
    not regress. The flood fill here is implemented independently of the generator's own.
    """
    for seed in range(30):
        level = generate_level(seed, *size)
        reached = flood_fill(level, level.player_start)
        assert reached == walkable_cells(level), f"seed {seed} at {size}"
        for room in level.rooms:
            for y in range(room.y, room.y2 + 1):
                for x in range(room.x, room.x2 + 1):
                    assert (x, y) in reached, f"seed {seed}: ({x}, {y}) unreachable"


@pytest.mark.parametrize("seed", range(20))
def test_g8_every_room_centre_is_reachable(seed):
    level = generate_level(seed)
    reached = flood_fill(level, level.player_start)
    for room in level.rooms:
        assert room.center in reached


# --------------------------------------------------------------------------------------
# G9 — doors
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_g9_doors_lie_on_a_room_perimeter_and_never_on_a_room_floor(seed):
    """G9a, plus the G9b/G9c tightening of CONTRACT-v2 §3 — the v1 rule that a door merely
    lie on *some* perimeter is no longer sufficient on its own."""
    level = generate_level(seed)
    for y in range(level.height):
        for x in range(level.width):
            if level.tile_at(x, y) is not Tile.DOOR:
                continue
            assert any(room.on_perimeter(x, y) for room in level.rooms)
            assert not any(room.contains(x, y) for room in level.rooms)
            assert_door_is_well_formed(level, x, y)


@pytest.mark.parametrize("seed", SEEDS[:8])
def test_g9_doors_are_walkable(seed):
    level = generate_level(seed)
    for y in range(level.height):
        for x in range(level.width):
            if level.tile_at(x, y) is Tile.DOOR:
                assert level.is_walkable(x, y)


def test_g9_a_multi_room_level_actually_grows_doors():
    """Zero doors is legal for a single-room level, but a corridor-joined level should
    show the door mechanism is wired up at all."""
    total = 0
    for seed in range(10):
        level = generate_level(seed)
        if len(level.rooms) < 2:
            continue
        total += sum(row.count(Tile.DOOR) for row in level.grid)
    assert total > 0


def test_single_room_level_needs_no_doors():
    level = generate_level(3, MIN_ROOM_SIZE + 2, MIN_ROOM_SIZE + 2)
    assert len(level.rooms) == 1
    assert sum(row.count(Tile.DOOR) for row in level.grid) == 0


# --------------------------------------------------------------------------------------
# G9a-G9d and G4a — the tightened door constraint (CONTRACT-v2 §3, fix B "reroute")
#
# The v1 generator failed G9b/G9c on 13.1% of doors and put 2525 doors per 400 seeds next
# to another door, because centre-to-centre doglegs clipped room corners diagonally. These
# tests are the regression guard: the headline criterion is zero violations over 200 seeds.
# --------------------------------------------------------------------------------------

#: The acceptance criteria ask for at least 200 seeds on the door guarantees.
DOOR_SEEDS = range(200)


def test_g9b_and_g9c_hold_for_every_door_over_200_seeds():
    """The headline criterion. Zero doors adrift of a wall run, zero without passage."""
    checked = 0
    for seed in DOOR_SEEDS:
        level = generate_level(seed)
        for x, y in doors_in(level):
            assert_door_is_well_formed(level, x, y)
            checked += 1
    assert checked > 1000, f"only {checked} doors examined — the sweep proved nothing"


@pytest.mark.parametrize("size", SIZES)
def test_g9b_and_g9c_hold_at_every_size(size):
    for seed in range(40):
        level = generate_level(seed, *size)
        for x, y in doors_in(level):
            assert_door_is_well_formed(level, x, y)


@pytest.mark.parametrize("max_rooms", [1, 2, 3, 7, 12, 25])
def test_g9b_and_g9c_hold_for_every_room_ceiling(max_rooms):
    for seed in range(30):
        level = generate_level(seed, max_rooms=max_rooms)
        for x, y in doors_in(level):
            assert_door_is_well_formed(level, x, y)


def test_g9d_no_door_is_orthogonally_adjacent_to_another_over_200_seeds():
    for seed in DOOR_SEEDS:
        level = generate_level(seed)
        doors = set(doors_in(level))
        for x, y in doors:
            for dx, dy in _NEIGHBOURS:
                assert (x + dx, y + dy) not in doors, (
                    f"G9d: seed {seed} doors ({x}, {y}) and "
                    f"({x + dx}, {y + dy}) are adjacent"
                )


def test_g9a_every_door_is_on_a_perimeter_and_never_inside_a_room_over_200_seeds():
    for seed in DOOR_SEEDS:
        level = generate_level(seed)
        for x, y in doors_in(level):
            assert any(room.on_perimeter(x, y) for room in level.rooms), (
                f"G9a: seed {seed} door ({x}, {y}) is on no room perimeter"
            )
            assert not any(room.contains(x, y) for room in level.rooms), (
                f"G9a: seed {seed} door ({x}, {y}) sits inside a room floor"
            )


def test_g4a_every_room_has_a_door_over_200_seeds():
    """The failure mode that disqualified the rejected one-line fix: demoting a malformed
    door to FLOOR left 6.3% of rooms with no door at all, entered through a corner gap."""
    multi_room_levels = 0
    for seed in DOOR_SEEDS:
        level = generate_level(seed)
        doors = doors_in(level)
        if len(level.rooms) == 1:
            assert doors == [], f"G4a: seed {seed} single-room level grew doors"
            continue
        multi_room_levels += 1
        for room in level.rooms:
            assert any(room.on_perimeter(x, y) for x, y in doors), (
                f"G4a: seed {seed} {room} has no door on its perimeter"
            )
    assert multi_room_levels > 150, "the sample was dominated by single-room levels"


def test_g4a_every_room_has_a_door_at_40x15():
    for seed in DOOR_SEEDS:
        level = generate_level(seed, 40, 15)
        doors = doors_in(level)
        if len(level.rooms) == 1:
            assert doors == []
            continue
        for room in level.rooms:
            assert any(room.on_perimeter(x, y) for x, y in doors), (
                f"G4a: seed {seed} at 40x15, {room} has no door"
            )


@pytest.mark.parametrize("size", SIZES)
def test_a_single_room_level_has_no_doors_at_any_size(size):
    for seed in range(30):
        level = generate_level(seed, *size)
        if len(level.rooms) == 1:
            assert doors_in(level) == [], f"seed {seed} at {size}"


def test_every_way_into_a_room_is_a_door():
    """The property that makes G4a structural rather than lucky.

    A corridor is routed only through cells outside every room's wall ring, so no walkable
    cell may touch a room floor unless it is that room's own floor or a door. An unmarked
    corner gap — a walkable cell leaking into a room beside its wall — is exactly the
    defect that ruled out demoting bad doors to FLOOR.
    """
    for seed in range(60):
        level = generate_level(seed)
        for room in level.rooms:
            for y in range(room.y, room.y2 + 1):
                for x in range(room.x, room.x2 + 1):
                    for dx, dy in _NEIGHBOURS:
                        nx, ny = x + dx, y + dy
                        if not level.is_walkable(nx, ny):
                            continue
                        assert room.contains(nx, ny) or (
                            level.tile_at(nx, ny) is Tile.DOOR
                        ), (
                            f"seed {seed}: ({nx}, {ny}) leaks into {room} "
                            f"without a door"
                        )


@pytest.mark.parametrize(
    "seed, cell", [(0, (58, 16)), (1, (66, 6)), (2, (32, 14)), (3, (20, 18))]
)
def test_the_historical_corner_junction_doors_are_gone(seed, cell):
    """The four corner-junction doors recorded in RESEARCH-v2 §1.

    Rerouting changes the map for these seeds, so the cell may now be anything at all; the
    point is that whatever sits there is no longer a malformed door.
    """
    level = generate_level(seed)
    x, y = cell
    if level.tile_at(x, y) is Tile.DOOR:
        assert_door_is_well_formed(level, x, y)


def test_doors_are_never_on_the_border():
    for seed in range(60):
        level = generate_level(seed)
        for x, y in doors_in(level):
            assert 1 <= x <= level.width - 2
            assert 1 <= y <= level.height - 2


def test_rerouting_did_not_cost_the_map_its_doors():
    """Reroute must not quietly shrink the dungeon: a default level still has plenty of
    rooms, and every one of them past the first is reached through a door."""
    rooms = doors = 0
    for seed in DOOR_SEEDS:
        level = generate_level(seed)
        rooms += len(level.rooms)
        doors += len(doors_in(level))
    assert rooms / len(DOOR_SEEDS) > 8.0, f"only {rooms / len(DOOR_SEEDS):.1f} rooms/level"
    assert doors >= rooms - len(DOOR_SEEDS)


# --------------------------------------------------------------------------------------
# G10 / G11 / G12
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_g10_player_start_is_inside_a_room_and_walkable(seed):
    level = generate_level(seed)
    assert any(room.contains(*level.player_start) for room in level.rooms)
    assert level.is_walkable(*level.player_start)
    assert level.tile_at(*level.player_start) is Tile.FLOOR


@pytest.mark.parametrize("seed", [-5, 0, 1, 1234, 2**40])
def test_g11_seed_is_recorded(seed):
    assert generate_level(seed).seed == seed


@pytest.mark.parametrize("size", SIZES)
def test_g12_dimensions_are_honoured(size):
    width, height = size
    level = generate_level(11, width, height)
    assert level.width == width
    assert level.height == height
    assert len(level.grid) == height
    assert all(len(row) == width for row in level.grid)


def test_g12_non_default_size_still_satisfies_g3_and_g8():
    level = generate_level(7, 40, 15)
    assert (level.width, level.height) == (40, 15)
    for x in range(level.width):
        assert level.tile_at(x, 0) is Tile.WALL
        assert level.tile_at(x, level.height - 1) is Tile.WALL
    for y in range(level.height):
        assert level.tile_at(0, y) is Tile.WALL
        assert level.tile_at(level.width - 1, y) is Tile.WALL
    assert flood_fill(level, level.player_start) == walkable_cells(level)


def test_smallest_legal_dimensions_produce_a_valid_single_room_level():
    size = MIN_ROOM_SIZE + 2
    level = generate_level(0, size, size)
    assert level.width == level.height == size
    assert len(level.rooms) == 1  # G4
    for x in range(size):  # G3
        assert level.tile_at(x, 0) is Tile.WALL
        assert level.tile_at(x, size - 1) is Tile.WALL
    for y in range(size):
        assert level.tile_at(0, y) is Tile.WALL
        assert level.tile_at(size - 1, y) is Tile.WALL
    assert flood_fill(level, level.player_start) == walkable_cells(level)  # G8
    assert any(room.contains(*level.player_start) for room in level.rooms)  # G10
    assert level.is_walkable(*level.player_start)
    assert len(walkable_cells(level)) == MIN_ROOM_SIZE * MIN_ROOM_SIZE


# --------------------------------------------------------------------------------------
# §3.1 — the error table
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", ["abc", 1.0, True, False, None, [1], (1,), 3 + 0j])
def test_bad_seed_type_raises_type_error(seed):
    with pytest.raises(TypeError):
        generate_level(seed)


@pytest.mark.parametrize("kwargs", [
    {"width": "80"},
    {"width": 80.0},
    {"height": "22"},
    {"height": 22.0},
    {"max_rooms": "12"},
    {"max_rooms": 12.0},
    {"max_rooms": None},
])
def test_bad_dimension_type_raises_type_error(kwargs):
    with pytest.raises(TypeError):
        generate_level(1, **kwargs)


@pytest.mark.parametrize("max_rooms", [0, -1, -100])
def test_max_rooms_below_one_raises_value_error(max_rooms):
    with pytest.raises(ValueError):
        generate_level(1, max_rooms=max_rooms)


@pytest.mark.parametrize("width", [MIN_ROOM_SIZE + 1, MIN_ROOM_SIZE, 1, 0, -3])
def test_width_too_small_raises_value_error(width):
    with pytest.raises(ValueError):
        generate_level(1, width=width)


@pytest.mark.parametrize("height", [MIN_ROOM_SIZE + 1, MIN_ROOM_SIZE, 1, 0, -3])
def test_height_too_small_raises_value_error(height):
    with pytest.raises(ValueError):
        generate_level(1, height=height)


def test_type_errors_take_precedence_over_value_errors():
    with pytest.raises(TypeError):
        generate_level("abc", width=0, max_rooms=0)


# --------------------------------------------------------------------------------------
# Seeds at the edges
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [0, -1, -42, -999983])
def test_zero_and_negative_seeds_are_accepted_and_deterministic(seed):
    first = generate_level(seed)
    second = generate_level(seed)
    assert first == second
    assert first.seed == seed
    assert len(first.rooms) >= 1
    assert flood_fill(first, first.player_start) == walkable_cells(first)


def test_seed_is_positional_and_mandatory():
    with pytest.raises(TypeError):
        generate_level()  # type: ignore[call-arg]


def test_positional_argument_order_matches_the_contract():
    assert generate_level(5, 40, 15, 4) == generate_level(
        seed=5, width=40, height=15, max_rooms=4
    )
