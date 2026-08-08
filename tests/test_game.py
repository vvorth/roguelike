"""Unit tests for :mod:`roguelike.game` (CONTRACT-v3 §7, amending v1 §7 and v2 §7).

Everything here runs headless. No test initialises curses, opens a terminal, or exercises
``run``/``play`` against a real screen — the whole rule set is reachable through the pure
:func:`roguelike.game.step`, which is the point of the split. The live smoke test belongs
to the integrator.

Levels come from two places. Hand-built character grids carry every assertion that wants
to be exact: ``#`` wall, ``.`` floor, ``+`` door, ``<`` up-staircase, ``>`` down-staircase,
with the ``Level``'s stair fields derived from the glyphs. Real generated dungeons carry
the multi-level tests, because descending *generates*, and a chain of hand-built levels
would be testing the test helper rather than the game.

The rules these tests exist to pin, in order of how easy they are to break:

1. **A rejected move consumes no turn** — v1's headline rule — and now also changes
   nothing at all, ``events`` included, so the last message stays on screen. The
   exceptions that *do* cost a turn without moving you are bumping a closed door and
   taking a staircase.
2. **Levels persist.** Climb back up and the level is as you left it: same fog, same open
   doors, the same ``Level`` object. That is the single most important test in this file
   (:func:`test_persistence_round_trip_preserves_fog_and_doors_exactly`).
3. **Levels line up.** The player's ``(x, y)`` does not change across a descent; the world
   changes underneath them.
4. **No wording lives in this module.** ``step`` emits ``Event`` values;
   :func:`roguelike.events.message_for` turns them into sentences.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import inspect
import subprocess
import sys
from collections import deque
from pathlib import Path

import pytest

from roguelike import dungeon, events, fov, game, world
from roguelike.events import Event, EventKind
from roguelike.game import (
    GameState,
    LevelState,
    format_stats,
    format_status_right,
    new_game,
    play,
    run,
    step,
)
from roguelike.keys import Command, CommandKind, translate_key
from roguelike.level import Level
from roguelike.tiles import Tile

# ---------------------------------------------------------------------------------------
# Test levels — hand-built so the assertions are exact
# ---------------------------------------------------------------------------------------

_CHAR_TO_TILE = {
    "#": Tile.WALL,
    ".": Tile.FLOOR,
    "+": Tile.DOOR,
    "<": Tile.STAIRS_UP,
    ">": Tile.STAIRS_DOWN,
}


def build_level(
    rows: list[str],
    player_start: tuple[int, int] | None = None,
    seed: int = 0,
    depth: int = 1,
) -> Level:
    """Build a ``Level`` from a list of equal-length glyph strings.

    The stair fields are read off the glyphs: ``<`` becomes ``stairs_up`` and every ``>``
    becomes an entry of ``stairs_down``, so a hand-built level is stair-consistent the way
    a generated one is (G18). ``player_start`` defaults to the up-staircase, which is what
    G17 pins it to on a generated level; a level with no ``<`` and no explicit start is a
    programming error here rather than a silent ``(0, 0)``.

    A ``+`` is always a ``Tile.DOOR``; whether it is open is decided solely by the
    ``open_doors`` set carried in the ``GameState``, never by the grid (CONTRACT-v2 §0.6).
    """
    grid = tuple(tuple(_CHAR_TO_TILE[c] for c in row) for row in rows)
    stairs_up: tuple[int, int] | None = None
    stairs_down: list[tuple[int, int]] = []
    for y, row in enumerate(rows):
        for x, char in enumerate(row):
            if char == "<":
                stairs_up = (x, y)
            elif char == ">":
                stairs_down.append((x, y))
    if player_start is None:
        assert stairs_up is not None, "give a player_start or put a '<' on the map"
        player_start = stairs_up
    return Level(
        len(rows[0]),
        len(rows),
        grid,
        (),
        player_start,
        seed,
        stairs_up,
        tuple(stairs_down),
        depth,
    )


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
EAST_ROOM_FLOOR = frozenset({(x, y) for x in (5, 6, 7) for y in (1, 2, 3)})


def two_room_level(player_start: tuple[int, int] = (2, 2), seed: int = 0) -> Level:
    return build_level(TWO_ROOM_ROWS, player_start, seed)


#: 20x7, both staircases, a door between them — and, unlike the levels above, **large
#: enough for the generator**, so a real descent off ``>`` at (16, 3) can build the level
#: below at the same dimensions with the up-staircase anchored there. The generator needs
#: MIN_ROOM_SIZE + 2 == 6 on each axis, and (16, 3) is inside the anchorable range
#: 2 <= x <= 17, 2 <= y <= 4.
#:
#:      01234567890123456789
#:   0  ####################
#:   1  #........#.........#
#:   2  #........#.........#
#:   3  #..<.....+......>..#
#:   4  #........#.........#
#:   5  #........#.........#
#:   6  ####################
STAIRS_ROWS = [
    "####################",
    "#........#.........#",
    "#........#.........#",
    "#..<.....+......>..#",
    "#........#.........#",
    "#........#.........#",
    "####################",
]

UP_CELL = (3, 3)
DOWN_CELL = (16, 3)
STAIRS_DOOR = (9, 3)
STAIRS_SIZE = (20, 7)


def stairs_level(
    player_start: tuple[int, int] | None = None, seed: int = 0, depth: int = 1
) -> Level:
    return build_level(STAIRS_ROWS, player_start, seed, depth)


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
DESCEND = Command(CommandKind.DESCEND)
ASCEND = Command(CommandKind.ASCEND)

ALL_KINDS = (*ALL_MOVES, QUIT, UNKNOWN, DESCEND, ASCEND)

#: Small but legal dimensions for the generated-dungeon tests, so descending stays quick.
SMALL = (40, 18)

GAME_SOURCE = Path(game.__file__).read_text(encoding="utf-8")
GAME_TREE = ast.parse(GAME_SOURCE)

STATE_FIELDS = (
    "master_seed",
    "depth",
    "level",
    "player",
    "explored",
    "visible",
    "open_doors",
    "saved",
    "turns",
    "running",
    "radius",
    "events",
    "outcome",
)


def snapshot(state: GameState) -> dict[str, object]:
    """Every field of ``state``, for a before/after purity comparison."""
    return {name: getattr(state, name) for name in STATE_FIELDS}


def start(
    level: Level,
    player: tuple[int, int] | None = None,
    radius: int = fov.DEFAULT_RADIUS,
    master_seed: int = 0,
    depth: int = 1,
    saved: dict[int, LevelState] | None = None,
) -> GameState:
    """The opening state for a hand-built ``level`` — what ``new_game`` does, but without
    generating.

    ``new_game`` takes a master seed now and builds level 1 itself, so a hand-built level
    can no longer go through it. This helper is the same thing by hand: player on the
    level's start, nothing open, first field of view computed immediately.
    """
    player = level.player_start if player is None else player
    open_doors: frozenset[tuple[int, int]] = frozenset()
    visible = fov.compute_visible(level, open_doors, player, radius)
    return GameState(
        master_seed,
        depth,
        level,
        player,
        explored=visible,
        visible=visible,
        open_doors=open_doors,
        saved={} if saved is None else saved,
        radius=radius,
    )


# ---------------------------------------------------------------------------------------
# Walking helpers for the generated-dungeon tests
# ---------------------------------------------------------------------------------------


def terrain_path(
    level: Level, origin: tuple[int, int], goal: tuple[int, int]
) -> list[tuple[int, int]]:
    """Shortest 8-directional route from ``origin`` to ``goal`` over *terrain*.

    Closed doors count as passable here, because bumping one opens it and the next step
    walks through — which is exactly what :func:`walk_to` does. Terrain-only pathing is
    also what makes the route independent of how many doors happen to be open already, so
    a test's route does not change as a side effect of an earlier test's walk.
    """
    if origin == goal:
        return []
    previous: dict[tuple[int, int], tuple[int, int] | None] = {origin: None}
    queue = deque([origin])
    while queue:
        cell = queue.popleft()
        if cell == goal:
            break
        x, y = cell
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                step_cell = (x + dx, y + dy)
                if step_cell in previous or not level.is_walkable(*step_cell):
                    continue
                previous[step_cell] = cell
                queue.append(step_cell)
    assert goal in previous, f"{goal} is unreachable from {origin}"

    steps: list[tuple[int, int]] = []
    cell = goal
    while previous[cell] is not None:
        prior = previous[cell]
        assert prior is not None
        steps.append((cell[0] - prior[0], cell[1] - prior[1]))
        cell = prior
    steps.reverse()
    return steps


def walk_to(state: GameState, goal: tuple[int, int]) -> GameState:
    """Walk the player to ``goal`` through :func:`step`, opening doors on the way.

    Every command goes through the real ``step``, so the resulting state is one the game
    could actually have reached — no teleporting, no hand-assembled fog. A step that does
    not move the player bumped a closed door open, so the same command is repeated once to
    walk through it.
    """
    for dx, dy in terrain_path(state.level, state.player, goal):
        command = Command(CommandKind.MOVE, dx, dy)
        origin = state.player
        state = step(state, command)
        if state.player == origin:
            assert state.open_doors > frozenset(), "only a door may block a planned step"
            state = step(state, command)
        assert state.player == (origin[0] + dx, origin[1] + dy)
    assert state.player == goal
    return state


def _down(state: GameState) -> tuple[int, int]:
    """The current level's down-staircase."""
    return state.level.stairs_down[0]


