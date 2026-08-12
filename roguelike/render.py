"""Rendering — a pure frame builder plus a dumb curses blitter (CONTRACT-v3 §4, v5 §4/§15).

Two layers, unchanged in spirit from v1/v2 but now with a chrome frame (a reserved stats
row and a two-part status row) wrapped around the map, and now drawing monsters and a
ranged-attack cursor on top of it (CONTRACT-v5 §4/§15 v5):

1. :func:`render_to_cells` is **pure**. It turns a :class:`~roguelike.level.Level`, a
   player position, three visibility frozensets and a :class:`Chrome` of already-composed
   text into a frame of styled :class:`Cell` objects. It carries all of the layout logic
   and all of the visibility logic, touches no global state, performs no I/O and never
   uses ``curses``. :func:`to_lines` is a thin plain-text view over that frame, also pure.
2. :func:`init_colors` and :func:`draw` are the only curses in this module. ``draw`` is
   a blitter with no layout logic and no visibility logic of its own: it reads the
   attribute chosen once by :func:`init_colors` for each ``(Role, Visibility)`` pair and
   pushes ``cells`` at an already-initialised curses window.

Every coordinate crossing this module's public surface is ``(x, y)`` with the origin at
the top-left; ``y`` grows down (CONTRACT §0.1). ``curses`` wants ``(y, x)``, and
:func:`draw` is the single place in the whole codebase where that inverted ordering is
allowed to appear — it is confined to ``getmaxyx``/``addstr`` at the bottom of this file.

**The map is no longer at row 0 (CONTRACT-v3 §0.8).** The frame now reserves row ``0`` for
:attr:`Chrome.stats` and its last row for the status line, so map cell ``(x, y)`` lands at
``cells[y + 1][x]``. That ``+1`` exists in exactly this function and nowhere else in the
codebase — game logic, FOV, movement and the generator never index the frame.

Fog of war (CONTRACT-v2 BRIEF): a map cell is drawn in its natural colour when currently
``visible``, a darker shade of that colour when merely ``explored``, and not drawn at all
— a blank space, not a dimmed glyph — when neither, so the player cannot infer the map
shape from unexplored area.

Glyphs are never spelled out here: they come from :data:`roguelike.tiles.TILE_CHARS`,
:data:`roguelike.tiles.PLAYER_CHAR` and :data:`roguelike.tiles.DOOR_OPEN_CHAR` — including
the stair glyphs, which need no special handling here since they are just another
``TILE_CHARS`` entry. Colours are never spelled out here either: they come from
:func:`roguelike.style.attr_for`. NPC glyphs and species identity are no exception to
"never spelled out here" — they arrive as parameters via :class:`NpcGlyph`, since this
module may not import the monster module, ``npc.py`` (CONTRACT-v5 §10 v5), and must
therefore stay usable without it ever existing. Chests are the same story one module
over: this module may not import ``items.py`` or ``loot.py`` (CONTRACT-v6 §10 v6), so a
chest arrives as a plain ``(x, y)`` position via the ``chests`` parameter — there is no
per-chest data to carry (every chest draws identically), so unlike :class:`NpcGlyph`
there is no small structure wrapping it, just the position itself. The one glyph this
module *does* own outright is :data:`CHEST_CHAR`, exactly as it already owns
:data:`PROJECTILE_CHAR` — both are the renderer's own invented symbols, not tile data.

``curses`` is imported for :class:`curses.error` and its attribute/colour constants;
nothing in this module calls a terminal-mutating curses function at import time, and only
:func:`init_colors`/:func:`draw` call curses at all, and only once already-initialised by
their caller (CONTRACT §0.3). Imports from :mod:`roguelike` are limited to
:mod:`roguelike.tiles`, :mod:`roguelike.level` and :mod:`roguelike.style` (CONTRACT-v3
§10). This module never imports ``events``, ``fov``, ``game``, ``generator``, ``movement``,
``keys``, ``npc``, ``combat``, ``stats``, ``items`` or ``loot`` — it receives finished
strings via :class:`Chrome`, finished NPC glyphs via :class:`NpcGlyph` and finished chest
positions via ``chests``, never events or game state.

**The one visibility rule that matters (CONTRACT-v5 §4/§15 v5):** an NPC is drawn only
when its position is in ``visible``, never from ``explored``. Terrain is remembered — a
wall seen an hour ago is still a wall — but a monster seen an hour ago has moved, so
drawing it from memory would draw a lie. The player glyph always wins over an NPC glyph
on the same cell, drawn last and unconditionally rather than relying on the upstream
occupancy invariant that is supposed to make the collision unreachable.

**Chests are the deliberate exception to that rule (CONTRACT-v6 §7.17/§27):** a chest
does not move, so it is drawn from ``explored`` exactly like terrain, not hidden the
instant it leaves view like an NPC. A remembered chest is not a lie — it is still there —
and that is precisely what makes walking back to one worth doing. Chests are drawn after
terrain but before NPCs and the player, so either of those still wins on the same cell.
"""

