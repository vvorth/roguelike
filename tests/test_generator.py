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


def is_open_spot(level: Level, x: int, y: int) -> bool:
    """CONTRACT-v3 §3.1, re-implemented independently of the generator's own helper.

    An **open spot** is a walkable, non-door cell all eight of whose neighbours are
    non-``WALL``; out-of-bounds neighbours count as ``WALL``.
    """
    if not level.in_bounds(x, y):
        return False
    tile = level.tile_at(x, y)
    if tile is Tile.DOOR or not level.is_walkable(x, y):
        return False
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            if tile_is_wall(level, x + dx, y + dy):
                return False
    return True


def open_spots(level: Level) -> list[tuple[int, int]]:
    """Every open spot on ``level``, in row-major order."""
    return [
        (x, y)
        for y in range(level.height)
        for x in range(level.width)
        if is_open_spot(level, x, y)
    ]


def cells_of(level: Level, tile: Tile) -> list[tuple[int, int]]:
    """Every cell holding ``tile``, in row-major order."""
    return [
        (x, y)
        for y in range(level.height)
        for x in range(level.width)
        if level.tile_at(x, y) is tile
    ]


def room_indices_containing(level: Level, cell: tuple[int, int]) -> list[int]:
    """Indices of the rooms whose floor rect contains ``cell`` (G5 makes this 0 or 1)."""
    return [i for i, room in enumerate(level.rooms) if room.contains(*cell)]


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


def test_grid_contains_only_the_known_tiles():
    """v1's "three known tiles" widened by CONTRACT-v3 §1: a generated level now also
    carries exactly one of each stair tile."""
    level = generate_level(5)
    kinds = {tile for row in level.grid for tile in row}
    assert kinds <= {
        Tile.WALL,
        Tile.FLOOR,
        Tile.DOOR,
        Tile.STAIRS_UP,
        Tile.STAIRS_DOWN,
    }
    assert Tile.STAIRS_UP in kinds
    assert Tile.STAIRS_DOWN in kinds


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
def test_g7_every_room_floor_cell_is_floor_or_a_stair(seed):
    """G7 as amended by CONTRACT-v3 §3.3: stairs are stamped over room-interior floor, so
    a room cell is ``FLOOR`` *or* a stair tile — never ``WALL`` and never a ``DOOR``."""
    level = generate_level(seed)
    for room in level.rooms:
        for y in range(room.y, room.y2 + 1):
            for x in range(room.x, room.x2 + 1):
                assert level.tile_at(x, y) in {
                    Tile.FLOOR,
                    Tile.STAIRS_UP,
                    Tile.STAIRS_DOWN,
                }


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
    # v1 said FLOOR here; CONTRACT-v3 G17 makes the spawn *be* the up-staircase.
    assert level.tile_at(*level.player_start) is Tile.STAIRS_UP


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


# ======================================================================================
# CONTRACT-v3 §3 — stairs, the anchor room, and the seed-chosen spawn
#
# Two things here fail silently and are the whole reason this section exists:
#   * G8 full connectivity, re-verified from the *up-staircase* with and without an
#     anchor — the anchor displaces rooms, and a stranded pocket looks like a normal map;
#   * G1/G2 determinism, re-verified with ``required_up`` set, because the anchor adds
#     four RNG draws ahead of every other draw in the level.
# ======================================================================================

#: The acceptance criteria ask for at least 200 seeds on the stair guarantees.
STAIR_SEEDS = range(200)

#: The three map sizes the connectivity sweep must cover.
G8_SIZES = [(DEFAULT_WIDTH, DEFAULT_HEIGHT), (40, 15), (60, 40)]


# --------------------------------------------------------------------------------------
# Acceptance smoke test and the new signature
# --------------------------------------------------------------------------------------


def test_generate_level_1234_has_a_depth_and_both_staircases():
    level = generate_level(1234)
    assert level.depth == 1
    assert level.stairs_up is not None
    assert len(level.stairs_down) == 1
    assert level.player_start == level.stairs_up
    assert level.tile_at(*level.stairs_up) is Tile.STAIRS_UP
    assert level.tile_at(*level.stairs_down[0]) is Tile.STAIRS_DOWN


def test_new_parameters_are_keyword_and_positional_in_contract_order():
    """CONTRACT-v3 §3: ``(seed, width, height, max_rooms, depth, required_up)``."""
    positional = generate_level(5, 40, 15, 4, 3, (10, 7))
    keyword = generate_level(
        seed=5, width=40, height=15, max_rooms=4, depth=3, required_up=(10, 7)
    )
    assert positional == keyword
    assert positional.depth == 3
    assert positional.stairs_up == (10, 7)


