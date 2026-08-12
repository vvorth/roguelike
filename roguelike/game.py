"""Game loop and curses lifecycle — the one place the whole system is wired together.

The loop is split in two so that every game rule is testable without a terminal
(CONTRACT §0.3, §7; CONTRACT-v2 §7; CONTRACT-v3 §7):

1. :func:`step` is **pure**. It takes a :class:`GameState` and a
   :class:`~roguelike.keys.Command` and returns a new :class:`GameState`. It carries all
   of the turn logic — quitting, rejected moves costing no turn, bump-to-open, descent and
   ascent, the turn counter, and when field of view is recomputed — and it never touches
   the terminal and never draws.
2. :func:`run` is a thin shell with no rules of its own: render, draw, read a key,
   translate it, :func:`step`, repeat. Anything decidable without a terminal belongs in
   :func:`step`.

This module holds the state; :mod:`roguelike.render` draws it. The two never meet:
:class:`GameState` is defined here and imported by nothing, because the renderer is handed
primitives instead — a :class:`~roguelike.level.Level`, a position, three frozensets and a
:class:`~roguelike.render.Chrome` of finished strings (v1 BRIEF Q14, CONTRACT-v3 §4). That
is what let the renderer be written by someone who never spoke to this module's author,
and it is why nothing here writes to the screen.

v2 added fog of war and openable doors. **v3 makes the dungeon multi-level**, and the
three ideas that carry it all land here:

* **Levels line up.** Descending passes the coordinate the player descended *from* to
  :func:`roguelike.dungeon.level_for` as ``required_up``, and the generator anchors the
  new level's up-staircase exactly there (G14). The player's ``(x, y)`` does not change
  across a descent — the world changes underneath them. Ascending is the mirror: you
  arrive on the down-staircase you originally used.
* **Levels persist.** :attr:`GameState.saved` maps depth to the
  :class:`LevelState` of a level the player has left. Terrain could be regenerated —
  generation is deterministic — but ``explored`` and ``open_doors`` could not, and
  climbing back up must find the level as you left it: same fog, same opened doors. Since
  the store has to exist for those two sets, it holds the ``Level`` too.
* **Wording lives in one table.** :func:`step` emits structured
  :class:`~roguelike.events.Event` values and never a sentence;
  :func:`roguelike.events.message_for` turns them into text, and it is called where the
  text is needed — building :class:`~roguelike.render.Chrome` in :func:`run`, and once for
  :attr:`GameState.outcome`, which is the one string that has to outlive the curses
  window.

**The event rule** (CONTRACT-v3 §7.1): the returned state's ``events`` is what *this*
command produced, whenever the command produced any event or consumed a turn. Otherwise
the state is returned unchanged — so the previous message is still there, and "the message
stays on screen until another turn" falls straight out of the existing turn rule rather
than needing any new machinery. Walking into a wall costs no turn, so it does not clear
the message; walking anywhere legal does.

**Fog and doors are per level.** ``explored``, ``visible`` and ``open_doors`` on
:class:`GameState` always describe the *current* depth. Every other depth lives in
``saved``, and the current depth is never in it.

Every coordinate here is ``(x, y)`` with the origin at the top-left and ``y`` growing
down, so "up" is ``dy = -1`` (CONTRACT §0.1). The ``(y, x)`` inversion curses needs lives
inside :func:`roguelike.render.draw` and appears nowhere in this file. Note that this
module never asks what *tile* a cell holds — it has no business importing
:mod:`roguelike.tiles` (CONTRACT-v3 §10) — so "am I on a staircase?" is answered from the
level's ``stairs_up``/``stairs_down`` coordinates, which G18 pins to the stair tiles.

``curses`` is imported at module top level, which touches nothing. Terminal initialisation
happens in exactly one place in the whole codebase: the ``curses.wrapper`` call inside
:func:`play`. ``wrapper`` restores the terminal on a normal return *and* on any exception,
``KeyboardInterrupt`` included, which is the clean quit path (v1 BRIEF Q16) — and it is
why :func:`play` prints the farewell *after* ``wrapper`` returns, onto a sane screen
rather than into a torn-down curses window.

**v4 adds automatic navigation**, and it turns on three ideas:

* **One mechanism gives both pacing and cancellation.** ``stdscr.timeout(100)`` was
  measured to deliver both the ten-turns-per-second cap (9 ticks in 1.0 s with no input)
  and instant cancellation (a waiting key returns in 0.00 ms). So the loop asks for a key
  with a 100 ms deadline: a key cancels the activity, and ``-1`` — the deadline expiring —
  means take one more turn of it. There is no ``sleep`` and no busy-wait anywhere in this
  project, and there must not be (CONTRACT-v4 §0.10).
* **Timing does not leak into the pure core.** :func:`step` and :func:`advance` are both
  pure functions of a state; only :func:`run` reads the keyboard or the clock. The whole
  of auto-explore is therefore unit-testable with no terminal.
* **Routes are re-planned every turn.** A full-level search costs a fraction of a
  millisecond against a 100 ms budget (CONTRACT-v4 §18.1), so :class:`Activity` carries no
  path, there is no route cache, and there is no incremental replanning to get wrong. A
  plan that has gone stale — because a door opened underneath it — cannot exist.

The one rule of the loop that is easy to get wrong: **the cancelling keypress is consumed
by the cancellation**. It must not also act as a command, or a panicked keypress would
stop the walk *and* move you into whatever you were fleeing.

**v5 brings the world to life**, and it lands here because this is where the rules live:

* **A turn consumed is one world-tick.** :func:`advance_npcs` is called by :func:`step`
  and by :func:`advance` immediately after any action that consumed a turn, and never
  otherwise. The corollary is v1's headline rule, sharpened: a rejected move consumes no
  turn, so **the world does not tick** — no monster moves, no poison burns, nothing
  regenerates. Walking into a wall leaves the state byte-identical (CONTRACT-v5 §7.8).
* **Randomness is derived, never stored** (CONTRACT-v5 §0.12). No ``random.Random`` lives
  on any state; every roll builds a fresh generator from
  :func:`roll_seed` ``(master_seed, turns, actor_id, salt)``. The player is permanently
  ``actor_id`` 0. :func:`step` and :func:`advance` therefore stay **pure**: same state in,
  same state out, forever, with no module-level draw anywhere in the file.
* **Every other module decides one thing and this one executes it.**
  :func:`roguelike.movement.try_move` says a cell is occupied,
  :func:`roguelike.npc.plan_action` returns an *intent*,
  :func:`roguelike.combat.resolve_attack` returns an arithmetic result, and none of them
  knows what a game is. Turning a refused step into a fight, an intent into a moved
  monster, and an ``AttackResult`` into a corpse, an ``Event`` and some experience is this
  module's job and nobody else's.
* **The contention guarantee lives in the caller.** ``plan_action`` is pure and sees one
  monster at a time, so "two monsters never take the same cell" is only true because each
  accepted move is folded into ``occupied`` *before* the next monster plans
  (CONTRACT-v5 §24.2). Likewise the ``WANDERING``/``HUNTING`` transition and the
  ``FORGET_TICKS`` revert are written here, not there.
* **The v4 seam is now wired.** :func:`interruption` returned ``None`` in every case
  because monsters and hit points did not exist; both now do, so all three of the user's
  conditions are live (CONTRACT-v5 §7.14). Its event is **appended** to the turn's events,
  not substituted for them — substituting would throw away ``The jackal hits you.`` in
  favour of a bare ``You stop.``

**v6 gives the player things to carry, and lands here for the same reason.** Seven
modules shipped the vocabulary — items and the pack, resistance and the shield roll,
species resistances, chests and depth-scaled loot, the chest glyph, the regeneration
effect, the keys and the wording — and **none of them is connected to anything** until
this module joins them up:

* **Equipment moved into the pack** (CONTRACT-v6 §7 v6). ``Player.melee`` and
  ``Player.ranged`` are now :attr:`Player.inventory`'s, because a shield slot on
  ``Player`` as well would put equipment in two places. It is v6's one breaking change.
* **A damage type meets a hide.** Every attack looks the defender's species up through
  :func:`roguelike.npc.resistance_of` and hands the answer to ``resolve_attack``, which
  applies it **to the raw roll** (§26.2). The player starts with a PIERCE dagger and the
  cave snake resists PIERCE — with no alternative that is 45.6% of floors cleared down to
  8.5% (§0.5), which is the pressure the whole pack exists to relieve.
* **A shield is a chance, never a subtraction** (§0.2). Which chance is the *caller's*
  decision — ``resolve_attack`` rolls what it is given and cannot tell a fang from an
  arrow — so :func:`_shield_block` makes it once, here.
* **Chests are not terrain** (§27.3). They live on ``LevelState`` beside the fog and the
  doors, exactly as ``open_doors`` did before them, and ``game.py`` places the monsters
  *after* them so nothing starts the game sitting on the level's only chest (§27.4).
* **The inventory screen is a sub-mode reading raw keys.** Its alphabet deliberately has
  no :class:`~roguelike.keys.CommandKind` (§5 v6), so :func:`run` hands the key straight
  to :func:`inventory_key` and every rule about what it means stays in this module.

This is the only module permitted to import this widely: :mod:`roguelike.level`,
:mod:`roguelike.keys`, :mod:`roguelike.movement`, :mod:`roguelike.render`,
:mod:`roguelike.fov`, :mod:`roguelike.world`, :mod:`roguelike.dungeon`,
:mod:`roguelike.events`, :mod:`roguelike.pathfind`, :mod:`roguelike.activity`,
:mod:`roguelike.stats`, :mod:`roguelike.items`, :mod:`roguelike.status`,
:mod:`roguelike.combat`, :mod:`roguelike.npc` and :mod:`roguelike.loot`
(CONTRACT-v6 §10 v6).
"""

from __future__ import annotations

import curses
import random
from dataclasses import dataclass, replace

from roguelike import dungeon, events, fov, render
from roguelike.activity import Activity, ActivityKind, frontier_cells, walk_step
from roguelike.combat import ranged_block_chance, resolve_attack
from roguelike.events import Event, EventKind
from roguelike.items import (
    DAGGER,
    SHORTBOW,
    Consumable,
    DamageType,
    Inventory,
    Resistance,
    Shield,
    Weapon,
    WeaponKind,
    add,
    drop,
    equip,
)
from roguelike.keys import HELP_ENTRIES, Command, CommandKind, translate_key
from roguelike.level import Level
from roguelike.loot import Chest, place_chest
from roguelike.movement import try_move
from roguelike.npc import (
    wants_to_flee,
    NPC,
    FORGET_TICKS,
    PERCEPTION_RADIUS,
    SPECIES_DATA,
    AiState,
    NpcActionKind,
    plan_action,
    resistance_of,
    spawn_npcs,
)
from roguelike.pathfind import (
    DIRECTIONS,
    Coord,
    Passable,
    find_path,
    line_cells,
    octile,
)
from roguelike.stats import BASELINE, Actor, Stats, derive
from roguelike.status import (
    REGEN_TURNS,
    StatusEffect,
    StatusKind,
    apply_effect,
    tick_effects,
)
from roguelike.world import is_closed_door, is_passable, is_planning_passable

__all__ = [
    "LevelState",
    "Player",
    "Targeting",
    "GameState",
    "ENERGY_THRESHOLD",
    "MAX_EVENTS",
    "new_game",
    "roll_seed",
    "step",
    "advance",
    "advance_npcs",
    "level_up",
    "xp_to_next",
    "xp_for_kill",
    "NPC_XP_DIVISOR",
    "interruption",
    "help_lines",
    "help_page_count",
    "help_page_lines",
    "format_help_status",
    "inventory_lines",
    "format_inventory_status",
    "inventory_key",
    "ITEM_LETTERS",
    "BARE_HANDS",
    "describe_cell",
    "format_stats",
    "format_status_right",
    "run",
    "play",
]

#: The energy an NPC action costs (CONTRACT-v5 §24.3). It lives here, not in
#: :mod:`roguelike.npc`, because the turn loop owns scheduling: an NPC gains its ``speed``
#: in energy on every world-tick and acts once for each whole threshold it has banked, so
#: speed 100 is exactly one action a tick, 180 is two on some ticks and one on others.
ENERGY_THRESHOLD: int = 100

#: The most events that may be stored on a state at once (CONTRACT-v5 §16.1). Six
#: monsters acting in one tick can produce a line far wider than 80 columns, which the
#: renderer would silently clip; capping here means the clipping decision is a rule rather
#: than an accident of terminal width.
MAX_EVENTS: int = 3

#: How long a cave snake's bite lasts and how hard it bites each tick (RESEARCH-v5 §9).
#: The contract fixes the 30% application chance in the bestiary but leaves the effect's
#: shape to the research note, which is where these two numbers come from.
POISON_TURNS: int = 5
POISON_MAGNITUDE: int = 2

#: The player is permanently actor 0 (CONTRACT-v5 §0.12); NPCs are numbered from 1.
_PLAYER_ACTOR_ID: int = 0

#: Roll salts (CONTRACT-v5 §0.12). ``1`` seeds an attack — :func:`resolve_attack` draws
#: to-hit, then damage, then poison from the single generator it is handed, in that fixed
#: order — and ``4`` seeds the AI's wander coin flip. Salts 2 (damage) and 3 (status) are
#: named by the contract but never reached: combat's three draws share one stream by
#: design, and applying a status effect is bookkeeping, not a roll.
_SALT_ATTACK: int = 1
_SALT_WANDER: int = 4

#: Roll salt for a level's chest (CONTRACT-v6 §27.2). A chest is drawn from **the level's
#: own seed**, like its monsters — but from a *separate* stream, derived through
#: :func:`roll_seed`, rather than from the same generator ``spawn_npcs`` draws from. Both
#: are equally reproducible; a separate stream is chosen because sharing one would make
#: the chest's very first "is there a chest at all?" draw shift every monster on every
#: level, silently re-rolling the whole v5 bestiary layout for no gain.
_SALT_CHEST: int = 5

#: What a ``None`` melee slot swings (CONTRACT-v6 §7.15): 1–2 BLUNT, and strength still
#: applies, because a fist is an arm. It is a :class:`~roguelike.items.Weapon` rather than
#: a special case in :func:`_player_attack` so that bare-handed combat goes down the exact
#: same path as an equipped one — resistance included, which is the point: a giant bat is
#: vulnerable to BLUNT, and punching one is measurably better than stabbing it.
#: It is **not** in ``items.py``: §7.15 makes "what bare-handed means" this module's rule.
BARE_HANDS: Weapon = Weapon(
    "bare hands", WeaponKind.MELEE, 1, 2, range=1, damage_type=DamageType.BLUNT
)

#: The kit every run starts with (CONTRACT-v6 §7 v6). The same dagger and shortbow v5
#: carried on ``Player.melee``/``Player.ranged``; only where they live has changed.
_STARTING_INVENTORY: Inventory = Inventory(melee=DAGGER, ranged=SHORTBOW)

#: The letters that select a carried item on the inventory screen, index by index — so the
#: item shown as ``c`` is ``carried[2]``.
#:
#: **``d`` and ``e`` are missing on purpose, and this resolves a contradiction in the
#: contract.** §5 v6 says the letters ``a``–``t`` select an item; §7.17 says ``e`` equips
#: or uses the selection and ``d`` drops it. Both cannot be true of the same keystroke: if
#: ``d`` selected the fourth item there would be no key left to drop anything with. §7.17
#: describes the screen the player actually operates, so the two action keys win, and the
#: labels simply skip them — what is printed beside an item is exactly the key that
#: selects it, so nothing is hidden and no item is unreachable. Twenty letters for
#: :data:`roguelike.items.CARRY_LIMIT` items, eighteen of them inside §5 v6's ``a``–``t``.
ITEM_LETTERS: str = "abcfghijklmnopqrstuv"


@dataclass(frozen=True)
class LevelState:
    """A level the player has left, and everything about it that cannot be re-derived.

    The ``Level`` itself *could* be regenerated — :func:`roguelike.dungeon.level_for` is
    deterministic — but ``explored`` and ``open_doors`` are runtime facts about a
    particular game, and losing them would reset the fog every time the player climbed a
    staircase. Since the store has to exist for those two, it carries the level too.

    ``npcs`` is here for exactly the same reason (CONTRACT-v5 §24.5): where the monsters
    are, how hurt they are and how much energy they have banked cannot be regenerated
    from a seed once a fight has started. **NPCs on a level the player has left are
    frozen** — they do not act, do not heal and do not move, because the world only ticks
    on the level the player is standing on. Climb back down and the pack is exactly where
    you left it.

    ``chests`` is here for the third time the same reason applies (CONTRACT-v6 §27.3): a
    chest is **not terrain**, because an opened one has changed and ``Level`` is frozen.
    That is precisely the problem doors had and it is solved precisely the way doors
    solved it (CONTRACT-v2 §0.6). Which chest has been emptied is a runtime fact about a
    particular game, so it travels with the level the player left, not with the player.

    There is deliberately no player position here: you always re-enter a level at a known
    staircase, never where you happened to be standing.
    """

    level: Level
    explored: frozenset[tuple[int, int]]
    open_doors: frozenset[tuple[int, int]]
    npcs: tuple[NPC, ...] = ()
    chests: tuple[Chest, ...] = ()


