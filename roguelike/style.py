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


def attr_for(role: Role, visibility: Visibility, colors: int = 256) -> Attr:
    """Return the display :class:`Attr` for ``role`` at ``visibility``.

    ``colors`` is the terminal's colour capability (``curses.COLORS``), supplied by
    the caller — this module never detects it itself, since detection needs curses.

    Raises:
        ValueError: for ``Visibility.UNSEEN`` (unseen cells are never drawn, so
            asking for their attribute is a caller bug), and for
            ``(Role.PLAYER, Visibility.EXPLORED)`` (the player is always visible).
    """
    if visibility is Visibility.UNSEEN:
        raise ValueError(
            "attr_for(..., Visibility.UNSEEN): unseen cells are never drawn, "
            "so their attribute should never be requested"
        )
    if role is Role.PLAYER and visibility is Visibility.EXPLORED:
        raise ValueError(
            "attr_for(Role.PLAYER, Visibility.EXPLORED): the player is always "
            "visible, so this combination is a caller bug"
        )

    if role is Role.PLAYER:
        # visibility is guaranteed VISIBLE at this point.
        if colors >= 256:
            return Attr(231, bold=True)
        if colors >= 8:
            return Attr(_ANSI_WHITE, bold=True)
        return Attr(-1, bold=True)

    # role is TERRAIN or DOOR.
    if colors >= 256:
        # Binding palette (CONTRACT-v2 §15.1). Both pairs sit on xterm ramps
        # where a lower index is a darker shade of the same hue: 250->238 is the
        # 256-colour grayscale ramp, 180->94 is the 256-colour orange/brown ramp.
        palette = {
            Role.TERRAIN: {Visibility.VISIBLE: 250, Visibility.EXPLORED: 238},
            Role.DOOR: {Visibility.VISIBLE: 180, Visibility.EXPLORED: 94},
        }
        return Attr(palette[role][visibility])

    if colors >= 8:
        # EXPLORED gets the same colour as VISIBLE here — the dim signal on a
        # sub-256-colour terminal is the renderer's job (curses.A_DIM), not ours.
        base = _ANSI_WHITE if role is Role.TERRAIN else _ANSI_YELLOW
        return Attr(base)

    # Monochrome / no usable colour: terminal default for everything.
    return Attr(-1)