# ---------------------------------------------------------------------------------------
# LevelState and GameState shape
# ---------------------------------------------------------------------------------------


def test_levelstate_field_order_and_no_defaults() -> None:
    fields = dataclasses.fields(LevelState)
    assert [f.name for f in fields] == ["level", "explored", "open_doors"]
    for field in fields:
        assert field.default is dataclasses.MISSING


def test_levelstate_is_frozen_and_compares_by_value() -> None:
    level = room_level()
    entry = LevelState(level, frozenset({(1, 1)}), frozenset())
    assert entry == LevelState(level, frozenset({(1, 1)}), frozenset())
    assert entry != LevelState(level, frozenset(), frozenset())
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.explored = frozenset()  # type: ignore[misc]


def test_levelstate_carries_no_player_position() -> None:
    # You always re-enter a level at a staircase, never where you were standing.
    assert "player" not in {f.name for f in dataclasses.fields(LevelState)}


def test_gamestate_field_order_and_defaults() -> None:
    fields = dataclasses.fields(GameState)
    assert [f.name for f in fields] == list(STATE_FIELDS)
    for field in fields[:8]:
        assert field.default is dataclasses.MISSING
    assert fields[8].default == 0            # turns
    assert fields[9].default is True         # running
    assert fields[10].default == fov.DEFAULT_RADIUS
    assert fields[11].default == ()          # events
    assert fields[12].default is None        # outcome


def test_gamestate_constructs_positionally_with_defaults() -> None:
    level = room_level()
    state = GameState(7, 1, level, (1, 1), frozenset(), frozenset(), frozenset(), {})
    assert state.master_seed == 7
    assert state.depth == 1
    assert state.level is level
    assert state.player == (1, 1)
    assert state.saved == {}
    assert state.turns == 0
    assert state.running is True
    assert state.radius == fov.DEFAULT_RADIUS
    assert state.events == ()
    assert state.outcome is None


def test_gamestate_is_frozen() -> None:
    state = start(room_level())
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.turns = 5  # type: ignore[misc]


@pytest.mark.parametrize("field", STATE_FIELDS)
def test_gamestate_every_field_is_frozen(field: str) -> None:
    state = start(room_level())
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(state, field, None)


def test_gamestate_equality_is_by_value() -> None:
    level = room_level()
    empty: frozenset[tuple[int, int]] = frozenset()
    base = GameState(1, 1, level, (1, 1), empty, empty, empty, {}, 3, True)
    assert base == GameState(1, 1, level, (1, 1), empty, empty, empty, {}, 3, True)
    assert base != GameState(2, 1, level, (1, 1), empty, empty, empty, {}, 3, True)
    assert base != GameState(1, 2, level, (1, 1), empty, empty, empty, {}, 3, True)
    assert base != GameState(1, 1, level, (1, 1), empty, empty, empty, {}, 4, True)
    assert base != GameState(1, 1, level, (1, 1), empty, empty, empty, {}, 3, False)
    assert base != dataclasses.replace(base, events=(Event(EventKind.DOOR_OPENED),))
    assert base != dataclasses.replace(base, outcome="done")
    assert base != dataclasses.replace(
        base, saved={1: LevelState(level, empty, empty)}
    )


def test_gamestate_holds_frozensets_not_mutable_sets() -> None:
    state = start(two_room_level())
    for name in ("explored", "visible", "open_doors"):
        assert isinstance(getattr(state, name), frozenset)
    assert isinstance(state.saved, dict)
    assert isinstance(state.events, tuple)


# ---------------------------------------------------------------------------------------
# new_game — a master seed in, level 1 out
# ---------------------------------------------------------------------------------------


def test_new_game_takes_a_master_seed_and_builds_level_one() -> None:
    state = new_game(1234)
    assert state.master_seed == 1234
    assert state.depth == 1
    assert state.level == dungeon.level_for(1234, 1)
    assert state.level.depth == 1
    assert state.level.seed == dungeon.seed_for(1234, 1)


def test_new_game_stands_the_player_on_the_up_staircase() -> None:
    state = new_game(1234)
    assert state.player == state.level.stairs_up
    assert state.player == state.level.player_start
    assert state.level.tile_at(*state.player) is Tile.STAIRS_UP


def test_new_game_opening_values() -> None:
    state = new_game(1234)
    assert state.turns == 0
    assert state.running is True
    assert state.open_doors == frozenset()
    assert state.saved == {}
    assert state.events == ()
    assert state.outcome is None


def test_new_game_computes_the_first_fov_so_explored_equals_visible() -> None:
    state = new_game(1234)
    assert state.visible, "the starting cell is always seen"
    assert state.explored == state.visible
    assert state.player in state.visible


def test_new_game_matches_compute_visible_exactly() -> None:
    state = new_game(1234, *SMALL)
    assert state.visible == fov.compute_visible(
        state.level, frozenset(), state.player, fov.DEFAULT_RADIUS
    )


def test_new_game_honours_explicit_dimensions() -> None:
    state = new_game(1234, 31, 17)
    assert (state.level.width, state.level.height) == (31, 17)


def test_new_game_default_dimensions_are_eighty_by_twenty_two() -> None:
    state = new_game(1234)
    assert (state.level.width, state.level.height) == (80, 22)


def test_new_game_honours_a_custom_radius() -> None:
    state = new_game(1234, *SMALL, radius=1)
    assert state.radius == 1
    x, y = state.player
    assert state.visible == frozenset(
        {(x, y), (x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)}
    )


def test_new_game_radius_zero_sees_only_the_starting_cell() -> None:
    state = new_game(1234, *SMALL, radius=0)
    assert state.visible == frozenset({state.player})
    assert state.explored == state.visible


def test_new_game_default_radius_is_the_fov_default() -> None:
    state = new_game(1234, *SMALL)
    assert state.radius == fov.DEFAULT_RADIUS
    assert new_game(1234, *SMALL, radius=fov.DEFAULT_RADIUS) == state


def test_new_game_with_a_negative_radius_propagates_value_error() -> None:
    with pytest.raises(ValueError):
        new_game(1234, *SMALL, radius=-1)


def test_new_game_is_deterministic_for_the_same_master_seed() -> None:
    assert new_game(1234, *SMALL) == new_game(1234, *SMALL)
    assert new_game(1234, *SMALL) != new_game(1235, *SMALL)


def test_new_game_starts_with_every_door_closed() -> None:
    state = new_game(1234, *SMALL)
    doors = {
        (x, y)
        for y in range(state.level.height)
        for x in range(state.level.width)
        if state.level.tile_at(x, y) is Tile.DOOR
    }
    assert doors, "the test seed should produce a level with doors"
    assert not (state.open_doors & doors)


# ---------------------------------------------------------------------------------------
# step — MOVE onto passable ground
# ---------------------------------------------------------------------------------------


def test_move_onto_floor_updates_position_and_consumes_a_turn() -> None:
    state = start(room_level(player_start=(1, 1)))
    after = step(state, MOVE_E)
    assert after.player == (2, 1)
    assert after.turns == 1
    assert after.running is True
    assert after.level is state.level
    assert after.depth == state.depth
    assert after.open_doors == state.open_doors
    assert after.saved == state.saved


def test_successful_move_returns_a_distinct_object() -> None:
    state = start(room_level())
    after = step(state, MOVE_E)
    assert after is not state
    assert isinstance(after, GameState)


def test_move_recomputes_fov_for_the_new_position() -> None:
    # radius 1 makes the recompute exact and unmistakable.
    state = start(hall_level(player_start=(4, 3)), radius=1)
    after = step(state, MOVE_E)
    assert after.player == (5, 3)
    assert after.visible == frozenset({(5, 3), (4, 3), (6, 3), (5, 2), (5, 4)})
    assert after.visible != state.visible
    assert after.visible == fov.compute_visible(state.level, frozenset(), (5, 3), 1)


def test_move_keeps_the_radius_and_the_master_seed() -> None:
    state = start(hall_level(player_start=(4, 3)), radius=2, master_seed=99)
    after = step(state, MOVE_E)
    assert after.radius == 2
    assert after.master_seed == 99


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
    state = start(open_level(player_start=(2, 2)))
    after = step(state, command)
    assert after.player == expected
    assert after.turns == 1


