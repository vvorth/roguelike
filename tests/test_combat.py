"""Tests for roguelike.combat (CONTRACT-v5 §23, task T25)."""

from __future__ import annotations

import ast
import copy
import dataclasses
import random
from pathlib import Path

import pytest

from roguelike import combat
from roguelike.combat import AttackResult, resolve_attack, to_hit_chance
from roguelike.stats import Actor, Stats

BASELINE_STATS = Stats(10, 10, 10)
RAT_STATS = Stats(4, 14, 3)


def _actor(stats: Stats, hp: int, status_effects: tuple = ()) -> Actor:
    return Actor(stats=stats, hp=hp, status_effects=status_effects)


class _CountingRandom(random.Random):
    """A Random subclass that counts calls to randint, to pin roll order/count."""

    def __init__(self, seed):
        super().__init__(seed)
        self.draws = 0

    def randint(self, a, b):
        self.draws += 1
        return super().randint(a, b)


# ---------------------------------------------------------------------------
# to_hit_chance — exact values and clamping
# ---------------------------------------------------------------------------


def test_to_hit_chance_baseline_is_85():
    assert to_hit_chance(5) == 85


def test_to_hit_chance_zero_evasion_is_90():
    assert to_hit_chance(0) == 90


def test_to_hit_chance_29_evasion_is_61():
    assert to_hit_chance(29) == 61


def test_to_hit_chance_14_evasion_is_76():
    assert to_hit_chance(14) == 76


def test_to_hit_chance_17_evasion_is_73():
    assert to_hit_chance(17) == 73


def test_to_hit_chance_clamps_at_upper_evasion_to_floor_of_5():
    assert to_hit_chance(200) == 5


def test_to_hit_chance_clamps_at_negative_evasion_to_ceiling_of_95():
    assert to_hit_chance(-200) == 95


@pytest.mark.parametrize("evasion", [-1000, -60, -1, 0, 5, 30, 60, 61, 500, 1000])
def test_to_hit_chance_never_outside_5_to_95(evasion):
    chance = to_hit_chance(evasion)
    assert 5 <= chance <= 95


# ---------------------------------------------------------------------------
# The first trap: no attacker accuracy term
# ---------------------------------------------------------------------------


def test_to_hit_is_independent_of_attacker_agi_not_a_reintroduced_accuracy_term():
    """Guards against RESEARCH-v5 §0 defect 4 / CONTRACT-v5 §23.1: to-hit must depend only on
    the defender's evasion. Two attackers with wildly different AGI (3 vs 18), same seeded rng,
    same defender, must produce the same hit/miss outcome every time."""
    defender = _actor(BASELINE_STATS, hp=45)
    low_agi_attacker = _actor(Stats(10, 3, 10), hp=45)
    high_agi_attacker = _actor(Stats(10, 18, 10), hp=45)

    for seed in range(200):
        rng_low = random.Random(seed)
        rng_high = random.Random(seed)
        result_low = resolve_attack(rng_low, low_agi_attacker, defender, 2, 5, True)
        result_high = resolve_attack(rng_high, high_agi_attacker, defender, 2, 5, True)
        assert result_low.hit == result_high.hit


# ---------------------------------------------------------------------------
# strength_applies splits melee vs natural/ranged
# ---------------------------------------------------------------------------


def test_strength_applies_true_gives_high_str_attacker_more_damage():
    defender = _actor(BASELINE_STATS, hp=45)  # block 0
    baseline_attacker = _actor(BASELINE_STATS, hp=45)
    strong_attacker = _actor(Stats(16, 10, 10), hp=45)  # str modifier +3

    compared = 0
    for seed in range(300):
        rng_base = random.Random(seed)
        rng_strong = random.Random(seed)
        res_base = resolve_attack(rng_base, baseline_attacker, defender, 2, 5, True)
        res_strong = resolve_attack(rng_strong, strong_attacker, defender, 2, 5, True)
        if res_base.hit and res_strong.hit:
            assert res_strong.damage > res_base.damage
            compared += 1
    assert compared > 0


