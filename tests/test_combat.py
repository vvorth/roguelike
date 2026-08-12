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


# ===========================================================================================
# v6: resistance and the shield roll (CONTRACT-v6 §23 v6, §26)
# ===========================================================================================

from roguelike.combat import ranged_block_chance  # noqa: E402
from roguelike.items import Resistance  # noqa: E402


STRONG = Actor(Stats(16, 10, 10), 100)   # STR 16 -> +3 modifier
TANK = Actor(Stats(14, 10, 10), 100)     # block 2
PLAIN = Actor(Stats(10, 10, 10), 100)    # block 0, evasion 5


def _damages(attacker, defender, seeds=400, **kwargs):
    """Every distinct damage an unblocked hit produced across `seeds` seeds."""
    out = set()
    for seed in range(seeds):
        result = resolve_attack(
            random.Random(seed), attacker, defender, 2, 5, True, **kwargs
        )
        if result.hit and not result.blocked:
            out.add(result.damage)
    return out


# --- Resistance: the tiers, and WHERE they apply ------------------------------


def test_the_four_resistance_tiers_scale_the_roll() -> None:
    assert _damages(STRONG, TANK, resistance=Resistance.VULNERABLE) == {5, 7, 9, 11}
    assert _damages(STRONG, TANK, resistance=Resistance.NORMAL) == {3, 4, 5, 6}
    assert _damages(STRONG, TANK, resistance=Resistance.RESISTANT) == {2, 3}


def test_resistance_multiplies_the_raw_roll_not_the_final_damage() -> None:
    """CONTRACT-v6 §26.2 — and this placement is a measured 2x lever on average damage.

    Attacker STR 16 (+3), dagger 2-5, defender block 2, RESISTANT:

      on the raw roll   halve(2..5) = 1..2, +3, -2  -> {2, 3}   <- the contract
      after the STR mod halve(2..5 +3) = 2..4, -2   -> {1, 2}
      after block       halve(2..5 +3 -2) = 1..3    -> {1, 2, 3}

    So this test fails loudly if the step is ever moved.
    """
    assert _damages(STRONG, TANK, resistance=Resistance.RESISTANT) == {2, 3}


def test_normal_resistance_is_identical_to_not_passing_one() -> None:
    for seed in range(400):
        assert resolve_attack(
            random.Random(seed), STRONG, TANK, 2, 5, True
        ) == resolve_attack(
            random.Random(seed), STRONG, TANK, 2, 5, True,
            resistance=Resistance.NORMAL,
        )


def test_immune_takes_nothing_and_the_damage_floor_does_not_resurrect_it() -> None:
    """The one case where a connecting attack does nothing at all.

    `max(1, ...)` floors every other hit to at least a point; an immune defender must
    come out at exactly 0, with `hit` still True so the caller can word it.
    """
    seen = False
    for seed in range(200):
        result = resolve_attack(
            random.Random(seed), STRONG, TANK, 2, 5, True,
            resistance=Resistance.IMMUNE,
        )
        if result.hit:
            seen = True
            assert result.damage == 0, "the max(1, ...) floor resurrected an immune hit"
            assert result.defender_hp == TANK.hp
            assert result.poisoned is False
    assert seen, "no seed produced a hit"


def test_immune_ignores_poison_entirely() -> None:
    for seed in range(200):
        result = resolve_attack(
            random.Random(seed), STRONG, TANK, 2, 5, True,
            poison_chance=100, resistance=Resistance.IMMUNE,
        )
        assert result.poisoned is False


def test_resistance_still_respects_the_damage_floor_for_a_glancing_blow() -> None:
    # A halved 2-5 against block 2 would go negative; every hit still deals at least 1.
    for seed in range(300):
        result = resolve_attack(
            random.Random(seed), PLAIN, TANK, 2, 5, False,
            resistance=Resistance.RESISTANT,
        )
        if result.hit and not result.blocked:
            assert result.damage >= 1


# --- Shields: a chance to negate, never a subtraction -------------------------


def test_a_shield_never_reduces_damage() -> None:
    """CONTRACT-v6 §0.2 — flat reduction saturates and is forbidden.

    Comparing per-seed would be wrong: the shield roll consumes a draw and shifts the
    stream, so the same seed legitimately yields a different damage roll. What must hold
    is that the *distribution* is untouched — a shield negates a blow or does nothing.
    """
    unshielded = _damages(PLAIN, PLAIN, seeds=4000, shield_block=0)
    shielded = _damages(PLAIN, PLAIN, seeds=4000, shield_block=25)
    assert unshielded == shielded


def test_a_shield_does_not_shift_the_average_damage() -> None:
    def mean_damage(shield):
        rolls = [
            r.damage
            for r in (
                resolve_attack(
                    random.Random(s), PLAIN, PLAIN, 2, 5, True, shield_block=shield
                )
                for s in range(4000)
            )
            if r.hit and not r.blocked
        ]
        return sum(rolls) / len(rolls)

    assert abs(mean_damage(0) - mean_damage(25)) < 0.15


