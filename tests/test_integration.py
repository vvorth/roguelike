"""End-to-end checks across the whole stack — orchestrator-owned (CONTRACT §9 / v2 / v3).

Every test here crosses at least two task boundaries, which is what no individual
worker could verify: each of them only ever saw their own module plus the frozen
core types. Single-module behaviour is covered by the per-task suites and is
deliberately not repeated here.

v3 makes the dungeon multi-level, so this file now also pins the properties that
only exist once generation, the stair contract, fog persistence and the message
system meet: that descending keeps the player on the same coordinate, that the
level below anchors its up-staircase there, that fog and opened doors survive a
round trip, and that the chrome rows carry the right text.

v4 adds automatic navigation, so this file now also pins the properties that
only exist once input, pathfinding, the frontier planner and the turn loop meet:
that a planned route is actually walkable by the real movement rules, that
auto-explore reaches the whole level using only what has been seen and then
stops, that travel reaches a staircase, and that an activity dies on any command
and never survives a level change.

Nothing in this file initialises curses. The live curses session is verified
separately, out of band, and recorded in .plan/INTEGRATION-v4.md.
"""

from __future__ import annotations

import copy
import subprocess
import sys
from collections import deque
from pathlib import Path

import pytest

from roguelike import activity, dungeon, events, pathfind, world
from roguelike.events import EventKind
from roguelike.fov import compute_visible
from roguelike.game import (
    GameState,
    advance,
    format_stats,
    format_status_right,
    new_game,
    step,
)
from roguelike.generator import generate_level
from roguelike.keys import Command, CommandKind, translate_key
from roguelike.level import Level
from roguelike.render import Chrome, render_to_cells, to_lines
from roguelike.style import Role, Visibility
from roguelike.tiles import DOOR_OPEN_CHAR, PLAYER_CHAR, TILE_CHARS, Tile

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SEEDS = (0, 1, 7, 42, 1234, 2026, -1)
SMALL = (40, 18)          # a real dungeon, small enough to walk quickly

DESCEND = Command(CommandKind.DESCEND)
ASCEND = Command(CommandKind.ASCEND)

WALK_KEYS = "jjllkkhhyubn55nnbbjjllll"


# --------------------------------------------------------------------------
# Helpers — written here, independent of any module under test
# --------------------------------------------------------------------------


def walkable_cells(level: Level) -> set[tuple[int, int]]:
    return {
        (x, y)
        for y in range(level.height)
        for x in range(level.width)
        if level.is_walkable(x, y)
    }


def flood_fill(level: Level, start: tuple[int, int]) -> set[tuple[int, int]]:
    """4-directional flood fill over walkable terrain, implemented here so a bug
    in the generator's own self-check cannot hide from this suite."""
    if not level.is_walkable(*start):
        return set()
    seen = {start}
    queue = deque([start])
    while queue:
        x, y = queue.popleft()
        for nxt in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if nxt not in seen and level.is_walkable(*nxt):
                seen.add(nxt)
                queue.append(nxt)
    return seen


def open_spot(level: Level, x: int, y: int) -> bool:
    """'At least 1 tile away from any wall' — all eight neighbours non-WALL."""
    return all(
        level.in_bounds(x + dx, y + dy)
        and level.tile_at(x + dx, y + dy) is not Tile.WALL
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
    )


def route_to(state: GameState, target: tuple[int, int]) -> GameState | None:
    """Walk to ``target`` by breadth-first search over *real* steps.

    Every move goes through translate_key -> step, so this exercises the whole
    input/movement/FOV path rather than teleporting the player.
    """
    seen = {state.player}
    queue = deque([state])
    while queue:
        current = queue.popleft()
        if current.player == target:
            return current
        for key in "hjkl":
            nxt = step(current, translate_key(key))
            if nxt.player != current.player and nxt.player not in seen:
                seen.add(nxt.player)
                queue.append(nxt)
            elif nxt.open_doors != current.open_doors:
                queue.append(nxt)
    return None


def descend_once(state: GameState) -> GameState:
    at_stair = route_to(state, state.level.stairs_down[0])
    assert at_stair is not None, "the down-staircase was unreachable"
    return step(at_stair, DESCEND)


