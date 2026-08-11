"""Character statistics and their four derived formulas (CONTRACT-v5 §20).

A leaf: imports only :mod:`roguelike.status`, for the ``StatusEffect`` tuple carried on
:class:`Actor`.

**Player and NPC compose an `Actor`; they do not inherit from one.** Frozen-dataclass
inheritance with defaults is a known trap, and composition keeps ``combat.py`` written once
against a single type (CONTRACT-v5 §20).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from roguelike.status import StatusEffect

__all__ = [
    "Condition",
    "condition",
    "BASELINE",
    "Stats",
    "Derived",
    "Actor",
    "derive",
]

BASELINE: int = 10


@dataclass(frozen=True)
class Stats:
    """The three primary stats. Baseline is :data:`BASELINE` for all three."""

    str_: int
    agi: int
    vit: int


@dataclass(frozen=True)
class Derived:
    """The four values :func:`derive` computes from :class:`Stats`."""

    max_hp: int
    speed: int
    evasion: int
    block: int


class Condition(IntEnum):
    """How hurt an actor looks, to itself or to anyone watching.

    Five bands rather than a number, because this is what one creature can *see* of
    another: an approximation, not a hit-point readout. The player's own bar is the
    same vocabulary, so "am I worse off than that thing?" is one comparison of two
    values from the same scale.

    An :class:`~enum.IntEnum`, ordered best to worst, so the members compare directly:
    ``Condition.WOUNDED < Condition.NEAR_DEATH`` reads as "less hurt than". That
    comparison is the whole point — it is how a monster decides whether the player is
    in better shape than it is, and it is why this is not a plain ``Enum``
    (:class:`roguelike.tiles.Tile` is an ``IntEnum`` for the same kind of reason).
    """

    UNHURT = 0
    SCRATCHED = 1
    WOUNDED = 2
    BADLY_WOUNDED = 3
    NEAR_DEATH = 4


def condition(hp: int, max_hp: int) -> Condition:
    """Which band ``hp`` out of ``max_hp`` falls into.

    Thresholds are quarters, compared by integer cross-multiplication so no float and
    no rounding rule enters (CONTRACT-v5 §0.13):

    ==================  ==========================
    at full health      ``UNHURT``
    above three quarters ``SCRATCHED``
    above one half      ``WOUNDED``
    above one quarter   ``BADLY_WOUNDED``
    anything less       ``NEAR_DEATH``
    ==================  ==========================

    A dead or negative ``hp`` reads ``NEAR_DEATH``; a non-positive ``max_hp`` reads
    ``NEAR_DEATH`` too rather than dividing by zero. Never raises.
    """
    if max_hp <= 0 or hp <= 0:
        return Condition.NEAR_DEATH
    if hp >= max_hp:
        return Condition.UNHURT
    if hp * 4 > max_hp * 3:
        return Condition.SCRATCHED
    if hp * 2 > max_hp:
        return Condition.WOUNDED
    if hp * 4 > max_hp:
        return Condition.BADLY_WOUNDED
    return Condition.NEAR_DEATH


@dataclass(frozen=True)
class Actor:
    """The shared core of the player and every NPC."""

    stats: Stats
    hp: int
    status_effects: tuple[StatusEffect, ...] = ()

    @property
    def condition(self) -> Condition:
        """How hurt this actor looks. Derived, never stored — one source of truth."""
        return condition(self.hp, derive(self.stats).max_hp)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def derive(stats: Stats) -> Derived:
    """Compute the four derived stats from ``stats`` (CONTRACT-v5 §20.1).

    Pure and total: never raises, for any integer input including negative and zero.

    | Derived    | Formula                                    |
    |------------|---------------------------------------------|
    | ``max_hp`` | ``5 + vit * 4``                              |
    | ``speed``  | ``100 + 10 * (agi - 10)``                    |
    | ``evasion``| ``clamp(5 + (agi - 10) * 3, 0, 60)``         |
    | ``block``  | ``max(0, (str_ - 10) // 2)``                 |

    ``block`` is deliberately 0 at baseline and stays 0 for every ``str_ <= 10`` — it is
    advantage *over* baseline, not a flat reduction. Do not change ``(str_ - 10) // 2`` to
    ``str_ // 2``: that reintroduces a measured, fatal bug (every attack in the game floored
    to 1 damage). See CONTRACT-v5 §20.1 and RESEARCH-v5 §0/§2.
    """
    max_hp = 5 + stats.vit * 4
    speed = 100 + 10 * (stats.agi - 10)
    evasion = _clamp(5 + (stats.agi - 10) * 3, 0, 60)
    block = max(0, (stats.str_ - 10) // 2)
    return Derived(max_hp=max_hp, speed=speed, evasion=evasion, block=block)