from __future__ import annotations

import curses
from dataclasses import dataclass, replace

from roguelike.level import Level
from roguelike.style import Role, Visibility, attr_for, role_for
from roguelike.tiles import DOOR_OPEN_CHAR, PLAYER_CHAR, TILE_CHARS

__all__ = [
    "Cell",
    "Chrome",
    "NpcGlyph",
    "render_to_cells",
    "render_text_page",
    "PROJECTILE_CHAR",
    "CHEST_CHAR",
    "to_lines",
    "init_colors",
    "draw",
]


@dataclass(frozen=True)
class Cell:
    """One styled character of a rendered frame.

    ``species`` is only ever set (to the species' lower-case name, e.g. ``"rat"``) on a
    cell with ``role is Role.NPC``; every other cell leaves it ``None``. ``reverse``
    marks the single cell carrying the ranged-target cursor (``curses.A_REVERSE``,
    CONTRACT-v5 §4/§15 v5) — it never changes ``char``, so the underlying glyph (terrain,
    door, player or NPC) stays exactly what it already was.
    """

    char: str
    role: Role
    visibility: Visibility
    species: str | None = None
    reverse: bool = False


@dataclass(frozen=True)
class NpcGlyph:
    """One monster to draw this frame — a small local structure, not ``npc.py``'s ``NPC``.

    This module may not import ``npc.py`` (CONTRACT-v5 §10 v5), so an NPC's glyph
    and species identity arrive as plain data instead. ``species`` is the same lower-case
    species name string ``npc.SpeciesData.name`` already carries (e.g. ``"rat"``,
    ``"cave snake"``) and is passed straight through to :func:`roguelike.style.attr_for`
    to select the NPC's colour — this module never interprets it itself.
    """

    position: tuple[int, int]
    glyph: str
    species: str


@dataclass(frozen=True)
class Chrome:
    """Finished, already-worded UI text for the frame around the map (CONTRACT-v3 §4).

    The renderer never formats wording — it just lays these three finished strings out.

    Attributes:
        stats: top row, reserved for player stats that do not exist yet. Blank for now.
        message: bottom row, left-aligned — the current event message.
        status_right: bottom row, right-aligned — e.g. the level and seed. Always wins
            over ``message`` when the two would collide (§4.2).
    """

    stats: str = ""
    message: str = ""
    status_right: str = ""


#: Drawn for a missile in flight. Conventional, and distinct from every tile glyph and
#: every species glyph in the bestiary.
PROJECTILE_CHAR: str = "*"

#: Drawn for a chest (CONTRACT-v6 §7.17/§27). Distinct from every tile glyph
#: (``#``, ``.``, ``+``, ``'``, ``<``, ``>``), the player glyph (``@``), the projectile
#: glyph (``*``), and every species glyph in the bestiary (``r``, ``j``, ``B``, ``s``).
CHEST_CHAR: str = "~"


def _chrome_row(text: str, width: int) -> list[Cell]:
    """Pad/truncate ``text`` to exactly ``width`` and wrap it as a terrain/visible row."""
    padded = text[:width].ljust(width)
    return [Cell(ch, Role.TERRAIN, Visibility.VISIBLE) for ch in padded]