def test_depth_and_required_up_default_to_one_and_none():
    assert generate_level(11) == generate_level(11, depth=1, required_up=None)


def test_stairs_down_is_a_tuple_of_coordinates_not_a_bare_coordinate():
    """G15: the field is a tuple so a second branch can appear without a shape change."""
    level = generate_level(21)
    assert isinstance(level.stairs_down, tuple)
    assert len(level.stairs_down) == 1
    assert isinstance(level.stairs_down[0], tuple)
    assert len(level.stairs_down[0]) == 2


# --------------------------------------------------------------------------------------
# G13 — both stair cells are open spots
# --------------------------------------------------------------------------------------


def test_g13_both_stairs_are_open_spots_over_200_seeds():
    """The guarantee the *next* level depends on: a stair cell is one tile clear of every
    wall on all eight sides, which is exactly what makes it anchorable from below."""
    for seed in STAIR_SEEDS:
        level = generate_level(seed)
        assert is_open_spot(level, *level.stairs_up), f"seed {seed}: up not an open spot"
        assert is_open_spot(level, *level.stairs_down[0]), (
            f"seed {seed}: down not an open spot"
        )


@pytest.mark.parametrize("size", SIZES)
def test_g13_holds_at_every_size(size):
    for seed in range(20):
        level = generate_level(seed, *size)
        assert is_open_spot(level, *level.stairs_up), f"seed {seed} at {size}"
        assert is_open_spot(level, *level.stairs_down[0]), f"seed {seed} at {size}"


@pytest.mark.parametrize("max_rooms", [1, 2, 3, 7, 12, 25])
def test_g13_holds_for_every_room_ceiling(max_rooms):
    for seed in range(20):
        level = generate_level(seed, max_rooms=max_rooms)
        assert is_open_spot(level, *level.stairs_up)
        assert is_open_spot(level, *level.stairs_down[0])


def test_g13_implies_the_next_level_can_always_anchor_there():
    """RESEARCH-v3 §1: an open spot satisfies ``2 <= x <= width - 3``, which is precisely
    the anchorable range. Measured minimum over 300 levels was exactly 2 — the bound is
    reached but never crossed, so the descent chain can never hand up a bad coordinate."""
    smallest_x = smallest_y = 10**6
    for seed in range(120):
        level = generate_level(seed)
        for cell in (level.stairs_up, level.stairs_down[0]):
            x, y = cell
            assert 2 <= x <= level.width - 3, f"seed {seed}: {cell} not anchorable in x"
            assert 2 <= y <= level.height - 3, f"seed {seed}: {cell} not anchorable in y"
            smallest_x = min(smallest_x, x)
            smallest_y = min(smallest_y, y)
    assert smallest_x <= 4 and smallest_y <= 4, (
        "the sweep never came near the margin, so it proved nothing about the bound"
    )


def test_open_spots_never_include_a_door_or_a_wall():
    """The definition's two exclusions, checked against a real level rather than assumed."""
    level = generate_level(31337)
    spots = set(open_spots(level))
    assert spots
    for x, y in spots:
        assert level.tile_at(x, y) is not Tile.DOOR
        assert level.is_walkable(x, y)
    for cell in doors_in(level):
        assert cell not in spots
    for y in range(level.height):
        for x in range(level.width):
            if level.tile_at(x, y) is Tile.WALL:
                assert (x, y) not in spots


# --------------------------------------------------------------------------------------
# G14 — the anchor. required_up is honoured exactly, never approximately.
# --------------------------------------------------------------------------------------


