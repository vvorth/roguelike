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
5. **Timing lives in ``run`` and nowhere else** (v4). ``step`` and ``advance`` are pure, so
   a whole auto-explore of a real generated level runs to completion in a test with no
   screen, no clock and no waiting; the loop's 100 ms deadline is observed only through a
   stub screen that records the number it was handed.
6. **A turn consumed is one world-tick, and only a turn consumed** (v5). Walking into a
   wall leaves every monster's position and energy and the player's hit points exactly as
   they were — the headline rule of point 1, extended to everything the world now does.

**Monsters are opt-in in this file.** Every generated level is populated at generation
time (CONTRACT-v5 §24.4), which would put wandering animals in the middle of two hundred
tests about fog, staircases and pathfinding that have nothing to say about them. The
``_unpopulated`` fixture below turns spawning off by default; a test that wants the real
population asks for the ``monsters`` fixture by name, and the v5 sections mostly build
their monsters by hand so that every fight is exact rather than incidental.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import inspect
import random
import re
import subprocess
import sys
from collections import deque
from pathlib import Path

import pytest

from roguelike import combat, dungeon, events, fov, game, world
from roguelike.activity import Activity, ActivityKind
from roguelike.events import Event, EventKind
from roguelike.game import (
    ENERGY_THRESHOLD,
    MAX_EVENTS,
    GameState,
    LevelState,
    Player,
    Targeting,
    advance,
    advance_npcs,
    format_help_status,
    format_stats,
    format_status_right,
    help_lines,
    help_page_count,
    help_page_lines,
    interruption,
    level_up,
    new_game,
    play,
    roll_seed,
    run,
    step,
    xp_to_next,
)
from roguelike.items import DAGGER, SHORTBOW
from roguelike.keys import QUIT_COMMAND, Command, CommandKind, translate_key
from roguelike.level import Level
from roguelike.npc import (
    NPC,
    SPECIES_DATA,
    AiState,
    Species,
    spawn_npcs,
)
from roguelike.stats import Actor, Stats, derive
from roguelike.status import REGEN_TURNS, StatusEffect, StatusKind
from roguelike.tiles import Tile


@pytest.fixture(autouse=True)
def _unpopulated(request, monkeypatch):
    """Play a monsterless dungeon unless the test asks for ``monsters``.

    Spawning is real and is tested (see the v5 sections); switching it off by default is
    what keeps two hundred v1-v4 tests about terrain, fog and routing saying what they
    were written to say, rather than quietly becoming tests about whether a jackal
    happened to wander into the corridor.
    """
    if "monsters" in request.fixturenames:
        return
    monkeypatch.setattr(game, "spawn_npcs", lambda rng, level, first_actor_id=1: ())


@pytest.fixture
def monsters():
    """Opt back in to real spawning (CONTRACT-v5 §24.4)."""
    return True

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


#: 7x3. A dead-straight corridor: one cell tall, walled above and below, so every cell of
#: it is *thin* by the 2x2 test (CONTRACT-v4 §18.2) and an auto-walk follows it to the end
#: and stops with nowhere further to go.
#:
#:      0123456
#:   0  #######
#:   1  #.....#
#:   2  #######
CORRIDOR_ROWS = [
    "#######",
    "#.....#",
    "#######",
]


#: 6x5. A corridor that turns south at (2, 1) — the bend an auto-walk must *follow*,
#: rather than stopping at, because a corridor with exactly one way on is not a choice.
#:
#:      012345
#:   0  ######
#:   1  #..###
#:   2  ##.###
#:   3  ##.###
#:   4  ######
BEND_ROWS = [
    "######",
    "#..###",
    "##.###",
    "##.###",
    "######",
]


#: 7x4. A T-junction at (3, 2): the corridor along row 2 meets a branch going north. An
#: auto-walk arriving from the west has two ways on and must stop rather than guess.
#:
#:      0123456
#:   0  #######
#:   1  ###.###
#:   2  #.....#
#:   3  #######
JUNCTION_ROWS = [
    "#######",
    "###.###",
    "#.....#",
    "#######",
]

JUNCTION_CELL = (3, 2)


#: 10x6. A corridor along row 3 running east into a room — the auto-walk must stop on
#: (3, 3), one cell short of the first *wide* cell, without stepping into the room.
#:
#:      0123456789
#:   0  ##########
#:   1  ##########
#:   2  ####....##
#:   3  #.......##
#:   4  ####....##
#:   5  ##########
OPENING_ROWS = [
    "##########",
    "##########",
    "####....##",
    "#.......##",
    "####....##",
    "##########",
]

OPENING_STOP = (3, 3)


#: 9x3. A corridor with a closed door in the middle of it, at (4, 1). An auto-walk bumps
#: the door open, spends the turn, says so — and keeps walking (user decision 3).
#:
#:      012345678
#:   0  #########
#:   1  #...+...#
#:   2  #########
DOOR_CORRIDOR_ROWS = [
    "#########",
    "#...+...#",
    "#########",
]

CORRIDOR_DOOR = (4, 1)


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
AUTO_EXPLORE = Command(CommandKind.AUTO_EXPLORE)
WALK_PREFIX = Command(CommandKind.WALK_PREFIX)

ALL_KINDS = (
    *ALL_MOVES,
    QUIT,
    UNKNOWN,
    DESCEND,
    ASCEND,
    AUTO_EXPLORE,
    WALK_PREFIX,
)

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
    "activity",
    "awaiting_walk",
    "player_actor",
    "npcs",
    "targeting",
    "help_page",
    "awaiting_attack",
    "projectile",
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


def test_levelstate_field_order_and_defaults() -> None:
    # CONTRACT-v5 §24.5 appends `npcs`, with a default, so every v3/v4 three-argument
    # construction still works — asserted directly below.
    fields = dataclasses.fields(LevelState)
    assert [f.name for f in fields] == ["level", "explored", "open_doors", "npcs"]
    for field in fields[:3]:
        assert field.default is dataclasses.MISSING
    assert fields[3].default == ()
    entry = LevelState(room_level(), frozenset(), frozenset())
    assert entry.npcs == ()


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
    # v4 appends exactly two fields, both with defaults, so every v3 construction — and
    # every positional one — keeps working untouched (CONTRACT-v4 §7).
    assert fields[13].default is None        # activity
    assert fields[14].default is False       # awaiting_walk
    # v5 appends exactly three, on the same terms (CONTRACT-v5 §7 v5).
    assert fields[15].default == Player(
        actor=Actor(stats=Stats(10, 10, 10), hp=45)
    )                                        # player_actor
    assert fields[16].default == ()          # npcs
    assert fields[17].default is None        # targeting
    assert fields[18].default is None        # help_page
    assert fields[19].default is False       # awaiting_attack
    assert fields[20].default == ()          # projectile
    assert len(fields) == 21


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
    assert state.activity is None
    assert state.awaiting_walk is False
    assert state.npcs == ()
    assert state.targeting is None
    assert state.player_actor.actor.hp == 45
    assert state.player_actor.level == 1
    assert state.player_actor.melee is DAGGER
    assert state.player_actor.ranged is SHORTBOW


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


def test_ascend_with_no_staircase_known_still_says_so_and_costs_nothing() -> None:
    # v3's rule, kept whole for the case it still covers: an up-staircase that has never
    # been seen is one the character cannot walk to, so `<` still just says so
    # (CONTRACT-v4 §7.4, user decision 2). Standing at (5, 3) the level's `<` at (3, 3) is
    # in plain view, so the fog is cleared by hand to put it back out of knowledge — the
    # travel case is the next test.
    state = start(stairs_level(), player=(5, 3))
    state = dataclasses.replace(
        state,
        explored=state.explored - {UP_CELL},
        visible=state.visible - {UP_CELL},
    )
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
    assert after.activity is None


def test_ascend_off_the_stairs_with_the_staircase_known_starts_travelling() -> None:
    # The v4 half of the same key: the `<` at (3, 3) *is* explored from (5, 3), so `<`
    # becomes a destination rather than a complaint. Still no turn — starting an activity
    # is not an action.
    state = start(stairs_level(), player=(5, 3))
    assert UP_CELL in state.explored
    after = step(state, ASCEND)

    assert after.events == (Event(EventKind.TRAVELLING),)
    assert after.activity == Activity(ActivityKind.TRAVEL, goal=UP_CELL)
    assert after.turns == 0
    assert after.player == state.player


def test_ascend_on_the_down_staircase_is_still_not_a_way_up() -> None:
    # Standing on `>` is not standing on `<`: the command does not take *this* staircase.
    # Having walked the length of the level, the character does know where the up
    # staircase is, so v4 sets off towards it instead of reporting nothing — and either
    # way no turn is consumed and the depth does not change.
    state = walk_to(start(stairs_level()), DOWN_CELL)
    after = step(state, ASCEND)
    assert after.depth == state.depth
    assert after.turns == state.turns
    assert after.player == DOWN_CELL
    assert after.events == (Event(EventKind.TRAVELLING),)
    assert after.activity == Activity(ActivityKind.TRAVEL, goal=UP_CELL)


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


def test_format_stats_is_the_v5_layout() -> None:
    # CONTRACT-v5 §7.13, character for character, at the baseline the game starts from.
    assert (
        format_stats(start(room_level()))
        == "HP 45/45  Lv 1  XP 0/25  Str 10 Agi 10 Vit 10"
    )
    assert format_stats(new_game(1234, *SMALL)) == format_stats(start(room_level()))


def test_format_stats_shows_real_values_not_a_template() -> None:
    state = start(room_level())
    state = dataclasses.replace(
        state,
        player_actor=Player(
            actor=Actor(stats=Stats(str_=12, agi=9, vit=13), hp=37), xp=17, level=3
        ),
    )
    assert format_stats(state) == "HP 37/57  Lv 3  XP 17/225  Str 12 Agi 9 Vit 13"


def test_format_stats_uses_two_spaces_between_fields_and_one_inside_the_stat_block() -> None:
    text = format_stats(start(room_level()))
    assert type(text) is str
    assert text.count("  ") == 3
    assert "Str 10 Agi 10 Vit 10" in text
    assert text == text.strip()  # padding is the renderer's job (CONTRACT-v3 §4.2)


def test_format_stats_max_hp_comes_from_stats_derive_not_a_second_formula() -> None:
    for vit in (1, 5, 10, 20):
        state = dataclasses.replace(
            start(room_level()),
            player_actor=Player(actor=Actor(stats=Stats(10, 10, vit), hp=1)),
        )
        assert f"/{derive(Stats(10, 10, vit)).max_hp}  Lv" in format_stats(state)


def test_format_stats_tracks_damage_taken() -> None:
    state = start(room_level())
    hurt = dataclasses.replace(
        state,
        player_actor=dataclasses.replace(
            state.player_actor,
            actor=dataclasses.replace(state.player_actor.actor, hp=12),
        ),
    )
    assert format_stats(hurt).startswith("HP 12/45")


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
        "roguelike.pathfind",   # v4 (CONTRACT-v4 §10)
        "roguelike.activity",   # v4
        "roguelike.stats",      # v5 (CONTRACT-v5 §10 v5)
        "roguelike.items",      # v5
        "roguelike.status",     # v5
        "roguelike.combat",     # v5
        "roguelike.npc",        # v5
    }
    assert "roguelike.game" not in roguelike_imports
    assert "roguelike.style" not in roguelike_imports
    assert "roguelike.tiles" not in roguelike_imports
    assert "roguelike.generator" not in roguelike_imports, (
        "generation is reached through dungeon now (CONTRACT-v3 §10)"
    )
    assert imported - roguelike_imports <= {
        "__future__",
        "curses",
        "dataclasses",
        "random",  # v5: `random.Random(roll_seed(...))`, never a module-level draw
    }


def test_no_module_reads_a_clock_or_sleeps() -> None:
    """CONTRACT-v4 §0.10: ``stdscr.timeout`` is the only pacing mechanism permitted.

    Both halves of the v4 loop — the ten-turns-a-second pace and instant cancellation —
    fall out of one 100 ms deadline on ``getch``. Anything else here would be a second
    mechanism doing a job the first already does, and a sleeping loop cannot be cancelled
    by a keypress at all.
    """
    for forbidden in (
        "time.sleep",
        "time.time",
        "time.monotonic",
        "perf_counter",
        "datetime",
        "threading",
        "asyncio",
        "select.select",
    ):
        assert forbidden not in GAME_SOURCE

    imported: set[str] = set()
    for node in ast.walk(GAME_TREE):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert "time" not in imported

    # No busy-wait. v5 adds two `while` loops — the energy accumulator (§24.3) and the
    # levelling loop (§7.11) — and both are bounded by arithmetic that shrinks on every
    # pass. The turn loop in `run` is still the only one that waits for anything, and it
    # waits on `getch`, never by spinning on a condition.
    whiles = {
        _enclosing_function(GAME_TREE, node)
        for node in ast.walk(GAME_TREE)
        if isinstance(node, ast.While)
    }
    assert whiles == {"run", "advance_npcs", "level_up"}

    loop = next(
        node
        for node in ast.walk(_function("run"))
        if isinstance(node, ast.While)
    )
    calls_getch = any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == "getch"
        for child in ast.walk(loop)
    )
    assert calls_getch, "every pass of the loop blocks on input rather than spinning"


def test_the_loop_paces_with_timeout_and_nothing_else() -> None:
    node = _function("run")
    timeouts = [
        child
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == "timeout"
    ]
    assert len(timeouts) == 3, (
        "one deadline for an activity, one for ordinary play, one per projectile frame"
    )
    # And `timeout` is called nowhere else in the module — pacing is the loop's alone.
    assert (
        len(
            [
                child
                for child in ast.walk(GAME_TREE)
                if isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "timeout"
            ]
        )
        == 3
    )


def test_advance_holds_no_terminal_and_no_pacing() -> None:
    for name in ("advance", "interruption", "_planned_step", "_finished"):
        node = _function(name)
        for child in ast.walk(node):
            if isinstance(child, ast.Name):
                assert child.id not in {"render", "curses", "stdscr"}
            if isinstance(child, ast.Attribute):
                assert child.attr not in {"timeout", "getch", "draw", "render_to_cells"}


