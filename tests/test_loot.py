"""Unit tests for :mod:`roguelike.loot` (CONTRACT-v6 §27, task T34).

Three things get the heaviest coverage here, because each is a way this module could
quietly be wrong without ever raising:

* **The depth/grade table (§27.1) is a table, not a curve** — every row is asserted
  literally, field by field, so a future retune of one row shows up as a loud diff rather
  than a silent drift.
* **Placement never lands in a doorway or inside the safe radius**, over many seeds and
  several generated levels — the same trap ``spawn_npcs`` is tested against in
  ``test_npc.py``, and for the same reason (CONTRACT-v5 §24.4's "never a doorway").
* **Termination is structural.** On a map with no legal cell, ``place_chest`` must return
  ``None`` promptly rather than spin — asserted with a wall-clock guard, not just a
  correctness check.

Nothing here initialises curses, and nothing needs a TTY.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import inspect
import os
import pathlib
import random
import subprocess
import sys
import time

import pytest

from roguelike import loot as loot_module
from roguelike.generator import generate_level
from roguelike.items import (
    BANDAGE,
    BUCKLER,
    CLUB,
    DAGGER,
    KITE_SHIELD,
    LONGBOW,
    POTION_OF_HEALING,
    SHORTBOW,
    SLING,
    SWORD,
    TOWER_SHIELD,
    Shield,
    Weapon,
)
from roguelike.level import Level, freeze_grid
from roguelike.loot import (
    CHEST_CHANCE,
    CHEST_CHANCE_DEEP,
    CHEST_SAFE_RADIUS,
    DEEP_FROM,
    Chest,
    chest_chance,
    grade_weights,
    place_chest,
)
from roguelike.tiles import Tile

ALL_ITEM_CONSTANTS = frozenset(
    {
        CLUB,
        DAGGER,
        SWORD,
        SLING,
        SHORTBOW,
        LONGBOW,
        BUCKLER,
        KITE_SHIELD,
        TOWER_SHIELD,
        POTION_OF_HEALING,
        BANDAGE,
    }
)

CHAR_TO_TILE = {
    "#": Tile.WALL,
    ".": Tile.FLOOR,
    "+": Tile.DOOR,
    "<": Tile.STAIRS_UP,
    ">": Tile.STAIRS_DOWN,
}


def make_level(
    rows: list[str], player_start: tuple[int, int] = (1, 1), seed: int = 0
) -> Level:
    """Build a ``Level`` from character rows, with an empty ``rooms`` tuple.

    Used only for maps with no doors at all: ``place_chest``'s door detection leans on
    ``Level.rooms`` (see ``loot.py``'s module docstring), which a hand-drawn, room-less
    level cannot supply honestly. Every map built here is either entirely open or entirely
    walled, so that distinction never matters.
    """
    assert len({len(row) for row in rows}) == 1, "all rows must be the same width"
    grid = [[CHAR_TO_TILE[c] for c in row] for row in rows]
    return Level(len(grid[0]), len(grid), freeze_grid(grid), (), player_start, seed)


def chebyshev(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_public_surface_is_exactly_the_contract_surface():
    assert set(loot_module.__all__) == {
        "Chest",
        "CHEST_CHANCE",
        "CHEST_CHANCE_DEEP",
        "DEEP_FROM",
        "CHEST_SAFE_RADIUS",
        "chest_chance",
        "grade_weights",
        "place_chest",
    }


def test_signatures_match_the_contract():
    assert list(inspect.signature(chest_chance).parameters) == ["depth"]
    assert list(inspect.signature(grade_weights).parameters) == ["depth"]
    assert list(inspect.signature(place_chest).parameters) == ["rng", "level", "depth"]


# ---------------------------------------------------------------------------
# Chest
# ---------------------------------------------------------------------------


def test_chest_is_frozen():
    assert dataclasses.is_dataclass(Chest)
    assert Chest.__dataclass_params__.frozen


def test_chest_rejects_assignment():
    chest = Chest(position=(1, 1), contents=(CLUB,))
    with pytest.raises(dataclasses.FrozenInstanceError):
        chest.opened = True


def test_chest_defaults_opened_to_false():
    chest = Chest(position=(1, 1), contents=(CLUB,))
    assert chest.opened is False


def test_chest_fields():
    chest = Chest(position=(3, 4), contents=(CLUB, DAGGER), opened=True)
    assert chest.position == (3, 4)
    assert chest.contents == (CLUB, DAGGER)
    assert chest.opened is True


# ---------------------------------------------------------------------------
# chest_chance — CONTRACT-v6 §11 v6
# ---------------------------------------------------------------------------


def test_chest_chance_constants():
    assert CHEST_CHANCE == 12
    assert CHEST_CHANCE_DEEP == 8
    assert DEEP_FROM == 10


@pytest.mark.parametrize("depth", range(1, 10))
def test_chest_chance_is_twelve_below_deep_from(depth):
    assert chest_chance(depth) == 12


@pytest.mark.parametrize("depth", [10, 11, 15, 30, 100])
def test_chest_chance_is_eight_at_or_above_deep_from(depth):
    assert chest_chance(depth) == 8


def test_chest_chance_never_raises_for_any_depth_at_least_one():
    for depth in range(1, 500):
        chest_chance(depth)  # must not raise


# ---------------------------------------------------------------------------
# grade_weights — CONTRACT-v6 §27.1, asserted literally
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_grade_weights_depth_1_to_3(depth):
    assert grade_weights(depth) == (80, 19, 1)


@pytest.mark.parametrize("depth", [4, 5, 6])
def test_grade_weights_depth_4_to_6(depth):
    assert grade_weights(depth) == (55, 40, 5)


@pytest.mark.parametrize("depth", [7, 8, 9])
def test_grade_weights_depth_7_to_9(depth):
    assert grade_weights(depth) == (30, 60, 10)


@pytest.mark.parametrize("depth", [10, 11, 20, 100])
def test_grade_weights_depth_10_plus(depth):
    assert grade_weights(depth) == (15, 70, 15)


def test_every_row_sums_to_one_hundred():
    for depth in range(1, 40):
        assert sum(grade_weights(depth)) == 100


def test_fine_weight_is_one_percent_at_depth_one():
    # The "extremely low chance of a higher grade" requirement, asserted as such.
    assert grade_weights(1)[2] == 1


def test_fine_weight_is_monotonically_non_decreasing_with_depth():
    fine_weights = [grade_weights(depth)[2] for depth in range(1, 40)]
    assert fine_weights == sorted(fine_weights)


def test_grade_weights_never_raises_for_any_depth_at_least_one():
    for depth in range(1, 500):
        grade_weights(depth)  # must not raise


# ---------------------------------------------------------------------------
# place_chest — placement rules, over many seeds and several generated levels
# ---------------------------------------------------------------------------

SWEEP_LEVELS = [(80, 22), (60, 30), (100, 25)]


def _sweep_chests():
    """(level, depth, chest) for 150 seeds on the default size, 20 each on two others."""
    for seed in range(150):
        depth = 1 + (seed % 20)
        level = generate_level(seed, depth=depth)
        chest = place_chest(random.Random(seed * 7 + 1), level, depth)
        yield level, depth, chest
    for width, height in SWEEP_LEVELS[1:]:
        for seed in range(200, 220):
            level = generate_level(seed, width=width, height=height)
            chest = place_chest(random.Random(seed), level, level.depth)
            yield level, level.depth, chest


SWEEP = None


def sweep():
    global SWEEP
    if SWEEP is None:
        SWEEP = list(_sweep_chests())
    return SWEEP


def test_the_sweep_covers_many_seeds_several_levels_and_produces_some_chests():
    runs = sweep()
    assert len(runs) >= 190
    assert len({(level.width, level.height) for level, _, _ in runs}) >= 3
    found = [chest for _, _, chest in runs if chest is not None]
    assert len(found) >= 5, "the sweep should place at least a handful of chests"


def test_every_placed_chest_sits_on_a_passable_non_door_cell():
    for level, _, chest in sweep():
        if chest is None:
            continue
        x, y = chest.position
        assert level.is_walkable(x, y), (level.seed, chest.position)
        assert level.tile_at(x, y) is not Tile.DOOR, (level.seed, chest.position)


def test_every_placed_chest_is_at_least_the_safe_radius_from_player_start():
    for level, _, chest in sweep():
        if chest is None:
            continue
        distance = chebyshev(chest.position, level.player_start)
        assert distance >= CHEST_SAFE_RADIUS, (level.seed, chest.position, distance)


def test_every_placed_chest_has_one_to_three_items():
    for _, _, chest in sweep():
        if chest is None:
            continue
        assert 1 <= len(chest.contents) <= 3


def test_every_item_in_every_chest_is_one_of_items_pys_constants():
    for _, _, chest in sweep():
        if chest is None:
            continue
        for item in chest.contents:
            assert item in ALL_ITEM_CONSTANTS, item


# ---------------------------------------------------------------------------
# Grade distribution — generous bands, per CONTRACT-v6 §27.1
# ---------------------------------------------------------------------------


def _graded_contents_at(depth: int, seeds: range):
    """Every weapon/shield drawn across ``seeds`` at ``depth``, tagged by its own grade.

    Only weapons and shields carry a ``Grade`` (consumables do not -- see loot.py's
    "Consumables and grade"), and every grade's pool holds the consumables in equal
    measure, so restricting to graded items is what isolates the underlying grade roll.
    """
    level = generate_level(1, width=30, height=18, max_rooms=5, depth=depth)
    grades = []
    for seed in seeds:
        chest = place_chest(random.Random(seed), level, depth)
        if chest is None:
            continue
        for item in chest.contents:
            if isinstance(item, (Weapon, Shield)):
                grades.append(item.grade)
    return grades


@pytest.mark.parametrize("depth", [1, 5, 12])
def test_grade_distribution_is_roughly_the_table_at_depths_1_5_12(depth):
    from roguelike.items import Grade

    grades = _graded_contents_at(depth, range(6000))
    assert len(grades) >= 80, "sample too small to say anything -- widen the sweep"

    expected = grade_weights(depth)
    total = len(grades)
    observed = {
        Grade.CRUDE: grades.count(Grade.CRUDE) * 100 // total,
        Grade.STANDARD: grades.count(Grade.STANDARD) * 100 // total,
        Grade.FINE: grades.count(Grade.FINE) * 100 // total,
    }
    # Generous bands -- this is not a statistical precision test, just "roughly".
    band = 18
    assert abs(observed[Grade.CRUDE] - expected[0]) <= band, (depth, observed, expected)
    assert abs(observed[Grade.STANDARD] - expected[1]) <= band, (depth, observed, expected)
    assert abs(observed[Grade.FINE] - expected[2]) <= band, (depth, observed, expected)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_same_seed_and_level_gives_an_identical_chest():
    level = generate_level(7)
    found_any = False
    for seed in range(300):
        first = place_chest(random.Random(seed), level, 1)
        second = place_chest(random.Random(seed), level, 1)
        assert first == second, seed
        if first is not None:
            found_any = True
    assert found_any, "no chest ever placed across 300 seeds -- widen the sweep"


def test_different_seeds_generally_produce_different_chests():
    level = generate_level(7)
    outcomes = {place_chest(random.Random(seed), level, 1) for seed in range(200)}
    assert len(outcomes) > 1


def test_place_chest_is_deterministic_across_pythonhashseed():
    """A set/dict-iteration-order dependency inside place_chest must fail this."""
    project_root = pathlib.Path(__file__).resolve().parent.parent
    script = (
        "import random\n"
        "from roguelike.generator import generate_level\n"
        "from roguelike.loot import place_chest\n"
        "for seed in range(30):\n"
        "    for depth in (1, 5, 12):\n"
        "        level = generate_level(seed, depth=depth)\n"
        "        chest = place_chest(random.Random(seed * 13 + depth), level, depth)\n"
        "        print(depth, seed, chest)\n"
    )
    outputs = []
    for hash_seed in ("0", "1234"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = hash_seed
        env["PYTHONPATH"] = str(project_root)
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(project_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout.strip())
    assert outputs[0] == outputs[1]
    assert "Chest(" in outputs[0]


# ---------------------------------------------------------------------------
# No legal cell -> None, promptly, never raising
# ---------------------------------------------------------------------------


def test_no_legal_cell_on_a_tiny_generated_level_returns_none_promptly():
    # 6x6 is the generator's floor; its longest Chebyshev span is well under the radius.
    level = generate_level(3, width=6, height=6)
    started = time.perf_counter()
    results = [place_chest(random.Random(seed), level, 1) for seed in range(200)]
    elapsed = time.perf_counter() - started
    assert all(chest is None for chest in results)
    assert elapsed < 1.0, f"200 draws on a 6x6 level took {elapsed:.3f}s"


def test_no_legal_cell_on_a_single_cell_level_returns_none():
    level = make_level(["."], player_start=(0, 0))
    started = time.perf_counter()
    assert place_chest(random.Random(0), level, 1) is None
    assert time.perf_counter() - started < 1.0


def test_no_legal_cell_on_an_all_wall_level_returns_none_and_never_hangs():
    level = make_level(["####", "####"], player_start=(0, 0))
    started = time.perf_counter()
    results = [place_chest(random.Random(seed), level, 1) for seed in range(50)]
    elapsed = time.perf_counter() - started
    assert all(chest is None for chest in results)
    assert elapsed < 1.0, f"50 draws on an all-wall level took {elapsed:.3f}s"


def test_the_safe_radius_is_never_relaxed_to_force_a_placement():
    # A small open room where every cell is inside the safe radius of player_start.
    level = make_level(["." * 10 for _ in range(10)], player_start=(5, 5))
    for seed in range(100):
        chest = place_chest(random.Random(seed), level, 1)
        assert chest is None, (seed, chest)


# ---------------------------------------------------------------------------
# Purity
# ---------------------------------------------------------------------------


def test_place_chest_mutates_no_argument():
    level = generate_level(9)
    before = copy.deepcopy(level)
    place_chest(random.Random(9), level, 1)
    assert level == before


def test_repeated_calls_do_not_cache_or_drift():
    level = generate_level(12)
    results = [place_chest(random.Random(3), level, 1) for _ in range(10)]
    assert len(set(results)) == 1


# ---------------------------------------------------------------------------
# Import hygiene and integer arithmetic (CONTRACT-v6 §10 v6)
# ---------------------------------------------------------------------------


def _module_path() -> pathlib.Path:
    return pathlib.Path(loot_module.__file__)


def _module_imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                names.add(node.module)
    return names


def test_loot_imports_only_items_and_level():
    tree = ast.parse(_module_path().read_text())
    imports = _module_imports(tree)
    project = {name for name in imports if name.split(".")[0] == "roguelike"}
    assert project == {"roguelike.items", "roguelike.level"}, sorted(project)


def test_loot_does_not_import_the_forbidden_modules():
    tree = ast.parse(_module_path().read_text())
    imports = _module_imports(tree)
    for forbidden in (
        "roguelike.game",
        "roguelike.npc",
        "roguelike.combat",
        "roguelike.render",
        "roguelike.world",
        "roguelike.tiles",
        "game",
        "npc",
        "combat",
        "render",
        "curses",
    ):
        assert forbidden not in imports, forbidden


def test_loot_creates_no_random_instance_of_its_own():
    """CONTRACT-v5 §0.12: randomness is derived by the caller and passed in."""
    source = _module_path().read_text()
    assert "random.Random(" not in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            assert name not in {"seed", "getrandbits"}, name


def test_loot_contains_no_float_literals():
    tree = ast.parse(_module_path().read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            pytest.fail(f"float literal found in loot.py: {node.value!r}")


def test_loot_contains_no_true_division():
    tree = ast.parse(_module_path().read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            pytest.fail("true division found in loot.py")


def test_no_conftest_in_tests_directory():
    assert not (pathlib.Path(__file__).parent / "conftest.py").exists()
