"""Colour and visibility vocabulary (CONTRACT-v2 §15).

Pure data and pure functions — no `curses` import here, at all. Allocating curses
colour pairs and choosing terminal attributes belongs to the renderer (§4), which
already owns every curses call. This module only describes what a thing should look
like; it never touches a terminal, so it stays importable and testable with no TTY.

Imports nothing from the project except :mod:`roguelike.tiles`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from roguelike.tiles import Tile

__all__ = [
    "Visibility",
    "Role",
    "Attr",
    "role_for",
    "attr_for",
]

# Fixed ANSI colour indices, spelled out as literals rather than imported from
# `curses` (importing curses here would defeat the whole point of this module
# being pure data — see the module docstring).
_ANSI_WHITE = 7  # curses.COLOR_WHITE
_ANSI_YELLOW = 3  # curses.COLOR_YELLOW
_ANSI_RED = 1  # curses.COLOR_RED

# Per-species 256-colour indices (CONTRACT-v5 §24.1 / §4 v5, binding). Keyed by the
# same lower-case species name string carried by `npc.SpeciesData.name` — this module
# never imports `roguelike.npc`, so a plain string is the species' whole identity here.
_NPC_COLORS_256: dict[str, int] = {
    "rat": 250,
    "jackal": 173,
    "giant bat": 140,
    "cave snake": 70,
}


class Visibility(Enum):
    """How recently/currently a cell has been seen."""

    UNSEEN = auto()  # never seen — not drawn at all
    EXPLORED = auto()  # seen before, not in view now — dimmed
    VISIBLE = auto()  # in view now — natural colour


class Role(Enum):
    """The semantic role a drawn glyph plays, independent of visibility."""

    TERRAIN = auto()  # wall and floor
    DOOR = auto()
    PLAYER = auto()
    PROJECTILE = auto()  # a missile in flight
    NPC = auto()  # a monster — only ever drawn at Visibility.VISIBLE (CONTRACT-v5 §4/§15 v5)
    CHEST = auto()  # a chest — drawn at both VISIBLE and EXPLORED, like terrain: unlike a
    # monster it does not move, so a remembered chest is still there (CONTRACT-v6 §7.17/§27)


@dataclass(frozen=True)
class Attr:
    """A colour/weight description for a (Role, Visibility) pair.

    ``color`` is a 256-colour index; ``-1`` means "use the terminal's default
    colour" (the renderer maps that to ``curses.use_default_colors()`` territory).
    """

    color: int
    bold: bool = False


def role_for(tile: Tile, is_player: bool = False) -> Role:
    """Return the semantic :class:`Role` for a rendered cell.

    The player is always ``Role.PLAYER`` when ``is_player`` is True, regardless of
    what tile they stand on — the player glyph is drawn over the terrain, including
    a wall. Otherwise ``Tile.DOOR`` is ``Role.DOOR`` and every other tile
    (``Tile.WALL``, ``Tile.FLOOR``) is ``Role.TERRAIN``.
    """
    if is_player:
        return Role.PLAYER
    if tile is Tile.DOOR:
        return Role.DOOR
    return Role.TERRAIN


def attr_for(
    role: Role, visibility: Visibility, colors: int = 256, species: str | None = None
) -> Attr:
    """Return the display :class:`Attr` for ``role`` at ``visibility``.

    ``colors`` is the terminal's colour capability (``curses.COLORS``), supplied by
    the caller — this module never detects it itself, since detection needs curses.

    ``species`` selects the colour among the four monsters when ``role`` is
    ``Role.NPC`` (CONTRACT-v5 §24.1) — the lower-case species name, e.g. ``"rat"`` or
    ``"cave snake"``, exactly as carried by ``npc.SpeciesData.name``. It is ignored
    for every other role. An unrecognised or missing species falls back to the
    terminal default (``-1``) rather than raising — the same "degrade, never crash"
    discipline as the capability ladder below.

    Raises:
        ValueError: for ``Visibility.UNSEEN`` (unseen cells are never drawn, so
            asking for their attribute is a caller bug), for
            ``(Role.PLAYER, Visibility.EXPLORED)`` (the player is always visible),
            and for ``(Role.NPC, Visibility.EXPLORED)`` (an NPC is only ever drawn
            when visible — monsters move, so a remembered one is a lie).
    """
    if visibility is Visibility.UNSEEN:
        raise ValueError(
            "attr_for(..., Visibility.UNSEEN): unseen cells are never drawn, "
            "so their attribute should never be requested"
        )
    if role is Role.PROJECTILE and visibility is Visibility.EXPLORED:
        raise ValueError(
            "attr_for(Role.PROJECTILE, Visibility.EXPLORED): a missile is only ever "
            "drawn mid-flight, so this combination is a caller bug"
        )
    if role is Role.PLAYER and visibility is Visibility.EXPLORED:
        raise ValueError(
            "attr_for(Role.PLAYER, Visibility.EXPLORED): the player is always "
            "visible, so this combination is a caller bug"
        )
    if role is Role.NPC and visibility is Visibility.EXPLORED:
        raise ValueError(
            "attr_for(Role.NPC, Visibility.EXPLORED): an NPC is only ever drawn "
            "when visible — monsters move, so a remembered one is a lie, and "
            "asking for this combination is a caller bug"
        )

    if role is Role.PROJECTILE:
        # Always in flight, therefore always visible; bright so the eye follows it.
        if colors >= 256:
            return Attr(226, bold=True)
        if colors >= 8:
            return Attr(_ANSI_YELLOW, bold=True)
        return Attr(-1, bold=True)

    if role is Role.PLAYER:
        # visibility is guaranteed VISIBLE at this point.
        if colors >= 256:
            return Attr(231, bold=True)
        if colors >= 8:
            return Attr(_ANSI_WHITE, bold=True)
        return Attr(-1, bold=True)

    if role is Role.NPC:
        # visibility is guaranteed VISIBLE at this point.
        if colors >= 256:
            return Attr(_NPC_COLORS_256.get(species or "", -1))
        if colors >= 8:
            return Attr(_ANSI_RED)
        return Attr(-1)

    # role is TERRAIN, DOOR or CHEST.
    if colors >= 256:
        # Binding palette (CONTRACT-v2 §15.1, CONTRACT-v6 §7.17/§27 for CHEST). All
        # three pairs sit on xterm ramps where a lower index is a darker shade of the
        # same hue: 250->238 is the 256-colour grayscale ramp, 180->94 is the
        # 256-colour orange/brown ramp, 220->178 is the 256-colour gold ramp — chosen
        # to read as treasure and distinct from every other role's colour (terrain
        # 250/238, door 180/94, player 231, projectile 226, the four species
        # 250/173/140/70).
        palette = {
            Role.TERRAIN: {Visibility.VISIBLE: 250, Visibility.EXPLORED: 238},
            Role.DOOR: {Visibility.VISIBLE: 180, Visibility.EXPLORED: 94},
            Role.CHEST: {Visibility.VISIBLE: 220, Visibility.EXPLORED: 178},
        }
        return Attr(palette[role][visibility])

    if colors >= 8:
        # EXPLORED gets the same colour as VISIBLE here — the dim signal on a
        # sub-256-colour terminal is the renderer's job (curses.A_DIM), not ours.
        base = _ANSI_WHITE if role is Role.TERRAIN else _ANSI_YELLOW
        return Attr(base)

    # Monochrome / no usable colour: terminal default for everything.
    return Attr(-1)
