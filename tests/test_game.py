"""Unit tests for :mod:`roguelike.game` (CONTRACT §7 as amended by CONTRACT-v2 §7).

Everything here runs headless. No test initialises curses, opens a terminal, or exercises
``run``/``play`` against a real screen — the whole rule set is reachable through the pure
:func:`roguelike.game.step`, which is the point of the split. The live smoke test belongs
to the integrator.

Levels are hand-built from character grids so every assertion is exact; a couple of tests
use :func:`roguelike.generator.generate_level` purely to confirm the module composes with
a real generated level.

The three rules these tests exist to pin, in order of how easy they are to break:

1. A rejected move consumes no turn — and now also recomputes nothing. There is exactly
   one exception: bumping a **closed door** costs a turn, because it opens the door.
2. Field of view is recomputed on exactly two transitions (an accepted move, a door
   opening) and on no others. After a rejected move, an unknown key or a quit, ``visible``
   must be the *identical* set object, not a recomputed equal one.
3. ``explored`` only ever grows, and it is never empty — :func:`new_game` computes the
   first field of view immediately.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

from roguelike import fov, game
from roguelike.game import GameState, format_status, new_game, play, run, step
from roguelike.generator import generate_level
from roguelike.keys import Command, CommandKind, translate_key
from roguelike.level import Level
from roguelike.tiles import Tile

# ---------------------------------------------------------------------------------------
# Test levels — hand-built so the assertions are exact
# ---------------------------------------------------------------------------------------

_CHAR_TO_TILE = {"#": Tile.WALL, ".": Tile.FLOOR, "+": Tile.DOOR}


def build_level(
    rows: list[str], player_start: tuple[int, int], seed: int = 0
) -> Level:
    """Build a ``Level`` from a list of equal-length ``#``/``.``/``+`` strings.

    A ``+`` is always a ``Tile.DOOR``; whether it is open is decided solely by the
    ``open_doors`` set carried in the ``GameState``, never by the grid (CONTRACT-v2 §0.6).
    """
    grid = tuple(tuple(_CHAR_TO_TILE[c] for c in row) for row in rows)
    return Level(len(rows[0]), len(rows), grid, (), player_start, seed)


#: 7x5. Note the wall at (3, 1), which blocks an eastward run along row 1.
#:
#:      0123456
#:   0  #######
#:   1  #..#..#
#:   2  #.....#
#:   3  #.....#
#:   4  #######
ROOM_ROWS = [
    "#######",
    "#..#..#",
    "#.....#",
    "#.....#",
    "#######",
]


def room_level(player_start: tuple[int, int] = (1, 1), seed: int = 0) -> Level:
    return build_level(ROOM_ROWS, player_start, seed)


#: 5x5 with a completely open 3x3 interior, so all eight neighbours of (2, 2) are FLOOR.
OPEN_ROWS = [
    "#####",
    "#...#",
    "#...#",
    "#...#",
    "#####",
]


def open_level(player_start: tuple[int, int] = (2, 2), seed: int = 0) -> Level:
    return build_level(OPEN_ROWS, player_start, seed)


#: 9x7 with a wide-open 7x5 interior — used for radius tests, where a big unobstructed
#: room makes "larger radius sees a superset" a statement about the radius and nothing
#: else.
HALL_ROWS = [
    "#########",
    "#.......#",
    "#.......#",
    "#.......#",
    "#.......#",
    "#.......#",
    "#########",
]


def hall_level(player_start: tuple[int, int] = (4, 3), seed: int = 0) -> Level:
    return build_level(HALL_ROWS, player_start, seed)


#: 9x5: two 3x3 rooms joined by a single DOOR at (4, 2). The whole of column x == 4 is
#: WALL apart from that door, so with the door closed the eastern room is completely
#: unseen, and opening it must reveal the room. This is the level the bump-to-open rules
#: are pinned on.
#:
#:      012345678
#:   0  #########
#:   1  #...#...#
#:   2  #...+...#
#:   3  #...#...#
#:   4  #########
TWO_ROOM_ROWS = [
    "#########",
    "#...#...#",
    "#...+...#",
    "#...#...#",
    "#########",
]

DOOR_CELL = (4, 2)

#: Every cell of the eastern room's floor, none of which may be visible while the door is
#: shut.
EAST_ROOM_FLOOR = frozenset(
    {(x, y) for x in (5, 6, 7) for y in (1, 2, 3)}
)


def two_room_level(player_start: tuple[int, int] = (2, 2), seed: int = 0) -> Level:
    return build_level(TWO_ROOM_ROWS, player_start, seed)


MOVE_N = Command(CommandKind.MOVE, 0, -1)
MOVE_S = Command(CommandKind.MOVE, 0, 1)
MOVE_W = Command(CommandKind.MOVE, -1, 0)
MOVE_E = Command(CommandKind.MOVE, 1, 0)
MOVE_NE = Command(CommandKind.MOVE, 1, -1)
MOVE_NW = Command(CommandKind.MOVE, -1, -1)
MOVE_SE = Command(CommandKind.MOVE, 1, 1)
MOVE_SW = Command(CommandKind.MOVE, -1, 1)

ALL_MOVES = (MOVE_N, MOVE_S, MOVE_W, MOVE_E, MOVE_NE, MOVE_NW, MOVE_SE, MOVE_SW)

QUIT = Command(CommandKind.QUIT)
UNKNOWN = Command(CommandKind.UNKNOWN)

GAME_SOURCE = Path(game.__file__).read_text(encoding="utf-8")
GAME_TREE = ast.parse(GAME_SOURCE)

STATE_FIELDS = (
    "level",
    "player",
    "explored",
    "visible",
    "open_doors",
    "turns",
    "running",
    "radius",
)


def snapshot(state: GameState) -> dict[str, object]:
    """Every field of ``state``, for a before/after purity comparison."""
    return {name: getattr(state, name) for name in STATE_FIELDS}


# ---------------------------------------------------------------------------------------
# GameState shape
# ---------------------------------------------------------------------------------------


def test_gamestate_field_order_and_defaults() -> None:
    fields = dataclasses.fields(GameState)
    assert [f.name for f in fields] == list(STATE_FIELDS)
    # The three coordinate sets precede `turns` precisely because they have no defaults.
    for field in fields[:5]:
        assert field.default is dataclasses.MISSING
    assert fields[5].default == 0
    assert fields[6].default is True
    assert fields[7].default == fov.DEFAULT_RADIUS


def test_gamestate_constructs_positionally_with_defaults() -> None:
    level = room_level()
    state = GameState(level, (1, 1), frozenset(), frozenset(), frozenset())
    assert state.level is level
    assert state.player == (1, 1)
    assert state.explored == frozenset()
    assert state.visible == frozenset()
    assert state.open_doors == frozenset()
    assert state.turns == 0
    assert state.running is True
    assert state.radius == fov.DEFAULT_RADIUS


def test_gamestate_is_frozen() -> None:
    state = new_game(room_level())
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.turns = 5  # type: ignore[misc]


@pytest.mark.parametrize("field", STATE_FIELDS)
def test_gamestate_every_field_is_frozen(field: str) -> None:
    state = new_game(room_level())
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(state, field, None)


def test_gamestate_equality_is_by_value() -> None:
    level = room_level()
    empty: frozenset[tuple[int, int]] = frozenset()
    base = GameState(level, (1, 1), empty, empty, empty, 3, True)
    assert base == GameState(level, (1, 1), empty, empty, empty, 3, True)
    assert base != GameState(level, (1, 1), empty, empty, empty, 4, True)
    assert base != GameState(level, (1, 1), empty, empty, empty, 3, False)
    assert base != GameState(level, (1, 1), empty, empty, frozenset({(4, 2)}), 3, True)
    assert base != GameState(level, (1, 1), frozenset({(1, 1)}), empty, empty, 3, True)


def test_gamestate_holds_frozensets_not_mutable_sets() -> None:
    state = new_game(two_room_level())
    for name in ("explored", "visible", "open_doors"):
        assert isinstance(getattr(state, name), frozenset)


# ---------------------------------------------------------------------------------------
# new_game — the initial state is NOT blank
# ---------------------------------------------------------------------------------------


def test_new_game_starts_at_player_start_with_zero_turns() -> None:
    level = room_level(player_start=(2, 3), seed=99)
    state = new_game(level)
    assert state.level is level
    assert state.player == level.player_start
    assert state.player == (2, 3)
    assert state.turns == 0
    assert state.running is True
    assert state.open_doors == frozenset()


def test_new_game_computes_the_first_fov_so_explored_equals_visible() -> None:
    state = new_game(room_level(player_start=(1, 1)))
    assert state.visible, "the starting cell is always seen"
    assert state.explored == state.visible
    assert state.player in state.visible


def test_new_game_visible_is_never_empty_even_standing_on_a_wall() -> None:
    # Level tolerates a non-walkable start (CONTRACT §2.2); fov guarantees the origin (F1).
    state = new_game(room_level(player_start=(0, 0)))
    assert state.player == (0, 0)
    assert (0, 0) in state.visible
    assert state.explored == state.visible


def test_new_game_matches_compute_visible_exactly() -> None:
    level = two_room_level(player_start=(2, 2))
    state = new_game(level)
    assert state.visible == fov.compute_visible(
        level, frozenset(), (2, 2), fov.DEFAULT_RADIUS
    )


def test_new_game_honours_a_custom_radius() -> None:
    level = hall_level(player_start=(4, 3))
    state = new_game(level, radius=1)
    assert state.radius == 1
    assert state.visible == frozenset({(4, 3), (3, 3), (5, 3), (4, 2), (4, 4)})
    assert state.explored == state.visible


def test_new_game_radius_zero_sees_only_the_starting_cell() -> None:
    state = new_game(hall_level(player_start=(4, 3)), radius=0)
    assert state.visible == frozenset({(4, 3)})
    assert state.explored == frozenset({(4, 3)})


def test_a_larger_radius_yields_a_superset_on_an_open_level() -> None:
    level = hall_level(player_start=(4, 3))
    seen = [new_game(level, radius=r).visible for r in range(0, 6)]
    for smaller, larger in zip(seen, seen[1:]):
        assert larger >= smaller
    assert seen[-1] > seen[0]


def test_new_game_default_radius_is_the_fov_default() -> None:
    state = new_game(room_level())
    assert state.radius == fov.DEFAULT_RADIUS
    assert new_game(room_level(), fov.DEFAULT_RADIUS) == state


def test_new_game_with_a_negative_radius_propagates_value_error() -> None:
    with pytest.raises(ValueError):
        new_game(room_level(), radius=-1)


def test_new_game_on_a_generated_level() -> None:
    level = generate_level(1234)
    state = new_game(level)
    assert state.player == level.player_start
    assert level.is_walkable(*state.player)
    assert state.turns == 0
    assert state.running is True
    assert state.open_doors == frozenset()
    assert state.explored == state.visible
    assert state.visible


def test_new_game_starts_with_every_door_closed() -> None:
    state = new_game(two_room_level())
    assert state.open_doors == frozenset()
    assert not (state.visible & EAST_ROOM_FLOOR)


# ---------------------------------------------------------------------------------------
# step — MOVE onto passable ground
# ---------------------------------------------------------------------------------------


def test_move_onto_floor_updates_position_and_consumes_a_turn() -> None:
    state = new_game(room_level(player_start=(1, 1)))
    after = step(state, MOVE_E)
    assert after.player == (2, 1)
    assert after.turns == 1
    assert after.running is True
    assert after.level is state.level
    assert after.open_doors == state.open_doors


def test_successful_move_returns_a_distinct_object() -> None:
    state = new_game(room_level())
    after = step(state, MOVE_E)
    assert after is not state
    assert isinstance(after, GameState)


def test_move_recomputes_fov_for_the_new_position() -> None:
    # radius 1 makes the recompute exact and unmistakable: the visible set is the four
    # orthogonal neighbours plus the cell itself, so it cannot help but change.
    state = new_game(hall_level(player_start=(4, 3)), radius=1)
    after = step(state, MOVE_E)
    assert after.player == (5, 3)
    assert after.visible == frozenset({(5, 3), (4, 3), (6, 3), (5, 2), (5, 4)})
    assert after.visible != state.visible
    assert after.visible == fov.compute_visible(state.level, frozenset(), (5, 3), 1)


def test_move_at_full_radius_changes_what_is_visible_when_it_must() -> None:
    # Walking into the doorway of the two-room level sees round both jambs; standing one
    # cell back, inside the western room, does not.
    state = new_game(two_room_level(player_start=(2, 2)))
    state = step(state, MOVE_E)              # (3, 2)
    opened = step(state, MOVE_E)             # bump opens the door, still at (3, 2)
    in_doorway = step(opened, MOVE_E)        # now standing on the door
    assert in_doorway.player == DOOR_CELL
    assert in_doorway.visible != opened.visible
    # Standing in the gap you see along the wall in both directions...
    assert {(5, 0), (5, 4), (6, 0), (6, 4)} <= in_doorway.visible
    assert not ({(5, 0), (5, 4), (6, 0), (6, 4)} & opened.visible)
    # ...and lose the two cells the doorjambs you now stand between used to reveal.
    assert {(4, 0), (4, 4)} <= opened.visible
    assert not ({(4, 0), (4, 4)} & in_doorway.visible)
    # Nothing is forgotten, though: explored still only grows.
    assert in_doorway.explored > opened.explored


def test_move_keeps_the_radius() -> None:
    state = new_game(hall_level(player_start=(4, 3)), radius=2)
    assert step(state, MOVE_E).radius == 2


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (MOVE_N, (2, 1)),
        (MOVE_S, (2, 3)),
        (MOVE_W, (1, 2)),
        (MOVE_E, (3, 2)),
        (MOVE_NE, (3, 1)),
        (MOVE_NW, (1, 1)),
        (MOVE_SE, (3, 3)),
        (MOVE_SW, (1, 3)),
    ],
)
def test_all_eight_deltas_land_on_the_right_cell(
    command: Command, expected: tuple[int, int]
) -> None:
    state = new_game(open_level(player_start=(2, 2)))
    after = step(state, command)
    assert after.player == expected
    assert after.turns == 1


def test_north_decreases_y_from_an_asymmetric_position() -> None:
    # x != y, so a swapped axis cannot pass silently (CONTRACT §0.1: up is dy = -1).
    state = new_game(room_level(player_start=(2, 3)))
    after = step(state, MOVE_N)
    assert after.player == (2, 2)
    assert after.player[1] < state.player[1]
    assert after.player[0] == state.player[0]


def test_south_increases_y_from_an_asymmetric_position() -> None:
    state = new_game(room_level(player_start=(1, 1)))
    after = step(state, MOVE_S)
    assert after.player == (1, 2)
    assert after.player[1] > state.player[1]
    assert after.player[0] == state.player[0]


def test_east_increases_x_and_west_decreases_it() -> None:
    state = new_game(room_level(player_start=(2, 3)))
    assert step(state, MOVE_E).player == (3, 3)
    assert step(state, MOVE_W).player == (1, 3)


def test_turns_accumulate_across_successive_moves() -> None:
    state = new_game(room_level(player_start=(1, 1)))
    for expected_turns in (1, 2, 3):
        state = step(state, MOVE_S if expected_turns == 1 else MOVE_E)
        assert state.turns == expected_turns


def test_diagonal_between_two_walls_is_allowed_no_corner_cutting_rule() -> None:
    # v1 BRIEF Q7: a diagonal is legal iff the destination is passable.
    rows = [
        "#####",
        "#..##",
        "#.#.#",
        "##..#",
        "#####",
    ]
    state = new_game(build_level(rows, (2, 1)))
    after = step(state, MOVE_SE)  # (3, 2) is FLOOR, though (3, 1) and (2, 2) are WALL
    assert after.player == (3, 2)
    assert after.turns == 1


# ---------------------------------------------------------------------------------------
# step — explored only ever grows
# ---------------------------------------------------------------------------------------


def test_explored_grows_and_never_shrinks_over_a_walk() -> None:
    state = new_game(hall_level(player_start=(1, 1)), radius=1)
    walk = (MOVE_E, MOVE_E, MOVE_S, MOVE_E, MOVE_S, MOVE_W, MOVE_N, MOVE_E, MOVE_E)
    grew_at_least_once = False
    for command in walk:
        previous = state
        state = step(state, command)
        assert state.explored >= previous.explored
        assert state.explored >= state.visible
        if state.explored > previous.explored:
            grew_at_least_once = True
    assert grew_at_least_once


def test_explored_is_exactly_the_union_of_every_visible_set_seen() -> None:
    state = new_game(hall_level(player_start=(1, 1)), radius=1)
    union = set(state.visible)
    for command in (MOVE_E, MOVE_S, MOVE_E, MOVE_N, MOVE_E, MOVE_S):
        state = step(state, command)
        union |= state.visible
        assert state.explored == frozenset(union)


def test_ground_once_seen_is_never_forgotten() -> None:
    state = new_game(hall_level(player_start=(1, 1)), radius=1)
    first_sight = state.visible
    for _ in range(5):
        state = step(state, MOVE_E)
    # Long since out of view at radius 1, but still remembered.
    assert not (first_sight <= state.visible)
    assert first_sight <= state.explored


def test_explored_always_contains_visible_on_a_generated_level() -> None:
    state = new_game(generate_level(2026, 40, 18))
    for key in "jjllkkhhnnbb":
        state = step(state, translate_key(key))
        assert state.explored >= state.visible


# ---------------------------------------------------------------------------------------
# step — MOVE into a wall: THE rule. Every field must be untouched.
# ---------------------------------------------------------------------------------------


def test_move_into_a_wall_changes_absolutely_nothing() -> None:
    state = new_game(room_level(player_start=(2, 1)))
    state = step(state, MOVE_W)  # get a turn and some explored ground on the clock
    assert state.player == (1, 1)
    before = snapshot(state)

    after = step(state, MOVE_W)  # (0, 1) is the border WALL
    assert after is state
    for name, value in before.items():
        assert getattr(after, name) == value
    # Identity, not just equality: nothing was recomputed and then found to be equal.
    assert after.visible is state.visible
    assert after.explored is state.explored
    assert after.open_doors is state.open_doors


def test_move_into_a_wall_from_a_fresh_game_leaves_turns_at_zero() -> None:
    state = new_game(room_level(player_start=(1, 1)))
    after = step(state, MOVE_N)  # (1, 0) is the border WALL
    assert after.player == (1, 1)
    assert after.turns == 0
    assert after.visible is state.visible


@pytest.mark.parametrize("command", [MOVE_N, MOVE_W, MOVE_NW, MOVE_NE, MOVE_SW])
def test_several_blocked_directions_all_cost_no_turn(command: Command) -> None:
    state = new_game(room_level(player_start=(1, 1)))
    after = step(state, command)
    assert after.player == (1, 1)
    assert after.turns == 0
    assert after.visible is state.visible


def test_repeated_blocked_moves_never_move_and_never_raise() -> None:
    state = new_game(room_level(player_start=(1, 1)))
    original = state
    for _ in range(10):
        state = step(state, MOVE_W)
    assert state is original
    assert state.player == (1, 1)
    assert state.turns == 0
    assert state.running is True


def test_diagonal_into_a_wall_costs_no_turn() -> None:
    # (3, 1) is WALL; from (2, 2) the north-east diagonal targets it.
    state = new_game(room_level(player_start=(2, 2)))
    after = step(state, MOVE_NE)
    assert after.player == (2, 2)
    assert after.turns == 0
    assert after.visible is state.visible


# ---------------------------------------------------------------------------------------
# step — MOVE off the map edge
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("command", ALL_MOVES)
def test_move_off_the_map_edge_from_a_corner_cell_is_a_no_op(command: Command) -> None:
    # A 3x3 all-floor level: every direction from (0, 0) either leaves the map or lands
    # inside it, and neither may raise.
    state = new_game(build_level(["...", "...", "..."], (0, 0)))
    after = step(state, command)
    assert after.running is True
    if command.dx < 0 or command.dy < 0:
        assert after is state
        assert after.player == (0, 0)
        assert after.turns == 0
        assert after.visible is state.visible


@pytest.mark.parametrize(
    ("start", "command"),
    [
        ((0, 1), MOVE_W),
        ((2, 1), MOVE_E),
        ((1, 0), MOVE_N),
        ((1, 2), MOVE_S),
        ((0, 0), MOVE_NW),
        ((2, 2), MOVE_SE),
    ],
)
def test_stepping_off_every_edge_leaves_every_field_alone(
    start: tuple[int, int], command: Command
) -> None:
    state = new_game(build_level(["...", "...", "..."], start))
    before = snapshot(state)
    after = step(state, command)
    assert after.player == start
    for name, value in before.items():
        assert getattr(after, name) == value


def test_move_off_the_edge_does_not_raise_on_a_one_by_one_level() -> None:
    state = new_game(build_level(["."], (0, 0)))
    for command in ALL_MOVES:
        after = step(state, command)
        assert after.player == (0, 0)
        assert after.turns == 0
        assert after.visible == frozenset({(0, 0)})


# ---------------------------------------------------------------------------------------
# step — bump-to-open. The most important behaviour in this task.
# ---------------------------------------------------------------------------------------


def _at_the_door() -> GameState:
    """A game standing at (3, 2), one step west of the closed door at (4, 2)."""
    state = new_game(two_room_level(player_start=(2, 2)))
    state = step(state, MOVE_E)
    assert state.player == (3, 2)
    assert state.turns == 1
    return state


def test_the_room_beyond_a_closed_door_is_not_visible_to_begin_with() -> None:
    state = _at_the_door()
    assert state.open_doors == frozenset()
    assert not (state.visible & EAST_ROOM_FLOOR)
    assert not (state.explored & EAST_ROOM_FLOOR)
    # The door's own face is visible — you can see what you are about to bump into.
    assert DOOR_CELL in state.visible


def test_bumping_a_closed_door_opens_it_without_moving_and_costs_a_turn() -> None:
    state = _at_the_door()
    after = step(state, MOVE_E)

    assert after.player == state.player == (3, 2), "opening a door does not move you"
    assert after.turns == state.turns + 1, "opening a door costs a turn"
    assert after.open_doors == frozenset({DOOR_CELL})
    assert after.running is True
    assert after.level is state.level
    assert after is not state


def test_bumping_a_closed_door_recomputes_fov_and_reveals_the_room_beyond() -> None:
    state = _at_the_door()
    after = step(state, MOVE_E)

    assert after.visible != state.visible
    assert after.visible > state.visible
    assert EAST_ROOM_FLOOR <= after.visible, "the whole room beyond must come into view"
    assert EAST_ROOM_FLOOR <= after.explored
    assert after.visible == fov.compute_visible(
        state.level, frozenset({DOOR_CELL}), (3, 2), state.radius
    )


def test_opening_a_door_adds_only_that_door() -> None:
    #: Two doors on the same wall run would break G9d; use two separate walls instead.
    rows = [
        "#######",
        "#..#..#",
        "#..+..#",
        "#..#..#",
        "#..#..#",
        "#..+..#",
        "#######",
    ]
    state = new_game(build_level(rows, (2, 2)))
    assert state.open_doors == frozenset()

    first = step(state, MOVE_E)  # bumps (3, 2)
    assert len(first.open_doors) == len(state.open_doors) + 1
    assert first.open_doors == frozenset({(3, 2)})

    walked = first
    for command in (MOVE_S, MOVE_S, MOVE_S):
        walked = step(walked, command)
    assert walked.player == (2, 5)
    second = step(walked, MOVE_E)  # bumps (3, 5)
    assert len(second.open_doors) == len(walked.open_doors) + 1
    assert second.open_doors == frozenset({(3, 2), (3, 5)})


def test_a_second_move_in_the_same_direction_walks_onto_the_opened_door() -> None:
    state = _at_the_door()
    opened = step(state, MOVE_E)
    walked = step(opened, MOVE_E)

    assert walked.player == DOOR_CELL
    assert walked.turns == state.turns + 2
    assert walked.open_doors == frozenset({DOOR_CELL})


def test_bumping_a_door_that_is_already_open_moves_normally() -> None:
    level = two_room_level(player_start=(3, 2))
    state = GameState(
        level,
        (3, 2),
        explored=frozenset(),
        visible=frozenset(),
        open_doors=frozenset({DOOR_CELL}),
        turns=4,
    )
    after = step(state, MOVE_E)
    assert after.player == DOOR_CELL, "an open door is not blocked"
    assert after.turns == 5
    assert after.open_doors == frozenset({DOOR_CELL}), "nothing new was opened"


def test_walking_through_an_opened_door_into_the_far_room() -> None:
    state = _at_the_door()
    for command in (MOVE_E, MOVE_E, MOVE_E, MOVE_E):
        state = step(state, command)
    # bump (turn 2), onto the door (3), (5, 2) (4), (6, 2) (5)
    assert state.player == (6, 2)
    assert state.turns == 5
    assert state.open_doors == frozenset({DOOR_CELL})


def test_a_door_never_closes_again() -> None:
    state = _at_the_door()
    state = step(state, MOVE_E)  # open it
    for command in (MOVE_E, MOVE_E, MOVE_W, MOVE_W, MOVE_N, MOVE_S, UNKNOWN, MOVE_W):
        state = step(state, command)
        assert DOOR_CELL in state.open_doors


def test_bumping_a_door_from_a_stopped_state_does_nothing() -> None:
    state = _at_the_door()
    stopped = step(state, QUIT)
    after = step(stopped, MOVE_E)
    assert after == stopped
    assert after.open_doors == frozenset()
    assert after.turns == stopped.turns


def test_a_wall_bump_and_a_door_bump_differ_in_exactly_the_turn_count() -> None:
    state = _at_the_door()
    wall = step(state, MOVE_NE)  # (4, 1) is WALL
    door = step(state, MOVE_E)   # (4, 2) is a closed DOOR
    assert wall.turns == state.turns
    assert door.turns == state.turns + 1
    assert wall.player == door.player == state.player


# ---------------------------------------------------------------------------------------
# step — QUIT
# ---------------------------------------------------------------------------------------


def test_quit_clears_running_and_leaves_everything_else_alone() -> None:
    state = _at_the_door()
    state = step(state, MOVE_E)  # open a door so open_doors is non-trivial
    before = snapshot(state)

    after = step(state, QUIT)
    assert after.running is False
    assert after.turns == before["turns"]
    assert after.player == before["player"]
    assert after.level is state.level
    assert after.visible is state.visible
    assert after.explored is state.explored
    assert after.open_doors is state.open_doors


def test_quit_from_a_fresh_game() -> None:
    state = new_game(room_level())
    after = step(state, QUIT)
    assert after.running is False
    assert after.turns == 0
    assert after.player == state.player


def test_quit_returns_a_distinct_object() -> None:
    state = new_game(room_level())
    assert step(state, QUIT) is not state


# ---------------------------------------------------------------------------------------
# step — UNKNOWN
# ---------------------------------------------------------------------------------------


def test_unknown_leaves_the_state_entirely_unchanged() -> None:
    state = step(_at_the_door(), MOVE_E)
    after = step(state, UNKNOWN)
    assert after == state
    assert after.visible is state.visible
    assert after.explored is state.explored
    assert after.open_doors is state.open_doors
    assert after.turns == state.turns
    assert after.running is True


def test_unknown_repeated_never_accrues_turns_or_recomputes_fov() -> None:
    state = new_game(room_level())
    original = state
    for _ in range(5):
        state = step(state, UNKNOWN)
    assert state is original
    assert state.turns == 0
    assert state.running is True


def test_numpad_five_reaches_step_as_unknown_and_costs_nothing() -> None:
    # v1 BRIEF Q8: there is no wait command, and UNKNOWN must not consume a turn.
    command = translate_key("5")
    assert command.kind is CommandKind.UNKNOWN
    state = new_game(room_level())
    assert step(state, command) is state


# ---------------------------------------------------------------------------------------
# step — a stopped game is inert
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("command", [*ALL_MOVES, QUIT, UNKNOWN])
def test_stopped_state_is_returned_equal_for_every_command(command: Command) -> None:
    level = two_room_level(player_start=(3, 2))
    state = GameState(
        level,
        (3, 2),
        explored=frozenset({(3, 2), (2, 2)}),
        visible=frozenset({(3, 2)}),
        open_doors=frozenset(),
        turns=9,
        running=False,
    )
    after = step(state, command)
    assert after == state
    assert after is state
    assert after.player == (3, 2)
    assert after.turns == 9
    assert after.running is False
    assert after.open_doors == frozenset()


def test_a_legal_move_after_quitting_does_nothing() -> None:
    state = step(new_game(room_level(player_start=(1, 1))), QUIT)
    after = step(state, MOVE_E)  # (2, 1) is FLOOR and would otherwise be legal
    assert after == state
    assert after.player == (1, 1)
    assert after.turns == 0
    assert after.running is False


def test_quitting_twice_is_idempotent() -> None:
    state = step(new_game(room_level()), QUIT)
    assert step(state, QUIT) == state


# ---------------------------------------------------------------------------------------
# step — purity
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command", [MOVE_E, MOVE_N, MOVE_W, MOVE_SE, QUIT, UNKNOWN]
)
def test_step_never_mutates_its_input(command: Command) -> None:
    level = two_room_level(player_start=(3, 2), seed=17)
    state = GameState(
        level,
        (3, 2),
        explored=frozenset({(3, 2), (2, 2), (3, 1)}),
        visible=frozenset({(3, 2), (2, 2)}),
        open_doors=frozenset(),
        turns=3,
        running=True,
        radius=6,
    )
    before = snapshot(state)
    level_snapshot = copy.deepcopy(level)

    step(state, command)

    for name, value in before.items():
        assert getattr(state, name) == value
    assert state.level is before["level"]
    assert state.visible is before["visible"]
    assert state.explored is before["explored"]
    assert state.open_doors is before["open_doors"]
    assert level == level_snapshot


def test_step_does_not_mutate_the_frozensets_it_was_handed() -> None:
    explored = frozenset({(3, 2)})
    visible = frozenset({(3, 2)})
    open_doors: frozenset[tuple[int, int]] = frozenset()
    state = GameState(
        two_room_level(player_start=(3, 2)),
        (3, 2),
        explored,
        visible,
        open_doors,
        turns=0,
    )
    after = step(state, MOVE_E)  # opens the door — the transition that grows two sets

    assert explored == frozenset({(3, 2)})
    assert visible == frozenset({(3, 2)})
    assert open_doors == frozenset()
    assert after.open_doors == frozenset({DOOR_CELL})
    assert after.open_doors is not open_doors
    assert after.explored is not explored
    assert after.visible is not visible


def test_step_does_not_mutate_the_command() -> None:
    command = Command(CommandKind.MOVE, 1, 0)
    step(new_game(room_level()), command)
    assert command == Command(CommandKind.MOVE, 1, 0)


def test_step_is_deterministic_for_the_same_inputs() -> None:
    state = _at_the_door()
    assert step(state, MOVE_E) == step(state, MOVE_E)
    assert step(state, MOVE_N) == step(state, MOVE_N)


def test_step_shares_the_level_object_rather_than_copying_it() -> None:
    state = _at_the_door()
    assert step(state, MOVE_E).level is state.level
    assert step(state, MOVE_W).level is state.level
    assert step(state, QUIT).level is state.level


@pytest.mark.parametrize(
    ("command", "changes"),
    [(MOVE_W, True), (MOVE_E, True), (QUIT, True), (MOVE_NE, False), (UNKNOWN, False)],
)
def test_the_returned_object_is_distinct_exactly_when_something_changed(
    command: Command, changes: bool
) -> None:
    # From (3, 2): west is floor, east is a closed door (both change something),
    # north-east is a wall, and UNKNOWN is a no-op.
    state = _at_the_door()
    after = step(state, command)
    assert (after is not state) is changes


# ---------------------------------------------------------------------------------------
# step — FOV is recomputed on exactly two transitions and no others
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command", [MOVE_NE, UNKNOWN, QUIT]
)
def test_fov_is_not_recomputed_on_a_non_transition(command: Command) -> None:
    # From (3, 2) in the two-room level, (4, 1) to the north-east is a wall.
    state = _at_the_door()
    after = step(state, command)
    assert after.visible is state.visible
    assert after.explored is state.explored


def test_fov_is_recomputed_on_an_accepted_move() -> None:
    state = new_game(hall_level(player_start=(2, 2)), radius=1)
    after = step(state, MOVE_E)
    assert after.visible is not state.visible
    assert after.visible != state.visible


def test_fov_is_recomputed_on_a_door_opening() -> None:
    state = _at_the_door()
    after = step(state, MOVE_E)
    assert after.visible is not state.visible
    assert after.visible != state.visible


def test_exactly_the_turn_consuming_transitions_recompute_fov() -> None:
    state = _at_the_door()
    for command in (*ALL_MOVES, QUIT, UNKNOWN):
        after = step(state, command)
        recomputed = after.visible is not state.visible
        consumed_a_turn = after.turns != state.turns
        assert recomputed == consumed_a_turn


# ---------------------------------------------------------------------------------------
# step — scripted sequences
# ---------------------------------------------------------------------------------------


def test_scripted_sequence_with_a_blocked_move_and_a_door_opening() -> None:
    #      012345678
    #   0  #########
    #   1  #...#...#      start at (2, 2)
    #   2  #...+...#
    #   3  #...#...#
    #   4  #########
    state = new_game(two_room_level(player_start=(2, 2)))
    script = [
        MOVE_N,   # (2,2) -> (2,1)          turn 1
        MOVE_E,   # (2,1) -> (3,1)          turn 2
        MOVE_E,   # (4,1) is WALL           BLOCKED, no turn
        MOVE_S,   # (3,1) -> (3,2)          turn 3
        MOVE_E,   # (4,2) closed DOOR       OPENED, turn 4, no movement
        MOVE_E,   # (3,2) -> (4,2)          turn 5
        MOVE_E,   # (4,2) -> (5,2)          turn 6
        UNKNOWN,  # no-op                   no turn
    ]
    for command in script:
        state = step(state, command)

    assert state.player == (5, 2)
    assert state.turns == 6
    assert state.running is True
    assert state.open_doors == frozenset({DOOR_CELL})
    # The turn count matches neither the command count nor the number of moves made.
    assert state.turns != len(script)
    assert state.turns != 5  # five commands actually moved the player
    assert state.explored >= state.visible
    assert EAST_ROOM_FLOOR <= state.explored


def test_scripted_sequence_ending_in_quit() -> None:
    state = new_game(room_level(player_start=(1, 1)))
    for command in (MOVE_E, MOVE_S, MOVE_W, QUIT, MOVE_E, MOVE_S):
        state = step(state, command)
    assert state.player == (1, 2)
    assert state.turns == 3
    assert state.running is False


def test_walking_a_full_loop_returns_to_the_start_with_the_right_turn_count() -> None:
    state = new_game(room_level(player_start=(1, 2)))
    for command in (MOVE_S, MOVE_E, MOVE_N, MOVE_W):
        state = step(state, command)
    assert state.player == (1, 2)
    assert state.turns == 4


def test_keys_translate_into_a_playable_sequence_without_curses() -> None:
    # End to end through the input layer: raw key codes in, final state out.
    # l opens the door at (4,2) t1 ; l steps onto it t2 ; l -> (5,2) t3 ; q stops the game.
    state = new_game(two_room_level(player_start=(3, 2)))
    for key in "lllq":
        state = step(state, translate_key(key))
    assert state.running is False
    assert state.player == (5, 2)
    assert state.turns == 3
    assert state.open_doors == frozenset({DOOR_CELL})


# ---------------------------------------------------------------------------------------
# format_status — unchanged from v1
# ---------------------------------------------------------------------------------------


def _status_state(
    player: tuple[int, int], turns: int, seed: int = 0
) -> GameState:
    return GameState(
        room_level(seed=seed),
        player,
        frozenset(),
        frozenset(),
        frozenset(),
        turns=turns,
    )


def test_format_status_exact_literal() -> None:
    state = _status_state((3, 1), 7, seed=4242)
    assert format_status(state) == "Seed: 4242  Pos: (3, 1)  Turns: 7  [q] quit"


def test_format_status_on_a_fresh_game() -> None:
    state = new_game(room_level(player_start=(2, 3), seed=0))
    assert format_status(state) == "Seed: 0  Pos: (2, 3)  Turns: 0  [q] quit"


def test_format_status_with_a_negative_seed() -> None:
    assert format_status(_status_state((1, 1), 0, seed=-9)) == (
        "Seed: -9  Pos: (1, 1)  Turns: 0  [q] quit"
    )


def test_format_status_mentions_no_fov_or_door_information() -> None:
    # CONTRACT-v2 §7 retains the v1 format *exactly*. No new fields.
    state = step(_at_the_door(), MOVE_E)
    status = format_status(state)
    assert status == "Seed: 0  Pos: (3, 2)  Turns: 2  [q] quit"
    for word in ("Doors", "doors", "Seen", "seen", "Visible", "visible", "Explored"):
        assert word not in status


def test_format_status_is_identical_before_and_after_seeing_more_ground() -> None:
    # Opening a door changes `visible`, `explored` and `open_doors` but bumps only the
    # turn counter in the status line.
    state = _at_the_door()
    opened = step(state, MOVE_E)
    assert opened.visible != state.visible
    assert format_status(opened) == format_status(state).replace("Turns: 1", "Turns: 2")


def test_format_status_returns_a_plain_str_unpadded() -> None:
    status = format_status(new_game(room_level(seed=1)))
    assert type(status) is str
    assert status == status.strip()  # padding is the renderer's job (CONTRACT-v2 §4)


def test_format_status_uses_two_spaces_between_fields() -> None:
    status = format_status(_status_state((1, 2), 3, seed=5))
    assert "  Pos:" in status
    assert "  Turns:" in status
    assert "  [q] quit" in status
    assert status.endswith("[q] quit")
    assert status.startswith("Seed: ")


def test_format_status_reports_x_first_then_y() -> None:
    assert "Pos: (4, 2)" in format_status(_status_state((4, 2), 0, seed=1))


def test_format_status_reflects_live_values_after_a_move() -> None:
    state = new_game(room_level(player_start=(1, 1), seed=8))
    before = format_status(state)
    after = format_status(step(state, MOVE_E))
    assert before == "Seed: 8  Pos: (1, 1)  Turns: 0  [q] quit"
    assert after == "Seed: 8  Pos: (2, 1)  Turns: 1  [q] quit"
    assert before != after


def test_format_status_is_unchanged_after_a_blocked_move() -> None:
    state = new_game(room_level(player_start=(1, 1), seed=8))
    assert format_status(step(state, MOVE_W)) == format_status(state)


def test_format_status_seed_comes_from_the_level() -> None:
    level = generate_level(777, 30, 15)
    assert format_status(new_game(level)).startswith("Seed: 777  ")


def test_format_status_does_not_mutate_the_state() -> None:
    state = _status_state((1, 1), 2, seed=3)
    format_status(state)
    assert state == _status_state((1, 1), 2, seed=3)


# ---------------------------------------------------------------------------------------
# Module hygiene — asserted by reading the source (CONTRACT §0.3, §7, §10)
# ---------------------------------------------------------------------------------------


def _enclosing_function(tree: ast.Module, target: ast.AST) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for child in ast.walk(node):
                if child is target:
                    return node.name
    return None


def _curses_calls(tree: ast.Module, attr: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "curses"
    ]


def _function(name: str) -> ast.FunctionDef:
    return next(
        node
        for node in GAME_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_curses_wrapper_is_called_only_inside_play() -> None:
    calls = _curses_calls(GAME_TREE, "wrapper")
    assert len(calls) == 1
    assert _enclosing_function(GAME_TREE, calls[0]) == "play"


@pytest.mark.parametrize(
    "name",
    ["initscr", "newwin", "endwin", "setupterm", "start_color", "init_pair", "newpad"],
)
def test_no_other_terminal_mutating_curses_call_appears(name: str) -> None:
    assert _curses_calls(GAME_TREE, name) == []


def test_no_curses_call_happens_at_module_level() -> None:
    for node in GAME_TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            continue
        for child in ast.walk(node):
            assert not (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "curses"
            )


@pytest.mark.parametrize("name", ["addstr", "addch", "insstr", "addnstr"])
def test_module_contains_no_direct_screen_writes(name: str) -> None:
    assert name not in GAME_SOURCE


def test_module_source_never_uses_y_x_ordering_helpers() -> None:
    # The (y, x) inversion lives solely in render.draw (CONTRACT §0.1).
    assert "getmaxyx" not in GAME_SOURCE


def test_import_set_matches_the_contract_import_graph() -> None:
    imported: set[str] = set()
    for node in ast.walk(GAME_TREE):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports"
            imported.add(node.module or "")
            if node.module == "roguelike":
                imported.update(f"roguelike.{a.name}" for a in node.names)

    roguelike_imports = {m for m in imported if m.startswith("roguelike")}
    assert roguelike_imports <= {
        "roguelike",
        "roguelike.level",
        "roguelike.keys",
        "roguelike.movement",
        "roguelike.render",
        "roguelike.generator",
        "roguelike.fov",
        "roguelike.world",
    }
    assert "roguelike.game" not in roguelike_imports
    assert "roguelike.style" not in roguelike_imports
    assert "roguelike.tiles" not in roguelike_imports
    assert imported - roguelike_imports <= {"__future__", "curses", "dataclasses"}


def test_step_does_not_touch_the_renderer_or_curses() -> None:
    for name in ("step", "_take_turn", "new_game", "format_status"):
        node = _function(name)
        for child in ast.walk(node):
            if isinstance(child, ast.Name):
                assert child.id not in {"render", "curses", "draw", "render_to_cells"}
            if isinstance(child, ast.Attribute):
                assert child.attr not in {"draw", "render_to_cells", "init_colors"}


def test_the_renderer_is_reached_only_from_run() -> None:
    users = {
        _enclosing_function(GAME_TREE, node)
        for node in ast.walk(GAME_TREE)
        if isinstance(node, ast.Name) and node.id == "render"
    }
    assert users <= {"run"}


def test_step_does_not_reimplement_collision_keys_or_visibility() -> None:
    node = _function("step")
    called = {
        child.func.id
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }
    assert "try_move" in called, "collision is delegated, not re-derived"
    assert "is_walkable" not in called
    assert "is_passable" not in called
    assert "translate_key" not in called


def test_run_contains_no_game_rules_of_its_own() -> None:
    node = _function("run")
    called_names = {
        child.func.id
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }
    called_attrs = {
        child.func.attr
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
    }
    # It delegates rather than re-deriving anything.
    assert {"step", "new_game", "format_status", "translate_key"} <= called_names
    assert {"render_to_cells", "draw", "init_colors"} <= called_attrs
    assert "try_move" not in called_names
    assert "compute_visible" not in called_names
    assert "compute_visible" not in called_attrs
    assert "is_walkable" not in called_names


def test_init_colors_is_called_exactly_once_and_only_in_run() -> None:
    calls = [
        node
        for node in ast.walk(GAME_TREE)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "init_colors"
    ]
    assert len(calls) == 1
    assert _enclosing_function(GAME_TREE, calls[0]) == "run"


def test_public_surface_is_exactly_the_contract_surface() -> None:
    assert game.__all__ == [
        "GameState",
        "new_game",
        "step",
        "format_status",
        "run",
        "play",
    ]
    public = {
        name
        for name in vars(game)
        if not name.startswith("_") and name not in {"annotations"}
    }
    # Imported helpers are visible as module attributes; the declared surface is __all__.
    assert set(game.__all__) <= public


def test_signatures_match_the_contract() -> None:
    assert (
        str(inspect.signature(new_game))
        == "(level: 'Level', radius: 'int' = 20) -> 'GameState'"
    )
    assert inspect.signature(new_game).parameters["radius"].default == fov.DEFAULT_RADIUS
    assert (
        str(inspect.signature(step))
        == "(state: 'GameState', command: 'Command') -> 'GameState'"
    )
    assert str(inspect.signature(format_status)) == "(state: 'GameState') -> 'str'"
    assert str(inspect.signature(run)) == "(stdscr, level: 'Level') -> 'None'"
    assert (
        str(inspect.signature(play))
        == "(seed: 'int', width: 'int' = 80, height: 'int' = 22) -> 'None'"
    )


def test_importing_the_module_does_not_initialise_a_terminal() -> None:
    # curses.LINES/COLS only exist once initscr() has run.
    code = (
        "import curses, roguelike.game; "
        "print(hasattr(curses, 'LINES'), hasattr(curses, 'COLS'))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        cwd=str(Path(game.__file__).resolve().parent.parent),
        check=True,
    )
    assert result.stdout.strip() == "False False"


def test_module_has_future_annotations_import() -> None:
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in GAME_TREE.body
    )


def test_no_third_party_imports() -> None:
    stdlib_or_project = {"__future__", "curses", "dataclasses", "roguelike"}
    for node in ast.walk(GAME_TREE):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in stdlib_or_project
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] in stdlib_or_project


def test_no_out_of_scope_surface_was_added() -> None:
    # No message log, menus, pause, score, game-over, save/load, entities, and — new in
    # v2 — no explicit open/close-door command beyond bump-to-open.
    field_names = {f.name for f in dataclasses.fields(GameState)}
    for name in (
        "messages",
        "message_log",
        "score",
        "game_over",
        "paused",
        "entities",
        "save",
        "load",
        "menu",
        "open_door",
        "close_door",
        "toggle_door",
    ):
        assert not hasattr(game, name)
        assert name not in field_names


def test_no_open_or_close_command_kind_was_invented() -> None:
    assert {kind.name for kind in CommandKind} == {"MOVE", "QUIT", "UNKNOWN"}
    for name in ("OPEN", "CLOSE", "WAIT"):
        assert name not in GAME_SOURCE


def test_run_and_play_exist_and_are_functions() -> None:
    assert callable(run)
    assert callable(play)


# ---------------------------------------------------------------------------------------
# Composition with a real generated level — still headless
# ---------------------------------------------------------------------------------------


def test_a_generated_level_can_be_walked_through_step() -> None:
    level = generate_level(2026, 40, 18)
    state = new_game(level)
    moved_at_least_once = False
    for command in ALL_MOVES:
        after = step(state, command)
        if after.player != state.player:
            moved_at_least_once = True
            assert after.turns == state.turns + 1
            assert level.is_walkable(*after.player)
        elif after.open_doors != state.open_doors:
            assert after.turns == state.turns + 1  # bumped a door open
        else:
            assert after.turns == state.turns
            assert after.visible is state.visible
    assert moved_at_least_once, "the start cell should have at least one walkable neighbour"


def test_turn_count_equals_accepted_moves_plus_doors_opened() -> None:
    level = generate_level(31337, 40, 18)
    state = new_game(level)
    accepted = 0
    opened = 0
    for key in "jjkkhhllyybbuunnjjllhhkk":
        before = state
        state = step(state, translate_key(key))
        if state.player != before.player:
            accepted += 1
        elif state.open_doors != before.open_doors:
            opened += 1
    assert state.turns == accepted + opened
    assert state.running is True


def test_a_generated_level_never_shrinks_explored_over_a_long_walk() -> None:
    state = new_game(generate_level(4242, 60, 20))
    for key in "jjjlllkkkhhhnnnbbbuuuyyy":
        previous = state
        state = step(state, translate_key(key))
        assert state.explored >= previous.explored
        assert state.explored >= state.visible
        assert state.open_doors >= previous.open_doors


def test_every_open_door_on_a_generated_level_was_a_door_tile() -> None:
    state = new_game(generate_level(555, 60, 20))
    for key in "lllljjjjhhhhkkkkllll":
        state = step(state, translate_key(key))
    for x, y in state.open_doors:
        assert state.level.tile_at(x, y) is Tile.DOOR