def test_g14_every_open_spot_of_a_fresh_level_can_be_forced_as_the_up_stair():
    """The headline criterion, and the exact shape of the descent link: whatever open spot
    the player descends from, the level below must place its up-staircase there.

    Several spots are sampled per seed, spread across the row-major list so the sample
    covers rooms all over the map rather than clustering in the first one.
    """
    anchored = 0
    for seed in STAIR_SEEDS:
        above = generate_level(seed)
        spots = open_spots(above)
        assert spots, f"seed {seed} produced no open spot at all"
        for index in (0, len(spots) // 2, len(spots) - 1):
            target = spots[index]
            below = generate_level(seed + 1, depth=2, required_up=target)
            assert below.stairs_up == target, (
                f"seed {seed}: required_up {target} came back as {below.stairs_up}"
            )
            assert below.player_start == target
            assert below.tile_at(*target) is Tile.STAIRS_UP
            assert is_open_spot(below, *target)
            anchored += 1
    assert anchored >= 600, f"only {anchored} anchored levels built"


def test_g14_the_two_stair_coordinates_of_any_level_are_themselves_anchorable():
    """The property the integrator's end-to-end descent test rests on: feed a level's own
    down-stair back in as ``required_up`` and the link closes."""
    for seed in range(60):
        above = generate_level(seed)
        below = generate_level(seed * 7 + 1, depth=2, required_up=above.stairs_down[0])
        assert below.stairs_up == above.stairs_down[0]


@pytest.mark.parametrize("size", [(DEFAULT_WIDTH, DEFAULT_HEIGHT), (40, 15), (30, 30)])
def test_g14_holds_at_the_extreme_legal_coordinates(size):
    """``(2, 2)`` and ``(width - 3, height - 3)`` are the corners of the anchorable range;
    a room whose interior covers them only just fits inside the wall margin."""
    width, height = size
    for corner in ((2, 2), (width - 3, height - 3), (2, height - 3), (width - 3, 2)):
        for seed in range(8):
            level = generate_level(seed, width, height, required_up=corner)
            assert level.stairs_up == corner
            assert is_open_spot(level, *corner)
            anchor = level.rooms[0]
            assert anchor.contains(*corner)
            assert 1 <= anchor.x and anchor.x2 <= width - 2  # G6 survives the anchor
            assert 1 <= anchor.y and anchor.y2 <= height - 2


def test_g14_extreme_coordinates_work_on_the_minimal_legal_map():
    size = MIN_ROOM_SIZE + 2
    for corner in ((2, 2), (size - 3, size - 3)):
        level = generate_level(0, size, size, required_up=corner)
        assert level.stairs_up == corner
        assert is_open_spot(level, *corner)


def test_g14_holds_for_every_room_ceiling_including_one():
    """``max_rooms=1`` leaves room for the anchor and nothing else — the anchor still wins,
    because it is placed before the rejection sampling ever runs."""
    for max_rooms in (1, 2, 3, 12, 25):
        for seed in range(10):
            level = generate_level(seed, max_rooms=max_rooms, required_up=(20, 8))
            assert level.stairs_up == (20, 8)
            assert 1 <= len(level.rooms) <= max_rooms


# --------------------------------------------------------------------------------------
# The anchor rule itself (CONTRACT-v3 §3.2) — placed first, never repaired
# --------------------------------------------------------------------------------------


def test_the_anchor_room_is_room_zero_and_holds_the_coordinate_strictly_inside():
    """Strictly inside — never on a floor edge. That is what makes the cell an open spot,
    and being ``rooms[0]`` is what stops the corridor router from ever dropping it."""
    for seed in range(80):
        target = (2 + seed % 70, 2 + seed % 17)
        level = generate_level(seed, required_up=target)
        anchor = level.rooms[0]
        x, y = target
        assert anchor.contains(x, y)
        assert anchor.x < x < anchor.x2, f"seed {seed}: {target} on a vertical floor edge"
        assert anchor.y < y < anchor.y2, f"seed {seed}: {target} on a horizontal edge"
        assert MIN_ROOM_SIZE <= anchor.width <= MAX_ROOM_SIZE
        assert MIN_ROOM_SIZE <= anchor.height <= MAX_ROOM_SIZE


def test_the_anchor_does_not_orphan_a_door_or_deform_a_room():
    """CONTRACT-v3 §3.2 says requirements 8 and 9 are *not implemented* because there is
    nothing to repair. This is the guard: with an anchor in play, every v2 door guarantee
    and every v1 room guarantee still holds untouched.
    """
    for seed in range(120):
        target = (2 + (seed * 13) % 70, 2 + (seed * 5) % 17)
        level = generate_level(seed, required_up=target)
        for i, room in enumerate(level.rooms):  # G5 / G6
            assert 1 <= room.x and room.x2 <= level.width - 2
            assert 1 <= room.y and room.y2 <= level.height - 2
            for other in level.rooms[i + 1 :]:
                assert not room.intersects(other)
        doors = doors_in(level)
        door_cells = set(doors)
        for x, y in doors:  # G9a-G9d
            assert any(room.on_perimeter(x, y) for room in level.rooms)
            assert not any(room.contains(x, y) for room in level.rooms)
            assert_door_is_well_formed(level, x, y)
            for dx, dy in _NEIGHBOURS:
                assert (x + dx, y + dy) not in door_cells
        if len(level.rooms) == 1:  # G4a
            assert doors == []
        else:
            for room in level.rooms:
                assert any(room.on_perimeter(x, y) for x, y in doors), (
                    f"seed {seed}: {room} lost its door to the anchor"
                )


def test_the_anchor_costs_at_most_about_one_room_per_level():
    """RESEARCH-v3 §1 priced the feature at roughly one room per level. A collapse to
    single-room levels would satisfy every other guarantee here and still be a defect."""
    plain = anchored = 0
    for seed in STAIR_SEEDS:
        plain += len(generate_level(seed).rooms)
        anchored += len(generate_level(seed, required_up=(40, 11)).rooms)
    plain_mean = plain / len(STAIR_SEEDS)
    anchored_mean = anchored / len(STAIR_SEEDS)
    assert plain_mean > 8.0, f"baseline itself regressed to {plain_mean:.2f} rooms/level"
    assert anchored_mean > plain_mean - 2.0, (
        f"the anchor cost {plain_mean - anchored_mean:.2f} rooms/level "
        f"({plain_mean:.2f} -> {anchored_mean:.2f})"
    )


def test_every_room_is_still_at_least_min_room_size_with_an_anchor():
    """The property that makes G13 and G16 unconditional: a room 4x4 or larger always has
    a 2x2 interior, so it always offers at least four open spots."""
    for seed in range(60):
        level = generate_level(seed, required_up=(30, 9))
        for room in level.rooms:
            assert MIN_ROOM_SIZE <= room.width <= MAX_ROOM_SIZE
            assert MIN_ROOM_SIZE <= room.height <= MAX_ROOM_SIZE
            interior = [
                (x, y)
                for y in range(room.y + 1, room.y2)
                for x in range(room.x + 1, room.x2)
            ]
            assert len(interior) >= 4
            for x, y in interior:
                assert is_open_spot(level, x, y), f"{room} interior ({x}, {y}) is not open"


# --------------------------------------------------------------------------------------
# G15 / G18 — exactly one of each stair, at the recorded coordinates
# --------------------------------------------------------------------------------------


def test_g15_and_g18_exactly_one_of_each_stair_over_200_seeds():
    for seed in STAIR_SEEDS:
        level = generate_level(seed)
        assert len(level.stairs_down) == 1, f"seed {seed}"
        assert cells_of(level, Tile.STAIRS_UP) == [level.stairs_up], f"seed {seed}"
        assert cells_of(level, Tile.STAIRS_DOWN) == [level.stairs_down[0]], f"seed {seed}"


def test_g15_and_g18_hold_with_an_anchor_over_200_seeds():
    for seed in STAIR_SEEDS:
        level = generate_level(seed, depth=4, required_up=(2 + seed % 70, 2 + seed % 17))
        assert len(level.stairs_down) == 1
        assert cells_of(level, Tile.STAIRS_UP) == [level.stairs_up]
        assert cells_of(level, Tile.STAIRS_DOWN) == [level.stairs_down[0]]


@pytest.mark.parametrize("size", SIZES)
def test_g18_holds_at_every_size(size):
    for seed in range(20):
        level = generate_level(seed, *size)
        assert cells_of(level, Tile.STAIRS_UP) == [level.stairs_up]
        assert cells_of(level, Tile.STAIRS_DOWN) == [level.stairs_down[0]]


def test_stair_tiles_are_walkable_terrain():
    """CONTRACT-v3 §0.9 — this is what keeps ``world``/``fov``/``movement`` unedited."""
    level = generate_level(77)
    assert level.is_walkable(*level.stairs_up)
    assert level.is_walkable(*level.stairs_down[0])


# --------------------------------------------------------------------------------------
# G16 — the two stairs differ, and sit in different rooms
# --------------------------------------------------------------------------------------


def test_g16_stairs_differ_and_sit_in_different_rooms_over_200_seeds():
    multi_room_levels = 0
    for seed in STAIR_SEEDS:
        level = generate_level(seed)
        up, down = level.stairs_up, level.stairs_down[0]
        assert up != down, f"seed {seed}: both stairs at {up}"
        if len(level.rooms) > 1:
            multi_room_levels += 1
            up_rooms = room_indices_containing(level, up)
            down_rooms = room_indices_containing(level, down)
            assert up_rooms and down_rooms, f"seed {seed}: a stair is in no room"
            assert up_rooms != down_rooms, (
                f"seed {seed}: both stairs are in room {up_rooms[0]}"
            )
    assert multi_room_levels > 150, "the sample was dominated by single-room levels"


def test_g16_holds_with_an_anchor_over_200_seeds():
    for seed in STAIR_SEEDS:
        level = generate_level(seed, required_up=(2 + seed % 70, 2 + seed % 17))
        up, down = level.stairs_up, level.stairs_down[0]
        assert up != down
        if len(level.rooms) > 1:
            assert room_indices_containing(level, up) != room_indices_containing(
                level, down
            )


@pytest.mark.parametrize("size", SIZES)
def test_g16_holds_at_every_size(size):
    for seed in range(20):
        level = generate_level(seed, *size)
        assert level.stairs_up != level.stairs_down[0]
        if len(level.rooms) > 1:
            assert room_indices_containing(
                level, level.stairs_up
            ) != room_indices_containing(level, level.stairs_down[0])


def test_g16_a_single_room_level_still_gets_two_distinct_stairs():
    """The minimal legal map is one 4x4 room. Its 2x2 interior is four open spots — enough
    for two distinct stairs, and the reason G16's "different rooms" clause is conditional.
    """
    size = MIN_ROOM_SIZE + 2
    for seed in range(20):
        level = generate_level(seed, size, size)
        assert len(level.rooms) == 1
        up, down = level.stairs_up, level.stairs_down[0]
        assert up != down
        assert level.rooms[0].contains(*up)
        assert level.rooms[0].contains(*down)
        assert is_open_spot(level, *up)
        assert is_open_spot(level, *down)


# --------------------------------------------------------------------------------------
# G17 / G19 — the spawn is the up-staircase; the depth is recorded
# --------------------------------------------------------------------------------------


def test_g17_and_g19_over_200_seeds():
    for seed in STAIR_SEEDS:
        depth = 1 + seed % 9
        level = generate_level(seed, depth=depth)
        assert level.player_start == level.stairs_up, f"seed {seed}"
        assert level.depth == depth, f"seed {seed}"


def test_g17_and_g19_hold_with_an_anchor_over_200_seeds():
    for seed in STAIR_SEEDS:
        depth = 1 + seed % 9
        target = (2 + seed % 70, 2 + seed % 17)
        level = generate_level(seed, depth=depth, required_up=target)
        assert level.player_start == level.stairs_up == target
        assert level.depth == depth


@pytest.mark.parametrize("depth", [1, 2, 3, 10, 99, 10_000])
def test_g19_records_any_positive_depth_without_changing_the_map(depth):
    """``depth`` is inert data here — the generator knows nothing about dungeons, so two
    levels differing only in depth must have identical terrain."""
    level = generate_level(4242, depth=depth)
    assert level.depth == depth
    assert level.grid == generate_level(4242).grid


# --------------------------------------------------------------------------------------
# G20 — the up-stair is an RNG draw over the open spots, not a fixed corner
# --------------------------------------------------------------------------------------


def test_g20_the_up_stair_is_not_rooms_zero_center():
    """A lazy implementation reusing v2's ``rooms[0].center`` spawn must fail this."""
    seeds = list(range(60))
    matches = 0
    for seed in seeds:
        level = generate_level(seed)
        if level.stairs_up == level.rooms[0].center:
            matches += 1
    assert matches < len(seeds) // 3, (
        f"the up-stair equalled rooms[0].center on {matches} of {len(seeds)} seeds — "
        f"that is the v2 spawn, not a seed-determined choice"
    )


