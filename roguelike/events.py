"""Event vocabulary and message wording (CONTRACT-v3 §16).

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


@dataclass(frozen=True)
class Event:
    """One structured occurrence.

    ``depth`` is only meaningful for kinds whose message template uses
    ``{depth}``; other kinds ignore it entirely, even if a caller supplies
    one.
    """

    kind: EventKind
    depth: int | None = None


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
}


def message_for(events: Sequence[Event]) -> str:
    """Render ``events`` into the single line of text the player sees.

    Multiple events are joined with a single space, in emission order.
    ``events`` is never mutated. An empty sequence returns ``""`` and never
    raises. A kind whose template requires ``{depth}`` raises ``ValueError``
    if the corresponding event's ``depth`` is ``None``; a kind whose template
    has no placeholder ignores ``depth`` entirely. The result is never
    truncated or padded — fitting the line is the renderer's job.
    """
    words = []
    for event in events:
        template = MESSAGES[event.kind]
        if "{depth}" in template and event.depth is None:
            raise ValueError(
                f"{event.kind.name} requires a depth but Event.depth is None"
            )
        words.append(template.format(depth=event.depth))
    return " ".join(words)
