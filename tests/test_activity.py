"""Unit tests for :mod:`roguelike.activity` (CONTRACT-v4 §19, task T20).

Most levels here are built by hand from character rows, so every expected frontier set
and every corridor decision can be read off the map in the test itself.

The exception is the end-to-end section at the bottom, which imports the real generator,
field of view and movement: the claim being verified there — "auto-explore reaches
essentially the whole level and stops because there is nothing left, not because it ran
out of turns" — is a claim about real levels, and a hand-built map could not support it.

Nothing here initialises curses, and nothing needs a TTY.
"""

from __future__ import annotations

import ast
import copy
import pathlib
import time

import pytest

from roguelike import activity, fov, movement, pathfind, world
from roguelike.activity import Activity, ActivityKind, frontier_cells, walk_step
from roguelike.generator import generate_level
from roguelike.level import Level, freeze_grid
from roguelike.pathfind import Passable, is_intersection, is_wide
from roguelike.tiles import Tile, is_walkable_tile
from roguelike.world import is_passable, is_planning_passable

CHAR_TO_TILE = {
    "#": Tile.WALL,
    ".": Tile.FLOOR,
    "+": Tile.DOOR,
    "<": Tile.STAIRS_UP,
    ">": Tile.STAIRS_DOWN,
}

NO_DOORS: frozenset[tuple[int, int]] = frozenset()


def make_level(
    rows: list[str], player_start: tuple[int, int] = (1, 1), seed: int = 0
) -> Level:
    """Build a ``Level`` from character rows. ``rows[y][x]``, so row 0 is the top."""
    grid = [[CHAR_TO_TILE[c] for c in row] for row in rows]
    return Level(len(grid[0]), len(grid), freeze_grid(grid), (), player_start, seed)


def all_cells(level: Level) -> frozenset[tuple[int, int]]:
    """Every in-bounds coordinate — "the character has seen the entire map"."""
    return frozenset(
        (x, y) for y in range(level.height) for x in range(level.width)
    )


def columns_upto(level: Level, x_max: int) -> frozenset[tuple[int, int]]:
    """Every cell in columns ``0..x_max`` — a tidy "explored so far" region whose
    boundary is a straight vertical line, so the expected frontier is obvious."""
    return frozenset(
        (x, y) for y in range(level.height) for x in range(x_max + 1)
    )


def char_passable(rows: list[str]) -> Passable:
    """A ``passable`` callable straight from a character map: ``#`` is solid, every
    other glyph is walkable, and everything off the map is solid."""

    def passable(x: int, y: int) -> bool:
        if 0 <= y < len(rows) and 0 <= x < len(rows[y]):
            return rows[y][x] != "#"
        return False

    return passable


def planning_passable_for(level: Level, open_doors: frozenset = NO_DOORS) -> Passable:
    """The predicate a planner uses: closed doors included, because bumping opens them."""
    return lambda x, y: is_planning_passable(level, open_doors, x, y)


# ==========================================================================
# The value type
# ==========================================================================


def test_activity_kind_has_exactly_the_four_contract_members():
    assert {k.name for k in ActivityKind} == {"TRAVEL", "AUTO_EXPLORE", "AUTO_WALK", "REST"}


def test_activity_kind_members_are_distinct_auto_values():
    values = [k.value for k in ActivityKind]
    assert len(set(values)) == 4
    assert sorted(values) == [1, 2, 3, 4]  # auto() from 1, in declaration order


def test_activity_defaults_leave_every_optional_field_empty():
    act = Activity(ActivityKind.AUTO_EXPLORE)
    assert act.kind is ActivityKind.AUTO_EXPLORE
    assert act.goal is None
    assert act.direction == (0, 0)
    assert act.came_from is None


def test_activity_carries_a_travel_goal_and_a_walk_direction():
    travel = Activity(ActivityKind.TRAVEL, goal=(9, 4))
    walk = Activity(ActivityKind.AUTO_WALK, direction=(1, 0), came_from=(3, 3))
    assert travel.goal == (9, 4)
    assert walk.direction == (1, 0)
    assert walk.came_from == (3, 3)


