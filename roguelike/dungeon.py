"""Depth and seed derivation — how a master seed becomes a chain of levels.

This module is the whole of the dungeon's *identity*: given the master seed the player
started the run with, it says which level lives at which depth. It holds no state, no
cache and no player. The dungeon the player is walking through — which levels have been
visited, what has been explored, which doors stand open — lives in
:class:`roguelike.game.GameState`, because that is runtime state and this is not.

Two functions, both pure:

* :func:`seed_for` mixes ``(master_seed, depth, branch)`` into one plain ``int``. Plain,
  because :func:`roguelike.generator.generate_level` requires an ``int`` seed and because
  an explicit integer mix is self-evidently deterministic across processes — no
  ``hash()``, no string seeding, nothing that could depend on ``PYTHONHASHSEED``.
* :func:`level_for` turns a depth into the ``Level`` at that depth.

**How levels line up.** Descending passes the coordinate the player descended *from* as
``required_up``; the generator anchors the new level's up-staircase there exactly (G14).
So level *N*'s down-staircase and level *N+1*'s up-staircase are the same ``(x, y)`` and
the player simply stays put while the world changes underneath them. The caller
(:mod:`roguelike.game`) owns that chaining rule; this module just honours the coordinate
it is handed.

**Why there is no cache here.** Generation is deterministic, so re-deriving a level's
*terrain* is free in the sense that matters — it always comes out identical. What cannot
be re-derived is the fog and the open doors, which are runtime state; those are kept
per-depth by the game, and once that store exists it holds the ``Level`` too. Caching
here as well would be a second source of truth for no gain.

``branch`` is the scaffolding for future branching (RESEARCH-v3 §3): it is always ``0``
today, it feeds the mix so a second staircase would lead somewhere genuinely different,
and nothing else in the codebase needs to change when one appears.

Imports only :mod:`roguelike.generator` and :mod:`roguelike.level` (CONTRACT-v3 §10).
Never touches curses.
"""

from __future__ import annotations

from roguelike.generator import DEFAULT_HEIGHT, DEFAULT_WIDTH, generate_level
from roguelike.level import Level

__all__ = ["seed_for", "level_for"]


def seed_for(master_seed: int, depth: int, branch: int = 0) -> int:
    """Derive the per-level generator seed for ``depth`` of ``master_seed``'s dungeon.

    The mix is the one CONTRACT-v3 §17 spells out::

        (master_seed * 0x9E3779B1 + depth * 0x85EBCA77 + branch * 0xC2B2AE35) & 0x7FFFFFFF

    Three odd multipliers (the golden-ratio and xxHash constants) and a mask to 31 bits,
    so the result is always a non-negative ``int``, whatever sign the master seed had.
    Pure integer arithmetic: deterministic within a process, across processes, and across
    ``PYTHONHASHSEED`` values.

    Raises:
        ValueError: if ``depth < 1``. Depth is 1-based; there is no level 0.
    """
    if depth < 1:
        raise ValueError(f"depth must be >= 1, got {depth}")
    return (
        master_seed * 0x9E3779B1 + depth * 0x85EBCA77 + branch * 0xC2B2AE35
    ) & 0x7FFFFFFF


def level_for(
    master_seed: int,
    depth: int,
    required_up: tuple[int, int] | None = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> Level:
    """Generate the level at ``depth`` of ``master_seed``'s dungeon.

    ``required_up`` forces the up-staircase onto an exact coordinate — the cell the player
    descended from on the level above — and the generator guarantees
    ``level.stairs_up == required_up`` (G14). Pass ``None`` for level 1, which has no
    level above it and so no coordinate to line up with.

    ``max_rooms`` is deliberately left at the generator's default: nothing here varies the
    level's *shape* by depth, so a dungeon is a chain of ordinary levels differing only in
    their seed and their up-staircase.

    Pure and uncached: two calls with the same arguments return equal levels, and each
    call regenerates from scratch.

    Raises:
        ValueError: if ``depth < 1``; and, propagated unchanged from
            :func:`~roguelike.generator.generate_level`, if ``required_up`` is out of the
            anchorable range ``2 <= x <= width - 3``, ``2 <= y <= height - 3``. Every
            coordinate an honest descent produces is inside that range on a map of the
            *same* dimensions (it is an open spot, G13), so the only way to trip it is to
            hand it a coordinate from a differently-sized map.
        TypeError: propagated from the generator for a malformed ``required_up`` or a
            non-``int`` dimension.
    """
    if depth < 1:
        raise ValueError(f"depth must be >= 1, got {depth}")
    return generate_level(
        seed_for(master_seed, depth),
        width,
        height,
        depth=depth,
        required_up=required_up,
    )