# --------------------------------------------------------------------------
# Terrain and stairs: generator x level  (v1/v2 properties + the v3 stair rules)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_every_walkable_tile_is_reachable_from_the_up_staircase(seed: int) -> None:
    level = generate_level(seed)
    assert flood_fill(level, level.stairs_up) == walkable_cells(level)


@pytest.mark.parametrize("seed", SEEDS)
def test_both_staircases_are_valid_spawn_cells(seed: int) -> None:
    """Requirement 1, and what makes the level below anchorable at this one's
    down-staircase."""
    level = generate_level(seed)
    for coord in (level.stairs_up, *level.stairs_down):
        assert open_spot(level, *coord), f"seed {seed}: {coord} is beside a wall"
        # An open spot is provably within the anchorable range.
        assert 2 <= coord[0] <= level.width - 3
        assert 2 <= coord[1] <= level.height - 3


@pytest.mark.parametrize("seed", SEEDS)
def test_the_spawn_is_the_up_staircase(seed: int) -> None:
    level = generate_level(seed)
    assert level.player_start == level.stairs_up
    assert level.tile_at(*level.stairs_up) is Tile.STAIRS_UP
    assert level.tile_at(*level.stairs_down[0]) is Tile.STAIRS_DOWN


@pytest.mark.parametrize("seed", SEEDS)
def test_the_two_staircases_are_distinct_and_usually_far_apart(seed: int) -> None:
    level = generate_level(seed)
    assert level.stairs_up != level.stairs_down[0]
    if len(level.rooms) > 1:
        up_room = [r for r in level.rooms if r.contains(*level.stairs_up)]
        down_room = [r for r in level.rooms if r.contains(*level.stairs_down[0])]
        assert up_room and down_room
        assert up_room[0] != down_room[0], "stairs must not share a room"


@pytest.mark.parametrize("seed", SEEDS)
def test_every_door_is_still_embedded_in_a_wall_run(seed: int) -> None:
    """The v2 door fix must survive the v3 generator rewrite."""
    level = generate_level(seed)
    for y in range(level.height):
        for x in range(level.width):
            if level.tile_at(x, y) is not Tile.DOOR:
                continue

            def tile(px, py):
                return level.tile_at(px, py) if level.in_bounds(px, py) else Tile.WALL

            walls_lr = tile(x - 1, y) is Tile.WALL and tile(x + 1, y) is Tile.WALL
            walls_ud = tile(x, y - 1) is Tile.WALL and tile(x, y + 1) is Tile.WALL
            open_lr = tile(x - 1, y) is not Tile.WALL and tile(x + 1, y) is not Tile.WALL
            open_ud = tile(x, y - 1) is not Tile.WALL and tile(x, y + 1) is not Tile.WALL
            assert (walls_ud and open_lr) or (walls_lr and open_ud)
            for nxt in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if level.in_bounds(*nxt):
                    assert level.tile_at(*nxt) is not Tile.DOOR


# --------------------------------------------------------------------------
# The descent chain: dungeon x generator x level
# --------------------------------------------------------------------------


@pytest.mark.parametrize("master", (0, 7, 1234, -1))
def test_a_five_level_chain_lines_up_and_stays_connected(master: int) -> None:
    """Level N+1's up-staircase is exactly level N's down-staircase, and every
    level is fully connected from it."""
    required = None
    previous_down = None
    for depth in range(1, 6):
        level = dungeon.level_for(master, depth, required_up=required, width=40, height=18)
        assert level.depth == depth
        if previous_down is not None:
            assert level.stairs_up == previous_down, (
                f"master {master} depth {depth}: stairs do not line up"
            )
        assert flood_fill(level, level.stairs_up) == walkable_cells(level)
        assert open_spot(level, *level.stairs_down[0])
        previous_down = level.stairs_down[0]
        required = previous_down


def test_the_same_master_seed_rebuilds_the_same_dungeon() -> None:
    def chain(master):
        out, required = [], None
        for depth in range(1, 5):
            lv = dungeon.level_for(master, depth, required_up=required, width=40, height=18)
            out.append((lv.grid, lv.stairs_up, lv.stairs_down))
            required = lv.stairs_down[0]
        return out

    assert chain(31337) == chain(31337)
    assert chain(31337) != chain(31338)


