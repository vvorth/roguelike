"""Unit tests for :mod:`roguelike.movement` (CONTRACT §6, task T05).

Test levels are built by hand from character rows and constructed directly as ``Level``
objects — nothing here imports the generator.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import inspect
import pathlib

import pytest

from roguelike.level import Level, freeze_grid
from roguelike.movement import MoveResult, is_blocked, try_move
from roguelike.tiles import Tile

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

# Two diagonal walls flanking a walkable destination (BRIEF Q7): stepping from
# (1, 1) to (2, 2) squeezes between the walls at (2, 1) and (1, 2).
DIAGONAL_ROWS = [
    "#####",
    "#.#.#",
    "##.##",
    "#.#.#",
    "#####",
]

ALL_DELTAS = [
    (-1, -1), (0, -1), (1, -1),
    (-1, 0), (1, 0),
    (-1, 1), (0, 1), (1, 1),
]


def open_level() -> Level:
    return make_level(OPEN_ROWS, player_start=(1, 1), seed=7)


# --------------------------------------------------------------------------
# Accepted moves
# --------------------------------------------------------------------------


def test_move_onto_floor_is_accepted():
    level = open_level()
    result = try_move(level, (2, 1), 1, 0)
    assert result == MoveResult((3, 1), True)
    assert result.moved is True
    assert result.position == (3, 1)


def test_move_onto_door_is_accepted():
    # CONTRACT-v2 §6: passability now goes through world.is_passable, which treats a
    # door absent from open_doors as impassable. An open door must be named
    # explicitly for the move to succeed — see the v1-regression test further down
    # for the (now-changed) no-argument case.
    level = open_level()
    assert level.tile_at(3, 5) is Tile.DOOR
    result = try_move(level, (3, 4), 0, 1, open_doors=frozenset({(3, 5)}))
    assert result.moved is True
    assert result.position == (3, 5)


@pytest.mark.parametrize("dx, dy", ALL_DELTAS)
def test_all_eight_directions_land_on_expected_target(dx, dy):
    # Start at (2, 3): x != y, so a swapped axis cannot pass silently.
    level = open_level()
    start = (2, 3)
    assert level.is_walkable(*start)
    result = try_move(level, start, dx, dy)
    assert result.moved is True
    assert result.position == (start[0] + dx, start[1] + dy)


def test_sign_convention_dy_minus_one_is_up():
    level = open_level()
    start = (2, 3)
    up = try_move(level, start, 0, -1)
    down = try_move(level, start, 0, 1)
    assert up.position == (2, 2)
    assert up.position[1] < start[1]
    assert down.position == (2, 4)
    assert down.position[1] > start[1]


def test_sign_convention_dx_minus_one_is_left():
    level = open_level()
    start = (3, 2)
    left = try_move(level, start, -1, 0)
    right = try_move(level, start, 1, 0)
    assert left.position == (2, 2)
    assert right.position == (4, 2)


def test_diagonal_between_two_walls_is_allowed():
    # Deliberate scope decision (BRIEF Q7): no corner-cutting rules. If this ever
    # starts failing, someone has added a rule the contract forbids.
    level = make_level(DIAGONAL_ROWS, player_start=(1, 1))
    assert level.tile_at(2, 1) is Tile.WALL
    assert level.tile_at(1, 2) is Tile.WALL
    assert level.tile_at(2, 2) is Tile.FLOOR
    result = try_move(level, (1, 1), 1, 1)
    assert result == MoveResult((2, 2), True)


def test_all_four_diagonals_between_walls_are_allowed():
    level = make_level(DIAGONAL_ROWS, player_start=(1, 1))
    for start, (dx, dy) in [
        ((1, 1), (1, 1)),
        ((3, 1), (-1, 1)),
        ((1, 3), (1, -1)),
        ((3, 3), (-1, -1)),
    ]:
        result = try_move(level, start, dx, dy)
        assert result.moved is True
        assert result.position == (2, 2)


# --------------------------------------------------------------------------
# Rejected moves
# --------------------------------------------------------------------------


def test_move_into_wall_is_rejected_and_position_unchanged():
    level = open_level()
    start = (1, 1)
    result = try_move(level, start, -1, 0)
    assert result.moved is False
    assert result.position == start
    assert result.position == (1, 1)


def test_rejected_result_returns_the_input_tuple_itself():
    level = open_level()
    start = (1, 1)
    result = try_move(level, start, 0, -1)
    assert result.position == start
    assert result.position is start


def test_zero_delta_does_not_move_and_does_not_raise():
    level = open_level()
    start = (2, 3)
    result = try_move(level, start, 0, 0)
    assert result.moved is False
    assert result.position == start


def test_zero_delta_on_a_wall_tile_also_does_not_move():
    level = open_level()
    start = (0, 0)
    result = try_move(level, start, 0, 0)
    assert result == MoveResult((0, 0), False)


def test_repeated_rejected_moves_never_change_position():
    level = open_level()
    pos = (1, 1)
    for _ in range(10):
        result = try_move(level, pos, -1, 0)
        assert result.moved is False
        pos = result.position
    assert pos == (1, 1)


@pytest.mark.parametrize(
    "start, dx, dy, edge",
    [
        ((0, 2), -1, 0, "left of x=0"),
        ((3, 0), 0, -1, "above y=0"),
        ((6, 2), 1, 0, "right of width-1"),
        ((3, 5), 0, 1, "below height-1"),
    ],
)
def test_moving_off_each_edge_is_rejected_without_raising(start, dx, dy, edge):
    level = open_level()
    result = try_move(level, start, dx, dy)
    assert result.moved is False, edge
    assert result.position == start, edge


def test_moving_off_a_corner_diagonally_is_rejected():
    level = open_level()
    for start, (dx, dy) in [
        ((0, 0), (-1, -1)),
        ((6, 0), (1, -1)),
        ((0, 5), (-1, 1)),
        ((6, 5), (1, 1)),
    ]:
        result = try_move(level, start, dx, dy)
        assert result.moved is False
        assert result.position == start


def test_moving_from_far_outside_the_map_is_rejected_without_raising():
    level = open_level()
    for start in [(-5, -5), (100, 100), (-1, 2), (2, -1)]:
        for dx, dy in ALL_DELTAS:
            result = try_move(level, start, dx, dy)
            if result.moved:
                # Only a step that lands back on a walkable cell may succeed.
                assert level.is_walkable(*result.position)
            else:
                assert result.position == start


def test_single_cell_all_wall_level_rejects_everything():
    level = make_level(["#"], player_start=(0, 0))
    for dx, dy in ALL_DELTAS + [(0, 0)]:
        result = try_move(level, (0, 0), dx, dy)
        assert result.moved is False
        assert result.position == (0, 0)


# --------------------------------------------------------------------------
# Illegal deltas
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [2, -2, 10, -10, 3, 99])
def test_bad_dx_raises_value_error(bad):
    level = open_level()
    with pytest.raises(ValueError):
        try_move(level, (2, 3), bad, 0)


@pytest.mark.parametrize("bad", [2, -2, 10, -10, 3, 99])
def test_bad_dy_raises_value_error(bad):
    level = open_level()
    with pytest.raises(ValueError):
        try_move(level, (2, 3), 0, bad)


def test_both_deltas_bad_raises_value_error():
    level = open_level()
    with pytest.raises(ValueError):
        try_move(level, (2, 3), 2, 2)


@pytest.mark.parametrize("dx, dy", ALL_DELTAS + [(0, 0)])
def test_legal_deltas_never_raise(dx, dy):
    level = open_level()
    try_move(level, (2, 3), dx, dy)
    try_move(level, (0, 0), dx, dy)


# --------------------------------------------------------------------------
# is_blocked
# --------------------------------------------------------------------------


def test_is_blocked_true_for_walls():
    level = open_level()
    assert is_blocked(level, 0, 0) is True
    assert is_blocked(level, 3, 0) is True
    assert is_blocked(level, 0, 2) is True


def test_is_blocked_false_for_floor_and_door():
    level = open_level()
    assert is_blocked(level, 1, 1) is False
    assert is_blocked(level, 5, 3) is False
    assert level.tile_at(3, 5) is Tile.DOOR
    assert is_blocked(level, 3, 5) is False


@pytest.mark.parametrize(
    "x, y",
    [(-1, 0), (0, -1), (-1, -1), (7, 2), (2, 6), (7, 6), (-100, 3), (3, 1000)],
)
def test_is_blocked_true_for_out_of_bounds(x, y):
    level = open_level()
    assert is_blocked(level, x, y) is True


def test_is_blocked_matches_not_is_walkable_over_a_full_sweep():
    level = open_level()
    for y in range(-2, level.height + 2):
        for x in range(-2, level.width + 2):
            assert is_blocked(level, x, y) == (not level.is_walkable(x, y))


def test_is_blocked_agrees_with_try_move_rejection_off_doors():
    # is_blocked is retained terrain-only (CONTRACT-v2 §6): it has no notion of door
    # state, so it still agrees with try_move's default (all-doors-closed) rejection
    # everywhere EXCEPT at a door cell, which is walkable terrain but, by default,
    # an impassable closed door (see test_is_blocked_and_try_move_diverge_at_a_closed_door
    # below for that documented divergence).
    level = open_level()
    door = (3, 5)
    for y in range(-1, level.height + 1):
        for x in range(-1, level.width + 1):
            for dx, dy in ALL_DELTAS:
                target = (x + dx, y + dy)
                if target == door:
                    continue
                result = try_move(level, (x, y), dx, dy)
                assert result.moved is not is_blocked(level, *target)


def test_is_blocked_and_try_move_diverge_at_a_closed_door():
    # The one place is_blocked (terrain-only) and try_move (door-aware via world) must
    # disagree: a door is terrain-walkable (is_blocked says False, i.e. "not blocked"),
    # but try_move's default open_doors=frozenset() treats it as closed and impassable.
    level = open_level()
    door = (3, 5)
    assert is_blocked(level, *door) is False
    result = try_move(level, (3, 4), 0, 1)
    assert result.moved is False
    assert result.blocked_by_door == door


# --------------------------------------------------------------------------
# MoveResult shape and immutability
# --------------------------------------------------------------------------


def test_move_result_is_a_frozen_dataclass_with_binding_field_order():
    # CONTRACT-v2 §6 amendment: blocked_by_door is a THIRD field with a default, and
    # CONTRACT-v5 §7.9 appends blocked_by_npc as a FOURTH, also defaulted — so v1's
    # two-field positional construction still works unchanged (asserted below).
    assert dataclasses.is_dataclass(MoveResult)
    fields = [f.name for f in dataclasses.fields(MoveResult)]
    assert fields == ["position", "moved", "blocked_by_door", "blocked_by_npc"]
    result = MoveResult((4, 2), True)
    assert result.position == (4, 2)
    assert result.moved is True
    assert result.blocked_by_door is None
    assert result.blocked_by_npc is None


def test_move_result_is_immutable():
    result = MoveResult((1, 1), False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.moved = True
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.position = (9, 9)


def test_move_result_equality_by_value():
    assert MoveResult((1, 2), True) == MoveResult((1, 2), True)
    assert MoveResult((1, 2), True) != MoveResult((1, 2), False)
    assert MoveResult((1, 2), True) != MoveResult((2, 1), True)


# --------------------------------------------------------------------------
# Purity
# --------------------------------------------------------------------------


def test_level_and_position_unchanged_after_mixed_move_sequence():
    level = open_level()
    before = copy.deepcopy(level)
    start = (2, 3)
    moves = [(1, 0), (-1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (0, 0), (-1, 0)]
    pos = start
    for dx, dy in moves:
        result = try_move(level, pos, dx, dy)
        pos = result.position
    # Rejected moves in the sequence tried to leave the room; nothing escaped it.
    assert level == before
    assert level.grid == before.grid
    assert start == (2, 3)


def test_try_move_is_deterministic_for_the_same_inputs():
    level = open_level()
    first = try_move(level, (2, 3), 1, -1)
    second = try_move(level, (2, 3), 1, -1)
    assert first == second


def test_accepted_move_does_not_alias_the_input_position():
    level = open_level()
    start = (2, 3)
    result = try_move(level, start, 1, 0)
    assert result.position is not start
    assert start == (2, 3)


# --------------------------------------------------------------------------
# Import hygiene (CONTRACT §10, §0.3)
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


def test_movement_module_imports_are_within_the_contract_graph():
    # CONTRACT-v2 §10 replacement: movement.py <- level, world (world is new here).
    import roguelike.movement

    imports = _module_imports(pathlib.Path(roguelike.movement.__file__))
    forbidden = {
        "roguelike.render",
        "roguelike.keys",
        "roguelike.game",
        "roguelike.generator",
        "curses",
    }
    assert not (imports & forbidden)
    roguelike_imports = {n for n in imports if n.split(".")[0] == "roguelike"}
    assert roguelike_imports <= {"roguelike.level", "roguelike.tiles", "roguelike.world"}
    assert imports <= {
        "__future__",
        "dataclasses",
        "roguelike.level",
        "roguelike.tiles",
        "roguelike.world",
    }


# --------------------------------------------------------------------------
# CONTRACT-v2 §6 amendment: open_doors, blocked_by_door
# --------------------------------------------------------------------------

DOOR_POS = (3, 5)
DOOR_APPROACH = (3, 4)  # one step north of the door


def test_try_move_onto_floor_reports_no_blocked_by_door():
    level = open_level()
    result = try_move(level, (2, 1), 1, 0)
    assert result == MoveResult((3, 1), True, None)
    assert result.blocked_by_door is None


def test_try_move_onto_open_door_succeeds():
    level = open_level()
    result = try_move(level, DOOR_APPROACH, 0, 1, open_doors=frozenset({DOOR_POS}))
    assert result.moved is True
    assert result.position == DOOR_POS
    assert result.blocked_by_door is None


def test_try_move_onto_closed_door_is_rejected_with_blocked_by_door_set():
    level = open_level()
    result = try_move(level, DOOR_APPROACH, 0, 1, open_doors=frozenset())
    assert result.moved is False
    assert result.blocked_by_door == DOOR_POS


def test_try_move_onto_closed_door_returns_the_input_position_object_itself():
    level = open_level()
    start = DOOR_APPROACH
    result = try_move(level, start, 0, 1, open_doors=frozenset())
    assert result.position is start
    assert result.moved is False


def test_try_move_into_wall_leaves_blocked_by_door_none():
    level = open_level()
    start = (1, 1)
    result = try_move(level, start, -1, 0)
    assert result.moved is False
    assert result.blocked_by_door is None


def test_try_move_off_every_edge_leaves_blocked_by_door_none():
    level = open_level()
    for start, dx, dy in [
        ((0, 2), -1, 0),
        ((3, 0), 0, -1),
        ((6, 2), 1, 0),
        ((3, 5), 0, 1),
    ]:
        result = try_move(level, start, dx, dy)
        assert result.moved is False
        assert result.blocked_by_door is None


def test_try_move_with_no_open_doors_argument_treats_every_door_as_closed():
    # CONTRACT-v2 §6: the intended behaviour change. A v1 call site that walked
    # through the door at DOOR_POS by relying on Level.is_walkable now gets
    # moved=False, because try_move defaults to open_doors=frozenset() and routes
    # through world.is_passable, which treats a door absent from open_doors as
    # impassable regardless of the (always-walkable) terrain underneath it.
    level = open_level()
    assert level.is_walkable(*DOOR_POS) is True  # terrain says walkable...
    result = try_move(level, DOOR_APPROACH, 0, 1)  # ...but no open_doors given
    assert result.moved is False
    assert result.blocked_by_door == DOOR_POS
    assert result.position == DOOR_APPROACH


def test_try_move_default_closed_door_vs_explicit_open_doors_diverge():
    level = open_level()
    closed_by_default = try_move(level, DOOR_APPROACH, 0, 1)
    opened_explicitly = try_move(
        level, DOOR_APPROACH, 0, 1, open_doors=frozenset({DOOR_POS})
    )
    assert closed_by_default.moved is False
    assert opened_explicitly.moved is True


def test_try_move_uses_world_is_passable_not_level_is_walkable_for_the_target(monkeypatch):
    # The uniform rejection path must go through world.is_passable, not a re-derived
    # bounds check or a direct call to level.is_walkable for the pass/fail decision.
    import roguelike.movement as movement_module

    calls = []
    real_is_passable = movement_module.is_passable

    def spy(level, open_doors, x, y):
        calls.append((x, y))
        return real_is_passable(level, open_doors, x, y)

    monkeypatch.setattr(movement_module, "is_passable", spy)
    level = open_level()
    try_move(level, (2, 1), 1, 0)
    assert (3, 1) in calls


def test_try_move_zero_delta_on_a_door_does_not_move_and_is_not_door_blocked():
    level = open_level()
    result = try_move(level, DOOR_POS, 0, 0, open_doors=frozenset())
    assert result.moved is False
    assert result.blocked_by_door is None


def test_try_move_zero_delta_on_floor_still_works_with_open_doors_argument():
    level = open_level()
    result = try_move(level, (2, 3), 0, 0, open_doors=frozenset({DOOR_POS}))
    assert result.moved is False
    assert result.blocked_by_door is None


@pytest.mark.parametrize("bad", [2, -2, 10, -10, 3, 99])
def test_bad_dx_raises_before_door_check_even_when_target_is_a_door(bad):
    level = open_level()
    with pytest.raises(ValueError):
        try_move(level, DOOR_APPROACH, bad, 1, open_doors=frozenset())


@pytest.mark.parametrize("bad", [2, -2, 10, -10, 3, 99])
def test_bad_dy_raises_before_door_check_even_when_target_is_a_door(bad):
    level = open_level()
    with pytest.raises(ValueError):
        try_move(level, DOOR_APPROACH, 0, bad, open_doors=frozenset())


def test_move_result_three_field_construction_and_defaults():
    result = MoveResult((1, 2), False, (3, 4))
    assert result.blocked_by_door == (3, 4)
    default_result = MoveResult((1, 2), True)
    assert default_result.blocked_by_door is None


def test_move_result_is_immutable_including_new_field():
    result = MoveResult((1, 1), False, (3, 5))
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.blocked_by_door = None


def test_move_result_equality_considers_blocked_by_door():
    assert MoveResult((1, 1), False, (2, 2)) == MoveResult((1, 1), False, (2, 2))
    assert MoveResult((1, 1), False, (2, 2)) != MoveResult((1, 1), False, None)


def test_open_doors_argument_is_not_mutated():
    level = open_level()
    doors = frozenset({DOOR_POS})
    try_move(level, DOOR_APPROACH, 0, 1, open_doors=doors)
    assert doors == frozenset({DOOR_POS})


# --------------------------------------------------------------------------
# CONTRACT-v5 §7.9: occupancy, and bump-to-attack's half of it
#
# `occupied` is a set of coordinates and nothing else — this module has never
# heard of a monster, a hit point or a fight. It reports *which* cell it refused
# and the caller decides that refusing to walk into something means swinging at
# it, exactly as it already decides that bumping a door means opening it.
# --------------------------------------------------------------------------

# (2, 3) is floor with floor on all eight sides, so every delta from it is a
# legal step until something is standing in the way.
CENTRE = (2, 3)


def test_occupied_defaults_to_empty_so_every_v1_to_v4_call_is_unchanged():
    level = open_level()
    for dx, dy in ALL_DELTAS:
        assert try_move(level, CENTRE, dx, dy) == try_move(
            level, CENTRE, dx, dy, frozenset(), frozenset()
        )


@pytest.mark.parametrize("dx, dy", ALL_DELTAS)
def test_a_step_into_an_occupied_cell_is_refused_and_names_it(dx, dy):
    level = open_level()
    target = (CENTRE[0] + dx, CENTRE[1] + dy)
    result = try_move(level, CENTRE, dx, dy, occupied=frozenset({target}))
    assert result.moved is False
    assert result.position == CENTRE
    assert result.blocked_by_npc == target
    assert result.blocked_by_door is None


def test_the_refused_position_is_the_same_object_not_an_equal_copy():
    # The whole point of returning the input position is that a rejected move
    # changes nothing at all — including identity.
    level = open_level()
    position = (2, 3)
    result = try_move(level, position, 1, 0, occupied=frozenset({(3, 3)}))
    assert result.position is position


def test_an_occupied_cell_elsewhere_does_not_block_the_step():
    level = open_level()
    result = try_move(level, CENTRE, 1, 0, occupied=frozenset({(1, 1), (4, 4)}))
    assert result.moved is True
    assert result.position == (3, 3)
    assert result.blocked_by_npc is None


def test_an_npc_on_a_closed_door_wins_over_the_door():
    # CONTRACT-v5 §7.9: you attack the thing, not the door. Unreachable in play —
    # monsters stand on passable cells — but defined rather than accidental.
    level = open_level()
    result = try_move(
        level,
        DOOR_APPROACH,
        0,
        1,
        open_doors=frozenset(),
        occupied=frozenset({DOOR_POS}),
    )
    assert result.moved is False
    assert result.blocked_by_npc == DOOR_POS
    assert result.blocked_by_door is None


def test_an_npc_on_an_open_door_still_blocks():
    level = open_level()
    result = try_move(
        level,
        DOOR_APPROACH,
        0,
        1,
        open_doors=frozenset({DOOR_POS}),
        occupied=frozenset({DOOR_POS}),
    )
    assert result.moved is False
    assert result.blocked_by_npc == DOOR_POS


def test_occupancy_is_asked_before_passability():
    # A wall cell listed as occupied reports the actor, not the wall. Nothing can
    # produce that state; the precedence is pinned so it cannot drift into being
    # order-dependent on which check happens to run first.
    level = open_level()
    wall = (2, 0)
    result = try_move(level, (2, 1), 0, -1, occupied=frozenset({wall}))
    assert result.moved is False
    assert result.blocked_by_npc == wall


def test_a_zero_delta_is_never_reported_as_actor_blocked():
    level = open_level()
    result = try_move(level, CENTRE, 0, 0, occupied=frozenset({CENTRE}))
    assert result.moved is False
    assert result.blocked_by_npc is None
    assert result.blocked_by_door is None


@pytest.mark.parametrize("bad", [2, -2, 10, -10])
def test_bad_delta_raises_before_the_occupancy_check(bad):
    level = open_level()
    with pytest.raises(ValueError):
        try_move(level, CENTRE, bad, 0, frozenset(), frozenset({(3, 3)}))


def test_occupied_is_accepted_positionally_as_the_sixth_argument():
    level = open_level()
    result = try_move(level, CENTRE, 1, 0, frozenset(), frozenset({(3, 3)}))
    assert result.blocked_by_npc == (3, 3)


def test_neither_set_is_mutated():
    level = open_level()
    doors = frozenset({DOOR_POS})
    actors = frozenset({(3, 3)})
    try_move(level, CENTRE, 1, 0, doors, actors)
    try_move(level, DOOR_APPROACH, 0, 1, doors, actors)
    assert doors == frozenset({DOOR_POS})
    assert actors == frozenset({(3, 3)})


def test_a_wall_is_still_a_plain_rejection_when_a_set_is_supplied():
    level = open_level()
    result = try_move(level, (1, 1), -1, 0, frozenset(), frozenset({(5, 5)}))
    assert result.moved is False
    assert result.blocked_by_door is None
    assert result.blocked_by_npc is None


def test_a_closed_door_is_still_a_door_rejection_when_a_set_is_supplied():
    level = open_level()
    result = try_move(level, DOOR_APPROACH, 0, 1, frozenset(), frozenset({(1, 1)}))
    assert result.moved is False
    assert result.blocked_by_door == DOOR_POS
    assert result.blocked_by_npc is None


def test_move_result_four_field_construction_and_equality():
    result = MoveResult((1, 2), False, None, (3, 4))
    assert result.blocked_by_npc == (3, 4)
    assert MoveResult((1, 2), False, None, (3, 4)) == MoveResult(
        (1, 2), False, None, (3, 4)
    )
    assert MoveResult((1, 2), False, None, (3, 4)) != MoveResult((1, 2), False, None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.blocked_by_npc = None


def test_module_still_imports_only_level_and_world():
    # CONTRACT-v5 §10 v5 leaves movement.py's import list alone: occupancy arrives
    # as coordinates, so nothing about monsters is imported to understand them.
    source = pathlib.Path(
        __import__("roguelike.movement", fromlist=["movement"]).__file__
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert {name for name in imported if name.startswith("roguelike")} == {
        "roguelike.level",
        "roguelike.world",
    }


# --------------------------------------------------------------------------
# v6 — the increment that added nothing here (CONTRACT-v6 §27.3, §10 v6)
# --------------------------------------------------------------------------
#
# Items, chests, damage types, resistances and shields are all rules about what a bump
# *does*, never about whether the step is allowed. These pin that, so a later increment
# that quietly grows a fifth collision case has to say so out loud.


def test_v6_added_no_collision_case_and_no_parameter():
    assert [f.name for f in dataclasses.fields(MoveResult)] == [
        "position",
        "moved",
        "blocked_by_door",
        "blocked_by_npc",
    ]
    parameters = list(inspect.signature(try_move).parameters)
    assert parameters == ["level", "position", "dx", "dy", "open_doors", "occupied"]


def test_a_cell_holding_a_chest_is_entered_like_any_other_floor():
    # A chest is not an obstacle: the player stands on it to open it (CONTRACT-v6 §7.18).
    # This module is never told about one, which is exactly why stepping onto it works —
    # asserted here as the property it is, rather than left to `game.py` to imply.
    level = open_level()
    result = try_move(level, (2, 1), 1, 0)
    assert result.moved is True
    assert result.blocked_by_npc is None
    assert result.blocked_by_door is None


def test_movement_names_nothing_from_items_or_loot():
    # Asked of the code, not of the text: the module docstring explains *why* v6 left this
    # file alone, so it necessarily contains the words "items" and "chest".
    source = pathlib.Path(
        __import__("roguelike.movement", fromlist=["movement"]).__file__
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"Chest", "Inventory", "Weapon", "Shield", "items", "loot"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            assert node.id not in forbidden
        elif isinstance(node, ast.Attribute):
            assert node.attr not in forbidden
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "") not in {"roguelike.items", "roguelike.loot"}
