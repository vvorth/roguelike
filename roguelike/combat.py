"""Combat resolution — a pure calculator (CONTRACT-v5 §23).

A leaf over :mod:`roguelike.stats`. Answers exactly one question, "does this attack hit, and
for how much?", for **both** sides of every fight — the player attacking an NPC and an NPC
attacking the player use this module unchanged. It does not know what an NPC is, does not emit
messages, does not remove anything from the world, and does not end the game; it returns a
structured :class:`AttackResult` and the caller (``game.py``) interprets it, exactly as
:func:`roguelike.movement.try_move` returns a ``MoveResult`` that ``game.py`` interprets.

**Two previously-measured bugs, deliberately absent:**

1. There is **no attacker term** in to-hit. To-hit depends only on the defender's evasion.
   Adding ``+ (attacker.agi - 10)`` makes AGI drive speed *and* evasion *and* accuracy — the
   single best stat, and a contradiction of stats.py's one-identity-per-stat design.
2. The attacker's STR modifier applies to **wielded weapons only**, never to natural attacks
   (bite, claw). A species' natural damage range already encodes how strong it is; adding its
   STR modifier on top counts the same fact twice and floors every animal's damage to 1.

No ``Random`` instance is created or stored here (CONTRACT-v5 §0.12); ``rng`` is supplied by the
caller, which derives a fresh generator per roll from ``(master_seed, turns, actor_id, salt)``.
"""

from __future__ import annotations

from dataclasses import dataclass

from roguelike.stats import Actor, derive

__all__ = ["AttackResult", "to_hit_chance", "resolve_attack"]


@dataclass(frozen=True)
class AttackResult:
    """The outcome of one resolved attack.

    ``damage`` is always ``0`` when ``hit`` is ``False``. ``defender_hp`` is the defender's HP
    after ``damage`` is subtracted (unchanged from ``defender.hp`` on a miss). ``killed`` is
    exactly ``defender_hp <= 0`` — true even when the defender was already dead going in.
    ``poisoned`` is only ever ``True`` when ``hit`` is ``True`` and the poison roll (if any)
    succeeded; this module never applies the effect itself.
    """

    hit: bool
    damage: int
    defender_hp: int
    killed: bool
    poisoned: bool = False


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


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

    roll = rng.randint(damage_min, damage_max)
    if strength_applies:
        damage = max(
            1, roll + (attacker.stats.str_ - 10) // 2 - defender_derived.block
        )
    else:
        damage = max(1, roll - defender_derived.block)

    defender_hp = defender.hp - damage
    killed = defender_hp <= 0

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
