"""Unit tests for roguelike.events (CONTRACT-v3 §16, CONTRACT-v6 §16).

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
    EventKind.CLOSE_WHICH_WAY: "Close a door in which direction?",
    EventKind.DOOR_CLOSED: "The door closes.",
    EventKind.NOTHING_TO_CLOSE: "There is no open door that way.",
    EventKind.NO_DOOR_ADJACENT: "There is no open door beside you.",
    EventKind.DOORWAY_BLOCKED: "The {name} is standing in the doorway.",
    # -- v6: shields, resistance, pickup, equipment, chests (CONTRACT-v6 §16) --
    EventKind.SHIELD_BLOCKED: "Your shield turns the blow.",
    EventKind.NPC_SHIELD_BLOCKED: "The {name} blocks with its shield.",
    EventKind.RESISTED: "The {name} shrugs it off.",
    EventKind.VULNERABLE_HIT: "It tears into the {name}!",
    EventKind.IMMUNE_HIT: "The {name} is unharmed.",
    EventKind.PICKED_UP: "You pick up the {name}.",
    EventKind.NOTHING_TO_PICK_UP: "There is nothing here to pick up.",
    EventKind.PACK_FULL: "You cannot carry any more.",
    EventKind.EQUIPPED: "You ready the {name}.",
    EventKind.DROPPED: "You drop the {name}.",
    EventKind.DRANK: "You drink the {name}.",
    EventKind.BANDAGED: "You bind your wounds.",
    EventKind.CHEST_HERE: "There is a chest here.",
    EventKind.CHEST_OPENED: "The chest holds: {name}",
    EventKind.CHEST_EMPTY: "The chest is empty.",
}


# --- EventKind shape ----------------------------------------------------------


def test_event_kind_has_exactly_fifty_six_members() -> None:
    # The eight from v3, the eight from v4 (CONTRACT-v4 §16), the twenty new
    # v5 kinds (combat/death/levelling/poison/targeting plus the melee,
    # look and rest kinds that arrived alongside them), plus the fifteen new
    # v6 shield/resistance/pickup/equipment/chest kinds (CONTRACT-v6 §16), in
    # declaration order.
    assert [member.name for member in EventKind] == [
        "DOOR_OPENED",
        "STAIRS_HERE_UP",
        "STAIRS_HERE_DOWN",
        "DESCENDED",
        "ASCENDED",
        "LEFT_DUNGEON",
        "NO_STAIRS_DOWN",
        "NO_STAIRS_UP",
        "WALK_WHICH_WAY",
        "TRAVELLING",
        "ARRIVED",
        "EXPLORED_EVERYTHING",
        "NOTHING_FURTHER",
        "STOPPED_AT_JUNCTION",
        "STOPPED_AT_OPENING",
        "INTERRUPTED",
        "PLAYER_HIT_NPC",
        "PLAYER_MISSED_NPC",
        "NPC_HIT_PLAYER",
        "NPC_MISSED_PLAYER",
        "NPC_KILLED",
        "PLAYER_DIED",
        "LEVELLED_UP",
        "POISONED",
        "POISON_DAMAGE",
        "NO_TARGET",
        "TARGETING",
        "SPOTTED_HOSTILE",
        "ATTACK_WHICH_WAY",
        "ATTACKED_NOTHING",
        "SWAPPED_PLACES",
        "LOOKING",
        "RESTING",
        "RESTED",
        "CANNOT_REST",
        "HOSTILE_IN_VIEW",
        "CLOSE_WHICH_WAY",
        "DOOR_CLOSED",
        "NOTHING_TO_CLOSE",
        "NO_DOOR_ADJACENT",
        "DOORWAY_BLOCKED",
        "SHIELD_BLOCKED",
        "NPC_SHIELD_BLOCKED",
        "RESISTED",
        "VULNERABLE_HIT",
        "IMMUNE_HIT",
        "PICKED_UP",
        "NOTHING_TO_PICK_UP",
        "PACK_FULL",
        "EQUIPPED",
        "DROPPED",
        "DRANK",
        "BANDAGED",
        "CHEST_HERE",
        "CHEST_OPENED",
        "CHEST_EMPTY",
    ]


def test_event_kind_has_exactly_fifty_six_members_by_len() -> None:
    assert len(list(EventKind)) == 56


def test_event_kind_uses_auto_values() -> None:
    assert [member.value for member in EventKind] == list(range(1, 57))


# --- MESSAGES: complete and exact --------------------------------------------


def test_messages_has_an_entry_for_every_event_kind() -> None:
    # Iterates EventKind so a future kind added without wording fails loudly.
    for kind in EventKind:
        assert kind in MESSAGES, f"{kind.name} has no entry in MESSAGES"


def test_messages_has_no_extra_entries() -> None:
    assert set(MESSAGES) == set(EventKind)


def test_messages_has_exactly_fifty_six_entries() -> None:
    assert len(MESSAGES) == 56


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


# --- message_for: the eight new v4 activity messages (CONTRACT-v4 §16) -------


def test_walk_which_way_message() -> None:
    assert (
        message_for([Event(EventKind.WALK_WHICH_WAY)]) == "Walk in which direction?"
    )


def test_travelling_message() -> None:
    assert (
        message_for([Event(EventKind.TRAVELLING)])
        == "You travel towards the staircase."
    )


def test_arrived_message() -> None:
    assert message_for([Event(EventKind.ARRIVED)]) == "You arrive at the staircase."


def test_explored_everything_message() -> None:
    assert (
        message_for([Event(EventKind.EXPLORED_EVERYTHING)])
        == "You have explored everything you can reach here."
    )


def test_nothing_further_message() -> None:
    assert (
        message_for([Event(EventKind.NOTHING_FURTHER)])
        == "There is nowhere further to go."
    )


def test_stopped_at_junction_message() -> None:
    assert (
        message_for([Event(EventKind.STOPPED_AT_JUNCTION)])
        == "You stop at a junction."
    )


def test_stopped_at_opening_message() -> None:
    assert (
        message_for([Event(EventKind.STOPPED_AT_OPENING)])
        == "You stop before the opening."
    )


def test_interrupted_message() -> None:
    assert message_for([Event(EventKind.INTERRUPTED)]) == "You stop."


# --- message_for: the twelve new v5 combat/death/levelling/poison/targeting
# messages (CONTRACT-v5 §16) ---------------------------------------------------


def test_player_hit_npc_message() -> None:
    assert (
        message_for((Event(EventKind.PLAYER_HIT_NPC, name="jackal"),))
        == "You hit the jackal."
    )


def test_player_missed_npc_message() -> None:
    assert (
        message_for((Event(EventKind.PLAYER_MISSED_NPC, name="jackal"),))
        == "You miss the jackal."
    )


def test_npc_hit_player_message() -> None:
    assert (
        message_for((Event(EventKind.NPC_HIT_PLAYER, name="rat"),))
        == "The rat hits you."
    )


def test_npc_missed_player_message() -> None:
    assert (
        message_for((Event(EventKind.NPC_MISSED_PLAYER, name="rat"),))
        == "The rat misses you."
    )


def test_npc_killed_message() -> None:
    assert (
        message_for((Event(EventKind.NPC_KILLED, name="jackal"),))
        == "You kill the jackal!"
    )


def test_player_died_message() -> None:
    assert message_for((Event(EventKind.PLAYER_DIED),)) == "You die..."


def test_levelled_up_message() -> None:
    assert (
        message_for((Event(EventKind.LEVELLED_UP, level=3),))
        == "Welcome to level 3."
    )


def test_poisoned_message() -> None:
    assert message_for((Event(EventKind.POISONED),)) == "You feel sick."


def test_poison_damage_message() -> None:
    assert message_for((Event(EventKind.POISON_DAMAGE),)) == "The poison burns."


def test_no_target_message() -> None:
    assert (
        message_for((Event(EventKind.NO_TARGET),))
        == "There is nothing to shoot at."
    )


def test_targeting_message() -> None:
    assert (
        message_for((Event(EventKind.TARGETING, name="cave snake"),))
        == "Target: cave snake. [Tab] next, [f] fire, any other key cancels."
    )


def test_spotted_hostile_message() -> None:
    assert (
        message_for((Event(EventKind.SPOTTED_HOSTILE, name="giant bat"),))
        == "There is a giant bat in view."
    )


# --- message_for: the fifteen new v6 shield/resistance/pickup/equipment/chest
# messages (CONTRACT-v6 §16) — each asserted literally ------------------------


def test_shield_blocked_message() -> None:
    assert (
        message_for((Event(EventKind.SHIELD_BLOCKED),))
        == "Your shield turns the blow."
    )


def test_npc_shield_blocked_message() -> None:
    assert (
        message_for((Event(EventKind.NPC_SHIELD_BLOCKED, name="orc"),))
        == "The orc blocks with its shield."
    )


def test_resisted_message() -> None:
    assert (
        message_for((Event(EventKind.RESISTED, name="skeleton"),))
        == "The skeleton shrugs it off."
    )


def test_vulnerable_hit_message() -> None:
    assert (
        message_for((Event(EventKind.VULNERABLE_HIT, name="zombie"),))
        == "It tears into the zombie!"
    )


def test_immune_hit_message() -> None:
    assert (
        message_for((Event(EventKind.IMMUNE_HIT, name="rust monster"),))
        == "The rust monster is unharmed."
    )


def test_picked_up_message() -> None:
    assert (
        message_for((Event(EventKind.PICKED_UP, name="dagger"),))
        == "You pick up the dagger."
    )


def test_nothing_to_pick_up_message() -> None:
    assert (
        message_for((Event(EventKind.NOTHING_TO_PICK_UP),))
        == "There is nothing here to pick up."
    )


def test_pack_full_message() -> None:
    assert (
        message_for((Event(EventKind.PACK_FULL),)) == "You cannot carry any more."
    )


def test_equipped_message() -> None:
    assert (
        message_for((Event(EventKind.EQUIPPED, name="long sword"),))
        == "You ready the long sword."
    )


def test_dropped_message() -> None:
    assert (
        message_for((Event(EventKind.DROPPED, name="shield"),))
        == "You drop the shield."
    )


def test_drank_message() -> None:
    assert (
        message_for((Event(EventKind.DRANK, name="potion of healing"),))
        == "You drink the potion of healing."
    )


def test_bandaged_message() -> None:
    assert message_for((Event(EventKind.BANDAGED),)) == "You bind your wounds."


def test_chest_here_message() -> None:
    assert (
        message_for((Event(EventKind.CHEST_HERE),)) == "There is a chest here."
    )


def test_chest_opened_message() -> None:
    assert (
        message_for((Event(EventKind.CHEST_OPENED, name="a leather pack"),))
        == "The chest holds: a leather pack"
    )


def test_chest_empty_message() -> None:
    assert message_for((Event(EventKind.CHEST_EMPTY),)) == "The chest is empty."


# --- v6: name requirement -----------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    [
        EventKind.NPC_SHIELD_BLOCKED,
        EventKind.RESISTED,
        EventKind.VULNERABLE_HIT,
        EventKind.IMMUNE_HIT,
        EventKind.PICKED_UP,
        EventKind.EQUIPPED,
        EventKind.DROPPED,
        EventKind.DRANK,
        EventKind.CHEST_OPENED,
    ],
)
def test_v6_missing_name_raises_value_error(kind: EventKind) -> None:
    with pytest.raises(ValueError):
        message_for((Event(kind),))
    with pytest.raises(ValueError):
        message_for((Event(kind, name=None),))


@pytest.mark.parametrize(
    "kind",
    [
        EventKind.SHIELD_BLOCKED,
        EventKind.NOTHING_TO_PICK_UP,
        EventKind.PACK_FULL,
        EventKind.BANDAGED,
        EventKind.CHEST_HERE,
        EventKind.CHEST_EMPTY,
    ],
)
def test_no_placeholder_v6_kinds_ignore_all_supplied_fields(kind: EventKind) -> None:
    plain = message_for((Event(kind),))
    with_everything = message_for((Event(kind, depth=1, name="rat", level=2),))
    assert plain == with_everything == MESSAGES[kind]


# --- v5: name/level requirement -----------------------------------------------


@pytest.mark.parametrize(
    "kind",
    [
        EventKind.PLAYER_HIT_NPC,
        EventKind.PLAYER_MISSED_NPC,
        EventKind.NPC_HIT_PLAYER,
        EventKind.NPC_MISSED_PLAYER,
        EventKind.NPC_KILLED,
        EventKind.TARGETING,
        EventKind.SPOTTED_HOSTILE,
    ],
)
def test_missing_name_raises_value_error(kind: EventKind) -> None:
    with pytest.raises(ValueError):
        message_for((Event(kind),))
    with pytest.raises(ValueError):
        message_for((Event(kind, name=None),))


def test_missing_level_raises_value_error_for_levelled_up() -> None:
    with pytest.raises(ValueError):
        message_for((Event(EventKind.LEVELLED_UP),))
    with pytest.raises(ValueError):
        message_for((Event(EventKind.LEVELLED_UP, level=None),))


def test_irrelevant_fields_are_ignored_for_player_died() -> None:
    # PLAYER_DIED's template has no placeholders at all: name, level and
    # depth must all be ignored, and none of them should raise.
    event = Event(EventKind.PLAYER_DIED, name="rat", level=9, depth=2)
    assert message_for((event,)) == "You die..."


def test_irrelevant_depth_and_level_ignored_for_name_only_kinds() -> None:
    event = Event(EventKind.NPC_KILLED, name="jackal", level=9, depth=2)
    assert message_for((event,)) == "You kill the jackal!"


def test_irrelevant_depth_and_name_ignored_for_level_only_kind() -> None:
    event = Event(EventKind.LEVELLED_UP, level=3, name="rat", depth=2)
    assert message_for((event,)) == "Welcome to level 3."


@pytest.mark.parametrize(
    "kind",
    [
        EventKind.PLAYER_DIED,
        EventKind.POISONED,
        EventKind.POISON_DAMAGE,
        EventKind.NO_TARGET,
    ],
)
def test_no_placeholder_v5_kinds_ignore_all_supplied_fields(kind: EventKind) -> None:
    plain = message_for((Event(kind),))
    with_everything = message_for((Event(kind, depth=1, name="rat", level=2),))
    assert plain == with_everything == MESSAGES[kind]


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
        EventKind.WALK_WHICH_WAY,
        EventKind.TRAVELLING,
        EventKind.ARRIVED,
        EventKind.EXPLORED_EVERYTHING,
        EventKind.NOTHING_FURTHER,
        EventKind.STOPPED_AT_JUNCTION,
        EventKind.STOPPED_AT_OPENING,
        EventKind.INTERRUPTED,
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


def test_three_v5_combat_events_join_in_order() -> None:
    # v5 is the first caller that can plausibly emit more than two events in
    # one tick (several NPCs acting), so exercise a full three-event combat
    # line explicitly, in addition to the pre-existing v1-v4 coverage above.
    events = (
        Event(EventKind.PLAYER_HIT_NPC, name="jackal"),
        Event(EventKind.NPC_KILLED, name="jackal"),
        Event(EventKind.LEVELLED_UP, level=2),
    )
    assert message_for(events) == (
        "You hit the jackal. You kill the jackal! Welcome to level 2."
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


def test_event_defaults_name_and_level_to_none() -> None:
    # v5: the two new fields (CONTRACT-v5 §16) must default to None so every
    # v1-v4 construction keeps working unchanged.
    event = Event(EventKind.DOOR_OPENED)
    assert event.name is None
    assert event.level is None


def test_event_is_frozen() -> None:
    event = Event(EventKind.DOOR_OPENED)
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.depth = 3  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.kind = EventKind.ASCENDED  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.name = "rat"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.level = 2  # type: ignore[misc]


def test_event_equality_is_by_value() -> None:
    assert Event(EventKind.DESCENDED, depth=2) == Event(EventKind.DESCENDED, depth=2)
    assert Event(EventKind.DESCENDED, depth=2) != Event(EventKind.DESCENDED, depth=3)
    assert Event(EventKind.DESCENDED) != Event(EventKind.ASCENDED)
    assert Event(EventKind.NPC_KILLED, name="rat") == Event(
        EventKind.NPC_KILLED, name="rat"
    )
    assert Event(EventKind.NPC_KILLED, name="rat") != Event(
        EventKind.NPC_KILLED, name="jackal"
    )
    assert Event(EventKind.LEVELLED_UP, level=2) != Event(
        EventKind.LEVELLED_UP, level=3
    )


def test_event_is_hashable() -> None:
    assert len({Event(EventKind.DOOR_OPENED), Event(EventKind.DOOR_OPENED)}) == 1


def test_event_field_order_is_kind_depth_name_level() -> None:
    # CONTRACT-v5 §16: the two new fields are appended after depth, so every
    # existing positional construction keeps working unchanged.
    assert [field.name for field in dataclasses.fields(Event)] == [
        "kind",
        "depth",
        "name",
        "level",
    ]
    event = Event(EventKind.DESCENDED, 3)
    assert event.kind is EventKind.DESCENDED
    assert event.depth == 3
    assert event.name is None
    assert event.level is None
    full = Event(EventKind.NPC_KILLED, None, "jackal", None)
    assert full.name == "jackal"


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
    # "BLOCKED" is deliberately not in this forbidden list as of v6:
    # SHIELD_BLOCKED / NPC_SHIELD_BLOCKED are legitimate combat events about a
    # shield turning a blow, not a player misstepping into scenery — the
    # thing this test guards against. "SHIELD_BLOCKED" contains neither
    # "BUMP" nor "WALL", so it cannot be confused with that here.
    names = {member.name for member in EventKind}
    for forbidden in ("BUMP", "WALL", "BUMPED"):
        assert not any(forbidden in name for name in names)


def test_no_severity_or_timestamp_fields_on_event() -> None:
    # v5 adds exactly `name` and `level` (CONTRACT-v5 §16) — no severity, no
    # timestamp, no priority field; the message-line cap lives in game.py.
    field_names = {field.name for field in dataclasses.fields(Event)}
    assert field_names == {"kind", "depth", "name", "level"}


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