def test_north_decreases_y_from_an_asymmetric_position() -> None:
    # x != y, so a swapped axis cannot pass silently (CONTRACT §0.1: up is dy = -1).
    state = start(room_level(player_start=(2, 3)))
    after = step(state, MOVE_N)
    assert after.player == (2, 2)
    assert after.player[1] < state.player[1]
    assert after.player[0] == state.player[0]


def test_south_increases_y_from_an_asymmetric_position() -> None:
    state = start(room_level(player_start=(1, 1)))
    after = step(state, MOVE_S)
    assert after.player == (1, 2)
    assert after.player[1] > state.player[1]


def test_east_increases_x_and_west_decreases_it() -> None:
    state = start(room_level(player_start=(2, 3)))
    assert step(state, MOVE_E).player == (3, 3)
    assert step(state, MOVE_W).player == (1, 3)


def test_turns_accumulate_across_successive_moves() -> None:
    state = start(room_level(player_start=(1, 1)))
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
    state = start(build_level(rows, (2, 1)))
    after = step(state, MOVE_SE)  # (3, 2) is FLOOR, though (3, 1) and (2, 2) are WALL
    assert after.player == (3, 2)
    assert after.turns == 1


# ---------------------------------------------------------------------------------------
# step — explored only ever grows
# ---------------------------------------------------------------------------------------


def test_explored_grows_and_never_shrinks_over_a_walk() -> None:
    state = start(hall_level(player_start=(1, 1)), radius=1)
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
    state = start(hall_level(player_start=(1, 1)), radius=1)
    union = set(state.visible)
    for command in (MOVE_E, MOVE_S, MOVE_E, MOVE_N, MOVE_E, MOVE_S):
        state = step(state, command)
        union |= state.visible
        assert state.explored == frozenset(union)


def test_ground_once_seen_is_never_forgotten() -> None:
    state = start(hall_level(player_start=(1, 1)), radius=1)
    first_sight = state.visible
    for _ in range(5):
        state = step(state, MOVE_E)
    assert not (first_sight <= state.visible)
    assert first_sight <= state.explored


def test_explored_always_contains_visible_on_a_generated_level() -> None:
    state = new_game(2026, *SMALL)
    for key in "jjllkkhhnnbb":
        state = step(state, translate_key(key))
        assert state.explored >= state.visible


# ---------------------------------------------------------------------------------------
# step — MOVE into a wall: THE rule. Every field must be untouched, events included.
# ---------------------------------------------------------------------------------------


def test_move_into_a_wall_changes_absolutely_nothing() -> None:
    state = start(room_level(player_start=(2, 1)))
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
    assert after.saved is state.saved


def test_move_into_a_wall_from_a_fresh_game_leaves_turns_at_zero() -> None:
    state = start(room_level(player_start=(1, 1)))
    after = step(state, MOVE_N)  # (1, 0) is the border WALL
    assert after.player == (1, 1)
    assert after.turns == 0
    assert after.visible is state.visible


@pytest.mark.parametrize("command", [MOVE_N, MOVE_W, MOVE_NW, MOVE_NE, MOVE_SW])
def test_several_blocked_directions_all_cost_no_turn(command: Command) -> None:
    state = start(room_level(player_start=(1, 1)))
    after = step(state, command)
    assert after.player == (1, 1)
    assert after.turns == 0
    assert after.visible is state.visible


def test_repeated_blocked_moves_never_move_and_never_raise() -> None:
    state = start(room_level(player_start=(1, 1)))
    original = state
    for _ in range(10):
        state = step(state, MOVE_W)
    assert state is original
    assert state.turns == 0
    assert state.running is True


def test_diagonal_into_a_wall_costs_no_turn() -> None:
    state = start(room_level(player_start=(2, 2)))
    after = step(state, MOVE_NE)  # (3, 1) is WALL
    assert after.player == (2, 2)
    assert after.turns == 0
    assert after.visible is state.visible


@pytest.mark.parametrize("command", ALL_MOVES)
def test_move_off_the_map_edge_from_a_corner_cell_is_a_no_op(command: Command) -> None:
    state = start(build_level(["...", "...", "..."], (0, 0)))
    after = step(state, command)
    assert after.running is True
    if command.dx < 0 or command.dy < 0:
        assert after is state
        assert after.player == (0, 0)
        assert after.turns == 0


@pytest.mark.parametrize(
    ("origin", "command"),
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
    origin: tuple[int, int], command: Command
) -> None:
    state = start(build_level(["...", "...", "..."], origin))
    before = snapshot(state)
    after = step(state, command)
    assert after.player == origin
    for name, value in before.items():
        assert getattr(after, name) == value


def test_move_off_the_edge_does_not_raise_on_a_one_by_one_level() -> None:
    state = start(build_level(["."], (0, 0)))
    for command in ALL_MOVES:
        after = step(state, command)
        assert after.player == (0, 0)
        assert after.turns == 0


# ---------------------------------------------------------------------------------------
# step — bump-to-open
# ---------------------------------------------------------------------------------------


def _at_the_door() -> GameState:
    """A game standing at (3, 2), one step west of the closed door at (4, 2)."""
    state = start(two_room_level(player_start=(2, 2)))
    state = step(state, MOVE_E)
    assert state.player == (3, 2)
    assert state.turns == 1
    return state


def test_the_room_beyond_a_closed_door_is_not_visible_to_begin_with() -> None:
    state = _at_the_door()
    assert state.open_doors == frozenset()
    assert not (state.visible & EAST_ROOM_FLOOR)
    assert not (state.explored & EAST_ROOM_FLOOR)
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


def test_bumping_a_closed_door_emits_door_opened() -> None:
    after = step(_at_the_door(), MOVE_E)
    assert after.events == (Event(EventKind.DOOR_OPENED),)
    assert events.message_for(after.events) == "The door opens."


def test_bumping_a_closed_door_recomputes_fov_and_reveals_the_room_beyond() -> None:
    state = _at_the_door()
    after = step(state, MOVE_E)

    assert after.visible > state.visible
    assert EAST_ROOM_FLOOR <= after.visible, "the whole room beyond must come into view"
    assert EAST_ROOM_FLOOR <= after.explored
    assert after.visible == fov.compute_visible(
        state.level, frozenset({DOOR_CELL}), (3, 2), state.radius
    )


def test_opening_a_door_adds_only_that_door() -> None:
    rows = [
        "#######",
        "#..#..#",
        "#..+..#",
        "#..#..#",
        "#..#..#",
        "#..+..#",
        "#######",
    ]
    state = start(build_level(rows, (2, 2)))
    first = step(state, MOVE_E)  # bumps (3, 2)
    assert first.open_doors == frozenset({(3, 2)})

    walked = first
    for command in (MOVE_S, MOVE_S, MOVE_S):
        walked = step(walked, command)
    assert walked.player == (2, 5)
    second = step(walked, MOVE_E)  # bumps (3, 5)
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
    state = dataclasses.replace(
        start(level), open_doors=frozenset({DOOR_CELL}), turns=4
    )
    after = step(state, MOVE_E)
    assert after.player == DOOR_CELL, "an open door is not blocked"
    assert after.turns == 5
    assert after.open_doors == frozenset({DOOR_CELL}), "nothing new was opened"


def test_a_door_never_closes_again() -> None:
    state = step(_at_the_door(), MOVE_E)
    for command in (MOVE_E, MOVE_E, MOVE_W, MOVE_W, MOVE_N, MOVE_S, UNKNOWN, MOVE_W):
        state = step(state, command)
        assert DOOR_CELL in state.open_doors


def test_a_wall_bump_and_a_door_bump_differ_in_exactly_the_turn_count() -> None:
    state = _at_the_door()
    wall = step(state, MOVE_NE)  # (4, 1) is WALL
    door = step(state, MOVE_E)   # (4, 2) is a closed DOOR
    assert wall.turns == state.turns
    assert door.turns == state.turns + 1
    assert wall.player == door.player == state.player


# ---------------------------------------------------------------------------------------
# step — QUIT and UNKNOWN
# ---------------------------------------------------------------------------------------


def test_quit_clears_running_and_leaves_everything_else_alone() -> None:
    state = step(_at_the_door(), MOVE_E)  # open a door so the state is non-trivial
    before = snapshot(state)

    after = step(state, QUIT)
    assert after.running is False
    assert after.turns == before["turns"]
    assert after.player == before["player"]
    assert after.level is state.level
    assert after.visible is state.visible
    assert after.explored is state.explored
    assert after.open_doors is state.open_doors
    assert after.saved is state.saved


def test_quit_emits_no_event_and_keeps_the_last_message() -> None:
    state = step(_at_the_door(), MOVE_E)
    assert state.events == (Event(EventKind.DOOR_OPENED),)
    after = step(state, QUIT)
    assert after.events == state.events