def test_strength_applies_false_gives_identical_damage_regardless_of_str():
    defender = _actor(BASELINE_STATS, hp=45)  # block 0
    baseline_attacker = _actor(BASELINE_STATS, hp=45)
    strong_attacker = _actor(Stats(16, 10, 10), hp=45)

    compared = 0
    for seed in range(300):
        rng_base = random.Random(seed)
        rng_strong = random.Random(seed)
        res_base = resolve_attack(rng_base, baseline_attacker, defender, 2, 5, False)
        res_strong = resolve_attack(rng_strong, strong_attacker, defender, 2, 5, False)
        if res_base.hit and res_strong.hit:
            assert res_strong.damage == res_base.damage
            compared += 1
    assert compared > 0


# ---------------------------------------------------------------------------
# The second trap: natural attacks are not double-penalised by STR
# ---------------------------------------------------------------------------


def test_rat_bite_is_not_double_penalised_by_str_full_range_appears():
    """Guards against RESEARCH-v5 §0 defect 3 / CONTRACT-v5 §23.2: a rat's 1-3 bite already
    encodes its weakness; applying its STR modifier on top would floor every hit to 1. A rat
    (Stats(4, 14, 3)) biting a baseline (block 0) defender with strength_applies=False and
    damage 1-3 must produce the *full* 1..3 range across many seeds, not just 1."""
    rat = _actor(RAT_STATS, hp=17)
    defender = _actor(BASELINE_STATS, hp=45)  # block 0

    observed_damages = set()
    for seed in range(1000):
        rng = random.Random(seed)
        result = resolve_attack(rng, rat, defender, 1, 3, False)
        if result.hit:
            observed_damages.add(result.damage)

    assert observed_damages == {1, 2, 3}


# ---------------------------------------------------------------------------
# Damage floor and block
# ---------------------------------------------------------------------------


def test_damage_floor_is_never_zero_or_negative_against_high_block():
    weak_attacker = _actor(Stats(1, 10, 10), hp=45)  # str modifier (1-10)//2 = -5
    tanky_defender = _actor(Stats(40, 10, 10), hp=100)  # block (40-10)//2 = 15

    checked = 0
    for seed in range(300):
        rng = random.Random(seed)
        result = resolve_attack(rng, weak_attacker, tanky_defender, 1, 2, True)
        if result.hit:
            assert result.damage == 1
            checked += 1
    assert checked > 0


def test_block_subtracts_exactly_from_fixed_roll_damage():
    attacker = _actor(BASELINE_STATS, hp=45)  # str modifier 0
    low_block_defender = _actor(BASELINE_STATS, hp=45)  # block 0, evasion 5
    high_block_defender = _actor(Stats(16, 10, 10), hp=45)  # block 3, evasion 5 (same agi)

    compared = 0
    for seed in range(200):
        rng_low = random.Random(seed)
        rng_high = random.Random(seed)
        res_low = resolve_attack(rng_low, attacker, low_block_defender, 10, 10, True)
        res_high = resolve_attack(rng_high, attacker, high_block_defender, 10, 10, True)
        if res_low.hit and res_high.hit:
            assert res_low.damage - res_high.damage == 3
            compared += 1
    assert compared > 0


# ---------------------------------------------------------------------------
# defender_hp / killed relationship
# ---------------------------------------------------------------------------


def test_defender_hp_and_killed_are_consistent_with_damage_on_hit_or_miss():
    attacker = _actor(BASELINE_STATS, hp=45)
    defender = _actor(BASELINE_STATS, hp=5)

    for seed in range(300):
        rng = random.Random(seed)
        result = resolve_attack(rng, attacker, defender, 2, 5, True)
        assert result.defender_hp == defender.hp - result.damage
        assert result.killed == (result.defender_hp <= 0)


def test_miss_leaves_defender_hp_damage_killed_and_poison_at_defaults():
    attacker = _actor(BASELINE_STATS, hp=45)
    high_evasion_defender = _actor(Stats(10, 30, 10), hp=45)  # evasion 60, chance 30

    found_miss = False
    for seed in range(300):
        rng = random.Random(seed)
        result = resolve_attack(
            rng, attacker, high_evasion_defender, 2, 5, True, poison_chance=50
        )
        if not result.hit:
            found_miss = True
            assert result.damage == 0
            assert result.defender_hp == high_evasion_defender.hp
            assert result.killed is False
            assert result.poisoned is False
    assert found_miss


# ---------------------------------------------------------------------------
# Poison
# ---------------------------------------------------------------------------


