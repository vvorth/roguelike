"""Static weapon inventory (CONTRACT-v5 §21).

A leaf: imports nothing from :mod:`roguelike`.

Inventory is static by design: there is no pickup, no drop, no ground item, no inventory
screen, no ammunition count, no loot table, and no ``Item`` base class. Every player is
constructed at ``new_game`` holding exactly :data:`DAGGER` and :data:`SHORTBOW`, and this
never changes. That construction is the caller's responsibility (out of scope here) — this
module only defines the two weapons.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

__all__ = [
    "WeaponKind",
    "Weapon",
    "DAGGER",
    "SHORTBOW",
]


class WeaponKind(Enum):
    """The complete weapon-kind vocabulary (CONTRACT-v5 §21). Do not add members."""

    MELEE = auto()
    RANGED = auto()


@dataclass(frozen=True)
class Weapon:
    """A static weapon definition.

    ``range`` is a Chebyshev distance; a ``MELEE`` weapon's range is always 1.
    """

    name: str
    kind: WeaponKind
    damage_min: int
    damage_max: int
    range: int = 1


DAGGER: Weapon = Weapon("dagger", WeaponKind.MELEE, 2, 5, range=1)
SHORTBOW: Weapon = Weapon("shortbow", WeaponKind.RANGED, 1, 4, range=6)
