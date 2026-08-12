"""Combat resolution — a pure calculator (CONTRACT-v5 §23, CONTRACT-v6 §23 v6 and §26).

A leaf over :mod:`roguelike.stats` and :mod:`roguelike.items`. Answers exactly one question,
"does this attack hit, and for how much?", for **both** sides of every fight — the player
attacking an NPC and an NPC attacking the player use this module unchanged. It does not know
what an NPC is, does not emit messages, does not remove anything from the world, and does not
end the game; it returns a structured :class:`AttackResult` and the caller (``game.py``)
interprets it, exactly as :func:`roguelike.movement.try_move` returns a ``MoveResult`` that
``game.py`` interprets.

**Four previously-measured bugs, deliberately absent:**

1. There is **no attacker term** in to-hit. To-hit depends only on the defender's evasion.
   Adding ``+ (attacker.agi - 10)`` makes AGI drive speed *and* evasion *and* accuracy — the
   single best stat, and a contradiction of stats.py's one-identity-per-stat design.
2. The attacker's STR modifier applies to **wielded weapons only**, never to natural attacks
   (bite, claw). A species' natural damage range already encodes how strong it is; adding its
   STR modifier on top counts the same fact twice and floors every animal's damage to 1.
3. **Resistance multiplies the raw damage roll**, before the STR modifier and before block
   (CONTRACT-v6 §26.2). Where this step sits is a measured 2× lever: the same 50% resistance
   yields average damage anywhere from 1.25 to 2.50 purely from its position in the pipeline
   (§0.3). A resistant hide should blunt the blade, not the arm. **Do not move it.**
4. **A shield is a chance to negate a blow, never a subtraction.** At just +1 flat damage
   reduction, 58% of all monster damage rolls already floor to 1 and the player wins 100% of
   jackal fights (§0.2). ``shield_block`` is rolled; on success the attack deals nothing at
   all, and on failure it changes the damage by exactly zero.

``Resistance.IMMUNE`` is the one case where a connecting attack does nothing: damage is ``0``
with ``hit`` still ``True``, and the ``max(1, …)`` floor must not resurrect it to 1 (§26.2).

No ``Random`` instance is created or stored here (CONTRACT-v5 §0.12); ``rng`` is supplied by the
caller, which derives a fresh generator per roll from ``(master_seed, turns, actor_id, salt)``.
"""

from __future__ import annotations

from dataclasses import dataclass

from roguelike.items import Resistance
from roguelike.stats import Actor, derive

__all__ = ["AttackResult", "to_hit_chance", "ranged_block_chance", "resolve_attack"]


@dataclass(frozen=True)
class AttackResult:
    """The outcome of one resolved attack.

    ``damage`` is always ``0`` when ``hit`` is ``False``. ``defender_hp`` is the defender's HP
    after ``damage`` is subtracted (unchanged from ``defender.hp`` on a miss). ``killed`` is
    exactly ``defender_hp <= 0`` — true even when the defender was already dead going in.
    ``poisoned`` is only ever ``True`` when ``hit`` is ``True`` and the poison roll (if any)
    succeeded; this module never applies the effect itself.

    ``blocked`` means a shield turned the blow: the attack connected but was stopped, so
    ``damage`` is ``0`` and ``poisoned`` is ``False`` — a shield stops the venom with the fang.
    It is distinct from a miss, which the player is told about differently.
    """

    hit: bool
    damage: int
    defender_hp: int
    killed: bool
    poisoned: bool = False
    blocked: bool = False


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


#: How much one point of AGI difference moves a shield's chance against a missile
#: (CONTRACT-v6 §23.6). Swept before it was chosen: at 1 the stat gap is decoration, at 3 it
#: swamps the shield so a buckler on a fast character beats a tower shield on a slow one.
_RANGED_AGI_COEFF: int = 2

#: The floor and cap on that chance. The cap is the binding half of the requirement that a
#: shot must **always** be able to land: at 75 an arrow still gets through a quarter of the
#: time however good the shield and however slow the archer. The floor keeps a small shield
#: from ever being literally worthless.
_RANGED_BLOCK_FLOOR: int = 5
_RANGED_BLOCK_CAP: int = 75


def ranged_block_chance(
    shield_block: int, defender_agi: int, attacker_agi: int
) -> int:
    """A shield's percentage chance to stop a *missile* (CONTRACT-v6 §23.6).

    ``clamp(shield_block + (defender_agi - attacker_agi) * 2, 5, 75)``.

    AGI is the only stat that can carry this: STR is arm strength, VIT is constitution, and
    ``evasion`` is already spent on the to-hit roll — reusing it would make AGI count twice in
    one exchange.

    **There is always a chance to be hit.** That is what the cap of 75 is for, and it is a
    requirement rather than a tuning choice.

    Pure, total, integer-only; never raises for any input, however absurd.
    """
    shifted = shield_block + (defender_agi - attacker_agi) * _RANGED_AGI_COEFF
    return _clamp(shifted, _RANGED_BLOCK_FLOOR, _RANGED_BLOCK_CAP)