def test_seed_derivation_is_stable_across_processes() -> None:
    script = (
        "import sys; sys.path.insert(0, %r)\n"
        "from roguelike import dungeon\n"
        "print([dungeon.seed_for(1234, d) for d in range(1, 6)])\n"
    ) % str(PROJECT_ROOT)
    expected = str([dungeon.seed_for(1234, d) for d in range(1, 6)])
    for hash_seed in ("0", "1", "424242"):
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True,
            env={"PYTHONHASHSEED": hash_seed, "PATH": "/usr/bin:/bin"},
            cwd=str(PROJECT_ROOT), check=True,
        )
        assert result.stdout.strip() == expected


def test_branch_scaffolding_yields_a_different_dungeon() -> None:
    """Requirement 3's scaffolding: a branch index must actually change the seed."""
    assert dungeon.seed_for(1234, 3, branch=1) != dungeon.seed_for(1234, 3, branch=0)


# --------------------------------------------------------------------------
# Playing: keys x game x movement x fov x world
# --------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_a_scripted_walk_never_enters_a_wall_or_leaves_the_map(seed: int) -> None:
    state = new_game(seed, *SMALL)
    assert state.level.tile_at(*state.player) is Tile.STAIRS_UP

    for key in WALK_KEYS * 4:
        state = step(state, translate_key(key))
        x, y = state.player
        assert state.level.in_bounds(x, y)
        assert state.level.tile_at(x, y) is not Tile.WALL
        assert world.is_passable(state.level, state.open_doors, x, y)


@pytest.mark.parametrize("seed", SEEDS)
def test_turns_count_only_actions_that_cost_one(seed: int) -> None:
    state = new_game(seed, *SMALL)
    accepted = opened = 0
    for key in WALK_KEYS * 3:
        before = state
        state = step(state, translate_key(key))
        if state.player != before.player:
            accepted += 1
        elif state.open_doors != before.open_doors:
            opened += 1
        assert state.turns == accepted + opened


def test_pressing_descend_off_the_stairs_costs_nothing_but_says_so() -> None:
    state = new_game(1234, *SMALL)
    moved = step(state, translate_key("j"))
    if moved.player == state.player:
        pytest.skip("could not step off the staircase")
    after = step(moved, DESCEND)
    assert after.turns == moved.turns
    assert after.depth == moved.depth
    assert after.player == moved.player
    assert [e.kind for e in after.events] == [EventKind.NO_STAIRS_DOWN]


@pytest.mark.parametrize("seed", SEEDS)
def test_the_map_starts_hidden_and_fog_only_grows(seed: int) -> None:
    state = new_game(seed, *SMALL)
    total = state.level.width * state.level.height
    assert state.explored and len(state.explored) < total * 0.5

    previous = state
    for key in WALK_KEYS * 3:
        state = step(state, translate_key(key))
        assert state.explored >= previous.explored, "ground once seen was forgotten"
        assert state.explored >= state.visible
        previous = state


def test_visible_agrees_with_a_direct_fov_call() -> None:
    """game.step must delegate visibility rather than reimplement it."""
    state = new_game(1234, *SMALL)
    for key in WALK_KEYS:
        state = step(state, translate_key(key))
        assert state.visible == compute_visible(
            state.level, state.open_doors, state.player, state.radius
        )


# --------------------------------------------------------------------------
# Stairs end to end: the heart of v3
# --------------------------------------------------------------------------


@pytest.mark.parametrize("seed", (1234, 7, 42))
def test_descending_keeps_the_player_put_and_lines_the_stairs_up(seed: int) -> None:
    state = new_game(seed, *SMALL)
    at_stair = route_to(state, state.level.stairs_down[0])
    assert at_stair is not None
    assert [e.kind for e in at_stair.events] == [EventKind.STAIRS_HERE_DOWN], (
        "stepping onto the down-staircase must announce it"
    )

    below = step(at_stair, DESCEND)

    assert below.depth == at_stair.depth + 1
    assert below.player == at_stair.player, "descending must not move the player"
    assert below.level.stairs_up == at_stair.level.stairs_down[0]
    assert below.level.tile_at(*below.player) is Tile.STAIRS_UP
    assert below.turns == at_stair.turns + 1
    assert [e.kind for e in below.events] == [EventKind.DESCENDED]
    assert at_stair.depth in below.saved


