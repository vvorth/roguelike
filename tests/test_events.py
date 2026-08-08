"""Unit tests for roguelike.events (CONTRACT-v3 §16).

These tests import no other project module and initialise no terminal; the
suite passes with stdin redirected from /dev/null and no TTY attached.
"""

from __future__ import annotations

import ast
import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

from roguelike.events import Event, EventKind, MESSAGES, message_for
from roguelike import events as events_module


# --- The wording table — binding, CONTRACT-v3 §16.1 -------------------------
# Reproduced independently (as string literals) rather than imported, so a
# typo in MESSAGES cannot silently pass its own test.

EXPECTED_MESSAGES = {
    EventKind.DOOR_OPENED: "The door opens.",
    EventKind.STAIRS_HERE_UP: "There is a staircase leading up here.",
    EventKind.STAIRS_HERE_DOWN: "There is a staircase leading down here.",
    EventKind.DESCENDED: "You descend to level {depth}.",
    EventKind.ASCENDED: "You climb up to level {depth}.",
    EventKind.LEFT_DUNGEON: "You climb out of the dungeon and give up. Farewell.",
    EventKind.NO_STAIRS_DOWN: "There are no stairs leading down here.",
    EventKind.NO_STAIRS_UP: "There are no stairs leading up here.",
}


# --- EventKind shape ----------------------------------------------------------


def test_event_kind_has_exactly_eight_members() -> None:
    assert [member.name for member in EventKind] == [
        "DOOR_OPENED",
        "STAIRS_HERE_UP",
        "STAIRS_HERE_DOWN",
        "DESCENDED",
        "ASCENDED",
        "LEFT_DUNGEON",
        "NO_STAIRS_DOWN",
        "NO_STAIRS_UP",
    ]


def test_event_kind_uses_auto_values() -> None:
    assert [member.value for member in EventKind] == list(range(1, 9))


# --- MESSAGES: complete and exact --------------------------------------------


def test_messages_has_an_entry_for_every_event_kind() -> None:
    # Iterates EventKind so a future kind added without wording fails loudly.
    for kind in EventKind:
        assert kind in MESSAGES, f"{kind.name} has no entry in MESSAGES"


def test_messages_has_no_extra_entries() -> None:
    assert set(MESSAGES) == set(EventKind)


@pytest.mark.parametrize("kind", list(EventKind))
def test_message_matches_the_binding_table_exactly(kind: EventKind) -> None:
    assert MESSAGES[kind] == EXPECTED_MESSAGES[kind]


def test_messages_are_plain_strings() -> None:
    for template in MESSAGES.values():
        assert isinstance(template, str)


# --- message_for: single events -----------------------------------------------


def test_door_opened_message() -> None:
    assert message_for([Event(EventKind.DOOR_OPENED)]) == "The door opens."


def test_stairs_here_up_message() -> None:
    assert (
        message_for([Event(EventKind.STAIRS_HERE_UP)])
        == "There is a staircase leading up here."
    )


def test_stairs_here_down_message() -> None:
    assert (
        message_for([Event(EventKind.STAIRS_HERE_DOWN)])
        == "There is a staircase leading down here."
    )


def test_no_stairs_down_message() -> None:
    assert (
        message_for([Event(EventKind.NO_STAIRS_DOWN)])
        == "There are no stairs leading down here."
    )


def test_no_stairs_up_message() -> None:
    assert (
        message_for([Event(EventKind.NO_STAIRS_UP)])
        == "There are no stairs leading up here."
    )


def test_left_dungeon_message() -> None:
    assert (
        message_for([Event(EventKind.LEFT_DUNGEON)])
        == "You climb out of the dungeon and give up. Farewell."
    )


def test_descended_message_fills_depth() -> None:
    assert (
        message_for([Event(EventKind.DESCENDED, depth=3)])
        == "You descend to level 3."
    )


def test_ascended_message_fills_depth() -> None:
    assert (
        message_for([Event(EventKind.ASCENDED, depth=1)]) == "You climb up to level 1."
    )


def test_depth_zero_is_a_valid_depth_value() -> None:
    # depth=0 is falsy but not None; must still be filled in, not treated as
    # "missing".
    assert message_for([Event(EventKind.DESCENDED, depth=0)]) == "You descend to level 0."


# --- depth requirement -------------------------------------------------------


def test_missing_depth_raises_value_error_for_descended() -> None:
    with pytest.raises(ValueError):
        message_for([Event(EventKind.DESCENDED)])


def test_missing_depth_raises_value_error_for_ascended() -> None:
    with pytest.raises(ValueError):
        message_for([Event(EventKind.ASCENDED, depth=None)])


@pytest.mark.parametrize(
    "kind",
    [
        EventKind.DOOR_OPENED,
        EventKind.STAIRS_HERE_UP,
        EventKind.STAIRS_HERE_DOWN,
        EventKind.LEFT_DUNGEON,
        EventKind.NO_STAIRS_DOWN,
        EventKind.NO_STAIRS_UP,
    ],
)
def test_depthless_kinds_ignore_a_supplied_depth(kind: EventKind) -> None:
    with_depth = message_for([Event(kind, depth=7)])
    without_depth = message_for([Event(kind)])
    assert with_depth == without_depth == MESSAGES[kind]