def test_step_does_not_touch_the_renderer_or_curses() -> None:
    for name in (
        "step",
        "advance",
        "interruption",
        "_take_turn",
        "_change_level",
        "_descend",
        "_ascend",
        "_stair_events",
        "_travel_or_report",
        "_explored_passable",
        "_whole_level_passable",
        "_planned_step",
        "_finished",
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
    # `run` and the two presentation helpers it alone calls. The invariant that matters
    # is that no *rule* draws, so those are asserted absent explicitly below.
    assert users <= {"run", "_projectile_frame", "_npc_glyphs"}
    assert "step" not in users and "advance" not in users and "advance_npcs" not in users


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
        "Player",
        "Targeting",
        "GameState",
        "ENERGY_THRESHOLD",
        "MAX_EVENTS",
        "new_game",
        "roll_seed",
        "step",
        "advance",
        "advance_npcs",
        "level_up",
        "xp_to_next",
        "interruption",
        "help_lines",
        "help_page_count",
        "help_page_lines",
        "format_help_status",
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
    assert str(inspect.signature(advance)) == "(state: 'GameState') -> 'GameState'"
    assert str(inspect.signature(interruption)) == (
        "(before: 'GameState', after: 'GameState') -> 'Event | None'"
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
    # `random` is v5's only new stdlib import, and it is imported for the *type* — every
    # generator is built from `roll_seed` inside a function (CONTRACT-v5 §0.12). The next
    # test pins that no module-level draw exists.
    stdlib_or_project = {"__future__", "curses", "dataclasses", "random", "roguelike"}
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
    # v4 adds exactly two (CONTRACT-v4 §5) — the five above are v1-v3's, unchanged. The
    # point of the test is unaltered: nothing beyond the contract's vocabulary exists, and
    # this module invents no key of its own.
    assert {kind.name for kind in CommandKind} == {
        "MOVE",
        "QUIT",
        "UNKNOWN",
        "DESCEND",
        "ASCEND",
        "AUTO_EXPLORE",
        "WALK_PREFIX",
        # v5 adds exactly two (CONTRACT-v5 §5 v5). There is deliberately no ATTACK:
        # walking into a monster *is* the attack (§7.9).
        "FIRE",
        "TARGET_NEXT",
        # The help screen, and an explicit directional attack. Still no WAIT.
        "HELP",
        "ATTACK",
    }
    for name in ("OPEN", "CLOSE", "WAIT", "REST", "SEARCH", "TRAVEL", "RUN"):
        assert f"CommandKind.{name}" not in GAME_SOURCE


def test_no_extra_activity_kind_was_invented() -> None:
    assert {kind.name for kind in ActivityKind} == {
        "TRAVEL",
        "AUTO_EXPLORE",
        "AUTO_WALK",
    }
    for name in ("REST", "SEARCH", "FOLLOW", "DESCEND", "ASCEND"):
        assert f"ActivityKind.{name}" not in GAME_SOURCE


def test_no_route_cache_or_turn_cap_was_added() -> None:
    """CONTRACT-v4 §7.5, §18.1: re-plan every turn, because it is affordable.

    A cached path is the one thing that could go stale against a door opening underneath
    it, and a ``max_turns`` cap would make finishing a level a matter of luck. Neither is
    needed at 0.235 ms a search, so neither may be written.
    """
    assert "path" not in {f.name for f in dataclasses.fields(Activity)}
    for name in ("max_turns", "_cache", "cached", "lru_cache", "memo"):
        # Whole words: v5's monsters carry a `memory` field (CONTRACT-v5 §24.2), which is
        # not a memo table, and the point of this test is that no cache exists.
        assert re.search(rf"\b{name}\b", GAME_SOURCE) is None


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
        #: Every value ``run`` has handed to ``timeout``, in order. This is the whole of
        #: how v4's pacing is observable without a terminal: -1 is "block for a key", 100
        #: is "give the player a tenth of a second to interrupt" (CONTRACT-v4 §7.7).
        self.timeouts: list[int] = []

    def getch(self) -> int:
        assert self._keys, "run asked for more input than the script provides"
        return self._keys.pop(0)

    def timeout(self, delay: int) -> None:
        self.timeouts.append(delay)

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

    # v5 fills the row that stood reserved and blank for three versions (§7.13); it is
    # padded to the full width by the renderer, exactly as the empty string was.
    stats_row = screen.row(0, 0, width)
    assert stats_row == format_stats(state).ljust(width)[:width]
    assert stats_row.startswith("HP 45/45  Lv 1  XP 0/25")
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


# ---------------------------------------------------------------------------------------
# v4 — helpers for driving an activity to its end, headlessly
# ---------------------------------------------------------------------------------------


def corridor_state(rows: list[str], player: tuple[int, int]) -> GameState:
    """A game standing at ``player`` on a hand-built corridor map."""
    return start(build_level(rows, player_start=player))


def walk(state: GameState, dx: int, dy: int) -> GameState:
    """``w`` then a direction: the two keystrokes that start an auto-walk."""
    return step(step(state, WALK_PREFIX), Command(CommandKind.MOVE, dx, dy))


def finish_activity(state: GameState, limit: int = 4000) -> GameState:
    """Call :func:`advance` until the activity clears, and return the final state.

    The limit is a test harness guard, not a game rule: every activity in this project
    terminates by running out of route or out of frontier (CONTRACT-v4 §7.5), and a run
    that hits the limit is a bug in the engine rather than a slow level. Nothing here
    waits, sleeps or reads a clock — ``advance`` is a pure function and this loop runs at
    whatever speed the machine manages.
    """
    ticks = 0
    while state.activity is not None:
        assert ticks < limit, "the activity never finished"
        state = advance(state)
        ticks += 1
    return state


def event_kinds(state: GameState) -> set[EventKind]:
    return {event.kind for event in state.events}


# ---------------------------------------------------------------------------------------
# step — the walk prefix (CONTRACT-v4 §7.4)
# ---------------------------------------------------------------------------------------


def test_walk_prefix_asks_which_way_and_costs_no_turn() -> None:
    state = corridor_state(CORRIDOR_ROWS, (1, 1))
    after = step(state, WALK_PREFIX)

    assert after.awaiting_walk is True
    assert after.events == (Event(EventKind.WALK_WHICH_WAY),)
    assert events.message_for(after.events) == "Walk in which direction?"
    assert after.turns == 0
    assert after.player == state.player
    assert after.activity is None
    assert after.visible is state.visible, "no turn passed, so nothing was recomputed"


def test_a_direction_after_the_prefix_starts_an_auto_walk_without_a_turn() -> None:
    state = step(corridor_state(CORRIDOR_ROWS, (1, 1)), WALK_PREFIX)
    after = step(state, MOVE_E)

    assert after.activity == Activity(ActivityKind.AUTO_WALK, direction=(1, 0))
    assert after.awaiting_walk is False, "the prefix is spent"
    assert after.turns == 0, "advance takes every step of the walk, not step"
    assert after.player == state.player


@pytest.mark.parametrize("command", ALL_MOVES)
def test_every_direction_can_start_a_walk(command: Command) -> None:
    state = step(start(open_level()), WALK_PREFIX)
    after = step(state, command)
    assert after.activity == Activity(
        ActivityKind.AUTO_WALK, direction=(command.dx, command.dy)
    )
    assert after.turns == 0


@pytest.mark.parametrize(
    "command", [QUIT, UNKNOWN, DESCEND, ASCEND, AUTO_EXPLORE, WALK_PREFIX]
)
def test_the_prefix_swallows_any_non_direction_command_whole(command: Command) -> None:
    # CONTRACT-v4 §11: prefix cleared, command consumed, no turn, no action, no event —
    # `w` followed by a typo is a typo, not an error and not half a command.
    state = _with_a_message(step(start(stairs_level(), player=(5, 3)), WALK_PREFIX))
    after = step(state, command)

    assert after.awaiting_walk is False
    assert after.activity is None
    assert after.turns == state.turns
    assert after.player == state.player
    assert after.events == state.events, "not even the message is disturbed"
    assert after.running is True


def test_quit_after_the_prefix_does_not_quit() -> None:
    # The sharpest case of the rule above, and the one a player would notice: `wq` must
    # not end the game.
    state = step(new_game(1234, *SMALL), WALK_PREFIX)
    after = step(state, QUIT)
    assert after.running is True
    assert after.awaiting_walk is False
    assert after.outcome is None


def test_the_prefix_does_not_survive_a_second_command() -> None:
    state = step(step(new_game(1234, *SMALL), WALK_PREFIX), UNKNOWN)
    assert state.awaiting_walk is False
    after = step(state, MOVE_E)
    assert after.activity is None, "the direction is an ordinary move again"


def test_translate_key_reaches_the_prefix_and_the_walk() -> None:
    state = corridor_state(CORRIDOR_ROWS, (1, 1))
    state = step(state, translate_key("w"))
    assert state.awaiting_walk is True
    state = step(state, translate_key("l"))
    assert state.activity == Activity(ActivityKind.AUTO_WALK, direction=(1, 0))


# ---------------------------------------------------------------------------------------
# step — auto-explore (CONTRACT-v4 §7.4)
# ---------------------------------------------------------------------------------------


def test_auto_explore_starts_an_activity_and_costs_no_turn() -> None:
    state = new_game(1234, *SMALL)
    after = step(state, AUTO_EXPLORE)

    assert after.activity == Activity(ActivityKind.AUTO_EXPLORE)
    assert after.turns == 0
    assert after.player == state.player
    assert after.depth == state.depth
    assert after.events == state.events


def test_auto_explore_reaches_step_through_translate_key() -> None:
    after = step(new_game(1234, *SMALL), translate_key("E"))
    assert after.activity == Activity(ActivityKind.AUTO_EXPLORE)


def test_auto_explore_with_nothing_left_reports_it_and_costs_no_turn() -> None:
    # CONTRACT-v4 §11. `step` does not special-case it — starting the activity is
    # unconditional — so the report comes from the first `advance`, which finds no
    # frontier, clears the activity and spends no turn.
    state = finish_activity(step(new_game(1234, *SMALL), AUTO_EXPLORE))
    turns = state.turns

    again = advance(step(state, AUTO_EXPLORE))
    assert again.events == (Event(EventKind.EXPLORED_EVERYTHING),)
    assert again.activity is None
    assert again.turns == turns
    assert again.player == state.player


# ---------------------------------------------------------------------------------------
# step — travel to a known staircase (CONTRACT-v4 §7.4)
# ---------------------------------------------------------------------------------------


def explored_stairs_state() -> GameState:
    """Standing off the stairs on a level whose down-staircase has been seen."""
    state = walk_to(start(stairs_level()), DOWN_CELL)
    state = walk_to(state, UP_CELL)
    assert DOWN_CELL in state.explored
    assert state.player != DOWN_CELL
    return state


def test_descend_off_the_stairs_with_the_staircase_known_starts_travelling() -> None:
    state = explored_stairs_state()
    after = step(state, DESCEND)

    assert after.activity == Activity(ActivityKind.TRAVEL, goal=DOWN_CELL)
    assert after.events == (Event(EventKind.TRAVELLING),)
    assert events.message_for(after.events) == "You travel towards the staircase."
    assert after.turns == state.turns, "starting an activity is not an action"
    assert after.player == state.player
    assert after.depth == state.depth


def test_descend_off_the_stairs_with_nothing_known_starts_no_activity() -> None:
    # v3's rule survives intact (user decision 2): the door on this level is shut, so the
    # eastern half — and the `>` in it — has never been seen.
    state = start(stairs_level())
    assert DOWN_CELL not in state.explored
    after = step(state, DESCEND)

    assert after.events == (Event(EventKind.NO_STAIRS_DOWN),)
    assert after.activity is None
    assert after.turns == 0
    assert after.player == state.player


def test_descend_underfoot_still_descends_rather_than_travelling() -> None:
    state = walk_to(start(stairs_level()), DOWN_CELL)
    after = step(state, DESCEND)
    assert after.depth == 2
    assert after.activity is None
    assert after.turns == state.turns + 1


def test_travel_picks_the_nearest_of_several_known_staircases() -> None:
    rows = [
        "###########",
        "#>...<...>#",
        "###########",
    ]
    level = build_level(rows, player_start=(5, 1))
    state = start(level)
    assert level.stairs_down == ((1, 1), (9, 1))
    assert set(level.stairs_down) <= state.explored

    after = step(state, DESCEND)
    assert after.activity is not None
    assert after.activity.goal in level.stairs_down

    # (5, 1) is equidistant from both, so it proves nothing on its own; one cell either
    # way must settle it, and the answer must be the near staircase both times.
    west = step(start(build_level(rows, player_start=(4, 1))), DESCEND)
    assert west.activity == Activity(ActivityKind.TRAVEL, goal=(1, 1))
    east = step(start(build_level(rows, player_start=(6, 1))), DESCEND)
    assert east.activity == Activity(ActivityKind.TRAVEL, goal=(9, 1))

    # And the far one is chosen when the near one has not been found yet.
    blinkered = dataclasses.replace(
        start(build_level(rows, player_start=(4, 1))),
        explored=start(build_level(rows, player_start=(4, 1))).explored - {(1, 1)},
    )
    assert step(blinkered, DESCEND).activity == Activity(
        ActivityKind.TRAVEL, goal=(9, 1)
    )


# ---------------------------------------------------------------------------------------
# step — a command always clears a running activity (CONTRACT-v4 §7.4)
# ---------------------------------------------------------------------------------------


def running_explore(seed: int = 1234) -> GameState:
    """A game part-way through an auto-explore, so there is an activity to interrupt."""
    state = advance(advance(step(new_game(seed, *SMALL), AUTO_EXPLORE)))
    assert state.activity is not None
    return state


@pytest.mark.parametrize("command", ALL_KINDS)
def test_any_command_clears_a_running_activity(command: Command) -> None:
    """CONTRACT-v4 §7.4, stated as strongly as it can be: **a command means the same
    thing whether or not something was running**. The activity is cleared before the
    command is looked at, so no command can inherit one — and a command that starts an
    activity of its own (``E``, or ``>`` off the stairs) starts the one it always would.
    """
    state = running_explore()
    idle = dataclasses.replace(state, activity=None)
    assert step(state, command) == step(idle, command)


def test_a_move_during_an_activity_clears_it_and_is_an_ordinary_move() -> None:
    state = running_explore()
    after = step(state, MOVE_E)
    assert after.activity is None
    # The move itself is unchanged by the cancellation: it either moved, opened a door, or
    # was rejected, exactly as it would have with no activity running.
    assert after == step(dataclasses.replace(state, activity=None), MOVE_E)


def test_quit_during_an_activity_clears_it_and_quits() -> None:
    state = running_explore()
    after = step(state, QUIT)
    assert after.activity is None
    assert after.running is False


def test_a_travel_activity_does_not_survive_a_descent() -> None:
    state = walk_to(start(stairs_level()), DOWN_CELL)
    state = dataclasses.replace(
        state, activity=Activity(ActivityKind.TRAVEL, goal=UP_CELL)
    )
    after = step(state, DESCEND)
    assert after.depth == 2
    assert after.activity is None


def test_an_explore_activity_does_not_survive_a_descent_or_the_ascent_back() -> None:
    state = walk_to(start(stairs_level()), DOWN_CELL)
    state = dataclasses.replace(state, activity=Activity(ActivityKind.AUTO_EXPLORE))
    below = step(state, DESCEND)
    assert below.depth == 2 and below.activity is None

    below = dataclasses.replace(below, activity=Activity(ActivityKind.AUTO_EXPLORE))
    above = step(below, ASCEND)
    assert above.depth == 1 and above.activity is None


# ---------------------------------------------------------------------------------------
# advance — the idle cases (CONTRACT-v4 §7.5, §11)
# ---------------------------------------------------------------------------------------


def test_advance_with_no_activity_returns_the_state_unchanged() -> None:
    state = new_game(1234, *SMALL)
    assert advance(state) is state


@pytest.mark.parametrize("seed", [1234, 7, 42])
def test_advance_is_idle_however_many_times_it_is_called(seed: int) -> None:
    state = new_game(seed, *SMALL)
    for _ in range(5):
        assert advance(state) is state
    assert state.turns == 0


def test_advance_does_nothing_once_the_game_has_stopped() -> None:
    state = dataclasses.replace(
        new_game(1234, *SMALL),
        running=False,
        activity=Activity(ActivityKind.AUTO_EXPLORE),
    )
    assert advance(state) is state


# ---------------------------------------------------------------------------------------
# advance — auto-walk (CONTRACT-v4 §7.5, §19.2)
# ---------------------------------------------------------------------------------------


def test_auto_walk_follows_a_corridor_to_its_end() -> None:
    state = walk(corridor_state(CORRIDOR_ROWS, (1, 1)), 1, 0)
    final = finish_activity(state)

    assert final.player == (5, 1), "the last cell of the corridor"
    assert final.activity is None
    assert final.events == (Event(EventKind.NOTHING_FURTHER),)
    assert events.message_for(final.events) == "There is nowhere further to go."
    assert final.turns == 4, "one turn per cell walked, and none for stopping"


def test_auto_walk_follows_a_bend() -> None:
    # The corridor turns south at (2, 1); a walk started eastwards must go round it
    # rather than stopping, because a corridor with one way on is not a choice.
    state = walk(corridor_state(BEND_ROWS, (1, 1)), 1, 0)
    final = finish_activity(state)

    assert final.player == (2, 3)
    assert final.events == (Event(EventKind.NOTHING_FURTHER),)
    assert final.turns == 3


def test_auto_walk_stops_at_a_junction() -> None:
    state = walk(corridor_state(JUNCTION_ROWS, (1, 2)), 1, 0)
    final = finish_activity(state)

    assert final.player == JUNCTION_CELL
    assert final.activity is None
    assert final.events == (Event(EventKind.STOPPED_AT_JUNCTION),)
    assert events.message_for(final.events) == "You stop at a junction."


def test_auto_walk_stops_before_an_opening_without_entering_it() -> None:
    state = walk(corridor_state(OPENING_ROWS, (1, 3)), 1, 0)
    final = finish_activity(state)

    assert final.player == OPENING_STOP
    assert final.activity is None
    assert final.events == (Event(EventKind.STOPPED_AT_OPENING),)
    assert events.message_for(final.events) == "You stop before the opening."
    assert final.level.is_walkable(4, 3), "the room really is one step further on"


def test_auto_walk_into_a_wall_stops_at_once_without_a_turn() -> None:
    state = walk(corridor_state(CORRIDOR_ROWS, (1, 1)), -1, 0)
    final = advance(state)

    assert final.player == (1, 1)
    assert final.turns == 0
    assert final.activity is None
    assert final.events == (Event(EventKind.NOTHING_FURTHER),)


def test_auto_walk_opens_a_door_and_keeps_walking() -> None:
    # User decision 3, and the rule most easily got wrong: the door opens, costs its turn,
    # says so — and the walk is still running afterwards.
    state = walk(corridor_state(DOOR_CORRIDOR_ROWS, (1, 1)), 1, 0)

    opened = None
    while state.activity is not None and opened is None:
        before = state
        state = advance(state)
        if state.open_doors != before.open_doors:
            opened = state

    assert opened is not None, "the walk reached the door"
    assert opened.open_doors == frozenset({CORRIDOR_DOOR})
    assert opened.events == (Event(EventKind.DOOR_OPENED),)
    assert opened.turns == before.turns + 1, "opening a door costs its turn"
    assert opened.player == before.player, "and does not move you"
    assert opened.activity is not None, "and does not stop the walk"

    final = finish_activity(state)
    assert final.player == (7, 1), "the walk carried on past the door"
    assert final.events == (Event(EventKind.NOTHING_FURTHER),)


def test_auto_walk_carries_came_from_forward_so_it_never_doubles_back() -> None:
    state = walk(corridor_state(CORRIDOR_ROWS, (1, 1)), 1, 0)
    assert state.activity is not None and state.activity.came_from is None

    seen = [state.player]
    while state.activity is not None:
        previous = state.player
        state = advance(state)
        if state.activity is not None and state.player != previous:
            assert state.activity.came_from == previous
            seen.append(state.player)
    assert seen == [(1, 1), (2, 1), (3, 1), (4, 1), (5, 1)]
    assert len(set(seen)) == len(seen), "no cell is visited twice"


def test_auto_walk_never_changes_depth() -> None:
    state = walk(start(stairs_level(), player=(5, 3)), 1, 0)
    final = finish_activity(state)
    assert final.depth == 1
    assert final.saved == {}


# ---------------------------------------------------------------------------------------
# advance — travel (CONTRACT-v4 §7.5)
# ---------------------------------------------------------------------------------------


def test_travel_walks_to_the_staircase_and_arrives() -> None:
    state = step(explored_stairs_state(), DESCEND)
    final = finish_activity(state)

    assert final.player == DOWN_CELL
    assert final.activity is None
    assert final.events == (Event(EventKind.ARRIVED),)
    assert events.message_for(final.events) == "You arrive at the staircase."
    assert final.turns > state.turns, "walking there costs a turn per step"
    assert final.depth == 1, "travel does not take the staircase for you"


def test_travel_leaves_the_player_standing_on_the_staircase_ready_to_descend() -> None:
    state = finish_activity(step(explored_stairs_state(), DESCEND))
    after = step(state, DESCEND)
    assert after.depth == 2


def test_travel_to_the_cell_already_underfoot_arrives_at_once() -> None:
    state = dataclasses.replace(
        start(stairs_level(), player=(5, 3)),
        activity=Activity(ActivityKind.TRAVEL, goal=(5, 3)),
    )
    after = advance(state)
    assert after.events == (Event(EventKind.ARRIVED),)
    assert after.activity is None
    assert after.turns == state.turns


def test_travel_to_an_unreachable_goal_reports_nowhere_further() -> None:
    # A staircase seen but walled off. It cannot arise from today's generator, which
    # connects everything; the fog is set by hand so the seam is pinned anyway.
    level = build_level(["#####", "#.#>#", "#####"], player_start=(1, 1))
    state = start(level)
    state = dataclasses.replace(state, explored=state.explored | {(3, 1)})

    started = step(state, DESCEND)
    assert started.activity == Activity(ActivityKind.TRAVEL, goal=(3, 1))
    assert started.events == (Event(EventKind.TRAVELLING),)

    after = advance(started)
    assert after.events == (Event(EventKind.NOTHING_FURTHER),)
    assert after.activity is None
    assert after.turns == state.turns
    assert after.player == (1, 1)


def test_travel_never_routes_over_ground_that_has_not_been_seen() -> None:
    # The character walks a route they could have worked out. Forgetting the middle of the
    # corridor must make the far staircase unreachable, not merely a longer walk.
    state = explored_stairs_state()
    blinkered = dataclasses.replace(
        state, explored=state.explored - {(12, y) for y in range(state.level.height)}
    )
    started = step(blinkered, DESCEND)
    assert started.activity is not None
    final = finish_activity(started)
    assert final.player != DOWN_CELL
    assert final.events == (Event(EventKind.NOTHING_FURTHER),)


# ---------------------------------------------------------------------------------------
# advance — auto-explore, end to end on real generated levels
# ---------------------------------------------------------------------------------------


def walkable_cells(level: Level) -> set[tuple[int, int]]:
    return {
        (x, y)
        for y in range(level.height)
        for x in range(level.width)
        if level.is_walkable(x, y)
    }


@pytest.mark.parametrize("seed", [1234, 7, 42])
def test_auto_explore_explores_the_level_and_says_when_it_is_done(seed: int) -> None:
    state = new_game(seed, *SMALL)
    walkable = walkable_cells(state.level)

    running = step(state, AUTO_EXPLORE)
    announcements = 0
    ticks = 0
    while running.activity is not None:
        assert ticks < 4000, "the exploration never terminated"
        running = advance(running)
        announcements += EventKind.EXPLORED_EVERYTHING in event_kinds(running)
        ticks += 1

    covered = len(running.explored & walkable) / len(walkable)
    assert covered >= 0.95, f"seed {seed} only reached {covered:.1%}"
    assert running.activity is None
    assert announcements == 1, "the level is finished exactly once"
    assert running.events == (Event(EventKind.EXPLORED_EVERYTHING),)
    assert running.depth == 1, "auto-explore never descends (user decision 1)"
    assert running.saved == {}
    assert running.running is True


def test_auto_explore_opens_doors_and_carries_on() -> None:
    state = step(new_game(1234, *SMALL), AUTO_EXPLORE)
    opened_at = None
    while state.activity is not None:
        before = state
        state = advance(state)
        if state.open_doors > before.open_doors and opened_at is None:
            opened_at = state

    assert opened_at is not None, "the seed's level has a door on the way"
    assert opened_at.events == (Event(EventKind.DOOR_OPENED),)
    assert opened_at.activity is not None, "a door does not interrupt (user decision 3)"
    assert state.open_doors >= opened_at.open_doors
    assert len(state.open_doors) > 1, "and the ones after the first are opened too"
    for x, y in state.open_doors:
        assert state.level.tile_at(x, y) is Tile.DOOR


def test_auto_explore_does_not_stall_when_the_player_stands_on_a_frontier() -> None:
    """T20's one request of this module, and the reason ``advance`` subtracts the
    player's own cell from the frontier goals.

    A character whose own cell borders the unknown *is* standing on a frontier. Leave it
    in the goal set and the search is asked to reach where it already is: it answers with
    a one-cell path that has no step in it, and the activity gets nowhere — for ever, and
    without saying so. It cannot happen at the default sight radius, where every
    neighbour is already seen, so the case is built here on purpose with a radius of one.
    """
    state = start(open_level(player_start=(2, 2)), radius=1)
    assert (1, 1) not in state.explored, "the diagonals are outside a radius of one"
    assert state.player in game.frontier_cells(
        state.level, state.explored, state.open_doors
    ), "the character really is standing on a frontier"

    after = advance(step(state, AUTO_EXPLORE))
    assert after.turns == state.turns + 1, "a turn was actually taken"
    assert after.player != state.player
    assert after.activity is not None


def test_auto_explore_only_ever_grows_what_is_known() -> None:
    state = step(new_game(7, *SMALL), AUTO_EXPLORE)
    while state.activity is not None:
        before = state
        state = advance(state)
        assert state.explored >= before.explored
        assert state.open_doors >= before.open_doors
        assert state.turns in (before.turns, before.turns + 1)
        assert state.depth == before.depth
        assert state.level is before.level


def test_auto_explore_costs_one_turn_per_advance_that_did_something() -> None:
    state = step(new_game(42, *SMALL), AUTO_EXPLORE)
    ticks = turns_spent = 0
    while state.activity is not None:
        before = state
        state = advance(state)
        ticks += 1
        turns_spent += state.turns - before.turns
    assert turns_spent == state.turns
    assert turns_spent == ticks - 1, "every tick but the last one that finished it"


# ---------------------------------------------------------------------------------------
# advance — purity (CONTRACT-v4 §0.10)
# ---------------------------------------------------------------------------------------


def activity_states() -> list[GameState]:
    """One state per activity kind, each with a turn genuinely left to take."""
    return [
        step(explored_stairs_state(), DESCEND),
        step(new_game(1234, *SMALL), AUTO_EXPLORE),
        walk(corridor_state(CORRIDOR_ROWS, (1, 1)), 1, 0),
        walk(corridor_state(DOOR_CORRIDOR_ROWS, (3, 1)), 1, 0),  # bumps a door open
        dataclasses.replace(
            start(stairs_level(), player=(5, 3)),
            activity=Activity(ActivityKind.TRAVEL, goal=(5, 3)),  # finishes at once
        ),
    ]


@pytest.mark.parametrize("index", range(5))
def test_advance_never_mutates_its_input(index: int) -> None:
    state = activity_states()[index]
    before = snapshot(state)
    level_snapshot = copy.deepcopy(state.level)
    saved_snapshot = copy.deepcopy(state.saved)
    activity_snapshot = copy.deepcopy(state.activity)

    advance(state)

    for name, value in before.items():
        assert getattr(state, name) == value
    assert state.level is before["level"]
    assert state.visible is before["visible"]
    assert state.explored is before["explored"]
    assert state.open_doors is before["open_doors"]
    assert state.saved is before["saved"]
    assert state.events is before["events"]
    assert state.activity is before["activity"]
    assert state.level == level_snapshot
    assert state.saved == saved_snapshot
    assert state.activity == activity_snapshot


@pytest.mark.parametrize("index", range(5))
def test_advance_is_deterministic(index: int) -> None:
    state = activity_states()[index]
    assert advance(state) == advance(state)


def test_advance_does_not_mutate_the_sets_or_the_saved_dict_it_was_handed() -> None:
    entry = LevelState(room_level(), frozenset({(1, 1)}), frozenset())
    saved = {1: entry}
    base = walk(corridor_state(DOOR_CORRIDOR_ROWS, (3, 1)), 1, 0)
    explored, visible, open_doors = base.explored, base.visible, base.open_doors
    state = dataclasses.replace(base, saved=saved, depth=2)

    after = advance(state)

    assert saved == {1: entry}
    assert saved is state.saved
    assert explored == base.explored
    assert visible == base.visible
    assert open_doors == frozenset()
    assert after.open_doors == frozenset({CORRIDOR_DOOR})
    assert after.open_doors is not open_doors


def test_advance_shares_the_level_object_rather_than_copying_it() -> None:
    for state in activity_states():
        assert advance(state).level is state.level


def test_advance_recomputes_fov_exactly_when_a_turn_was_spent() -> None:
    for state in activity_states():
        after = advance(state)
        if after.turns == state.turns:
            assert after.visible is state.visible
        else:
            assert after.visible == fov.compute_visible(
                after.level, after.open_doors, after.player, after.radius
            )
            assert after.explored == state.explored | after.visible


# ---------------------------------------------------------------------------------------
# interruption — the seam, pinned closed (CONTRACT-v4 §7.6)
# ---------------------------------------------------------------------------------------


def test_interruption_returns_none_for_every_pair_that_can_be_built() -> None:
    # User decision 3 in test form: nothing today interrupts an activity, and if that ever
    # changes it must be a deliberate change to this contract, not a quiet one.
    fresh = new_game(1234, *SMALL)
    walked = step(fresh, MOVE_E)
    door = _at_the_door()
    opened = step(door, MOVE_E)
    stairs = walk_to(start(stairs_level()), DOWN_CELL)
    descended = step(stairs, DESCEND)
    quit_state = step(fresh, QUIT)

    pairs = [
        (fresh, fresh),
        (fresh, walked),
        (walked, fresh),
        (door, opened),
        (stairs, descended),
        (fresh, quit_state),
        (opened, opened),
    ]
    for before, after in pairs:
        assert interruption(before, after) is None


def test_interruption_returns_none_when_a_door_opened_under_an_activity() -> None:
    state = walk(corridor_state(DOOR_CORRIDOR_ROWS, (3, 1)), 1, 0)
    after = advance(state)
    assert after.open_doors == frozenset({CORRIDOR_DOOR}), "the door really did open"
    assert interruption(state, after) is None
    assert after.activity is not None, "so the activity survived it"


def test_interruption_never_mutates_either_state() -> None:
    before_state = _at_the_door()
    after_state = step(before_state, MOVE_E)
    first = snapshot(before_state)
    second = snapshot(after_state)

    interruption(before_state, after_state)

    for name, value in first.items():
        assert getattr(before_state, name) == value
    for name, value in second.items():
        assert getattr(after_state, name) == value


def test_advance_calls_interruption_after_every_activity_turn() -> None:
    # The seam is worth shipping only if it is wired in; assert the call site exists.
    node = _function("advance")
    called = {
        child.func.id
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }
    assert "interruption" in called


# ---------------------------------------------------------------------------------------
# run — the paced loop, through the stub screen (CONTRACT-v4 §7.7)
# ---------------------------------------------------------------------------------------


def exploring_game(seed: int = 1234) -> GameState:
    return step(new_game(seed, *SMALL), AUTO_EXPLORE)


def test_run_blocks_for_a_key_when_no_activity_is_running() -> None:
    screen = StubScreen([ord("q")])
    run(screen, new_game(1234, *SMALL))
    assert screen.timeouts == [-1], "ordinary play must not spin"


def test_run_gives_an_activity_a_hundred_millisecond_deadline() -> None:
    screen = StubScreen([-1, ord("q"), ord("q")])
    final = run(screen, exploring_game())

    assert screen.timeouts[0] == 100
    assert screen.timeouts[-1] == -1, "and blocks again once the activity is gone"
    assert final.running is False


def test_no_key_within_the_deadline_advances_exactly_one_turn() -> None:
    state = exploring_game()
    screen = StubScreen([-1, ord("q"), ord("q")])
    final = run(screen, state)

    expected = advance(state)
    assert final.turns == expected.turns == state.turns + 1
    assert final.player == expected.player
    assert final.player != state.player


def test_a_key_cancels_the_activity_and_is_consumed_doing_so() -> None:
    # The rule the whole design turns on: a panicked keypress stops the walk and does
    # nothing else. If the key also acted as a command it would move you into whatever you
    # were running from.
    state = exploring_game()
    moving_key = ord("l")
    assert step(state, translate_key(moving_key)).player != state.player, (
        "the key really would have moved the player if it had been obeyed"
    )

    screen = StubScreen([moving_key, ord("q")])
    final = run(screen, state)

    assert final.activity is None
    assert final.events == (Event(EventKind.INTERRUPTED),)
    assert events.message_for(final.events) == "You stop."
    assert final.player == state.player, "the cancelling key did not also move you"
    assert final.turns == state.turns, "and did not consume a turn"
    assert screen.timeouts == [100, -1]


@pytest.mark.parametrize("key", ["q", ">", "<", "E", "w", "j", "x"])
def test_any_key_at_all_cancels_and_none_of_them_acts(key: str) -> None:
    state = exploring_game()
    screen = StubScreen([ord(key), ord("q")])
    final = run(screen, state)

    assert final.player == state.player
    assert final.turns == state.turns
    assert final.depth == state.depth
    assert final.awaiting_walk is False
    # `q` quits on the *second* read, never on the first: the first was consumed.
    assert len(screen.frames) == 2


def test_the_loop_alternates_deadline_and_block_as_the_activity_comes_and_goes() -> None:
    state = exploring_game()
    screen = StubScreen([-1, -1, ord("l"), ord("q")])
    run(screen, state)
    assert screen.timeouts == [100, 100, 100, -1]


def test_run_draws_a_frame_for_every_turn_of_an_activity() -> None:
    state = exploring_game()
    screen = StubScreen([-1, -1, -1, ord("q"), ord("q")])
    run(screen, state)
    # Four activity reads plus the final blocking read, one frame drawn before each.
    assert len(screen.frames) == 5
    assert screen.refreshes == len(screen.frames)


def test_run_can_drive_a_whole_auto_explore_to_its_end() -> None:
    """The loop and the pure core agree: pressing ``E`` and holding still explores the
    level, and reaches exactly the state ``advance`` alone reaches.

    Every activity turn is one ``getch`` that returned nothing, so the script is a run of
    ``-1``s — one per turn, plus the one that finds no frontier left and ends it — and
    then a real key. Nothing here waits 100 ms for anything: the stub answers at once, and
    the number is only ever *recorded*.
    """
    state = exploring_game()
    finished = finish_activity(state)
    ticks = finished.turns + 1

    screen = StubScreen([-1] * ticks + [ord("q")])
    final = run(screen, state)

    assert final.activity is None
    assert final.turns == finished.turns
    assert final.explored == finished.explored
    assert final.events == (Event(EventKind.EXPLORED_EVERYTHING),)
    assert screen.timeouts == [100] * ticks + [-1]


def test_run_still_plays_an_ordinary_game_with_no_activity_in_sight() -> None:
    screen = StubScreen([ord("l"), ord("j"), ord("q")])
    final = run(screen, new_game(1234, *SMALL))
    assert screen.timeouts == [-1, -1, -1]
    assert final.running is False
    assert final.activity is None


# =======================================================================================
# v5 — combat, monsters, targeting, levelling and death (CONTRACT-v5 §7 v5)
#
# Monsters are built by hand here rather than spawned, so every fight is exact: the same
# level, the same species, the same starting energy, the same seed. Spawning itself is
# `npc.py`'s to test, and is exercised end to end further down through the `monsters`
# fixture.
# =======================================================================================

FIRE = Command(CommandKind.FIRE)
TARGET_NEXT = Command(CommandKind.TARGET_NEXT)

#: Salts, restated from CONTRACT-v5 §0.12 so a test never has to read the module to know
#: which stream a roll came from. The player is permanently actor 0.
SALT_ATTACK = 1


def make_npc(
    position: tuple[int, int],
    species: Species = Species.RAT,
    actor_id: int = 1,
    *,
    stats: Stats | None = None,
    hp: int | None = None,
    energy: int = 0,
    ai_state: AiState = AiState.HUNTING,
    memory: int = 0,
    effects: tuple[StatusEffect, ...] = (),
) -> NPC:
    """One monster, exactly as described. ``stats`` overrides the species' own."""
    stats = SPECIES_DATA[species].stats if stats is None else stats
    hp = derive(stats).max_hp if hp is None else hp
    return NPC(
        actor_id=actor_id,
        species=species,
        actor=Actor(stats=stats, hp=hp, status_effects=effects),
        position=position,
        energy=energy,
        ai_state=ai_state,
        memory=memory,
    )


def with_npcs(state: GameState, *npcs: NPC) -> GameState:
    return dataclasses.replace(state, npcs=tuple(npcs))


def with_player(
    state: GameState,
    *,
    stats: Stats | None = None,
    hp: int | None = None,
    xp: int = 0,
    level: int = 1,
    regen_counter: int = 0,
    effects: tuple[StatusEffect, ...] = (),
) -> GameState:
    stats = Stats(10, 10, 10) if stats is None else stats
    hp = derive(stats).max_hp if hp is None else hp
    return dataclasses.replace(
        state,
        player_actor=Player(
            actor=Actor(stats=stats, hp=hp, status_effects=effects),
            xp=xp,
            level=level,
            regen_counter=regen_counter,
        ),
    )


#: A player who cannot plausibly die, for tests that need to watch a monster act for
#: dozens of ticks without the run ending underneath them.
UNKILLABLE = Stats(str_=10, agi=10, vit=200)


def tick(state: GameState) -> GameState:
    """One world-tick, seeded the way a consumed turn seeds it.

    ``_take_turn`` advances ``turns`` *before* ``advance_npcs`` runs, and every roll of the
    tick is derived from that counter — so a test that calls ``advance_npcs`` twice on the
    same ``turns`` would replay one tick, not run two. It also replaces ``events`` with
    what the action produced, which is why they are cleared here: ``advance_npcs`` appends
    to them (§7.14), so a helper that left last tick's line in place would accumulate.
    """
    return advance_npcs(
        dataclasses.replace(state, turns=state.turns + 1, events=())
    )


def player_attack_result(
    state: GameState, target: NPC, weapon, strength_applies: bool
):
    """Replay the exact roll ``step`` will make, through the real combat module.

    Nothing is guessed: the seed comes from :func:`roll_seed` with the player's permanent
    ``actor_id`` 0 and the attack salt, and the arithmetic comes from
    :func:`roguelike.combat.resolve_attack` itself. A test can therefore assert the damage
    the game *should* deal without restating a formula that lives elsewhere.
    """
    rng = random.Random(roll_seed(state.master_seed, state.turns, 0, SALT_ATTACK))
    return combat.resolve_attack(
        rng,
        state.player_actor.actor,
        target.actor,
        weapon.damage_min,
        weapon.damage_max,
        strength_applies,
    )


def seed_where_player(state: GameState, target: NPC, weapon, hit: bool) -> int:
    """The first master seed at which the player's next swing hits (or misses)."""
    for seed in range(500):
        candidate = dataclasses.replace(state, master_seed=seed)
        if player_attack_result(candidate, target, weapon, True).hit is hit:
            return seed
    raise AssertionError("no seed produced the requested outcome")


def fight_level() -> Level:
    """A 9x7 hall: a wide-open interior where every neighbour of the centre is floor."""
    return hall_level()


#: 9x5, two 3x3 rooms with a solid wall between them and **no door**. A monster in the
#: eastern room can neither see nor reach the player in the western one, so the world
#: ticks — status effects, regeneration, energy — without a fight breaking out in the
#: middle of a test about poison. `two_room_level` cannot be used for this: its door would
#: be opened within a turn or two and the monster would come through.
CAGE_ROWS = [
    "#########",
    "#...#...#",
    "#...#...#",
    "#...#...#",
    "#########",
]

CAGE_PLAYER = (2, 2)
CAGE_CELL = (6, 2)


def caged(
    species: Species = Species.RAT,
    *,
    stats: Stats | None = None,
    energy: int = 0,
    effects: tuple[StatusEffect, ...] = (),
    hp: int | None = None,
) -> GameState:
    """A game with exactly one monster that can never reach or see the player."""
    return with_npcs(
        start(build_level(CAGE_ROWS, player_start=CAGE_PLAYER)),
        make_npc(
            CAGE_CELL,
            species,
            stats=stats,
            energy=energy,
            effects=effects,
            hp=hp,
            ai_state=AiState.WANDERING,
        ),
    )


# ---------------------------------------------------------------------------------------
# Bump-to-attack (CONTRACT-v5 §7.9)
# ---------------------------------------------------------------------------------------


def test_walking_into_a_monster_attacks_it_and_costs_a_turn() -> None:
    state = with_npcs(start(fight_level()), make_npc((5, 3)))
    after = step(state, MOVE_E)

    assert after.player == (4, 3), "the attacker does not move into the cell"
    assert after.turns == state.turns + 1
    assert after.events[0].kind in {
        EventKind.PLAYER_HIT_NPC,
        EventKind.PLAYER_MISSED_NPC,
    }
    assert after.events[0].name == "rat"


def test_a_hit_reduces_the_monsters_hit_points_by_the_rolled_damage() -> None:
    state = with_npcs(start(fight_level()), make_npc((5, 3)))
    state = dataclasses.replace(
        state, master_seed=seed_where_player(state, state.npcs[0], DAGGER, True)
    )
    expected = player_attack_result(state, state.npcs[0], DAGGER, True)

    after = step(state, MOVE_E)
    assert after.events[0].kind is EventKind.PLAYER_HIT_NPC
    assert after.npcs[0].actor.hp == expected.defender_hp
    assert after.npcs[0].actor.hp < state.npcs[0].actor.hp


def test_a_miss_leaves_the_monster_untouched_but_still_costs_the_turn() -> None:
    state = with_npcs(start(fight_level()), make_npc((5, 3)))
    state = dataclasses.replace(
        state, master_seed=seed_where_player(state, state.npcs[0], DAGGER, False)
    )

    after = step(state, MOVE_E)
    assert after.events[0].kind is EventKind.PLAYER_MISSED_NPC
    assert after.npcs[0].actor == state.npcs[0].actor
    assert after.turns == state.turns + 1


def test_melee_applies_the_strength_modifier() -> None:
    # CONTRACT-v5 §23.2: a wielded melee weapon takes (STR - 10) // 2. With STR 16 that is
    # +3, which is only visible against a defender the roll cannot floor to 1.
    state = with_player(start(fight_level()), stats=Stats(str_=16, agi=10, vit=10))
    state = with_npcs(state, make_npc((5, 3), Species.CAVE_SNAKE))
    state = dataclasses.replace(
        state, master_seed=seed_where_player(state, state.npcs[0], DAGGER, True)
    )

    with_strength = player_attack_result(state, state.npcs[0], DAGGER, True)
    without = player_attack_result(state, state.npcs[0], DAGGER, False)
    assert with_strength.damage == without.damage + 3, "the fixture must distinguish them"

    after = step(state, MOVE_E)
    assert after.npcs[0].actor.hp == with_strength.defender_hp


def test_attacking_diagonally_works_in_all_eight_directions() -> None:
    for command, cell in (
        (MOVE_N, (4, 2)),
        (MOVE_S, (4, 4)),
        (MOVE_E, (5, 3)),
        (MOVE_W, (3, 3)),
        (MOVE_NE, (5, 2)),
        (MOVE_NW, (3, 2)),
        (MOVE_SE, (5, 4)),
        (MOVE_SW, (3, 4)),
    ):
        state = with_npcs(start(fight_level()), make_npc(cell))
        after = step(state, command)
        assert after.player == (4, 3)
        assert after.turns == 1
        assert after.events[0].kind in {
            EventKind.PLAYER_HIT_NPC,
            EventKind.PLAYER_MISSED_NPC,
        }


def test_a_monster_does_not_block_a_step_to_a_different_cell() -> None:
    state = with_npcs(start(fight_level()), make_npc((5, 3)))
    after = step(state, MOVE_N)
    assert after.player == (4, 2)
    assert after.npcs[0].position == (5, 3) or after.npcs[0].position != (4, 2)


def test_killing_a_monster_removes_it_and_says_so() -> None:
    state = with_npcs(start(fight_level()), make_npc((5, 3), hp=1))
    state = dataclasses.replace(
        state, master_seed=seed_where_player(state, state.npcs[0], DAGGER, True)
    )
    after = step(state, MOVE_E)
    assert after.npcs == ()
    assert [e.kind for e in after.events] == [
        EventKind.PLAYER_HIT_NPC,
        EventKind.NPC_KILLED,
    ]
    assert after.events[1].name == "rat"


# ---------------------------------------------------------------------------------------
# The headline rule, extended: no turn means no world-tick (CONTRACT-v5 §7.8)
# ---------------------------------------------------------------------------------------


def test_walking_into_a_wall_does_not_tick_the_world() -> None:
    """The single most important test in this file's v5 half.

    A rejected move has consumed no turn since v1. v5's consequence is that nothing else
    may happen either: no monster moves, none of them banks energy, no poison burns and
    nothing regenerates. Asserted field by field rather than by equality, so a failure
    says *what* moved.
    """
    state = with_player(
        with_npcs(
            start(fight_level()),
            make_npc((7, 1), Species.JACKAL, 1, energy=90),
            make_npc((1, 5), Species.GIANT_BAT, 2, energy=40),
        ),
        hp=30,
        regen_counter=9,
        effects=(StatusEffect(StatusKind.POISONED, 3, 2),),
    )
    state = dataclasses.replace(state, player=(1, 1))

    after = step(state, MOVE_NW)  # into the corner wall

    assert after is state, "nothing happened at all, so nothing was rebuilt"
    assert after.turns == state.turns
    assert [(n.position, n.energy) for n in after.npcs] == [
        (n.position, n.energy) for n in state.npcs
    ]
    assert after.player_actor == state.player_actor
    assert after.player_actor.actor.hp == 30
    assert after.player_actor.regen_counter == 9
    assert after.visible is state.visible


@pytest.mark.parametrize(
    "command",
    [QUIT, UNKNOWN, WALK_PREFIX, AUTO_EXPLORE, TARGET_NEXT],
    ids=["quit", "unknown", "walk-prefix", "auto-explore", "target-next"],
)
def test_no_command_without_a_turn_ticks_the_world(command: Command) -> None:
    state = with_npcs(
        start(fight_level()), make_npc((7, 5), Species.JACKAL, 1, energy=99)
    )
    after = step(state, command)
    assert after.turns == state.turns
    assert after.npcs == state.npcs


def test_a_move_that_is_accepted_does_tick_the_world() -> None:
    state = with_npcs(
        start(fight_level()), make_npc((7, 5), Species.JACKAL, 1, energy=0)
    )
    after = step(state, MOVE_N)
    assert after.turns == 1
    assert after.npcs[0] != state.npcs[0], "the jackal took its action"


def test_bumping_a_door_open_ticks_the_world() -> None:
    state = with_npcs(_at_the_door(), make_npc((1, 1), Species.JACKAL, 1, energy=0))
    after = step(state, MOVE_E)
    assert after.turns == state.turns + 1
    assert DOOR_CELL in after.open_doors
    assert after.npcs[0].energy != state.npcs[0].energy


def test_taking_a_staircase_ticks_the_world() -> None:
    state = with_npcs(
        walk_to(start(stairs_level()), DOWN_CELL),
        make_npc((4, 1), Species.JACKAL, 1, energy=0),
    )
    before_turns = state.turns
    after = step(state, DESCEND)
    assert after.turns == before_turns + 1
    assert after.depth == 2
    # The departed level's monsters are filed away untouched — the tick belongs to the
    # level the player is now standing on.
    assert after.saved[1].npcs == state.npcs


# ---------------------------------------------------------------------------------------
# NPC turns and the energy rule (CONTRACT-v5 §24.3)
# ---------------------------------------------------------------------------------------


def actions_taken(before: NPC, after: NPC, speed: int) -> int:
    """How many actions a monster took, read off its energy: bank speed, spend 100 each."""
    spent = before.energy + speed - after.energy
    assert spent % ENERGY_THRESHOLD == 0
    return spent // ENERGY_THRESHOLD


def test_a_baseline_speed_monster_acts_exactly_once_per_tick() -> None:
    state = caged(stats=Stats(10, 10, 10))
    assert derive(state.npcs[0].actor.stats).speed == 100
    for _ in range(10):
        after = tick(state)
        assert actions_taken(state.npcs[0], after.npcs[0], 100) == 1
        state = after


def test_a_fast_monster_acts_eighteen_times_in_ten_ticks() -> None:
    # Speed 180 (the giant bat): two actions on some ticks, one on others, never a
    # fractional action and never a lost one.
    state = caged(Species.GIANT_BAT)
    assert derive(state.npcs[0].actor.stats).speed == 180
    counts = []
    for _ in range(10):
        after = tick(state)
        counts.append(actions_taken(state.npcs[0], after.npcs[0], 180))
        state = after
    assert sum(counts) == 18
    assert set(counts) == {1, 2}


def test_a_slow_monster_acts_eight_times_in_ten_ticks() -> None:
    state = caged(Species.CAVE_SNAKE)
    assert derive(state.npcs[0].actor.stats).speed == 80
    counts = []
    for _ in range(10):
        after = tick(state)
        counts.append(actions_taken(state.npcs[0], after.npcs[0], 80))
        state = after
    assert sum(counts) == 8
    assert set(counts) == {0, 1}


def test_energy_carries_across_ticks_rather_than_resetting() -> None:
    state = caged(Species.CAVE_SNAKE, energy=0)
    first = tick(state)
    assert first.npcs[0].energy == 80, "banked, not spent"
    second = tick(first)
    assert second.npcs[0].energy == 60, "160 banked, one action spent"


def test_monsters_act_in_actor_id_order() -> None:
    # Two hunters, one free cell between them and the player. The lower actor_id plans
    # first and takes it; the higher one must find another way, because `game.py` folds
    # each accepted move into `occupied` before the next monster plans.
    rows = [
        "#####",
        "#...#",
        "#.#.#",
        "#...#",
        "#####",
    ]
    state = start(build_level(rows, player_start=(1, 2)))
    state = with_npcs(
        state,
        make_npc((3, 1), actor_id=1, stats=Stats(10, 10, 10)),
        make_npc((3, 3), actor_id=2, stats=Stats(10, 10, 10)),
    )
    after = tick(state)
    assert after.npcs[0].position == (2, 1)
    assert after.npcs[1].position != (2, 1)
    positions = [n.position for n in after.npcs]
    assert len(set(positions)) == len(positions)


def test_two_monsters_never_take_the_same_cell_over_a_long_run() -> None:
    state = with_player(
        with_npcs(
            start(fight_level()),
            make_npc((1, 1), Species.RAT, 1, ai_state=AiState.WANDERING),
            make_npc((7, 1), Species.JACKAL, 2, ai_state=AiState.WANDERING),
            make_npc((1, 5), Species.GIANT_BAT, 3, ai_state=AiState.WANDERING),
            make_npc((7, 5), Species.CAVE_SNAKE, 4, ai_state=AiState.WANDERING),
        ),
        stats=UNKILLABLE,
    )
    for _ in range(60):
        state = tick(state)
        cells = [n.position for n in state.npcs]
        assert len(set(cells)) == len(cells), "monsters do not stack"
        assert state.player not in cells, "and never stand on the player"


def test_a_monster_adjacent_to_the_player_attacks_it() -> None:
    state = with_npcs(start(fight_level()), make_npc((5, 3), Species.JACKAL))
    hits = 0
    for _ in range(6):
        before_hp = state.player_actor.actor.hp
        state = tick(state)
        kinds = [e.kind for e in state.events]
        assert kinds and kinds[0] in {
            EventKind.NPC_HIT_PLAYER,
            EventKind.NPC_MISSED_PLAYER,
        }
        assert state.events[0].name == "jackal"
        if kinds[0] is EventKind.NPC_HIT_PLAYER:
            hits += 1
            assert state.player_actor.actor.hp < before_hp
    assert hits, "six jackal bites at 85% cannot all miss"


def test_a_hunting_monster_walks_towards_the_player() -> None:
    state = with_player(
        with_npcs(start(fight_level()), make_npc((7, 5), Species.RAT)), stats=UNKILLABLE
    )
    first = tick(state)
    assert max(
        abs(first.npcs[0].position[0] - 4), abs(first.npcs[0].position[1] - 3)
    ) < max(abs(7 - 4), abs(5 - 3))


def test_an_uneventful_tick_on_an_empty_level_returns_the_same_state() -> None:
    # CONTRACT-v5 §11.1: still the same object — but as the *result* of a tick in which
    # nothing happened, not because the function refused to run.
    state = start(fight_level())
    assert state.npcs == ()
    assert advance_npcs(state) is state
    assert advance_npcs(with_player(state, hp=45, regen_counter=0)) is not None


def test_a_wounded_player_regenerates_on_a_level_with_nothing_alive_on_it() -> None:
    """CONTRACT-v5 §11.1, the defect this amendment corrects.

    Gating the whole tick on ``state.npcs`` froze healing the moment a floor was cleared —
    and most of a level's turns are walked after its monsters are dead. Regeneration is
    what takes floor clears from 0.0% to 61.5% (RESEARCH-v5 §7), so freezing it there
    silently restores the unplayable balance.
    """
    empty = with_player(start(fight_level()), hp=10)
    assert empty.npcs == ()
    for _ in range(40):
        empty = tick(empty)
    assert empty.player_actor.actor.hp == 14


def test_regeneration_does_not_depend_on_a_monster_being_alive() -> None:
    # The same wounded player, the same forty ticks, with and without something on the
    # level: the status phase is not gated on `state.npcs`.
    empty = with_player(start(fight_level()), hp=10)
    occupied = with_player(caged(), hp=10)
    for _ in range(40):
        empty = tick(empty)
        occupied = tick(occupied)
    assert empty.player_actor.actor.hp == occupied.player_actor.actor.hp == 14


def test_poison_burns_and_expires_on_a_level_with_nothing_alive_on_it() -> None:
    state = with_player(
        start(fight_level()), hp=40, effects=(StatusEffect(StatusKind.POISONED, 5, 2),)
    )
    assert state.npcs == ()
    for expected_hp, expected_left in ((38, 4), (36, 3), (34, 2), (32, 1), (30, 0)):
        state = tick(state)
        assert state.player_actor.actor.hp == expected_hp
        assert EventKind.POISON_DAMAGE in {e.kind for e in state.events}
        effects = state.player_actor.actor.status_effects
        assert (effects[0].remaining_turns if effects else 0) == expected_left
    assert state.player_actor.actor.status_effects == (), "five turns, five burns"
    after = tick(state)
    assert after.player_actor.actor.hp == 30, "and then it is over"


def test_poison_can_kill_on_an_empty_level_through_the_same_death_path() -> None:
    state = with_player(
        start(fight_level()), hp=1, effects=(StatusEffect(StatusKind.POISONED, 5, 2),)
    )
    dead = tick(state)
    assert dead.running is False
    assert dead.player_actor.actor.hp <= 0
    assert dead.outcome == events.message_for(dead.events)
    assert [e.kind for e in dead.events] == [
        EventKind.POISON_DAMAGE,
        EventKind.PLAYER_DIED,
    ]
    assert "The poison burns. You die..." == events.message_for(dead.events)


def test_a_full_health_player_banks_no_healing_while_untouched() -> None:
    # The counter does not run at full health, which is what makes an uneventful tick
    # genuinely uneventful. A player wounded after a quiet stretch therefore waits the
    # whole REGEN_TURNS rather than being healed by ticks banked while unhurt.
    state = start(fight_level())
    for _ in range(REGEN_TURNS * 2):
        state = tick(state)
    assert state.player_actor.regen_counter == 0
    state = with_player(state, hp=44)
    for _ in range(REGEN_TURNS - 1):
        state = tick(state)
    assert state.player_actor.actor.hp == 44
    assert tick(state).player_actor.actor.hp == 45


def test_a_wandering_monster_that_sees_the_player_starts_hunting() -> None:
    state = with_player(
        with_npcs(
            start(fight_level()),
            make_npc((7, 5), Species.RAT, ai_state=AiState.WANDERING),
        ),
        stats=UNKILLABLE,
    )
    after = tick(state)
    assert after.npcs[0].ai_state is AiState.HUNTING
    assert after.npcs[0].memory == 0


def test_a_hunter_that_loses_sight_forgets_after_forget_ticks() -> None:
    # A monster sealed in its own little room can never see the player, so `memory` grows
    # by one on every action and the revert happens on the action after FORGET_TICKS.
    rows = [
        "#######",
        "#.#...#",
        "#.#...#",
        "#######",
    ]
    state = with_player(
        with_npcs(
            start(build_level(rows, player_start=(5, 1))),
            make_npc((1, 1), stats=Stats(10, 10, 10), ai_state=AiState.HUNTING),
        ),
        stats=UNKILLABLE,
    )
    memories = []
    for _ in range(7):
        state = tick(state)
        memories.append((state.npcs[0].ai_state, state.npcs[0].memory))
    assert memories[0] == (AiState.HUNTING, 1)
    assert memories[4] == (AiState.HUNTING, 5)
    assert memories[5] == (AiState.WANDERING, 0), "past FORGET_TICKS it gives up"


def test_a_monster_bumps_a_closed_door_open_and_says_so_only_when_it_is_seen() -> None:
    # 9x5 two rooms joined by a door at (4, 2); the player stands in the west room and can
    # see the door, and a hunter in the east room must open it to reach them.
    state = start(two_room_level())
    state = with_player(
        with_npcs(state, make_npc((5, 2), stats=Stats(10, 10, 10))), stats=UNKILLABLE
    )
    assert DOOR_CELL in state.visible

    after = tick(state)
    assert DOOR_CELL in after.open_doors
    assert after.npcs[0].position == (5, 2), "opening the door spends the action"
    assert [e.kind for e in after.events] == [EventKind.DOOR_OPENED]

    unseen = dataclasses.replace(state, visible=state.visible - {DOOR_CELL})
    quiet = tick(unseen)
    assert DOOR_CELL in quiet.open_doors
    assert quiet.events == (), "an off-screen door does not narrate itself"


# ---------------------------------------------------------------------------------------
# Levels are frozen while the player is elsewhere (CONTRACT-v5 §24.5)
# ---------------------------------------------------------------------------------------


def test_monsters_on_a_level_the_player_has_left_do_not_move() -> None:
    state = walk_to(start(stairs_level()), DOWN_CELL)
    state = with_npcs(
        state,
        make_npc((4, 1), Species.JACKAL, 1, energy=17, ai_state=AiState.WANDERING),
        make_npc((5, 5), Species.RAT, 2, energy=63, ai_state=AiState.HUNTING, memory=2),
    )
    upstairs = state.npcs

    below = step(state, DESCEND)
    assert below.saved[1].npcs == upstairs

    for _ in range(8):
        below = step(below, MOVE_N if below.turns % 2 else MOVE_S)
        assert below.saved[1].npcs == upstairs, "frozen, tick after tick"

    back = step(below, ASCEND)
    # Arriving is itself a consumed turn, so the level the player steps back onto ticks
    # exactly once — never once per turn spent below.
    assert back.depth == 1
    assert [n.actor_id for n in back.npcs] == [1, 2]
    for before_npc, after_npc in zip(upstairs, back.npcs):
        speed = derive(before_npc.actor.stats).speed
        assert actions_taken(before_npc, after_npc, speed) <= 2
        assert max(
            abs(after_npc.position[0] - before_npc.position[0]),
            abs(after_npc.position[1] - before_npc.position[1]),
        ) <= 2


def test_a_level_is_populated_once_and_never_repopulated(monsters) -> None:
    # Level 1 is hand-built and empty; level 2 is generated, and therefore populated, the
    # first time the player descends onto it.
    state = walk_to(start(stairs_level()), DOWN_CELL)
    first = step(state, DESCEND)
    below = first.npcs
    back = step(first, ASCEND)
    again = step(back, DESCEND)
    assert [n.actor_id for n in again.npcs] == [n.actor_id for n in below]
    assert [n.species for n in again.npcs] == [n.species for n in below]


def test_a_new_game_is_populated_from_the_levels_own_seed(monsters) -> None:
    first = new_game(1234, *SMALL)
    again = new_game(1234, *SMALL)
    assert first.npcs == again.npcs
    assert first.npcs == spawn_npcs(random.Random(first.level.seed), first.level)
    assert len(first.npcs) <= 6
    assert new_game(99, *SMALL).npcs != first.npcs


# ---------------------------------------------------------------------------------------
# Status effects, regeneration and death (CONTRACT-v5 §22, §7.12)
# ---------------------------------------------------------------------------------------


def test_poison_burns_once_per_tick_and_expires() -> None:
    state = caged()
    state = with_player(
        state, hp=40, effects=(StatusEffect(StatusKind.POISONED, 3, 2),)
    )
    first = tick(state)
    assert first.player_actor.actor.hp == 38
    assert EventKind.POISON_DAMAGE in {e.kind for e in first.events}
    second = tick(first)
    assert second.player_actor.actor.hp == 36
    third = tick(second)
    assert third.player_actor.actor.hp == 34
    assert third.player_actor.actor.status_effects == (), "three turns, three burns"
    fourth = tick(third)
    assert fourth.player_actor.actor.hp == 34


def test_poison_ticks_for_an_actor_whose_energy_did_not_cross_the_threshold() -> None:
    # A cave snake at speed 80 takes no action on the first tick. Its poison must burn
    # anyway: CONTRACT-v5 §22.3 decouples the status cadence from the energy scheduler.
    state = caged(
        Species.CAVE_SNAKE,
        energy=0,
        effects=(StatusEffect(StatusKind.POISONED, 4, 3),),
    )
    poisoned = state.npcs[0]
    after = tick(state)
    assert actions_taken(poisoned, after.npcs[0], 80) == 0, "it did not act"
    assert after.npcs[0].actor.hp == poisoned.actor.hp - 3, "but it still burned"


def test_the_player_regenerates_one_hit_point_every_regen_turns() -> None:
    state = with_player(caged(), hp=20, regen_counter=0)
    for turn in range(1, REGEN_TURNS):
        state = tick(state)
        assert state.player_actor.actor.hp == 20, f"not yet at tick {turn}"
    state = tick(state)
    assert state.player_actor.actor.hp == 21
    assert state.player_actor.regen_counter == 0


def test_regeneration_is_capped_at_max_hp() -> None:
    state = with_player(caged(), regen_counter=REGEN_TURNS - 1)
    assert state.player_actor.actor.hp == 45
    after = tick(state)
    assert after.player_actor.actor.hp == 45


def test_monsters_do_not_regenerate() -> None:
    state = caged(Species.JACKAL, hp=3)
    for _ in range(REGEN_TURNS * 3):
        state = tick(state)
        assert state.npcs[0].actor.hp <= 3


def test_death_by_a_blow_ends_the_run() -> None:
    state = with_player(
        with_npcs(start(fight_level()), make_npc((5, 3), Species.JACKAL)), hp=1
    )
    for _ in range(20):
        state = tick(state)
        if not state.running:
            break
    assert state.running is False
    assert EventKind.PLAYER_DIED in {e.kind for e in state.events}
    assert state.outcome == events.message_for(state.events)
    assert "You die..." in state.outcome
    assert state.activity is None


def test_death_by_poison_reaches_exactly_the_same_end_state_shape() -> None:
    poisoned = with_player(
        caged(), hp=2, effects=(StatusEffect(StatusKind.POISONED, 5, 2),)
    )
    by_poison = tick(poisoned)

    by_blow = with_player(
        with_npcs(start(fight_level()), make_npc((5, 3), Species.JACKAL)), hp=1
    )
    for _ in range(20):
        by_blow = tick(by_blow)
        if not by_blow.running:
            break

    for ended in (by_poison, by_blow):
        assert ended.running is False
        assert ended.player_actor.actor.hp <= 0
        assert ended.outcome == events.message_for(ended.events)
        assert EventKind.PLAYER_DIED in {e.kind for e in ended.events}
    assert "The poison burns." in events.message_for(by_poison.events)
    assert "You die..." in events.message_for(by_poison.events)
    # One path, one shape: the only difference is which event got there first.
    assert (by_poison.running, by_poison.activity, by_poison.targeting) == (
        by_blow.running,
        by_blow.activity,
        by_blow.targeting,
    )


def test_when_poison_kills_the_player_no_monster_acts_that_tick() -> None:
    monsters_before = (
        make_npc((5, 3), Species.JACKAL, 1, energy=99),
        make_npc((7, 5), Species.RAT, 2, energy=99),
    )
    state = with_player(
        with_npcs(start(fight_level()), *monsters_before),
        hp=1,
        effects=(StatusEffect(StatusKind.POISONED, 5, 2),),
    )
    after = tick(state)
    assert after.running is False
    assert after.npcs == monsters_before, "the tick stopped before anyone moved"
    assert {e.kind for e in after.events} == {
        EventKind.POISON_DAMAGE,
        EventKind.PLAYER_DIED,
    }


def test_a_dead_player_is_inert_for_every_command() -> None:
    state = with_player(
        caged(), hp=1, effects=(StatusEffect(StatusKind.POISONED, 5, 5),)
    )
    dead = tick(state)
    assert dead.running is False
    for command in (*ALL_MOVES, DESCEND, ASCEND, FIRE, AUTO_EXPLORE, UNKNOWN):
        assert step(dead, command) is dead
    assert advance(dead) is dead


def test_a_cave_snake_can_poison_the_player() -> None:
    state = with_player(
        with_npcs(start(fight_level()), make_npc((5, 3), Species.CAVE_SNAKE)),
        stats=UNKILLABLE,
    )
    for _ in range(40):
        state = tick(state)
        if state.player_actor.actor.status_effects:
            break
    effects = state.player_actor.actor.status_effects
    assert [e.kind for e in effects] == [StatusKind.POISONED]
    assert effects[0].remaining_turns <= 5 and effects[0].magnitude == 2
    assert EventKind.POISONED in {e.kind for e in state.events}


# ---------------------------------------------------------------------------------------
# Ranged targeting (CONTRACT-v5 §7.10)
# ---------------------------------------------------------------------------------------


def test_fire_with_nothing_in_range_says_so_and_costs_no_turn() -> None:
    state = start(fight_level())
    after = step(state, FIRE)
    assert [e.kind for e in after.events] == [EventKind.NO_TARGET]
    assert after.turns == state.turns
    assert after.targeting is None


def test_fire_with_a_target_starts_choosing_and_costs_no_turn() -> None:
    state = with_npcs(start(fight_level()), make_npc((7, 3), Species.JACKAL))
    after = step(state, FIRE)
    assert after.turns == state.turns
    assert after.targeting == Targeting(((7, 3),), 0)
    assert [e.kind for e in after.events] == [EventKind.TARGETING]
    assert after.events[0].name == "jackal"
    assert "jackal" in events.message_for(after.events)


def test_the_target_list_is_visible_monsters_in_range_sorted_by_distance() -> None:
    state = with_npcs(
        start(fight_level()),
        make_npc((7, 3), Species.RAT, 1),
        make_npc((5, 3), Species.JACKAL, 2),
        make_npc((5, 1), Species.GIANT_BAT, 3),
    )
    after = step(state, FIRE)
    assert after.targeting is not None
    assert after.targeting.targets == ((5, 3), (5, 1), (7, 3))


def test_a_monster_in_range_but_not_visible_is_excluded() -> None:
    state = with_npcs(
        start(fight_level()),
        make_npc((7, 3), Species.RAT, 1),
        make_npc((5, 3), Species.JACKAL, 2),
    )
    state = dataclasses.replace(state, visible=state.visible - {(5, 3)})
    after = step(state, FIRE)
    assert after.targeting is not None
    assert after.targeting.targets == ((7, 3),)


def test_a_monster_beyond_the_bows_range_is_excluded() -> None:
    # The shortbow reaches 6 cells (CONTRACT-v5 §21); a 20x7 hall has room to prove it.
    state = start(stairs_level(player_start=(3, 3)))
    state = with_npcs(
        state,
        make_npc((3 + SHORTBOW.range, 3), Species.RAT, 1),
        make_npc((3 + SHORTBOW.range + 1, 3), Species.JACKAL, 2),
    )
    after = step(state, FIRE)
    assert after.targeting is not None
    assert after.targeting.targets == ((3 + SHORTBOW.range, 3),)


def test_tab_cycles_the_targets_and_wraps_without_costing_a_turn() -> None:
    state = with_npcs(
        start(fight_level()),
        make_npc((5, 3), Species.RAT, 1),
        make_npc((7, 3), Species.JACKAL, 2),
    )
    chosen = step(state, FIRE)
    assert chosen.targeting.index == 0
    second = step(chosen, TARGET_NEXT)
    assert second.targeting.index == 1
    assert second.turns == state.turns
    assert second.events[0].name == "jackal"
    wrapped = step(second, TARGET_NEXT)
    assert wrapped.targeting.index == 0
    assert wrapped.events[0].name == "rat"
    assert wrapped.turns == state.turns


def test_target_next_with_nothing_being_targeted_does_nothing() -> None:
    state = with_npcs(start(fight_level()), make_npc((7, 3)))
    assert step(state, TARGET_NEXT) is state


def test_firing_resolves_a_ranged_attack_and_consumes_a_turn() -> None:
    state = with_player(start(fight_level()), stats=Stats(str_=16, agi=10, vit=10))
    state = with_npcs(state, make_npc((7, 3), Species.CAVE_SNAKE))
    state = dataclasses.replace(
        state, master_seed=seed_where_player(state, state.npcs[0], SHORTBOW, True)
    )
    chosen = step(state, FIRE)

    # CONTRACT-v5 §23.2: a bow's power is the bow's. `resolve_attack` cannot tell a bow
    # from a dagger, so passing strength_applies=False is this call site's responsibility,
    # and STR 16 is what makes the mistake visible.
    without_strength = player_attack_result(chosen, chosen.npcs[0], SHORTBOW, False)
    with_strength = player_attack_result(chosen, chosen.npcs[0], SHORTBOW, True)
    assert with_strength.damage == without_strength.damage + 3

    fired = step(chosen, FIRE)
    assert fired.turns == chosen.turns + 1
    assert fired.targeting is None
    assert fired.player == (4, 3), "shooting does not move you"
    assert fired.npcs[0].actor.hp == without_strength.defender_hp
    assert [e.kind for e in fired.events] == [EventKind.PLAYER_HIT_NPC]


def test_firing_at_the_selected_target_not_the_nearest_one() -> None:
    state = with_npcs(
        start(fight_level()),
        make_npc((5, 3), Species.RAT, 1, hp=1),
        make_npc((7, 3), Species.JACKAL, 2, hp=1),
    )
    state = dataclasses.replace(
        state, master_seed=seed_where_player(state, state.npcs[1], SHORTBOW, True)
    )
    fired = step(step(step(state, FIRE), TARGET_NEXT), FIRE)
    assert [n.actor_id for n in fired.npcs] == [1], "the jackal was the one shot"
    killed = [e for e in fired.events if e.kind is EventKind.NPC_KILLED]
    assert [e.name for e in killed] == ["jackal"]


@pytest.mark.parametrize(
    "command",
    [MOVE_N, QUIT, DESCEND, ASCEND, UNKNOWN, AUTO_EXPLORE, WALK_PREFIX],
    ids=["move", "quit", "descend", "ascend", "unknown", "explore", "walk"],
)
def test_any_other_key_cancels_targeting_and_is_consumed_whole(command: Command) -> None:
    state = with_npcs(start(fight_level()), make_npc((7, 3), Species.JACKAL))
    chosen = step(state, FIRE)
    after = step(chosen, command)
    assert after.targeting is None
    assert after.turns == chosen.turns, "no turn"
    assert after.player == chosen.player, "no action"
    assert after.events == chosen.events, "not even a new message"
    assert after.running is True
    assert after.activity is None
    assert after.awaiting_walk is False


def test_targeting_does_not_survive_a_level_change() -> None:
    state = walk_to(start(stairs_level()), DOWN_CELL)
    state = with_npcs(state, make_npc((17, 3), Species.JACKAL))
    chosen = step(state, FIRE)
    assert chosen.targeting is not None
    # A command while targeting is swallowed by the cancel, so the descent takes two
    # presses — and the second one lands on a state with no targeting at all.
    below = step(step(chosen, DESCEND), DESCEND)
    assert below.depth == 2
    assert below.targeting is None


def test_a_target_that_has_died_cancels_the_shot_with_no_turn() -> None:
    state = with_npcs(
        start(fight_level()),
        make_npc((5, 3), Species.RAT, 1),
        make_npc((7, 3), Species.JACKAL, 2),
    )
    chosen = step(state, FIRE)
    assert chosen.targeting.targets[chosen.targeting.index] == (5, 3)

    # The rat is gone by the time the arrow is loosed: the list is rebuilt, the shot is
    # cancelled, and no turn is spent (CONTRACT-v5 §11 v5).
    vanished = dataclasses.replace(chosen, npcs=(chosen.npcs[1],))
    after = step(vanished, FIRE)
    assert after.turns == vanished.turns
    assert after.targeting == Targeting(((7, 3),), 0)
    assert [e.kind for e in after.events] == [EventKind.TARGETING]


def test_a_last_target_that_has_died_leaves_nothing_to_shoot_at() -> None:
    state = with_npcs(start(fight_level()), make_npc((5, 3), Species.RAT))
    chosen = step(state, FIRE)
    vanished = dataclasses.replace(chosen, npcs=())
    after = step(vanished, FIRE)
    assert after.targeting is None
    assert [e.kind for e in after.events] == [EventKind.NO_TARGET]
    assert after.turns == vanished.turns


def test_firing_ticks_the_world() -> None:
    state = with_npcs(
        start(fight_level()), make_npc((7, 3), Species.JACKAL, energy=99)
    )
    fired = step(step(state, FIRE), FIRE)
    assert fired.turns == state.turns + 1
    assert fired.npcs and fired.npcs[0].energy != 99


# ---------------------------------------------------------------------------------------
# Levelling (CONTRACT-v5 §7.11)
# ---------------------------------------------------------------------------------------


def test_xp_to_next_is_twenty_five_l_squared() -> None:
    assert xp_to_next(1) == 25
    assert xp_to_next(2) == 100
    assert xp_to_next(3) == 225
    assert xp_to_next(4) == 400


def test_a_kill_credits_exactly_the_species_xp_value() -> None:
    for species in Species:
        state = with_npcs(start(fight_level()), make_npc((5, 3), species, hp=1))
        state = dataclasses.replace(
            state, master_seed=seed_where_player(state, state.npcs[0], DAGGER, True)
        )
        after = step(state, MOVE_E)
        assert after.npcs == ()
        assert after.player_actor.xp == SPECIES_DATA[species].xp_value


def test_reaching_the_first_threshold_levels_the_character_up() -> None:
    state = with_player(start(fight_level()), xp=24, level=1, hp=30)
    state = with_npcs(state, make_npc((5, 3), Species.RAT, hp=1))  # 5 xp
    state = dataclasses.replace(
        state, master_seed=seed_where_player(state, state.npcs[0], DAGGER, True)
    )
    after = step(state, MOVE_E)

    player = after.player_actor
    assert player.level == 2
    assert player.xp == 4, "29 earned, 25 spent, the remainder carried"
    assert player.actor.stats.vit == 11, "VIT every level"
    assert player.actor.hp == 30 + (derive(player.actor.stats).max_hp - 45)
    assert player.actor.hp == 34, "raised by the max-HP delta, not healed to full"
    assert player.actor.hp < derive(player.actor.stats).max_hp
    assert EventKind.LEVELLED_UP in {e.kind for e in after.events}
    assert [e.level for e in after.events if e.kind is EventKind.LEVELLED_UP] == [2]


def test_the_stat_gained_alternates_by_the_parity_of_the_level_reached() -> None:
    """CONTRACT-v5 §7.11's loop: ``level += 1`` **then** ``if level is odd: str_ += 1 else:
    agi += 1`` — the parity of the level just *reached*.

    So the level-2 character gains AGI and the level-3 character gains STR. This is also
    what the RESEARCH-v5 simulation that produced every published balance number computed,
    so the rule, the pseudocode and the measured curve all agree.
    """
    state = with_player(start(fight_level()), xp=25, level=1)
    second = level_up(state)
    assert second.player_actor.level == 2
    assert second.player_actor.actor.stats == Stats(str_=10, agi=11, vit=11)

    third = level_up(with_player(second, stats=second.player_actor.actor.stats, xp=100, level=2))
    assert third.player_actor.level == 3
    assert third.player_actor.actor.stats == Stats(str_=11, agi=11, vit=12)


def test_one_kill_crossing_two_thresholds_levels_twice() -> None:
    # 25 to reach level 2 and 100 to reach level 3: 130 XP in one go must land on level 3
    # with 5 carried over, not on level 2 with 105 banked.
    state = with_player(start(fight_level()), xp=130, level=1, hp=45)
    after = level_up(state)
    assert after.player_actor.level == 3
    assert after.player_actor.xp == 5
    assert [e.level for e in after.events if e.kind is EventKind.LEVELLED_UP] == [2, 3]
    assert after.player_actor.actor.stats == Stats(str_=11, agi=11, vit=12)
    assert after.player_actor.actor.hp == 45 + (
        derive(after.player_actor.actor.stats).max_hp - 45
    )


def test_level_up_with_nothing_to_spend_returns_the_same_state() -> None:
    state = with_player(start(fight_level()), xp=24, level=1)
    assert level_up(state) is state


def test_max_hp_after_levelling_comes_from_stats_derive() -> None:
    state = level_up(with_player(start(fight_level()), xp=25, level=1))
    stats = state.player_actor.actor.stats
    assert f"/{derive(stats).max_hp}" in format_stats(state)
    assert derive(stats).max_hp == 5 + stats.vit * 4


def test_levelling_never_heals_to_full() -> None:
    state = with_player(start(fight_level()), xp=25, level=1, hp=3)
    after = level_up(state)
    assert after.player_actor.actor.hp == 3 + (
        derive(after.player_actor.actor.stats).max_hp - 45
    )
    assert after.player_actor.actor.hp < derive(after.player_actor.actor.stats).max_hp


# ---------------------------------------------------------------------------------------
# interruption — the v4 seam, now live (CONTRACT-v5 §7.14)
# ---------------------------------------------------------------------------------------


def test_a_hostile_coming_into_view_cancels_the_activity() -> None:
    # A short corridor with a monster round the corner: the first step of the walk brings
    # its cell into view for the first time.
    rows = [
        "#######",
        "#.....#",
        "#######",
    ]
    state = corridor_state(rows, (1, 1))
    hidden = dataclasses.replace(state, visible=frozenset({(1, 1), (2, 1)}))
    hidden = with_npcs(hidden, make_npc((5, 1), Species.JACKAL, ai_state=AiState.WANDERING))
    walking = walk(hidden, 1, 0)

    after = advance(walking)
    assert after.activity is None, "the walk stopped"
    spotted = [e for e in after.events if e.kind is EventKind.SPOTTED_HOSTILE]
    assert [e.name for e in spotted] == ["jackal"]
    assert "A jackal comes into view!" in events.message_for(after.events)


def test_the_nearest_newcomer_is_the_one_named() -> None:
    before = start(fight_level())
    before = dataclasses.replace(before, visible=frozenset({(4, 3)}))
    after = with_npcs(
        start(fight_level()),
        make_npc((7, 5), Species.CAVE_SNAKE, 1),
        make_npc((5, 3), Species.JACKAL, 2),
    )
    event = interruption(before, after)
    assert event is not None
    assert event.kind is EventKind.SPOTTED_HOSTILE
    assert event.name == "jackal"


def test_an_already_visible_monster_merely_moving_does_not_interrupt() -> None:
    before = with_npcs(start(fight_level()), make_npc((6, 3), Species.RAT))
    after = with_npcs(start(fight_level()), make_npc((5, 3), Species.RAT))
    assert (6, 3) in before.visible and (5, 3) in before.visible
    assert interruption(before, after) is None


def test_taking_damage_interrupts() -> None:
    before = start(fight_level())
    after = with_player(start(fight_level()), hp=40)
    event = interruption(before, after)
    assert event is not None and event.kind is EventKind.INTERRUPTED


def test_being_newly_poisoned_interrupts() -> None:
    before = start(fight_level())
    after = with_player(
        start(fight_level()), effects=(StatusEffect(StatusKind.POISONED, 5, 2),)
    )
    event = interruption(before, after)
    assert event is not None and event.kind is EventKind.INTERRUPTED


def test_an_unchanged_poison_does_not_interrupt_again() -> None:
    effects = (StatusEffect(StatusKind.POISONED, 5, 2),)
    before = with_player(start(fight_level()), effects=effects)
    after = with_player(
        start(fight_level()), effects=(StatusEffect(StatusKind.POISONED, 4, 2),)
    )
    assert interruption(before, after) is None


def test_opening_a_door_still_does_not_interrupt() -> None:
    state = walk(corridor_state(DOOR_CORRIDOR_ROWS, (3, 1)), 1, 0)
    after = advance(state)
    assert after.open_doors == frozenset({CORRIDOR_DOOR})
    assert interruption(state, after) is None
    assert after.activity is not None


def test_the_interruption_event_is_appended_not_substituted() -> None:
    """CONTRACT-v5 §7.14's amendment to §7.5, which is the whole point of the change.

    Substituting would leave the player reading a bare ``You stop.`` with no idea what
    stopped them — the jackal's bite, the only informative half, thrown away.
    """
    rows = [
        "#######",
        "#.....#",
        "#######",
    ]
    state = corridor_state(rows, (2, 1))
    state = with_npcs(state, make_npc((3, 1), Species.JACKAL, energy=99))
    state = with_player(state, hp=40)
    walking = walk(state, -1, 0)

    for seed in range(200):
        attempt = advance(dataclasses.replace(walking, master_seed=seed))
        kinds = [e.kind for e in attempt.events]
        if EventKind.NPC_HIT_PLAYER in kinds:
            break
    else:  # pragma: no cover - 200 jackal bites at 85% cannot all miss
        raise AssertionError("no seed produced a bite")

    assert attempt.activity is None, "the walk was cancelled"
    line = events.message_for(attempt.events)
    assert "The jackal hits you." in line
    assert "You stop." in line
    assert kinds == [EventKind.NPC_HIT_PLAYER, EventKind.INTERRUPTED]


def test_a_monster_standing_in_the_way_stops_an_activity_without_a_fight() -> None:
    state = corridor_state(CORRIDOR_ROWS, (1, 1))
    state = with_npcs(state, make_npc((2, 1), Species.JACKAL, ai_state=AiState.WANDERING))
    walking = walk(state, 1, 0)
    after = advance(walking)
    assert after.activity is None
    assert after.turns == walking.turns, "no auto-fight, and no turn"
    assert after.npcs[0].actor.hp == state.npcs[0].actor.hp


def test_an_activity_turn_ticks_the_world_exactly_once() -> None:
    state = with_player(
        with_npcs(
            corridor_state(CORRIDOR_ROWS, (1, 1)),
            make_npc((5, 1), Species.CAVE_SNAKE, energy=0, ai_state=AiState.HUNTING),
        ),
        stats=UNKILLABLE,
    )
    walking = walk(state, 1, 0)
    after = advance(walking)
    assert after.turns == walking.turns + 1
    assert actions_taken(walking.npcs[0], after.npcs[0], 80) in (0, 1)
    assert after.npcs[0].energy == 80


# ---------------------------------------------------------------------------------------
# The message line (CONTRACT-v5 §16.1)
# ---------------------------------------------------------------------------------------


def test_the_cap_keeps_three_events_in_emission_order() -> None:
    emitted = (
        Event(EventKind.PLAYER_MISSED_NPC, name="rat"),
        Event(EventKind.NPC_HIT_PLAYER, name="jackal"),
        Event(EventKind.DOOR_OPENED),
        Event(EventKind.NPC_KILLED, name="rat"),
        Event(EventKind.NPC_MISSED_PLAYER, name="bat"),
    )
    capped = game._capped(emitted)
    assert len(capped) == MAX_EVENTS
    assert [e.kind for e in capped] == [
        EventKind.PLAYER_MISSED_NPC,
        EventKind.NPC_HIT_PLAYER,
        EventKind.NPC_KILLED,
    ]


def test_the_cap_prefers_the_higher_priority_band() -> None:
    lower = [Event(EventKind.DOOR_OPENED)] * 3
    assert [e.kind for e in game._capped(tuple(lower + [Event(EventKind.PLAYER_DIED)]))] == [
        EventKind.DOOR_OPENED,
        EventKind.DOOR_OPENED,
        EventKind.PLAYER_DIED,
    ]
    band_two = Event(EventKind.NPC_HIT_PLAYER, name="rat")
    band_three = Event(EventKind.PLAYER_HIT_NPC, name="rat")
    capped = game._capped((band_three, band_three, band_three, band_two))
    assert capped.count(band_two) == 1


def test_within_a_band_the_earlier_event_wins() -> None:
    first = Event(EventKind.NPC_HIT_PLAYER, name="rat")
    second = Event(EventKind.NPC_HIT_PLAYER, name="jackal")
    third = Event(EventKind.NPC_HIT_PLAYER, name="bat")
    fourth = Event(EventKind.NPC_HIT_PLAYER, name="snake")
    capped = game._capped((first, second, third, fourth))
    assert capped == (first, second, third)


def test_under_the_cap_nothing_is_reordered_or_dropped() -> None:
    pair = (Event(EventKind.PLAYER_HIT_NPC, name="rat"), Event(EventKind.DOOR_OPENED))
    assert game._capped(pair) == pair


def test_six_monsters_acting_never_exceed_the_cap() -> None:
    state = with_player(
        with_npcs(
            start(fight_level()),
            make_npc((3, 2), Species.RAT, 1),
            make_npc((4, 2), Species.RAT, 2),
            make_npc((5, 2), Species.JACKAL, 3),
            make_npc((3, 4), Species.JACKAL, 4),
            make_npc((4, 4), Species.GIANT_BAT, 5),
            make_npc((5, 4), Species.GIANT_BAT, 6),
        ),
        stats=UNKILLABLE,
    )
    for _ in range(12):
        state = tick(state)
        assert len(state.events) <= MAX_EVENTS
        assert len(events.message_for(state.events)) < 80


def test_an_unseen_monsters_bite_is_still_reported() -> None:
    state = with_player(
        with_npcs(start(fight_level()), make_npc((5, 3), Species.JACKAL)), hp=40
    )
    state = dataclasses.replace(state, visible=state.visible - {(5, 3)})
    for _ in range(10):
        state = tick(state)
        kinds = {e.kind for e in state.events}
        if EventKind.NPC_HIT_PLAYER in kinds:
            break
        assert EventKind.NPC_MISSED_PLAYER not in kinds, "a miss you cannot see is silent"
        state = dataclasses.replace(state, visible=state.visible - {(5, 3)})
    assert EventKind.NPC_HIT_PLAYER in {e.kind for e in state.events}


def test_hitting_a_monster_you_cannot_see_is_not_reported() -> None:
    state = with_npcs(start(fight_level()), make_npc((5, 3), Species.JACKAL))
    state = dataclasses.replace(state, visible=state.visible - {(5, 3)})
    after = step(state, MOVE_E)
    assert after.turns == state.turns + 1, "the swing happened"
    assert after.npcs[0].actor.hp <= state.npcs[0].actor.hp
    kinds = {e.kind for e in after.events}
    assert EventKind.PLAYER_HIT_NPC not in kinds
    assert EventKind.PLAYER_MISSED_NPC not in kinds


def test_being_poisoned_is_always_reported() -> None:
    state = with_player(
        with_npcs(start(fight_level()), make_npc((5, 3), Species.CAVE_SNAKE)),
        stats=UNKILLABLE,
    )
    state = dataclasses.replace(state, visible=state.visible - {(5, 3)})
    for _ in range(40):
        state = dataclasses.replace(state, visible=state.visible - {(5, 3)})
        state = tick(state)
        if state.player_actor.actor.status_effects:
            break
    assert EventKind.POISONED in {e.kind for e in state.events}


# ---------------------------------------------------------------------------------------
# Randomness: derived, never stored (CONTRACT-v5 §0.12)
# ---------------------------------------------------------------------------------------


def test_roll_seed_matches_the_contract_formula() -> None:
    for master, turns, actor, salt in (
        (0, 0, 0, 0),
        (1234, 7, 3, 1),
        (99, 1000, 6, 4),
        (-5, 2, 1, 2),
    ):
        assert roll_seed(master, turns, actor, salt) == (
            master * 0x9E3779B1
            + turns * 0x85EBCA77
            + actor * 0xC2B2AE35
            + salt * 0x27D4EB2F
        ) & 0x7FFFFFFF
        assert 0 <= roll_seed(master, turns, actor, salt) <= 0x7FFFFFFF


def test_roll_seed_separates_actors_salts_and_turns() -> None:
    base = roll_seed(1234, 10, 1, 1)
    assert base != roll_seed(1234, 10, 2, 1), "different actor"
    assert base != roll_seed(1234, 10, 1, 2), "different salt"
    assert base != roll_seed(1234, 11, 1, 1), "different turn"
    assert base != roll_seed(1235, 10, 1, 1), "different run"


def _contains_random(value: object, seen: set[int]) -> bool:
    if id(value) in seen:
        return False
    seen.add(id(value))
    if isinstance(value, random.Random):
        return True
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return any(
            _contains_random(getattr(value, f.name), seen)
            for f in dataclasses.fields(value)
        )
    if isinstance(value, (tuple, list, frozenset, set)):
        return any(_contains_random(item, seen) for item in value)
    if isinstance(value, dict):
        return any(_contains_random(item, seen) for item in value.values())
    return False


def test_no_generator_is_stored_anywhere_on_a_state(monsters) -> None:
    # CONTRACT-v5 §0.12: a stored Random is mutable, and two states built from one parent
    # by replace() would share and corrupt a single stream.
    for cls in (GameState, LevelState, Player, Targeting, NPC):
        for field in dataclasses.fields(cls):
            assert "Random" not in str(field.type)
    state = new_game(1234, *SMALL)
    state = step(state, MOVE_E)
    state = step(state, MOVE_S)
    assert not _contains_random(state, set())


def test_no_module_level_random_draw_exists() -> None:
    # The only permitted use of `random` is constructing a generator from a derived seed.
    calls = [
        node
        for node in ast.walk(GAME_TREE)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "random"
    ]
    assert calls, "the module does build generators"
    for call in calls:
        assert call.func.attr == "Random"
        assert _enclosing_function(GAME_TREE, call) is not None
        inner = call.args[0]
        if isinstance(inner, ast.Call):
            # every roll: a fresh generator from a seed derived off the state
            assert isinstance(inner.func, ast.Name) and inner.func.id == "roll_seed"
        else:
            # spawning: the level's own seed, so a population is as reproducible as the
            # rooms it lives in (CONTRACT-v5 §24.4)
            assert isinstance(inner, ast.Attribute) and inner.attr == "seed"


def test_the_same_seed_and_keys_produce_the_same_fight(monsters) -> None:
    keys = "jjjklllhhhkkjjjfffEllljjjkkkhhhfffjjjlllhhhkkkjjjlllwwjjkkllhh"
    first = new_game(4242, *SMALL)
    second = new_game(4242, *SMALL)
    assert len(keys) >= 50
    for key in keys:
        first = step(first, translate_key(key))
        second = step(second, translate_key(key))
    assert first == second
    assert first.npcs == second.npcs
    assert first.player_actor == second.player_actor


def test_a_scripted_fight_is_reproducible_across_hash_seeds() -> None:
    script = """
from roguelike.game import new_game, step, advance
from roguelike.keys import translate_key

state = new_game(20260810, 40, 18)
for key in "EjjjlllkkkhhhfffjjjlllkkkhhhfffjjjlllkkkhhhEfffjjjlllkkkhhh":
    state = step(state, translate_key(key))
    for _ in range(6):
        state = advance(state)
    if not state.running:
        break
print(
    state.turns,
    state.player,
    state.player_actor.actor.hp,
    state.player_actor.xp,
    state.player_actor.level,
    [(n.actor_id, n.position, n.energy, n.actor.hp) for n in state.npcs],
    sorted(state.open_doors),
    len(state.explored),
)
"""
    root = str(Path(game.__file__).resolve().parent.parent)
    outputs = []
    for hash_seed in ("0", "1234"):
        result = subprocess.run(
            [sys.executable, "-c", script],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            cwd=root,
            env={"PYTHONHASHSEED": hash_seed, "PATH": "/usr/bin:/bin"},
            check=True,
        )
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1]
    assert outputs[0].strip(), "the replay produced a state to compare"


# ---------------------------------------------------------------------------------------
# Purity, again, for every v5 path
# ---------------------------------------------------------------------------------------


def v5_states() -> list[GameState]:
    fight = with_npcs(start(fight_level()), make_npc((5, 3), Species.JACKAL))
    return [
        fight,
        with_player(fight, hp=20, effects=(StatusEffect(StatusKind.POISONED, 3, 2),)),
        step(fight, FIRE),
        with_npcs(start(fight_level()), make_npc((7, 5), Species.RAT)),
    ]


@pytest.mark.parametrize("index", range(4))
def test_step_never_mutates_a_v5_state(index: int) -> None:
    state = v5_states()[index]
    before = snapshot(state)
    copied = copy.deepcopy(state.npcs)
    for command in (MOVE_E, FIRE, TARGET_NEXT, MOVE_N, UNKNOWN):
        step(state, command)
    advance_npcs(state)
    interruption(state, state)
    for name, value in before.items():
        assert getattr(state, name) == value
    assert state.npcs == copied


def test_advance_npcs_rebuilds_saved_rather_than_mutating_it() -> None:
    state = walk_to(start(stairs_level()), DOWN_CELL)
    state = with_npcs(state, make_npc((4, 1), Species.RAT))
    below = step(state, DESCEND)
    assert state.saved == {}
    assert below.saved is not state.saved
    assert 1 in below.saved


def test_a_fight_leaves_the_input_monsters_untouched() -> None:
    state = with_npcs(start(fight_level()), make_npc((5, 3), Species.JACKAL, hp=25))
    original = state.npcs[0]
    step(state, MOVE_E)
    tick(state)
    assert original.actor.hp == 25
    assert original.position == (5, 3)
    assert original.energy == 0


# ---------------------------------------------------------------------------------------
# End to end, with the real population (CONTRACT-v5 §24.4)
# ---------------------------------------------------------------------------------------


def test_a_real_game_spawns_monsters_at_a_safe_distance(monsters) -> None:
    for seed in (1234, 7, 42):
        state = new_game(seed, *SMALL)
        assert state.npcs, "a generated level is populated"
        for npc in state.npcs:
            assert max(
                abs(npc.position[0] - state.player[0]),
                abs(npc.position[1] - state.player[1]),
            ) >= 8


def test_auto_explore_with_monsters_stops_instead_of_walking_into_them(monsters) -> None:
    state = step(new_game(1234, *SMALL), AUTO_EXPLORE)
    for _ in range(400):
        state = advance(state)
        if state.activity is None or not state.running:
            break
    assert state.activity is None
    assert EventKind.SPOTTED_HOSTILE in {e.kind for e in state.events} or not state.running


def test_the_loop_draws_monsters_and_the_target_cursor(monsters) -> None:
    # `run` holds no rules, but it is the only place the renderer's v5 signature is used.
    state = with_npcs(
        start(fight_level()), make_npc((5, 3), Species.JACKAL), make_npc((6, 1), Species.RAT, 2)
    )
    # `f` opens targeting; the next key is swallowed by the cancel, so quitting from a
    # targeting cursor genuinely takes two presses.
    screen = StubScreen([ord("f"), ord("q"), ord("q")])
    run(screen, state)
    frame = screen.frames[-1]
    glyphs = {frame.get((y + 1, x)) for (x, y) in ((5, 3), (6, 1))}
    assert glyphs == {"j", "r"}


def test_playing_a_real_game_can_end_in_death(monsters) -> None:
    # Not a balance assertion: the point is that the death path is reachable through the
    # ordinary loop, with no special casing, and that it ends the run cleanly.
    state = with_player(
        with_npcs(start(fight_level()), make_npc((5, 3), Species.JACKAL)), hp=1
    )
    for index in range(30):
        state = step(state, MOVE_W if index % 2 == 0 else MOVE_E)
        if not state.running:
            break
    assert state.running is False
    assert state.outcome and "You die..." in state.outcome


# --- The help screen --------------------------------------------------------
#
# `?` opens a paginated list of the key bindings. It is a sub-mode in the same
# shape as the `w` prefix and ranged targeting: it swallows the next key whole,
# costs no turn, and cannot leave the player stuck.


HELP = Command(CommandKind.HELP)


def test_question_mark_opens_the_help_at_page_zero() -> None:
    state = new_game(1234)
    after = step(state, HELP)
    assert after.help_page == 0


def test_opening_the_help_costs_no_turn_and_does_not_tick_the_world() -> None:
    state = new_game(1234)
    before = tuple((npc.position, npc.energy, npc.actor.hp) for npc in state.npcs)
    after = step(state, HELP)
    assert after.turns == state.turns
    assert after.player == state.player
    assert tuple((n.position, n.energy, n.actor.hp) for n in after.npcs) == before
    assert after.player_actor.actor.hp == state.player_actor.actor.hp


def test_any_key_turns_the_page_and_the_last_one_closes_the_help() -> None:
    # A short level so the entries genuinely span more than one page.
    state = new_game(1234, width=80, height=8)
    opened = step(state, HELP)
    total = help_page_count(opened)
    assert total > 1, "this test needs a level short enough to paginate"

    pages = []
    current = opened
    while current.help_page is not None:
        pages.append(current.help_page)
        current = step(current, MOVE_E)  # any key at all
    assert pages == list(range(total))
    assert current.help_page is None


def test_a_single_page_help_closes_on_the_very_next_key() -> None:
    state = new_game(1234)  # 22 body rows: every entry fits on one page
    opened = step(state, HELP)
    assert help_page_count(opened) == 1
    assert step(opened, MOVE_E).help_page is None


def test_keys_pressed_inside_the_help_do_not_reach_the_game() -> None:
    state = new_game(1234)
    opened = step(state, HELP)
    closed = step(opened, MOVE_E)
    assert closed.player == state.player, "a movement key must not also move the player"
    assert closed.turns == state.turns


def test_quitting_inside_the_help_is_swallowed_like_any_other_key() -> None:
    # Consistent with the `w` prefix, where a stray QUIT is consumed by the prefix.
    # Reading the help must never drop you out of the game by accident.
    state = new_game(1234)
    opened = step(state, HELP)
    closed = step(opened, QUIT_COMMAND)
    assert closed.running is True
    assert closed.help_page is None


def test_the_help_leaves_the_message_line_alone() -> None:
    state = new_game(1234)
    state = step(state, Command(CommandKind.DESCEND))  # emits an event
    message = state.events
    opened = step(state, HELP)
    assert opened.events == message
    assert step(opened, MOVE_E).events == message


def test_every_binding_line_appears_exactly_once_across_the_pages() -> None:
    state = new_game(1234, width=80, height=8)
    opened = step(state, HELP)
    collected: list[str] = []
    current = opened
    while current.help_page is not None:
        collected.extend(help_page_lines(current))
        current = step(current, MOVE_E)
    assert collected == list(help_lines(opened))


def test_help_lines_describe_every_binding_in_the_key_table() -> None:
    # The help is built from keys.HELP_ENTRIES, so a binding cannot be documented
    # in one place and bound in another. Spot-check the ones a player needs most.
    text = "\n".join(help_lines(new_game(1234)))
    for fragment in ("h j k l", "E", ">", "<", "f", "Tab", "?", "q"):
        assert fragment in text


def test_help_page_lines_is_empty_when_the_help_is_closed() -> None:
    assert help_page_lines(new_game(1234)) == ()


def test_the_footer_names_the_page_and_the_total() -> None:
    state = new_game(1234, width=80, height=8)
    opened = step(state, HELP)
    assert format_help_status(opened).startswith("Page 1/")
    assert str(help_page_count(opened)) in format_help_status(opened)


def test_a_dead_game_ignores_the_help_key() -> None:
    state = dataclasses.replace(new_game(1234), running=False)
    assert step(state, HELP) is state


# --- Explicit attack: F, then a direction ------------------------------------
#
# Walking into a monster already attacks it, but that is no use when you want to
# hit something you might otherwise walk past. `F` swings at an adjacent square
# without ever becoming a step.


ATTACK = Command(CommandKind.ATTACK)


def _with_adjacent_rat(state: GameState, dx: int = 1, dy: int = 0):
    """Put a full-health rat immediately beside the player."""
    data = SPECIES_DATA[Species.RAT]
    rat = NPC(
        actor_id=99,
        species=Species.RAT,
        actor=Actor(data.stats, derive(data.stats).max_hp),
        position=(state.player[0] + dx, state.player[1] + dy),
    )
    return dataclasses.replace(state, npcs=(rat,)), rat


def test_the_attack_key_asks_for_a_direction_and_costs_no_turn() -> None:
    state = new_game(1234)
    after = step(state, ATTACK)
    assert after.awaiting_attack is True
    assert after.turns == state.turns
    assert EventKind.ATTACK_WHICH_WAY in {e.kind for e in after.events}


def test_an_explicit_attack_hits_the_monster_without_moving_the_player() -> None:
    state, rat = _with_adjacent_rat(new_game(1234))
    after = step(step(state, ATTACK), Command(CommandKind.MOVE, 1, 0))
    assert after.player == state.player, "an explicit attack must never be a step"
    assert after.turns == state.turns + 1
    kinds = {e.kind for e in after.events}
    assert kinds & {EventKind.PLAYER_HIT_NPC, EventKind.PLAYER_MISSED_NPC, EventKind.NPC_KILLED}


def test_an_explicit_attack_works_diagonally() -> None:
    state, _ = _with_adjacent_rat(new_game(1234), dx=1, dy=1)
    after = step(step(state, ATTACK), Command(CommandKind.MOVE, 1, 1))
    assert after.player == state.player
    kinds = {e.kind for e in after.events}
    assert kinds & {EventKind.PLAYER_HIT_NPC, EventKind.PLAYER_MISSED_NPC, EventKind.NPC_KILLED}


def test_swinging_at_an_empty_square_costs_a_turn() -> None:
    # A free swing would be a free probe: it would tell the player whether a cell is
    # occupied at no cost, which is exactly what a monster's turn is meant to buy.
    state = dataclasses.replace(new_game(1234), npcs=())
    after = step(step(state, ATTACK), Command(CommandKind.MOVE, 1, 0))
    assert after.turns == state.turns + 1
    assert after.player == state.player
    assert EventKind.ATTACKED_NOTHING in {e.kind for e in after.events}


def test_attacking_a_wall_is_still_just_a_swing_and_never_a_move() -> None:
    state = dataclasses.replace(new_game(1234), npcs=())
    # Whatever lies that way, the player does not move and a turn passes.
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        after = step(step(state, ATTACK), Command(CommandKind.MOVE, dx, dy))
        assert after.player == state.player
        assert after.turns == state.turns + 1


def test_a_non_direction_after_the_attack_key_is_swallowed_whole() -> None:
    # Same rule as the walk prefix: a typo costs nothing and does nothing.
    state = new_game(1234)
    after = step(step(state, ATTACK), QUIT_COMMAND)
    assert after.running is True
    assert after.awaiting_attack is False
    assert after.turns == state.turns


def test_the_attack_prefix_does_not_survive_the_command_that_clears_it() -> None:
    state = new_game(1234)
    assert step(step(state, ATTACK), Command(CommandKind.MOVE, 1, 0)).awaiting_attack is False


# --- Projectile flight path --------------------------------------------------
#
# `step` records the cells an arrow flew through; `run` draws them. The path is
# presentation only -- the shot is fully resolved before the first frame.


def _aimed_at_a_rat():
    """A state with a rat in view and in bow range, already targeted.

    Built by hand rather than found by seed search: monsters spawn at least
    ``SPAWN_SAFE_RADIUS`` (8) cells from the player start while the shortbow reaches
    only 6, so **no seed has a shootable target on turn 0** — you always have to close
    the distance first. That is deliberate, and it means these tests must place their
    own target.
    """
    state = new_game(1234)
    px, py = state.player
    # Walk outwards along the row until a cell three east is still on the map and open.
    spot = (px + 3, py)
    if not state.level.is_walkable(*spot) or spot not in state.visible:
        candidates = [
            cell
            for cell in state.visible
            if state.level.is_walkable(*cell)
            and cell != state.player
            and max(abs(cell[0] - px), abs(cell[1] - py)) <= 6
        ]
        assert candidates, "seed 1234 should light at least one nearby floor cell"
        spot = sorted(candidates)[0]
    data = SPECIES_DATA[Species.RAT]
    rat = NPC(
        actor_id=99,
        species=Species.RAT,
        actor=Actor(data.stats, derive(data.stats).max_hp),
        position=spot,
    )
    state = dataclasses.replace(state, npcs=(rat,))
    aimed = step(state, Command(CommandKind.FIRE))
    assert aimed.targeting is not None, "the rat should be targetable"
    return aimed


def test_firing_records_a_flight_path_from_the_player_to_the_target() -> None:
    aimed = _aimed_at_a_rat()
    target = aimed.targeting.targets[aimed.targeting.index]
    shot = step(aimed, Command(CommandKind.FIRE))
    assert shot.projectile[0] == aimed.player
    assert shot.projectile[-1] == target


def test_a_flight_path_is_contiguous() -> None:
    aimed = _aimed_at_a_rat()
    shot = step(aimed, Command(CommandKind.FIRE))
    for before, after in zip(shot.projectile, shot.projectile[1:]):
        assert max(abs(after[0] - before[0]), abs(after[1] - before[1])) == 1


def test_a_flight_path_does_not_survive_into_the_next_turn() -> None:
    # It is a one-turn artefact; a later frame must never redraw a stale arrow.
    aimed = _aimed_at_a_rat()
    shot = step(aimed, Command(CommandKind.FIRE))
    assert shot.projectile
    assert step(shot, Command(CommandKind.MOVE, 0, 1)).projectile == ()


def test_ordinary_turns_record_no_flight_path() -> None:
    state = new_game(1234)
    assert state.projectile == ()
    assert step(state, Command(CommandKind.MOVE, 0, 1)).projectile == ()


def test_a_melee_attack_records_no_flight_path() -> None:
    state, _ = _with_adjacent_rat(new_game(1234))
    after = step(step(state, ATTACK), Command(CommandKind.MOVE, 1, 0))
    assert after.projectile == ()


def test_no_seed_offers_a_shot_on_the_very_first_turn() -> None:
    """Monsters spawn at least 8 cells away; the shortbow reaches 6.

    Recorded as a test because it is a real consequence of two constants chosen
    independently (``npc.SPAWN_SAFE_RADIUS`` and ``items.SHORTBOW.range``), and if one
    of them ever moves, this is the cheapest place to find out.
    """
    for seed in (1, 7, 42, 1234):
        state = new_game(seed)
        assert step(state, Command(CommandKind.FIRE)).targeting is None