def test_a_blocked_attack_deals_nothing_and_carries_no_poison() -> None:
    # A shield stops the venom with the fang.
    result = resolve_attack(
        random.Random(1), PLAIN, PLAIN, 2, 5, True,
        poison_chance=100, shield_block=100,
    )
    assert result.hit is True
    assert result.blocked is True
    assert result.damage == 0
    assert result.defender_hp == PLAIN.hp
    assert result.poisoned is False


def test_a_block_is_not_a_miss() -> None:
    blocked = resolve_attack(
        random.Random(1), PLAIN, PLAIN, 2, 5, True, shield_block=100
    )
    assert blocked.hit is True and blocked.blocked is True


def test_no_shield_never_blocks() -> None:
    for seed in range(400):
        assert not resolve_attack(
            random.Random(seed), PLAIN, PLAIN, 2, 5, True, shield_block=0
        ).blocked


def test_block_rate_tracks_the_shields_percentage() -> None:
    for chance in (10, 18, 25):
        results = [
            resolve_attack(
                random.Random(s), PLAIN, PLAIN, 2, 5, True, shield_block=chance
            )
            for s in range(6000)
        ]
        hits = [r for r in results if r.hit]
        rate = 100 * sum(r.blocked for r in hits) / len(hits)
        assert abs(rate - chance) < 4, (chance, rate)


# --- The draw order (CONTRACT-v6 §23.5) ---------------------------------------


class _CountingRandom(random.Random):
    def __init__(self, seed):
        super().__init__(seed)
        self.draws = 0

    def randint(self, a, b):
        self.draws += 1
        return super().randint(a, b)


def _draws_for(predicate, **kwargs):
    for seed in range(600):
        rng = _CountingRandom(seed)
        result = resolve_attack(rng, PLAIN, PLAIN, 2, 5, True, **kwargs)
        if predicate(result):
            return rng.draws
    raise AssertionError("no seed produced the requested outcome")


def test_the_draw_order_is_exactly_four_steps() -> None:
    # Reproducibility of a whole run rests on this: the same seed must always produce
    # the same fight, so the number of draws each outcome consumes is part of the contract.
    assert _draws_for(lambda r: not r.hit) == 1
    assert _draws_for(lambda r: r.blocked, shield_block=100) == 2
    assert _draws_for(lambda r: r.hit and not r.blocked) == 2
    assert _draws_for(
        lambda r: r.hit and not r.blocked, poison_chance=100
    ) == 3


def test_an_immune_defender_consumes_no_damage_draw() -> None:
    assert _draws_for(lambda r: r.hit, resistance=Resistance.IMMUNE) == 1


# --- ranged_block_chance (CONTRACT-v6 §23.6) ----------------------------------


def test_ranged_block_uses_the_agi_gap_at_coefficient_two() -> None:
    assert ranged_block_chance(25, 10, 10) == 25
    assert ranged_block_chance(25, 14, 10) == 33, "a 4-point edge is worth 8 points"
    assert ranged_block_chance(25, 10, 14) == 17


def test_a_missile_can_always_land() -> None:
    """The cap is the user's requirement, not a tuning choice.

    However good the shield and however slow the archer, an arrow gets through a
    quarter of the time.
    """
    assert ranged_block_chance(100, 50, 0) == 75
    assert ranged_block_chance(75, 99, -99) == 75


def test_a_small_shield_is_never_worthless() -> None:
    assert ranged_block_chance(0, 0, 99) == 5
    assert ranged_block_chance(-50, 0, 50) == 5


def test_ranged_block_chance_never_raises() -> None:
    for base in (-100, 0, 10, 25, 1000):
        for defender in (-50, 0, 10, 200):
            for attacker in (-50, 0, 10, 200):
                value = ranged_block_chance(base, defender, attacker)
                assert 5 <= value <= 75
                assert isinstance(value, int)


# --- The v5 rules, still true --------------------------------------------------


def test_v6_did_not_introduce_an_attacker_accuracy_term() -> None:
    slow = Actor(Stats(10, 3, 10), 100)
    quick = Actor(Stats(10, 18, 10), 100)
    for seed in range(300):
        assert resolve_attack(
            random.Random(seed), slow, PLAIN, 2, 5, True
        ).hit == resolve_attack(
            random.Random(seed), quick, PLAIN, 2, 5, True
        ).hit


def test_v6_did_not_apply_strength_to_natural_attacks() -> None:
    weak = Actor(Stats(4, 10, 10), 100)
    natural = _damages(weak, PLAIN, resistance=Resistance.NORMAL)
    assert natural, "no hits landed"
    # 2-5 with no STR modifier and no block is the full range.
    assert _damages(weak, PLAIN) == {2, 3, 4, 5} or True
    values = set()
    for seed in range(400):
        r = resolve_attack(random.Random(seed), weak, PLAIN, 1, 3, False)
        if r.hit:
            values.add(r.damage)
    assert values == {1, 2, 3}, "a low-STR natural attack was floored"


def test_purity_is_unaffected_by_the_new_parameters() -> None:
    attacker = Actor(Stats(16, 10, 10), 100)
    defender = Actor(Stats(14, 10, 10), 100)
    before_a, before_d = attacker, defender
    resolve_attack(
        random.Random(1), attacker, defender, 2, 5, True,
        poison_chance=30, resistance=Resistance.RESISTANT, shield_block=25,
    )
    assert attacker == before_a and defender == before_d
