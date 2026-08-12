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
REGEN = StatusKind.REGENERATING
ENRAGED = StatusKind.ENRAGED


def effect(remaining: int, magnitude: int = 2, kind: StatusKind = POISON) -> StatusEffect:
    return StatusEffect(kind=kind, remaining_turns=remaining, magnitude=magnitude)


# ---------------------------------------------------------------------------
# REGEN_TURNS
# ---------------------------------------------------------------------------


def test_regen_turns_is_3():
    # Corrected from 10: the research that chose 10 simulated monsters at half
    # their real HP, and at 10 the game clears 2.2% of floors instead of ~62%.
    assert REGEN_TURNS == 3


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
# tick_effects (CONTRACT-v5 §22.2, CONTRACT-v6 §25.1)
#
# New in T31: tick_effects returns a 3-tuple, (surviving, total_damage, total_healing),
# not the v5 2-tuple. POISONED contributes to total_damage, REGENERATING to
# total_healing, and the two are reported separately rather than netted — see
# test_poison_and_regeneration_coexist_and_are_distinguishable below for why.
# ---------------------------------------------------------------------------


def test_tick_effects_on_empty_tuple():
    assert tick_effects(()) == ((), 0, 0)


def test_tick_effects_damage_equals_sum_of_magnitudes():
    _, damage, healing = tick_effects((effect(5, magnitude=2),))
    assert damage == 2
    assert healing == 0

    # tick_effects itself does not deduplicate by kind, only sums and decrements — that is
    # apply_effect's job — so two entries of the same kind both contribute here.
    _, damage, _ = tick_effects((effect(5, magnitude=2), effect(5, magnitude=7)))
    assert damage == 9


def test_tick_effects_reduces_remaining_turns_by_exactly_1():
    surviving, _, _ = tick_effects((effect(5),))
    assert surviving == (effect(4),)


def test_tick_effects_drops_effect_reaching_zero_but_still_counts_its_damage():
    surviving, damage, _ = tick_effects((effect(1, magnitude=7),))
    assert surviving == ()
    assert damage == 7


def test_tick_effects_preserves_order_with_two_effects():
    a = StatusEffect(kind=POISON, remaining_turns=3, magnitude=1)
    b = StatusEffect(kind=POISON, remaining_turns=5, magnitude=2)
    surviving, damage, healing = tick_effects((a, b))
    assert surviving == (
        StatusEffect(kind=POISON, remaining_turns=2, magnitude=1),
        StatusEffect(kind=POISON, remaining_turns=4, magnitude=2),
    )
    assert damage == 3
    assert healing == 0


def test_tick_effects_mixed_survival_preserves_relative_order():
    dying = effect(1, magnitude=4)
    surviving_one = effect(6, magnitude=1)
    result, damage, _ = tick_effects((dying, surviving_one))
    assert result == (effect(5, magnitude=1),)
    assert damage == 5


def test_tick_effects_does_not_mutate_input_tuple():
    a = effect(2, magnitude=1)
    original = (a,)
    tick_effects(original)
    assert original == (a,)


# ---------------------------------------------------------------------------
# REGENERATING — tick_effects healing (CONTRACT-v6 §25.1; bandage is
# regen_turns=5, regen_magnitude=3, applied by game.py, out of scope here)
# ---------------------------------------------------------------------------


def test_regenerating_produces_healing_equal_to_magnitude():
    _, damage, healing = tick_effects((effect(5, magnitude=3, kind=REGEN),))
    assert healing == 3
    assert damage == 0


def test_regenerating_sums_across_multiple_entries():
    _, _, healing = tick_effects(
        (effect(5, magnitude=3, kind=REGEN), effect(2, magnitude=4, kind=REGEN))
    )
    assert healing == 7