def test_quit_sets_no_outcome() -> None:
    # Quitting has nothing to say; only climbing out of the dungeon does.
    assert step(start(room_level()), QUIT).outcome is None


def test_quit_returns_a_distinct_object() -> None:
    state = start(room_level())
    assert step(state, QUIT) is not state


def test_unknown_leaves_the_state_entirely_unchanged() -> None:
    state = step(_at_the_door(), MOVE_E)
    after = step(state, UNKNOWN)
    assert after is state
    assert after.events == state.events
    assert after.turns == state.turns
    assert after.running is True


def test_unknown_repeated_never_accrues_turns_or_recomputes_fov() -> None:
    state = start(room_level())
    original = state
    for _ in range(5):
        state = step(state, UNKNOWN)
    assert state is original
    assert state.turns == 0


def test_numpad_five_reaches_step_as_unknown_and_costs_nothing() -> None:
    command = translate_key("5")
    assert command.kind is CommandKind.UNKNOWN
    state = start(room_level())
    assert step(state, command) is state


# ---------------------------------------------------------------------------------------
# step — a stopped game is inert, for every command kind including the new two
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("command", ALL_KINDS)
def test_stopped_state_is_returned_unchanged_for_every_command(command: Command) -> None:
    state = dataclasses.replace(
        start(stairs_level()),
        turns=9,
        running=False,
        events=(Event(EventKind.LEFT_DUNGEON),),
        outcome="farewell",
    )
    after = step(state, command)
    assert after is state
    assert after.turns == 9
    assert after.running is False
    assert after.outcome == "farewell"
    assert after.events == (Event(EventKind.LEFT_DUNGEON),)


def test_a_legal_move_after_quitting_does_nothing() -> None:
    state = step(start(room_level(player_start=(1, 1))), QUIT)
    after = step(state, MOVE_E)  # (2, 1) is FLOOR and would otherwise be legal
    assert after == state
    assert after.turns == 0


def test_quitting_twice_is_idempotent() -> None:
    state = step(start(room_level()), QUIT)
    assert step(state, QUIT) == state


# ---------------------------------------------------------------------------------------
# step — stair messages: stepping ONTO the tile
# ---------------------------------------------------------------------------------------


def test_stepping_onto_the_down_staircase_says_so() -> None:
    state = start(stairs_level(), player=(15, 3))
    after = step(state, MOVE_E)
    assert after.player == DOWN_CELL
    assert after.events == (Event(EventKind.STAIRS_HERE_DOWN),)
    assert events.message_for(after.events) == "There is a staircase leading down here."


def test_stepping_onto_the_up_staircase_says_so() -> None:
    state = start(stairs_level(), player=(4, 3))
    after = step(state, MOVE_W)
    assert after.player == UP_CELL
    assert after.events == (Event(EventKind.STAIRS_HERE_UP),)
    assert events.message_for(after.events) == "There is a staircase leading up here."


def test_walking_adjacent_to_a_staircase_says_nothing() -> None:
    state = start(stairs_level(), player=(15, 4))
    after = step(state, MOVE_E)  # (16, 4) is beside the down-staircase, not on it
    assert after.player == (16, 4)
    assert after.events == ()


def test_walking_off_a_staircase_says_nothing() -> None:
    state = start(stairs_level())  # standing on the up-staircase
    after = step(state, MOVE_E)
    assert after.player == (4, 3)
    assert after.events == ()


def test_a_stair_message_carries_no_depth() -> None:
    after = step(start(stairs_level(), player=(15, 3)), MOVE_E)
    assert after.events[0].depth is None


def test_the_stair_message_replaces_the_previous_one() -> None:
    state = step(start(stairs_level(), player=(10, 3)), MOVE_W)  # bump the door open
    assert state.events == (Event(EventKind.DOOR_OPENED),)
    state = step(state, MOVE_W)  # onto the door
    assert state.events == ()
    for _ in range(6):
        state = step(state, MOVE_W)
    assert state.player == UP_CELL
    assert state.events == (Event(EventKind.STAIRS_HERE_UP),)


def test_a_level_without_stairs_never_emits_a_stair_message() -> None:
    state = start(hall_level(player_start=(1, 1)))
    assert state.level.stairs_up is None
    for command in (MOVE_E, MOVE_E, MOVE_S, MOVE_SE):
        state = step(state, command)
        assert state.events == ()


# ---------------------------------------------------------------------------------------
# step — DESCEND
# ---------------------------------------------------------------------------------------


def test_descend_off_the_stairs_emits_no_stairs_down_and_costs_nothing() -> None:
    state = start(stairs_level())  # on the UP staircase, not the down one
    before = snapshot(state)
    after = step(state, DESCEND)

    assert after.events == (Event(EventKind.NO_STAIRS_DOWN),)
    assert events.message_for(after.events) == "There are no stairs leading down here."
    assert after.turns == before["turns"] == 0
    assert after.depth == before["depth"]
    assert after.player == before["player"]
    assert after.level is state.level
    assert after.explored == before["explored"]
    assert after.visible is state.visible
    assert after.open_doors == before["open_doors"]
    assert after.saved == before["saved"]
    assert after.running is True


def test_descend_on_a_level_with_no_stairs_at_all_says_no_stairs() -> None:
    state = start(hall_level())
    assert state.level.stairs_down == ()
    after = step(state, DESCEND)
    assert after.events == (Event(EventKind.NO_STAIRS_DOWN),)
    assert after.turns == 0


def test_descend_moves_a_level_down_and_costs_a_turn() -> None:
    before = walk_to(start(stairs_level()), DOWN_CELL)
    after = step(before, DESCEND)

    assert after.depth == 2
    assert after.turns == before.turns + 1
    assert after.running is True
    assert after.events == (Event(EventKind.DESCENDED, depth=2),)
    assert events.message_for(after.events) == "You descend to level 2."


def test_the_player_coordinate_really_is_preserved_across_a_descent() -> None:
    before = walk_to(start(stairs_level()), DOWN_CELL)
    after = step(before, DESCEND)

    assert after.player == before.player
    assert after.level.stairs_up == before.level.stairs_down[0]
    assert after.player == after.level.stairs_up
    assert after.level.tile_at(*after.player) is Tile.STAIRS_UP


def test_descend_builds_the_level_below_from_the_master_seed_and_depth() -> None:
    before = walk_to(start(stairs_level(), master_seed=1234), DOWN_CELL)
    after = step(before, DESCEND)
    assert after.level == dungeon.level_for(
        1234, 2, required_up=DOWN_CELL, width=STAIRS_SIZE[0], height=STAIRS_SIZE[1]
    )
    assert after.level.depth == 2
    assert after.master_seed == 1234


def test_descend_files_the_level_left_behind_into_saved() -> None:
    before = walk_to(start(stairs_level()), DOWN_CELL)
    after = step(before, DESCEND)

    assert set(after.saved) == {1}
    entry = after.saved[1]
    assert entry.level is before.level
    assert entry.explored == before.explored
    assert entry.open_doors == before.open_doors


def test_the_current_depth_is_never_a_key_of_saved() -> None:
    state = walk_to(start(stairs_level()), DOWN_CELL)
    state = step(state, DESCEND)
    assert state.depth not in state.saved
    state = step(state, ASCEND)
    assert state.depth not in state.saved


def test_descending_starts_the_new_level_unexplored_with_no_open_doors() -> None:
    before = walk_to(start(stairs_level()), DOWN_CELL)
    after = step(before, DESCEND)
    assert after.open_doors == frozenset()
    assert after.explored == after.visible
    assert after.visible == fov.compute_visible(
        after.level, frozenset(), after.player, after.radius
    )
    assert after.explored != before.explored, "the fog is the new level's, not the old"


def test_descending_does_not_carry_the_old_level_s_fog_down() -> None:
    before = walk_to(start(stairs_level()), DOWN_CELL)
    after = step(before, DESCEND)
    # The old level's explored ground is filed away, not inherited.
    assert after.saved[1].explored == before.explored
    assert not (after.explored >= before.explored)


# ---------------------------------------------------------------------------------------
# step — ASCEND
# ---------------------------------------------------------------------------------------


def test_ascend_off_the_stairs_emits_no_stairs_up_and_costs_nothing() -> None:
    state = start(stairs_level(), player=(5, 3))
    before = snapshot(state)
    after = step(state, ASCEND)

    assert after.events == (Event(EventKind.NO_STAIRS_UP),)
    assert events.message_for(after.events) == "There are no stairs leading up here."
    assert after.turns == 0
    assert after.depth == before["depth"]
    assert after.player == before["player"]
    assert after.explored == before["explored"]
    assert after.open_doors == before["open_doors"]
    assert after.running is True
    assert after.outcome is None


def test_ascend_on_the_down_staircase_is_still_no_stairs_up() -> None:
    state = walk_to(start(stairs_level()), DOWN_CELL)
    after = step(state, ASCEND)
    assert after.events == (Event(EventKind.NO_STAIRS_UP),)
    assert after.turns == state.turns


