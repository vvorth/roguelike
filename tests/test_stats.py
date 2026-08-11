"""Unit tests for :mod:`roguelike.stats` (CONTRACT-v5 §20, task T22).

A leaf module: these tests build ``Stats`` values directly, with no engine, no level, and no
terminal involved.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

from roguelike.stats import BASELINE, Actor, Derived, Stats, derive
from roguelike.status import StatusEffect, StatusKind

# ---------------------------------------------------------------------------
# Baseline and formulas
# ---------------------------------------------------------------------------


def test_baseline_is_10():
    assert BASELINE == 10


def test_derive_at_baseline_matches_all_four_fields_exactly():
    result = derive(Stats(10, 10, 10))
    assert result == Derived(max_hp=45, speed=100, evasion=5, block=0)
    assert result.max_hp == 45
    assert result.speed == 100
    assert result.evasion == 5
    assert result.block == 0


def test_max_hp_formula():
    assert derive(Stats(10, 10, 0)).max_hp == 5
    assert derive(Stats(10, 10, 3)).max_hp == 17
    assert derive(Stats(10, 10, 10)).max_hp == 45


def test_speed_formula():
    assert derive(Stats(10, 10, 10)).speed == 100
    assert derive(Stats(10, 14, 10)).speed == 140
    assert derive(Stats(10, 8, 10)).speed == 80


def test_evasion_clamps_at_the_low_end_not_negative():
    assert derive(Stats(10, 8, 10)).evasion == 0


def test_evasion_clamps_at_the_high_end_at_60():
    assert derive(Stats(10, 1000, 10)).evasion == 60
    assert derive(Stats(10, 100, 10)).evasion == 60


def test_evasion_formula_unclamped_case():
    assert derive(Stats(10, 14, 10)).evasion == 17


# ---------------------------------------------------------------------------
# block: deliberately zero at and below baseline — see CONTRACT-v5 §20.1
# ---------------------------------------------------------------------------


def test_block_is_zero_at_baseline_and_stays_zero_below_it_do_not_fix_this():
    """`block` must be 0 for str_ <= 10.

    An earlier draft used ``str_ // 2``, giving a baseline block of 5 against attacks of
    1-5 damage, which floored EVERY attack in the game to exactly 1 and made all four
    monster species mechanically identical. If this test starts failing because someone
    "fixed" block back to a positive baseline value, that is the bug coming back — revert
    the change, do not update this test.
    """
    assert derive(Stats(4, 10, 10)).block == 0
    assert derive(Stats(10, 10, 10)).block == 0


def test_block_above_baseline_is_positive_and_floors_toward_negative_infinity():
    assert derive(Stats(12, 10, 10)).block == 1
    assert derive(Stats(16, 10, 10)).block == 3


def test_block_never_negative_for_very_low_str():
    assert derive(Stats(-100, 10, 10)).block == 0
    assert derive(Stats(5, 10, 10)).block == 0  # (5-10)//2 == -3, not -2


# ---------------------------------------------------------------------------
# The four bestiary stat blocks — no special cases
# ---------------------------------------------------------------------------


def test_bestiary_rat():
    assert derive(Stats(4, 14, 3)) == Derived(max_hp=17, speed=140, evasion=17, block=0)


def test_bestiary_jackal():
    assert derive(Stats(8, 13, 5)) == Derived(max_hp=25, speed=130, evasion=14, block=0)


def test_bestiary_giant_bat():
    assert derive(Stats(3, 18, 2)) == Derived(max_hp=13, speed=180, evasion=29, block=0)


def test_bestiary_cave_snake():
    assert derive(Stats(6, 8, 5)) == Derived(max_hp=25, speed=80, evasion=0, block=0)


# ---------------------------------------------------------------------------
# derive is total: never raises
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stats",
    [
        Stats(0, 0, 0),
        Stats(-10, -10, -10),
        Stats(-1000, -1000, -1000),
        Stats(10_000, 10_000, 10_000),
        Stats(-5, 25, 0),
    ],
)
def test_derive_never_raises(stats: Stats):
    result = derive(stats)
    assert isinstance(result, Derived)


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


def test_stats_is_frozen():
    s = Stats(10, 10, 10)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.str_ = 99  # type: ignore[misc]


def test_derived_is_frozen():
    d = derive(Stats(10, 10, 10))
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.max_hp = 99  # type: ignore[misc]


def test_actor_is_frozen():
    a = Actor(stats=Stats(10, 10, 10), hp=45)
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.hp = 1  # type: ignore[misc]


def test_actor_defaults_status_effects_to_empty_tuple():
    a = Actor(stats=Stats(10, 10, 10), hp=45)
    assert a.status_effects == ()


def test_actor_accepts_status_effects():
    effect = StatusEffect(kind=StatusKind.POISONED, remaining_turns=3, magnitude=2)
    a = Actor(stats=Stats(10, 10, 10), hp=45, status_effects=(effect,))
    assert a.status_effects == (effect,)


# ---------------------------------------------------------------------------
# Import hygiene (CONTRACT-v5 §10): stats.py imports only roguelike.status
# ---------------------------------------------------------------------------


def _module_source_and_tree() -> tuple[str, ast.Module]:
    path = pathlib.Path(__file__).resolve().parent.parent / "roguelike" / "stats.py"
    source = path.read_text()
    return source, ast.parse(source)


def _module_imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                names.add(node.module)
    return names


def test_stats_imports_only_roguelike_status_and_no_curses():
    _, tree = _module_source_and_tree()
    imports = _module_imports(tree)
    project_imports = {name for name in imports if name == "roguelike" or name.startswith("roguelike.")}
    assert project_imports == {"roguelike.status"}
    assert "curses" not in imports


def test_stats_does_not_import_curses_module_directly():
    source, _ = _module_source_and_tree()
    assert "import curses" not in source


def test_stats_contains_no_float_literals():
    _, tree = _module_source_and_tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            pytest.fail(f"float literal found in stats.py: {node.value!r}")