def test_g20_the_up_stair_is_not_pinned_to_any_room_index_or_corner():
    """Stronger than the letter of G20: neither the room it lands in nor its offset within
    that room may be a constant. Every "always the first room" or "always the top-left
    interior cell" shortcut is caught here.
    """
    room_indices = set()
    offsets = set()
    for seed in range(60):
        level = generate_level(seed)
        index = room_indices_containing(level, level.stairs_up)[0]
        room = level.rooms[index]
        room_indices.add(index)
        offsets.add((level.stairs_up[0] - room.x, level.stairs_up[1] - room.y))
    assert len(room_indices) > 1, f"the up-stair is always in room {room_indices}"
    assert len(offsets) > 1, f"the up-stair is always at room offset {offsets}"


def test_g20_the_down_stair_is_also_seed_chosen():
    offsets = set()
    for seed in range(60):
        level = generate_level(seed)
        index = room_indices_containing(level, level.stairs_down[0])[0]
        room = level.rooms[index]
        offsets.add((level.stairs_down[0][0] - room.x, level.stairs_down[0][1] - room.y))
    assert len(offsets) > 1, "the down-stair sits at a fixed offset in its room"


def test_g20_the_chosen_up_stair_is_always_one_of_the_open_spots():
    for seed in range(60):
        level = generate_level(seed)
        spots = set(open_spots(level))
        assert level.stairs_up in spots
        assert level.stairs_down[0] in spots


