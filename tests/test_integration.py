"""End-to-end checks across the whole stack — orchestrator-owned (CONTRACT §9 / §9 v2).

Every test here crosses at least two task boundaries, which is what no individual
worker could verify: each of them only ever saw their own module plus the frozen
core types. Single-module behaviour is covered by the per-task suites and is
deliberately not repeated here.

v2 adds colour, fog of war, permissive field of view and openable doors, so this
file now also pins the properties that only appear once those meet: that the map
starts hidden and is revealed by walking, that memory never shrinks, that an
unexplored map does not leak through the renderer, and that bumping a door opens
it and reveals what is behind.

Nothing in this file initialises curses. The live curses session is verified
separately, out of band, and recorded in .plan/INTEGRATION-v2.md.
"""

from __future__ import annotations

import copy
import subprocess
import sys
from collections import deque
from pathlib import Path

import pytest

from roguelike import world
from roguelike.fov import DEFAULT_RADIUS, compute_visible
from roguelike.game import GameState, format_status, new_game, step
from roguelike.generator import generate_level
from roguelike.keys import CommandKind, translate_key
from roguelike.level import Level
from roguelike.movement import try_move
from roguelike.render import render_to_cells, to_lines
from roguelike.style import Attr, Role, Visibility, attr_for
from roguelike.tiles import DOOR_OPEN_CHAR, PLAYER_CHAR, TILE_CHARS, Tile

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SEEDS = (0, 1, 7, 42, 1234, 31337, 2026, -1, -99)

# Mixes hjkl, diagonals, numpad digits, and an unbound key that must be a no-op.
WALK_KEYS = "jjllkkhhyubn55nnbbjjllll"


def walkable_cells(level: Level) -> set[tuple[int, int]]:
    return {
        (x, y)
        for y in range(level.height)
        for x in range(level.width)
        if level.is_walkable(x, y)
    }


def flood_fill(level: Level, start: tuple[int, int]) -> set[tuple[int, int]]:
    """4-directional flood fill over walkable terrain, written independently of
    the generator's own internal check so a bug there cannot hide from us."""
    if not level.is_walkable(*start):
        return set()
    seen = {start}
    queue = deque([start])
    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if (nx, ny) not in seen and level.is_walkable(nx, ny):
                seen.add((nx, ny))
                queue.append((nx, ny))
    return seen


def drive(state: GameState, keys: str) -> tuple[GameState, list[GameState]]:
    """Feed raw key characters through the real input abstraction into the loop —
    the headless equivalent of what game.run does with getch."""
    history = [state]
    for key in keys:
        state = step(state, translate_key(key))
        history.append(state)
    return state, history


# --------------------------------------------------------------------------
# Terrain: generator x level  (v1 properties that must not regress)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_every_walkable_tile_is_reachable_from_player_start(seed: int) -> None:
    level = generate_level(seed)
    assert flood_fill(level, level.player_start) == walkable_cells(level)


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("size", [(80, 22), (40, 15), (24, 12)])
def test_connectivity_holds_at_other_map_sizes(seed, size) -> None:
    width, height = size
    level = generate_level(seed, width, height)
    assert flood_fill(level, level.player_start) == walkable_cells(level)


@pytest.mark.parametrize("seed", SEEDS)
def test_every_door_is_embedded_in_a_wall_run(seed: int) -> None:
    """CONTRACT-v2 §3 G9b/G9c, re-derived here. The v1 build violated this on
    13.1% of doors; this test is the guard against a silent regression."""
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
            assert (walls_ud and open_lr) or (walls_lr and open_ud), (
                f"seed {seed}: malformed door at ({x}, {y})"
            )
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if level.in_bounds(nx, ny):
                    assert level.tile_at(nx, ny) is not Tile.DOOR


# --------------------------------------------------------------------------
# The walk: keys x game x movement x world x level
# --------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_scripted_walk_never_enters_a_wall_or_leaves_the_map(seed: int) -> None:
    """The core safety invariant, asserted after every single keystroke."""
    level = generate_level(seed)
    state = new_game(level)
    assert level.is_walkable(*state.player)

    final, history = drive(state, WALK_KEYS * 6)

    for index, snap in enumerate(history):
        x, y = snap.player
        assert level.in_bounds(x, y), f"seed {seed} @{index}: left the map at ({x}, {y})"
        assert level.tile_at(x, y) is not Tile.WALL, (
            f"seed {seed} @{index}: stood in a wall at ({x}, {y})"
        )
        # Stronger than v1: the player must stand only where the world is
        # currently passable, which now depends on which doors are open.
        assert world.is_passable(level, snap.open_doors, x, y)


