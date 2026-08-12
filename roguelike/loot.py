"""Chests and depth-scaled loot (CONTRACT-v6 §27).

A near-leaf: imports only :mod:`roguelike.items` and :mod:`roguelike.level`. It must not
import :mod:`roguelike.game`, :mod:`roguelike.npc`, :mod:`roguelike.combat`,
:mod:`roguelike.render`, :mod:`roguelike.world`, :mod:`roguelike.tiles` or :mod:`curses`
(CONTRACT-v6 §10 v6).

**Chests are not terrain.** :class:`~roguelike.level.Level` is a frozen, generated map;
an opened chest changes, and terrain cannot. This is exactly the problem doors had, solved
the same way: mutable state lives *outside* ``Level`` (CONTRACT-v2 §0.6, the ``open_doors``
precedent). There is no ``Tile.CHEST`` and :mod:`roguelike.generator` is untouched. This
module only *produces* :class:`Chest` values; ``game.py`` is the one that stores them on
``LevelState.chests``, alongside ``npcs`` (§27.3).

Placement follows :func:`roguelike.npc.spawn_npcs` as its model, deliberately: the same
seeded-``Random`` discipline (an ``rng`` is passed in, never created or stored here — see
"Randomness" below), the same Chebyshev safe radius around ``level.player_start``, and the
same termination rule — **choose from the list of cells that are still legal and filter
it; never guess and re-roll.** On a map with no legal cell, :func:`place_chest` returns
``None``. It never hangs and never relaxes :data:`CHEST_SAFE_RADIUS` to make a placement
happen.

Detecting a door without importing ``Tile``
--------------------------------------------
"Passable with no door open" (§27.2) excludes doorways, exactly as ``spawn_npcs`` excludes
them via ``world.is_passable(level, frozenset(), x, y)``. This module cannot import
:mod:`roguelike.world` or :mod:`roguelike.tiles` (§10 v6 lists only ``items`` and
``level``), so it cannot compare a tile against ``Tile.DOOR`` directly. It does not need
to: the generator's own guarantees make ``Level.rooms`` sufficient on their own.

* G7 (``generator.py``) carves every cell of a room's floor rectangle — exactly
  ``Room.contains`` — to ``FLOOR`` or a stair. **Never a door.**
* G9a-G9d place every door on some room's *wall ring* — exactly ``Room.on_perimeter`` —
  and G5 makes every two rooms' wall rings (floor-plus-ring, "ring box") pairwise
  disjoint, so a wall-ring cell of one room is never a floor or ring cell of another.
* A corridor cell is, by construction (``_blocked_mask`` in ``generator.py``), outside
  *every* room's ring box, and is always carved as plain ``FLOOR`` — never a door.

Put together: the only walkable cells that ever lie on some room's ``on_perimeter`` are
doors (a wall-ring cell is otherwise ``WALL``, and ``WALL`` is never walkable). So

    is_walkable(x, y) and not any(room.on_perimeter(x, y) for room in level.rooms)

is *exactly* "passable, with every door treated as closed" — with no ``Tile`` import and
no dependence on ``world.py``. It relies on levels built by :func:`roguelike.generator.
generate_level`, which enforces G5/G7/G9a-G9d on every level it returns; a hand-built
``Level`` whose ``rooms`` disagree with its grid is outside that contract, exactly as it
would be for any other consumer of ``Level.rooms``.

Randomness
----------
No ``random.Random`` is ever created or stored here (CONTRACT-v5 §0.12); ``random`` is
imported only under ``TYPE_CHECKING``, so a module-level draw is not merely forbidden but
impossible. :func:`place_chest` draws, in this fixed order, so a caller can reason about
seed reuse the same way the module doc for ``npc.py`` does:

1. one ``rng.randint(1, 100)`` — does a chest spawn at all, at :func:`chest_chance`
   percent. Nothing further is drawn if this roll fails.
2. one ``rng.choice(...)`` over the legal cells — where it goes.
3. one ``rng.randint(1, 3)`` — how many items it holds.
4. per item, in order: one ``rng.randint(1, 100)`` to pick a :class:`~roguelike.items.
   Grade` against :func:`grade_weights`, then one ``rng.randrange(...)`` to pick a
   concrete item uniformly from that grade's pool.

Consumables and grade
----------------------
:class:`~roguelike.items.Consumable` has no ``grade`` field (§25) — grade is meaningful
only for a :class:`~roguelike.items.Weapon` or :class:`~roguelike.items.Shield`. Chests
are stated to be *the only source* of any item (no monster drops, no shops, no floor
litter), so excluding the two consumables from loot entirely would make them unobtainable
in the whole game. This module's resolution, made explicit because §27 is silent on it:
**every grade's item pool is that grade's graded weapons and shields, plus both
consumables, always.** A rolled grade therefore only ever governs which *equipment* tier
is eligible; a consumable is reachable at every depth and every grade roll.

Purity
------
:func:`chest_chance`, :func:`grade_weights` and :func:`place_chest` are pure: no mutation
of any argument, no I/O, no module-level mutable state, no caching between calls. All
arithmetic is integer; there is no float anywhere in this file. Never touches curses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from roguelike.items import (
    BANDAGE,
    BUCKLER,
    CLUB,
    DAGGER,
    KITE_SHIELD,
    LONGBOW,
    POTION_OF_HEALING,
    SHORTBOW,
    SLING,
    SWORD,
    TOWER_SHIELD,
    Grade,
)
from roguelike.level import Level

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from random import Random

__all__ = [
    "Chest",
    "CHEST_CHANCE",
    "CHEST_CHANCE_DEEP",
    "DEEP_FROM",
    "CHEST_SAFE_RADIUS",
    "chest_chance",
    "grade_weights",
    "place_chest",
]


@dataclass(frozen=True)
class Chest:
    """One chest, sitting on one cell of one level (CONTRACT-v6 §27).

    ``contents`` holds 1-3 items, each one of ``items.py``'s eleven module constants
    (:data:`~roguelike.items.CLUB` through :data:`~roguelike.items.BANDAGE`). ``opened``
    defaults ``False``; flipping it and removing an item as it is looted is ``game.py``'s
    job (§7.18), not this module's — a ``Chest`` here is only ever the *initial* roll.
    """

    position: tuple[int, int]
    contents: tuple[object, ...]
    opened: bool = False


#: Percent chance of a chest per level, below :data:`DEEP_FROM` (§27, RESEARCH-v6 §5).
CHEST_CHANCE: int = 12

#: Percent chance of a chest per level, from :data:`DEEP_FROM` onward. Rarer, not more
#: absent: the user's requirement was "very low", not "eventually zero".
CHEST_CHANCE_DEEP: int = 8

#: The depth at which :data:`CHEST_CHANCE` steps down to :data:`CHEST_CHANCE_DEEP`.
DEEP_FROM: int = 10

#: No chest spawns within this Chebyshev distance of ``level.player_start`` — the same
#: idea and the same number as :data:`roguelike.npc.SPAWN_SAFE_RADIUS`, so a chest can
#: never be the first thing on a level's screen either.
CHEST_SAFE_RADIUS: int = 8

# ---------------------------------------------------------------------------
# Grade pools — CONTRACT-v6 §25.1's constants, grouped by roguelike.items.Grade
# ---------------------------------------------------------------------------

#: Every weapon and shield §25.1 defines, each carrying its own ``grade`` field.
_GRADED_ITEMS: tuple[object, ...] = (
    CLUB,
    DAGGER,
    SWORD,
    SLING,
    SHORTBOW,
    LONGBOW,
    BUCKLER,
    KITE_SHIELD,
    TOWER_SHIELD,
)

#: Both consumables. Neither carries a ``grade`` field (see "Consumables and grade"
#: above), so both are appended to every grade's pool below rather than to just one.
_CONSUMABLES: tuple[object, ...] = (POTION_OF_HEALING, BANDAGE)

#: ``Grade`` -> the items :func:`place_chest` may draw when that grade is rolled. Built
#: once at import time from the module constants above, never mutated.
_POOLS: dict[Grade, tuple[object, ...]] = {
    grade: tuple(item for item in _GRADED_ITEMS if item.grade is grade) + _CONSUMABLES
    for grade in (Grade.CRUDE, Grade.STANDARD, Grade.FINE)
}

#: :class:`~roguelike.items.Grade` members in ascending order, indexed by the band a roll
#: falls into inside :func:`_roll_grade`.
_GRADE_ORDER: tuple[Grade, ...] = (Grade.CRUDE, Grade.STANDARD, Grade.FINE)


def _chebyshev(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Eight-way step distance between two cells: ``max(|dx|, |dy|)``.

    Pure integer arithmetic; never raises. Duplicated from ``npc.py`` rather than
    imported, the same call it makes about ``_ENERGY_THRESHOLD``: this module must not
    import ``npc`` (§10 v6), and a second public name for one three-token expression
    would only invite the two copies to drift.
    """
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _is_legal_cell(level: Level, x: int, y: int) -> bool:
    """True iff ``(x, y)`` is passable terrain with every door treated as closed.

    See the module docstring ("Detecting a door without importing ``Tile``") for why
    ``is_walkable`` plus ``not any(room.on_perimeter(...))`` is exactly that predicate on
    a level built by :func:`roguelike.generator.generate_level`.
    """
    if not level.is_walkable(x, y):
        return False
    return not any(room.on_perimeter(x, y) for room in level.rooms)