def test_g20_different_seeds_move_the_stairs():
    ups = {generate_level(seed).stairs_up for seed in range(50)}
    downs = {generate_level(seed).stairs_down[0] for seed in range(50)}
    assert len(ups) > 25, f"only {len(ups)} distinct up-stairs over 50 seeds"
    assert len(downs) > 25, f"only {len(downs)} distinct down-stairs over 50 seeds"


# --------------------------------------------------------------------------------------
# G8 — connectivity from the up-staircase. The guarantee that fails silently.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("size", G8_SIZES)
def test_g8_from_the_up_stair_over_30_seeds_at_three_sizes_without_an_anchor(size):
    for seed in range(30):
        level = generate_level(seed, *size)
        reached = flood_fill(level, level.stairs_up)
        assert reached == walkable_cells(level), f"seed {seed} at {size}"
        assert level.stairs_down[0] in reached
        for room in level.rooms:
            for y in range(room.y, room.y2 + 1):
                for x in range(room.x, room.x2 + 1):
                    assert (x, y) in reached, f"seed {seed} at {size}: ({x}, {y}) cut off"


@pytest.mark.parametrize("size", G8_SIZES)
def test_g8_from_the_up_stair_over_30_seeds_at_three_sizes_with_an_anchor(size):
    """The anchor displaces rooms and reroutes corridors, so this is where a stranded
    pocket would appear. The flood fill is implemented independently in this module."""
    width, height = size
    for seed in range(30):
        target = (2 + (seed * 7) % (width - 5), 2 + (seed * 3) % (height - 5))
        level = generate_level(seed, width, height, required_up=target)
        reached = flood_fill(level, level.stairs_up)
        assert level.stairs_up == target
        assert reached == walkable_cells(level), f"seed {seed} at {size}, anchor {target}"
        assert level.stairs_down[0] in reached
        for room in level.rooms:
            for y in range(room.y, room.y2 + 1):
                for x in range(room.x, room.x2 + 1):
                    assert (x, y) in reached, f"seed {seed} at {size}: ({x}, {y}) cut off"