@pytest.mark.parametrize("seed", (1234, 7))
def test_a_three_level_descent_stays_walkable_all_the_way_down(seed: int) -> None:
    state = new_game(seed, *SMALL)
    seen_depths = [state.depth]
    for _ in range(2):
        state = descend_once(state)
        seen_depths.append(state.depth)
        assert flood_fill(state.level, state.player) == walkable_cells(state.level)
        assert world.is_passable(state.level, state.open_doors, *state.player)
    assert seen_depths == [1, 2, 3]


def test_fog_and_doors_survive_a_round_trip() -> None:
    """The single most important v3 behaviour: climbing back must not reset the map."""
    state = new_game(1234, *SMALL)
    below = descend_once(state)
    upper_level = below.saved[1].level
    upper_explored = below.saved[1].explored
    upper_doors = below.saved[1].open_doors

    # Explore the lower level, then climb back.
    for key in WALK_KEYS * 3:
        below = step(below, translate_key(key))
    lower_explored, lower_doors = below.explored, below.open_doors

    at_up = route_to(below, below.level.stairs_up)
    assert at_up is not None
    back = step(at_up, ASCEND)

    assert back.depth == 1
    assert back.level is upper_level, "the saved level object must be restored"
    assert back.explored == upper_explored, "fog reset on the way up"
    assert back.open_doors == upper_doors, "doors closed themselves on the way up"
    assert back.player == upper_level.stairs_down[0], "must arrive on the stair used"
    assert [e.kind for e in back.events] == [EventKind.ASCENDED]

    # And going back down must restore the lower level too.
    again = step(route_to(back, upper_level.stairs_down[0]), DESCEND)
    assert again.explored == lower_explored
    assert again.open_doors == lower_doors


def test_climbing_out_at_depth_one_ends_the_game() -> None:
    state = new_game(1234, *SMALL)
    assert state.player == state.level.stairs_up
    after = step(state, ASCEND)
    assert after.running is False
    assert after.outcome == events.MESSAGES[EventKind.LEFT_DUNGEON]
    assert [e.kind for e in after.events] == [EventKind.LEFT_DUNGEON]
    # And a stopped game ignores everything afterwards.
    for command in (DESCEND, ASCEND, translate_key("j")):
        assert step(after, command) == after


def test_open_doors_are_tracked_per_level_not_globally() -> None:
    state = new_game(1234, *SMALL)
    below = descend_once(state)
    upper_doors = below.saved[1].open_doors
    for coord in below.open_doors:
        assert below.level.tile_at(*coord) is Tile.DOOR
    for coord in upper_doors:
        assert below.saved[1].level.tile_at(*coord) is Tile.DOOR


# --------------------------------------------------------------------------
# The frame: render x style x game x events
# --------------------------------------------------------------------------


def chrome_for(state: GameState) -> Chrome:
    return Chrome(
        stats=format_stats(state),
        message=events.message_for(state.events),
        status_right=format_status_right(state),
    )


@pytest.mark.parametrize("seed", (1234, 7))
def test_the_frame_has_two_chrome_rows_and_the_map_is_offset(seed: int) -> None:
    state = new_game(seed, *SMALL)
    width, height = SMALL
    for key in WALK_KEYS:
        state = step(state, translate_key(key))
        cells = render_to_cells(
            state.level, state.player, state.visible, state.explored,
            state.open_doors, chrome_for(state),
        )
        assert len(cells) == height + 2
        assert all(len(row) == width for row in cells)

        px, py = state.player
        assert cells[py + 1][px].char == PLAYER_CHAR, "map must be offset by one row"
        assert cells[py + 1][px].role is Role.PLAYER

        for y in range(height):
            for x in range(width):
                if (x, y) == (px, py):
                    continue
                cell = cells[y + 1][x]
                if (x, y) in state.visible:
                    assert cell.visibility is Visibility.VISIBLE
                elif (x, y) in state.explored:
                    assert cell.visibility is Visibility.EXPLORED
                else:
                    assert cell.visibility is Visibility.UNSEEN
                    assert cell.char == " "


