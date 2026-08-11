"""Monsters: the bestiary, the two-state AI, and the spawn rule (CONTRACT-v5 §24).

**This module decides; it does not act.** :func:`plan_action` returns an
:class:`NpcAction` *intent* — wait, move here, attack that — and ``game.py`` carries it
out. It is the same split that put the planners in :mod:`roguelike.activity` and
``advance`` in ``game.py``, and it is what keeps the import graph acyclic: nothing here
imports ``combat``, ``game``, ``render``, ``keys`` or ``events`` (CONTRACT-v5 §10 v5).

All coordinates are ``(x, y)`` with the origin at the top-left; ``x`` grows right and
``y`` grows down (CONTRACT §0.1). Every distance in this module is **Chebyshev** —
``max(|dx|, |dy|)`` — because movement is eight-way, so a diagonal neighbour is one step
away exactly like an orthogonal one.

Derived stats are not stored
----------------------------
:class:`SpeciesData` carries only the three primary stats; ``max_hp``, ``speed``,
``evasion`` and ``block`` all fall out of :func:`roguelike.stats.derive` with no special
cases (CONTRACT-v5 §24.1). Writing them down a second time is how two HP formulas start
to drift, so they are written down once, in ``stats.py``.

Randomness
----------
No ``random.Random`` is ever created or stored here (CONTRACT-v5 §0.12) — the caller
derives a fresh generator per roll and passes it in. ``random`` is imported only under
``TYPE_CHECKING``, so a module-level draw is not merely forbidden but impossible.

The two functions consume ``rng`` by a fixed protocol, stated so a caller can reason
about seed reuse:

* :func:`plan_action` draws **nothing at all** when it hunts. When it wanders it draws
  ``rng.randrange(2)`` for the wait/move coin flip (salt 4, per §0.12) and then, only if
  the flip said move and some neighbour is legal, one ``rng.choice(...)``.
* :func:`spawn_npcs` draws, per placed NPC and in this order, one ``rng.choice(...)`` for
  the cell, one ``rng.randrange(len(Species))`` for the species and one
  ``rng.randrange(0, 100)`` for the starting energy.

No new pathfinding
------------------
Hunting calls :func:`roguelike.pathfind.find_path` and re-plans every turn, exactly as
the player's travel activity does. There is no Dijkstra map, no flow field, no path cache
and no ``path`` field on :class:`NPC`. That is measured, not stylistic: per-hunter A\\*
costs ~0.5 ms and a shared Dijkstra map only overtakes ``N`` separate searches at about
12–15 simultaneous hunters, while a level carries six (RESEARCH-v5 §8).

Purity
------
:func:`plan_action` and :func:`spawn_npcs` are pure: no mutation of any argument, no I/O,
no module-level mutable state, no caching between calls, and no dependence on set
iteration order. All arithmetic is integer (CONTRACT-v5 §0.13); there is no float
anywhere in this file. Never touches curses.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from roguelike.fov import has_line_of_sight
from roguelike.level import Level
from roguelike.pathfind import Coord, find_path
from roguelike.stats import Actor, Stats, derive
from roguelike.world import is_passable, is_planning_passable

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from random import Random

__all__ = [
    "Species",
    "SpeciesData",
    "AiState",
    "NPC",
    "NpcActionKind",
    "NpcAction",
    "SPECIES_DATA",
    "PERCEPTION_RADIUS",
    "FORGET_TICKS",
    "MONSTERS_PER_LEVEL",
    "SPAWN_SAFE_RADIUS",
    "SPAWN_MIN_SEPARATION",
    "plan_action",
    "spawn_npcs",
]


class Species(Enum):
    """The complete bestiary (CONTRACT-v5 §24.1). Four members; do not add a fifth."""

    RAT = auto()
    JACKAL = auto()
    GIANT_BAT = auto()
    CAVE_SNAKE = auto()


@dataclass(frozen=True)
class SpeciesData:
    """Everything that is true of a species rather than of an individual monster.

    ``name`` is lower-case on purpose: messages read ``The {name} hits you.``

    There is deliberately **no** ``max_hp``, ``speed``, ``evasion`` or ``block`` field —
    those are :func:`roguelike.stats.derive` of :attr:`stats` (CONTRACT-v5 §24.1).
    """

    name: str
    glyph: str
    stats: Stats
    attack_min: int
    attack_max: int
    xp_value: int
    poison_chance: int = 0


class AiState(Enum):
    """The two states a monster's mind can be in (CONTRACT-v5 §24.2)."""

    WANDERING = auto()
    HUNTING = auto()