def chest_chance(depth: int) -> int:
    """Percent chance of a chest on a level at ``depth`` (CONTRACT-v6 §27, §11 v6).

    :data:`CHEST_CHANCE` (12) below :data:`DEEP_FROM`, :data:`CHEST_CHANCE_DEEP` (8) at
    or above it. A plain comparison; never raises for any ``depth``.
    """
    return CHEST_CHANCE_DEEP if depth >= DEEP_FROM else CHEST_CHANCE


def grade_weights(depth: int) -> tuple[int, int, int]:
    """``(crude, standard, fine)`` percent weights for a chest rolled at ``depth``.

    The table is CONTRACT-v6 §27.1, literally — four bands, each row summing to 100, the
    ``fine`` share rising from an "extremely low" 1% at the shallowest depths to 15% at
    depth 10 and beyond. A table rather than a formula: one row can be retuned without
    re-deriving the others. Never raises for any ``depth``.
    """
    if depth <= 3:
        return (80, 19, 1)
    if depth <= 6:
        return (55, 40, 5)
    if depth <= 9:
        return (30, 60, 10)
    return (15, 70, 15)


def _roll_grade(rng: "Random", weights: tuple[int, int, int]) -> Grade:
    """One weighted draw over ``weights`` (which must sum to 100). One ``rng`` call."""
    crude, standard, _fine = weights
    roll = rng.randint(1, 100)
    if roll <= crude:
        return _GRADE_ORDER[0]
    if roll <= crude + standard:
        return _GRADE_ORDER[1]
    return _GRADE_ORDER[2]


