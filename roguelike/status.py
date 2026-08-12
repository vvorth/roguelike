"""Status-effect vocabulary and ticking rules (CONTRACT-v5 §22, CONTRACT-v6 §25.1).

A leaf: imports nothing from :mod:`roguelike`. Every actor (player or NPC) carries a tuple of
:class:`StatusEffect` — at most one entry per :class:`StatusKind`, refreshed via
:func:`apply_effect` and advanced one world-tick at a time via :func:`tick_effects`.

Three kinds exist: ``POISONED`` and ``REGENERATING`` each carry a per-tick magnitude that
:func:`tick_effects` reports back (as damage and healing respectively); ``ENRAGED`` is a pure
duration flag — nothing applies it yet, and it contributes neither damage nor healing when
ticked (task T22's seam, unchanged by T31). Adding a further :class:`StatusKind` or any
per-kind behaviour beyond "contribute a per-tick number, or don't" is out of scope for this
module.
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
    """The status-effect vocabulary (CONTRACT-v5 §22, CONTRACT-v6 §25.1)."""

    POISONED = auto()
    ENRAGED = auto()
    REGENERATING = auto()


#: An enraged creature never flees, however badly hurt it is (see
#: :func:`roguelike.npc.plan_action`). Nothing applies it yet — it is the seam the
#: "except when in some enraged state" rule needs, shipped called-and-tested with no
#: live source, exactly as ``interruption`` shipped in v4.
@dataclass(frozen=True)
class StatusEffect:
    """One active status effect on an actor.

    ``magnitude`` is always a non-negative per-tick number; what it *means* depends on
    ``kind`` (see :func:`tick_effects`):

    - ``POISONED`` — HP lost on that tick.
    - ``REGENERATING`` — HP regained on that tick.
    - ``ENRAGED`` — carries no numeric effect; ``magnitude`` is conventionally 0 and is
      ignored by :func:`tick_effects` either way.
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
) -> tuple[tuple[StatusEffect, ...], int, int]:
    """Advance every effect by one world-tick (CONTRACT-v5 §22.2, CONTRACT-v6 §25.1).

    Returns ``(surviving_effects, total_damage, total_healing)`` — three values, not a
    signed net. A net of zero is ambiguous between "nothing happened" and "2 damage and 2
    healing"; callers (the game loop) word those two outcomes differently, so both totals
    are always reported separately.

    Every effect has ``remaining_turns`` reduced by 1, including the tick that drops it to
    0 — that tick still contributes the effect's ``magnitude`` before the entry is dropped
    from the surviving tuple (poison still deals its damage, and regeneration still heals,
    on the tick that removes them). Order is preserved.

    Each effect's ``magnitude`` is routed by its ``kind``:

    - ``POISONED`` adds to ``total_damage``.
    - ``REGENERATING`` adds to ``total_healing``.
    - ``ENRAGED`` (or any other kind) adds to neither — it is a duration flag only.

    Pure — ``effects`` is never mutated.
    """
    surviving: tuple[StatusEffect, ...] = ()
    total_damage = 0
    total_healing = 0
    for effect in effects:
        if effect.kind is StatusKind.POISONED:
            total_damage += effect.magnitude
        elif effect.kind is StatusKind.REGENERATING:
            total_healing += effect.magnitude
        remaining = effect.remaining_turns - 1
        if remaining > 0:
            surviving += (
                StatusEffect(
                    kind=effect.kind,
                    remaining_turns=remaining,
                    magnitude=effect.magnitude,
                ),
            )
    return surviving, total_damage, total_healing
