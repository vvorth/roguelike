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

This is the only module permitted to import this widely: :mod:`roguelike.level`,
:mod:`roguelike.keys`, :mod:`roguelike.movement`, :mod:`roguelike.render`,
:mod:`roguelike.fov`, :mod:`roguelike.world`, :mod:`roguelike.dungeon` and
:mod:`roguelike.events` (CONTRACT-v3 §10).
"""

from __future__ import annotations

import curses
from dataclasses import dataclass, replace

from roguelike import dungeon, events, fov, render
from roguelike.events import Event, EventKind
from roguelike.keys import Command, CommandKind, translate_key
from roguelike.level import Level
from roguelike.movement import try_move

__all__ = [
    "LevelState",
    "GameState",
    "new_game",
    "step",
    "format_stats",
    "format_status_right",
    "run",
    "play",
]


@dataclass(frozen=True)
class LevelState:
    """A level the player has left, and everything about it that cannot be re-derived.

    The ``Level`` itself *could* be regenerated — :func:`roguelike.dungeon.level_for` is
    deterministic — but ``explored`` and ``open_doors`` are runtime facts about a
    particular game, and losing them would reset the fog every time the player climbed a
    staircase. Since the store has to exist for those two, it carries the level too.

    There is deliberately no player position here: you always re-enter a level at a known
    staircase, never where you happened to be standing.
    """

    level: Level
    explored: frozenset[tuple[int, int]]
    open_doors: frozenset[tuple[int, int]]


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

    Field order is binding (CONTRACT-v3 §7): everything without a default comes first.
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
    )


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
    """
    saved = {
        **state.saved,
        state.depth: LevelState(state.level, state.explored, state.open_doors),
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
    )


def _descend(state: GameState) -> GameState:
    """Take the down-staircase under the player, or report that there isn't one.

    The level below is restored from ``saved`` when the player has been there before, and
    generated otherwise — with ``required_up`` set to the coordinate being descended from,
    so the new level's up-staircase lands exactly there (G14) and the player's ``(x, y)``
    is unchanged by the descent. The world moves, not the player.

    Off the stairs this emits ``NO_STAIRS_DOWN`` and consumes **no turn**: it is a
    mistyped key, not an action.
    """
    stairs_down = state.level.stairs_down
    if not stairs_down or state.player != stairs_down[0]:
        return replace(state, events=(Event(EventKind.NO_STAIRS_DOWN),))

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
    else:
        level = below.level
        explored = below.explored
        open_doors = below.open_doors

    return _change_level(
        state,
        depth,
        level,
        target,
        explored,
        open_doors,
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

    Off the stairs this emits ``NO_STAIRS_UP`` and consumes no turn.
    """
    if state.level.stairs_up is None or state.player != state.level.stairs_up:
        return replace(state, events=(Event(EventKind.NO_STAIRS_UP),))

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
        (Event(EventKind.ASCENDED, depth=depth),),
    )


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
      door counts as impassable. Collision is not re-derived here. There are three
      outcomes:

      * **accepted** — the player moves, the turn counter advances, field of view is
        recomputed for the new position, and stepping onto a staircase says so.
      * **blocked by a closed door** — bump-to-open. The door joins ``open_doors``, the
        turn counter advances, the player does **not** move, field of view is recomputed
        (opening a door is precisely a change in what can be seen), and ``DOOR_OPENED`` is
        emitted. The next move in the same direction walks through.
      * **blocked by anything else** (a wall, the border, off the map) — the state comes
        back untouched, turn counter and message included. "A rejected move consumes no
        turn" is v1's headline rule and it is unchanged; ``visible`` is the identical set
        afterwards, never a recomputed equal one. There is deliberately no "you bump into
        a wall" event — it would fire on every misstep.

    - :attr:`~roguelike.keys.CommandKind.DESCEND` and
      :attr:`~roguelike.keys.CommandKind.ASCEND` take the staircase under the player, or,
      if there is none there, say so and cost nothing. Ascending from level 1 leaves the
      dungeon and ends the game. See :func:`_descend` and :func:`_ascend`.
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

    if command.kind is CommandKind.QUIT:
        return replace(state, running=False)

    if command.kind is CommandKind.MOVE:
        result = try_move(
            state.level, state.player, command.dx, command.dy, state.open_doors
        )
        if result.moved:
            return _take_turn(
                state,
                result.position,
                state.open_doors,
                _stair_events(state.level, result.position),
            )
        if result.blocked_by_door is not None:
            return _take_turn(
                state,
                state.player,
                state.open_doors | {result.blocked_by_door},
                (Event(EventKind.DOOR_OPENED),),
            )
        # Walked into a wall, the border or off the map: nothing happened at all, so
        # nothing is recomputed, no turn is spent, and the last message stays up.
        return state

    if command.kind is CommandKind.DESCEND:
        return _descend(state)

    if command.kind is CommandKind.ASCEND:
        return _ascend(state)

    # CommandKind.UNKNOWN — nothing happens, and nothing that happened is undone.
    return state


# --------------------------------------------------------------------------------------
# Chrome text (CONTRACT-v3 §7.2)
# --------------------------------------------------------------------------------------


def format_stats(state: GameState) -> str:
    """The top chrome row: **empty**.

    The row is reserved for player stats — hit points, level, that sort of thing — and
    none of them exist yet. Inventing something to fill it (a turn counter, a coordinate
    readout) would be inventing a feature, so it stays blank until there is something true
    to put there. The renderer pads it to width.
    """
    return ""


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
# The loop (CONTRACT-v3 §7.3)
# --------------------------------------------------------------------------------------


def run(stdscr, state: GameState) -> GameState:
    """Run the turn loop on an already-initialised curses window until the game ends.

    A shell around :func:`step` and the renderer, holding no game rules: it composes the
    chrome text, renders the current state to cells, blits them, blocks for a key, turns it
    into a :class:`~roguelike.keys.Command` and hands both to :func:`step`. ``stdscr`` must
    already be initialised — :func:`play` does that, and nothing here does.

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
        )
        render.draw(stdscr, cells)
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