def test_activity_is_frozen():
    act = Activity(ActivityKind.AUTO_WALK, direction=(0, 1))
    with pytest.raises(Exception):  # FrozenInstanceError, a subclass of AttributeError
        act.direction = (0, -1)  # type: ignore[misc]


def test_activity_equality_is_by_value_and_it_is_hashable():
    a = Activity(ActivityKind.TRAVEL, goal=(2, 2))
    b = Activity(ActivityKind.TRAVEL, goal=(2, 2))
    assert a == b
    assert len({a, b}) == 1


def test_activity_has_no_path_field():
    # Routes are re-planned every turn (CONTRACT-v4 §18.1); a cached path on the
    # activity would be surface that can go stale against a door opening underneath it.
    assert not hasattr(Activity(ActivityKind.AUTO_EXPLORE), "path")


# ==========================================================================
# frontier_cells — the shape of the frontier
# ==========================================================================

# A 7x5 room: floor from (1,1) to (5,3) inside a solid wall border.
ROOM_ROWS = [
    "#######",
    "#.....#",
    "#.....#",
    "#.....#",
    "#######",
]

# A door in the middle of an east-west corridor, at (3, 1).
DOOR_CORRIDOR_ROWS = [
    "#######",
    "#..+..#",
    "#######",
]

# A closed door in the middle of a small room, at (2, 2).
DOOR_ROOM_ROWS = [
    "#####",
    "#...#",
    "#.+.#",
    "#...#",
    "#####",
]


def test_frontier_cells_is_exactly_the_explored_edge_facing_the_unexplored():
    level = make_level(ROOM_ROWS)
    explored = columns_upto(level, 3)  # columns 0..3 seen, 4..6 not

    # Column 3's floor cells each touch column 4, which is unexplored. Column 2 and
    # column 1 are fully surrounded by explored cells. The explored *walls* in column 3
    # touch the unexplored region too, but are not planning-passable, so they are out.
    assert frontier_cells(level, explored, NO_DOORS) == {(3, 1), (3, 2), (3, 3)}


def test_frontier_cells_never_returns_a_wall_or_anything_outside_explored():
    level = make_level(ROOM_ROWS)
    explored = columns_upto(level, 3)
    result = frontier_cells(level, explored, NO_DOORS)

    assert result <= explored
    for x, y in result:
        assert level.tile_at(x, y) is not Tile.WALL
        assert is_planning_passable(level, NO_DOORS, x, y) is True


def test_a_fully_explored_level_with_no_closed_door_has_no_frontier():
    level = make_level(ROOM_ROWS)
    assert frontier_cells(level, all_cells(level), NO_DOORS) == frozenset()


def test_an_empty_explored_set_has_no_frontier():
    level = make_level(ROOM_ROWS)
    assert frontier_cells(level, frozenset(), NO_DOORS) == frozenset()


def test_the_map_border_does_not_make_the_edge_a_frontier():
    # The unexplored neighbour must be *in bounds*: off-map is not somewhere to explore,
    # so a completely explored level is finished even though every border cell has
    # out-of-bounds neighbours.
    level = make_level(ROOM_ROWS)
    assert frontier_cells(level, all_cells(level), NO_DOORS) == frozenset()


def test_frontier_cells_returns_a_frozenset():
    level = make_level(ROOM_ROWS)
    assert isinstance(frontier_cells(level, columns_upto(level, 3), NO_DOORS), frozenset)


def test_out_of_bounds_members_of_explored_are_skipped_not_raised():
    level = make_level(ROOM_ROWS)
    explored = columns_upto(level, 3) | {(-1, -1), (99, 99), (3, -5)}
    assert frontier_cells(level, explored, NO_DOORS) == {(3, 1), (3, 2), (3, 3)}


# --------------------------------------------------------------------------
# frontier_cells — the closed-door clause
# --------------------------------------------------------------------------