def test_the_status_row_carries_the_level_and_seed() -> None:
    state = new_game(1234, *SMALL)
    lines = to_lines(
        render_to_cells(
            state.level, state.player, state.visible, state.explored,
            state.open_doors, chrome_for(state),
        )
    )
    assert lines[0].strip() == "", "the stats row is reserved and blank"
    assert lines[-1].rstrip().endswith("Level 1  Seed 1234")


def test_the_status_row_shows_a_message_and_keeps_the_level_readable() -> None:
    state = new_game(1234, *SMALL)
    moved = step(state, translate_key("j"))
    if moved.player == state.player:
        pytest.skip("could not step off the staircase")
    refused = step(moved, DESCEND)

    lines = to_lines(
        render_to_cells(
            refused.level, refused.player, refused.visible, refused.explored,
            refused.open_doors, chrome_for(refused),
        )
    )
    status = lines[-1]
    assert status.endswith("Level 1  Seed 1234"), "the level must always survive"
    message = events.MESSAGES[EventKind.NO_STAIRS_DOWN]
    # On a 40-column level the message is clipped; whatever survives is a prefix.
    shown = status[: len(status.rstrip()) - len("Level 1  Seed 1234")].strip()
    assert shown and message.startswith(shown)


def test_the_status_row_tracks_the_depth_as_you_descend() -> None:
    state = new_game(1234, *SMALL)
    assert format_status_right(state) == "Level 1  Seed 1234"
    below = descend_once(state)
    assert format_status_right(below) == "Level 2  Seed 1234"


def test_stair_glyphs_reach_the_frame() -> None:
    state = new_game(1234, *SMALL)
    cells = render_to_cells(
        state.level, state.player, state.visible, state.explored,
        state.open_doors, chrome_for(state),
    )
    ux, uy = state.level.stairs_up
    # The player stands on the up-staircase at spawn, so check the glyph table
    # and the down-staircase once it has been seen.
    assert TILE_CHARS[Tile.STAIRS_UP] == "<"
    assert TILE_CHARS[Tile.STAIRS_DOWN] == ">"
    assert cells[uy + 1][ux].char == PLAYER_CHAR

    at_stair = route_to(state, state.level.stairs_down[0])
    dx, dy = at_stair.level.stairs_down[0]
    frame = render_to_cells(
        at_stair.level, (0, 0), at_stair.visible, at_stair.explored,
        at_stair.open_doors, chrome_for(at_stair),
    )
    assert frame[dy + 1][dx].char == TILE_CHARS[Tile.STAIRS_DOWN]


def find_closed_door(state: GameState) -> tuple[GameState, str] | None:
    """Breadth-first walk to a state standing beside a closed door, and the key
    that bumps into it. Deterministic — never relies on a canned walk."""
    deltas = {"h": (-1, 0), "l": (1, 0), "k": (0, -1), "j": (0, 1)}
    seen = {state.player}
    queue = deque([state])
    while queue:
        current = queue.popleft()
        for key, (dx, dy) in deltas.items():
            target = (current.player[0] + dx, current.player[1] + dy)
            if world.is_closed_door(current.level, current.open_doors, *target):
                return current, key
            if target in seen or not world.is_passable(
                current.level, current.open_doors, *target
            ):
                continue
            seen.add(target)
            queue.append(step(current, translate_key(key)))
    return None


def test_an_opened_door_changes_glyph_in_the_frame() -> None:
    state = new_game(1234, *SMALL)
    found = find_closed_door(state)
    assert found is not None, "seed 1234 has no reachable closed door"
    at_door, key = found

    after = step(at_door, translate_key(key))
    door = next(iter(after.open_doors - at_door.open_doors))
    assert [e.kind for e in after.events] == [EventKind.DOOR_OPENED]

    before_cells = render_to_cells(
        at_door.level, at_door.player, at_door.visible, at_door.explored,
        at_door.open_doors, chrome_for(at_door),
    )
    after_cells = render_to_cells(
        after.level, after.player, after.visible, after.explored,
        after.open_doors, chrome_for(after),
    )
    assert before_cells[door[1] + 1][door[0]].char == TILE_CHARS[Tile.DOOR]
    assert after_cells[door[1] + 1][door[0]].char == DOOR_OPEN_CHAR
    assert after_cells[door[1] + 1][door[0]].role is Role.DOOR