@dataclass(frozen=True)
class NPC:
    """One monster. Immutable — a turn produces a new one, never a mutation.

    ``memory`` counts the ticks since this NPC last had line of sight to the player; past
    :data:`FORGET_TICKS` the caller reverts it to :attr:`AiState.WANDERING`. Both
    ``ai_state`` and ``memory`` are maintained by ``game.py``: :func:`plan_action` reads
    them and never decides them.

    There is no ``path`` field and no ``hostile`` flag (everything is hostile).
    """

    actor_id: int
    species: Species
    actor: Actor
    position: Coord
    energy: int = 0
    ai_state: AiState = AiState.WANDERING
    memory: int = 0


class NpcActionKind(Enum):
    """The three things a monster can intend to do in one action."""

    WAIT = auto()
    MOVE = auto()
    ATTACK = auto()


@dataclass(frozen=True)
class NpcAction:
    """An *intent*, executed by ``game.py``.

    ``target`` is the destination cell for ``MOVE``, the victim's cell for ``ATTACK``,
    and ``None`` for ``WAIT``.
    """

    kind: NpcActionKind
    target: Coord | None = None


SPECIES_DATA: dict[Species, SpeciesData] = {
    Species.RAT: SpeciesData(
        name="rat",
        glyph="r",
        stats=Stats(str_=4, agi=14, vit=3),
        attack_min=1,
        attack_max=3,
        xp_value=5,
    ),
    Species.JACKAL: SpeciesData(
        name="jackal",
        glyph="j",
        stats=Stats(str_=8, agi=13, vit=5),
        attack_min=2,
        attack_max=4,
        xp_value=10,
    ),
    Species.GIANT_BAT: SpeciesData(
        name="giant bat",
        glyph="B",
        stats=Stats(str_=3, agi=18, vit=2),
        attack_min=1,
        attack_max=2,
        xp_value=8,
    ),
    Species.CAVE_SNAKE: SpeciesData(
        name="cave snake",
        glyph="s",
        stats=Stats(str_=6, agi=8, vit=5),
        attack_min=2,
        attack_max=4,
        xp_value=12,
        poison_chance=30,
    ),
}
"""The bestiary, one entry per :class:`Species` (CONTRACT-v5 §24.1).

A constant lookup table. The contract types it as a plain ``dict``, so it is one; this
module never mutates it and no caller should either.
"""

PERCEPTION_RADIUS: int = 10
"""A wandering monster notices the player only within this Chebyshev distance, *and*
only with line of sight (CONTRACT-v5 §24.2)."""

FORGET_TICKS: int = 5
"""How many ticks a hunting monster keeps chasing after losing sight of the player before
the caller reverts it to :attr:`AiState.WANDERING` (CONTRACT-v5 §24.2)."""

MONSTERS_PER_LEVEL: int = 6
"""How many monsters :func:`spawn_npcs` places, rules permitting (CONTRACT-v5 §24.4)."""

SPAWN_SAFE_RADIUS: int = 8
"""No monster spawns within this Chebyshev distance of ``level.player_start``."""

SPAWN_MIN_SEPARATION: int = 5
"""No two spawned monsters start within this Chebyshev distance of each other.

Not a nicety: a baseline player beats one jackal 97% of the time and **two jackals 0%**
of the time — 600 simulated fights, zero wins (RESEARCH-v5 §5). A spawn rule that can
drop a pair side by side kills level-1 characters through no fault of their own.
"""


#: The four orthogonal neighbour offsets, as ``(dx, dy)``, in a fixed order. A wandering
#: monster shuffles about orthogonally (CONTRACT-v5 §24.2); hunting uses the eight-way
#: deltas inside :func:`roguelike.pathfind.find_path`. Fixed order, never regenerated:
#: it is part of what makes a seeded wander reproducible.
_ORTHOGONAL: tuple[Coord, ...] = ((0, -1), (1, 0), (0, 1), (-1, 0))

#: :class:`Species` in definition order. Enum iteration order *is* definition order, so
#: this is stable across processes — unlike iterating a set of members would be.
_SPECIES_ORDER: tuple[Species, ...] = tuple(Species)

#: Spawning judges passability with no door open at all (CONTRACT-v5 §24.4), so nothing
#: ever starts standing in a doorway.
_NO_DOORS: frozenset[Coord] = frozenset()

#: The energy an action costs — ``ENERGY_THRESHOLD`` in CONTRACT-v5 §7 v5, which lives in
#: ``game.py`` because the turn loop owns it. Starting energy is drawn uniformly below it
#: (§24.4). Private and duplicated rather than imported: ``npc.py`` must not import
#: ``game`` (§10 v5), and exporting a second public name for it would invite the two
#: copies to drift in opposite directions.
_ENERGY_THRESHOLD: int = 100


