"""Game loop and curses lifecycle — the one place the whole system is wired together.

The loop is split in two so that every game rule is testable without a terminal
(CONTRACT §0.3, §7; CONTRACT-v2 §7):

1. :func:`step` is **pure**. It takes a :class:`GameState` and a
   :class:`~roguelike.keys.Command` and returns a new :class:`GameState`. It carries all
   of the turn logic — quitting, rejected moves costing no turn, bump-to-open, the turn
   counter, and when field of view is recomputed — and it never touches the terminal and
   never draws.
2. :func:`run` is a thin shell with no rules of its own: render, draw, read a key,
   translate it, :func:`step`, repeat. Anything decidable without a terminal belongs in
   :func:`step`.

This module holds the state; :mod:`roguelike.render` draws it. The two never meet:
:class:`GameState` is defined here and imported by nothing, because the renderer is handed
primitives instead — a :class:`~roguelike.level.Level`, a position, three frozensets and a
string (v1 BRIEF Q14, CONTRACT-v2 §7). That is what let the renderer be written by someone
who never spoke to this module's author, and it is why nothing here writes to the screen.

v2 adds fog of war and openable doors, and both land squarely in :func:`step`:

* **Passability is no longer a property of the map alone** (CONTRACT-v2 §0.6). A closed
  door is terrain-walkable but currently impassable, and which doors are open is mutable
  play state. It lives here, in :attr:`GameState.open_doors`, and is threaded explicitly
  into :func:`roguelike.movement.try_move` and :func:`roguelike.fov.compute_visible`. No
  module reaches for it globally.
* **Bump-to-open** (RESEARCH-v2 §6): walking into a closed door opens it, costs a turn and
  leaves you where you stood; the next move walks through. There is deliberately no ``o``
  command and no door-closing command.
* **Field of view is recomputed on exactly two transitions** — an accepted move and a door
  opening — because they are exactly the two things that change what can be seen. A
  rejected move, an unknown key and quitting all leave ``visible`` untouched, which is
  also why a rejected move is indistinguishable from no input at all.
* **``explored`` only ever grows** (``explored | visible``): ground once seen is
  remembered dimly forever, and the initial state is not blank — :func:`new_game` computes
  the first field of view immediately, so you can see where you stand.

Every coordinate here is ``(x, y)`` with the origin at the top-left and ``y`` growing
down, so "up" is ``dy = -1`` (CONTRACT §0.1). The ``(y, x)`` inversion curses needs lives
inside :func:`roguelike.render.draw` and appears nowhere in this file.

``curses`` is imported at module top level, which touches nothing. Terminal initialisation
happens in exactly one place in the whole codebase: the ``curses.wrapper`` call inside
:func:`play`. ``wrapper`` restores the terminal on a normal return *and* on any exception,
``KeyboardInterrupt`` included, which is the clean quit path (v1 BRIEF Q16).

This is the only module permitted to import this widely: :mod:`roguelike.level`,
:mod:`roguelike.keys`, :mod:`roguelike.movement`, :mod:`roguelike.render`,
:mod:`roguelike.generator` and :mod:`roguelike.fov` (CONTRACT-v2 §10).
"""

from __future__ import annotations

import curses
from dataclasses import dataclass, replace

from roguelike import fov, render
from roguelike.generator import generate_level
from roguelike.keys import Command, CommandKind, translate_key
from roguelike.level import Level
from roguelike.movement import try_move

__all__ = ["GameState", "new_game", "step", "format_status", "run", "play"]


@dataclass(frozen=True)
class GameState:
    """The complete state of a game in progress. Immutable — transitions build a new one.

    ``player`` is an ``(x, y)`` position. ``turns`` counts only turns actually consumed: a
    move rejected by :func:`roguelike.movement.try_move` leaves it alone (v1 BRIEF Q13),
    with the single exception of a bump into a closed door, which consumes a turn to open
    it without moving (CONTRACT-v2 §7). ``running`` goes ``False`` exactly once, when the
    player quits, and a state with ``running=False`` is inert — :func:`step` returns it
    unchanged for every command.

    The three coordinate sets are all ``frozenset``, so nothing downstream can mutate
    them, and no behaviour may depend on their iteration order (CONTRACT-v2 §0.7):

    * ``visible`` — what is in view *right now*. Replaced wholesale by every recompute.
    * ``explored`` — everything ever seen. Only ever grows.
    * ``open_doors`` — the doors opened so far. Only ever grows; there is no way to close
      one.

    ``radius`` is the sight radius handed to :func:`roguelike.fov.compute_visible`. It is
    per-game state rather than a constant read at the call site, because indoors the walls
    dominate long before the radius does and the number is expected to be tuned.

    Field order is binding (CONTRACT-v2 §7): the three sets precede ``turns`` because they
    have no defaults.
    """

    level: Level
    player: tuple[int, int]
    explored: frozenset[tuple[int, int]]
    visible: frozenset[tuple[int, int]]
    open_doors: frozenset[tuple[int, int]]
    turns: int = 0
    running: bool = True
    radius: int = fov.DEFAULT_RADIUS


def new_game(level: Level, radius: int = fov.DEFAULT_RADIUS) -> GameState:
    """Return the opening state for ``level``: the player at its start, zero turns.

    Every door starts closed, and the first field of view is computed immediately, so
    ``explored`` and ``visible`` are equal and non-empty from the outset — the starting
    cell is always seen, even standing on a wall (:func:`roguelike.fov.compute_visible`
    guarantees the origin unconditionally). "Nothing is explored" is the state *before*
    that first computation, and it is never observable.

    Raises:
        ValueError: if ``radius`` is negative — propagated unchanged from
            :func:`roguelike.fov.compute_visible`, which owns that rule.
    """
    open_doors: frozenset[tuple[int, int]] = frozenset()
    player = level.player_start
    visible = fov.compute_visible(level, open_doors, player, radius)
    return GameState(
        level,
        player,
        explored=visible,
        visible=visible,
        open_doors=open_doors,
        turns=0,
        running=True,
        radius=radius,
    )


