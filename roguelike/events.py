"""Event vocabulary and message wording (CONTRACT-v3 §16, CONTRACT-v4 §16,
CONTRACT-v5 §16).

Game logic emits structured, terminal-free :class:`Event` values; all English
wording lives in exactly one table, :data:`MESSAGES`. This module is a leaf:
it imports nothing from the ``roguelike`` package, and nothing that touches a
terminal — no ``curses``, no I/O. Adding an event later is one enum member,
one table entry, one emission at the call site; there is no framework, event
bus, observer list, or registry here.

There is deliberately no "you bump into a wall" event: it would fire on every
misstep and is the single noisiest message a roguelike can produce.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Sequence

__all__ = [
    "EventKind",
    "Event",
    "MESSAGES",
    "message_for",
]


class EventKind(Enum):
    """Every thing the game can tell the player about, and nothing else."""

    DOOR_OPENED = auto()
    STAIRS_HERE_UP = auto()
    STAIRS_HERE_DOWN = auto()
    DESCENDED = auto()
    ASCENDED = auto()
    LEFT_DUNGEON = auto()
    NO_STAIRS_DOWN = auto()
    NO_STAIRS_UP = auto()
    # -- v4: multi-turn activities (CONTRACT-v4 §16) --
    WALK_WHICH_WAY = auto()
    TRAVELLING = auto()
    ARRIVED = auto()
    EXPLORED_EVERYTHING = auto()
    NOTHING_FURTHER = auto()
    STOPPED_AT_JUNCTION = auto()
    STOPPED_AT_OPENING = auto()
    INTERRUPTED = auto()
    # -- v5: combat, death, levelling, poison, targeting (CONTRACT-v5 §16) --
    PLAYER_HIT_NPC = auto()
    PLAYER_MISSED_NPC = auto()
    NPC_HIT_PLAYER = auto()
    NPC_MISSED_PLAYER = auto()
    NPC_KILLED = auto()
    PLAYER_DIED = auto()
    LEVELLED_UP = auto()
    POISONED = auto()
    POISON_DAMAGE = auto()
    NO_TARGET = auto()
    TARGETING = auto()
    SPOTTED_HOSTILE = auto()
    ATTACK_WHICH_WAY = auto()
    ATTACKED_NOTHING = auto()
    SWAPPED_PLACES = auto()
    LOOKING = auto()
    RESTING = auto()
    RESTED = auto()
    CANNOT_REST = auto()
    HOSTILE_IN_VIEW = auto()


@dataclass(frozen=True)
class Event:
    """One structured occurrence.

    ``depth`` is only meaningful for kinds whose message template uses
    ``{depth}``; ``name`` for kinds using ``{name}``; ``level`` for kinds
    using ``{level}``. A kind's message template ignores whichever of these
    fields it does not use, even if a caller supplies one.
    """

    kind: EventKind
    depth: int | None = None
    name: str | None = None
    level: int | None = None


# --- Wording table (CONTRACT-v3 §16.1) — binding, copied character for ------
# character. This is the *only* place an English sentence may appear; the
# turn loop that produces Event values never contains one.

MESSAGES: dict[EventKind, str] = {
    EventKind.DOOR_OPENED: "The door opens.",
    EventKind.STAIRS_HERE_UP: "There is a staircase leading up here.",
    EventKind.STAIRS_HERE_DOWN: "There is a staircase leading down here.",
    EventKind.DESCENDED: "You descend to level {depth}.",
    EventKind.ASCENDED: "You climb up to level {depth}.",
    EventKind.LEFT_DUNGEON: "You climb out of the dungeon and give up. Farewell.",
    EventKind.NO_STAIRS_DOWN: "There are no stairs leading down here.",
    EventKind.NO_STAIRS_UP: "There are no stairs leading up here.",
    # -- v4: multi-turn activities (CONTRACT-v4 §16) --
    EventKind.WALK_WHICH_WAY: "Walk in which direction?",
    EventKind.TRAVELLING: "You travel towards the staircase.",
    EventKind.ARRIVED: "You arrive at the staircase.",
    EventKind.EXPLORED_EVERYTHING: "You have explored everything you can reach here.",
    EventKind.NOTHING_FURTHER: "There is nowhere further to go.",
    EventKind.STOPPED_AT_JUNCTION: "You stop at a junction.",
    EventKind.STOPPED_AT_OPENING: "You stop before the opening.",
    EventKind.INTERRUPTED: "You stop.",
    # -- v5: combat, death, levelling, poison, targeting (CONTRACT-v5 §16) --
    EventKind.PLAYER_HIT_NPC: "You hit the {name}.",
    EventKind.PLAYER_MISSED_NPC: "You miss the {name}.",
    EventKind.NPC_HIT_PLAYER: "The {name} hits you.",
    EventKind.NPC_MISSED_PLAYER: "The {name} misses you.",
    EventKind.NPC_KILLED: "You kill the {name}!",
    EventKind.PLAYER_DIED: "You die...",
    EventKind.LEVELLED_UP: "Welcome to level {level}.",
    EventKind.POISONED: "You feel sick.",
    EventKind.POISON_DAMAGE: "The poison burns.",
    EventKind.NO_TARGET: "There is nothing to shoot at.",
    EventKind.TARGETING: "Target: {name}. [Tab] next, [f] fire, any other key cancels.",
    EventKind.SPOTTED_HOSTILE: "There is a {name} in view.",
    EventKind.ATTACK_WHICH_WAY: "Attack in which direction?",
    EventKind.ATTACKED_NOTHING: "You swing at thin air.",
    EventKind.SWAPPED_PLACES: "You swap places with the {name}.",
    EventKind.LOOKING: "{name}  [direction] move  [x] done",
    EventKind.RESTING: "You settle down to rest.",
    EventKind.RESTED: "You feel rested.",
    EventKind.CANNOT_REST: "Not with enemies in view.",
    EventKind.HOSTILE_IN_VIEW: "Not while a {name} is in view.",
}


def message_for(events: Sequence[Event]) -> str:
    """Render ``events`` into the single line of text the player sees.

    Multiple events are joined with a single space, in emission order.
    ``events`` is never mutated. An empty sequence returns ``""`` and never
    raises. A kind whose template requires ``{depth}``, ``{name}`` or
    ``{level}`` raises ``ValueError`` if the corresponding ``Event`` field is
    ``None``; a kind whose template has no such placeholder ignores that
    field entirely, even if supplied. The result is never truncated or
    padded — fitting the line is the renderer's job (the message-line cap of
    CONTRACT-v5 §16.1 is applied by ``game.py``, not here).
    """
    words = []
    for event in events:
        template = MESSAGES[event.kind]
        if "{depth}" in template and event.depth is None:
            raise ValueError(
                f"{event.kind.name} requires a depth but Event.depth is None"
            )
        if "{name}" in template and event.name is None:
            raise ValueError(
                f"{event.kind.name} requires a name but Event.name is None"
            )
        if "{level}" in template and event.level is None:
            raise ValueError(
                f"{event.kind.name} requires a level but Event.level is None"
            )
        words.append(
            template.format(depth=event.depth, name=event.name, level=event.level)
        )
    return " ".join(words)


# --- Look-mode vocabulary (the one place these words are written) ------------
#
# `describe_*` below build the sentence the look cursor shows. They live here
# with the rest of the wording rather than in `game.py`, which composes no
# English of its own.

CONDITION_WORDS: dict[str, str] = {
    "UNHURT": "unhurt",
    "SCRATCHED": "lightly hurt",
    "WOUNDED": "wounded",
    "BADLY_WOUNDED": "badly wounded",
    "NEAR_DEATH": "almost dead",
}
"""How a health band reads to a watching eye. Keyed by
:class:`roguelike.stats.Condition` member *name*, so this module keeps importing
nothing from the package."""

TERRAIN_WORDS: dict[str, str] = {
    "WALL": "a wall",
    "FLOOR": "the floor",
    "DOOR": "a door",
    "STAIRS_UP": "a staircase leading up",
    "STAIRS_DOWN": "a staircase leading down",
}
"""How a tile reads. Keyed by :class:`roguelike.tiles.Tile` member *name*, for the
same reason."""


def describe_monster(name: str, condition_name: str) -> str:
    """``"a jackal, badly wounded"`` — what the cursor says over a creature."""
    return f"a {name}, {CONDITION_WORDS[condition_name]}"


def describe_player(condition_name: str) -> str:
    """``"yourself, wounded"`` — the cursor over your own square."""
    return f"yourself, {CONDITION_WORDS[condition_name]}"


def describe_terrain(tile_name: str, door_is_open: bool = False) -> str:
    """``"a door (open)"`` — what the cursor says over bare terrain."""
    word = TERRAIN_WORDS[tile_name]
    if tile_name == "DOOR":
        return f"{word} ({'open' if door_is_open else 'closed'})"
    return word


UNSEEN_DESCRIPTION: str = "somewhere you have not seen"
"""What the cursor says over a cell that has never been in view."""

REMEMBERED_PREFIX: str = "remembered: "
"""Marks a description of terrain recalled rather than currently seen, so the
player is never told a monster is somewhere it merely used to be."""