@dataclass(frozen=True)
class Player:
    """The player character: a shared :class:`~roguelike.stats.Actor` core plus the
    things only a player has (CONTRACT-v5 §7 v5).

    ``actor`` is the same type every monster carries, which is why
    :func:`roguelike.combat.resolve_attack` is written once and used by both sides of
    every fight.

    **v6's one breaking change lives here.** ``melee`` and ``ranged`` were fields of this
    class in v5; they are now :attr:`roguelike.items.Inventory.melee` and ``.ranged``
    inside ``inventory``, so every read of ``player.melee`` is ``player.inventory.melee``
    (CONTRACT-v6 §7 v6). It is deliberate: keeping equipment on ``Player`` *and* adding a
    shield slot would put the same idea in two places, and the pack has to live somewhere
    anyway. Ammunition is still infinite — there is no counter to go out of sync.

    Every slot may be ``None``: bare-handed is a legal state, and what it means is this
    module's rule (§7.15, :data:`BARE_HANDS`).

    ``regen_counter`` counts world-ticks towards the next point of natural healing
    (:data:`roguelike.status.REGEN_TURNS`). It is a plain integer on the state rather than
    a timer somewhere, because everything about a turn has to be reproducible from the
    state alone.
    """

    actor: Actor
    inventory: Inventory = _STARTING_INVENTORY
    xp: int = 0
    level: int = 1
    regen_counter: int = 0


@dataclass(frozen=True)
class Targeting:
    """The ranged-attack sub-mode: which cells can be shot, and which one is selected.

    Modelled on ``awaiting_walk`` (CONTRACT-v4 §7.4), which already proved the pattern: a
    terminal cannot observe key release, so choosing a target is necessarily several
    keystrokes and what has been chosen so far has to be remembered on the state. None of
    those keystrokes costs a turn; only the shot does (CONTRACT-v5 §7.10).

    ``targets`` is sorted by ``(Chebyshev distance, coordinate)`` — a total order, so
    cycling is deterministic — and ``index`` is always a valid index into it, because the
    empty case is reported as ``NO_TARGET`` and never stored.
    """

    targets: tuple[Coord, ...]
    index: int = 0


_BASELINE_STATS: Stats = Stats(str_=BASELINE, agi=BASELINE, vit=BASELINE)

#: The character every run starts with: baseline in all three stats, at full health, a
#: dagger and a shortbow. It is a module constant because it is also
#: :attr:`GameState.player_actor`'s default, and a frozen dataclass is safe to share.
_NEW_PLAYER: Player = Player(
    actor=Actor(stats=_BASELINE_STATS, hp=derive(_BASELINE_STATS).max_hp)
)


@dataclass(frozen=True)
class GameState:
    """The complete state of a game in progress. Immutable — transitions build a new one.

    ``master_seed`` is the seed the *run* was started from, not any individual level's
    generator seed; :func:`roguelike.dungeon.seed_for` derives the latter from the former
    and the depth. It is what the status line shows, because it is what makes a run
    replayable.

    ``depth`` is 1-based. ``level``, ``player``, ``explored``, ``visible`` and
    ``open_doors`` all describe **the current depth only**; every other depth the player
    has visited lives in ``saved``, keyed by depth, and the current depth is never a key of
    it.

    ``turns`` counts only turns actually consumed: a move rejected by
    :func:`roguelike.movement.try_move` leaves it alone (v1 BRIEF Q13), and so does a
    stair command given off the stairs. The exceptions that *do* cost a turn without
    moving you are bumping a closed door open (CONTRACT-v2 §7) and taking a staircase.

    ``running`` goes ``False`` exactly once — when the player quits, or climbs out of the
    dungeon from level 1 — and a state with ``running=False`` is inert: :func:`step`
    returns it unchanged for every command.

    ``events`` is what the *last* command that produced any produced. It is not a log:
    there is exactly one line of message, it is replaced by every turn-consuming action,
    and it survives everything that consumes no turn (CONTRACT-v3 §7.1).

    ``outcome`` is the one piece of text that has to outlive the curses window — the
    farewell printed by :func:`play` after the terminal is restored. ``None`` until the
    game ends by leaving the dungeon; quitting with ``q`` leaves it ``None``, because
    there is nothing to say.

    The three coordinate sets are all ``frozenset``, so nothing downstream can mutate
    them, and no behaviour may depend on their iteration order (CONTRACT-v2 §0.7):

    * ``visible`` — what is in view *right now*. Replaced wholesale by every recompute.
    * ``explored`` — everything ever seen **on this level**. Only ever grows.
    * ``open_doors`` — the doors opened so far **on this level**. Only ever grows; there
      is no way to close one.

    ``saved`` is the one field whose type is not frozen, because there is no frozen
    mapping in the standard library worth the ceremony. It is treated as immutable
    regardless: every transition builds a **new** dict and no code path mutates one that
    is already on a state.

    ``radius`` is the sight radius handed to :func:`roguelike.fov.compute_visible`. It is
    per-game state rather than a constant read at the call site, because indoors the walls
    dominate long before the radius does and the number is expected to be tuned.

    ``activity`` is the multi-turn action in progress, or ``None`` when the player is
    driving every turn by hand (CONTRACT-v4 §7). It is the whole of what makes automatic
    navigation a *state* rather than a loop: :func:`advance` performs exactly one turn of
    it and hands back a new state, so the pacing lives in :func:`run` and the rules live
    here. It carries no path — see the module docstring — and it does not survive a level
    change, because the route it was planned over ceases to exist.

    ``awaiting_walk`` is the one-keystroke prefix state left behind by ``w``: a terminal
    cannot observe key release, so "walk in a direction" is necessarily two keystrokes and
    the first of them has to be remembered somewhere (RESEARCH-v4 §6). It is cleared by
    the very next command whatever that command is.

    ``player_actor`` is the character sheet — stats, hit points, weapons, experience —
    while ``player`` remains the coordinate. They are separate fields because they are
    separate ideas and because keeping ``player`` where it has always been is what lets
    every v1–v4 construction of this class keep working.

    ``npcs`` are the monsters **on the current level only**, ordered by ``actor_id`` and
    iterated in that order (CONTRACT-v5 §24.3) — a tuple, never a set, so nothing can
    depend on hash order. Every other level's monsters live in ``saved`` alongside its fog
    and doors, and are frozen while the player is elsewhere.

    ``targeting`` is the ranged sub-mode, ``None`` whenever the player is not choosing a
    target. It does not survive a level change.

    ``chests`` are the chests **on the current level only**, exactly as ``npcs`` are, and
    for the same reason (CONTRACT-v6 §27.3): every other level's chests live in ``saved``
    beside its fog, its doors and its monsters. At most one is ever placed per level.

    ``inventory_open`` and ``inventory_cursor`` are the inventory screen: whether it is
    showing, and which carried item is selected. It is a sub-mode in the mould of the help
    screen — it swallows every key and **costs no turn to open, browse or close** (§7.17).
    The cursor is an index into ``player_actor.inventory.carried``.

    ``running`` goes ``False`` when the player quits, climbs out of the dungeon — or
    **dies**, which is v5's third and only involuntary ending (CONTRACT-v5 §7.12). All
    three set it in the same way and there is no separate death flag.

    Field order is binding (CONTRACT-v3 §7, CONTRACT-v4 §7, CONTRACT-v5 §7 v5):
    everything without a default comes first, and each version's new fields are appended
    with defaults, so every construction written against v1–v4 still works unchanged.
    """

    master_seed: int
    depth: int
    level: Level
    player: tuple[int, int]
    explored: frozenset[tuple[int, int]]
    visible: frozenset[tuple[int, int]]
    open_doors: frozenset[tuple[int, int]]
    saved: dict[int, LevelState]
    turns: int = 0
    running: bool = True
    radius: int = fov.DEFAULT_RADIUS
    events: tuple[Event, ...] = ()
    outcome: str | None = None
    activity: Activity | None = None
    awaiting_walk: bool = False
    player_actor: Player = _NEW_PLAYER
    npcs: tuple[NPC, ...] = ()
    targeting: Targeting | None = None
    help_page: int | None = None
    awaiting_attack: bool = False
    awaiting_close: bool = False
    look_cursor: Coord | None = None
    projectile: tuple[Coord, ...] = ()
    chests: tuple[Chest, ...] = ()
    inventory_open: bool = False
    inventory_cursor: int = 0


def new_game(
    master_seed: int,
    width: int = 80,
    height: int = 22,
    radius: int = fov.DEFAULT_RADIUS,
) -> GameState:
    """Start a new run: generate level 1 and stand the player on its up-staircase.

    ``master_seed`` identifies the whole dungeon, not this one level — every deeper level
    is derived from it by :func:`roguelike.dungeon.seed_for`, so the same master seed
    always yields the same chain of levels.

    The player starts on the up-staircase, which is also the level's ``player_start``
    (G17): the way out is where you came in, and taking it from level 1 ends the run.
    Nothing is explored yet and no door is open, and the first field of view is computed
    immediately, so ``explored`` and ``visible`` are equal and non-empty from the outset —
    "nothing is explored" is the state *before* that first computation, and it is never
    observable.

    The level is populated at the same moment it is generated (CONTRACT-v5 §24.4), from a
    generator seeded with **the level's own seed** — so a level's monsters are as
    reproducible as its rooms, and the same master seed always yields the same dungeon
    *and* the same population. Spawning lives in :func:`roguelike.npc.spawn_npcs`, not in
    the generator, which is why ``generator.py`` and ``dungeon.py`` stayed frozen across
    this increment.

    Raises:
        ValueError: if ``radius`` is negative — propagated unchanged from
            :func:`roguelike.fov.compute_visible`, which owns that rule — or if ``width``
            or ``height`` is too small for the generator.
    """
    level = dungeon.level_for(master_seed, 1, width=width, height=height)
    # G17 pins player_start to the up-staircase: the spawn *is* the way out.
    player = level.player_start
    open_doors: frozenset[tuple[int, int]] = frozenset()
    visible = fov.compute_visible(level, open_doors, player, radius)
    npcs, chests = _populate(level, 1)
    return GameState(
        master_seed,
        1,
        level,
        player,
        explored=visible,
        visible=visible,
        open_doors=open_doors,
        saved={},
        turns=0,
        running=True,
        radius=radius,
        events=(),
        outcome=None,
        player_actor=_NEW_PLAYER,
        npcs=npcs,
        chests=chests,
    )


def roll_seed(master_seed: int, turns: int, actor_id: int, salt: int) -> int:
    """Derive the seed for one roll from the state that asked for it (CONTRACT-v5 §0.12).

    The whole of this project's randomness discipline in one function. **No
    ``random.Random`` is ever stored** on a state, a player or a monster: a generator is
    mutable, and two states built from one parent by ``replace()`` would share and corrupt
    a single stream — exactly what the frozen-dataclass discipline exists to prevent. So
    every roll builds a *fresh* generator from four integers that are all already on the
    state, and :func:`step` and :func:`advance` stay pure functions of their input.

    ``actor_id`` is stable from spawn and the player is permanently ``0``; ``salt``
    separates independent roll kinds within one tick. The multipliers are the same
    odd-constant mix :func:`roguelike.dungeon.seed_for` uses; the mask keeps the result a
    non-negative 31-bit integer.

    Pure, total, and never raises — negative inputs simply mix to some other seed.
    """
    return (
        master_seed * 0x9E3779B1
        + turns * 0x85EBCA77
        + actor_id * 0xC2B2AE35
        + salt * 0x27D4EB2F
    ) & 0x7FFFFFFF


def _populate(level: Level, depth: int) -> tuple[tuple[NPC, ...], tuple[Chest, ...]]:
    """Fill a freshly generated level: its chest first, then its monsters (§24.4, §27.4).

    The one place :func:`roguelike.npc.spawn_npcs` and :func:`roguelike.loot.place_chest`
    are called. A level is populated exactly once — when it is first generated — and its
    contents thereafter live on the state or, once the player has left, in the
    :class:`LevelState` filed under its depth. Coming back down a staircase never re-rolls
    either of them.

    **The chest is placed first and the monsters second, and the order is binding**
    (CONTRACT-v6 §27.4). §27.2 requires that no chest sits on a cell holding a monster,
    but ``place_chest(rng, level, depth)`` is handed no monster list and ``loot.py`` may
    not import ``npc.py`` — the rule is unsatisfiable by the module that owns it, so the
    amendment moved it here, where both are visible. ``spawn_npcs`` likewise takes no set
    of forbidden cells, so the constraint is applied the only way this module can apply
    it: **a monster that lands on the chest is dropped**, exactly as ``spawn_npcs`` itself
    drops a monster it has no legal cell for (CONTRACT-v5 §11 v5) rather than relaxing a
    rule. The chest wins because it was placed first, and the rule the amendment names —
    "the *starting* state is never one where a monster is sitting on the level's only
    chest" — is what comes out.

    The two draws come from two streams, both derived from the level's own seed, so both
    are as reproducible as the level's rooms (§27.2). See :data:`_SALT_CHEST` for why they
    are not one stream.
    """
    chest = place_chest(
        random.Random(roll_seed(level.seed, 0, _PLAYER_ACTOR_ID, _SALT_CHEST)),
        level,
        depth,
    )
    chests: tuple[Chest, ...] = () if chest is None else (chest,)

    npcs = spawn_npcs(random.Random(level.seed), level)
    taken = {chest.position for chest in chests}
    if taken:
        npcs = tuple(npc for npc in npcs if npc.position not in taken)
    return npcs, chests


# --------------------------------------------------------------------------------------
# Turn transitions — the two ways a turn is consumed
# --------------------------------------------------------------------------------------


def _stair_events(level: Level, position: tuple[int, int]) -> tuple[Event, ...]:
    """The "there is a staircase here" events for standing on ``position``, if any.

    Decided from the level's stair *coordinates* rather than the tile under the player,
    because this module has no business importing :mod:`roguelike.tiles` (CONTRACT-v3
    §10). G18 makes the two readings the same on any generated level: the grid holds
    exactly one ``STAIRS_UP`` cell, at ``stairs_up``, and one ``STAIRS_DOWN`` cell, at
    ``stairs_down[0]``.

    ``stairs_up`` is ``None`` on a hand-built level with no stairs, which no coordinate
    equals, so such a level simply never produces these events.
    """
    if position == level.stairs_up:
        return (Event(EventKind.STAIRS_HERE_UP),)
    if position in level.stairs_down:
        return (Event(EventKind.STAIRS_HERE_DOWN),)
    return ()


def _chest_at(chests: tuple[Chest, ...], cell: Coord) -> Chest | None:
    """The chest standing on ``cell``, or ``None``. At most one is ever placed."""
    for chest in chests:
        if chest.position == cell:
            return chest
    return None


def _arrival_events(state: GameState, position: Coord) -> tuple[Event, ...]:
    """What stepping onto ``position`` announces: a staircase, and now a chest.

    "Stepping onto a chest's cell emits ``CHEST_HERE``, like a staircase" (CONTRACT-v6
    §7.18) — so it is said in the same place, by the same rule, and a cell that is both a
    staircase and a chest says both. Nothing here consumes a turn; the caller has already
    decided that.

    A chest and a staircase *can* share a cell: :func:`roguelike.loot.place_chest` keeps
    its distance from ``player_start`` and from doors, but a down-staircase is ordinary
    walkable floor to it.
    """
    emitted = _stair_events(state.level, position)
    if _chest_at(state.chests, position) is not None:
        emitted += (Event(EventKind.CHEST_HERE),)
    return emitted


def _take_turn(
    state: GameState,
    player: tuple[int, int],
    open_doors: frozenset[tuple[int, int]],
    emitted: tuple[Event, ...],
) -> GameState:
    """Return ``state`` advanced by one consumed turn *on the same level*.

    The single definition of what a turn spent on the current level does: advance the
    counter, recompute ``visible`` for the new position and door set, fold the result into
    ``explored``, and replace ``events`` with what this command produced. Its two
    callers — an accepted move and a door opening — are exactly the two transitions that
    can change what is visible without changing which level you are on (CONTRACT-v2 §7),
    so there is no way to consume such a turn without refreshing sight.

    ``emitted`` may be empty: an ordinary step onto ordinary ground produces no event, and
    clearing the previous message is the correct behaviour for it (a turn passed).

    Pure, like everything it is called from: nothing passed in is mutated.
    """
    visible = fov.compute_visible(state.level, open_doors, player, state.radius)
    return replace(
        state,
        player=player,
        open_doors=open_doors,
        visible=visible,
        explored=state.explored | visible,
        turns=state.turns + 1,
        events=emitted,
    )


