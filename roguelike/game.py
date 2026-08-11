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

This is the only module permitted to import this widely: :mod:`roguelike.level`,
:mod:`roguelike.keys`, :mod:`roguelike.movement`, :mod:`roguelike.render`,
:mod:`roguelike.fov`, :mod:`roguelike.world`, :mod:`roguelike.dungeon`,
:mod:`roguelike.events`, :mod:`roguelike.pathfind`, :mod:`roguelike.activity`,
:mod:`roguelike.stats`, :mod:`roguelike.items`, :mod:`roguelike.status`,
:mod:`roguelike.combat` and :mod:`roguelike.npc` (CONTRACT-v5 §10 v5).
"""

from __future__ import annotations

import curses
import random
from dataclasses import dataclass, replace

from roguelike import dungeon, events, fov, render
from roguelike.activity import Activity, ActivityKind, frontier_cells, walk_step
from roguelike.combat import resolve_attack
from roguelike.events import Event, EventKind
from roguelike.items import DAGGER, SHORTBOW, Weapon
from roguelike.keys import HELP_ENTRIES, Command, CommandKind, translate_key
from roguelike.level import Level
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
    spawn_npcs,
)
from roguelike.pathfind import Coord, Passable, find_path, line_cells, octile
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

    There is deliberately no player position here: you always re-enter a level at a known
    staircase, never where you happened to be standing.
    """

    level: Level
    explored: frozenset[tuple[int, int]]
    open_doors: frozenset[tuple[int, int]]
    npcs: tuple[NPC, ...] = ()


@dataclass(frozen=True)
class Player:
    """The player character: a shared :class:`~roguelike.stats.Actor` core plus the
    things only a player has (CONTRACT-v5 §7 v5).

    ``actor`` is the same type every monster carries, which is why
    :func:`roguelike.combat.resolve_attack` is written once and used by both sides of
    every fight.

    **Inventory is static.** ``melee`` and ``ranged`` are set at :func:`new_game` and
    never change: there is no pickup, no drop, no ground item and no ammunition count
    anywhere (CONTRACT-v5 §21). Ammo is infinite, so there is no counter to go out of
    sync.

    ``regen_counter`` counts world-ticks towards the next point of natural healing
    (:data:`roguelike.status.REGEN_TURNS`). It is a plain integer on the state rather than
    a timer somewhere, because everything about a turn has to be reproducible from the
    state alone.
    """

    actor: Actor
    melee: Weapon = DAGGER
    ranged: Weapon = SHORTBOW
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
    look_cursor: Coord | None = None
    projectile: tuple[Coord, ...] = ()


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
        npcs=_populate(level),
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


def _populate(level: Level) -> tuple[NPC, ...]:
    """Spawn a freshly generated level's monsters from that level's own seed (§24.4).

    The one place :func:`roguelike.npc.spawn_npcs` is called. A level is populated exactly
    once — when it is first generated — and its monsters thereafter live on the state or,
    once the player has left, in the :class:`LevelState` filed under its depth. Coming
    back down a staircase never re-rolls them.
    """
    return spawn_npcs(random.Random(level.seed), level)


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
    is found mid-fight. ``targeting`` is dropped, because the cells it names belong to a
    level that is no longer under the player's feet.
    """
    saved = {
        **state.saved,
        state.depth: LevelState(
            state.level, state.explored, state.open_doors, state.npcs
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
        # A level is populated once, when it is generated (CONTRACT-v5 §24.4).
        npcs = _populate(level)
    else:
        level = below.level
        explored = below.explored
        open_doors = below.open_doors
        npcs = below.npcs

    return _change_level(
        state,
        depth,
        level,
        target,
        explored,
        open_doors,
        npcs,
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
    EventKind.POISONED: 2,
    EventKind.POISON_DAMAGE: 2,
    EventKind.PLAYER_HIT_NPC: 3,
    EventKind.PLAYER_MISSED_NPC: 3,
}

_DEFAULT_PRIORITY: int = 4

#: The two events that are kept even when the monster responsible is out of sight: the
#: player always perceives what happens to the player (CONTRACT-v5 §16.1). Everything else
#: about an unseen monster is dropped — there is no ambient "you hear scurrying" in this
#: project, for the same reason there is no "you bump into a wall".
_ALWAYS_PERCEIVED: frozenset[EventKind] = frozenset(
    {EventKind.NPC_HIT_PLAYER, EventKind.POISONED}
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


def _player_attack(
    state: GameState, cell: Coord, weapon: Weapon, strength_applies: bool
) -> GameState:
    """Resolve one attack by the player on the monster standing on ``cell``.

    The single place a player attack is resolved, shared by bump-to-attack melee (§7.9)
    and by a fired shot (§7.10); the two differ only in the weapon and in whether strength
    applies. **Ranged weapons pass ``strength_applies=False``** even though they are
    wielded — a bow's power is the bow's, and :func:`roguelike.combat.resolve_attack` has
    no way to tell a bow from a dagger, so getting this right is this call site's job.

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
    result = resolve_attack(
        rng,
        state.player_actor.actor,
        target.actor,
        weapon.damage_min,
        weapon.damage_max,
        strength_applies,
    )

    name = _species_name(target)
    emitted: list[tuple[Event, Coord | None]] = []
    player = state.player_actor
    npcs = state.npcs

    if not result.hit:
        emitted.append((Event(EventKind.PLAYER_MISSED_NPC, name=name), cell))
    else:
        emitted.append((Event(EventKind.PLAYER_HIT_NPC, name=name), cell))
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
    reach = state.player_actor.ranged.range
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
    cell = targeting.targets[targeting.index]
    targets = _target_cells(state)
    if cell not in targets:
        if not targets:
            return replace(state, targeting=None, events=(Event(EventKind.NO_TARGET),))
        return _targeting_at(state, Targeting(targets, 0))
    # The flight path is recorded on the state, not drawn here: `step` stays pure and
    # `run` owns every clock in this project (CONTRACT-v4 §0.10). It is presentation
    # only -- the shot is already fully resolved by the time the arrow is drawn moving.
    after = _player_attack(state, cell, state.player_actor.ranged, False)
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
        + _stair_events(state.level, cell),
    )


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
        return _player_attack(state, target, state.player_actor.melee, True)
    return _take_turn(
        state,
        state.player,
        state.open_doors,
        (Event(EventKind.ATTACKED_NOTHING),),
    )


