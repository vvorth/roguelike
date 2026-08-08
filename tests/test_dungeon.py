"""Unit tests for :mod:`roguelike.dungeon` (CONTRACT-v3 §17).

Two pure functions, and between them the entire identity of a dungeon: which level lives
at which depth, and how a level knows where the one above it left off.

The three things these tests exist to pin, in order of how badly a break would hurt:

1. **Determinism across processes.** ``seed_for`` is a plain integer mix precisely so
   that no ``hash()`` and no ``PYTHONHASHSEED`` can reach it. Two subprocesses under
   different hash seeds must agree with each other and with this one.
2. **The descent chain lines up.** Level *N+1* generated with ``required_up`` set to level
   *N*'s down-staircase must put its up-staircase exactly there. That single coordinate is
   what lets the player stay put while the world changes underneath them.
3. **No caching, no state.** Both functions are pure; ``level_for`` regenerates every
   time and the levels come out equal anyway.

Nothing here touches curses or a terminal.
"""

from __future__ import annotations

import ast
import copy
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

from roguelike import dungeon
from roguelike.dungeon import level_for, seed_for
from roguelike.generator import DEFAULT_HEIGHT, DEFAULT_WIDTH, generate_level
from roguelike.level import Level
from roguelike.tiles import Tile

DUNGEON_SOURCE = Path(dungeon.__file__).read_text(encoding="utf-8")
DUNGEON_TREE = ast.parse(DUNGEON_SOURCE)

PROJECT_ROOT = Path(dungeon.__file__).resolve().parent.parent

#: Small but legal map dimensions, so the chain tests stay quick. The generator needs
#: MIN_ROOM_SIZE + 2 == 6 on each axis; 40x18 gives room for several rooms.
SMALL = (40, 18)


def _mix(master_seed: int, depth: int, branch: int = 0) -> int:
    """The CONTRACT-v3 §17 formula, spelled out independently of the implementation."""
    return (
        master_seed * 0x9E3779B1 + depth * 0x85EBCA77 + branch * 0xC2B2AE35
    ) & 0x7FFFFFFF


# ---------------------------------------------------------------------------------------
# seed_for — the mix
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("master_seed", [0, 1, 7, 1234, 2**31, -1, -999])
@pytest.mark.parametrize("depth", [1, 2, 3, 17, 100])
def test_seed_for_matches_the_contract_formula(master_seed: int, depth: int) -> None:
    assert seed_for(master_seed, depth) == _mix(master_seed, depth)


def test_seed_for_exact_literals() -> None:
    # Pinned values, so a "harmless" change to a multiplier cannot slip through a test
    # that only re-derives the same formula.
    assert seed_for(0, 1) == 0x85EBCA77 & 0x7FFFFFFF
    assert seed_for(1234, 1) == _mix(1234, 1)
    assert seed_for(1234, 1) == 760504745
    assert seed_for(1234, 2) == 859843616
    assert seed_for(1234, 2, branch=1) == 1978849877


def test_seed_for_is_deterministic_within_a_process() -> None:
    for depth in range(1, 12):
        assert seed_for(4242, depth) == seed_for(4242, depth)


@pytest.mark.parametrize("master_seed", [0, 1, 1234, -1, -2**40, 2**40])
def test_seed_for_is_always_non_negative(master_seed: int) -> None:
    for depth in (1, 2, 50):
        value = seed_for(master_seed, depth)
        assert isinstance(value, int)
        assert value >= 0
        assert value <= 0x7FFFFFFF


def test_seed_for_differs_for_different_depths() -> None:
    seeds = [seed_for(1234, depth) for depth in range(1, 41)]
    assert len(set(seeds)) == len(seeds)


def test_seed_for_differs_for_different_master_seeds() -> None:
    seeds = [seed_for(master, 3) for master in range(200)]
    assert len(set(seeds)) == len(seeds)


def test_seed_for_branch_scaffolding_changes_the_answer() -> None:
    # req 3: a second down-staircase must eventually lead somewhere genuinely different.
    assert seed_for(1234, 3, branch=1) != seed_for(1234, 3, branch=0)
    assert seed_for(1234, 3, branch=2) != seed_for(1234, 3, branch=1)


def test_seed_for_branch_defaults_to_zero() -> None:
    assert seed_for(1234, 3) == seed_for(1234, 3, 0)
    assert seed_for(1234, 3) == seed_for(1234, 3, branch=0)