def test_a_default_level_fills_a_classic_terminal_exactly() -> None:
    state = new_game(1234)
    lines = to_lines(
        render_to_cells(
            state.level, state.player, state.visible, state.explored,
            state.open_doors, chrome_for(state),
        )
    )
    assert len(lines) == 24
    assert all(len(line) == 80 for line in lines)


# --------------------------------------------------------------------------
# Immutability across the whole stack
# --------------------------------------------------------------------------


def test_a_full_descent_mutates_nothing() -> None:
    state = new_game(2026, *SMALL)
    reference = copy.deepcopy(state.level)
    original = state.level

    state = descend_once(state)
    for key in WALK_KEYS * 2:
        state = step(state, translate_key(key))
        render_to_cells(
            state.level, state.player, state.visible, state.explored,
            state.open_doors, chrome_for(state),
        )

    assert original == reference, "the upper level was mutated"
    assert state.saved[1].level is original


# --------------------------------------------------------------------------
# The entry point
# --------------------------------------------------------------------------


def test_main_parses_its_arguments() -> None:
    import main

    args = main.parse_args(["--seed", "99", "--width", "40", "--height", "15"])
    assert (args.seed, args.width, args.height) == (99, 40, 15)
    defaults = main.parse_args([])
    assert defaults.seed is None
    assert (defaults.width, defaults.height) == (80, 22)


