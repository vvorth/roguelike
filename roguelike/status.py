"""Status-effect vocabulary and ticking rules (CONTRACT-v5 §22).

A leaf: imports nothing from :mod:`roguelike`. Every actor (player or NPC) carries a tuple of
:class:`StatusEffect` — at most one entry per :class:`StatusKind`, refreshed via
:func:`apply_effect` and advanced one world-tick at a time via :func:`tick_effects`.

Only ``POISONED`` exists. Adding a second :class:`StatusKind`, a registry, or any per-kind
behaviour beyond "deal damage each tick" is out of scope for this module (task T22).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

__all__ = [
    "StatusKind",
    "StatusEffect",
    "REGEN_TURNS",
    "apply_effect",
    "tick_effects",
]


class StatusKind(Enum):
    """The status-effect vocabulary (CONTRACT-v5 §22)."""

    POISONED = auto()
    ENRAGED = auto()


#: An enraged creature never flees, however badly hurt it is (see
#: :func:`roguelike.npc.plan_action`). Nothing applies it yet — it is the seam the
#: "except when in some enraged state" rule needs, shipped called-and-tested with no
#: live source, exactly as ``interruption`` shipped in v4.
@dataclass(frozen=True)
class StatusEffect:
    """One active status effect on an actor.

    ``magnitude`` is the damage dealt on each tick (see :func:`tick_effects`); it is not
    reinterpreted per-kind since ``POISONED`` is the only kind that exists.
    """

    kind: StatusKind
    remaining_turns: int
    magnitude: int


REGEN_TURNS: int = 3
"""The player regains 1 HP every ``REGEN_TURNS`` world-ticks (CONTRACT-v5 §22.4).

**Was 10, corrected to 3.** RESEARCH-v5 §7 chose 10 from a simulation that turned out to
have a bug in it: the sweep passed the HP multiplier for the player but let monsters keep
the function's default, so it modelled monsters at ``5 + VIT*2`` while the shipped
``stats.derive`` gives everything ``5 + VIT*4``. Re-run against the real values, the
published "61.5% of floors cleared" was actually **2.2%** — the near-unplayable regime the
whole research re-check existed to eliminate. At 3 it is **61.9%**, which is what the
research intended all along. Monsters fleeing (added alongside) accounts for a few points
of that on its own.

Applying regeneration to the player is the caller's responsibility (``game.py``, out of scope
for this module) — this module only carries the constant.
"""


def apply_effect(
    effects: tuple[StatusEffect, ...], new: StatusEffect
) -> tuple[StatusEffect, ...]:
    """Fold ``new`` into ``effects``, refreshing rather than stacking (CONTRACT-v5 §22.1).

    - No effect of ``new.kind`` present: ``new`` is appended.
    - One present: replaced only if ``new.remaining_turns`` is strictly greater than the
      existing entry's ``remaining_turns``. Otherwise ``effects`` is returned unchanged.
    - Magnitude never stacks; there is never more than one entry per kind.

    Pure — ``effects`` is never mutated.
    """
    for index, existing in enumerate(effects):
        if existing.kind == new.kind:
            if new.remaining_turns > existing.remaining_turns:
                return effects[:index] + (new,) + effects[index + 1 :]
            return effects
    return effects + (new,)


def tick_effects(
    effects: tuple[StatusEffect, ...],
) -> tuple[tuple[StatusEffect, ...], int]:
    """Advance every effect by one world-tick (CONTRACT-v5 §22.2).

    Each effect contributes its ``magnitude`` to the returned damage total and has
    ``remaining_turns`` reduced by 1 — including the tick that drops it to 0, which still
    counts toward the damage total. An effect at ``remaining_turns == 0`` after the
    decrement is dropped from the surviving tuple. Order is preserved. Pure — ``effects`` is
    never mutated.
    """
    surviving: tuple[StatusEffect, ...] = ()
    total_damage = 0
    for effect in effects:
        total_damage += effect.magnitude
        remaining = effect.remaining_turns - 1
        if remaining > 0:
            surviving += (
                StatusEffect(
                    kind=effect.kind,
                    remaining_turns=remaining,
                    magnitude=effect.magnitude,
                ),
            )
    return surviving, total_damage