def _chebyshev(a: Coord, b: Coord) -> int:
    """Eight-way step distance between two cells: ``max(|dx|, |dy|)``.

    The right metric for this game because a diagonal move is one move
    (CONTRACT-v4 §11). Pure integer arithmetic; never raises.
    """
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def plan_action(
    rng: "Random",
    npc: NPC,
    level: Level,
    open_doors: frozenset[Coord],
    occupied: frozenset[Coord],
    player: Coord,
) -> NpcAction:
    """Decide what ``npc`` intends to do this action (CONTRACT-v5 §24.2).

    **HUNTING** — Chebyshev distance 1 from the player gives ``ATTACK`` with
    ``target=player``, from all eight adjacent cells including the diagonals; ``game.py``
    turns that into a melee resolution. Otherwise :func:`roguelike.pathfind.find_path`
    routes to the player and the result is ``MOVE`` to ``path[1]``. No path — walled off,
    or the only corridor plugged by another monster — gives ``WAIT``, never an exception
    (CONTRACT-v5 §11 v5).

    **WANDERING** — the monster first looks for the player:
    ``has_line_of_sight(level, open_doors, npc.position, player)`` **and** Chebyshev
    distance ``<= PERCEPTION_RADIUS``. The argument order is
    ``(observer, target)`` and the **NPC is the observer**; permissive line of sight is
    measurably asymmetric, so swapping those is a behaviour change (CONTRACT-v5 §14 v5).
    On success it returns the action it would return while hunting — spotting the player
    and then wasting the turn shuffling sideways would be a visible glitch. Otherwise it
    flips a coin between ``WAIT`` and ``MOVE`` to a uniformly chosen legal orthogonal
    neighbour, and ``WAIT``s if no neighbour is legal.

    **The state transition is the caller's.** This function is pure and returns only an
    action: it never writes ``ai_state`` or ``memory``. ``game.py`` performs the
    ``WANDERING -> HUNTING`` switch (with ``memory = 0``) on the same perception rule, and
    the ``HUNTING -> WANDERING`` revert once ``memory`` passes :data:`FORGET_TICKS`.

    **A legal ``MOVE`` target** is :func:`roguelike.world.is_planning_passable` — so a
    closed door *is* legal, and the monster bumps it open exactly as the player does — and
    **not in** ``occupied``. The same exclusion is applied inside the predicate handed to
    ``find_path``, or a hunter would happily plan straight through the monster next to it.
    Monsters neither stack nor swap places.

    **Not restricted to ``explored``.** There is no ``explored`` parameter and there is
    not meant to be one: a monster is not fogged, it lives on this level and knows it.
    That is the deliberate opposite of how the player's travel activity plans.

    Args:
        rng: A generator supplied by the caller, used only for the wander coin flip and
            the neighbour choice. Nothing is drawn while hunting. Never stored.
        npc: The monster deciding. Read-only.
        level: The map. Read-only.
        open_doors: The doors currently open, for both sight and passability.
        occupied: Every cell holding another actor — every other NPC and the player.
            ``npc``'s own cell may be present or absent; it makes no difference.
        player: The player's cell, both the perception target and the hunt goal.

    Returns:
        A fresh :class:`NpcAction`. Never ``None``, and never raises.
    """
    if npc.ai_state is AiState.HUNTING:
        return _hunt(npc, level, open_doors, occupied, player)

    # Chebyshev first: it is a subtraction, while line of sight is 0.167 ms (RESEARCH-v5
    # §8), and every monster asks this every turn whether or not the player can see it.
    if _chebyshev(npc.position, player) <= PERCEPTION_RADIUS and has_line_of_sight(
        level, open_doors, npc.position, player
    ):
        return _hunt(npc, level, open_doors, occupied, player)

    return _wander(rng, npc, level, open_doors, occupied)