def _take_turn(
    state: GameState,
    player: tuple[int, int],
    open_doors: frozenset[tuple[int, int]],
) -> GameState:
    """Return ``state`` advanced by one consumed turn, with field of view recomputed.

    The single place field of view is recomputed after the opening one, and therefore the
    single definition of what a consumed turn does: advance the counter, recompute
    ``visible`` for the new position and door set, and fold the result into ``explored``.
    Both callers — an accepted move and a door opening — are exactly the two transitions
    that can change what is visible (CONTRACT-v2 §7), so there is no third caller and no
    way to consume a turn without refreshing sight.

    Pure, like everything it is called from: ``state`` is not mutated, and neither
    ``player`` nor ``open_doors`` is.
    """
    visible = fov.compute_visible(state.level, open_doors, player, state.radius)
    return replace(
        state,
        player=player,
        open_doors=open_doors,
        visible=visible,
        explored=state.explored | visible,
        turns=state.turns + 1,
    )


def step(state: GameState, command: Command) -> GameState:
    """Apply one player command and return the resulting state.

    Pure: ``state`` is never mutated (it is frozen anyway), nothing is drawn, no terminal
    is touched, and no I/O happens. Every game rule in the project lives here.

    - :attr:`~roguelike.keys.CommandKind.QUIT` clears ``running`` and leaves ``turns``,
      ``player`` and all three coordinate sets alone — quitting is not a turn, and it
      changes nothing about what can be seen.
    - :attr:`~roguelike.keys.CommandKind.UNKNOWN` is an ordinary no-op, not an error, so
      the state comes back untouched (numpad ``5`` and every unbound key land here).
    - :attr:`~roguelike.keys.CommandKind.MOVE` delegates the whole collision question to
      :func:`roguelike.movement.try_move`, passing the current ``open_doors`` so a closed
      door counts as impassable. Collision is not re-derived here. There are three
      outcomes:

      * **accepted** — the player moves, the turn counter advances, and field of view is
        recomputed for the new position.
      * **blocked by a closed door** — bump-to-open. The door joins ``open_doors``, the
        turn counter advances, the player does **not** move, and field of view is
        recomputed: opening a door is precisely a change in what can be seen, and the room
        beyond becomes visible. The next move in the same direction walks through.
      * **blocked by anything else** (a wall, the border, off the map) — the state comes
        back untouched, turn counter included. "A rejected move consumes no turn" is v1's
        headline rule and it is unchanged; ``visible`` is the identical set afterwards,
        never a recomputed equal one.

    - A state that has stopped running is returned as-is whatever the command, so a key
      that arrives after the quit key cannot resurrect the game.

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
            return _take_turn(state, result.position, state.open_doors)
        if result.blocked_by_door is not None:
            return _take_turn(
                state, state.player, state.open_doors | {result.blocked_by_door}
            )
        # Walked into a wall, the border or off the map: nothing happened at all, so
        # nothing is recomputed and no turn is spent.
        return state

    # CommandKind.UNKNOWN — nothing happens, and nothing that happened is undone.
    return state


def format_status(state: GameState) -> str:
    """Compose the status bar text — a plain ``str``, exactly as CONTRACT §7 spells it.

    Two spaces separate the fields. Unchanged from v1: CONTRACT-v2 §7 retains this format
    exactly, so no visibility or door information appears here. Padding and truncation
    belong to the renderer (CONTRACT-v2 §4), so nothing is done about length here.
    """
    x, y = state.player
    return (
        f"Seed: {state.level.seed}  Pos: ({x}, {y})  "
        f"Turns: {state.turns}  [q] quit"
    )


def run(stdscr, level: Level) -> None:
    """Run the turn loop on an already-initialised curses window until the player quits.

    A shell around :func:`step` and the renderer, holding no game rules: it renders the
    current state to cells, blits them, blocks for a key, turns it into a
    :class:`~roguelike.keys.Command` and hands both to :func:`step`. ``stdscr`` must
    already be initialised — :func:`play` does that, and nothing here does.

    Colour pairs are allocated once, here, immediately after curses comes up and before
    the first frame; :func:`roguelike.render.init_colors` degrades to monochrome by itself
    on a terminal without colour, and the extra guard covers the rest.

    The two window settings are best-effort: hiding the cursor raises on terminals that
    cannot do it, and neither setting is worth failing a game over.

    Returns:
        ``None``, when the player quits.
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

    state = new_game(level)
    while state.running:
        cells = render.render_to_cells(
            state.level,
            state.player,
            state.visible,
            state.explored,
            state.open_doors,
            format_status(state),
        )
        render.draw(stdscr, cells)
        state = step(state, translate_key(stdscr.getch()))


def play(seed: int, width: int = 80, height: int = 22) -> None:
    """Generate a level from ``seed`` and play it.

    The only place in the codebase that initialises curses. ``curses.wrapper`` guarantees
    ``endwin()`` on both a normal return and an exception — including
    ``KeyboardInterrupt`` — so the terminal is always restored (v1 BRIEF Q16).
    """
    level = generate_level(seed, width, height)
    curses.wrapper(run, level)