def _tick_status(actor: Actor) -> tuple[Actor, int]:
    """Advance one actor's status effects by one world-tick (CONTRACT-v5 §22.3).

    Written once and used for the player and for every monster, which is the whole point
    of both composing a shared :class:`~roguelike.stats.Actor`. Ticking is **unconditional**
    — it does not wait for the actor's energy to cross the threshold, or poison could dodge
    a tick by being slow.

    Returns the actor with its effects advanced and the damage already subtracted, plus
    that damage, so the caller can decide what to say about it.
    """
    if not actor.status_effects:
        return actor, 0
    effects, damage = tick_effects(actor.status_effects)
    return replace(actor, hp=actor.hp - damage, status_effects=effects), damage


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
    )
    if not result.hit:
        emitted.append((Event(EventKind.NPC_MISSED_PLAYER, name=data.name), npc.position))
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

    A monster opens a closed door by the same rule the player does — it costs the action
    and does not move it — and the opening is announced **only if the door is somewhere the
    player can see**, which :func:`_perceived` decides from the coordinate handed along
    with the event. Off-screen monsters do not narrate themselves.

    ``occupied`` is rebuilt on every accepted step, so the next monster to plan sees the
    cell as taken. A target that has become impassable since it was planned is simply not
    taken; the action is spent either way.
    """
    if is_closed_door(state.level, open_doors, *target):
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
            return replace(
                state,
                activity=Activity(
                    ActivityKind.AUTO_WALK, direction=(command.dx, command.dy)
                ),
            )
        # A typo, not an error: the prefix is dropped, the command is swallowed whole,
        # and the message already on screen is left alone.
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
        return replace(state, activity=Activity(ActivityKind.AUTO_EXPLORE))

    if command.kind is CommandKind.HELP:
        return replace(state, help_page=0)

    if command.kind is CommandKind.LOOK:
        return _look_at(state, state.player)

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
                        state, result.blocked_by_npc, state.player_actor.melee, True
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
                    _stair_events(state.level, result.position),
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
            _stair_events(state.level, result.position),
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


def interruption(before: GameState, after: GameState) -> Event | None:
    """Should the turn just taken stop the activity, and if so, saying what?

    The v4 seam, now live (CONTRACT-v5 §7.14). It shipped returning ``None`` in every case
    because the three conditions the user named — *seeing a hostile, receiving damage, a
    character state change* — all needed monsters and hit points. Both now exist, and all
    three are computable from the two states this function already receives. In priority
    order:

    1. **A hostile comes into view** — a monster standing somewhere that is in
       ``after.visible`` and was not in ``before.visible``. Returns ``SPOTTED_HOSTILE``
       naming the species, choosing the nearest by Chebyshev distance and breaking ties by
       coordinate so the answer is total and reproducible.
    2. **The player took damage** — hit points went down, from any cause. ``INTERRUPTED``.
    3. **The character's state changed** — a status effect of a kind that was not there
       before. ``INTERRUPTED``.

    Otherwise ``None``. **Opening a door still does not interrupt** (v4 user decision 3):
    the door opens, costs its turn, says so, and the walk carries on. Neither does a
    monster that was already visible merely moving — the condition is about a position
    coming into view, not about a monster twitching in plain sight, or an auto-explore
    would stop every turn a rat shuffled.

    This is not a nicety: two jackals beat a baseline player 100% of the time, so an
    auto-explore that walks into a pack and keeps walking is a death with no chance to
    react.

    Still **one pure function** of the two states around a turn — deliberately not a
    registry, an observer list or a plugin mechanism. v4 said so with one condition and it
    is still the right shape with three.
    """
    appeared = [
        npc
        for npc in after.npcs
        if npc.position in after.visible and npc.position not in before.visible
    ]
    if appeared:
        nearest = min(
            appeared,
            key=lambda npc: (_chebyshev(after.player, npc.position), npc.position),
        )
        return Event(EventKind.SPOTTED_HOSTILE, name=_species_name(nearest))

    if after.player_actor.actor.hp < before.player_actor.actor.hp:
        return Event(EventKind.INTERRUPTED)

    gained = {effect.kind for effect in after.player_actor.actor.status_effects} - {
        effect.kind for effect in before.player_actor.actor.status_effects
    }
    if gained:
        return Event(EventKind.INTERRUPTED)

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