@pytest.mark.parametrize("max_rooms", [1, 2, 3, 7, 12, 25])
def test_g8_from_the_up_stair_for_every_room_ceiling_with_an_anchor(max_rooms):
    for seed in range(12):
        level = generate_level(seed, max_rooms=max_rooms, required_up=(30, 9))
        assert flood_fill(level, level.stairs_up) == walkable_cells(level)


def test_g8_survives_a_five_level_descent_chain():
    """The end-to-end shape the integrator will build on: each level's down-stair becomes
    the next level's ``required_up``. Seed derivation belongs to another task, so a plain
    local mix stands in for it here — the linkage is what is under test."""
    for master in range(12):
        required_up = None
        for depth in range(1, 6):
            seed = master * 1000 + depth
            level = generate_level(seed, depth=depth, required_up=required_up)
            if required_up is not None:
                assert level.stairs_up == required_up, f"master {master} depth {depth}"
            assert level.depth == depth
            assert flood_fill(level, level.stairs_up) == walkable_cells(level)
            assert is_open_spot(level, *level.stairs_down[0])
            required_up = level.stairs_down[0]


def test_stamping_a_stair_does_not_change_the_walkable_set():
    """Stairs are stamped over room-interior floor, so the walkable set — and therefore
    G8 — is exactly what it was before. Both stair cells were floor; nothing else moved."""
    level = generate_level(808)
    walkable = walkable_cells(level)
    floors = {
        (x, y)
        for y in range(level.height)
        for x in range(level.width)
        if level.tile_at(x, y) in {Tile.FLOOR, Tile.STAIRS_UP, Tile.STAIRS_DOWN}
    }
    doors = set(doors_in(level))
    assert walkable == floors | doors
    for cell in (level.stairs_up, level.stairs_down[0]):
        assert any(room.contains(*cell) for room in level.rooms)


# --------------------------------------------------------------------------------------
# G1 / G2 — determinism, re-verified with the anchor in play
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [0, 1, 1234, -7, 999983, 2**40])
def test_g1_determinism_holds_with_required_up_set(seed):
    target = (2 + seed % 70, 2 + seed % 17)
    a = generate_level(seed, depth=5, required_up=target)
    b = generate_level(seed, depth=5, required_up=target)
    assert a == b
    assert a.grid == b.grid
    assert a.stairs_up == b.stairs_up == target
    assert a.stairs_down == b.stairs_down
    assert a.rooms == b.rooms