def _change_level(
    state: GameState,
    depth: int,
    level: Level,
    player: tuple[int, int],
    explored: frozenset[tuple[int, int]],
    open_doors: frozenset[tuple[int, int]],
    npcs: tuple[NPC, ...],
    chests: tuple[Chest, ...],
    emitted: tuple[Event, ...],
) -> GameState:
    """Return ``state`` moved to ``depth``, one turn later. The other kind of turn.

    Shared by descent and ascent, which differ only in *which* level and coordinate they
    hand in. It performs the bookkeeping both must get right:

    * the level being left is filed into ``saved`` under the depth being left;
    * ``saved`` is rebuilt, never mutated — ``state.saved`` comes back untouched;
    * the depth being *entered* is removed from ``saved``, because ``explored``,
      ``visible`` and ``open_doors`` on the state are now the live copies of it. The
      invariant is that ``state.depth not in state.saved``, always;
    * field of view is recomputed against the destination level and *its* open doors, and
      folded into *its* explored set — never the departed level's.

    Arriving re-explores the arrival cell, which is why fog survives a round trip exactly:
    you always arrive on the staircase you left from, so the recomputed ``visible`` is a
    subset of what was already explored and the union changes nothing.

    The monsters travel with the level, not with the player (CONTRACT-v5 §24.5): the
    departed level's ``npcs`` are filed into ``saved`` beside its fog, and the arrived
    level's become the live set. Nothing ticks them in between, so a level left mid-fight
    is found mid-fight. ``chests`` travel exactly the same way (CONTRACT-v6 §27.3), which
    is what makes a chest you emptied on level 3 still empty when you climb back down to
    it. ``targeting`` is dropped, because the cells it names belong to a level that is no
    longer under the player's feet.
    """
    saved = {
        **state.saved,
        state.depth: LevelState(
            state.level, state.explored, state.open_doors, state.npcs, state.chests
        ),
    }
    saved.pop(depth, None)

    visible = fov.compute_visible(level, open_doors, player, state.radius)
    return replace(
        state,
        depth=depth,
        level=level,
        player=player,
        explored=explored | visible,
        visible=visible,
        open_doors=open_doors,
        saved=saved,
        turns=state.turns + 1,
        events=emitted,
        npcs=npcs,
        chests=chests,
        targeting=None,
    )


def _explored_passable(state: GameState) -> Passable:
    """The planning predicate for a route the *character* could have worked out.

    ``world.is_planning_passable`` restricted to ``explored`` — a cell may be routed
    through if it has been seen and is either passable or a closed door (bumping opens
    one, so planning through it is honest; CONTRACT-v4 §13). Restricting it to
    ``explored`` is what stops travel and auto-explore from routing over terrain the
    character has never laid eyes on.

    The character's own cell is never asked about: :func:`roguelike.pathfind.find_path`
    only calls the predicate on cells it expands *into*, so standing somewhere this
    predicate would reject cannot block a search.
    """
    level = state.level
    open_doors = state.open_doors
    explored = state.explored

    def passable(x: int, y: int) -> bool:
        return (x, y) in explored and is_planning_passable(level, open_doors, x, y)

    return passable


def _whole_level_passable(state: GameState) -> Passable:
    """The planning predicate for auto-walk: the level as it stands, fog aside.

    Auto-walk is a *local* rule — it reads at most the cells touching the one being
    considered, all of which are in view — so restricting it to ``explored`` would change
    nothing except to make "stop before the opening" depend on how much of the room ahead
    happened to be lit. Closed doors count as passable here for the same reason as
    everywhere else in planning: the walk bumps them open and carries on
    (RESEARCH-v4 §6).
    """
    level = state.level
    open_doors = state.open_doors

    def passable(x: int, y: int) -> bool:
        return is_planning_passable(level, open_doors, x, y)

    return passable


def _refuse_automatic_move(state: GameState) -> GameState | None:
    """Refuse to hand the reins over while something hostile is watching.

    Returns a state carrying the refusal, or ``None`` when there is nothing in view and
    the caller may proceed. Costs no turn either way — declining to start is not an
    action.

    Every automatic move goes through here: auto-explore, travel and auto-walk alike.
    Letting the player start one with a jackal already on screen is the same mistake as
    letting a walk continue into a pack — :func:`interruption` catches the second case,
    and this catches the first, which it structurally cannot: it only fires on a monster
    that *newly* appears.
    """
    name = _visible_hostile(state)
    if name is None:
        return None
    return replace(state, events=(Event(EventKind.HOSTILE_IN_VIEW, name=name),))


def _travel_or_report(
    state: GameState, candidates: tuple[Coord, ...], unknown: EventKind
) -> GameState:
    """Start travelling to the nearest known staircase, or report that none is known.

    The off-the-stairs half of ``>`` and ``<`` (CONTRACT-v4 §7.4). ``candidates`` is every
    staircase of that kind on the level; only those in ``explored`` are eligible, because
    the character cannot walk to a staircase they have not found. With none of them found,
    v3's behaviour survives exactly: say so, consume no turn, start nothing (user decision
    2).

    "Nearest" is nearest *by route*, not by straight line: the search is given all the
    eligible staircases at once and the one it reaches first is the goal. The fallback
    only fires when every known staircase is unreachable through explored ground, where
    any goal leads to the same ``NOTHING_FURTHER`` on the first :func:`advance`; it exists
    so that a goal can always be named and never so that a better one is passed over.

    No turn is consumed either way — starting an activity is not itself an action.
    """
    refusal = _refuse_automatic_move(state)
    if refusal is not None:
        return refusal

    goals = frozenset(cell for cell in candidates if cell in state.explored)
    if not goals:
        return replace(state, events=(Event(unknown),))

    route = find_path(_explored_passable(state), state.player, goals)
    goal = (
        route[-1]
        if route is not None
        else min(goals, key=lambda cell: (octile(state.player, cell), cell))
    )
    return replace(
        state,
        activity=Activity(ActivityKind.TRAVEL, goal=goal),
        events=(Event(EventKind.TRAVELLING),),
    )


def _descend(state: GameState) -> GameState:
    """Take the down-staircase under the player, or report that there isn't one.

    The level below is restored from ``saved`` when the player has been there before, and
    generated otherwise — with ``required_up`` set to the coordinate being descended from,
    so the new level's up-staircase lands exactly there (G14) and the player's ``(x, y)``
    is unchanged by the descent. The world moves, not the player.

    Off the stairs it is not a mistyped key any more but a destination: v4 turns it into
    a travel order towards the nearest down-staircase the character has already found, or,
    when none has been found, into v3's ``NO_STAIRS_DOWN`` unchanged. Either way it
    consumes **no turn** (CONTRACT-v4 §7.4).
    """
    stairs_down = state.level.stairs_down
    if not stairs_down or state.player != stairs_down[0]:
        return _travel_or_report(state, stairs_down, EventKind.NO_STAIRS_DOWN)

    target = stairs_down[0]
    depth = state.depth + 1
    below = state.saved.get(depth)
    if below is None:
        level = dungeon.level_for(
            state.master_seed,
            depth,
            required_up=target,
            width=state.level.width,
            height=state.level.height,
        )
        explored: frozenset[tuple[int, int]] = frozenset()
        open_doors: frozenset[tuple[int, int]] = frozenset()
        # A level is populated once, when it is generated (CONTRACT-v5 §24.4, §27.4).
        npcs, chests = _populate(level, depth)
    else:
        level = below.level
        explored = below.explored
        open_doors = below.open_doors
        npcs = below.npcs
        chests = below.chests

    return _change_level(
        state,
        depth,
        level,
        target,
        explored,
        open_doors,
        npcs,
        chests,
        (Event(EventKind.DESCENDED, depth=depth),),
    )


def _ascend(state: GameState) -> GameState:
    """Take the up-staircase under the player: back up a level, or out of the dungeon.

    From level 1 the up-staircase is the way out, and taking it ends the run: ``running``
    clears and ``outcome`` is set to the farewell, which :func:`play` prints once the
    terminal is restored. That text comes from the message table like every other piece of
    wording — this module composes no sentences of its own.

    Deeper, it is an ordinary staircase: the level above is restored from ``saved`` with
    its fog and its opened doors intact, and the player arrives on the down-staircase they
    originally used. ``saved`` always holds it, because the only way to be at depth *d* is
    to have descended through *d-1*.

    Off the stairs it is the mirror of ``>``: travel to the up-staircase if it has been
    found, ``NO_STAIRS_UP`` if it has not, and no turn either way (CONTRACT-v4 §7.4).
    """
    stairs_up = state.level.stairs_up
    if stairs_up is None or state.player != stairs_up:
        candidates = () if stairs_up is None else (stairs_up,)
        return _travel_or_report(state, candidates, EventKind.NO_STAIRS_UP)

    if state.depth == 1:
        emitted = (Event(EventKind.LEFT_DUNGEON),)
        return replace(
            state,
            running=False,
            events=emitted,
            outcome=events.message_for(emitted),
        )

    depth = state.depth - 1
    above = state.saved[depth]
    return _change_level(
        state,
        depth,
        above.level,
        above.level.stairs_down[0],
        above.explored,
        above.open_doors,
        above.npcs,
        above.chests,
        (Event(EventKind.ASCENDED, depth=depth),),
    )


# --------------------------------------------------------------------------------------
# Combat, monsters and the world-tick (CONTRACT-v5 §7.8 - §7.12)
# --------------------------------------------------------------------------------------


def _chebyshev(a: Coord, b: Coord) -> int:
    """Eight-way step distance: ``max(|dx|, |dy|)``.

    The right metric everywhere in this game because a diagonal move is one move, and the
    metric both the bestiary's perception rule and the ranged weapon's range are stated
    in. It is three lines rather than an import because :mod:`roguelike.pathfind` exports
    ``octile``, which is a *route cost*, not a distance, and the two disagree.
    """
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _occupied(state: GameState) -> frozenset[Coord]:
    """Every cell an actor is standing on: the monsters, and the player.

    Handed to :func:`roguelike.movement.try_move` so a step into a monster is refused with
    ``blocked_by_npc`` instead of walking through it, and to
    :func:`roguelike.npc.plan_action` so no monster plans into a cell that is taken.
    """
    return frozenset(npc.position for npc in state.npcs) | {state.player}


def _npc_at(npcs: tuple[NPC, ...], cell: Coord) -> NPC | None:
    """The monster standing on ``cell``, or ``None``. Occupancy makes it at most one."""
    for npc in npcs:
        if npc.position == cell:
            return npc
    return None


def _species_name(npc: NPC) -> str:
    """The lower-case species name the message table interpolates into ``{name}``."""
    return SPECIES_DATA[npc.species].name


#: Message-line priority (CONTRACT-v5 §16.1), lowest number first. Everything absent from
#: this table is band 4 — the "everything else" row.
_EVENT_PRIORITY: dict[EventKind, int] = {
    EventKind.PLAYER_DIED: 0,
    EventKind.LEVELLED_UP: 1,
    EventKind.NPC_KILLED: 1,
    EventKind.NPC_HIT_PLAYER: 2,
    EventKind.SHIELD_BLOCKED: 2,
    EventKind.POISONED: 2,
    EventKind.POISON_DAMAGE: 2,
    EventKind.PLAYER_HIT_NPC: 3,
    EventKind.PLAYER_MISSED_NPC: 3,
    # The resistance flavour rides with the blow it is about, so a capped line never
    # keeps "It tears into the bat!" while dropping "You hit the bat."
    EventKind.RESISTED: 3,
    EventKind.VULNERABLE_HIT: 3,
    EventKind.IMMUNE_HIT: 3,
}

_DEFAULT_PRIORITY: int = 4

#: The two events that are kept even when the monster responsible is out of sight: the
#: player always perceives what happens to the player (CONTRACT-v5 §16.1). Everything else
#: about an unseen monster is dropped — there is no ambient "you hear scurrying" in this
#: project, for the same reason there is no "you bump into a wall".
_ALWAYS_PERCEIVED: frozenset[EventKind] = frozenset(
    {EventKind.NPC_HIT_PLAYER, EventKind.SHIELD_BLOCKED, EventKind.POISONED}
)


def _perceived(
    pairs: list[tuple[Event, Coord | None]], visible: frozenset[Coord]
) -> tuple[Event, ...]:
    """Drop the events about monsters the player cannot see (CONTRACT-v5 §16.1).

    Each pair is an event and the cell it is *about* — a monster's position, or ``None``
    for something that happened to the player and has no location. An event survives if it
    has no location, if that location is in ``visible``, or if it is one of the two kinds
    the player always perceives. Applied **before** the cap, so an unseen monster's chatter
    cannot crowd out a message the player needed.
    """
    return tuple(
        event
        for event, cell in pairs
        if cell is None or cell in visible or event.kind in _ALWAYS_PERCEIVED
    )


def _capped(emitted: tuple[Event, ...]) -> tuple[Event, ...]:
    """Keep at most :data:`MAX_EVENTS` events, the highest-priority ones (§16.1).

    Selection is by priority band, ties broken by emission order, so when six monsters act
    at once the line the player reads is the one that mattered: a death outranks a
    level-up, which outranks being hit, which outranks hitting.

    **The survivors are returned in emission order**, not in priority order. §16.1 ranks
    the bands to say which events are *kept*; re-ordering them as well would render
    ``You die... The poison burns.`` and ``You kill the rat! Welcome to level 2. You hit
    the rat.`` — the causes reported after their effects. Within a band, emission order is
    preserved either way, which is the property the contract names.

    Under the cap this is exactly ``tuple(emitted)``, so the ordinary one- and two-event
    turn is untouched.
    """
    if len(emitted) <= MAX_EVENTS:
        return tuple(emitted)
    ranked = sorted(
        range(len(emitted)),
        key=lambda index: (
            _EVENT_PRIORITY.get(emitted[index].kind, _DEFAULT_PRIORITY),
            index,
        ),
    )
    return tuple(emitted[index] for index in sorted(ranked[:MAX_EVENTS]))


def xp_to_next(level: int) -> int:
    """Experience needed to go from character level ``level`` to the next one.

    ``25 * level * level`` (CONTRACT-v5 §7.11): 25 to reach level 2, 100 to reach level 3,
    225 to reach level 4. The definition is "from ``L``", not "to ``L``" — an earlier draft
    mixed the two and made reaching level 2 cost 100 XP where the design promised 25.
    """
    return 25 * level * level


def level_up(state: GameState) -> GameState:
    """Spend banked experience on as many character levels as it buys (§7.11). Pure.

    The loop **subtracts** each level's cost as it goes, so a single kill that crosses two
    thresholds levels twice and carries the remainder — the off-by-one an earlier draft
    got wrong.

    Growth is deterministic and has no allocation screen: ``vit`` every level, and ``str_``
    or ``agi`` by the parity of the level just reached. Derived stats are recomputed
    through the same :func:`roguelike.stats.derive` as spawning — there is no second HP
    formula anywhere in this project — and **current HP grows by exactly the max-HP delta,
    not to full**. A full heal would make levelling a heal-on-demand for anyone willing to
    grind.

    One ``LEVELLED_UP`` event is appended per level gained, carrying the new level; the
    result is capped by §16.1 like every other event list. With nothing to spend, the state
    is returned **unchanged** — the same object — so this is free to call after every kill.
    """
    player = state.player_actor
    xp = player.xp
    level = player.level
    stats = player.actor.stats
    hp = player.actor.hp
    gained: list[Event] = []

    while xp >= xp_to_next(level):
        xp -= xp_to_next(level)
        level += 1
        before_max = derive(stats).max_hp
        stats = Stats(
            str_=stats.str_ + (1 if level % 2 == 1 else 0),
            agi=stats.agi + (0 if level % 2 == 1 else 1),
            vit=stats.vit + 1,
        )
        hp += derive(stats).max_hp - before_max
        gained.append(Event(EventKind.LEVELLED_UP, level=level))

    if not gained:
        return state

    return replace(
        state,
        player_actor=replace(
            player,
            actor=replace(player.actor, stats=stats, hp=hp),
            xp=xp,
            level=level,
        ),
        events=_capped(state.events + tuple(gained)),
    )


def _melee_weapon(player: Player) -> Weapon:
    """What the player swings: the equipped melee weapon, or their fists (§7.15).

    An empty melee slot is a legal state, not a missing one, so it resolves to
    :data:`BARE_HANDS` here — one place, so that bump-to-attack and ``a``-plus-direction
    cannot disagree about what an unarmed player hits with.
    """
    return player.inventory.melee or BARE_HANDS