# --- message_for: empty input -------------------------------------------------


def test_empty_list_returns_empty_string() -> None:
    assert message_for([]) == ""


def test_empty_tuple_returns_empty_string() -> None:
    assert message_for(()) == ""


def test_empty_input_never_raises() -> None:
    message_for([])
    message_for(())


# --- message_for: joining multiple events ------------------------------------


def test_two_events_join_with_a_single_space() -> None:
    result = message_for([Event(EventKind.DOOR_OPENED), Event(EventKind.DESCENDED, depth=2)])
    assert result == "The door opens. You descend to level 2."


def test_join_order_matches_emission_order() -> None:
    forward = message_for(
        [Event(EventKind.DOOR_OPENED), Event(EventKind.STAIRS_HERE_DOWN)]
    )
    backward = message_for(
        [Event(EventKind.STAIRS_HERE_DOWN), Event(EventKind.DOOR_OPENED)]
    )
    assert forward == "The door opens. There is a staircase leading down here."
    assert backward == "There is a staircase leading down here. The door opens."
    assert forward != backward


def test_three_events_join_in_order() -> None:
    events = [
        Event(EventKind.DOOR_OPENED),
        Event(EventKind.STAIRS_HERE_DOWN),
        Event(EventKind.NO_STAIRS_UP),
    ]
    assert message_for(events) == (
        "The door opens. There is a staircase leading down here. "
        "There are no stairs leading up here."
    )


# --- purity -------------------------------------------------------------------


def test_message_for_is_pure() -> None:
    events = (Event(EventKind.DESCENDED, depth=4),)
    first = message_for(events)
    second = message_for(events)
    assert first == second == "You descend to level 4."


def test_message_for_does_not_mutate_a_list_input() -> None:
    events = [Event(EventKind.DOOR_OPENED), Event(EventKind.DESCENDED, depth=5)]
    before = list(events)
    message_for(events)
    assert events == before


def test_message_for_accepts_any_sequence() -> None:
    events = (Event(EventKind.DOOR_OPENED),)
    assert message_for(events) == message_for(list(events))


# --- Event shape --------------------------------------------------------------


def test_event_defaults_depth_to_none() -> None:
    event = Event(EventKind.DOOR_OPENED)
    assert event.depth is None


def test_event_is_frozen() -> None:
    event = Event(EventKind.DOOR_OPENED)
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.depth = 3  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.kind = EventKind.ASCENDED  # type: ignore[misc]


def test_event_equality_is_by_value() -> None:
    assert Event(EventKind.DESCENDED, depth=2) == Event(EventKind.DESCENDED, depth=2)
    assert Event(EventKind.DESCENDED, depth=2) != Event(EventKind.DESCENDED, depth=3)
    assert Event(EventKind.DESCENDED) != Event(EventKind.ASCENDED)


def test_event_is_hashable() -> None:
    assert len({Event(EventKind.DOOR_OPENED), Event(EventKind.DOOR_OPENED)}) == 1


# --- Structural constraints: leaf module, stdlib only, no terminal ----------


def _events_source() -> str:
    path = Path(events_module.__file__)
    return path.read_text(encoding="utf-8")


def _imported_root_modules() -> set[str]:
    tree = ast.parse(_events_source())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # a relative import is by definition intra-package
                roots.add("roguelike")
            elif node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_events_imports_nothing_from_the_roguelike_package() -> None:
    assert "roguelike" not in _imported_root_modules()


def test_events_does_not_import_curses() -> None:
    assert "curses" not in _imported_root_modules()
    assert "import curses" not in _events_source()


def test_events_imports_only_the_standard_library() -> None:
    non_stdlib = {
        root
        for root in _imported_root_modules()
        if root not in sys.stdlib_module_names and root != "__future__"
    }
    assert non_stdlib == set()


def test_events_uses_future_annotations() -> None:
    assert "__future__" in _imported_root_modules()


def test_no_bump_into_wall_event_exists() -> None:
    names = {member.name for member in EventKind}
    for forbidden in ("BUMP", "WALL", "BUMPED", "BLOCKED"):
        assert not any(forbidden in name for name in names)


def test_no_severity_or_timestamp_fields_on_event() -> None:
    field_names = {field.name for field in dataclasses.fields(Event)}
    assert field_names == {"kind", "depth"}


def test_importing_events_does_not_initialise_a_terminal() -> None:
    import curses

    assert not hasattr(curses, "LINES")
    assert not hasattr(curses, "COLS")


def test_events_imports_cleanly_in_a_fresh_process_with_no_stdin() -> None:
    project_root = Path(events_module.__file__).resolve().parents[1]
    code = (
        "import roguelike.events as e\n"
        "assert 'curses' not in dir(e)\n"
        "msg = e.message_for([e.Event(e.EventKind.DESCENDED, depth=9)])\n"
        "assert msg == 'You descend to level 9.', msg\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project_root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