def _compose_status_row(message: str, status_right: str, width: int) -> str:
    """Compose the status row text per CONTRACT-v3 §4.2.

    ``status_right`` always wins: if it alone is ``>= width`` it is truncated to ``width``
    and ``message`` is dropped entirely. Otherwise, if ``message`` plus a single
    separating space plus ``status_right`` fit, both appear in full with ``status_right``
    flush right. Otherwise ``message`` is truncated (never below zero length) so that
    ``status_right`` survives intact and flush right.
    """
    if len(status_right) >= width:
        return status_right[:width]

    if len(message) + 1 + len(status_right) > width:
        message = message[: max(0, width - len(status_right) - 1)]

    pad = width - len(message) - len(status_right)
    return message + " " * pad + status_right


def render_to_cells(
    level: Level,
    player_pos: tuple[int, int],
    visible: frozenset[tuple[int, int]],
    explored: frozenset[tuple[int, int]],
    open_doors: frozenset[tuple[int, int]],
    chrome: Chrome,
    npcs: tuple[NpcGlyph, ...] = (),
    target: tuple[int, int] | None = None,
    projectile: tuple[int, int] | None = None,
    chests: frozenset[tuple[int, int]] = frozenset(),
) -> list[list[Cell]]:
    """Render one frame as a grid of styled :class:`Cell`. Pure.

    Args:
        level: the level to draw. Never mutated (it is frozen anyway).
        player_pos: the player's ``(x, y)`` position. Out-of-bounds positions are simply
            not drawn — no exception.
        visible: coordinates currently in view. Wins over ``explored`` when a cell is in
            both.
        explored: coordinates seen before but not currently in view.
        open_doors: coordinates of currently-open doors, used to pick the door glyph.
        chrome: already-composed UI text for the stats row and the status row (§4).
        npcs: monsters to draw this frame (CONTRACT-v5 §4/§15 v5). **Appended with a
            default of ``()``, so every v1–v4 call site keeps working unchanged.** Each
            is drawn at its own ``position`` only when that position is in ``visible`` —
            never from ``explored``, since a remembered monster is a lie. Out-of-bounds
            positions are simply not drawn, exactly like ``player_pos``.
        target: the ranged-attack cursor's ``(x, y)`` cell, or ``None`` for no target
            (CONTRACT-v5 §7.10, §4/§15 v5). **Appended with a default of ``None``.**
            When set and in bounds, that cell's :attr:`Cell.reverse` is ``True`` and
            nothing else about it changes — no separate cursor glyph is drawn.
        chests: positions of chests to draw this frame (CONTRACT-v6 §7.17/§27).
            **Appended with a default of ``frozenset()``, so every pre-v6 call site
            keeps working unchanged.** Unlike ``npcs``, each is drawn whenever its
            position is in ``visible`` *or* ``explored`` — a chest does not move, so a
            remembered one is still there. Out-of-bounds positions are simply not
            drawn, exactly like ``player_pos``.

    Returns:
        Exactly ``level.height + 2`` rows of exactly ``level.width`` :class:`Cell` each:
        the reserved stats row, then the map — cell ``(x, y)`` at ``cells[y + 1][x]``
        (§0.8) — then the status row.

    Visibility precedence per map cell: ``visible`` beats ``explored`` beats unseen.
    An unseen cell is blank (``char == " "``, ``role == Role.TERRAIN``) — the map shape
    must never leak through unexplored area. Chests are drawn over that terrain next,
    at ``Visibility.VISIBLE`` or ``Visibility.EXPLORED`` matching the same precedence,
    but never on an unseen cell. NPCs are drawn over that next, each only if currently
    ``visible`` — so an NPC standing on a chest hides it, exactly as intended (a monster
    always wins over a chest on the same cell). The player glyph is drawn last of the
    glyphs and unconditionally overrides whatever is on its cell — terrain, door, chest,
    *or* NPC — drawn on a wall exactly as readily as on a floor. The ranged-target
    highlight, if any, is applied last of all, so it survives on top of terrain, a
    chest, an NPC or the player alike without altering any of their glyphs. Both chrome
    rows are ``Role.TERRAIN``, ``Visibility.VISIBLE`` in every cell, and never carry a
    target highlight (``target`` addresses map coordinates only).

    None of ``level``, ``player_pos``, ``visible``, ``explored``, ``open_doors``,
    ``chrome``, ``npcs``, ``target`` or ``chests`` is mutated; a fresh grid of fresh
    ``Cell``s is returned each call.
    """
    width = level.width

    rows: list[list[Cell]] = [_chrome_row(chrome.stats, width)]

    for y in range(level.height):
        row: list[Cell] = []
        for x in range(width):
            coord = (x, y)
            if coord in visible:
                visibility = Visibility.VISIBLE
            elif coord in explored:
                visibility = Visibility.EXPLORED
            else:
                row.append(Cell(" ", Role.TERRAIN, Visibility.UNSEEN))
                continue

            tile = level.tile_at(x, y)
            role = role_for(tile)
            if role is Role.DOOR and coord in open_doors:
                char = DOOR_OPEN_CHAR
            else:
                char = TILE_CHARS[tile]
            row.append(Cell(char, role, visibility))
        rows.append(row)

    # Chests — over the terrain, drawn from `explored` as well as `visible`, unlike an
    # NPC: a chest does not move, so a remembered one is still there and worth walking
    # back to (CONTRACT-v6 §7.17/§27). Never drawn on an unseen cell, so the map shape
    # still leaks through nowhere unexplored.
    for chest_x, chest_y in chests:
        if not level.in_bounds(chest_x, chest_y):
            continue
        if (chest_x, chest_y) in visible:
            rows[chest_y + 1][chest_x] = Cell(CHEST_CHAR, Role.CHEST, Visibility.VISIBLE)
        elif (chest_x, chest_y) in explored:
            rows[chest_y + 1][chest_x] = Cell(CHEST_CHAR, Role.CHEST, Visibility.EXPLORED)

    # NPCs — over the chests and the terrain, only when currently visible, never from
    # `explored` (the headline correctness rule: a remembered monster is a lie). Drawn
    # after chests so a monster standing on a chest wins, per CONTRACT-v6 §7.17/§27.
    for npc in npcs:
        npc_x, npc_y = npc.position
        if level.in_bounds(npc_x, npc_y) and npc.position in visible:
            rows[npc_y + 1][npc_x] = Cell(
                npc.glyph, Role.NPC, Visibility.VISIBLE, species=npc.species
            )

    # The player — drawn last of the glyphs and unconditionally, so it wins over an NPC
    # or a chest on the same cell without depending on the upstream occupancy invariant
    # that is supposed to make the collision unreachable in the first place.
    player_x, player_y = player_pos
    if level.in_bounds(player_x, player_y):
        rows[player_y + 1][player_x] = Cell(PLAYER_CHAR, Role.PLAYER, Visibility.VISIBLE)

    # A missile in flight — drawn over everything including the player, because for the
    # instant it is on screen it is the thing the eye should follow. It is transient: the
    # caller passes one cell per animation frame and `None` the rest of the time.
    if projectile is not None:
        missile_x, missile_y = projectile
        if level.in_bounds(missile_x, missile_y):
            rows[missile_y + 1][missile_x] = Cell(
                PROJECTILE_CHAR, Role.PROJECTILE, Visibility.VISIBLE
            )

    # The ranged-target cursor — applied last of all, on top of terrain, an NPC or the
    # player alike. It only ever flips `reverse`; the glyph underneath is untouched, so
    # a targeted monster still shows its species glyph, not a cursor character.
    if target is not None:
        target_x, target_y = target
        if level.in_bounds(target_x, target_y):
            targeted = rows[target_y + 1][target_x]
            rows[target_y + 1][target_x] = replace(targeted, reverse=True)

    status_text = _compose_status_row(chrome.message, chrome.status_right, width)
    rows.append(_chrome_row(status_text, width))

    return rows