def test_ascend_at_depth_one_ends_the_game() -> None:
    state = start(stairs_level())  # standing on the up-staircase of level 1
    after = step(state, ASCEND)

    assert after.running is False
    assert after.events == (Event(EventKind.LEFT_DUNGEON),)
    assert after.outcome
    assert isinstance(after.outcome, str)
    assert after.outcome == "You climb out of the dungeon and give up. Farewell."
    assert after.depth == 1


def test_leaving_the_dungeon_takes_the_wording_from_the_message_table() -> None:
    after = step(start(stairs_level()), ASCEND)
    assert after.outcome == events.MESSAGES[EventKind.LEFT_DUNGEON]
    assert after.outcome == events.message_for(after.events)


def test_leaving_the_dungeon_changes_nothing_else() -> None:
    state = start(stairs_level())
    after = step(state, ASCEND)
    assert after.player == state.player
    assert after.turns == state.turns
    assert after.level is state.level
    assert after.explored is state.explored
    assert after.saved == state.saved


def test_ascend_from_depth_two_returns_to_depth_one() -> None:
    below = step(walk_to(start(stairs_level()), DOWN_CELL), DESCEND)
    assert below.depth == 2
    after = step(below, ASCEND)

    assert after.depth == 1
    assert after.running is True
    assert after.turns == below.turns + 1
    assert after.events == (Event(EventKind.ASCENDED, depth=1),)
    assert events.message_for(after.events) == "You climb up to level 1."


def test_ascending_arrives_on_the_down_staircase_you_came_from() -> None:
    before = walk_to(start(stairs_level()), DOWN_CELL)
    after = step(step(before, DESCEND), ASCEND)
    assert after.player == before.level.stairs_down[0]
    assert after.player == DOWN_CELL
    assert after.level is before.level


def test_ascending_restores_the_level_object_itself() -> None:
    before = walk_to(start(stairs_level()), DOWN_CELL)
    after = step(step(before, DESCEND), ASCEND)
    assert after.level is before.level
    assert after.level == before.level


# ---------------------------------------------------------------------------------------
# Persistence — the most important test in this task
# ---------------------------------------------------------------------------------------


def test_persistence_round_trip_preserves_fog_and_doors_exactly() -> None:
    """Descend, explore, open a door, climb back up, and go down again.

    Both levels must come back exactly as they were left: same explored ground, same open
    doors, the same ``Level`` object. Fog must not reset — that is the whole reason
    ``saved`` exists.
    """
    state = new_game(1234, *SMALL)

    # --- level 1: walk across it to the down-staircase, opening doors on the way -------
    state = walk_to(state, _down(state))
    level_one = state.level
    one_explored = state.explored
    one_doors = state.open_doors
    assert one_doors, "crossing a level always passes through at least one door"

    # --- level 2: descend, then walk right across it to its own down-staircase --------
    state = step(state, DESCEND)
    assert state.depth == 2
    level_two = state.level
    fresh_explored = state.explored
    state = walk_to(state, _down(state))
    two_explored = state.explored
    two_doors = state.open_doors
    assert two_doors, "and so does crossing level 2"
    assert two_explored > fresh_explored, "walking must have revealed new ground"

    # --- back up to level 1: it must be exactly as we left it -------------------------
    state = walk_to(state, state.level.stairs_up)
    state = step(state, ASCEND)
    assert state.depth == 1
    assert state.level is level_one
    assert state.explored == one_explored, "fog must not reset"
    assert state.open_doors == one_doors, "doors must not close"
    assert state.player == level_one.stairs_down[0]

    # --- and back down: level 2 must be exactly as we left it too ---------------------
    state = step(state, DESCEND)
    assert state.depth == 2
    assert state.level is level_two
    assert state.explored == two_explored
    assert state.open_doors == two_doors
    assert state.player == level_two.stairs_up


def test_descending_twice_reuses_the_saved_level_rather_than_regenerating() -> None:
    state = new_game(1234, *SMALL)
    state = walk_to(state, _down(state))
    first = step(state, DESCEND)
    level_two = first.level

    state = step(first, ASCEND)
    again = step(state, DESCEND)

    assert again.level == level_two
    assert again.level is level_two, "restored from saved, not regenerated"


def test_a_saved_level_is_not_disturbed_by_what_happens_elsewhere() -> None:
    state = new_game(1234, *SMALL)
    state = walk_to(state, _down(state))
    filed = LevelState(state.level, state.explored, state.open_doors)
    state = step(state, DESCEND)
    for command in (*ALL_MOVES, *ALL_MOVES, UNKNOWN, ASCEND):
        if command is ASCEND:
            break
        state = step(state, command)
    assert state.saved[1] == filed


def test_fog_survives_three_descents_and_three_ascents() -> None:
    state = new_game(31337, *SMALL)
    marks: dict[int, tuple[Level, frozenset, frozenset]] = {}

    for depth in (1, 2, 3):
        assert state.depth == depth
        state = walk_to(state, _down(state))
        marks[depth] = (state.level, state.explored, state.open_doors)
        state = step(state, DESCEND)

    assert state.depth == 4
    deepest = (state.level, state.explored, state.open_doors)

    for depth in (3, 2, 1):
        state = walk_to(state, state.level.stairs_up)
        if state.depth == 4:
            marks[4] = deepest
        state = step(state, ASCEND)
        assert state.depth == depth
        level, explored, doors = marks[depth]
        assert state.level is level
        assert state.explored == explored
        assert state.open_doors == doors

    assert set(state.saved) == {2, 3, 4}


def test_saved_grows_by_one_entry_per_new_depth() -> None:
    state = new_game(1234, *SMALL)
    assert state.saved == {}
    state = walk_to(state, _down(state))
    state = step(state, DESCEND)
    assert set(state.saved) == {1}
    state = walk_to(state, _down(state))
    state = step(state, DESCEND)
    assert set(state.saved) == {1, 2}
    assert state.depth == 3


def test_leaving_the_dungeon_after_a_round_trip_still_works() -> None:
    state = new_game(1234, *SMALL)
    state = walk_to(state, _down(state))
    state = step(state, DESCEND)
    state = walk_to(state, state.level.stairs_up)
    state = step(state, ASCEND)
    assert state.depth == 1
    state = walk_to(state, state.level.stairs_up)
    final = step(state, ASCEND)
    assert final.running is False
    assert final.outcome == events.MESSAGES[EventKind.LEFT_DUNGEON]


# ---------------------------------------------------------------------------------------
# The event rule (CONTRACT-v3 §7.1)
# ---------------------------------------------------------------------------------------


def _with_a_message(state: GameState) -> GameState:
    """``state`` carrying a previous turn's message, so its survival can be observed."""
    return dataclasses.replace(state, events=(Event(EventKind.DESCENDED, depth=4),))


def test_a_blocked_move_leaves_the_previous_message_on_screen() -> None:
    state = _with_a_message(start(room_level(player_start=(1, 1))))
    after = step(state, MOVE_W)  # into the border wall
    assert after is state
    assert after.events == (Event(EventKind.DESCENDED, depth=4),)


def test_an_unknown_key_leaves_the_previous_message_on_screen() -> None:
    state = _with_a_message(start(room_level()))
    after = step(state, UNKNOWN)
    assert after is state
    assert after.events == state.events


def test_a_plain_move_clears_the_previous_message() -> None:
    # A turn passed, so the events tuple is replaced — with nothing, because walking onto
    # ordinary ground is not news.
    state = _with_a_message(start(room_level(player_start=(1, 1))))
    after = step(state, MOVE_E)
    assert after.turns == 1
    assert after.events == ()


@pytest.mark.parametrize(
    ("setup", "command"),
    [
        (lambda: _with_a_message(_at_the_door()), MOVE_E),                     # door
        (lambda: _with_a_message(start(stairs_level(), player=(15, 3))), MOVE_E),  # stair
        (lambda: _with_a_message(walk_to(start(stairs_level()), DOWN_CELL)), DESCEND),
        (lambda: _with_a_message(start(stairs_level())), ASCEND),
    ],
)
def test_every_turn_consuming_action_replaces_the_events(setup, command) -> None:
    state = setup()
    after = step(state, command)
    assert after.events != state.events
    assert after.events != ()


@pytest.mark.parametrize("command", [DESCEND, ASCEND])
def test_a_stair_command_off_the_stairs_replaces_the_message_without_a_turn(
    command: Command,
) -> None:
    # The one case where an event is produced but no turn is consumed: it still replaces
    # the message, because the command *did* produce an event.
    state = _with_a_message(start(stairs_level(), player=(5, 3)))
    after = step(state, command)
    assert after.turns == state.turns
    assert after.events != state.events
    assert len(after.events) == 1