def _shield_block(
    shield: Shield | None, missile: bool, defender_agi: int, attacker_agi: int
) -> int:
    """The block chance to hand :func:`roguelike.combat.resolve_attack`, in percent.

    **The caller's decision, and it has to be, because ``resolve_attack`` rolls whatever
    it is given and cannot tell a fist from an arrow** (CONTRACT-v6 §23.6). A shield's
    plain ``block_chance`` stops a blow; a *missile* is stopped by
    :func:`roguelike.combat.ranged_block_chance`, which shifts that number by the
    agility gap and clamps it to 5–75 so there is always a chance to be hit anyway.

    **No shield is zero, never a floor.** ``ranged_block_chance``'s floor of 5 keeps a
    small shield from being worthless; passing it for a defender carrying *no* shield
    would invent a 5% block out of nothing — which is what a monster would get, since
    nothing in the bestiary carries one.

    In v6 the missile case has no live caller: nothing shoots at the player, and no
    monster owns a shield (§23.6 records this as a choice). The rule is written here once
    rather than guessed at two call sites.
    """
    if shield is None:
        return 0
    if not missile:
        return shield.block_chance
    return ranged_block_chance(shield.block_chance, defender_agi, attacker_agi)


#: What a hit says about the defender's hide, beyond the blow itself (CONTRACT-v6 §16 v6).
#: ``NORMAL`` is absent: an ordinary hit on an ordinary body has nothing extra to report.
_RESISTANCE_EVENT: dict[Resistance, EventKind] = {
    Resistance.RESISTANT: EventKind.RESISTED,
    Resistance.VULNERABLE: EventKind.VULNERABLE_HIT,
    Resistance.IMMUNE: EventKind.IMMUNE_HIT,
}


def _player_attack(
    state: GameState, cell: Coord, weapon: Weapon, strength_applies: bool
) -> GameState:
    """Resolve one attack by the player on the monster standing on ``cell``.

    The single place a player attack is resolved, shared by bump-to-attack melee (§7.9)
    and by a fired shot (§7.10); the two differ only in the weapon and in whether strength
    applies. **Ranged weapons pass ``strength_applies=False``** even though they are
    wielded — a bow's power is the bow's, and :func:`roguelike.combat.resolve_attack` has
    no way to tell a bow from a dagger, so getting this right is this call site's job.

    ``weapon`` may be :data:`BARE_HANDS`; nothing here treats it differently, which is the
    point of expressing "unarmed" as a weapon (§7.15).

    **The weapon's damage type is looked up against the defender's species** and handed to
    ``resolve_attack`` as its ``resistance`` (CONTRACT-v6 §26.2, §26.3), so a PIERCE dagger
    does measurably less to a cave snake than a BLUNT club does, and the giant bat takes
    double from BLUNT. That is not flavour: with no alternative weapon, one resisting
    species takes floor clears from 45.6% to 8.5% (§0.5), which is the pressure the whole
    inventory exists to relieve. The defender's shield chance is ``0`` because **no
    monster in the bestiary carries a shield** — see :func:`_shield_block` for the rule
    that would apply if one ever did.

    A turn is consumed whether the blow lands or not, and the player does not move. A kill
    removes the monster, credits its experience and may level the character up. Events
    about a monster the player cannot see are dropped by :func:`_perceived`.

    Returns the state with the turn taken; the caller ticks the world.
    """
    target = _npc_at(state.npcs, cell)
    if target is None:
        # Nothing to hit: no turn, no event. Unreachable through `step`, which only ever
        # passes a cell it has just been told is occupied.
        return state

    rng = random.Random(
        roll_seed(state.master_seed, state.turns, _PLAYER_ACTOR_ID, _SALT_ATTACK)
    )
    resistance = resistance_of(target.species, weapon.damage_type)
    result = resolve_attack(
        rng,
        state.player_actor.actor,
        target.actor,
        weapon.damage_min,
        weapon.damage_max,
        strength_applies,
        0,
        resistance,
        # No monster owns a shield, so this is `_shield_block(None, ...)` by construction
        # — and it must be 0 rather than a floor, or every monster would block one arrow
        # in twenty with a shield it does not have.
        0,
    )

    name = _species_name(target)
    emitted: list[tuple[Event, Coord | None]] = []
    player = state.player_actor
    npcs = state.npcs

    if not result.hit:
        emitted.append((Event(EventKind.PLAYER_MISSED_NPC, name=name), cell))
    elif result.blocked:
        # Unreachable in v6 — nothing in the bestiary carries a shield — but the event
        # exists and this is the one place it could ever be said (§16 v6).
        emitted.append((Event(EventKind.NPC_SHIELD_BLOCKED, name=name), cell))
    else:
        emitted.append((Event(EventKind.PLAYER_HIT_NPC, name=name), cell))
        flavour = _RESISTANCE_EVENT.get(resistance)
        if flavour is not None:
            emitted.append((Event(flavour, name=name), cell))
        if result.killed:
            npcs = tuple(npc for npc in state.npcs if npc is not target)
            emitted.append((Event(EventKind.NPC_KILLED, name=name), cell))
            player = replace(
                player,
                xp=player.xp + xp_for_kill(SPECIES_DATA[target.species].xp_value, True),
            )
        else:
            npcs = tuple(
                replace(npc, actor=replace(npc.actor, hp=result.defender_hp))
                if npc is target
                else npc
                for npc in state.npcs
            )

    after = _take_turn(
        replace(state, npcs=npcs, player_actor=player, targeting=None),
        state.player,
        state.open_doors,
        _capped(_perceived(emitted, state.visible)),
    )
    return level_up(after)


def _target_cells(state: GameState) -> tuple[Coord, ...]:
    """Every cell the player could shoot right now, nearest first (CONTRACT-v5 §7.10).

    Exactly ``visible ∩ monster positions``, within the ranged weapon's range, sorted by
    ``(Chebyshev distance, coordinate)`` so cycling has a total order and never depends on
    iteration order.

    It reads ``state.visible`` and **must not** call
    :func:`roguelike.fov.has_line_of_sight`: the visible set is already computed, and two
    sources of truth for "can I see it" would let the screen and the target list disagree
    (CONTRACT-v5 §14 v5).
    """
    ranged = state.player_actor.inventory.ranged
    if ranged is None:
        # Nothing to shoot *with*, so nothing is shootable. §7.15 makes the refusal
        # `NO_TARGET`-style, and an empty list is exactly how that refusal is produced.
        return ()
    reach = ranged.range
    cells = [
        npc.position
        for npc in state.npcs
        if npc.position in state.visible
        and _chebyshev(state.player, npc.position) <= reach
    ]
    return tuple(sorted(cells, key=lambda cell: (_chebyshev(state.player, cell), cell)))


def _start_targeting(state: GameState) -> GameState:
    """``f`` with no target chosen yet: build the list, or say there is nothing to shoot.

    Costs no turn either way — choosing is not acting, exactly as with the ``w`` prefix.

    **Bare-handed, it always refuses** (§7.15, §11 v6): an empty ranged slot has no range,
    so :func:`_target_cells` returns nothing and the same ``NO_TARGET`` refusal comes out
    as for an empty room. There is no separate "you have no bow" wording, and no turn is
    spent finding out.
    """
    targets = _target_cells(state)
    if not targets:
        return replace(state, events=(Event(EventKind.NO_TARGET),))
    return _targeting_at(state, Targeting(targets, 0))


def _targeting_at(state: GameState, targeting: Targeting) -> GameState:
    """Store ``targeting`` and announce the monster it selects. Never a turn."""
    target = _npc_at(state.npcs, targeting.targets[targeting.index])
    name = "" if target is None else _species_name(target)
    return replace(
        state,
        targeting=targeting,
        events=(Event(EventKind.TARGETING, name=name),),
    )


def _fire(state: GameState) -> GameState:
    """``f`` while targeting: shoot the selected monster (CONTRACT-v5 §7.10).

    The list is rebuilt first. If the selected cell is no longer on it — the monster died
    or moved out of view between choosing and firing — the shot is **cancelled with no
    turn** and the player is left choosing again, or told there is nothing to shoot at.
    Otherwise the arrow flies: one turn, ``strength_applies=False``, targeting cleared.
    """
    targeting = state.targeting
    assert targeting is not None  # only ever reached with a target already chosen
    ranged = state.player_actor.inventory.ranged
    cell = targeting.targets[targeting.index]
    targets = _target_cells(state)
    if ranged is None or cell not in targets:
        if not targets:
            return replace(state, targeting=None, events=(Event(EventKind.NO_TARGET),))
        return _targeting_at(state, Targeting(targets, 0))
    # The flight path is recorded on the state, not drawn here: `step` stays pure and
    # `run` owns every clock in this project (CONTRACT-v4 §0.10). It is presentation
    # only -- the shot is already fully resolved by the time the arrow is drawn moving.
    after = _player_attack(state, cell, ranged, False)
    return replace(after, projectile=tuple(line_cells(state.player, cell)))


#: Experience a **monster** keeps from a kill, as a fraction of the victim's value:
#: one half, floored. Monster-versus-monster fighting does not exist yet, so today this
#: only ever divides in tests — but the rate is fixed here so that when such fights
#: arrive they cannot quietly breed a champion that out-levels the player by farming its
#: neighbours. The player always takes the full value.
NPC_XP_DIVISOR: int = 2


def xp_for_kill(xp_value: int, killer_is_player: bool) -> int:
    """Experience awarded for a kill, by who landed it (integer, never negative).

    The player takes the victim's full ``xp_value``. A monster takes **half, floored** —
    ``NPC_XP_DIVISOR`` — which keeps a long-lived monster from levelling as fast as the
    player would by killing the same things.

    **Fighting the player awards a monster nothing at all**, and not by arithmetic: a
    monster has no experience and no level to raise, so there is no field for this
    function to feed. The rule is enforced by the absence, which is the strongest form
    it can take; :func:`_npc_attacks_player` credits nobody, and a test asserts that the
    player's own experience is likewise untouched when a monster hits them.
    """
    if killer_is_player:
        return max(0, xp_value)
    return max(0, xp_value) // NPC_XP_DIVISOR


def _visible_hostile(state: GameState) -> str | None:
    """The nearest visible hostile's species name, or ``None`` if none is in view.

    Nearest by Chebyshev distance, ties broken by coordinate, so the name in the message
    is deterministic. Used both to refuse an automatic move and to stop one already
    running — the same question in both cases, asked in one place.
    """
    seen = [
        npc
        for npc in state.npcs
        if npc.position in state.visible and SPECIES_DATA[npc.species].hostile
    ]
    if not seen:
        return None
    nearest = min(seen, key=lambda npc: (_chebyshev(npc.position, state.player), npc.position))
    return SPECIES_DATA[nearest.species].name


def _hostile_in_view(state: GameState) -> bool:
    """Is any hostile monster currently visible? Used to refuse a rest."""
    return _visible_hostile(state) is not None


def _is_hostile_at(state: GameState, cell: Coord) -> bool:
    """Is the monster standing on ``cell`` hostile? ``False`` if nothing is there.

    Hostility is a property of the species (:attr:`roguelike.npc.SpeciesData.hostile`),
    so it is looked up rather than stored per monster.
    """
    for npc in state.npcs:
        if npc.position == cell:
            return SPECIES_DATA[npc.species].hostile
    return False


def describe_cell(state: GameState, cell: Coord) -> str:
    """What the look cursor reports about ``cell``. Never raises, never a turn.

    The rules follow what the character can honestly know, which is the whole point of
    the command — it must not become a map-and-monster oracle:

    * **Never seen** — says so, and reveals nothing else.
    * **Seen before but not now** — describes the *terrain* only, marked as remembered.
      A monster is never reported from memory, for the same reason the renderer refuses
      to draw one: it has moved.
    * **In view** — the player, then a monster (with its health band), then terrain.
      Whatever is standing there wins over the floor it stands on.

    All wording comes from :mod:`roguelike.events`; this function composes none.
    """
    if cell not in state.explored:
        return events.UNSEEN_DESCRIPTION

    if cell not in state.visible:
        tile = state.level.tile_at(*cell)
        return events.REMEMBERED_PREFIX + events.describe_terrain(
            tile.name, cell in state.open_doors
        )

    if cell == state.player:
        return events.describe_player(state.player_actor.actor.condition.name)

    for npc in state.npcs:
        if npc.position == cell:
            return events.describe_monster(
                SPECIES_DATA[npc.species].name, npc.actor.condition.name
            )

    tile = state.level.tile_at(*cell)
    return events.describe_terrain(tile.name, cell in state.open_doors)


def _look_at(state: GameState, cell: Coord) -> GameState:
    """Move the look cursor to ``cell`` and report what is there. **Never a turn.**

    Looking is free and unlimited by design: it is the player reading the screen, and
    charging a turn for that would make the information a resource rather than a
    courtesy. The world does not tick, so nothing moves while you study it.
    """
    return replace(
        state,
        look_cursor=cell,
        events=(Event(EventKind.LOOKING, name=describe_cell(state, cell)),),
    )


def _swap_with(state: GameState, cell: Coord) -> GameState:
    """Exchange places with the peaceful creature standing on ``cell``.

    Only ever reached for a **non-hostile** monster (hostiles are attacked instead), so
    this is not a way to shove an enemy aside. It exists so a harmless animal is not an
    impassable obstacle: in a one-cell corridor there is otherwise no way past it, and a
    route planned through its square fails for a reason the player cannot see or fix.

    Costs one turn — it is a move, and the world ticks accordingly. Field of view is
    recomputed from the player's new cell by :func:`_take_turn`, exactly as for any step.

    The monster is moved to the square the player just left, so no two actors ever share
    a cell and the occupancy invariant holds throughout.
    """
    swapped = tuple(
        replace(npc, position=state.player) if npc.position == cell else npc
        for npc in state.npcs
    )
    name = next(
        (SPECIES_DATA[npc.species].name for npc in state.npcs if npc.position == cell),
        None,
    )
    moved = replace(state, npcs=swapped)
    return _take_turn(
        moved,
        cell,
        state.open_doors,
        (Event(EventKind.SWAPPED_PLACES, name=name),)
        + _arrival_events(state, cell),
    )


def _adjacent_open_doors(state: GameState) -> tuple[Coord, ...]:
    """Every open door touching the player, in a fixed order.

    All eight neighbours — a door is reachable from a corner, and excluding diagonals
    would be an arbitrary rule the player learns by failing. Sorted so the answer is
    deterministic and never depends on iteration order.
    """
    x, y = state.player
    return tuple(
        sorted(
            (x + dx, y + dy)
            for dx, dy in DIRECTIONS
            if (x + dx, y + dy) in state.open_doors
        )
    )


def _close_at(state: GameState, target: Coord) -> GameState:
    """Shut the open door on ``target``, or say why not. Never moves the player.

    Costs one turn when it works, and recomputes field of view — shutting a door changes
    what can be seen, exactly as opening one does.

    Two ways it declines, neither costing a turn, because neither is an action: there is
    no open door there, or something is standing in the frame. A door cannot shut through
    a creature, and a monster in the doorway is exactly when a player most wants to try,
    so the refusal names it rather than failing silently.
    """
    if target not in state.open_doors:
        return replace(state, events=(Event(EventKind.NOTHING_TO_CLOSE),))
    for npc in state.npcs:
        if npc.position == target:
            return replace(
                state,
                events=(
                    Event(
                        EventKind.DOORWAY_BLOCKED,
                        name=SPECIES_DATA[npc.species].name,
                    ),
                ),
            )
    return _take_turn(
        state,
        state.player,
        state.open_doors - {target},
        (Event(EventKind.DOOR_CLOSED),),
    )


def _close_towards(state: GameState, dx: int, dy: int) -> GameState:
    """``c`` then a direction: shut the open door on the neighbouring cell.

    Reached only when **more than one** door touches the player — with a single one, ``c``
    shuts it outright and never asks. See :func:`_close_at` for what happens then.
    """
    return _close_at(state, (state.player[0] + dx, state.player[1] + dy))


def _attack_towards(state: GameState, dx: int, dy: int) -> GameState:
    """``F`` then a direction: attack the adjacent cell that way, without moving.

    The point of an explicit attack is that it **never** becomes a step. Walking into a
    monster already attacks it (CONTRACT-v5 §7.9), but that is no use when you want to
    hit something you might instead walk past, or to swing at a square you believe holds
    something. So this resolves an attack on the neighbouring cell whatever is there, and
    the player does not move either way.

    Swinging at an empty square **costs a turn** and says so. That is deliberate: a free
    swing would be a free probe, telling the player whether a cell is occupied at no cost,
    which is exactly the information a monster's turn is supposed to buy.

    **Hostility is not consulted here.** Bumping deliberately refuses to attack a peaceful
    creature, so this is the only way to hit one — picking a fight has to be possible, it
    just has to be deliberate.
    """
    target = (state.player[0] + dx, state.player[1] + dy)
    if any(npc.position == target for npc in state.npcs):
        return _player_attack(state, target, _melee_weapon(state.player_actor), True)
    return _take_turn(
        state,
        state.player,
        state.open_doors,
        (Event(EventKind.ATTACKED_NOTHING),),
    )