def test_a_closed_door_is_a_frontier_even_when_everything_around_it_is_explored():
    # Without this clause auto-explore strolls past unopened doors and announces the
    # level is finished (RESEARCH-v4 §4). Here the entire map is explored, so the
    # door is a frontier for one reason only: it is shut.
    level = make_level(DOOR_ROOM_ROWS)
    assert frontier_cells(level, all_cells(level), NO_DOORS) == {(2, 2)}


def test_opening_that_door_with_nothing_left_unexplored_ends_the_frontier():
    level = make_level(DOOR_ROOM_ROWS)
    assert frontier_cells(level, all_cells(level), frozenset({(2, 2)})) == frozenset()


def test_an_opened_door_is_still_a_frontier_while_its_far_side_is_unexplored():
    level = make_level(DOOR_CORRIDOR_ROWS)
    near_side = columns_upto(level, 3)  # up to and including the door at (3, 1)

    closed = frontier_cells(level, near_side, NO_DOORS)
    opened = frontier_cells(level, near_side, frozenset({(3, 1)}))
    both_sides_seen = frontier_cells(level, all_cells(level), frozenset({(3, 1)}))

    assert closed == {(3, 1)}  # shut, and hiding the far side
    assert opened == {(3, 1)}  # open now, but the far side is still unseen
    assert both_sides_seen == frozenset()  # open and fully seen: nothing left


def test_a_closed_door_outside_explored_is_not_a_frontier():
    level = make_level(DOOR_CORRIDOR_ROWS)
    explored = columns_upto(level, 2)  # stops one cell short of the door
    assert (3, 1) not in frontier_cells(level, explored, NO_DOORS)
    assert frontier_cells(level, explored, NO_DOORS) == {(2, 1)}


# --------------------------------------------------------------------------
# frontier_cells — THE NO-CHEATING TESTS
# --------------------------------------------------------------------------

# Two levels that agree on every cell of columns 0..3 and disagree beyond it. The
# explored region is exactly columns 0..3, so from the character's perspective these
# two dungeons are indistinguishable and the frontier must come out identical.
TWIN_NEAR_SIDE = [
    "#######",
    "#..+..#",
    "#######",
]
TWIN_FAR_SIDE_SOLID = [
    "#######",
    "#..+###",
    "#######",
]


def test_the_two_twin_levels_really_are_identical_inside_and_different_outside():
    # Guards the guard: if this ever stopped holding, the test below would pass for
    # the wrong reason.
    a = make_level(TWIN_NEAR_SIDE)
    b = make_level(TWIN_FAR_SIDE_SOLID)
    for y in range(a.height):
        for x in range(4):
            assert a.tile_at(x, y) == b.tile_at(x, y)
    assert any(
        a.tile_at(x, y) != b.tile_at(x, y)
        for y in range(a.height)
        for x in range(4, a.width)
    )


def test_frontier_cells_returns_the_same_set_for_two_levels_that_differ_only_unexplored():
    # THE test of this task. An implementation that peeks at unexplored terrain — for
    # instance by asking whether the unexplored neighbour is walkable — reports
    # different frontiers for these two maps. Reading only `explored` cannot.
    a = make_level(TWIN_NEAR_SIDE)
    b = make_level(TWIN_FAR_SIDE_SOLID)
    explored = columns_upto(a, 3)

    assert frontier_cells(a, explored, NO_DOORS) == frontier_cells(b, explored, NO_DOORS)
    assert frontier_cells(a, explored, NO_DOORS) == {(3, 1)}

    # And again with the door open, so the answer rests on the unexplored-neighbour
    # clause alone rather than on "it is a closed door". Beyond the door, level A has
    # floor and level B has solid rock; a cheat would notice, and would report an empty
    # frontier for B.
    opened = frozenset({(3, 1)})
    assert frontier_cells(a, explored, opened) == frontier_cells(b, explored, opened)
    assert frontier_cells(a, explored, opened) == {(3, 1)}