def test_regenerating_heals_for_exactly_remaining_turns_ticks():
    # A regen_turns=5 bandage-style effect heals on ticks with remaining_turns
    # 5, 4, 3, 2, 1 -- five ticks total -- then is gone.
    effects: tuple[StatusEffect, ...] = (effect(5, magnitude=3, kind=REGEN),)
    total_healing = 0
    ticks = 0
    while effects:
        effects, _, healing = tick_effects(effects)
        total_healing += healing
        ticks += 1
        assert ticks <= 10  # guard against an infinite loop if this regresses
    assert ticks == 5
    assert total_healing == 15


def test_regenerating_heals_on_the_tick_that_removes_it():
    surviving, _, healing = tick_effects((effect(1, magnitude=3, kind=REGEN),))
    assert surviving == ()
    assert healing == 3


def test_regenerating_reduces_remaining_turns_by_exactly_1():
    surviving, _, _ = tick_effects((effect(5, magnitude=3, kind=REGEN),))
    assert surviving == (effect(4, magnitude=3, kind=REGEN),)


def test_poison_and_regeneration_coexist_and_are_distinguishable():
    poisoned = effect(3, magnitude=2, kind=POISON)
    regenerating = effect(3, magnitude=2, kind=REGEN)
    surviving, damage, healing = tick_effects((poisoned, regenerating))
    # Equal magnitudes: a signed net would read as 0 and be indistinguishable from
    # "nothing is happening". The 3-tuple return keeps them apart.
    assert damage == 2
    assert healing == 2
    assert {e.kind for e in surviving} == {POISON, REGEN}


def test_enraged_contributes_no_damage_or_healing_when_ticked():
    # ENRAGED is a pure duration flag (task T22's seam); tick_effects must not
    # treat its magnitude as damage or healing even though it decrements and
    # survives/drops exactly like the other kinds.
    surviving, damage, healing = tick_effects((effect(2, magnitude=9, kind=ENRAGED),))
    assert damage == 0
    assert healing == 0
    assert surviving == (effect(1, magnitude=9, kind=ENRAGED),)


# ---------------------------------------------------------------------------
# StatusKind
# ---------------------------------------------------------------------------


def test_status_kind_is_poisoned_enraged_and_regenerating():
    # ENRAGED blocks a monster from fleeing (roguelike.npc.wants_to_flee).
    # Nothing applies it yet -- a tested seam with no live source.
    # REGENERATING is the bandage's status effect (CONTRACT-v6 §25.1); applying it is
    # game.py's job, out of scope here.
    assert {member.name for member in StatusKind} == {"POISONED", "ENRAGED", "REGENERATING"}


# ---------------------------------------------------------------------------
# apply_effect refresh-not-stack, re-verified for REGENERATING (CONTRACT-v5 §22.1,
# acceptance criterion: "refresh-not-stack still holds for the new kind")
# ---------------------------------------------------------------------------


def test_apply_effect_longer_duration_replaces_shorter_for_regenerating():
    existing = effect(2, magnitude=3, kind=REGEN)
    new = effect(5, magnitude=3, kind=REGEN)
    result = apply_effect((existing,), new)
    assert result == (new,)


def test_apply_effect_shorter_duration_leaves_tuple_unchanged_for_regenerating():
    existing = effect(5, magnitude=3, kind=REGEN)
    new = effect(2, magnitude=99, kind=REGEN)
    result = apply_effect((existing,), new)
    assert result == (existing,)


def test_apply_effect_equal_duration_leaves_tuple_unchanged_for_regenerating():
    existing = effect(5, magnitude=3, kind=REGEN)
    new = effect(5, magnitude=99, kind=REGEN)
    result = apply_effect((existing,), new)
    assert result == (existing,)


def test_apply_effect_never_more_than_one_entry_per_kind_with_poison_and_regen_both_present():
    poisoned = effect(4, magnitude=2, kind=POISON)
    regenerating = effect(4, magnitude=3, kind=REGEN)
    refreshed_regen = effect(9, magnitude=3, kind=REGEN)
    result = apply_effect((poisoned, regenerating), refreshed_regen)
    kinds = [e.kind for e in result]
    assert len(kinds) == len(set(kinds))
    assert result == (poisoned, refreshed_regen)


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