# --------------------------------------------------------------------------------------
# Chests, the pack and the inventory screen (CONTRACT-v6 §7.16 - §7.18)
# --------------------------------------------------------------------------------------


def _pick_up(state: GameState) -> GameState:
    """``g``: take one item out of the chest under the player (CONTRACT-v6 §7.16, §7.18).

    One item per turn, and **only the taking costs one**:

    * no chest on this cell — ``NOTHING_TO_PICK_UP``, **no turn** (§11 v6);
    * a chest that has been emptied — ``CHEST_EMPTY``, **no turn**. The contract charges a
      turn for *taking one item*, and there is none to take; this is the same rule as the
      empty cell above, and it means a player who keeps pressing ``g`` at an empty chest
      is not fed to the monsters by the message they are reading;
    * a full pack — ``PACK_FULL``, **no turn**, and nothing leaves the chest;
    * otherwise the first item moves from the chest to the pack, the chest is marked
      ``opened``, and the turn is spent.

    The first item out of a closed chest reports ``CHEST_OPENED`` — *"The chest holds:
    dagger"* — and every item after it reports ``PICKED_UP``. §16 v6 provides both
    sentences and each says exactly one thing: the first tells you what you have found,
    the rest tell you what you took. Saying both at once would read *"The chest holds:
    dagger You pick up the dagger."*

    An emptied chest stays on the map with ``opened=True`` (§7.18) — it is not removed and
    it does not change how it is drawn (T35 records that as a decision, not an oversight).

    Pure; the caller ticks the world, which by :func:`_tick_world` happens only in the one
    case that consumed a turn.
    """
    chest = _chest_at(state.chests, state.player)
    if chest is None:
        return replace(state, events=(Event(EventKind.NOTHING_TO_PICK_UP),))
    if not chest.contents:
        return replace(state, events=(Event(EventKind.CHEST_EMPTY),))

    player = state.player_actor
    item = chest.contents[0]
    inventory, taken = add(player.inventory, item)
    if not taken:
        return replace(state, events=(Event(EventKind.PACK_FULL),))

    emptied = replace(chest, contents=chest.contents[1:], opened=True)
    kind = EventKind.PICKED_UP if chest.opened else EventKind.CHEST_OPENED
    return _take_turn(
        replace(
            state,
            chests=tuple(emptied if held is chest else held for held in state.chests),
            player_actor=replace(player, inventory=inventory),
        ),
        state.player,
        state.open_doors,
        (Event(kind, name=item.name),),
    )


def _selected_item(state: GameState) -> object | None:
    """The carried item the inventory cursor is on, or ``None`` if it is on nothing.

    A cursor out of range is not an error: the pack shrinks when something is dropped,
    equipped or drunk, and asking about a slot that is no longer there simply answers
    "nothing".
    """
    carried = state.player_actor.inventory.carried
    if 0 <= state.inventory_cursor < len(carried):
        return carried[state.inventory_cursor]
    return None


def _use_or_equip(state: GameState) -> GameState:
    """``e`` on the inventory screen: wear it, wield it, or drink it (§7.17).

    **The dispatch happens here, before :func:`roguelike.items.equip` is called, and it
    has to**: ``equip`` raises :class:`ValueError` on a :class:`~roguelike.items.
    Consumable`, because a potion has no slot (T29's decision, CONTRACT-v6 §11 v6). "``e``
    equips *or uses*" is one key with two meanings and the meaning is decided by what the
    item is, never by catching the exception.

    Either way it **costs one turn** and closes the screen: a turn has passed, the world
    has moved, and what the player needs to see next is the map rather than their pack.
    With the cursor on nothing at all, nothing happens and no turn is spent.
    """
    item = _selected_item(state)
    if item is None:
        return state

    player = state.player_actor
    closed = replace(state, inventory_open=False, inventory_cursor=0)
    if isinstance(item, Consumable):
        return _consume(closed, state.inventory_cursor, item)
    if not isinstance(item, (Weapon, Shield)):
        # Nothing else can reach the pack — chests hold only §25.1's eleven items — so
        # this is the answer to a question that cannot be asked, chosen to be a no-op
        # rather than the ValueError `equip` would raise.
        return state

    return _take_turn(
        replace(
            closed,
            player_actor=replace(player, inventory=equip(player.inventory, item)),
        ),
        state.player,
        state.open_doors,
        (Event(EventKind.EQUIPPED, name=item.name),),
    )


def _consume(state: GameState, index: int, item: Consumable) -> GameState:
    """Drink or apply the consumable at ``index``, and spend the turn it costs.

    Two shapes, and an item may carry both (§25): ``heal`` is instant hit points, capped
    at ``max_hp`` through the same :func:`roguelike.stats.derive` as everything else;
    ``regen_turns``/``regen_magnitude`` become a ``REGENERATING`` status effect, which
    :func:`roguelike.status.apply_effect` **refreshes rather than stacks**, so a second
    bandage extends the mending instead of doubling it.

    The healing itself arrives a tick at a time through :func:`_tick_status`, exactly as
    poison damage does and through the same function — which is why a bandage and a snake
    bite running at once are reported as two separate numbers rather than one net.

    The item is consumed whether or not it did anything: it has been used up.
    """
    player = state.player_actor
    inventory, _used = drop(player.inventory, index)
    actor = player.actor
    emitted: list[Event] = []

    if item.heal:
        max_hp = derive(actor.stats).max_hp
        actor = replace(actor, hp=min(max_hp, actor.hp + item.heal))
        emitted.append(Event(EventKind.DRANK, name=item.name))
    if item.regen_turns and item.regen_magnitude:
        actor = replace(
            actor,
            status_effects=apply_effect(
                actor.status_effects,
                StatusEffect(
                    StatusKind.REGENERATING, item.regen_turns, item.regen_magnitude
                ),
            ),
        )
        emitted.append(Event(EventKind.BANDAGED))
    if not emitted:
        emitted.append(Event(EventKind.DRANK, name=item.name))

    return _take_turn(
        replace(state, player_actor=replace(player, actor=actor, inventory=inventory)),
        state.player,
        state.open_doors,
        tuple(emitted),
    )


def _drop_selected(state: GameState) -> GameState:
    """``d`` on the inventory screen: put the selected item down (§7.17).

    **It costs a turn**, which §7.17 does not say either way: it names equipping and
    drinking as costing one and opening, browsing and closing as costing none, and
    dropping is plainly an action rather than a look. Like the other two actions it closes
    the screen. Dropping onto a cursor that is on nothing does nothing and costs nothing.

    There is no item glyph on the floor in v6 — that is explicitly out of scope — so a
    dropped item is gone. The wording (``DROPPED``) is the contract's and is unchanged by
    that; picking it back up is a v7 problem.
    """
    item = _selected_item(state)
    if item is None:
        return state

    player = state.player_actor
    inventory, dropped = drop(player.inventory, state.inventory_cursor)
    if dropped is None:  # pragma: no cover - `_selected_item` already ruled this out
        return state

    return _take_turn(
        replace(
            state,
            inventory_open=False,
            inventory_cursor=0,
            player_actor=replace(player, inventory=inventory),
        ),
        state.player,
        state.open_doors,
        (Event(EventKind.DROPPED, name=dropped.name),),
    )


def inventory_key(state: GameState, key: int) -> GameState:
    """Apply one raw keystroke to the open inventory screen. Pure, and a rule, not a loop.

    **Why a raw key and not a :class:`~roguelike.keys.Command`.** The screen's keys are
    ``e``, ``d`` and the item letters, and §5 v6 is explicit that none of them may become a
    :class:`~roguelike.keys.CommandKind`: they are a sub-mode's alphabet, and binding them
    globally would cost the game three keys everywhere else in order to spend them in one
    place. ``translate_key`` therefore reports every one of them as ``UNKNOWN``, and the
    only way to tell an ``e`` from a ``d`` is to be handed the key itself. :func:`run` owns
    the keyboard and hands it over; every rule about what the key *means* is here.

    * an item letter (:data:`ITEM_LETTERS`) selects that item — **no turn**. A letter
      with no item beside it selects nothing, and so closes like any other key;
    * ``e`` equips or uses the selection, ``d`` drops it — **one turn each**, and the
      screen closes;
    * **any other key closes the screen** — no turn, no event, and the message that was on
      the map before it opened is still there.

    A dead or unopened state comes back untouched, so this is safe to call unconditionally.
    """
    if not state.running or not state.inventory_open:
        return state

    char = chr(key) if 0x20 <= key < 0x7F else ""

    if char == "e":
        return _tick_world(state, _use_or_equip(state))
    if char == "d":
        return _tick_world(state, _drop_selected(state))
    if char and char in ITEM_LETTERS:
        index = ITEM_LETTERS.index(char)
        if index < len(state.player_actor.inventory.carried):
            return replace(state, inventory_cursor=index)
        # A letter with no item beside it selects nothing, so it is one of the "any
        # other key" that close. Treating it as a silent no-op instead would leave an
        # empty pack answering to almost nothing — most of the alphabet is an item
        # letter — and a screen that ignores `q` reads as a stuck screen.

    return replace(state, inventory_open=False, inventory_cursor=0)


def _describe_item(item: object) -> str:
    """One line about one item: its name, and the number that makes it worth carrying.

    Grade is deliberately absent. It is a label and never a modifier (§25), so printing it
    beside the damage it does not change would read as though it did; the damage range,
    the damage type and the block chance are the whole of an item's effect.
    """
    if isinstance(item, Weapon):
        return (
            f"{item.name}  {item.damage_min}-{item.damage_max}"
            f" {item.damage_type.name.lower()}"
        )
    if isinstance(item, Shield):
        return f"{item.name}  blocks {item.block_chance}%"
    if isinstance(item, Consumable):
        if item.heal and item.regen_turns:
            return (
                f"{item.name}  heals {item.heal},"
                f" then {item.regen_magnitude} for {item.regen_turns} turns"
            )
        if item.heal:
            return f"{item.name}  heals {item.heal}"
        if item.regen_turns:
            return (
                f"{item.name}  mends {item.regen_magnitude} a turn"
                f" for {item.regen_turns}"
            )
        return item.name
    return str(item)  # pragma: no cover - nothing else ever reaches the pack


#: What an empty slot is called on the inventory screen. Bare hands are a state, not an
#: absence (§7.15), so the melee slot says what it *is* rather than what it lacks.
_NO_MELEE: str = "bare hands"
_NO_ITEM: str = "-"


def inventory_lines(state: GameState) -> tuple[str, ...]:
    """Every line of the inventory screen (CONTRACT-v6 §7.17).

    One equipment line, one blank, then the pack — one item per line, each behind the
    letter that selects it, with ``>`` against the selected one. That is twenty-two lines
    for a full pack, which is exactly the body of a default 22-row map, so the screen fits
    without pagination; :func:`roguelike.render.render_text_page` clips anything longer on
    a shorter level, as it does for the help.

    Layout only. The item names are the items' own, and the two sentences the screen shows
    the player — the footer, and whatever the action emits — come from
    :func:`format_inventory_status` and :mod:`roguelike.events` respectively.
    """
    inventory = state.player_actor.inventory
    melee = inventory.melee.name if inventory.melee is not None else _NO_MELEE
    ranged = inventory.ranged.name if inventory.ranged is not None else _NO_ITEM
    shield = inventory.shield.name if inventory.shield is not None else _NO_ITEM

    lines = [f"Melee: {melee}   Ranged: {ranged}   Shield: {shield}", ""]
    if not inventory.carried:
        lines.append("  (your pack is empty)")
        return tuple(lines)

    for index, item in enumerate(inventory.carried):
        letter = ITEM_LETTERS[index] if index < len(ITEM_LETTERS) else " "
        marker = ">" if index == state.inventory_cursor else " "
        lines.append(f"{marker} {letter}  {_describe_item(item)}")
    return tuple(lines)


def format_inventory_status(state: GameState) -> str:
    """The footer under the inventory: what the three keys do."""
    return "e wields or drinks  -  d drops  -  any other key returns"


def _tick_status(actor: Actor) -> tuple[Actor, int]:
    """Advance one actor's status effects by one world-tick (CONTRACT-v5 §22.3).

    Written once and used for the player and for every monster, which is the whole point
    of both composing a shared :class:`~roguelike.stats.Actor`. Ticking is **unconditional**
    — it does not wait for the actor's energy to cross the threshold, or poison could dodge
    a tick by being slow.

    :func:`roguelike.status.tick_effects` reports **damage and healing separately**, not a
    signed net (CONTRACT-v6, T31): a net of zero cannot tell "nothing happened" from
    "poison burned for 2 while a bandage closed 2", and those are different sentences. The
    two are applied together here and only the damage is handed back, because damage is
    the half that can kill and the half that has something to say — a bandage ticking is
    as quiet as natural regeneration.

    **Healing is capped at ``max_hp``** by the same :func:`roguelike.stats.derive` every
    other derived number in this project comes from; poison damage is not floored, because
    reaching zero is exactly how poison kills.

    Returns the actor with its effects advanced and the arithmetic already applied, plus
    the damage, so the caller can decide what to say about it.
    """
    if not actor.status_effects:
        return actor, 0
    effects, damage, healing = tick_effects(actor.status_effects)
    hp = actor.hp - damage + healing
    if healing:
        hp = min(hp, derive(actor.stats).max_hp)
    return replace(actor, hp=hp, status_effects=effects), damage


def _regenerate(player: Player) -> Player:
    """Count one tick towards natural healing, and heal on the tick that completes it.

    1 HP every :data:`roguelike.status.REGEN_TURNS` world-ticks, capped at ``max_hp``.
    **Monsters do not regenerate** (CONTRACT-v5 §22.4) — a deliberate asymmetry, without
    which disengaging from a fight would be pointless. It is also load-bearing rather than
    cosmetic: with no healing at all, 0.0% of floors are cleared against 61.5% with it
    (RESEARCH-v5 §7).

    At full health the counter does not run: there is nothing to count towards, so a player
    who has taken no damage waits the full :data:`~roguelike.status.REGEN_TURNS` ticks after
    being wounded rather than being healed by ticks banked while untouched. That is also
    what lets an uneventful tick return the state **unchanged** (§11.1) — the identity is a
    consequence of nothing having happened, and this is the only field that would otherwise
    creep on every turn of a quiet game.
    """
    actor = player.actor
    if actor.hp >= derive(actor.stats).max_hp:
        return player if player.regen_counter == 0 else replace(player, regen_counter=0)
    counter = player.regen_counter + 1
    if counter < REGEN_TURNS:
        return replace(player, regen_counter=counter)
    return replace(player, actor=replace(actor, hp=actor.hp + 1), regen_counter=0)


def _perceive(
    npc: NPC,
    level: Level,
    open_doors: frozenset[Coord],
    player: Coord,
    player_actor: Actor | None = None,
    rng: "random.Random | None" = None,
) -> NPC:
    """Update one monster's mind before it acts (CONTRACT-v5 §24.2).

    :func:`roguelike.npc.plan_action` is pure and returns only an intent — it never writes
    ``ai_state`` or ``memory``, so the transitions are written here, on the same perception
    rule it applies internally: within :data:`roguelike.npc.PERCEPTION_RADIUS` by Chebyshev
    distance **and** with line of sight, asked as ``(observer, target)`` with the **monster
    as the observer**. Permissive line of sight is measurably asymmetric, so those
    arguments are not interchangeable (CONTRACT-v5 §14 v5).

    * wandering and it sees you — it starts hunting, with a fresh memory;
    * hunting and it sees you — the trail is fresh again, memory back to zero;
    * hunting and it does not — memory grows, and past
      :data:`roguelike.npc.FORGET_TICKS` it gives up and goes back to wandering;
    * badly hurt, watching a healthier player, and unlucky on
      :func:`roguelike.npc.wants_to_flee` — it breaks off and runs. That is a one-way
      door: monsters do not heal, so nothing sends a fleeing creature back to hunting.

    **Memory counts actions, not ticks**, which is the choice this project makes where
    ``npc.py`` is indifferent: a bat at speed 180 acts twice on some ticks and forgets
    correspondingly sooner, which is what the energy model implies about a fast animal.
    """
    seen = _chebyshev(npc.position, player) <= PERCEPTION_RADIUS and fov.has_line_of_sight(
        level, open_doors, npc.position, player
    )

    # A monster that is losing badly, and can see it is losing, may break off. Checked
    # before anything else so a creature already running does not reconsider and a
    # creature about to die does not spend its last action closing in. Fleeing is a
    # one-way door: nothing here sends it back to hunting, because monsters do not heal.
    if (
        npc.ai_state is not AiState.FLEEING
        and seen
        and player_actor is not None
        and rng is not None
        and wants_to_flee(rng, npc, player_actor)
    ):
        return replace(npc, ai_state=AiState.FLEEING, memory=0)
    if npc.ai_state is AiState.FLEEING:
        return npc

    if npc.ai_state is AiState.WANDERING:
        if seen:
            return replace(npc, ai_state=AiState.HUNTING, memory=0)
        return npc
    if seen:
        return npc if npc.memory == 0 else replace(npc, memory=0)
    memory = npc.memory + 1
    if memory > FORGET_TICKS:
        return replace(npc, ai_state=AiState.WANDERING, memory=0)
    return replace(npc, memory=memory)