@pytest.mark.parametrize("seed", [0, 1, 1234, -7, 999983])
def test_g1_stairs_are_stable_across_interleaved_and_anchored_calls(seed):
    """A shared global RNG, or any state left over from the anchor draw, would show up as
    a different staircase after an unrelated call."""
    expected = generate_level(seed)
    generate_level(seed + 1, required_up=(9, 9))
    generate_level(seed + 2, 40, 15)
    assert generate_level(seed) == expected


def test_g1_the_anchor_draw_does_not_leak_between_required_up_values():
    """Two anchors on the same seed must give different levels but each must be stable."""
    first = generate_level(1234, required_up=(10, 5))
    second = generate_level(1234, required_up=(60, 15))
    assert first.stairs_up == (10, 5)
    assert second.stairs_up == (60, 15)
    assert first.grid != second.grid
    assert generate_level(1234, required_up=(10, 5)) == first
    assert generate_level(1234, required_up=(60, 15)) == second


@pytest.mark.parametrize("required_up", [None, (2, 2), (41, 13)])
def test_g2_grid_digest_matches_across_processes_with_and_without_an_anchor(required_up):
    """A fixed seed's grid, recomputed in fresh interpreters under three different
    ``PYTHONHASHSEED`` values. Any dependence on string or object hashing — or on set
    iteration order, which ``PYTHONHASHSEED`` perturbs — shows up as a mismatch."""
    seed = 4242
    expected = grid_digest(generate_level(seed, depth=3, required_up=required_up))
    script = (
        "import hashlib\n"
        "from roguelike.generator import generate_level\n"
        f"level = generate_level({seed}, depth=3, required_up={required_up!r})\n"
        "payload = '\\n'.join(''.join(str(int(t)) for t in row) for row in level.grid)\n"
        "print(hashlib.sha256(payload.encode('ascii')).hexdigest())\n"
        "print(level.stairs_up, level.stairs_down, level.depth)\n"
    )
    results = []
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
        results.append(result.stdout.strip().splitlines())
    assert [line[0] for line in results] == [expected] * 3
    assert len({tuple(line) for line in results}) == 1


def test_g2_stair_coordinates_match_across_processes():
    """The digest above covers the grid; the recorded coordinates are separate fields and
    are what the descent chain actually reads."""
    level = generate_level(31337, depth=2)
    script = (
        "from roguelike.generator import generate_level\n"
        "level = generate_level(31337, depth=2)\n"
        "print(level.stairs_up, level.stairs_down, level.player_start, level.depth)\n"
    )
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "31337"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    expected = (
        f"{level.stairs_up} {level.stairs_down} {level.player_start} {level.depth}"
    )
    assert result.stdout.strip() == expected


def test_g2_the_anchor_uses_the_level_rng_only():
    """Source scan, extending the existing one to the code paths added by v3. A stray
    ``random.randint`` in the anchor sizing would be invisible in a single process."""
    source = GENERATOR_SOURCE.read_text(encoding="utf-8")
    assert "random.Random(" in source
    for forbidden in (
        "random.randint(",
        "random.choice(",
        "random.shuffle(",
        "random.randrange(",
        "random.random(",
        "random.sample(",
        "random.seed(",
        "random.getrandbits(",
    ):
        assert forbidden not in source, f"module-level RNG call {forbidden!r} in source"


# --------------------------------------------------------------------------------------
# CONTRACT-v3 §3.4 — the new error table
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("depth", [0, -1, -100])
def test_depth_below_one_raises_value_error(depth):
    with pytest.raises(ValueError):
        generate_level(1, depth=depth)


@pytest.mark.parametrize("depth", ["1", 1.0, None, [1], (1,), 2 + 0j])
def test_non_int_depth_raises_type_error(depth):
    with pytest.raises(TypeError):
        generate_level(1, depth=depth)


@pytest.mark.parametrize(
    "required_up",
    ["x", "(1, 1)", (1,), (1, 2, 3), (), [2, 2], 5, {2: 2}, ("2", "2"), (2.0, 2.0),
     (2, "2"), (None, None)],
)
def test_malformed_required_up_raises_type_error(required_up):
    with pytest.raises(TypeError):
        generate_level(1, required_up=required_up)


@pytest.mark.parametrize(
    "required_up",
    [(0, 0), (1, 1), (1, 5), (5, 1), (2, 1), (1, 2), (0, 10), (10, 0),
     (DEFAULT_WIDTH, 0), (DEFAULT_WIDTH - 2, 10), (10, DEFAULT_HEIGHT - 2),
     (DEFAULT_WIDTH + 5, 5), (5, DEFAULT_HEIGHT + 5), (-1, -1), (-3, 10)],
)
def test_unanchorable_required_up_raises_value_error(required_up):
    with pytest.raises(ValueError):
        generate_level(1, required_up=required_up)