def test_seed_for_branch_is_positional_or_keyword() -> None:
    assert seed_for(1234, 3, 1) == seed_for(1234, 3, branch=1)


@pytest.mark.parametrize("depth", [0, -1, -100])
def test_seed_for_rejects_a_non_positive_depth(depth: int) -> None:
    with pytest.raises(ValueError):
        seed_for(1234, depth)


def test_seed_for_result_is_a_legal_generator_seed() -> None:
    # generate_level demands an int that is not a bool (CONTRACT §3.1).
    value = seed_for(-5, 9)
    assert type(value) is int
    assert not isinstance(value, bool)
    generate_level(value, *SMALL)


def test_seed_for_is_stable_across_processes_and_hash_seeds() -> None:
    code = (
        "from roguelike.dungeon import seed_for; "
        "print([seed_for(m, d, b) "
        "for m in (0, 1234, -7) for d in (1, 2, 9) for b in (0, 1)])"
    )
    outputs = []
    for hash_seed in ("0", "1"):
        result = subprocess.run(
            [sys.executable, "-c", code],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            env={"PYTHONHASHSEED": hash_seed, "PATH": "/usr/bin:/bin"},
            check=True,
        )
        outputs.append(result.stdout.strip())

    expected = str(
        [seed_for(m, d, b) for m in (0, 1234, -7) for d in (1, 2, 9) for b in (0, 1)]
    )
    assert outputs[0] == outputs[1] == expected


# ---------------------------------------------------------------------------------------
# level_for — the level at a depth
# ---------------------------------------------------------------------------------------


def test_level_for_returns_a_level_at_the_requested_depth() -> None:
    for depth in (1, 2, 3, 8):
        level = level_for(1234, depth, width=SMALL[0], height=SMALL[1])
        assert isinstance(level, Level)
        assert level.depth == depth


def test_level_for_uses_the_derived_seed_not_the_master_seed() -> None:
    level = level_for(1234, 3, width=SMALL[0], height=SMALL[1])
    assert level.seed == seed_for(1234, 3)
    assert level.seed != 1234


def test_level_for_equals_the_generator_call_the_contract_specifies() -> None:
    expected = generate_level(seed_for(1234, 4), *SMALL, depth=4, required_up=None)
    assert level_for(1234, 4, width=SMALL[0], height=SMALL[1]) == expected


def test_level_for_defaults_to_the_generator_dimensions() -> None:
    level = level_for(99, 1)
    assert (level.width, level.height) == (DEFAULT_WIDTH, DEFAULT_HEIGHT)


def test_level_for_honours_explicit_dimensions() -> None:
    level = level_for(99, 1, width=31, height=17)
    assert (level.width, level.height) == (31, 17)


def test_level_for_is_deterministic_and_uncached() -> None:
    first = level_for(1234, 5, width=SMALL[0], height=SMALL[1])
    second = level_for(1234, 5, width=SMALL[0], height=SMALL[1])
    assert first == second
    assert first is not second, "no caching: each call regenerates"


def test_level_for_different_depths_give_different_levels() -> None:
    grids = {level_for(1234, d, width=SMALL[0], height=SMALL[1]).grid for d in range(1, 8)}
    assert len(grids) == 7


def test_level_for_places_stairs_and_pins_the_spawn_to_the_up_stair() -> None:
    level = level_for(2026, 2, width=SMALL[0], height=SMALL[1])
    assert level.stairs_up is not None
    assert len(level.stairs_down) == 1
    assert level.player_start == level.stairs_up          # G17
    assert level.stairs_up != level.stairs_down[0]        # G16
    assert level.tile_at(*level.stairs_up) is Tile.STAIRS_UP        # G18
    assert level.tile_at(*level.stairs_down[0]) is Tile.STAIRS_DOWN  # G18


def test_level_for_honours_required_up_exactly() -> None:
    above = level_for(1234, 1, width=SMALL[0], height=SMALL[1])
    target = above.stairs_down[0]
    below = level_for(1234, 2, required_up=target, width=SMALL[0], height=SMALL[1])
    assert below.stairs_up == target
    assert below.player_start == target
    assert below.tile_at(*target) is Tile.STAIRS_UP


