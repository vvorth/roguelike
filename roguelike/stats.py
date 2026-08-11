"""Character statistics and their four derived formulas (CONTRACT-v5 §20).

A leaf: imports only :mod:`roguelike.status`, for the ``StatusEffect`` tuple carried on
:class:`Actor`.

**Player and NPC compose an `Actor`; they do not inherit from one.** Frozen-dataclass
inheritance with defaults is a known trap, and composition keeps ``combat.py`` written once
against a single type (CONTRACT-v5 §20).
"""

from __future__ import annotations

from dataclasses import dataclass

from roguelike.status import StatusEffect

__all__ = [
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


@dataclass(frozen=True)
class Actor:
    """The shared core of the player and every NPC."""

    stats: Stats
    hp: int
    status_effects: tuple[StatusEffect, ...] = ()


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