def test_main_reports_impossible_dimensions_instead_of_crashing() -> None:
    result = subprocess.run(
        [sys.executable, "main.py", "--seed", "1", "--width", "3", "--height", "3"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 2
    assert "error:" in result.stderr
    assert "Traceback" not in result.stderr


def test_importing_the_whole_package_touches_no_terminal() -> None:
    script = (
        "import sys; sys.path.insert(0, %r)\n"
        "import curses\n"
        "import main\n"
        "from roguelike import (dungeon, events, fov, game, generator, keys, level,\n"
        "                       movement, render, style, tiles, world)\n"
        "assert not hasattr(curses, 'LINES'), 'curses was initialised on import'\n"
        "print('clean')\n"
    ) % str(PROJECT_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True,
        cwd=str(PROJECT_ROOT), stdin=subprocess.DEVNULL, check=True,
    )
    assert result.stdout.strip() == "clean"


# --------------------------------------------------------------------------
# v4 — diagonals: keys x movement x game
# --------------------------------------------------------------------------


SHIFT_DIAGONALS = {
    "K": (1, -1), "L": (1, 1), "J": (-1, 1), "H": (-1, -1),
}


def test_shift_diagonals_actually_move_the_player_diagonally() -> None:
    """The key layer and the movement layer must agree about 45 degrees clockwise."""
    state = new_game(1234, *SMALL)
    for key, (dx, dy) in SHIFT_DIAGONALS.items():
        command = translate_key(key)
        assert command.kind is CommandKind.MOVE
        assert (command.dx, command.dy) == (dx, dy)
        target = (state.player[0] + dx, state.player[1] + dy)
        after = step(state, command)
        if world.is_passable(state.level, state.open_doors, *target):
            assert after.player == target, f"{key} did not move diagonally"
            assert after.turns == state.turns + 1


def test_every_diagonal_spelling_is_the_same_command() -> None:
    """Shift+hjkl, yubn and the numpad must be interchangeable."""
    for shift, legacy, digit in (("K", "u", "9"), ("L", "n", "3"),
                                 ("J", "b", "1"), ("H", "y", "7")):
        assert translate_key(shift) == translate_key(legacy) == translate_key(digit)


# --------------------------------------------------------------------------
# v4 — pathfinding agrees with the movement rules
# --------------------------------------------------------------------------


@pytest.mark.parametrize("seed", (1234, 7, 42))
def test_a_planned_route_is_actually_walkable_by_the_real_engine(seed: int) -> None:
    """A path is only useful if the movement layer accepts every step of it.

    This is the seam between pathfind (which knows nothing of the engine) and
    movement (which knows nothing of planning). Routes are planned over
    ``is_planning_passable``, which admits closed doors precisely because bumping
    one opens it — so a door costs a turn without moving, and the same step is
    then retried. That is exactly what the activity layer does.
    """
    state = new_game(seed, *SMALL)
    level = state.level
    walked = state

    route = pathfind.find_path(
        lambda x, y: world.is_planning_passable(level, walked.open_doors, x, y),
        level.stairs_up, {level.stairs_down[0]},
    )
    assert route is not None and route[0] == level.stairs_up

    for nxt in route[1:]:
        dx, dy = nxt[0] - walked.player[0], nxt[1] - walked.player[1]
        assert max(abs(dx), abs(dy)) == 1, "path took a non-unit step"
        was_shut = world.is_closed_door(level, walked.open_doors, *nxt)
        walked = step(walked, Command(CommandKind.MOVE, dx, dy))
        if was_shut:
            # The bump opened it and cost a turn without moving; now walk through.
            assert nxt in walked.open_doors
            assert walked.player != nxt
            walked = step(walked, Command(CommandKind.MOVE, dx, dy))
        assert walked.player == nxt, "the engine refused a step the planner proposed"
    assert walked.player == level.stairs_down[0]


# --------------------------------------------------------------------------
# v4 — auto-explore end to end
# --------------------------------------------------------------------------


def run_activity(state: GameState, cap: int = 4000) -> tuple[GameState, int]:
    """Drive `advance` until the activity clears, as the real loop would."""
    ticks = 0
    while state.activity is not None and ticks < cap:
        state = advance(state)
        ticks += 1
    assert ticks < cap, "activity never finished"
    return state, ticks


@pytest.mark.parametrize("seed", (1234, 7, 42))
def test_auto_explore_reveals_the_level_and_then_stops(seed: int) -> None:
    state = new_game(seed, *SMALL)
    started = step(state, translate_key("E"))
    assert started.activity is not None
    assert started.turns == state.turns, "starting an activity costs no turn"

    final, ticks = run_activity(started)

    truth = walkable_cells(final.level)
    seen = {c for c in final.explored if final.level.is_walkable(*c)}
    coverage = 100 * len(seen) / len(truth)
    assert coverage >= 95, f"seed {seed}: only explored {coverage:.1f}%"
    assert final.activity is None
    assert [e.kind for e in final.events] == [EventKind.EXPLORED_EVERYTHING]
    assert final.depth == 1, "auto-explore must never descend"


def test_auto_explore_opens_doors_on_the_way() -> None:
    state = step(new_game(1234, *SMALL), translate_key("E"))
    final, _ = run_activity(state)
    assert final.open_doors, "a full explore should have opened at least one door"
    for coord in final.open_doors:
        assert final.level.tile_at(*coord) is Tile.DOOR


@pytest.mark.parametrize("seed", (1234, 7))
def test_the_frontier_never_depends_on_unexplored_ground(seed: int) -> None:
    """The no-cheating rule, checked at the integration level: the frontier must
    be a function of `explored` alone."""
    state = new_game(seed, *SMALL)
    mine = activity.frontier_cells(state.level, state.explored, state.open_doors)

    # Rebuild the level with every unexplored cell turned to wall. Anything the
    # planner reads outside `explored` would change the answer.
    from roguelike.level import Level as _Level, freeze_grid as _freeze
    rows = [
        [
            state.level.tile_at(x, y) if (x, y) in state.explored else Tile.WALL
            for x in range(state.level.width)
        ]
        for y in range(state.level.height)
    ]
    altered = _Level(
        state.level.width, state.level.height, _freeze(rows), state.level.rooms,
        state.level.player_start, state.level.seed,
        stairs_up=state.level.stairs_up, stairs_down=state.level.stairs_down,
        depth=state.level.depth,
    )
    theirs = activity.frontier_cells(altered, state.explored, state.open_doors)
    assert mine == theirs, "frontier_cells read terrain the character has not seen"


# --------------------------------------------------------------------------
# v4 — travel to a known staircase
# --------------------------------------------------------------------------


def test_pressing_descend_off_the_stairs_travels_to_a_known_staircase() -> None:
    explored = step(new_game(1234, *SMALL), translate_key("E"))
    explored, _ = run_activity(explored)
    assert explored.player != explored.level.stairs_down[0]

    travelling = step(explored, DESCEND)
    assert travelling.activity is not None
    assert [e.kind for e in travelling.events] == [EventKind.TRAVELLING]
    assert travelling.turns == explored.turns, "starting travel costs no turn"

    arrived, _ = run_activity(travelling)
    assert arrived.player == arrived.level.stairs_down[0]
    assert arrived.activity is None
    assert [e.kind for e in arrived.events] == [EventKind.ARRIVED]

    # And now the staircase actually works.
    below = step(arrived, DESCEND)
    assert below.depth == 2


def test_pressing_descend_with_no_known_staircase_only_reports_it() -> None:
    state = new_game(1234, *SMALL)
    moved = step(state, translate_key("j"))
    if moved.player == state.player:
        pytest.skip("could not step off the staircase")
    if moved.level.stairs_down[0] in moved.explored:
        pytest.skip("the down staircase is visible from the start on this seed")

    refused = step(moved, DESCEND)
    assert refused.activity is None, "must not travel toward an unknown staircase"
    assert refused.turns == moved.turns
    assert [e.kind for e in refused.events] == [EventKind.NO_STAIRS_DOWN]


# --------------------------------------------------------------------------
# v4 — auto-walk, and activity lifecycle
# --------------------------------------------------------------------------


def test_walk_prefix_then_a_direction_starts_a_walk() -> None:
    state = new_game(1234, *SMALL)
    prefixed = step(state, translate_key("w"))
    assert prefixed.awaiting_walk is True
    assert prefixed.turns == state.turns
    assert [e.kind for e in prefixed.events] == [EventKind.WALK_WHICH_WAY]

    walking = step(prefixed, translate_key("l"))
    assert walking.awaiting_walk is False
    assert walking.activity is not None

    stopped, _ = run_activity(walking)
    assert stopped.activity is None
    assert [e.kind for e in stopped.events][0] in {
        EventKind.NOTHING_FURTHER,
        EventKind.STOPPED_AT_JUNCTION,
        EventKind.STOPPED_AT_OPENING,
    }


def test_walk_prefix_then_a_non_direction_is_consumed() -> None:
    state = new_game(1234, *SMALL)
    prefixed = step(state, translate_key("w"))
    after = step(prefixed, translate_key("q"))
    assert after.running is True, "the consumed key must not quit"
    assert after.awaiting_walk is False
    assert after.activity is None
    assert after.turns == state.turns


def test_any_command_clears_a_running_activity() -> None:
    started = step(new_game(1234, *SMALL), translate_key("E"))
    assert started.activity is not None
    for key in ("j", "q", "E"):
        assert step(started, translate_key(key)).activity is None or key == "E"


def test_an_activity_does_not_survive_a_level_change() -> None:
    explored, _ = run_activity(step(new_game(1234, *SMALL), translate_key("E")))
    at_stair = route_to(explored, explored.level.stairs_down[0])
    assert at_stair is not None
    travelling = step(at_stair, translate_key("E"))
    assert travelling.activity is not None
    below = step(travelling, DESCEND)
    assert below.activity is None, "an activity must not cross levels"
    assert below.depth == 2


@pytest.mark.parametrize("seed", (1234, 7))
def test_an_activity_never_walks_the_player_into_a_wall(seed: int) -> None:
    """The safety invariant, now that something other than the player is driving."""
    state = step(new_game(seed, *SMALL), translate_key("E"))
    ticks = 0
    while state.activity is not None and ticks < 4000:
        state = advance(state)
        ticks += 1
        x, y = state.player
        assert state.level.in_bounds(x, y)
        assert world.is_passable(state.level, state.open_doors, x, y)