def test_poison_never_set_when_chance_is_zero():
    attacker = _actor(BASELINE_STATS, hp=45)
    defender = _actor(BASELINE_STATS, hp=45)

    for seed in range(300):
        rng = random.Random(seed)
        result = resolve_attack(rng, attacker, defender, 2, 5, True, poison_chance=0)
        assert result.poisoned is False


def test_poison_never_set_on_a_miss_even_with_high_poison_chance():
    attacker = _actor(BASELINE_STATS, hp=45)
    high_evasion_defender = _actor(Stats(10, 30, 10), hp=45)

    found_miss = False
    for seed in range(300):
        rng = random.Random(seed)
        result = resolve_attack(
            rng, attacker, high_evasion_defender, 2, 5, True, poison_chance=100
        )
        if not result.hit:
            found_miss = True
            assert result.poisoned is False
    assert found_miss


def test_poison_rate_is_roughly_30_percent_of_hits_at_poison_chance_30():
    attacker = _actor(BASELINE_STATS, hp=45)
    low_evasion_defender = _actor(Stats(10, 0, 10), hp=45)  # evasion 0, chance 90

    hits = 0
    poisoned = 0
    for seed in range(3000):
        rng = random.Random(seed)
        result = resolve_attack(
            rng, attacker, low_evasion_defender, 2, 5, True, poison_chance=30
        )
        if result.hit:
            hits += 1
            if result.poisoned:
                poisoned += 1

    assert hits > 1000
    rate = poisoned / hits
    assert 0.20 <= rate <= 0.40


def test_resolve_attack_does_not_mutate_defender_status_effects():
    attacker = _actor(BASELINE_STATS, hp=45)
    defender = _actor(BASELINE_STATS, hp=45, status_effects=())

    rng = random.Random(7)
    resolve_attack(rng, attacker, defender, 2, 5, True, poison_chance=100)

    assert defender.status_effects == ()


# ---------------------------------------------------------------------------
# Roll order and determinism
# ---------------------------------------------------------------------------


def test_same_seed_gives_identical_results():
    attacker = _actor(Stats(12, 14, 8), hp=40)
    defender = _actor(Stats(9, 11, 12), hp=38)

    rng1 = random.Random(999)
    rng2 = random.Random(999)
    result1 = resolve_attack(rng1, attacker, defender, 2, 5, True, poison_chance=25)
    result2 = resolve_attack(rng2, attacker, defender, 2, 5, True, poison_chance=25)

    assert result1 == result2


def test_miss_consumes_exactly_one_draw():
    attacker = _actor(BASELINE_STATS, hp=45)
    high_evasion_defender = _actor(Stats(10, 30, 10), hp=45)  # chance 30, misses likely

    found_miss = False
    for seed in range(300):
        rng = _CountingRandom(seed)
        result = resolve_attack(
            rng, attacker, high_evasion_defender, 2, 5, True, poison_chance=50
        )
        if not result.hit:
            found_miss = True
            assert rng.draws == 1
    assert found_miss


def test_hit_without_poison_consumes_exactly_two_draws():
    attacker = _actor(BASELINE_STATS, hp=45)
    low_evasion_defender = _actor(Stats(10, 0, 10), hp=45)  # chance 90, hits likely

    found_hit = False
    for seed in range(300):
        rng = _CountingRandom(seed)
        result = resolve_attack(
            rng, attacker, low_evasion_defender, 2, 5, True, poison_chance=0
        )
        if result.hit:
            found_hit = True
            assert rng.draws == 2
    assert found_hit


def test_hit_with_poison_chance_consumes_exactly_three_draws():
    attacker = _actor(BASELINE_STATS, hp=45)
    low_evasion_defender = _actor(Stats(10, 0, 10), hp=45)  # chance 90, hits likely

    found_hit = False
    for seed in range(300):
        rng = _CountingRandom(seed)
        result = resolve_attack(
            rng, attacker, low_evasion_defender, 2, 5, True, poison_chance=30
        )
        if result.hit:
            found_hit = True
            assert rng.draws == 3
    assert found_hit