def spawn_npcs(
    rng: "Random", level: Level, first_actor_id: int = 1
) -> tuple[NPC, ...]:
    """Populate ``level`` with up to :data:`MONSTERS_PER_LEVEL` monsters (§24.4).

    Placement rules, all binding and none of them ever relaxed:

    * only on cells passable with **no door open** — floor and staircases, never a
      doorway;
    * never within :data:`SPAWN_SAFE_RADIUS` (8, Chebyshev) of ``level.player_start``;
    * never within :data:`SPAWN_MIN_SEPARATION` (5, Chebyshev) of an already-placed
      monster;
    * species drawn uniformly from the four;
    * ``energy`` drawn uniformly from ``range(0, 100)`` — **not** 0. Identical starting
      energy makes a pack act on the same ticks and move as one organism; staggering it
      costs one line;
    * ``hp`` starts at the species' full derived ``max_hp`` and ``status_effects`` empty;
    * ``actor_id`` runs sequentially from ``first_actor_id``, which defaults to 1 because
      **the player is permanently ``actor_id`` 0** (CONTRACT-v5 §0.12).

    **Termination is structural, not a retry budget.** Each of the at most
    :data:`MONSTERS_PER_LEVEL` passes chooses from the list of cells that are still legal
    and then filters that list down, so there is no rejection sampling to spin on: the
    work is bounded by ``MONSTERS_PER_LEVEL * len(candidates)`` on every input, including
    a map where nothing can be placed. When the rules leave no legal cell the function
    stops and returns **fewer** monsters — on a tiny level, none at all — rather than
    relaxing a radius (CONTRACT-v5 §11 v5).

    Choosing from the legal cells rather than guessing and re-rolling also means a
    placement is missed only when the map genuinely has no room left for it, never
    because a fixed number of dice throws happened to miss.

    Args:
        rng: A generator supplied by the caller — for a real level, the one seeded from
            that level's own seed, so a level's population is as reproducible as its
            rooms. Never stored.
        level: The map to populate. Read-only.
        first_actor_id: The ``actor_id`` of the first monster placed.

    Returns:
        A tuple ordered by ``actor_id``, of length ``MONSTERS_PER_LEVEL`` or shorter.
        Never raises.
    """
    # Deterministic scan order — a list built row by row, never a set — so nothing here
    # can depend on hash randomisation.
    candidates: list[Coord] = [
        (x, y)
        for y in range(level.height)
        for x in range(level.width)
        if is_passable(level, _NO_DOORS, x, y)
        and _chebyshev((x, y), level.player_start) >= SPAWN_SAFE_RADIUS
    ]

    npcs: list[NPC] = []
    for index in range(MONSTERS_PER_LEVEL):
        if not candidates:
            break
        position = rng.choice(candidates)
        species = _SPECIES_ORDER[rng.randrange(len(_SPECIES_ORDER))]
        energy = rng.randrange(0, _ENERGY_THRESHOLD)
        data = SPECIES_DATA[species]
        npcs.append(
            NPC(
                actor_id=first_actor_id + index,
                species=species,
                actor=Actor(
                    stats=data.stats,
                    hp=derive(data.stats).max_hp,
                    status_effects=(),
                ),
                position=position,
                energy=energy,
                ai_state=AiState.WANDERING,
                memory=0,
            )
        )
        candidates = [
            cell
            for cell in candidates
            if _chebyshev(cell, position) >= SPAWN_MIN_SEPARATION
        ]

    return tuple(npcs)


def _hunt(
    npc: NPC,
    level: Level,
    open_doors: frozenset[Coord],
    occupied: frozenset[Coord],
    player: Coord,
) -> NpcAction:
    """The HUNTING half of :func:`plan_action`. Draws no randomness at all."""
    if _chebyshev(npc.position, player) == 1:
        return NpcAction(NpcActionKind.ATTACK, player)

    def passable(x: int, y: int) -> bool:
        # The player's own cell is the goal, and it is in `occupied`. Excluding it would
        # make the goal unreachable and turn every hunter into a waiter, so the goal is
        # exempt — and only the goal. It can never be the returned step: `path[1]` is
        # adjacent to the NPC, and an adjacent player was already answered with ATTACK.
        if (x, y) == player:
            return True
        if (x, y) in occupied:
            return False
        return is_planning_passable(level, open_doors, x, y)

    path = find_path(passable, npc.position, frozenset((player,)))
    # `find_path` returns `[start]` when the NPC somehow stands on the player, which is
    # not a step; treat it like no path rather than indexing off the end.
    if path is None or len(path) < 2:
        return NpcAction(NpcActionKind.WAIT)
    return NpcAction(NpcActionKind.MOVE, path[1])


def _wander(
    rng: "Random",
    npc: NPC,
    level: Level,
    open_doors: frozenset[Coord],
    occupied: frozenset[Coord],
) -> NpcAction:
    """The WANDERING half of :func:`plan_action`: a coin flip, then a random step."""
    if rng.randrange(2) == 0:
        return NpcAction(NpcActionKind.WAIT)

    x, y = npc.position
    choices = [
        (x + dx, y + dy)
        for dx, dy in _ORTHOGONAL
        if _is_legal_move(level, open_doors, occupied, (x + dx, y + dy))
    ]
    if not choices:
        return NpcAction(NpcActionKind.WAIT)
    return NpcAction(NpcActionKind.MOVE, rng.choice(choices))


def _is_legal_move(
    level: Level,
    open_doors: frozenset[Coord],
    occupied: frozenset[Coord],
    cell: Coord,
) -> bool:
    """True iff a monster may intend to step onto ``cell``.

    Planning-passable — a closed door counts, since bumping it opens it — and not held by
    another actor.
    """
    if cell in occupied:
        return False
    return is_planning_passable(level, open_doors, cell[0], cell[1])