def to_lines(cells: list[list[Cell]]) -> list[str]:
    """Flatten a frame of :class:`Cell` to plain text, one string per row.

    Every returned string is exactly the length of its row. Never raises.
    """
    return ["".join(cell.char for cell in row) for row in cells]


def render_text_page(
    lines: tuple[str, ...],
    chrome: Chrome,
    width: int,
    height: int,
) -> list[list[Cell]]:
    """Render a full-screen page of plain text — the help screen, and nothing else yet.

    Deliberately knows nothing about *what* it is drawing. It takes finished strings and
    a :class:`Chrome`, exactly as :func:`render_to_cells` does, so that pagination,
    wording and the decision to show a page at all stay in the turn loop where the rest
    of the game's rules live (v1 BRIEF Q14).

    The frame is the same shape :func:`render_to_cells` produces — one stats row,
    ``height`` body rows, one status row — so the caller can hand either to
    :func:`draw` without knowing which it has.

    Lines longer than ``width`` are clipped, never wrapped: wrapping is a layout policy
    and the caller composed these strings knowing the width. Fewer lines than ``height``
    leaves the remaining rows blank; more are clipped, which is the caller's cue that its
    pagination is wrong.

    Every cell is ``Role.TERRAIN`` / ``Visibility.VISIBLE``, so a help page needs no new
    colour pair and :func:`init_colors` is unchanged.

    Pure: nothing is mutated, and the same arguments always produce the same frame.
    Never raises.
    """
    rows: list[list[Cell]] = [_chrome_row(chrome.stats, width)]
    for index in range(height):
        text = lines[index] if index < len(lines) else ""
        rows.append(_chrome_row(text, width))
    rows.append(
        _chrome_row(_compose_status_row(chrome.message, chrome.status_right, width), width)
    )
    return rows