def test_events_is_always_a_tuple_of_events() -> None:
    state = new_game(1234, *SMALL)
    for command in (*ALL_MOVES, DESCEND, ASCEND, UNKNOWN):
        after = step(state, command)
        assert isinstance(after.events, tuple)
        assert all(isinstance(event, Event) for event in after.events)


def test_every_emitted_event_renders_without_raising() -> None:
    # message_for raises if a {depth} template gets None — so every DESCENDED/ASCENDED
    # this module emits must carry its depth.
    state = new_game(1234, *SMALL)
    seen: set[EventKind] = set()
    for _ in range(3):
        state = walk_to(state, _down(state))
        state = step(state, DESCEND)
        seen.update(event.kind for event in state.events)
        assert events.message_for(state.events)
    state = step(state, ASCEND)
    seen.update(event.kind for event in state.events)
    assert events.message_for(state.events)
    assert {EventKind.DESCENDED, EventKind.ASCENDED} <= seen


def test_no_event_is_emitted_for_bumping_a_wall() -> None:
    # Deliberately absent from the vocabulary: it would fire on every misstep.
    state = start(room_level(player_start=(1, 1)))
    after = step(state, MOVE_W)
    assert after.events == ()
    assert "WALL" not in {kind.name for kind in EventKind}


# ---------------------------------------------------------------------------------------
# Chrome text (CONTRACT-v3 §7.2)
# ---------------------------------------------------------------------------------------


def test_format_stats_is_empty() -> None:
    assert format_stats(start(room_level())) == ""
    assert format_stats(new_game(1234, *SMALL)) == ""


def test_format_stats_stays_empty_after_playing() -> None:
    state = step(_at_the_door(), MOVE_E)
    assert format_stats(state) == ""
    assert type(format_stats(state)) is str


def test_format_status_right_exact_literal() -> None:
    state = dataclasses.replace(start(room_level()), master_seed=4242, depth=3)
    assert format_status_right(state) == "Level 3  Seed 4242"


def test_format_status_right_uses_two_spaces() -> None:
    text = format_status_right(new_game(1234, *SMALL))
    assert text == "Level 1  Seed 1234"
    assert "  Seed" in text


def test_format_status_right_uses_the_master_seed_not_the_derived_one() -> None:
    state = new_game(1234, *SMALL)
    state = walk_to(state, _down(state))
    state = step(state, DESCEND)
    assert state.level.seed == dungeon.seed_for(1234, 2)
    assert state.level.seed != 1234
    assert format_status_right(state) == "Level 2  Seed 1234"
    assert str(state.level.seed) not in format_status_right(state)


def test_format_status_right_tracks_the_depth() -> None:
    state = walk_to(start(stairs_level(), master_seed=7), DOWN_CELL)
    assert format_status_right(state) == "Level 1  Seed 7"
    assert format_status_right(step(state, DESCEND)) == "Level 2  Seed 7"


def test_format_status_right_returns_a_plain_unpadded_str() -> None:
    text = format_status_right(new_game(1234, *SMALL))
    assert type(text) is str
    assert text == text.strip()  # padding is the renderer's job (CONTRACT-v3 §4.2)


def test_v2_format_status_is_gone() -> None:
    assert not hasattr(game, "format_status")
    assert "format_status" not in [
        name for name in game.__all__ if name == "format_status"
    ]


def test_chrome_text_functions_do_not_mutate_the_state() -> None:
    state = new_game(1234, *SMALL)
    before = snapshot(state)
    format_stats(state)
    format_status_right(state)
    for name, value in before.items():
        assert getattr(state, name) == value


def _string_literals(tree: ast.Module) -> list[str]:
    """Every ``str`` constant in ``tree`` that is not a docstring.

    Docstrings and comments are prose about the code; string *literals* are the code's
    own output, and those are what may not contain wording.
    """
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_the_module_composes_no_wording_of_its_own() -> None:
    # Every sentence in the game comes from events.MESSAGES; game.py holds none, not even
    # a copy of one. Docstrings are prose about the code and are exempt; what matters is
    # that no string this module can ever *emit* is a sentence.
    literals = _string_literals(GAME_TREE)
    assert literals, "the status-right format string should be in here"
    for literal in literals:
        for message in events.MESSAGES.values():
            assert message not in literal
        text = literal.strip()
        assert not (
            text.endswith(".") and len(text.split()) >= 3
        ), f"game.py must not contain a sentence: {literal!r}"


# ---------------------------------------------------------------------------------------
# step — purity
# ---------------------------------------------------------------------------------------


def _purity_state() -> GameState:
    level = stairs_level(seed=17)
    return GameState(
        1234,
        2,
        level,
        DOWN_CELL,
        explored=frozenset({DOWN_CELL, (15, 3), (15, 2)}),
        visible=frozenset({DOWN_CELL, (15, 3)}),
        open_doors=frozenset({STAIRS_DOOR}),
        saved={1: LevelState(room_level(), frozenset({(1, 1)}), frozenset())},
        turns=3,
        running=True,
        radius=6,
        events=(Event(EventKind.ASCENDED, depth=2),),
    )


@pytest.mark.parametrize("command", ALL_KINDS)
def test_step_never_mutates_its_input(command: Command) -> None:
    state = _purity_state()
    before = snapshot(state)
    level_snapshot = copy.deepcopy(state.level)
    saved_snapshot = copy.deepcopy(state.saved)

    step(state, command)

    for name, value in before.items():
        assert getattr(state, name) == value
    assert state.level is before["level"]
    assert state.visible is before["visible"]
    assert state.explored is before["explored"]
    assert state.open_doors is before["open_doors"]
    assert state.saved is before["saved"]
    assert state.events is before["events"]
    assert state.level == level_snapshot
    assert state.saved == saved_snapshot


@pytest.mark.parametrize("command", ALL_KINDS)
def test_step_never_mutates_the_saved_dict_it_was_handed(command: Command) -> None:
    entry = LevelState(room_level(), frozenset({(1, 1)}), frozenset())
    saved = {1: entry}
    state = dataclasses.replace(_purity_state(), saved=saved)

    after = step(state, command)

    assert saved == {1: entry}, "the input dict is never written to"
    assert saved is state.saved
    if after.saved is not saved:
        assert isinstance(after.saved, dict)


def test_step_builds_a_new_saved_dict_on_a_level_change() -> None:
    state = walk_to(start(stairs_level()), DOWN_CELL)
    original = state.saved
    after = step(state, DESCEND)
    assert after.saved is not original
    assert original == {}


def test_step_does_not_mutate_the_frozensets_it_was_handed() -> None:
    explored = frozenset({(3, 2)})
    visible = frozenset({(3, 2)})
    open_doors: frozenset[tuple[int, int]] = frozenset()
    state = GameState(
        0, 1, two_room_level(player_start=(3, 2)), (3, 2), explored, visible, open_doors, {}
    )
    after = step(state, MOVE_E)  # opens the door — the transition that grows two sets

    assert explored == frozenset({(3, 2)})
    assert visible == frozenset({(3, 2)})
    assert open_doors == frozenset()
    assert after.open_doors == frozenset({DOOR_CELL})
    assert after.open_doors is not open_doors
    assert after.explored is not explored


def test_step_does_not_mutate_the_command() -> None:
    command = Command(CommandKind.MOVE, 1, 0)
    step(start(room_level()), command)
    assert command == Command(CommandKind.MOVE, 1, 0)


def test_step_is_deterministic_for_the_same_inputs() -> None:
    state = _at_the_door()
    assert step(state, MOVE_E) == step(state, MOVE_E)
    assert step(state, MOVE_N) == step(state, MOVE_N)


def test_descending_twice_from_the_same_state_gives_the_same_result() -> None:
    state = walk_to(start(stairs_level()), DOWN_CELL)
    assert step(state, DESCEND) == step(state, DESCEND)


def test_step_shares_the_level_object_rather_than_copying_it() -> None:
    state = _at_the_door()
    for command in (MOVE_E, MOVE_W, QUIT, UNKNOWN, DESCEND, ASCEND):
        assert step(state, command).level is state.level


@pytest.mark.parametrize(
    ("command", "changes"),
    [
        (MOVE_W, True),
        (MOVE_E, True),
        (QUIT, True),
        (DESCEND, True),   # emits NO_STAIRS_DOWN, so events change
        (ASCEND, True),    # emits NO_STAIRS_UP
        (MOVE_NE, False),
        (UNKNOWN, False),
    ],
)
def test_the_returned_object_is_distinct_exactly_when_something_changed(
    command: Command, changes: bool
) -> None:
    state = _at_the_door()
    assert (step(state, command) is not state) is changes