class UnexploredTerrainIsSecret:
    """A stand-in for :class:`Level` that raises the instant anything asks what an
    *unexplored* cell contains.

    Duck-typed rather than a subclass so that every path into the grid has to come
    through the overrides here — including ``is_walkable``, which on the real ``Level``
    would otherwise reach ``Level.tile_at`` directly and slip past the check.
    """

    def __init__(self, level: Level, explored: frozenset[tuple[int, int]]) -> None:
        self._level = level
        self._explored = explored
        self.width = level.width
        self.height = level.height

    def in_bounds(self, x: int, y: int) -> bool:
        return self._level.in_bounds(x, y)

    def tile_at(self, x: int, y: int) -> Tile:
        if (x, y) not in self._explored:
            raise AssertionError(
                f"frontier_cells peeked at unexplored terrain at ({x}, {y})"
            )
        return self._level.tile_at(x, y)

    def is_walkable(self, x: int, y: int) -> bool:
        if not self.in_bounds(x, y):
            return False
        return is_walkable_tile(self.tile_at(x, y))


def has_an_unexplored_walkable_neighbour(
    level: Level, explored: frozenset[tuple[int, int]], cell: tuple[int, int]
) -> bool:
    """Is this a cell where a peeking implementation would actually *reach* for the
    unexplored tile? Used to prove the spy tests below exercise the cheat path rather
    than passing because nothing ever looks."""
    x, y = cell
    return any(
        level.in_bounds(x + dx, y + dy)
        and (x + dx, y + dy) not in explored
        and level.is_walkable(x + dx, y + dy)
        for dx, dy in pathfind.DIRECTIONS
    )


def test_frontier_cells_never_asks_what_an_unexplored_cell_contains_hand_built():
    level = make_level(ROOM_ROWS)
    explored = columns_upto(level, 3)
    spy = UnexploredTerrainIsSecret(level, explored)

    honest = frontier_cells(level, explored, NO_DOORS)
    assert honest == {(3, 1), (3, 2), (3, 3)}
    # The frontier here genuinely borders unexplored floor, so an implementation that
    # inspects that floor would touch the spy and be caught.
    assert any(has_an_unexplored_walkable_neighbour(level, explored, c) for c in honest)

    assert frontier_cells(spy, explored, NO_DOORS) == honest  # type: ignore[arg-type]