def test_level_for_required_up_is_the_third_positional_parameter() -> None:
    above = level_for(1234, 1, width=SMALL[0], height=SMALL[1])
    target = above.stairs_down[0]
    positional = level_for(1234, 2, target, SMALL[0], SMALL[1])
    assert positional == level_for(
        1234, 2, required_up=target, width=SMALL[0], height=SMALL[1]
    )


def test_required_up_changes_the_level_it_produces() -> None:
    above = level_for(1234, 1, width=SMALL[0], height=SMALL[1])
    anchored = level_for(
        1234, 2, required_up=above.stairs_down[0], width=SMALL[0], height=SMALL[1]
    )
    free = level_for(1234, 2, width=SMALL[0], height=SMALL[1])
    assert anchored.seed == free.seed, "same depth, same derived seed"
    assert anchored != free, "but the anchor room reshapes the layout"


@pytest.mark.parametrize("depth", [0, -1, -50])
def test_level_for_rejects_a_non_positive_depth(depth: int) -> None:
    with pytest.raises(ValueError):
        level_for(1234, depth)


def test_level_for_rejects_a_non_positive_depth_before_generating() -> None:
    # The guard is this module's, not a ValueError that leaked out of the generator by
    # accident; the generator would also raise, but only after the seed had been derived.
    with pytest.raises(ValueError, match="depth"):
        level_for(1234, 0, width=SMALL[0], height=SMALL[1])


def test_a_coordinate_from_a_differently_sized_map_is_rejected_not_silently_moved() -> None:
    # T15's warning: every honest descent coordinate is anchorable on a map of the SAME
    # dimensions, so this can only happen if the dimensions change between depths. It must
    # be loud when it does.
    wide = level_for(1, 1, width=78, height=SMALL[1])
    target = wide.stairs_down[0]
    assert target == (56, 9), "pinned so the test really exercises the out-of-range case"
    assert target[0] > 40 - 3
    with pytest.raises(ValueError, match="anchorable"):
        level_for(1, 2, required_up=target, width=40, height=SMALL[1])


def test_level_for_propagates_generator_type_errors() -> None:
    with pytest.raises(TypeError):
        level_for(1234, 2, required_up=[5, 5])  # type: ignore[arg-type]


def test_level_for_propagates_dimension_errors() -> None:
    with pytest.raises(ValueError):
        level_for(1234, 1, width=4, height=4)


def test_level_for_does_not_mutate_the_coordinate_it_is_given() -> None:
    above = level_for(1234, 1, width=SMALL[0], height=SMALL[1])
    target = above.stairs_down[0]
    snapshot = copy.deepcopy(target)
    level_for(1234, 2, required_up=target, width=SMALL[0], height=SMALL[1])
    assert target == snapshot
    assert above == copy.deepcopy(above)


# ---------------------------------------------------------------------------------------
# The descent chain — the point of the module
# ---------------------------------------------------------------------------------------


def build_chain(master_seed: int, depths: int, size: tuple[int, int] = SMALL) -> list[Level]:
    """Build the first ``depths`` levels of a dungeon exactly as ``game.step`` does."""
    width, height = size
    levels = [level_for(master_seed, 1, width=width, height=height)]
    for depth in range(2, depths + 1):
        levels.append(
            level_for(
                master_seed,
                depth,
                required_up=levels[-1].stairs_down[0],
                width=width,
                height=height,
            )
        )
    return levels


@pytest.mark.parametrize("master_seed", [1, 7, 1234, 31337])
def test_a_five_level_chain_lines_up_at_every_join(master_seed: int) -> None:
    chain = build_chain(master_seed, 5)
    for above, below in zip(chain, chain[1:]):
        assert below.stairs_up == above.stairs_down[0]
        assert below.player_start == above.stairs_down[0]
        assert below.tile_at(*below.stairs_up) is Tile.STAIRS_UP
        assert above.tile_at(*above.stairs_down[0]) is Tile.STAIRS_DOWN


def test_a_chain_has_the_right_depths_and_distinct_seeds() -> None:
    chain = build_chain(1234, 6)
    assert [level.depth for level in chain] == [1, 2, 3, 4, 5, 6]
    assert len({level.seed for level in chain}) == 6


def test_the_same_master_seed_builds_the_identical_chain_twice() -> None:
    assert build_chain(1234, 4) == build_chain(1234, 4)