# ---------------------------------------------------------------------------------------
# step — FOV is recomputed on exactly the turn-consuming transitions
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("command", [MOVE_NE, UNKNOWN, QUIT, DESCEND, ASCEND])
def test_fov_is_not_recomputed_without_a_turn(command: Command) -> None:
    # From (3, 2) in the two-room level: north-east is a wall, and neither staircase
    # exists, so none of these consumes a turn.
    state = _at_the_door()
    after = step(state, command)
    assert after.visible is state.visible
    assert after.explored is state.explored
    assert after.turns == state.turns


def test_fov_is_recomputed_on_an_accepted_move() -> None:
    state = start(hall_level(player_start=(2, 2)), radius=1)
    after = step(state, MOVE_E)
    assert after.visible is not state.visible
    assert after.visible != state.visible


def test_fov_is_recomputed_on_a_door_opening() -> None:
    state = _at_the_door()
    after = step(state, MOVE_E)
    assert after.visible is not state.visible
    assert after.visible != state.visible


def test_fov_is_recomputed_against_the_new_level_on_a_descent() -> None:
    state = walk_to(start(stairs_level()), DOWN_CELL)
    after = step(state, DESCEND)
    assert after.visible == fov.compute_visible(
        after.level, frozenset(), after.player, after.radius
    )
    assert after.visible != state.visible


def test_fov_is_recomputed_against_the_restored_doors_on_an_ascent() -> None:
    state = new_game(1234, *SMALL)
    state = walk_to(state, _down(state))
    doors = state.open_doors
    state = step(state, DESCEND)
    state = walk_to(state, state.level.stairs_up)
    after = step(state, ASCEND)
    assert after.open_doors == doors
    assert after.visible == fov.compute_visible(
        after.level, doors, after.player, after.radius
    )


def test_exactly_the_turn_consuming_transitions_recompute_fov() -> None:
    state = _at_the_door()
    for command in ALL_KINDS:
        after = step(state, command)
        recomputed = after.visible is not state.visible
        consumed_a_turn = after.turns != state.turns
        assert recomputed == consumed_a_turn


# ---------------------------------------------------------------------------------------
# Scripted sequences
# ---------------------------------------------------------------------------------------


def test_scripted_sequence_with_a_blocked_move_and_a_door_opening() -> None:
    state = start(two_room_level(player_start=(2, 2)))
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
    assert state.turns != len(script)
    assert state.turns != 5  # five commands actually moved the player
    assert EAST_ROOM_FLOOR <= state.explored


def test_scripted_sequence_ending_in_quit() -> None:
    state = start(room_level(player_start=(1, 1)))
    for command in (MOVE_E, MOVE_S, MOVE_W, QUIT, MOVE_E, MOVE_S):
        state = step(state, command)
    assert state.player == (1, 2)
    assert state.turns == 3
    assert state.running is False


def test_keys_translate_into_a_playable_sequence_without_curses() -> None:
    # End to end through the input layer: raw key codes in, final state out.
    state = start(two_room_level(player_start=(3, 2)))
    for key in "lllq":
        state = step(state, translate_key(key))
    assert state.running is False
    assert state.player == (5, 2)
    assert state.turns == 3
    assert state.open_doors == frozenset({DOOR_CELL})


def test_the_stair_keys_reach_step_through_translate_key() -> None:
    state = walk_to(start(stairs_level()), DOWN_CELL)
    after = step(state, translate_key(">"))
    assert after.depth == 2
    assert step(after, translate_key("<")).depth == 1


def test_a_scripted_run_across_three_depths() -> None:
    """A real run: down to level 3 and back to level 2, counting every turn.

    The turn count must match neither the number of commands issued (mistyped stair
    commands and wall bumps cost nothing) nor the number of moves accepted (doors and
    staircases cost a turn without one).
    """
    state = new_game(1234, *SMALL)
    issued = accepted = opened = stairs = 0

    def issue(current: GameState, command: Command) -> GameState:
        nonlocal issued, accepted, opened, stairs
        issued += 1
        after = step(current, command)
        if after.depth != current.depth:
            stairs += 1
        elif after.player != current.player:
            accepted += 1
        elif after.open_doors != current.open_doors:
            opened += 1
        return after

    # A mistyped '>' at the top of the stairs: a message, and nothing else.
    state = issue(state, DESCEND)
    assert state.turns == 0
    assert state.events == (Event(EventKind.NO_STAIRS_DOWN),)

    for depth in (2, 3):
        for dx, dy in terrain_path(state.level, state.player, _down(state)):
            command = Command(CommandKind.MOVE, dx, dy)
            origin = state.player
            state = issue(state, command)
            if state.player == origin:
                state = issue(state, command)  # the bump opened a door; now walk through
        assert state.player == _down(state)
        state = issue(state, DESCEND)
        assert state.depth == depth

    assert state.depth == 3
    assert state.player == state.level.stairs_up

    # One flight back up, arriving on the staircase we came down.
    two_stairs_down = state.saved[2].level.stairs_down[0]
    state = issue(state, ASCEND)
    assert state.depth == 2
    assert state.player == two_stairs_down

    assert stairs == 3
    assert state.turns == accepted + opened + stairs
    assert state.turns != issued, "mistyped commands and bumps cost no turn"
    assert state.turns != accepted, "doors and staircases cost turns without moving"
    assert opened > 0


# ---------------------------------------------------------------------------------------
# Module hygiene — asserted by reading the source (CONTRACT §0.3, CONTRACT-v3 §7, §10)
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
        "roguelike.fov",
        "roguelike.world",
        "roguelike.dungeon",
        "roguelike.events",
    }
    assert "roguelike.game" not in roguelike_imports
    assert "roguelike.style" not in roguelike_imports
    assert "roguelike.tiles" not in roguelike_imports
    assert "roguelike.generator" not in roguelike_imports, (
        "generation is reached through dungeon now (CONTRACT-v3 §10)"
    )
    assert imported - roguelike_imports <= {"__future__", "curses", "dataclasses"}


def test_step_does_not_touch_the_renderer_or_curses() -> None:
    for name in (
        "step",
        "_take_turn",
        "_change_level",
        "_descend",
        "_ascend",
        "_stair_events",
        "new_game",
        "format_stats",
        "format_status_right",
    ):
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


def test_visibility_is_only_ever_computed_by_fov() -> None:
    attrs = {
        child.func.attr
        for child in ast.walk(GAME_TREE)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
    }
    assert "compute_visible" in attrs
    for node in ast.walk(GAME_TREE):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "compute_visible"
        ):
            assert isinstance(node.func.value, ast.Name)
            assert node.func.value.id == "fov"


def test_level_generation_goes_through_dungeon() -> None:
    assert "generate_level" not in GAME_SOURCE
    assert "level_for" in GAME_SOURCE
    for node in ast.walk(GAME_TREE):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "level_for"
        ):
            assert isinstance(node.func.value, ast.Name)
            assert node.func.value.id == "dungeon"


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
    assert {"step", "translate_key", "format_stats", "format_status_right"} <= called_names
    assert {"render_to_cells", "draw", "init_colors", "message_for"} <= called_attrs
    assert "try_move" not in called_names
    assert "compute_visible" not in called_names
    assert "compute_visible" not in called_attrs
    assert "level_for" not in called_attrs
    assert "is_walkable" not in called_names


def test_run_builds_a_chrome_rather_than_a_bare_string() -> None:
    node = _function("run")
    attrs = {
        child.func.attr
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
    }
    assert "Chrome" in attrs


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


def test_run_returns_the_final_state() -> None:
    node = _function("run")
    returns = [child for child in ast.walk(node) if isinstance(child, ast.Return)]
    assert returns, "run must return the final GameState (CONTRACT-v3 §7.3)"
    assert any(
        isinstance(child.value, ast.Name) and child.value.id == "state"
        for child in returns
    )


def test_play_prints_the_outcome_after_the_wrapper_returns() -> None:
    node = _function("play")
    body = ast.dump(node)
    assert "wrapper" in body
    assert "outcome" in body
    prints = [
        child
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Name)
        and child.func.id == "print"
    ]
    assert len(prints) == 1
    # The print must come after the wrapper call, textually and structurally: the wrapper
    # is assigned to a name that the print's guard reads.
    wrapper_line = next(
        n.lineno
        for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "wrapper"
    )
    assert prints[0].lineno > wrapper_line


def test_public_surface_is_exactly_the_contract_surface() -> None:
    assert game.__all__ == [
        "LevelState",
        "GameState",
        "new_game",
        "step",
        "format_stats",
        "format_status_right",
        "run",
        "play",
    ]
    public = {
        name
        for name in vars(game)
        if not name.startswith("_") and name not in {"annotations"}
    }
    assert set(game.__all__) <= public