# --------------------------------------------------------------------------- curses

# (Role, Visibility) combinations that are ever actually drawn. UNSEEN cells are never
# drawn (render_to_cells never emits Visibility.UNSEEN with anything but a blank space,
# and attr_for raises for UNSEEN), and Role.PLAYER is always Visibility.VISIBLE, so those
# combinations are deliberately absent here. Role.NPC is likewise absent: an NPC's colour
# depends on its species, not just its (role, visibility), so it is allocated separately
# below into `_NPC_ATTRS` rather than crammed into this dict's key shape. Role.CHEST gets
# both VISIBLE and EXPLORED, like TERRAIN and DOOR — a chest does not move, so it is
# drawn (and needs a colour) at both (CONTRACT-v6 §7.17/§27).
_ATTR_COMBOS: tuple[tuple[Role, Visibility], ...] = (
    (Role.TERRAIN, Visibility.VISIBLE),
    (Role.TERRAIN, Visibility.EXPLORED),
    (Role.DOOR, Visibility.VISIBLE),
    (Role.DOOR, Visibility.EXPLORED),
    (Role.PLAYER, Visibility.VISIBLE),
    (Role.PROJECTILE, Visibility.VISIBLE),
    (Role.CHEST, Visibility.VISIBLE),
    (Role.CHEST, Visibility.EXPLORED),
)

# The species keys style.attr_for accepts for Role.NPC (CONTRACT-v5 §24.1's four-species
# bestiary), spelled directly here — as plain identifier strings, never as colour data —
# rather than imported from npc.py, which this module may not import (§10 v5). Every NPC
# handed to render_to_cells is expected to carry one of these four as its `species`.
_NPC_SPECIES: tuple[str, ...] = ("rat", "jackal", "giant bat", "cave snake")


# Populated once by init_colors(); read (never written) by draw(). Empty until
# init_colors() has run, which is exactly the "no attribute" fallback draw() needs.
_CELL_ATTRS: dict[tuple[Role, Visibility], int] = {}

# One curses attribute per NPC species, keyed by the same lower-case name string carried
# by NpcGlyph.species. Populated once by init_colors(); read (never written) by draw().
_NPC_ATTRS: dict[str, int] = {}