def test_frontier_cells_never_asks_what_an_unexplored_cell_contains_generated():
    # The same proof on a real 80x22 dungeon. The explored region is the left half of
    # the map, so its boundary slices through rooms and corridors and plenty of
    # frontier cells sit next to unexplored *floor* — exactly where a peek would happen.
    level = generate_level(1234)
    explored = frozenset(
        (x, y) for y in range(level.height) for x in range(level.width // 2)
    )
    spy = UnexploredTerrainIsSecret(level, explored)

    honest = frontier_cells(level, explored, NO_DOORS)
    assert honest
    assert any(has_an_unexplored_walkable_neighbour(level, explored, c) for c in honest)

    assert frontier_cells(spy, explored, NO_DOORS) == honest  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# frontier_cells — purity
# --------------------------------------------------------------------------


def test_frontier_cells_does_not_modify_its_arguments():
    level = make_level(DOOR_CORRIDOR_ROWS)
    level_before = copy.deepcopy(level)
    explored = columns_upto(level, 3)
    explored_before = frozenset(explored)
    doors = frozenset({(3, 1)})
    doors_before = frozenset(doors)

    frontier_cells(level, explored, doors)

    assert level == level_before
    assert level.grid == level_before.grid
    assert explored == explored_before
    assert doors == doors_before


def test_frontier_cells_is_deterministic_across_repeated_and_reordered_input():
    level = make_level(ROOM_ROWS)
    cells = [(x, y) for y in range(level.height) for x in range(4)]
    forwards = frozenset(cells)
    backwards = frozenset(reversed(cells))

    first = frontier_cells(level, forwards, NO_DOORS)
    assert first == frontier_cells(level, forwards, NO_DOORS)
    assert first == frontier_cells(level, backwards, NO_DOORS)


# ==========================================================================
# walk_step
# ==========================================================================

STRAIGHT_ROWS = [
    "#######",
    "#.....#",
    "#######",
]

# A corridor that runs east from (1,1) to (2,1) and then turns south.
BEND_ROWS = [
    "#####",
    "#..##",
    "##.##",
    "##.##",
    "#####",
]

# A T: the east-west corridor (1..3, 1) meets a southward stub at (2, 2).
T_ROWS = [
    "#####",
    "#...#",
    "##.##",
    "#####",
]

# A corridor dropping from (4,1) into a room that fills (2..6, 3..5).
OPENING_ROWS = [
    "#########",
    "####.####",
    "####.####",
    "##.....##",
    "##.....##",
    "##.....##",
    "#########",
]

# An open room with a corridor mouth leaving it north at (4,0) and south at (4,4).
HALL_ROWS = [
    "####.####",
    "#.......#",
    "#.......#",
    "#.......#",
    "####.####",
]


def test_walk_step_follows_a_straight_corridor_and_reports_no_reason():
    p = char_passable(STRAIGHT_ROWS)
    assert walk_step(p, (3, 1), (2, 1), (1, 0)) == ((4, 1), "")


def test_came_from_stops_the_walk_turning_round():
    # Standing mid-corridor with two passable neighbours: the one behind is excluded,
    # so the single remaining candidate is ahead — and reversing came_from reverses
    # the walk, which shows the exclusion is what is doing the work.
    p = char_passable(STRAIGHT_ROWS)
    assert walk_step(p, (3, 1), (2, 1), (1, 0))[0] == (4, 1)
    assert walk_step(p, (3, 1), (4, 1), (1, 0))[0] == (2, 1)


def test_walk_step_follows_a_bend_even_though_direction_points_elsewhere():
    # Direction is still east from when the walk started; the corridor turns south.
    # The corridor wins: `direction` is stale after the first step and is not consulted.
    p = char_passable(BEND_ROWS)
    assert walk_step(p, (2, 1), (1, 1), (1, 0)) == ((2, 2), "")


def test_walk_step_stops_blocked_at_a_dead_end():
    p = char_passable(STRAIGHT_ROWS)
    assert walk_step(p, (5, 1), (4, 1), (1, 0)) == (None, "blocked")


def test_walk_step_stops_at_a_t_junction_without_moving():
    p = char_passable(T_ROWS)
    next_coord, reason = walk_step(p, (2, 1), (2, 2), (0, -1))
    assert next_coord is None
    assert reason == "intersection"
    # The junction really is one by the pathfinder's own definition.
    assert is_intersection(p, 2, 1) is True


def test_walk_step_stops_before_a_room_and_does_not_return_the_room_cell():
    p = char_passable(OPENING_ROWS)
    room_cell = (4, 3)
    assert is_wide(p, *room_cell) is True

    next_coord, reason = walk_step(p, (4, 2), (4, 1), (0, 1))
    assert reason == "opening"
    assert next_coord is None
    assert next_coord != room_cell  # stops one cell short, never enters


def test_walk_step_walks_the_corridor_then_stops_at_the_opening():
    p = char_passable(OPENING_ROWS)
    assert walk_step(p, (4, 1), None, (0, 1)) == ((4, 2), "")
    assert walk_step(p, (4, 2), (4, 1), (0, 1)) == (None, "opening")


def test_walk_step_in_the_open_goes_straight_ignoring_side_openings():
    p = char_passable(HALL_ROWS)
    assert is_wide(p, 1, 1) is True

    position = (1, 1)
    visited = [position]
    for _ in range(20):
        nxt, reason = walk_step(p, position, visited[-2] if len(visited) > 1 else None, (1, 0))
        if nxt is None:
            break
        position = nxt
        visited.append(position)

    # Straight across the hall, right past the corridor mouth above (4,1), to the wall.
    assert visited == [(x, 1) for x in range(1, 8)]
    assert reason == "blocked"


def test_a_wide_area_never_follows_a_turn():
    # At (7,1) the hall continues south, but the walk was going east and east is wall.
    p = char_passable(HALL_ROWS)
    assert p(7, 2) is True
    assert walk_step(p, (7, 1), (6, 1), (1, 0)) == (None, "blocked")


def test_walk_step_in_the_open_may_step_into_a_corridor_mouth():
    # The "stop before a room" rule guards entering a *room*; leaving one into a
    # corridor is an ordinary step in the requested direction.
    p = char_passable(HALL_ROWS)
    assert is_wide(p, 4, 1) is True
    assert is_wide(p, 4, 0) is False
    assert walk_step(p, (4, 1), (4, 2), (0, -1)) == ((4, 0), "")


def test_zero_direction_in_the_open_is_blocked():
    p = char_passable(HALL_ROWS)
    assert is_wide(p, 2, 2) is True
    assert walk_step(p, (2, 2), None, (0, 0)) == (None, "blocked")


def test_zero_direction_in_a_corridor_is_blocked():
    # Not spelled out by CONTRACT-v4 §11, which covers the open case, but the only
    # coherent answer: position + (0,0) is the cell already occupied, so "moving"
    # there would burn a turn going nowhere, for ever.
    p = char_passable(STRAIGHT_ROWS)
    assert is_wide(p, 3, 1) is False
    assert walk_step(p, (3, 1), None, (0, 0)) == (None, "blocked")
    assert walk_step(p, (3, 1), (2, 1), (0, 0)) == ((4, 1), "")  # a real corridor step


def test_the_first_step_of_a_corridor_walk_uses_direction():
    # came_from is None, and both neighbours are passable, so the corridor rule alone
    # could not choose. The direction the player pressed decides — and reversing it
    # reverses the step.
    p = char_passable(STRAIGHT_ROWS)
    assert walk_step(p, (3, 1), None, (1, 0)) == ((4, 1), "")
    assert walk_step(p, (3, 1), None, (-1, 0)) == ((2, 1), "")


def test_the_first_step_out_of_a_junction_obeys_the_key_that_was_pressed():
    # A first step never reports "intersection": the player has just said which way to
    # go, so it is obeyed rather than second-guessed. Only later steps, where
    # `direction` is stale, hand the decision to the corridor.
    p = char_passable(T_ROWS)
    assert is_intersection(p, 2, 1) is True
    assert walk_step(p, (2, 1), None, (1, 0)) == ((3, 1), "")
    assert walk_step(p, (2, 1), None, (0, 1)) == ((2, 2), "")
    assert walk_step(p, (2, 1), None, (0, -1)) == (None, "blocked")  # into the wall


def test_walk_step_stops_blocked_when_the_first_step_walks_into_a_wall():
    p = char_passable(STRAIGHT_ROWS)
    assert walk_step(p, (3, 1), None, (0, -1)) == (None, "blocked")


def test_the_passable_callable_decides_whether_a_closed_door_may_be_walked_into():
    # walk_step has no opinion about doors; it asks the predicate it was given. With
    # the planning predicate the door is a cell to bump (and the turn loop opens it);
    # with the movement predicate it is simply a wall.
    level = make_level(["#####", "#.+.#", "#####"])
    assert walk_step(planning_passable_for(level), (1, 1), None, (1, 0)) == ((2, 1), "")
    assert walk_step(
        lambda x, y: is_passable(level, NO_DOORS, x, y), (1, 1), None, (1, 0)
    ) == (None, "blocked")


# --------------------------------------------------------------------------
# walk_step — one step at a time, over a whole journey
# --------------------------------------------------------------------------

JOURNEY_ROWS = [
    "#########",
    "#.#######",
    "#.#######",
    "#...#####",
    "###.#####",
    "###.#####",
    "##.....##",
    "##.....##",
    "##.....##",
    "#########",
]


def test_a_walk_driven_one_step_per_turn_follows_two_bends_and_halts_at_the_room():
    """The turn loop's usage: call walk_step once per turn, carrying came_from."""
    p = char_passable(JOURNEY_ROWS)
    position = (1, 1)
    came_from = None
    path = []
    reason = ""

    for _ in range(50):
        nxt, reason = walk_step(p, position, came_from, (0, 1))
        if nxt is None:
            break
        came_from, position = position, nxt
        path.append(position)

    assert path == [(1, 2), (1, 3), (2, 3), (3, 3), (3, 4), (3, 5)]
    assert reason == "opening"
    assert position == (3, 5)  # one cell short of the room, which starts at (3, 6)
    assert is_wide(p, 3, 6) is True


# --------------------------------------------------------------------------
# walk_step — total behaviour
# --------------------------------------------------------------------------


def test_walk_step_reason_is_always_one_of_the_four_contract_strings():
    p = char_passable(JOURNEY_ROWS)
    directions = ((0, 0), (0, -1), (1, 0), (0, 1), (-1, 0), (1, 1), (-1, -1))
    for y in range(-1, len(JOURNEY_ROWS) + 1):
        for x in range(-1, len(JOURNEY_ROWS[0]) + 1):
            for came_from in (None, (x - 1, y), (x, y - 1)):
                for direction in directions:
                    nxt, reason = walk_step(p, (x, y), came_from, direction)
                    assert reason in ("", "blocked", "intersection", "opening")
                    assert (nxt is None) == (reason != "")


def test_a_moving_step_is_always_to_an_adjacent_passable_cell():
    p = char_passable(JOURNEY_ROWS)
    for y in range(len(JOURNEY_ROWS)):
        for x in range(len(JOURNEY_ROWS[0])):
            for came_from in (None, (x - 1, y), (x, y + 1)):
                for direction in ((0, -1), (1, 0), (0, 1), (-1, 0)):
                    nxt, reason = walk_step(p, (x, y), came_from, direction)
                    if nxt is None:
                        continue
                    assert reason == ""
                    assert p(*nxt) is True
                    assert max(abs(nxt[0] - x), abs(nxt[1] - y)) == 1
                    if not is_wide(p, x, y):
                        # Only the corridor rule excludes came_from. In the open the
                        # step is `position + direction` and nothing else, so an
                        # artificial came_from that happens to lie that way is
                        # ignored — a real walk keeps a fixed direction, so it never
                        # doubles back.
                        assert nxt != came_from


def test_walk_step_never_raises_far_out_of_bounds():
    p = char_passable(STRAIGHT_ROWS)
    for position in ((-50, -50), (10**6, 10**6), (-1, 1), (7, 1)):
        for came_from in (None, (0, 0)):
            walk_step(p, position, came_from, (1, 0))


def test_walk_step_is_deterministic():
    p = char_passable(JOURNEY_ROWS)
    for _ in range(3):
        assert walk_step(p, (1, 1), None, (0, 1)) == ((1, 2), "")
        assert walk_step(p, (3, 5), (3, 4), (0, 1)) == (None, "opening")


# ==========================================================================
# Import hygiene (CONTRACT-v4 §10)
# ==========================================================================


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


def test_activity_module_imports_are_within_the_contract_graph():
    imports = _module_imports(pathlib.Path(activity.__file__))
    assert "curses" not in imports
    roguelike_imports = {n for n in imports if n.split(".")[0] == "roguelike"}
    assert roguelike_imports == {
        "roguelike.level",
        "roguelike.world",
        "roguelike.pathfind",
    }
    for forbidden in ("roguelike.game", "roguelike.render", "roguelike.keys",
                      "roguelike.events"):
        assert forbidden not in imports
    assert imports <= {
        "__future__",
        "dataclasses",
        "enum",
        "roguelike.level",
        "roguelike.world",
        "roguelike.pathfind",
    }


def test_activity_exports_exactly_the_contract_surface():
    assert set(activity.__all__) == {
        "ActivityKind",
        "Activity",
        "frontier_cells",
        "walk_step",
    }


# ==========================================================================
# End to end, on real generated levels
# ==========================================================================

SEEDS = (1234, 7, 42, 2026, 0)
TURN_CAP = 2000  # a failsafe only; every run must finish far below it


class ExploreRun:
    """What one auto-explore run over a real level did."""

    def __init__(self) -> None:
        self.turns = 0
        self.stop_reason = "cap"
        self.coverage = 0.0
        self.worst_plan_ms = 0.0
        self.frontiers_were_always_sane = True


def auto_explore(level: Level, radius: int = fov.DEFAULT_RADIUS) -> ExploreRun:
    """Drive frontier search with the real pathfinder, field of view and movement.

    This is the shape ``advance`` will take in T21, minus ``GameState``: every turn,
    recompute the frontier from ``explored`` alone, path to the nearest one over the
    planning predicate *restricted to explored cells*, take one step by the ordinary
    movement rules (so a closed door is bumped open and costs its turn), and fold the
    new field of view into ``explored``.
    """
    run = ExploreRun()
    player = level.player_start
    open_doors: frozenset[tuple[int, int]] = frozenset()
    explored = fov.compute_visible(level, open_doors, player, radius)

    while run.turns < TURN_CAP:
        started = time.perf_counter()
        frontier = frontier_cells(level, explored, open_doors)

        if not frontier <= explored:
            run.frontiers_were_always_sane = False

        # A cell already stood on is not somewhere to walk to: find_path would return
        # the one-cell path [player] and the walk would never progress.
        goals = frontier - {player}
        if not goals:
            run.stop_reason = "frontiers exhausted"
            break

        def passable(x: int, y: int, _e=explored, _d=open_doors) -> bool:
            return (x, y) in _e and is_planning_passable(level, _d, x, y)

        path = pathfind.find_path(passable, player, goals)
        run.worst_plan_ms = max(run.worst_plan_ms, (time.perf_counter() - started) * 1000)

        if path is None or len(path) < 2:
            run.stop_reason = "no route to any frontier"
            break

        step_x, step_y = path[1]
        result = movement.try_move(
            level, player, step_x - player[0], step_y - player[1], open_doors
        )
        if result.blocked_by_door is not None:
            open_doors = open_doors | {result.blocked_by_door}  # bump-to-open
        elif result.moved:
            player = result.position
        else:
            run.stop_reason = "stuck against terrain"
            break

        run.turns += 1
        explored = explored | fov.compute_visible(level, open_doors, player, radius)

    walkable = {
        (x, y)
        for y in range(level.height)
        for x in range(level.width)
        if level.is_walkable(x, y)
    }
    run.coverage = 100.0 * len(walkable & explored) / len(walkable)
    return run


@pytest.mark.parametrize("seed", SEEDS)
def test_auto_explore_covers_a_real_level_and_stops_because_it_is_finished(seed):
    level = generate_level(seed)
    run = auto_explore(level)

    assert run.stop_reason == "frontiers exhausted"
    assert run.turns < TURN_CAP  # the cap is a failsafe and must never be what stops it
    assert run.coverage >= 95.0
    assert run.frontiers_were_always_sane


def test_auto_explore_planning_stays_far_inside_the_hundred_millisecond_budget():
    # Reference measurement: ~2 ms per turn against the 100 ms budget the paced loop
    # allows (CONTRACT-v4 §0.10). The margin here is deliberately generous so a loaded
    # machine cannot make this flap.
    worst = max(auto_explore(generate_level(seed)).worst_plan_ms for seed in SEEDS)
    assert worst < 50.0


def test_auto_explore_opens_the_doors_it_needs_and_finishes_behind_them():
    # The load-bearing pair from RESEARCH-v4 §4 in one test: a level with doors is
    # explored to completion, which can only happen if routes were planned *through*
    # closed doors and closed doors counted as frontiers.
    level = generate_level(42)
    doors = [
        (x, y)
        for y in range(level.height)
        for x in range(level.width)
        if level.tile_at(x, y) is Tile.DOOR
    ]
    assert doors, "seed 42 is expected to contain doors"

    run = auto_explore(level)
    assert run.coverage == 100.0
    assert run.stop_reason == "frontiers exhausted"


def test_frontier_search_does_not_stall_in_the_first_room():
    # The specific failure mode of forgetting is_planning_passable: every frontier
    # behind a door is unreachable, so the run stops after a handful of turns having
    # seen only the starting room. Shown here as a floor on turns and coverage.
    level = generate_level(7)
    run = auto_explore(level)
    assert run.turns > 50
    assert run.coverage > 50.0