def _npc_attacks_player(
    npc: NPC,
    player: Player,
    rng: random.Random,
    emitted: list[tuple[Event, Coord | None]],
) -> Player:
    """Resolve one monster's attack on the player, and apply whatever it left behind.

    A natural attack, so ``strength_applies=False``: a species' bite range already encodes
    how strong it is, and adding its strength modifier on top counts the same fact twice
    (CONTRACT-v5 §23.2). :func:`roguelike.combat.resolve_attack` rolls the poison but never
    applies it — that is this call site's job, through
    :func:`roguelike.status.apply_effect`, which refreshes rather than stacks.

    **This is where a shield earns its place** (CONTRACT-v6 §23.5). Every bite in the
    bestiary is a melee attack, so the chance passed is the shield's plain ``block_chance``
    — see :func:`_shield_block`. A blocked blow deals no damage **and no poison**: the
    shield stops the venom with the fang, which is a rule of the draw order, not of this
    call site. Measured end to end, the three shields take floor clears from 45.6% bare to
    60.9 / 77.7 / 85.8 percent (§0.4).

    The player has **no resistance of their own**: resistance is a property of a species
    (§26.3) and the player is not one, so the default ``NORMAL`` is what applies.
    """
    data = SPECIES_DATA[npc.species]
    result = resolve_attack(
        rng,
        npc.actor,
        player.actor,
        data.attack_min,
        data.attack_max,
        False,
        data.poison_chance,
        Resistance.NORMAL,
        _shield_block(
            player.inventory.shield,
            False,
            player.actor.stats.agi,
            npc.actor.stats.agi,
        ),
    )
    if not result.hit:
        emitted.append((Event(EventKind.NPC_MISSED_PLAYER, name=data.name), npc.position))
        return player

    if result.blocked:
        emitted.append((Event(EventKind.SHIELD_BLOCKED), npc.position))
        return player

    emitted.append((Event(EventKind.NPC_HIT_PLAYER, name=data.name), npc.position))
    actor = replace(player.actor, hp=result.defender_hp)
    if result.poisoned:
        actor = replace(
            actor,
            status_effects=apply_effect(
                actor.status_effects,
                StatusEffect(StatusKind.POISONED, POISON_TURNS, POISON_MAGNITUDE),
            ),
        )
        emitted.append((Event(EventKind.POISONED), None))
    return replace(player, actor=actor)


def _dead(
    state: GameState,
    player: Player,
    npcs: tuple[NPC, ...],
    open_doors: frozenset[Coord],
    emitted: list[tuple[Event, Coord | None]],
) -> GameState:
    """End the run because the player's hit points reached zero (CONTRACT-v5 §7.12).

    One path for every cause — a blow, an arrow, poison — differing only in which events
    got there first. It is the exact shape of the existing ``LEFT_DUNGEON`` ending:
    ``running`` clears and ``outcome`` is set from the message table, so :func:`play` can
    print the farewell once the terminal has been restored. Any activity and any targeting
    are dropped, because there is nobody left to carry them out.
    """
    emitted.append((Event(EventKind.PLAYER_DIED), None))
    final = _capped(state.events + _perceived(emitted, state.visible))
    return replace(
        state,
        player_actor=player,
        npcs=npcs,
        open_doors=open_doors,
        events=final,
        running=False,
        outcome=events.message_for(final),
        activity=None,
        targeting=None,
    )


def advance_npcs(state: GameState) -> GameState:
    """Run one world-tick: status effects, then every monster's actions (§7.8). Pure.

    Called by :func:`step` and by :func:`advance` **immediately after any action that
    consumed a turn, and never otherwise**. A rejected move consumes no turn and therefore
    does not tick the world: walk into a wall and every monster is where it was, with the
    energy it had, and the player's hit points are untouched.

    The order within a tick is binding:

    1. **Status effects and regeneration first**, for the player and every monster, whether
       or not their energy crossed the threshold. Poison can kill; if it kills the player,
       the tick stops there and no monster acts.
    2. **Then every monster acts, in ``actor_id`` order**, by the energy rule: it banks its
       ``speed`` and acts once for each whole :data:`ENERGY_THRESHOLD` it holds. Speed 100
       is one action a tick; 180 is two on some ticks and one on others; 80 is eight
       actions in ten ticks. The loop is bounded by construction and has no iteration cap.
    3. Monsters reduced to zero hit points are removed and their experience credited.
    4. Events are filtered by what the player can see and capped (§16.1).

    **Each accepted move is folded into ``occupied`` before the next monster plans**, which
    is the only reason two monsters never take the same cell: ``plan_action`` is pure and
    sees one monster at a time, so the contention guarantee has to live in the caller.

    The player's field of view is **not** recomputed here — :func:`_take_turn` does that,
    as it always has. Moving monsters do not change what terrain is visible, and a door one
    of them opens is folded into ``open_doors`` for the next recomputation.

    **An empty level still ticks** (CONTRACT-v5 §11.1). Step 1 runs for the player whether
    or not a monster exists: only step 2 has nothing to do. Gating the whole function on
    ``state.npcs`` froze regeneration and poison the moment a floor was cleared — and since
    most of a level's turns are walked *after* its monsters are dead, and regeneration is
    what takes floor clears from 0.0% to 61.5% (RESEARCH-v5 §7), that quietly restored the
    unplayable balance. The state does come back **unchanged — the same object** when the
    tick genuinely changed nothing, which on an empty level means an unhurt, unpoisoned
    player; that is a consequence of the arithmetic, never a precondition tested first.
    """
    emitted: list[tuple[Event, Coord | None]] = []

    # --- 1. Status effects and regeneration, unconditionally, player first -------------
    # Unconditional in both senses: an actor whose energy never crossed the threshold still
    # burns (§22.3), and a level with no monsters on it still ticks (§11.1).
    player = state.player_actor
    actor, damage = _tick_status(player.actor)
    if damage:
        emitted.append((Event(EventKind.POISON_DAMAGE), None))
    if actor is not player.actor:
        player = replace(player, actor=actor)
    if actor.hp <= 0:
        return _dead(state, player, state.npcs, state.open_doors, emitted)
    player = _regenerate(player)

    npcs: list[NPC] = []
    for npc in state.npcs:
        npc_actor, _ = _tick_status(npc.actor)
        if npc_actor.hp <= 0:
            # Nothing in the v5 bestiary poisons a monster, so this is a seam rather than
            # live content — but a death is a death, and it credits its experience through
            # the same path a killing blow does.
            emitted.append((Event(EventKind.NPC_KILLED, name=_species_name(npc)), npc.position))
            player = replace(
                player,
                xp=player.xp + xp_for_kill(SPECIES_DATA[npc.species].xp_value, True),
            )
            continue
        npcs.append(replace(npc, actor=npc_actor))

    # --- 2. Every monster acts, in actor_id order. Nothing to do on an empty level, and
    # nothing that needs saying about it: the loop simply does not run.
    open_doors = state.open_doors
    occupied = frozenset(npc.position for npc in npcs) | {state.player}
    killed_the_player = False

    for index, npc in enumerate(npcs):
        wander_rng = random.Random(
            roll_seed(state.master_seed, state.turns, npc.actor_id, _SALT_WANDER)
        )
        attack_rng = random.Random(
            roll_seed(state.master_seed, state.turns, npc.actor_id, _SALT_ATTACK)
        )
        energy = npc.energy + derive(npc.actor.stats).speed
        while energy >= ENERGY_THRESHOLD:
            energy -= ENERGY_THRESHOLD
            npc = _perceive(
                npc,
                state.level,
                open_doors,
                state.player,
                player.actor,
                wander_rng,
            )
            action = plan_action(
                wander_rng, npc, state.level, open_doors, occupied, state.player
            )
            if action.kind is NpcActionKind.ATTACK:
                player = _npc_attacks_player(npc, player, attack_rng, emitted)
                if player.actor.hp <= 0:
                    killed_the_player = True
                    break
            elif action.kind is NpcActionKind.MOVE and action.target is not None:
                npc, open_doors, occupied = _npc_moves(
                    state, npc, action.target, open_doors, occupied, emitted
                )
        npcs[index] = replace(npc, energy=energy)
        if killed_the_player:
            break

    if killed_the_player:
        return _dead(state, player, tuple(npcs), open_doors, emitted)

    updated = tuple(npcs)
    if (
        player is state.player_actor
        and not emitted
        and updated == state.npcs
        and open_doors == state.open_doors
    ):
        # The tick happened and changed nothing — an unhurt, unpoisoned player on a level
        # with nothing left alive on it. Handing back the same object is worth the four
        # comparisons, and it is the *result* of the tick rather than a shortcut around it
        # (CONTRACT-v5 §11.1).
        return state

    # `level_up` is a no-op unless a monster died of poison during the status phase and
    # its experience crossed a threshold; the player's own kills are levelled where they
    # are resolved, before the world ticks at all.
    return level_up(
        replace(
            state,
            player_actor=player,
            npcs=updated,
            open_doors=open_doors,
            events=_capped(state.events + _perceived(emitted, state.visible)),
        )
    )


def _npc_moves(
    state: GameState,
    npc: NPC,
    target: Coord,
    open_doors: frozenset[Coord],
    occupied: frozenset[Coord],
    emitted: list[tuple[Event, Coord | None]],
) -> tuple[NPC, frozenset[Coord], frozenset[Coord]]:
    """Carry out one ``MOVE`` intent: step, or bump a door open (CONTRACT-v5 §24.2).

    **Only a species that opens doors may bump one open.** No animal can work a latch, so
    for the whole of today's bestiary a shut door is a wall: the planner will not route
    through one, and this refuses it a second time in case anything ever does. That
    belt-and-braces matters because the two checks answer different questions — the
    planner asks "where could I go?", this asks "what happens now?" — and a monster that
    slipped through the first would otherwise open a door it cannot work.

    A humanoid opens a closed door by the same rule the player does — it costs the action
    and does not move it — and the opening is announced **only if the door is somewhere the
    player can see**, which :func:`_perceived` decides from the coordinate handed along
    with the event. Off-screen monsters do not narrate themselves.

    ``occupied`` is rebuilt on every accepted step, so the next monster to plan sees the
    cell as taken. A target that has become impassable since it was planned is simply not
    taken; the action is spent either way.
    """
    if is_closed_door(state.level, open_doors, *target):
        if not SPECIES_DATA[npc.species].opens_doors:
            return npc, open_doors, occupied
        emitted.append((Event(EventKind.DOOR_OPENED), target))
        return npc, open_doors | {target}, occupied
    if target not in occupied and is_passable(state.level, open_doors, *target):
        return (
            replace(npc, position=target),
            open_doors,
            (occupied - {npc.position}) | {target},
        )
    return npc, open_doors, occupied


def _tick_world(before: GameState, after: GameState) -> GameState:
    """Tick the world iff the transition just made actually consumed a turn.

    The single guard behind v1's headline rule as v5 extends it: **no turn, no
    world-tick**. Comparing the two turn counters means the rule cannot be forgotten at
    one call site out of six, and a run that has already ended — the player climbed out,
    or died — is never ticked again.
    """
    if after.turns == before.turns or not after.running:
        return after
    return advance_npcs(after)