def test_signatures_match_the_contract() -> None:
    assert str(inspect.signature(new_game)) == (
        "(master_seed: 'int', width: 'int' = 80, height: 'int' = 22, "
        "radius: 'int' = 20) -> 'GameState'"
    )
    assert inspect.signature(new_game).parameters["radius"].default == fov.DEFAULT_RADIUS
    assert (
        str(inspect.signature(step))
        == "(state: 'GameState', command: 'Command') -> 'GameState'"
    )
    assert str(inspect.signature(format_stats)) == "(state: 'GameState') -> 'str'"
    assert str(inspect.signature(format_status_right)) == "(state: 'GameState') -> 'str'"
    assert str(inspect.signature(run)) == "(stdscr, state: 'GameState') -> 'GameState'"
    assert (
        str(inspect.signature(play))
        == "(seed: 'int', width: 'int' = 80, height: 'int' = 22) -> 'None'"
    )


def test_importing_the_module_does_not_initialise_a_terminal() -> None:
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
    # No message log or scrollback, no menus, pause, score, game-over screen, save/load,
    # entities, and no explicit open/close-door command beyond bump-to-open.
    field_names = {f.name for f in dataclasses.fields(GameState)}
    for name in (
        "messages",
        "message_log",
        "history",
        "score",
        "game_over",
        "paused",
        "entities",
        "monsters",
        "items",
        "inventory",
        "save",
        "load",
        "menu",
        "open_door",
        "close_door",
        "toggle_door",
    ):
        assert not hasattr(game, name)
        assert name not in field_names


def test_no_extra_command_kind_was_invented() -> None:
    assert {kind.name for kind in CommandKind} == {
        "MOVE",
        "QUIT",
        "UNKNOWN",
        "DESCEND",
        "ASCEND",
    }
    for name in ("OPEN", "CLOSE", "WAIT", "REST", "SEARCH"):
        assert f"CommandKind.{name}" not in GAME_SOURCE


def test_only_the_first_down_staircase_is_used() -> None:
    # The tuple is branching scaffolding; today the game reads [0] and moves on.
    assert "stairs_down[0]" in GAME_SOURCE
    assert "branch" not in GAME_SOURCE


def test_run_and_play_exist_and_are_functions() -> None:
    assert callable(run)
    assert callable(play)


# ---------------------------------------------------------------------------------------
# run — driven with a stub screen, which is not a terminal
# ---------------------------------------------------------------------------------------


class StubScreen:
    """A screen-shaped object that is not a terminal and initialises no curses.

    ``run`` holds no game rules, but it *is* where the renderer's signature is actually
    used, and a mismatch there is invisible to every test of :func:`step`. Driving the
    loop with a scripted key sequence costs nothing and needs no TTY: the real curses
    calls inside ``run`` and ``render.draw`` all raise ``curses.error`` with no terminal
    up, and both functions swallow that by design. The live smoke test on a real screen
    belongs to the integrator.
    """

    def __init__(self, keys: list[int]) -> None:
        self._keys = list(keys)
        self.frames: list[dict[tuple[int, int], str]] = []
        self._current: dict[tuple[int, int], str] = {}
        self.keypad_called: bool | None = None
        self.refreshes = 0

    def getch(self) -> int:
        assert self._keys, "run asked for more input than the script provides"
        return self._keys.pop(0)

    def erase(self) -> None:
        self._current = {}
        self.frames.append(self._current)

    def addstr(self, y: int, x: int, char: str, attr: int = 0) -> None:
        self._current[(y, x)] = char

    def refresh(self) -> None:
        self.refreshes += 1

    def keypad(self, flag: bool) -> None:
        self.keypad_called = flag

    def getmaxyx(self) -> tuple[int, int]:
        return (40, 100)

    def row(self, frame_index: int, y: int, width: int) -> str:
        frame = self.frames[frame_index]
        return "".join(frame.get((y, x), " ") for x in range(width))


def test_run_drives_the_loop_and_returns_the_final_state() -> None:
    screen = StubScreen([ord("q")])
    state = new_game(1234, *SMALL)
    final = run(screen, state)

    assert isinstance(final, GameState)
    assert final.running is False
    assert final.turns == state.turns
    assert screen.frames, "the state must be drawn before the first key is read"
    assert screen.refreshes == len(screen.frames)


def test_run_renders_one_frame_per_command_and_none_after_the_last() -> None:
    screen = StubScreen([ord("l"), ord("q")])
    run(screen, new_game(1234, *SMALL))
    assert len(screen.frames) == 2, "no frame is drawn once the game has stopped"


def test_run_lays_the_chrome_out_through_the_renderer() -> None:
    width, height = SMALL
    screen = StubScreen([ord("q")])
    state = new_game(1234, width, height)
    run(screen, state)

    assert screen.row(0, 0, width) == " " * width, "the stats row is reserved and blank"
    status = screen.row(0, height + 1, width)
    assert status.endswith("Level 1  Seed 1234")
    assert status.strip() == "Level 1  Seed 1234", "no message yet"


def test_run_shows_the_event_message_beside_the_status() -> None:
    # A level wide enough to hold the whole sentence *and* the status, so this test is
    # about the message reaching the status row and nothing else. The narrow case — where
    # the two collide — is the next test.
    width, height = 80, 18
    state = new_game(1234, width, height)
    # '>' at the spawn is the up-staircase, so it reports that there are no down stairs.
    screen = StubScreen([ord(">"), ord("q")])
    final = run(screen, state)

    assert final.turns == 0
    message = events.MESSAGES[EventKind.NO_STAIRS_DOWN]
    assert len(message) + 1 + len("Level 1  Seed 1234") <= width, "both must fit"

    status = screen.row(1, height + 1, width)
    assert len(status) == width
    assert status.startswith(message)
    assert status.endswith("Level 1  Seed 1234")


def test_a_message_too_long_for_the_row_is_clipped_so_the_status_survives() -> None:
    """CONTRACT-v3 §4.2: ``status_right`` always wins.

    The level and the seed are the two things that must stay readable — the seed is what
    makes a run replayable — so a message that would collide with them is truncated to
    ``width - len(status_right) - 1``, never the other way round. The renderer owns the
    rule; this pins that the game feeds it the two halves the right way round, which is
    the only way a caller can get it wrong.
    """
    width, height = SMALL  # 40 columns: 38 + 1 + 18 = 57 needed, so they cannot both fit
    state = new_game(1234, width, height)
    screen = StubScreen([ord(">"), ord("q")])
    run(screen, state)

    message = events.MESSAGES[EventKind.NO_STAIRS_DOWN]
    right = "Level 1  Seed 1234"
    assert len(message) + 1 + len(right) > width, "the collision is the point"

    status = screen.row(1, height + 1, width)
    assert len(status) == width
    assert status.endswith(right), "the level and seed must survive intact"
    clipped = message[: width - len(right) - 1]
    assert status == clipped + " " + right
    assert status.startswith("There are no stairs l")


def test_run_returns_the_state_carrying_the_outcome() -> None:
    screen = StubScreen([ord("<")])
    final = run(screen, new_game(1234, *SMALL))
    assert final.running is False
    assert final.outcome == events.MESSAGES[EventKind.LEFT_DUNGEON]


def test_run_sets_keypad_so_the_arrow_keys_work() -> None:
    screen = StubScreen([ord("q")])
    run(screen, new_game(1234, *SMALL))
    assert screen.keypad_called is True


# ---------------------------------------------------------------------------------------
# Composition with a real generated dungeon — still headless
# ---------------------------------------------------------------------------------------


def test_a_generated_level_can_be_walked_through_step() -> None:
    state = new_game(2026, *SMALL)
    moved_at_least_once = False
    for command in ALL_MOVES:
        after = step(state, command)
        if after.player != state.player:
            moved_at_least_once = True
            assert after.turns == state.turns + 1
            assert world.is_passable(state.level, state.open_doors, *after.player)
        elif after.open_doors != state.open_doors:
            assert after.turns == state.turns + 1  # bumped a door open
        else:
            assert after.turns == state.turns
            assert after.visible is state.visible
    assert moved_at_least_once, "the start cell has at least one walkable neighbour"


def test_turn_count_equals_accepted_moves_plus_doors_opened() -> None:
    state = new_game(31337, *SMALL)
    accepted = opened = 0
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
    state = new_game(4242, 60, 20)
    for key in "jjjlllkkkhhhnnnbbbuuuyyy":
        previous = state
        state = step(state, translate_key(key))
        assert state.explored >= previous.explored
        assert state.open_doors >= previous.open_doors


def test_every_open_door_on_a_generated_level_was_a_door_tile() -> None:
    state = new_game(555, 60, 20)
    for key in "lllljjjjhhhhkkkkllll":
        state = step(state, translate_key(key))
    for x, y in state.open_doors:
        assert state.level.tile_at(x, y) is Tile.DOOR


def test_standing_on_the_start_staircase_the_up_command_is_the_way_out() -> None:
    # G17 makes the spawn the up-staircase, so the very first '<' ends the run.
    state = new_game(1234, *SMALL)
    after = step(state, translate_key("<"))
    assert after.running is False
    assert after.outcome