def test_different_master_seeds_build_different_chains() -> None:
    assert build_chain(1234, 3) != build_chain(1235, 3)


def test_a_chain_regenerated_from_the_master_seed_alone_is_reproducible() -> None:
    # The whole run is replayable from the number on the status line, which is why
    # format_status_right shows the master seed and not the derived one.
    original = build_chain(4242, 4)
    assert build_chain(4242, 4) == original


def test_every_level_in_a_chain_is_fully_connected_from_its_up_stair() -> None:
    for level in build_chain(1234, 4):
        reached = {level.stairs_up}
        stack = [level.stairs_up]
        while stack:
            x, y = stack.pop()
            for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                cell = (x + dx, y + dy)
                if cell not in reached and level.is_walkable(*cell):
                    reached.add(cell)
                    stack.append(cell)
        walkable = {
            (x, y)
            for y in range(level.height)
            for x in range(level.width)
            if level.is_walkable(x, y)
        }
        assert reached == walkable
        assert level.stairs_down[0] in reached


def test_a_deep_chain_never_trips_the_anchorable_range() -> None:
    # G13 makes every down-staircase an open spot, and an open spot always satisfies
    # 2 <= x <= width - 3 — so an honest chain can descend indefinitely.
    for level in build_chain(31337, 12):
        x, y = level.stairs_down[0]
        assert 2 <= x <= level.width - 3
        assert 2 <= y <= level.height - 3


# ---------------------------------------------------------------------------------------
# Module hygiene — asserted by reading the source (CONTRACT-v3 §10, §17)
# ---------------------------------------------------------------------------------------


def test_public_surface_is_exactly_the_contract_surface() -> None:
    assert dungeon.__all__ == ["seed_for", "level_for"]
    for name in dungeon.__all__:
        assert callable(getattr(dungeon, name))


def test_signatures_match_the_contract() -> None:
    assert (
        str(inspect.signature(seed_for))
        == "(master_seed: 'int', depth: 'int', branch: 'int' = 0) -> 'int'"
    )
    assert str(inspect.signature(level_for)) == (
        "(master_seed: 'int', depth: 'int', "
        "required_up: 'tuple[int, int] | None' = None, "
        "width: 'int' = 80, height: 'int' = 22) -> 'Level'"
    )


def test_import_set_matches_the_contract_import_graph() -> None:
    imported: set[str] = set()
    for node in ast.walk(DUNGEON_TREE):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports"
            imported.add(node.module or "")

    roguelike_imports = {m for m in imported if m.startswith("roguelike")}
    assert roguelike_imports == {"roguelike.generator", "roguelike.level"}
    assert imported - roguelike_imports <= {"__future__"}


@pytest.mark.parametrize(
    "forbidden",
    ["curses", "random", "time", "os", "hashlib", "uuid", "roguelike.game"],
)
def test_forbidden_imports_are_absent(forbidden: str) -> None:
    for node in ast.walk(DUNGEON_TREE):
        if isinstance(node, ast.Import):
            assert all(alias.name != forbidden for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module != forbidden


def test_module_has_no_mutable_module_level_state() -> None:
    # No cache, no registry, no memo dict (CONTRACT-v3 §17: "no caching").
    for name, value in vars(dungeon).items():
        if name.startswith("__"):
            continue
        assert not isinstance(value, (dict, list, set))


def test_module_has_future_annotations_import() -> None:
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in DUNGEON_TREE.body
    )


def test_no_third_party_imports() -> None:
    allowed = {"__future__", "roguelike"}
    for node in ast.walk(DUNGEON_TREE):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in allowed
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] in allowed


def test_no_out_of_scope_surface_was_added() -> None:
    # dungeon.py is seed derivation and nothing else: no player, no store, no branching
    # generation, no persistence to disk.
    for name in (
        "Dungeon",
        "descend",
        "ascend",
        "save",
        "load",
        "cache",
        "branch_for",
        "levels",
    ):
        assert not hasattr(dungeon, name)


def test_importing_the_module_does_not_initialise_a_terminal() -> None:
    code = (
        "import curses, roguelike.dungeon; "
        "print(hasattr(curses, 'LINES'), hasattr(curses, 'COLS'))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        check=True,
    )
    assert result.stdout.strip() == "False False"