def step(state: GameState, command: Command) -> GameState:
    """Apply one player command and return the resulting state.

    Pure: ``state`` is never mutated (it is frozen, and its ``saved`` dict is rebuilt
    rather than written to), nothing is drawn, no terminal is touched, and no I/O happens.
    Every game rule in the project lives here.

    - :attr:`~roguelike.keys.CommandKind.QUIT` clears ``running`` and leaves everything
      else — ``turns``, ``player``, the three sets, ``events`` — alone. Quitting is not a
      turn, it changes nothing about what can be seen, and it emits no event.
    - :attr:`~roguelike.keys.CommandKind.UNKNOWN` is an ordinary no-op, not an error, so
      the state comes back untouched, ``events`` included: the last message stays on
      screen (numpad ``5`` and every unbound key land here).
    - :attr:`~roguelike.keys.CommandKind.MOVE` delegates the whole collision question to
      :func:`roguelike.movement.try_move`, passing the current ``open_doors`` so a closed
      door counts as impassable and the current occupancy so a monster does. Collision is
      not re-derived here. There are four outcomes:

      * **accepted** — the player moves, the turn counter advances, field of view is
        recomputed for the new position, and stepping onto a staircase says so.
      * **blocked by a monster** — bump-to-attack (CONTRACT-v5 §7.9). The melee weapon is
        swung, the turn counter advances, the player does **not** move, and
        ``PLAYER_HIT_NPC`` or ``PLAYER_MISSED_NPC`` is emitted. There is no attack command
        and no attack key: walking into something *is* the attack, which is the
        ADOM/NetHack convention this project already follows for doors.
      * **blocked by a closed door** — bump-to-open. The door joins ``open_doors``, the
        turn counter advances, the player does **not** move, field of view is recomputed
        (opening a door is precisely a change in what can be seen), and ``DOOR_OPENED`` is
        emitted. The next move in the same direction walks through.
      * **blocked by anything else** (a wall, the border, off the map) — the state comes
        back untouched, turn counter and message included, **and the world does not
        tick**: no monster moves, no poison burns, nothing regenerates. "A rejected move
        consumes no turn" is v1's headline rule and it is unchanged; ``visible`` is the
        identical set afterwards, never a recomputed equal one. There is deliberately no
        "you bump into a wall" event — it would fire on every misstep.

    - :attr:`~roguelike.keys.CommandKind.DESCEND` and
      :attr:`~roguelike.keys.CommandKind.ASCEND` take the staircase under the player, or,
      off the stairs, start travelling to the nearest one already found — or say there is
      none, exactly as in v3. Ascending from level 1 leaves the dungeon and ends the game.
      See :func:`_descend` and :func:`_ascend`.
    - :attr:`~roguelike.keys.CommandKind.AUTO_EXPLORE` starts an activity and costs no
      turn; every step of the exploration is taken by :func:`advance`. Pressing it with
      nothing left to explore is not special-cased here — the first :func:`advance` finds
      no frontier, clears the activity and reports it, still without a turn
      (CONTRACT-v4 §11).
    - :attr:`~roguelike.keys.CommandKind.WALK_PREFIX` is half a command: it sets
      ``awaiting_walk``, asks which way, and costs no turn. The **next** command is
      swallowed by the prefix whatever it is — a ``MOVE`` starts the walk, and anything
      else (including ``QUIT``) is a typo that clears the prefix and does nothing at all,
      not even replacing the message (CONTRACT-v4 §7.4, §11).
    - :attr:`~roguelike.keys.CommandKind.FIRE` and
      :attr:`~roguelike.keys.CommandKind.TARGET_NEXT` are the ranged sub-mode
      (CONTRACT-v5 §7.10), which costs no turn until the arrow actually flies. ``f`` with
      nothing in range says so; ``f`` with something in range starts choosing; ``Tab``
      cycles and wraps; ``f`` again fires and consumes the turn. **Any other key while
      choosing cancels**, and is swallowed whole exactly like a mistyped ``w`` prefix — no
      turn, no action, not even a new message.
    - :attr:`~roguelike.keys.CommandKind.PICK_UP` takes one item out of the chest under
      the player and **costs a turn**; with no chest there, an emptied one, or a full
      pack it reports why and costs none (CONTRACT-v6 §7.16, §7.18). See :func:`_pick_up`.
    - :attr:`~roguelike.keys.CommandKind.INVENTORY` opens the inventory screen and
      **costs no turn, ever** — like the help and look screens. The screen's own keys do
      not arrive here at all: they are raw keys with no ``CommandKind``, and
      :func:`inventory_key` is what interprets them (§5 v6, §7.17).
    - **Any command clears a running activity first.** The loop normally cancels before
      it ever gets here, but the rule cannot depend on that: a command the player typed is
      always about now, never about the walk they had started.
    - **Every command that consumes a turn ticks the world exactly once**
      (:func:`advance_npcs`), and every command that does not, does not.
    - A state that has stopped running is returned as-is whatever the command, so a key
      that arrives after the last one cannot resurrect the game.

    Raises:
        ValueError: if a ``MOVE`` command carries a ``dx`` or ``dy`` outside
            ``{-1, 0, 1}`` — propagated unchanged from
            :func:`roguelike.movement.try_move`, which owns that rule.
            :func:`roguelike.keys.translate_key` never produces such a command.
    """
    if not state.running:
        return state

    # A flight path belongs to exactly the turn that produced it. Clearing it here means
    # no later frame can redraw a stale arrow, whatever the next command turns out to be.
    if state.projectile:
        state = replace(state, projectile=())

    # The help screen swallows every key, so it is answered before anything else — even
    # before an activity is cleared, because reading the help is not a game action and
    # must not disturb a walk in progress.
    if state.help_page is not None:
        return _turn_help_page(state)

    # The inventory screen is driven by raw keys through `inventory_key`, because its
    # alphabet deliberately has no `CommandKind` (§5 v6) — so a *command* arriving while
    # it is open is a key `run` never routes here. It is answered the way the screen
    # answers every key it has no use for: close, no turn, message untouched.
    if state.inventory_open:
        return replace(state, inventory_open=False, inventory_cursor=0)

    if state.look_cursor is not None:
        if command.kind is CommandKind.MOVE:
            x = state.look_cursor[0] + command.dx
            y = state.look_cursor[1] + command.dy
            if state.level.in_bounds(x, y):
                return _look_at(state, (x, y))
            # At the edge the cursor simply stays put and re-reports; nothing is
            # gained by beeping at the player and nothing costs a turn either way.
            return _look_at(state, state.look_cursor)
        # Any other key closes the cursor. No turn, no action, and the message is
        # cleared so the description does not linger over the restored map.
        return replace(state, look_cursor=None, events=())

    if state.activity is not None:
        state = replace(state, activity=None)

    if state.awaiting_walk:
        state = replace(state, awaiting_walk=False)
        if command.kind is CommandKind.MOVE:
            refusal = _refuse_automatic_move(state)
            if refusal is not None:
                return refusal
            return replace(
                state,
                activity=Activity(
                    ActivityKind.AUTO_WALK, direction=(command.dx, command.dy)
                ),
            )
        # A typo, not an error: the prefix is dropped, the command is swallowed whole,
        # and the message already on screen is left alone.
        return state

    if state.awaiting_close:
        state = replace(state, awaiting_close=False)
        if command.kind is CommandKind.MOVE:
            return _close_towards(state, command.dx, command.dy)
        # Same rule as every other prefix: a typo is swallowed whole and costs nothing.
        return state

    if state.awaiting_attack:
        state = replace(state, awaiting_attack=False)
        if command.kind is CommandKind.MOVE:
            return _tick_world(state, _attack_towards(state, command.dx, command.dy))
        # Same rule as the walk prefix: a typo is swallowed whole and costs nothing.
        return state

    if state.targeting is not None:
        if command.kind is CommandKind.FIRE:
            return _tick_world(state, _fire(state))
        if command.kind is CommandKind.TARGET_NEXT:
            targeting = state.targeting
            return _targeting_at(
                state,
                replace(
                    targeting,
                    index=(targeting.index + 1) % len(targeting.targets),
                ),
            )
        # Anything else cancels the shot and is consumed by the cancelling, exactly like a
        # mistyped `w` prefix: no turn, no action, and the message stays as it was.
        return replace(state, targeting=None)

    if command.kind is CommandKind.QUIT:
        return replace(state, running=False)

    if command.kind is CommandKind.WALK_PREFIX:
        return replace(
            state,
            awaiting_walk=True,
            events=(Event(EventKind.WALK_WHICH_WAY),),
        )

    if command.kind is CommandKind.AUTO_EXPLORE:
        refusal = _refuse_automatic_move(state)
        if refusal is not None:
            return refusal
        return replace(state, activity=Activity(ActivityKind.AUTO_EXPLORE))

    if command.kind is CommandKind.HELP:
        return replace(state, help_page=0)

    if command.kind is CommandKind.INVENTORY:
        # **No turn, ever** (§7.16) — like the help and look screens. Opening it is
        # reading the screen, and the world does not move while you read.
        return replace(state, inventory_open=True, inventory_cursor=0)

    if command.kind is CommandKind.PICK_UP:
        return _tick_world(state, _pick_up(state))

    if command.kind is CommandKind.REST:
        # Refused outright with something already in view. `interruption` only fires on
        # a hostile that *newly* appears, so without this the player would settle down
        # beside a jackal and be woken by its teeth.
        if _hostile_in_view(state):
            return replace(state, events=(Event(EventKind.CANNOT_REST),))
        if state.player_actor.actor.hp >= derive(
            state.player_actor.actor.stats
        ).max_hp:
            return replace(state, events=(Event(EventKind.RESTED),))
        return replace(
            state,
            activity=Activity(ActivityKind.REST),
            events=(Event(EventKind.RESTING),),
        )

    if command.kind is CommandKind.LOOK:
        return _look_at(state, state.player)

    if command.kind is CommandKind.CLOSE:
        # Only ask when the question is real. With one door beside you there is nothing
        # to disambiguate, and making the player name a direction they have no choice
        # about is a keystroke that carries no information.
        doors = _adjacent_open_doors(state)
        if not doors:
            # A different sentence from the one a wrong direction earns: nothing was
            # aimed at, so "that way" would be answering a question nobody asked.
            return replace(state, events=(Event(EventKind.NO_DOOR_ADJACENT),))
        if len(doors) == 1:
            return _close_at(state, doors[0])
        return replace(
            state,
            awaiting_close=True,
            events=(Event(EventKind.CLOSE_WHICH_WAY),),
        )

    if command.kind is CommandKind.ATTACK:
        return replace(
            state,
            awaiting_attack=True,
            events=(Event(EventKind.ATTACK_WHICH_WAY),),
        )

    if command.kind is CommandKind.FIRE:
        return _start_targeting(state)

    if command.kind is CommandKind.MOVE:
        result = try_move(
            state.level,
            state.player,
            command.dx,
            command.dy,
            state.open_doors,
            _occupied(state),
        )
        if result.blocked_by_npc is not None:
            # Bumping attacks a hostile and only a hostile. Killing something harmless
            # must be a decision, not a mistyped direction — the explicit attack
            # (`a` + direction) still hits a peaceful creature, and is the only way.
            if _is_hostile_at(state, result.blocked_by_npc):
                return _tick_world(
                    state,
                    _player_attack(
                        state,
                        result.blocked_by_npc,
                        _melee_weapon(state.player_actor),
                        True,
                    ),
                )
            # Walking into a peaceful creature **swaps places with it**. Without this a
            # harmless animal standing in a corridor is an impassable wall that cannot
            # be removed except by murdering it, and any route planned through its cell
            # silently fails. Swapping costs the ordinary turn a move costs.
            return _tick_world(state, _swap_with(state, result.blocked_by_npc))
        if result.moved:
            return _tick_world(
                state,
                _take_turn(
                    state,
                    result.position,
                    state.open_doors,
                    _arrival_events(state, result.position),
                ),
            )
        if result.blocked_by_door is not None:
            return _tick_world(
                state,
                _take_turn(
                    state,
                    state.player,
                    state.open_doors | {result.blocked_by_door},
                    (Event(EventKind.DOOR_OPENED),),
                ),
            )
        # Walked into a wall, the border or off the map: nothing happened at all, so
        # nothing is recomputed, no turn is spent, the world does not tick, and the last
        # message stays up.
        return state

    if command.kind is CommandKind.DESCEND:
        return _tick_world(state, _descend(state))

    if command.kind is CommandKind.ASCEND:
        return _tick_world(state, _ascend(state))

    # CommandKind.UNKNOWN, and CommandKind.TARGET_NEXT with nothing being targeted —
    # nothing happens, and nothing that happened is undone.
    return state


# --------------------------------------------------------------------------------------
# Activities — one turn at a time, and still pure (CONTRACT-v4 §7.5, §7.6)
# --------------------------------------------------------------------------------------


#: What each ``walk_step`` stop reason is called when the player reads it. The reason
#: strings are the ones CONTRACT-v4 §19.2 fixes; the wording behind each kind lives, as
#: ever, in :data:`roguelike.events.MESSAGES` and nowhere near here.
_WALK_STOPPED: dict[str, EventKind] = {
    "blocked": EventKind.NOTHING_FURTHER,
    "intersection": EventKind.STOPPED_AT_JUNCTION,
    "opening": EventKind.STOPPED_AT_OPENING,
}

#: What an activity says when the very next cell of its plan turns out to be unenterable.
#: Unreachable in practice — every cell a planner proposes is planning-passable, and the
#: only planning-passable cell ``try_move`` refuses is a closed door, which is bumped open
#: instead — so this is the answer to a question the code should never be asked, chosen to
#: match what the same activity says when it runs out of route.
_ACTIVITY_BLOCKED: dict[ActivityKind, EventKind] = {
    ActivityKind.TRAVEL: EventKind.NOTHING_FURTHER,
    ActivityKind.AUTO_EXPLORE: EventKind.EXPLORED_EVERYTHING,
    ActivityKind.AUTO_WALK: EventKind.NOTHING_FURTHER,
}


def _finished(state: GameState, kind: EventKind) -> GameState:
    """Clear the activity and say why it ended. Exactly one event, never a turn."""
    return replace(state, activity=None, events=(Event(kind),))


def _planned_step(
    state: GameState, activity: Activity
) -> tuple[Coord, None] | tuple[None, EventKind]:
    """Where the activity wants to go next, or why it is over. Never both.

    The whole of "which cell?" for all three kinds, and the only place any of them is
    planned. Each kind is re-planned from scratch every turn — there is no path to carry
    forward and nothing that can go stale (CONTRACT-v4 §7.5).

    * ``AUTO_WALK`` delegates to :func:`roguelike.activity.walk_step`, which decides
      locally and reports its own stop reason.
    * ``TRAVEL`` arrives when the player is standing on the goal, and otherwise searches
      for it over explored ground.
    * ``AUTO_EXPLORE`` heads for the nearest frontier. **The player's own cell is
      subtracted from the goals**: standing on a frontier would make the search return a
      one-cell path with no step in it, and the activity would stall silently rather than
      finish. It does not arise at the default sight radius, where every neighbour is
      already seen, and it costs one set difference to make impossible.

    Both searches use the same explored-only predicate, so neither can route the character
    over ground they have never seen.
    """
    if activity.kind is ActivityKind.AUTO_WALK:
        target, reason = walk_step(
            _whole_level_passable(state),
            state.player,
            activity.came_from,
            activity.direction,
        )
        return (target, None) if target is not None else (None, _WALK_STOPPED[reason])

    if activity.kind is ActivityKind.TRAVEL:
        if activity.goal is None:
            return (None, EventKind.NOTHING_FURTHER)
        if state.player == activity.goal:
            return (None, EventKind.ARRIVED)
        route = find_path(_explored_passable(state), state.player, {activity.goal})
        if route is None:
            return (None, EventKind.NOTHING_FURTHER)
        return (route[1], None)

    goals = frontier_cells(state.level, state.explored, state.open_doors) - {state.player}
    if not goals:
        return (None, EventKind.EXPLORED_EVERYTHING)
    route = find_path(_explored_passable(state), state.player, goals)
    if route is None:
        return (None, EventKind.EXPLORED_EVERYTHING)
    return (route[1], None)


def advance(state: GameState) -> GameState:
    """Perform exactly one turn of the activity in progress and return the new state.

    Pure, in the same sense and to the same degree as :func:`step`: no mutation, no
    terminal, no I/O, no clock. The pacing that makes an activity watchable belongs to
    :func:`run` and lives nowhere else (CONTRACT-v4 §0.10), which is why the whole of
    auto-explore can be driven to completion in a test with no screen anywhere.

    With no activity — or on a game that has stopped — the state comes back **unchanged**,
    the same object, so calling this on an idle state is free and harmless.

    Otherwise the activity is asked where to go next (:func:`_planned_step`). Finishing
    clears the activity and emits exactly one event saying why: ``ARRIVED``,
    ``NOTHING_FURTHER``, ``EXPLORED_EVERYTHING``, ``STOPPED_AT_JUNCTION`` or
    ``STOPPED_AT_OPENING``. Otherwise the step is taken **by the same rules as a ``MOVE``
    command** — the same :func:`roguelike.movement.try_move`, the same turn counter, the
    same field of view recomputation, the same stair messages — so that a closed door on
    the route is bumped open, costs its turn, reports itself, and the activity carries
    straight on (user decision 3). Not stopping for a door is what lets auto-explore reach
    the far side of the level at all.

    ``came_from`` is carried forward for an auto-walk that actually moved, which is what
    lets the corridor rule tell "onwards" from "back the way I came". A bump does not move
    the player, so it does not disturb it.

    Nothing here descends, ascends, or touches ``depth``: auto-explore stops on the
    current level and hands control back (user decision 1). An activity cannot survive a
    level change either, because every command clears it and only a command can change
    level.
    """
    activity = state.activity
    if activity is None or not state.running:
        return state

    # Resting is the one activity that is not a move: there is no cell to plan, only a
    # turn to let pass. It is handled before `_planned_step`, which exists to answer
    # "which way?" and has nothing to say here.
    if activity.kind is ActivityKind.REST:
        return _rest_a_turn(state)

    target, stopped = _planned_step(state, activity)
    if target is None:
        return _finished(state, stopped)

    dx = target[0] - state.player[0]
    dy = target[1] - state.player[1]
    result = try_move(
        state.level, state.player, dx, dy, state.open_doors, _occupied(state)
    )
    if result.moved:
        after = _take_turn(
            state,
            result.position,
            state.open_doors,
            _arrival_events(state, result.position),
        )
        if activity.kind is ActivityKind.AUTO_WALK:
            after = replace(after, activity=replace(activity, came_from=state.player))
    elif result.blocked_by_door is not None:
        after = _take_turn(
            state,
            state.player,
            state.open_doors | {result.blocked_by_door},
            (Event(EventKind.DOOR_OPENED),),
        )
    elif result.blocked_by_npc is not None:
        # An automatic walk never turns into a fight: there is no auto-fight in this
        # project, so a monster standing on the route ends the activity and hands control
        # back to the player, who can then choose to swing at it.
        return _finished(state, EventKind.INTERRUPTED)
    else:
        return _finished(state, _ACTIVITY_BLOCKED[activity.kind])

    after = advance_npcs(after)
    if not after.running:
        # The tick killed the player. There is nothing left to interrupt.
        return replace(after, activity=None)

    interrupted = interruption(state, after)
    if interrupted is not None:
        # **Appended, not substituted** (CONTRACT-v5 §7.14 amending §7.5): replacing the
        # turn's events would throw away `The jackal hits you.` in favour of a bare
        # `You stop.` and leave the player with no idea why they stopped.
        return replace(
            after,
            activity=None,
            events=_capped(after.events + (interrupted,)),
        )
    return after


def _rest_a_turn(state: GameState) -> GameState:
    """Let one turn pass while the player sits still, or stop because there is no need.

    Resting is deliberately **not** a fast-forward: it takes real turns, one per call,
    so the world ticks exactly as it would if the player pressed a key each time —
    monsters move, poison burns, wounds close at the ordinary
    :data:`roguelike.status.REGEN_TURNS` rate. Nothing is skipped and nothing is free.

    It ends of its own accord at full health. Everything else that ends it — a hostile
    coming into view, taking damage, being poisoned — is :func:`interruption`'s job, the
    same seam that stops travel and auto-explore, so resting needed no stopping rules of
    its own.
    """
    player = state.player_actor
    if player.actor.hp >= derive(player.actor.stats).max_hp:
        return _finished(state, EventKind.RESTED)
    # A turn spent doing nothing at all: no move, no attack, just the clock.
    return _tick_world(state, _take_turn(state, state.player, state.open_doors, ()))