def _roll_item(rng: "Random", weights: tuple[int, int, int]) -> object:
    """One item: a grade roll against ``weights``, then a uniform pick from its pool."""
    pool = _POOLS[_roll_grade(rng, weights)]
    return pool[rng.randrange(len(pool))]


def place_chest(rng: "Random", level: Level, depth: int) -> Chest | None:
    """Place at most one chest on ``level`` (CONTRACT-v6 §27.2).

    The roll for *whether* a chest exists at all happens first, at
    :func:`chest_chance` (depth) percent; on failure nothing further is drawn and the
    result is ``None``. On success, the candidate cells are every cell that is
    :func:`_is_legal_cell` **and** at least :data:`CHEST_SAFE_RADIUS` (8, Chebyshev) from
    ``level.player_start`` — built as one deterministic, row-major list, never a set, so
    nothing here can depend on hash randomisation.

    **Termination is structural, not a retry budget.** There is exactly one placement to
    make, chosen by filtering that list rather than by guessing a cell and checking it, so
    the work is bounded by ``level.width * level.height`` on every input, including a map
    where nothing can be placed. When the candidate list comes up empty the function
    returns ``None`` (§11 v6) rather than shrinking :data:`CHEST_SAFE_RADIUS` to make a
    placement happen.

    Contents are 1-3 items (a uniform ``rng.randint(1, 3)``), each rolled independently
    against :func:`grade_weights` (depth) — see the module docstring's "Randomness" for
    the exact draw order, and "Consumables and grade" for how a :class:`~roguelike.items.
    Consumable` fits a system built around :class:`~roguelike.items.Grade`.

    Args:
        rng: A generator supplied by the caller — for a real level, the one seeded from
            that level's own seed, so a level's loot is as reproducible as its rooms and
            its monsters. Never stored.
        level: The map to place a chest on. Read-only.
        depth: The dungeon depth, feeding both :func:`chest_chance` and
            :func:`grade_weights`.

    Returns:
        A single :class:`Chest`, or ``None``. Never raises.
    """
    if rng.randint(1, 100) > chest_chance(depth):
        return None

    candidates = [
        (x, y)
        for y in range(level.height)
        for x in range(level.width)
        if _is_legal_cell(level, x, y)
        and _chebyshev((x, y), level.player_start) >= CHEST_SAFE_RADIUS
    ]
    if not candidates:
        return None

    position = rng.choice(candidates)
    weights = grade_weights(depth)
    count = rng.randint(1, 3)
    contents = tuple(_roll_item(rng, weights) for _ in range(count))
    return Chest(position=position, contents=contents)
