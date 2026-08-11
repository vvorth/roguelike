"""Unit tests for :mod:`roguelike.status` (CONTRACT-v5 §22, task T22).

A leaf module: no engine, no level, no terminal involved.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

from roguelike.status import REGEN_TURNS, StatusEffect, StatusKind, apply_effect, tick_effects

POISON = StatusKind.POISONED


def effect(remaining: int, magnitude: int = 2, kind: StatusKind = POISON) -> StatusEffect:
    return StatusEffect(kind=kind, remaining_turns=remaining, magnitude=magnitude)


# ---------------------------------------------------------------------------
# REGEN_TURNS
# ---------------------------------------------------------------------------


def test_regen_turns_is_10():
    assert REGEN_TURNS == 10


# ---------------------------------------------------------------------------
# apply_effect — refresh, never stack (CONTRACT-v5 §22.1)
# ---------------------------------------------------------------------------


def test_apply_effect_appends_to_empty_tuple():
    new = effect(5)
    result = apply_effect((), new)
    assert result == (new,)


def test_apply_effect_longer_duration_replaces_shorter():
    existing = effect(3, magnitude=1)
    new = effect(8, magnitude=1)
    result = apply_effect((existing,), new)
    assert result == (new,)


def test_apply_effect_shorter_duration_leaves_tuple_unchanged():
    existing = effect(8, magnitude=1)
    new = effect(3, magnitude=99)
    result = apply_effect((existing,), new)
    assert result == (existing,)


def test_apply_effect_equal_duration_leaves_tuple_unchanged():
    existing = effect(5, magnitude=1)
    new = effect(5, magnitude=99)
    result = apply_effect((existing,), new)
    assert result == (existing,)


def test_apply_effect_result_never_has_two_entries_of_same_kind():
    existing = effect(3)
    new = effect(9)
    result = apply_effect((existing,), new)
    kinds = [e.kind for e in result]
    assert len(kinds) == len(set(kinds))
    assert result == (new,)


def test_apply_effect_does_not_mutate_input_tuple():
    existing = effect(3)
    original = (existing,)
    new = effect(9)
    apply_effect(original, new)
    assert original == (existing,)
    assert len(original) == 1


def test_apply_effect_does_not_mutate_input_tuple_on_no_op_path():
    existing = effect(8)
    original = (existing,)
    new = effect(2)
    result = apply_effect(original, new)
    assert original == (existing,)
    assert result is original or result == original


# ---------------------------------------------------------------------------
# tick_effects (CONTRACT-v5 §22.2)
# ---------------------------------------------------------------------------


def test_tick_effects_on_empty_tuple():
    assert tick_effects(()) == ((), 0)


def test_tick_effects_damage_equals_sum_of_magnitudes():
    _, damage = tick_effects((effect(5, magnitude=2),))
    assert damage == 2

    # tick_effects itself does not deduplicate by kind, only sums and decrements — that is
    # apply_effect's job — so two entries of the same kind both contribute here.
    _, damage = tick_effects((effect(5, magnitude=2), effect(5, magnitude=7)))
    assert damage == 9


def test_tick_effects_reduces_remaining_turns_by_exactly_1():
    surviving, _ = tick_effects((effect(5),))
    assert surviving == (effect(4),)


def test_tick_effects_drops_effect_reaching_zero_but_still_counts_its_damage():
    surviving, damage = tick_effects((effect(1, magnitude=7),))
    assert surviving == ()
    assert damage == 7


def test_tick_effects_preserves_order_with_two_effects():
    a = StatusEffect(kind=POISON, remaining_turns=3, magnitude=1)
    b = StatusEffect(kind=POISON, remaining_turns=5, magnitude=2)
    surviving, damage = tick_effects((a, b))
    assert surviving == (
        StatusEffect(kind=POISON, remaining_turns=2, magnitude=1),
        StatusEffect(kind=POISON, remaining_turns=4, magnitude=2),
    )
    assert damage == 3


def test_tick_effects_mixed_survival_preserves_relative_order():
    dying = effect(1, magnitude=4)
    surviving_one = effect(6, magnitude=1)
    result, damage = tick_effects((dying, surviving_one))
    assert result == (effect(5, magnitude=1),)
    assert damage == 5


def test_tick_effects_does_not_mutate_input_tuple():
    a = effect(2, magnitude=1)
    original = (a,)
    tick_effects(original)
    assert original == (a,)


# ---------------------------------------------------------------------------
# StatusKind
# ---------------------------------------------------------------------------


def test_status_kind_has_exactly_poisoned():
    assert {member.name for member in StatusKind} == {"POISONED"}


# ---------------------------------------------------------------------------
# Frozen dataclass
# ---------------------------------------------------------------------------


def test_status_effect_is_frozen():
    e = effect(5)
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.remaining_turns = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Import hygiene (CONTRACT-v5 §10/§22): status.py imports nothing from the project
# ---------------------------------------------------------------------------


def _module_source_and_tree() -> tuple[str, ast.Module]:
    path = pathlib.Path(__file__).resolve().parent.parent / "roguelike" / "status.py"
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


def test_status_imports_nothing_from_the_project_and_no_curses():
    _, tree = _module_source_and_tree()
    imports = _module_imports(tree)
    assert not any(name == "roguelike" or name.startswith("roguelike.") for name in imports)
    assert "curses" not in imports


def test_status_does_not_import_curses_module_directly():
    source, _ = _module_source_and_tree()
    assert "import curses" not in source


def test_status_contains_no_float_literals():
    _, tree = _module_source_and_tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            pytest.fail(f"float literal found in status.py: {node.value!r}")