@pytest.mark.parametrize("seed", SEEDS)
def test_turns_equal_accepted_moves_plus_doors_opened(seed: int) -> None:
    """v1's 'a rejected move consumes no turn', extended for the third outcome:
    bumping a closed door consumes a turn without moving."""
    level = generate_level(seed)
    state = new_game(level)
    accepted = opened = blocked = 0

    for key in WALK_KEYS * 4:
        before = state
        state = step(state, translate_key(key))
        if state.player != before.player:
            accepted += 1
        elif state.open_doors != before.open_doors:
            opened += 1
        elif state.turns == before.turns:
            blocked += 1
        assert state.turns == accepted + opened

    assert blocked > 0, "the walk was never blocked, so it proves nothing"


@pytest.mark.parametrize("seed", SEEDS)
def test_a_player_only_ever_stands_on_terrain_it_could_reach(seed: int) -> None:
    level = generate_level(seed)
    reachable = flood_fill(level, level.player_start)
    final, history = drive(new_game(level), WALK_KEYS * 4)
    for snap in history:
        assert snap.player in reachable


# --------------------------------------------------------------------------
# Fog of war: fov x game x level  (the heart of v2)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_the_map_starts_mostly_hidden(seed: int) -> None:
    """Fog of war is meaningful only if the opening frame hides most of the map."""
    level = generate_level(seed)
    state = new_game(level)
    total = level.width * level.height
    assert state.explored, "the starting cell must be seen"
    assert len(state.explored) < total * 0.5, (
        f"seed {seed}: {len(state.explored)}/{total} explored at turn 0 — "
        "fog of war is not hiding anything"
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_explored_grows_monotonically_and_never_shrinks(seed: int) -> None:
    level = generate_level(seed)
    _, history = drive(new_game(level), WALK_KEYS * 6)
    for older, newer in zip(history, history[1:]):
        assert newer.explored >= older.explored, "ground once seen was forgotten"
        assert newer.explored >= newer.visible, "a visible cell was not explored"


@pytest.mark.parametrize("seed", SEEDS)
def test_passing_through_a_door_reveals_strictly_more_of_the_map(seed: int) -> None:
    """Fog lifts as you explore — but only where it actually can.

    A short walk *inside one room* legitimately reveals nothing new: at radius 20
    the whole room is visible the moment you stand in it, which is why radius 20
    and radius 8 differ by so little indoors. The meaningful progression property
    is that crossing into somewhere new reveals more, so that is what this asserts.
    """
    level = generate_level(seed)
    start = new_game(level)
    at_door, key = find_door_approach(level, start)
    if at_door is None:
        pytest.skip(f"seed {seed}: no reachable closed door")

    opened = step(at_door, translate_key(key))
    through = step(opened, translate_key(key))

    assert opened.explored > at_door.explored, "opening the door revealed nothing"
    assert through.explored >= opened.explored
    assert through.explored > start.explored


@pytest.mark.parametrize("seed", (1234, 7, 42))
def test_visible_agrees_with_a_direct_fov_call(seed: int) -> None:
    """game.step must not reimplement visibility — it must delegate to fov."""
    level = generate_level(seed)
    state = new_game(level)
    for key in WALK_KEYS:
        state = step(state, translate_key(key))
        expected = compute_visible(level, state.open_doors, state.player, state.radius)
        assert state.visible == expected


@pytest.mark.parametrize("seed", SEEDS)
def test_every_visible_cell_is_within_radius_and_in_bounds(seed: int) -> None:
    level = generate_level(seed)
    _, history = drive(new_game(level), WALK_KEYS * 2)
    for snap in history:
        px, py = snap.player
        for x, y in snap.visible:
            assert level.in_bounds(x, y)
            assert (x - px) ** 2 + (y - py) ** 2 <= snap.radius**2


@pytest.mark.parametrize("seed", SEEDS)
def test_a_rejected_move_does_not_recompute_visibility(seed: int) -> None:
    """FOV recomputes only on an accepted move or a door opening.

    The starting cell is often the middle of a room with all eight directions
    open, so this walks out to a cell that genuinely has a wall beside it rather
    than skipping.
    """
    level = generate_level(seed)
    start = new_game(level)

    # Breadth-first walk to the first reachable state with a blocked direction.
    deltas = {"h": (-1, 0), "l": (1, 0), "k": (0, -1), "j": (0, 1)}
    seen = {start.player}
    queue = deque([start])
    blocked_state = blocked_key = None
    while queue and blocked_state is None:
        current = queue.popleft()
        for key, (dx, dy) in deltas.items():
            result = try_move(level, current.player, dx, dy, current.open_doors)
            if not result.moved and result.blocked_by_door is None:
                blocked_state, blocked_key = current, key
                break
            if result.moved and result.position not in seen:
                seen.add(result.position)
                queue.append(step(current, translate_key(key)))

    assert blocked_state is not None, f"seed {seed}: no wall anywhere on the map"

    after = step(blocked_state, translate_key(blocked_key))
    assert after is blocked_state or after.visible is blocked_state.visible, (
        "a rejected move recomputed the field of view"
    )
    assert after.turns == blocked_state.turns
    assert after.player == blocked_state.player
    assert after.explored == blocked_state.explored
    assert after.open_doors == blocked_state.open_doors


# --------------------------------------------------------------------------
# Doors: bump-to-open across keys x movement x world x fov x game
# --------------------------------------------------------------------------


def find_door_approach(level: Level, state: GameState):
    """Walk the map for a state standing next to a closed door, and the key
    that bumps into it."""
    deltas = {"h": (-1, 0), "l": (1, 0), "k": (0, -1), "j": (0, 1)}
    seen = {state.player}
    queue = deque([state])
    while queue:
        current = queue.popleft()
        for key, (dx, dy) in deltas.items():
            target = (current.player[0] + dx, current.player[1] + dy)
            if world.is_closed_door(level, current.open_doors, *target):
                return current, key
            if target in seen or not world.is_passable(level, current.open_doors, *target):
                continue
            seen.add(target)
            queue.append(step(current, translate_key(key)))
    return None, None


@pytest.mark.parametrize("seed", SEEDS)
def test_bumping_a_door_opens_it_costs_a_turn_and_reveals_what_is_behind(seed) -> None:
    level = generate_level(seed)
    at_door, key = find_door_approach(level, new_game(level))
    if at_door is None:
        pytest.skip(f"seed {seed}: no reachable closed door")

    after = step(at_door, translate_key(key))

    assert after.player == at_door.player, "opening a door must not move the player"
    assert after.turns == at_door.turns + 1, "opening a door costs exactly one turn"
    assert len(after.open_doors) == len(at_door.open_doors) + 1
    opened = next(iter(after.open_doors - at_door.open_doors))
    assert level.tile_at(*opened) is Tile.DOOR
    assert after.visible > at_door.visible or after.visible != at_door.visible, (
        "opening a door must recompute the field of view"
    )
    assert after.explored >= at_door.explored

    # And the next move in the same direction now walks onto the door cell.
    through = step(after, translate_key(key))
    assert through.player == opened
    assert through.turns == at_door.turns + 2


@pytest.mark.parametrize("seed", SEEDS)
def test_open_doors_are_always_real_door_tiles(seed: int) -> None:
    level = generate_level(seed)
    final, _ = drive(new_game(level), WALK_KEYS * 6)
    for coord in final.open_doors:
        assert level.tile_at(*coord) is Tile.DOOR


# --------------------------------------------------------------------------
# The frame: render x style x fov x game
# --------------------------------------------------------------------------


@pytest.mark.parametrize("seed", (1234, 7, -1))
def test_rendered_frame_matches_visibility_state(seed: int) -> None:
    level = generate_level(seed)
    state = new_game(level)
    _, history = drive(state, WALK_KEYS)

    for snap in history:
        cells = render_to_cells(
            snap.level, snap.player, snap.visible, snap.explored,
            snap.open_doors, format_status(snap),
        )
        assert len(cells) == level.height + 1
        assert all(len(row) == level.width for row in cells)

        px, py = snap.player
        assert cells[py][px].char == PLAYER_CHAR
        assert cells[py][px].role is Role.PLAYER
        assert cells[py][px].visibility is Visibility.VISIBLE

        for y in range(level.height):
            for x in range(level.width):
                if (x, y) == (px, py):
                    continue
                cell = cells[y][x]
                if (x, y) in snap.visible:
                    assert cell.visibility is Visibility.VISIBLE
                elif (x, y) in snap.explored:
                    assert cell.visibility is Visibility.EXPLORED
                else:
                    assert cell.visibility is Visibility.UNSEEN
                    assert cell.char == " ", "unexplored ground leaked a glyph"


@pytest.mark.parametrize("seed", SEEDS)
def test_the_unexplored_map_does_not_leak_through_the_renderer(seed: int) -> None:
    """The player must not be able to infer the map shape from unexplored area."""
    level = generate_level(seed)
    state = new_game(level)
    cells = render_to_cells(
        level, state.player, state.visible, state.explored,
        state.open_doors, format_status(state),
    )
    lines = to_lines(cells)
    for y in range(level.height):
        for x in range(level.width):
            if (x, y) in state.explored or (x, y) == state.player:
                continue
            assert lines[y][x] == " ", f"unexplored cell ({x}, {y}) drew a glyph"


def test_an_opened_door_changes_glyph_in_the_frame() -> None:
    level = generate_level(1234)
    at_door, key = find_door_approach(level, new_game(level))
    assert at_door is not None

    after = step(at_door, translate_key(key))
    door = next(iter(after.open_doors - at_door.open_doors))

    before_cells = render_to_cells(
        level, at_door.player, at_door.visible, at_door.explored,
        at_door.open_doors, "",
    )
    after_cells = render_to_cells(
        level, after.player, after.visible, after.explored, after.open_doors, "",
    )
    assert before_cells[door[1]][door[0]].char == TILE_CHARS[Tile.DOOR]
    assert after_cells[door[1]][door[0]].char == DOOR_OPEN_CHAR
    assert after_cells[door[1]][door[0]].role is Role.DOOR


def test_colours_differ_between_visible_and_explored() -> None:
    """The user's rule: explored area is a darker shade of its natural colour."""
    for role in (Role.TERRAIN, Role.DOOR):
        lit = attr_for(role, Visibility.VISIBLE)
        dim = attr_for(role, Visibility.EXPLORED)
        assert lit != dim
        assert dim.color < lit.color, f"{role} explored is not darker than visible"
    player = attr_for(Role.PLAYER, Visibility.VISIBLE)
    assert player.bold is True, "the player must be bold"
    assert attr_for(Role.TERRAIN, Visibility.VISIBLE).bold is False


def test_every_drawn_cell_of_a_real_frame_has_a_usable_attribute() -> None:
    """No frame the game can produce may contain a cell the palette refuses."""
    level = generate_level(1234)
    state, _ = drive(new_game(level), WALK_KEYS)
    cells = render_to_cells(
        level, state.player, state.visible, state.explored,
        state.open_doors, format_status(state),
    )
    for row in cells:
        for cell in row:
            if cell.visibility is Visibility.UNSEEN:
                assert cell.char == " "
                continue
            attr = attr_for(cell.role, cell.visibility)
            assert isinstance(attr, Attr) and isinstance(attr.color, int)


def test_status_line_tracks_the_walk() -> None:
    level = generate_level(1234)
    state = new_game(level)
    x, y = state.player
    assert format_status(state) == f"Seed: 1234  Pos: ({x}, {y})  Turns: 0  [q] quit"
    state, _ = drive(state, "jjll")
    x, y = state.player
    assert format_status(state) == (
        f"Seed: 1234  Pos: ({x}, {y})  Turns: {state.turns}  [q] quit"
    )


def test_frame_fits_a_classic_terminal_at_the_default_size() -> None:
    level = generate_level(1234)
    state = new_game(level)
    cells = render_to_cells(
        level, state.player, state.visible, state.explored, state.open_doors, "s",
    )
    lines = to_lines(cells)
    assert len(lines) == 23
    assert all(len(line) == 80 for line in lines)


# --------------------------------------------------------------------------
# Determinism and immutability across the whole stack
# --------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_same_seed_produces_an_identical_level(seed: int) -> None:
    assert generate_level(seed) == generate_level(seed)


def test_determinism_survives_a_separate_process() -> None:
    script = (
        "import sys; sys.path.insert(0, %r)\n"
        "from roguelike.generator import generate_level\n"
        "from roguelike.game import new_game\n"
        "from roguelike.render import render_to_cells, to_lines\n"
        "level = generate_level(31337)\n"
        "s = new_game(level)\n"
        "cells = render_to_cells(level, s.player, s.visible, s.explored, s.open_doors, '')\n"
        "print('\\n'.join(to_lines(cells)))\n"
    ) % str(PROJECT_ROOT)

    level = generate_level(31337)
    state = new_game(level)
    expected = "\n".join(
        to_lines(
            render_to_cells(
                level, state.player, state.visible, state.explored, state.open_doors, ""
            )
        )
    )

    for hash_seed in ("0", "1", "424242"):
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True,
            env={"PYTHONHASHSEED": hash_seed, "PATH": "/usr/bin:/bin"},
            cwd=str(PROJECT_ROOT), check=True,
        )
        assert result.stdout.rstrip("\n") == expected, (
            f"frame differed with PYTHONHASHSEED={hash_seed}"
        )


def test_a_full_walk_mutates_nothing() -> None:
    level = generate_level(2026)
    reference = copy.deepcopy(level)
    state = new_game(level)

    for key in WALK_KEYS * 3:
        state = step(state, translate_key(key))
        render_to_cells(
            state.level, state.player, state.visible, state.explored,
            state.open_doors, format_status(state),
        )

    assert level == reference
    assert state.level is level


def test_different_seeds_produce_different_levels() -> None:
    assert len({generate_level(seed).grid for seed in SEEDS}) > 1


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
        "from roguelike import (game, generator, keys, level, movement, render,\n"
        "                       tiles, world, style, fov)\n"
        "assert not hasattr(curses, 'LINES'), 'curses was initialised on import'\n"
        "print('clean')\n"
    ) % str(PROJECT_ROOT)

    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True,
        cwd=str(PROJECT_ROOT), stdin=subprocess.DEVNULL, check=True,
    )
    assert result.stdout.strip() == "clean"