def test_miss_consumes_one_draw_by_comparing_generator_state():
    """Same assertion as test_miss_consumes_exactly_one_draw, verified instead by comparing the
    internal state of two generators, per the brief's alternative technique."""
    attacker = _actor(BASELINE_STATS, hp=45)
    high_evasion_defender = _actor(Stats(10, 30, 10), hp=45)

    found_miss = False
    for seed in range(300):
        rng_via_combat = random.Random(seed)
        rng_manual = random.Random(seed)
        result = resolve_attack(
            rng_via_combat, attacker, high_evasion_defender, 2, 5, True
        )
        if not result.hit:
            found_miss = True
            rng_manual.randint(1, 100)  # exactly one manual draw, mirroring the to-hit roll
            assert rng_via_combat.getstate() == rng_manual.getstate()
    assert found_miss


# ---------------------------------------------------------------------------
# Purity
# ---------------------------------------------------------------------------


def test_attacker_and_defender_are_unchanged_after_the_call():
    attacker = _actor(Stats(14, 12, 9), hp=30)
    defender = _actor(Stats(8, 16, 11), hp=25, status_effects=())
    attacker_copy = copy.deepcopy(attacker)
    defender_copy = copy.deepcopy(defender)

    rng = random.Random(42)
    resolve_attack(rng, attacker, defender, 2, 5, True, poison_chance=20)

    assert attacker == attacker_copy
    assert defender == defender_copy


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_raises_value_error_when_damage_min_greater_than_damage_max():
    attacker = _actor(BASELINE_STATS, hp=45)
    defender = _actor(BASELINE_STATS, hp=45)
    rng = random.Random(1)

    with pytest.raises(ValueError):
        resolve_attack(rng, attacker, defender, 5, 2, True)


def test_already_dead_defender_at_zero_hp_resolves_normally_and_stays_killed():
    attacker = _actor(BASELINE_STATS, hp=45)
    dead_defender = _actor(BASELINE_STATS, hp=0)

    for seed in range(100):
        rng = random.Random(seed)
        result = resolve_attack(rng, attacker, dead_defender, 2, 5, True)
        assert result.killed is True


def test_already_dead_defender_at_negative_hp_resolves_normally_and_stays_killed():
    attacker = _actor(BASELINE_STATS, hp=45)
    dead_defender = _actor(BASELINE_STATS, hp=-5)

    for seed in range(100):
        rng = random.Random(seed)
        result = resolve_attack(rng, attacker, dead_defender, 2, 5, True)
        assert result.killed is True


# ---------------------------------------------------------------------------
# Statistical sanity
# ---------------------------------------------------------------------------


def test_statistical_hit_rate_near_85_percent_at_baseline_over_2000_seeds():
    attacker = _actor(BASELINE_STATS, hp=45)
    defender = _actor(BASELINE_STATS, hp=45)  # evasion 5 -> chance 85

    total = 2000
    hits = 0
    for seed in range(total):
        rng = random.Random(seed)
        result = resolve_attack(rng, attacker, defender, 2, 5, True)
        if result.hit:
            hits += 1

    rate = hits / total
    assert 0.80 <= rate <= 0.90


# ---------------------------------------------------------------------------
# AttackResult shape
# ---------------------------------------------------------------------------


def test_attack_result_is_frozen():
    result = AttackResult(hit=True, damage=1, defender_hp=10, killed=False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.damage = 5  # type: ignore[misc]


def test_attack_result_poisoned_defaults_to_false():
    result = AttackResult(hit=True, damage=1, defender_hp=10, killed=False)
    assert result.poisoned is False


# ---------------------------------------------------------------------------
# Module hygiene: import graph, no floats, no true division
# ---------------------------------------------------------------------------


def test_combat_module_imports_only_the_allowed_modules():
    source = Path(combat.__file__).read_text()
    tree = ast.parse(source)

    allowed_modules = {
        "__future__",
        "dataclasses",
        "roguelike.stats",
        "roguelike.items",
        "roguelike.status",
    }
    forbidden_names = {"events", "npc", "level", "world", "game", "curses"}

    seen_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                seen_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            seen_modules.add(node.module or "")

    for module in seen_modules:
        assert module in allowed_modules, f"combat.py imports disallowed module: {module}"
        assert module.split(".")[-1] not in forbidden_names


def test_combat_module_has_no_float_literals_or_true_division():
    source = Path(combat.__file__).read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            pytest.fail(f"float literal found in combat.py: {node.value!r}")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            pytest.fail("true division ('/') found in combat.py")