def init_colors(colors: int | None = None) -> None:
    """Allocate one curses colour-pair attribute per ``(Role, Visibility)`` pair, plus
    one more per NPC species.

    Call once, after curses is up. Detects ``curses.COLORS`` when ``colors`` is ``None``;
    an explicit ``colors`` (e.g. ``0`` for "no colour support") overrides detection, which
    is how a colourless terminal is exercised without a live TTY.

    Every curses call is individually wrapped against :class:`curses.error`, so a
    terminal without colour support — or, in tests, no terminal at all — leaves this
    function's fallback (a plain attribute with no colour) in place rather than raising.

    The resulting attributes are stored in two module-level dicts that :func:`draw`
    reads: ``_CELL_ATTRS`` for terrain/door/player, ``_NPC_ATTRS`` for monsters (keyed by
    species, since two different species at the same visibility need different colours).
    This is the only place colour pairs are allocated: never per frame, never in
    :func:`draw`.
    """
    try:
        curses.start_color()
    except curses.error:
        pass
    try:
        curses.use_default_colors()
    except curses.error:
        pass

    if colors is None:
        try:
            detected = curses.COLORS
        except AttributeError:
            detected = 0
    else:
        detected = colors

    def _allocate(pair_number: int, style_attr) -> int:
        try:
            curses.init_pair(pair_number, style_attr.color, -1)
        except (curses.error, ValueError):
            # A real curses build raises curses.error when the terminal has no colour
            # support. Python's curses module additionally raises ValueError here
            # specifically when curses has never been initialised at all (it validates
            # the pair number against curses.COLOR_PAIRS, which defaults to -1) — the
            # exact situation every test in this suite runs under. Both are the same
            # "no usable colour" case from this function's point of view.
            pass

        try:
            value = curses.color_pair(pair_number)
        except curses.error:
            value = 0

        if style_attr.bold:
            value |= curses.A_BOLD
        return value

    attrs: dict[tuple[Role, Visibility], int] = {}
    for pair_number, (role, visibility) in enumerate(_ATTR_COMBOS, start=1):
        style_attr = attr_for(role, visibility, colors=detected)
        value = _allocate(pair_number, style_attr)
        if visibility is Visibility.EXPLORED and detected < 256:
            value |= curses.A_DIM
        attrs[(role, visibility)] = value

    npc_attrs: dict[str, int] = {}
    for offset, species in enumerate(_NPC_SPECIES, start=len(_ATTR_COMBOS) + 1):
        style_attr = attr_for(Role.NPC, Visibility.VISIBLE, colors=detected, species=species)
        npc_attrs[species] = _allocate(offset, style_attr)

    global _CELL_ATTRS, _NPC_ATTRS
    _CELL_ATTRS = attrs
    _NPC_ATTRS = npc_attrs


def draw(stdscr, cells: list[list[Cell]]) -> None:
    """Blit one frame of :class:`Cell` to an already-initialised curses window.

    Contains no layout logic and no visibility logic: for each on-screen cell it looks up
    the attribute :func:`init_colors` computed for that cell's ``(role, visibility)`` —
    or, for ``Role.NPC``, for its ``species`` — falling back to no attribute (``0``) if
    :func:`init_colors` was never called, and writes the cell's character. A cell with
    ``reverse`` set has ``curses.A_REVERSE`` added on top of whatever attribute it would
    otherwise get; the character written is unaffected. ``UNSEEN`` cells simply write
    their (already blank) space character.

    Erases before drawing and refreshes after; clips to rows ``< max_y`` and columns
    ``< max_x`` rather than raising when the terminal is smaller than the frame; never
    writes the window's bottom-right cell (writing it raises in real curses); swallows
    :class:`curses.error` from every call throughout.

    ``stdscr`` must already be initialised by the caller — this function never
    initialises curses itself.

    Returns:
        ``None``. Mutates nothing but the screen.
    """
    try:
        stdscr.erase()
    except curses.error:
        pass

    # --- the one and only (y, x) region in this codebase (CONTRACT §0.1) ---
    try:
        max_y, max_x = stdscr.getmaxyx()
    except curses.error:
        max_y = max_x = 0

    for y in range(min(len(cells), max_y)):
        row = cells[y]
        for x in range(min(len(row), max_x)):
            # The window's bottom-right cell cannot be written without the cursor
            # advancing off the window, which raises. Skip it.
            if y == max_y - 1 and x == max_x - 1:
                continue
            cell = row[x]
            if cell.role is Role.NPC:
                attr = _NPC_ATTRS.get(cell.species, 0)
            else:
                attr = _CELL_ATTRS.get((cell.role, cell.visibility), 0)
            if cell.reverse:
                attr |= curses.A_REVERSE
            try:
                stdscr.addstr(y, x, cell.char, attr)
            except curses.error:
                pass
    # --- end of the (y, x) region ---

    try:
        stdscr.refresh()
    except curses.error:
        pass