def test_the_anchorable_range_boundary_is_exact():
    """``2 <= x <= width - 3`` — one step further out must raise, the boundary itself must
    work. An off-by-one here would let the descent chain build an unanchorable level."""
    width, height = DEFAULT_WIDTH, DEFAULT_HEIGHT
    for good in ((2, 2), (width - 3, 2), (2, height - 3), (width - 3, height - 3)):
        assert generate_level(1, required_up=good).stairs_up == good
    for bad in (
        (1, 2), (2, 1), (width - 2, 2), (2, height - 2), (width - 3, height - 2),
    ):
        with pytest.raises(ValueError):
            generate_level(1, required_up=bad)


@pytest.mark.parametrize(
    "required_up",
    [(1, 2), (2, 1), (DEFAULT_WIDTH - 2, 2), (2, DEFAULT_HEIGHT - 2),
     (DEFAULT_WIDTH, 0), (0, 0)],
)
def test_unanchorable_required_up_is_rejected_by_validation_not_by_accident(required_up):
    """A one-cell-wide off-by-one in the range check is invisible to a bare
    ``pytest.raises(ValueError)``: an out-of-range coordinate makes the anchor's own
    ``randint`` bounds cross over, which raises ``ValueError`` too. Pinning the message
    is what distinguishes the deliberate §3.4 rejection from that accident.

    The wording is this module's, not the contract's — the contract fixes only the
    exception type.
    """
    with pytest.raises(ValueError, match="anchorable"):
        generate_level(1, required_up=required_up)


def test_type_errors_still_take_precedence_over_value_errors_for_the_new_arguments():
    with pytest.raises(TypeError):
        generate_level(1, depth="1", max_rooms=0)
    with pytest.raises(TypeError):
        generate_level(1, depth=0, required_up="x")
    with pytest.raises(TypeError):
        generate_level("abc", depth=0, required_up=(0, 0))


def test_required_up_is_validated_against_the_actual_map_size():
    """A coordinate legal on 80x22 is not legal on the minimal map, and vice versa."""
    assert generate_level(1, required_up=(40, 11)).stairs_up == (40, 11)
    with pytest.raises(ValueError):
        generate_level(1, MIN_ROOM_SIZE + 2, MIN_ROOM_SIZE + 2, required_up=(40, 11))
    size = MIN_ROOM_SIZE + 2
    assert generate_level(1, size, size, required_up=(3, 3)).stairs_up == (3, 3)


# --------------------------------------------------------------------------------------
# The minimal legal map, end to end under v3
# --------------------------------------------------------------------------------------


def test_minimal_legal_map_satisfies_g13_to_g19():
    """A one-room 6x6 map still needs two distinct stair cells, both open spots. Its whole
    open-spot population is the room's 2x2 interior — the tightest case in the contract."""
    size = MIN_ROOM_SIZE + 2
    level = generate_level(0, size, size)
    assert level.width == level.height == size
    assert len(level.rooms) == 1
    room = level.rooms[0]
    assert set(open_spots(level)) == {
        (x, y) for y in range(room.y + 1, room.y2) for x in range(room.x + 1, room.x2)
    }
    up, down = level.stairs_up, level.stairs_down[0]
    assert is_open_spot(level, *up)  # G13
    assert is_open_spot(level, *down)
    assert len(level.stairs_down) == 1  # G15
    assert up != down  # G16
    assert level.player_start == up  # G17
    assert cells_of(level, Tile.STAIRS_UP) == [up]  # G18
    assert cells_of(level, Tile.STAIRS_DOWN) == [down]
    assert level.depth == 1  # G19
    assert flood_fill(level, up) == walkable_cells(level)  # G8
    assert len(walkable_cells(level)) == MIN_ROOM_SIZE * MIN_ROOM_SIZE


@pytest.mark.parametrize("seed", range(12))
def test_minimal_legal_map_is_deterministic_and_anchorable(seed):
    size = MIN_ROOM_SIZE + 2
    assert generate_level(seed, size, size) == generate_level(seed, size, size)
    for corner in ((2, 2), (3, 3), (2, 3), (3, 2)):
        level = generate_level(seed, size, size, required_up=corner)
        assert level.stairs_up == corner
        assert level.stairs_down[0] != corner
        assert flood_fill(level, level.stairs_up) == walkable_cells(level)