def interruption(before: GameState, after: GameState) -> Event | None:
    """Should the turn just taken stop the activity, and if so, saying what?

    The v4 seam, now live (CONTRACT-v5 §7.14). It shipped returning ``None`` in every case
    because the three conditions the user named — *seeing a hostile, receiving damage, a
    character state change* — all needed monsters and hit points. Both now exist, and all
    three are computable from the two states this function already receives. In priority
    order:

    1. **A hostile is in view at all** — not merely one that has just appeared. Returns
       ``SPOTTED_HOSTILE`` naming the species, choosing the nearest by Chebyshev distance
       and breaking ties by coordinate so the answer is total and reproducible.
       "Newly visible" was the original rule and it was too weak: an activity that began
       before something wandered into sight would keep walking past a monster that had
       been on screen the whole time.
    …checked *after* the two below, which say things the player cannot see for
       themselves.

    2. **The player took damage** — hit points went down, from any cause. ``INTERRUPTED``.
    3. **The character's state changed** — a status effect of a kind that was not there
       before. ``INTERRUPTED``.

    Otherwise ``None``. **Opening a door still does not interrupt** (v4 user decision 3):
    the door opens, costs its turn, says so, and the walk carries on.

    Nothing is lost by stopping every turn a visible monster shuffles: the activity is
    over on the first such turn, so there is no second one to stop. And an automatic move
    cannot be *started* with a hostile in view either (:func:`_refuse_automatic_move`),
    so the two rules together mean automatic movement only ever happens on an empty
    screen.

    This is not a nicety: two jackals beat a baseline player 100% of the time, so an
    auto-explore that walks into a pack and keeps walking is a death with no chance to
    react.

    Still **one pure function** of the two states around a turn — deliberately not a
    registry, an observer list or a plugin mechanism. v4 said so with one condition and it
    is still the right shape with three.
    """
    # Damage and status changes answer first: they say something the player cannot see
    # for themselves, and "The jackal hits you. You stop." reads better than being told
    # about a jackal that has been on screen for ten turns.
    if after.player_actor.actor.hp < before.player_actor.actor.hp:
        return Event(EventKind.INTERRUPTED)

    gained = {effect.kind for effect in after.player_actor.actor.status_effects} - {
        effect.kind for effect in before.player_actor.actor.status_effects
    }
    if gained:
        return Event(EventKind.INTERRUPTED)

    # ANY hostile in view stops an automatic move, not merely one that just appeared.
    # "Newly visible" was too weak: an activity started before something wandered into
    # sight, or resumed after a stop, would happily keep walking past a jackal that had
    # been on screen the whole time. The player asked for the reins back the moment
    # anything hostile can be seen, and that is the condition that means it.
    watching = [
        npc
        for npc in after.npcs
        if npc.position in after.visible and SPECIES_DATA[npc.species].hostile
    ]
    if watching:
        nearest = min(
            watching,
            key=lambda npc: (_chebyshev(after.player, npc.position), npc.position),
        )
        return Event(EventKind.SPOTTED_HOSTILE, name=_species_name(nearest))

    return None


# --------------------------------------------------------------------------------------
# Chrome text (CONTRACT-v3 §7.2)
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# The help screen
# --------------------------------------------------------------------------------------


#: How the two columns of a help entry are laid out. The key column is wide enough for
#: the longest entry in `keys.HELP_ENTRIES` ("move into a monster") with a gap after it.
_HELP_KEY_WIDTH: int = 22


def help_lines(state: GameState) -> tuple[str, ...]:
    """Every line of the help screen, before pagination.

    Built from :data:`roguelike.keys.HELP_ENTRIES`, which lives beside the binding tables,
    so a key and its description cannot drift apart. This function owns only the *layout*
    of those pairs — two columns — and no wording of its own.
    """
    return tuple(
        f"{keys:<{_HELP_KEY_WIDTH}}{description}"
        for keys, description in HELP_ENTRIES
    )


def help_page_count(state: GameState) -> int:
    """How many pages the help occupies at this level's height. Always at least 1."""
    body = max(1, state.level.height)
    lines = len(help_lines(state))
    return max(1, -(-lines // body))  # ceiling division, integer only


def help_page_lines(state: GameState) -> tuple[str, ...]:
    """The lines of the page currently on screen, or ``()`` when help is not showing."""
    if state.help_page is None:
        return ()
    body = max(1, state.level.height)
    start = state.help_page * body
    return help_lines(state)[start : start + body]


def _turn_help_page(state: GameState) -> GameState:
    """Advance the help by one page, closing it after the last (CONTRACT §7 v5 style).

    **Any key turns the page**, and turning past the last page closes the screen. One
    rule, no special keys to remember, and no way to get stuck: keep pressing and you are
    back in the dungeon. It costs **no turn** and emits no event — reading the help is not
    a game action, so the world does not tick and the message already on screen survives.

    This mirrors the ``w``-prefix and the ranged-targeting sub-mode, which likewise
    swallow the next key whole rather than interpreting it as a command.
    """
    page = (state.help_page or 0) + 1
    if page >= help_page_count(state):
        return replace(state, help_page=None)
    return replace(state, help_page=page)


def format_help_status(state: GameState) -> str:
    """The footer under the help: which page this is, and how to leave."""
    total = help_page_count(state)
    page = (state.help_page or 0) + 1
    if total == 1:
        return "Page 1/1  -  any key returns to the game"
    return f"Page {page}/{total}  -  any key continues"


def format_stats(state: GameState) -> str:
    """The top chrome row: ``HP h/m  Lv l  XP x/n  Str s Agi a Vit v`` (§7.13).

    The row was reserved for player statistics and stood empty for three versions because
    there were none. There are now, so it is filled in: current and maximum hit points,
    character level, experience towards the next one, and the three primary stats.

    The health band in brackets is the same five-word vocabulary a monster is described
    with under the look cursor, so "am I worse off than that thing?" is a comparison of
    two phrases from one scale rather than a guess.

    ``max_hp`` comes from :func:`roguelike.stats.derive`, like every other derived value in
    this project — there is no second HP formula here or anywhere. Two spaces between
    fields and one inside the stat block, as everywhere else. A plain ``str``; fitting it
    to the terminal is the renderer's job (CONTRACT-v3 §4.2).
    """
    player = state.player_actor
    stats = player.actor.stats
    derived = derive(stats)
    return (
        f"HP {player.actor.hp}/{derived.max_hp}"
        f" ({events.CONDITION_WORDS[player.actor.condition.name]})"
        f"  Lv {player.level}"
        f"  XP {player.xp}/{xp_to_next(player.level)}"
        f"  Str {stats.str_} Agi {stats.agi} Vit {stats.vit}"
    )


def format_status_right(state: GameState) -> str:
    """The right-hand half of the status row: ``"Level {depth}  Seed {master_seed}"``.

    Two spaces between the fields, as everywhere else in this project. The **master** seed,
    not the current level's derived generator seed: the master seed is what reproduces the
    whole run, and a player reading it off the screen wants to be able to replay from it.

    A plain ``str``; fitting it to the terminal is the renderer's job (CONTRACT-v3 §4.2),
    and this half is the half that wins when the message would collide with it.
    """
    return f"Level {state.depth}  Seed {state.master_seed}"


# --------------------------------------------------------------------------------------
# The loop (CONTRACT-v3 §7.3, CONTRACT-v4 §7.7)
# --------------------------------------------------------------------------------------


#: The deadline, in milliseconds, that :func:`run` gives a keypress while an activity is
#: running. Measured on a real terminal: it delivers both the ten-turns-per-second cap
#: (9 ticks in 1.0 s with no input) and instant cancellation (a key already waiting comes
#: back in 0.00 ms). It is the *only* pacing mechanism in this project — there is no
#: sleep, no clock read and no busy-wait anywhere (CONTRACT-v4 §0.10).
_ACTIVITY_TICK_MS: int = 100

#: What ``getch`` returns when that deadline passes with no key: nothing was typed, so
#: the activity gets another turn.
_NO_KEY: int = -1

#: The deadline that means "no deadline" — ordinary play, where ``getch`` blocks until
#: the player types something and the game spends no cycles waiting. Curses spells this
#: with the same number it returns for "nothing arrived"; they are unrelated meanings and
#: are named apart here so neither can be mistaken for the other.
_BLOCKING: int = -1

#: Milliseconds the arrow rests on each cell of its flight. Short enough that a
#: full-room shot is over in a fraction of a second, long enough to read as motion
#: rather than a flicker. Delivered by the same `stdscr.timeout` that paces activities —
#: this project has no `sleep` and no clock read anywhere, and must not grow one
#: (CONTRACT-v4 §0.10).
_PROJECTILE_FRAME_MS: int = 25

#: How long the arrow rests on the square it struck before the frame is redrawn without
#: it. A quarter of a second: long enough to see where the shot landed, short enough that
#: nobody mistakes it for something lying on the floor. Without this the marker stayed on
#: screen until the next keypress, which read as a persistent object that is not there.
_IMPACT_HOLD_MS: int = 250


def _target_cell(state: GameState) -> Coord | None:
    """The cell carrying the highlight, or ``None``. The renderer reverses it.

    Two cursors share one highlight: the ranged-target cursor and the look cursor. They
    are mutually exclusive by construction — look mode swallows every key, so no shot can
    be lined up while it is open — so one field on the frame serves both and the renderer
    needs to know about neither. Look wins if both are somehow set, since it is the one
    the player is actively steering.
    """
    if state.look_cursor is not None:
        return state.look_cursor
    if state.targeting is None:
        return None
    return state.targeting.targets[state.targeting.index]


def _chest_cells(state: GameState) -> frozenset[Coord]:
    """Where the chests are, as the renderer wants them: bare coordinates.

    A chest reaches the renderer as plain positions, because ``render.py`` may not import
    ``loot.py`` (CONTRACT-v6 §10 v6) — and unlike a monster it needs nothing else, since
    every chest draws with the same glyph whether it is full or empty (T35). The whole set
    goes over every time: *when* a chest may be drawn is decided inside the pure renderer,
    which draws one from ``explored`` because — unlike a monster — a chest does not move.
    """
    return frozenset(chest.position for chest in state.chests)


def _npc_glyphs(state: GameState) -> tuple[render.NpcGlyph, ...]:
    """The monsters as the renderer wants them: position, glyph, species name.

    A monster reaches the renderer as plain data, because ``render.py`` may not import
    ``npc.py`` (CONTRACT-v5 §10 v5). Glyph and name come from the same ``SPECIES_DATA``
    entry, so they cannot disagree. The whole list goes over every time: "draw a monster
    only where it can be seen, never from memory" is enforced inside the pure renderer,
    not by caller discipline.
    """
    return tuple(
        render.NpcGlyph(
            position=npc.position,
            glyph=SPECIES_DATA[npc.species].glyph,
            species=SPECIES_DATA[npc.species].name,
        )
        for npc in state.npcs
    )


def _projectile_frame(state: GameState, cell: Coord) -> list[list[render.Cell]]:
    """One frame of the arrow's flight, with the missile drawn at ``cell``.

    Frame *construction* only — no clock, no keyboard, no drawing. The loop owns the
    pacing, so ``stdscr.timeout`` still appears in exactly one function in this module
    (CONTRACT-v4 §0.10), and this stays a pure function of a state and a coordinate.
    """
    return render.render_to_cells(
        state.level,
        state.player,
        state.visible,
        state.explored,
        state.open_doors,
        render.Chrome(
            stats=format_stats(state),
            message=events.message_for(state.events),
            status_right=format_status_right(state),
        ),
        _npc_glyphs(state),
        None,
        cell,
        _chest_cells(state),
    )


def _cancelled(state: GameState) -> GameState:
    """Stop the activity because the player pressed something. One event, no turn.

    Separate from :func:`run` because it is a rule and :func:`run` holds none, and
    separate from :func:`step` because the key that caused it is **consumed by the
    cancellation** and never reaches :func:`step` as a command — otherwise a panicked
    keypress would stop the walk *and* move you into whatever you were fleeing
    (CONTRACT-v4 §7.7).
    """
    return replace(state, activity=None, events=(Event(EventKind.INTERRUPTED),))


def run(stdscr, state: GameState) -> GameState:
    """Run the turn loop on an already-initialised curses window until the game ends.

    A shell around :func:`step`, :func:`advance` and the renderer, holding no game rules:
    it composes the chrome text, renders the current state to cells, blits them, asks for
    a key, and hands the result to whichever of the two owns the next transition.
    ``stdscr`` must already be initialised — :func:`play` does that, and nothing here does.

    **The inventory screen is the one place a key is not translated.** Its keys — ``e``,
    ``d`` and the item letters — have no :class:`~roguelike.keys.CommandKind` by design
    (CONTRACT-v6 §5 v6), so while it is open the raw key goes to :func:`inventory_key`
    instead of to :func:`step`. That is a routing decision, not a rule: what the key
    *means* is decided there, with everything else this module knows.

    **The keyboard is read two different ways, and that is the whole of the v4 loop.**
    With no activity in progress it blocks for a key exactly as v3 did (``timeout(-1)``)
    and steps. With one, it gives the key a 100 ms deadline: a key that arrives cancels
    the activity and **is consumed doing so**, and a deadline that passes with no key —
    ``getch`` returning ``-1`` — is one more turn of the activity. That single call is
    both the pace (about ten turns a second, fast enough to watch and slow enough to read)
    and the cancellation, which is why there is no timer, no sleep and no polling loop
    here or anywhere else.

    Colour pairs are allocated once, here, immediately after curses comes up and before
    the first frame; :func:`roguelike.render.init_colors` degrades to monochrome by itself
    on a terminal without colour, and the extra guard covers the rest.

    The two window settings are best-effort: hiding the cursor raises on terminals that
    cannot do it, and neither setting is worth failing a game over.

    Returns:
        The final :class:`GameState`, so the caller can read ``outcome`` off it once the
        terminal has been restored.
    """
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    try:
        stdscr.keypad(True)
    except curses.error:
        pass
    try:
        render.init_colors()
    except curses.error:
        pass

    while state.running:
        # Only the *drawing* differs here. The keyboard is read below by exactly the same
        # two paths as always — one deadline for an activity, one for ordinary play — so
        # the help screen adds no third way to read a key, and no rule of its own here.
        if state.help_page is not None:
            # The help replaces the frame entirely: no map, no monsters, no cursor. It
            # goes through the same `draw`, because `render_text_page` hands back a frame
            # of exactly the same shape.
            cells = render.render_text_page(
                help_page_lines(state),
                render.Chrome(
                    stats="Keys",
                    message=format_help_status(state),
                    status_right="",
                ),
                state.level.width,
                state.level.height,
            )
        elif state.inventory_open:
            # The inventory is the second full-screen page, and it needed no new drawing
            # code at all (T35): same `render_text_page`, same frame shape, same `draw`.
            cells = render.render_text_page(
                inventory_lines(state),
                render.Chrome(
                    stats="Inventory",
                    message=format_inventory_status(state),
                    status_right="",
                ),
                state.level.width,
                state.level.height,
            )
        else:
            chrome = render.Chrome(
                stats=format_stats(state),
                message=events.message_for(state.events),
                status_right=format_status_right(state),
            )
            cells = render.render_to_cells(
                state.level,
                state.player,
                state.visible,
                state.explored,
                state.open_doors,
                chrome,
                _npc_glyphs(state),
                _target_cell(state),
                None,
                _chest_cells(state),
            )
        render.draw(stdscr, cells)

        # A shot just resolved: play the arrow's flight before asking for the next key.
        # Purely a view of a turn that has already happened — the damage was dealt, the
        # monster possibly killed and the message composed by `step`, so skipping the
        # animation entirely would change nothing but what the player sees.
        #
        # This is the project's only animation and it introduces NO new timing mechanism:
        # each frame waits on the same `stdscr.timeout` that paces an activity, so there
        # is still no `sleep`, no clock read and no busy-wait anywhere (CONTRACT-v4 §0.10).
        # A keypress cuts the flight short — the arrow has already landed, and a player
        # hammering keys should not be made to wait. The first cell is skipped: it is the
        # player's own square, where blanking the `@` for a frame would look like a bug.
        if state.projectile:
            stdscr.timeout(_PROJECTILE_FRAME_MS)
            interrupted = False
            for cell in state.projectile[1:]:
                render.draw(stdscr, _projectile_frame(state, cell))
                if stdscr.getch() != _NO_KEY:
                    interrupted = True
                    break
            # Hold the impact briefly, then redraw the frame built above — the one
            # without the arrow in it. The marker must not outlive the moment: nothing
            # is lying on that square, and leaving it there until the next keypress
            # says otherwise. A player who is already pressing keys skips the hold.
            if not interrupted:
                stdscr.timeout(_IMPACT_HOLD_MS)
                stdscr.getch()
            render.draw(stdscr, cells)

        if state.activity is not None:
            stdscr.timeout(_ACTIVITY_TICK_MS)
            key = stdscr.getch()
            state = advance(state) if key == _NO_KEY else _cancelled(state)
        elif state.inventory_open:
            # The one place a key is not translated. The inventory screen's alphabet has
            # no `CommandKind` on purpose (§5 v6), so `translate_key` would flatten `e`,
            # `d` and every item letter into a single `UNKNOWN` and the screen could not
            # work at all. The key goes over raw and `inventory_key` decides what it
            # means — the rule stays in this module, the keyboard stays in this function.
            stdscr.timeout(_BLOCKING)
            state = inventory_key(state, stdscr.getch())
        else:
            stdscr.timeout(_BLOCKING)
            state = step(state, translate_key(stdscr.getch()))

    return state


def play(seed: int, width: int = 80, height: int = 22) -> None:
    """Start a new run from ``seed`` and play it.

    The only place in the codebase that initialises curses. ``curses.wrapper`` guarantees
    ``endwin()`` on both a normal return and an exception — including
    ``KeyboardInterrupt`` — so the terminal is always restored (v1 BRIEF Q16).

    The farewell is printed **after** ``wrapper`` returns, so it lands on a restored
    terminal rather than into a window that is about to be torn down. Quitting with ``q``
    sets no ``outcome`` and prints nothing.
    """
    state = curses.wrapper(run, new_game(seed, width, height))
    if state.outcome:
        print(state.outcome)