def _resisted(roll: int, resistance: Resistance) -> int:
    """Apply ``resistance`` to a raw damage roll (CONTRACT-v6 §26.1).

    Coarse tiers rather than percentages, because percentages are unusable at these numbers:
    on a 2–5 roll, 25% and 33% give the identical outcome set and 66% and 75% are both flat 1
    (§0.3).

    ``IMMUNE`` is handled by the caller, not here — it must bypass the ``max(1, …)`` floor,
    and this function only knows about the roll.
    """
    if resistance is Resistance.RESISTANT:
        return roll // 2
    if resistance is Resistance.VULNERABLE:
        return roll * 2
    return roll


def to_hit_chance(defender_evasion: int) -> int:
    """Return the percent chance to hit a defender with the given evasion (CONTRACT-v5 §23.1).

    ``clamp(90 - defender_evasion, 5, 95)``. Pure and total: never raises, and the result is
    always in ``5..95`` inclusive for any integer input. **There is no attacker term** — this
    function does not and must not take an attacker parameter.
    """
    return _clamp(90 - defender_evasion, 5, 95)


def resolve_attack(
    rng,
    attacker: Actor,
    defender: Actor,
    damage_min: int,
    damage_max: int,
    strength_applies: bool,
    poison_chance: int = 0,
    resistance: Resistance = Resistance.NORMAL,
    shield_block: int = 0,
) -> AttackResult:
    """Resolve one attack from ``attacker`` against ``defender`` (CONTRACT-v5 §23).

    Pure: mutates neither ``attacker`` nor ``defender``, performs no I/O, and never applies a
    status effect (that is the caller's job, via ``status.apply_effect``, using
    ``AttackResult.poisoned`` as the signal).

    Draws from ``rng`` in exactly this order, and only when needed — the same seed must always
    produce the same fight:

    1. the to-hit roll (``rng.randint(1, 100)``), always;
    2. the damage roll (``rng.randint(damage_min, damage_max)``), only if the attack hit;
    3. the poison roll (``rng.randint(1, 100)``), only if the attack hit **and**
       ``poison_chance > 0``.

    A miss therefore consumes exactly one draw.

    To-hit depends only on ``defender``'s evasion (CONTRACT-v5 §23.1) — ``attacker`` never
    contributes an accuracy term. Damage is ``roll(damage_min, damage_max) - defender.block``
    when ``strength_applies`` is ``False`` (natural attacks, and all ranged weapons even though
    they are wielded), or that plus ``(attacker.stats.str_ - 10) // 2`` when ``strength_applies``
    is ``True`` (wielded melee weapons only), floored to a minimum of 1 on a confirmed hit
    (CONTRACT-v5 §23.2). Both the STR modifier and ``block`` use floor division, which rounds
    toward negative infinity, not truncation (CONTRACT-v5 §0.13).

    Raises:
        ValueError: if ``damage_min > damage_max``.
    """
    if damage_min > damage_max:
        raise ValueError(
            f"damage_min ({damage_min}) must be <= damage_max ({damage_max})"
        )

    defender_derived = derive(defender.stats)
    chance = to_hit_chance(defender_derived.evasion)
    hit = rng.randint(1, 100) <= chance

    if not hit:
        return AttackResult(
            hit=False,
            damage=0,
            defender_hp=defender.hp,
            killed=defender.hp <= 0,
            poisoned=False,
        )

    # 2. The shield roll, before any damage is rolled — a blocked blow deals nothing, so
    #    there is nothing to roll. This ordering is what makes a block cost exactly two draws
    #    (CONTRACT-v6 §23.5), which the reproducibility of a whole run depends on.
    if shield_block > 0 and rng.randint(1, 100) <= shield_block:
        return AttackResult(
            hit=True,
            damage=0,
            defender_hp=defender.hp,
            killed=defender.hp <= 0,
            poisoned=False,
            blocked=True,
        )

    # An immune defender takes nothing at all. Returned before the roll so `IMMUNE` costs no
    # damage draw, and — the point — so the `max(1, …)` floor below cannot resurrect it to 1.
    if resistance is Resistance.IMMUNE:
        return AttackResult(
            hit=True,
            damage=0,
            defender_hp=defender.hp,
            killed=defender.hp <= 0,
            poisoned=False,
        )

    # 3. Damage. Resistance multiplies the RAW ROLL, before the strength modifier and before
    #    block (CONTRACT-v6 §26.2). Moving this step is a measured 2× lever on average damage;
    #    a resistant hide blunts the blade, not the arm.
    roll = _resisted(rng.randint(damage_min, damage_max), resistance)
    if strength_applies:
        damage = max(
            1, roll + (attacker.stats.str_ - 10) // 2 - defender_derived.block
        )
    else:
        damage = max(1, roll - defender_derived.block)

    defender_hp = defender.hp - damage
    killed = defender_hp <= 0

    # 4. Poison, last, and only when damage was actually dealt.
    poisoned = False
    if poison_chance > 0:
        poisoned = rng.randint(1, 100) <= poison_chance

    return AttackResult(
        hit=True,
        damage=damage,
        defender_hp=defender_hp,
        killed=killed,
        poisoned=poisoned,
    )
