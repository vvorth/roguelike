"""Everything the player can carry, wield or drink (CONTRACT-v6 §25).

A leaf: imports nothing from :mod:`roguelike`, and no :mod:`curses`. Everything here is
testable with no engine, no level and no terminal.

The module is two halves. The first is *vocabulary*: the enums (:class:`ItemKind`,
:class:`WeaponKind`, :class:`DamageType`, :class:`Resistance`, :class:`Grade`), the three
frozen item records (:class:`Weapon`, :class:`Shield`, :class:`Consumable`) and one module
constant for every row of §25.1's binding tables. The second is *the pack*:
:class:`Inventory` and the four pure functions :func:`equip`, :func:`unequip`, :func:`add`
and :func:`drop`, which move items between the pack and the three equipment slots.

**The numbers are measured, not tuned.** One point of melee damage is worth 20–40 percentage
points of floor-clear survival: 1–4 clears 2.2% of floors, the shipped 2–5 dagger clears
45.6%, 4–8 clears 98.3% (CONTRACT-v6 §0.1). That is why the three weapon tiers here sit only
one point apart, and why :data:`DAGGER` stays 2–5 and :data:`SHORTBOW` stays 1–4 exactly as
v5 shipped them. Shield block chances are 10/18/25 for the same reason (§0.4): the 15/25/35
an earlier draft proposed measured too strong.

This module *declares* :class:`Resistance` but never applies it — the multiplier is
:mod:`roguelike.combat`'s, applied to the raw damage roll (§26.2), and which species resists
what is :mod:`roguelike.npc`'s (§26.3). A damage type is a property of a weapon, so the whole
damage vocabulary lives here rather than in the frozen :mod:`roguelike.stats`.

Out of scope by decision, not by omission: weight, encumbrance, stacking, identification,
curses, enchantments, an ``Item`` base class beyond the three records above, food, hunger,
ammunition and shops. There is no food and no hunger clock (§25.1).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum, IntEnum, auto

__all__ = [
    "ItemKind",
    "WeaponKind",
    "DamageType",
    "Resistance",
    "Grade",
    "Weapon",
    "Shield",
    "Consumable",
    "Inventory",
    "CARRY_LIMIT",
    "CLUB",
    "DAGGER",
    "SWORD",
    "SLING",
    "SHORTBOW",
    "LONGBOW",
    "BUCKLER",
    "KITE_SHIELD",
    "TOWER_SHIELD",
    "POTION_OF_HEALING",
    "BANDAGE",
    "equip",
    "unequip",
    "add",
    "drop",
]


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class ItemKind(Enum):
    """The complete item vocabulary (CONTRACT-v6 §25). Do not add members.

    A plain :class:`~enum.Enum`: item kinds have no order, unlike :class:`Grade`. The
    three records themselves carry no ``kind`` field — the contract's dataclasses do not
    have one — so a caller classifies an item by its type. This enum is the shared name
    for those three categories.
    """

    WEAPON = auto()
    SHIELD = auto()
    CONSUMABLE = auto()


class WeaponKind(Enum):
    """The complete weapon-kind vocabulary (CONTRACT-v5 §21). Do not add members.

    Unchanged from v5. It also decides which slot :func:`equip` puts a weapon in.
    """

    MELEE = auto()
    RANGED = auto()


class DamageType(Enum):
    """What a weapon does to a body (CONTRACT-v6 §25). Do not add members.

    A plain :class:`~enum.Enum`: no type is "more" than another. Which species resists
    which type is the bestiary's business (§26.3) — the cave snake resists ``PIERCE``,
    which is exactly why a ``BLUNT`` club is worth carrying alongside the starting dagger.
    """

    SLASH = auto()
    PIERCE = auto()
    BLUNT = auto()


class Resistance(IntEnum):
    """How well a body shrugs off one damage type (CONTRACT-v6 §26.1).

    Four coarse tiers rather than a percentage, because percentages are unusable at these
    numbers: on a 2–5 roll, 25% and 33% resistance produce the identical set of outcomes,
    66% and 75% are both a flat 1, and the *same* 50% yields average damage anywhere from
    1.25 to 2.50 purely from where in the pipeline it is applied (§0.3).

    An :class:`~enum.IntEnum`, ordered best-for-the-defender to worst, so the members
    compare directly: ``Resistance.IMMUNE < Resistance.NORMAL`` reads as "takes less from
    this than a normal body would". The multiplier each tier means — 0, halved rounding
    down, unchanged, doubled — is applied by :mod:`roguelike.combat` to the raw damage
    roll and by nothing here (§26.2).
    """

    IMMUNE = 0
    RESISTANT = 1
    NORMAL = 2
    VULNERABLE = 3


class Grade(IntEnum):
    """How well an item was made (CONTRACT-v6 §25).

    An :class:`~enum.IntEnum`, ordered worst to best, so ``Grade.CRUDE < Grade.FINE``
    reads as "shoddier than". The ordering is the point: :mod:`roguelike.loot` rolls a
    grade per depth band (§27.1) and the renderer names it, both of which want to compare
    two grades rather than look one up.

    Grade is a *label*, never a modifier. It does not add damage, block or anything else;
    the numbers in §25.1's tables are the whole of an item's effect.
    """

    CRUDE = 0
    STANDARD = 1
    FINE = 2


# ---------------------------------------------------------------------------
# The three item records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Weapon:
    """A static weapon definition.

    ``range`` is a Chebyshev distance; a ``MELEE`` weapon's range is always 1.

    ``damage_type`` and ``grade`` are **appended with defaults** (CONTRACT-v6 §25), so
    every v5 construction keeps working unchanged: ``Weapon("dagger", WeaponKind.MELEE,
    2, 5)`` still builds, and still builds a ``SLASH``/``STANDARD`` weapon.
    """

    name: str
    kind: WeaponKind
    damage_min: int
    damage_max: int
    range: int = 1
    damage_type: DamageType = DamageType.SLASH
    grade: Grade = Grade.STANDARD


@dataclass(frozen=True)
class Shield:
    """A static shield definition.

    ``block_chance`` is a whole percent, and a shield is *only* ever a chance to negate a
    blow entirely — **no item in this project may grant flat damage reduction**. At just
    +1 flat block, 58% of all monster damage rolls already floor to 1 and the player wins
    100% of jackal fights (CONTRACT-v6 §0.2).

    A blocked attack deals no damage and no poison: the shield stops the venom with the
    fang (§23.5).
    """

    name: str
    block_chance: int
    grade: Grade = Grade.STANDARD


@dataclass(frozen=True)
class Consumable:
    """A static consumable definition — one use, then it is gone.

    Two shapes, both expressible in these three fields: an instant heal (``heal``), and a
    regeneration effect that ticks ``regen_magnitude`` HP for ``regen_turns`` turns. A
    consumable may in principle carry both; neither shipped item does.

    There is no food and no hunger (§25.1). A consumable is never equipped — it is used,
    which is why :func:`equip` refuses one.
    """

    name: str
    heal: int = 0
    regen_turns: int = 0
    regen_magnitude: int = 0


# ---------------------------------------------------------------------------
# The item tables — CONTRACT-v6 §25.1, binding
# ---------------------------------------------------------------------------
#
# | Weapon   | kind   | damage | type   | grade    | range |
# |----------|--------|--------|--------|----------|-------|
# | club     | MELEE  | 2-4    | BLUNT  | CRUDE    | 1     |
# | dagger   | MELEE  | 2-5    | PIERCE | STANDARD | 1     |
# | sword    | MELEE  | 3-5    | SLASH  | FINE     | 1     |
# | sling    | RANGED | 2-4    | BLUNT  | CRUDE    | 5     |
# | shortbow | RANGED | 1-4    | PIERCE | STANDARD | 6     |
# | longbow  | RANGED | 3-5    | PIERCE | FINE     | 8     |

CLUB: Weapon = Weapon(
    "club", WeaponKind.MELEE, 2, 4, range=1,
    damage_type=DamageType.BLUNT, grade=Grade.CRUDE,
)

#: The reference weapon. **2–5 is measured, not chosen** (CONTRACT-v6 §0.1): it clears 45.6%
#: of floors, where 1–4 clears 2.2% and 4–8 clears 98.3%. The entire game's balance is
#: expressed relative to this line. Do not retune it.
DAGGER: Weapon = Weapon(
    "dagger", WeaponKind.MELEE, 2, 5, range=1,
    damage_type=DamageType.PIERCE, grade=Grade.STANDARD,
)

SWORD: Weapon = Weapon(
    "sword", WeaponKind.MELEE, 3, 5, range=1,
    damage_type=DamageType.SLASH, grade=Grade.FINE,
)

SLING: Weapon = Weapon(
    "sling", WeaponKind.RANGED, 2, 4, range=5,
    damage_type=DamageType.BLUNT, grade=Grade.CRUDE,
)

#: The starting ranged weapon. **1–4 is measured, not chosen**; it keeps its v5 damage
#: exactly (CONTRACT-v6 §25.1). Do not retune it.
SHORTBOW: Weapon = Weapon(
    "shortbow", WeaponKind.RANGED, 1, 4, range=6,
    damage_type=DamageType.PIERCE, grade=Grade.STANDARD,
)

LONGBOW: Weapon = Weapon(
    "longbow", WeaponKind.RANGED, 3, 5, range=8,
    damage_type=DamageType.PIERCE, grade=Grade.FINE,
)

# | Shield       | block | grade    |
# |--------------|-------|----------|
# | buckler      | 10%   | CRUDE    |
# | kite shield  | 18%   | STANDARD |
# | tower shield | 25%   | FINE     |
#
# Measured end to end against the shipped dagger: 45.6% floor clears bare, 60.9% / 77.7% /
# 85.8% with these three (§0.4). The 15/25/35 an earlier draft proposed measured 72/86/94% —
# too strong.

BUCKLER: Shield = Shield("buckler", 10, grade=Grade.CRUDE)
KITE_SHIELD: Shield = Shield("kite shield", 18, grade=Grade.STANDARD)
TOWER_SHIELD: Shield = Shield("tower shield", 25, grade=Grade.FINE)

# | Consumable        | effect                            |
# |-------------------|-----------------------------------|
# | potion of healing | heal=10                           |
# | bandage           | regen_turns=5, regen_magnitude=3  |

POTION_OF_HEALING: Consumable = Consumable("potion of healing", heal=10)
BANDAGE: Consumable = Consumable("bandage", regen_turns=5, regen_magnitude=3)


# ---------------------------------------------------------------------------
# The pack
# ---------------------------------------------------------------------------

#: How many items ``Inventory.carried`` holds. A hard cap: :func:`add` refuses beyond it.
CARRY_LIMIT: int = 20

#: The three equipment slot names :func:`unequip` accepts. Slot membership is decided by an
#: item's type, never stored on the item.
_SLOTS: tuple[str, ...] = ("melee", "ranged", "shield")


@dataclass(frozen=True)
class Inventory:
    """A pack and three equipment slots (CONTRACT-v6 §25).

    ``carried`` is an ordered tuple of at most :data:`CARRY_LIMIT` items — the order is the
    order the inventory screen lists them in, so it is meaningful and the four functions
    preserve it. An item is in exactly one place: carried, or in a slot, never both.

    **Every slot may be ``None``** — bare-handed is representable, and what it means is
    :mod:`roguelike.game`'s business (§7.15: a ``None`` melee slot attacks for 1–2 BLUNT).

    Frozen, like everything else here. The four module functions return new inventories;
    nothing mutates one.
    """

    carried: tuple[object, ...] = ()
    melee: Weapon | None = None
    ranged: Weapon | None = None
    shield: Shield | None = None


def _slot_for(item: object) -> str:
    """Which slot ``item`` belongs in, or raise :class:`ValueError` if it belongs in none.

    A :class:`Weapon` goes to ``"melee"`` or ``"ranged"`` by its :class:`WeaponKind`; a
    :class:`Shield` goes to ``"shield"``. Everything else — a :class:`Consumable`, or any
    other object that found its way into the pack — is not equipment.
    """
    if isinstance(item, Weapon):
        return "ranged" if item.kind is WeaponKind.RANGED else "melee"
    if isinstance(item, Shield):
        return "shield"
    raise ValueError(f"not equippable: {item!r}")


def equip(inventory: Inventory, item: object) -> Inventory:
    """Move ``item`` out of ``carried`` and into its slot (CONTRACT-v6 §25.2).

    A :class:`Weapon` goes to ``melee`` or ``ranged`` by its :class:`WeaponKind`; a
    :class:`Shield` goes to ``shield``. Whatever occupied that slot returns to the end of
    ``carried``, so the pack's length never changes and :data:`CARRY_LIMIT` cannot be
    breached by equipping.

    Pure: returns a new :class:`Inventory` and mutates nothing.

    Raises :class:`ValueError` if ``item`` is not in ``carried`` (§11 v6), and likewise if
    it is not equipment at all — a :class:`Consumable` is used, not worn.

    ``carried`` is searched by equality, so equipping one of two identical daggers removes
    exactly one of them.
    """
    slot = _slot_for(item)

    carried = list(inventory.carried)
    try:
        index = carried.index(item)
    except ValueError:
        raise ValueError(f"item not carried: {item!r}") from None
    del carried[index]

    displaced = getattr(inventory, slot)
    if displaced is not None:
        carried.append(displaced)

    return replace(inventory, carried=tuple(carried), **{slot: item})


def unequip(inventory: Inventory, slot: str) -> Inventory:
    """Empty ``slot`` and return what was in it to ``carried`` (CONTRACT-v6 §25.2).

    ``slot`` is one of ``"melee"``, ``"ranged"`` or ``"shield"``. An already-empty slot is
    a no-op: the same inventory comes back, because bare-handed is a legal state and
    asking for it twice is not an error.

    Pure: returns a new :class:`Inventory` and mutates nothing.

    Raises :class:`ValueError` for a slot name that does not exist — that is a caller bug,
    not a game state.

    The one edge the contract does not name: unequipping is the only operation that grows
    ``carried``, so with a pack already at :data:`CARRY_LIMIT` there is nowhere for the
    item to go. It is a no-op then, keeping the cap true rather than quietly exceeding it.
    """
    if slot not in _SLOTS:
        raise ValueError(f"no such slot: {slot!r}")

    item = getattr(inventory, slot)
    if item is None:
        return inventory
    if len(inventory.carried) >= CARRY_LIMIT:
        return inventory

    return replace(inventory, carried=inventory.carried + (item,), **{slot: None})


def add(inventory: Inventory, item: object) -> tuple[Inventory, bool]:
    """Append ``item`` to ``carried`` (CONTRACT-v6 §25.2).

    Returns ``(inventory, True)`` with the item appended, or ``(inventory, False)`` with
    **the input returned unchanged** when the pack already holds :data:`CARRY_LIMIT`
    items (§11 v6). The flag is what tells :mod:`roguelike.game` whether to emit
    ``PICKED_UP`` or ``PACK_FULL``.

    Pure: returns a new :class:`Inventory` on success and mutates nothing.
    """
    if len(inventory.carried) >= CARRY_LIMIT:
        return inventory, False
    return replace(inventory, carried=inventory.carried + (item,)), True


def drop(inventory: Inventory, index: int) -> tuple[Inventory, object | None]:
    """Remove the item at ``index`` from ``carried`` and hand it back (§25.2).

    Returns ``(inventory, item)``, or ``(inventory, None)`` with **nothing changed** when
    ``index`` is out of range (§11 v6). A negative index counts as out of range rather
    than counting from the end: the index comes from a letter on the inventory screen, so
    only a bug produces a negative one, and silently dropping the wrong item is worse than
    dropping none.

    Only ``carried`` is touched — an equipped item is unequipped first, by a separate
    call. Pure: returns a new :class:`Inventory` on success and mutates nothing.
    """
    if index < 0 or index >= len(inventory.carried):
        return inventory, None

    carried = list(inventory.carried)
    item = carried.pop(index)
    return replace(inventory, carried=tuple(carried)), item
