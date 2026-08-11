"""Unit tests for roguelike.keys (CONTRACT §5).

These tests import curses only to reference its KEY_* constants; nothing here
initialises a terminal, and the suite passes with stdin redirected from
/dev/null and no TTY attached.
"""

from __future__ import annotations

import ast
import curses
import curses.ascii
import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

from roguelike.keys import (
    Command,
    CommandKind,
    QUIT_COMMAND,
    UNKNOWN_COMMAND,
    translate_key,
)
from roguelike import keys as keys_module


# --- Binding table, reproduced independently from CONTRACT §5.1 -------------
# dy = -1 is NORTH (CONTRACT §0.1).

LETTER_BINDINGS = {
    "h": (-1, 0),
    "l": (1, 0),
    "k": (0, -1),
    "j": (0, 1),
    "y": (-1, -1),
    "u": (1, -1),
    "b": (-1, 1),
    "n": (1, 1),
}

DIGIT_BINDINGS = {
    "4": (-1, 0),
    "6": (1, 0),
    "8": (0, -1),
    "2": (0, 1),
    "7": (-1, -1),
    "9": (1, -1),
    "1": (-1, 1),
    "3": (1, 1),
}

ARROW_BINDINGS = {
    curses.KEY_LEFT: (-1, 0),
    curses.KEY_RIGHT: (1, 0),
    curses.KEY_UP: (0, -1),
    curses.KEY_DOWN: (0, 1),
}

ALL_EIGHT_DELTAS = {
    (dx, dy)
    for dx in (-1, 0, 1)
    for dy in (-1, 0, 1)
    if (dx, dy) != (0, 0)
}

# --- v4: Shift-diagonal bindings, rotated 45 degrees clockwise from the base
# direction (CONTRACT-v4 §5.1). Reproduced independently, as the acceptance
# criteria require, rather than imported from roguelike.keys.
#
# TRAP: curses names the Shift+Up/Shift+Down constants KEY_SR/KEY_SF ("scroll
# reverse"/"scroll forward"). KEY_SR is Shift+Up -> north-east -> negative dy.
# KEY_SF is Shift+Down -> south-west -> positive dy. A swap must fail loudly,
# so several tests below assert the sign explicitly rather than just equality.
SHIFT_ARROW_BINDINGS = {
    curses.KEY_SR: (1, -1),  # Shift+Up -> north-east
    curses.KEY_SRIGHT: (1, 1),  # Shift+Right -> south-east
    curses.KEY_SF: (-1, 1),  # Shift+Down -> south-west
    curses.KEY_SLEFT: (-1, -1),  # Shift+Left -> north-west
}

SHIFT_LETTER_BINDINGS = {
    "K": (1, -1),  # north-east
    "L": (1, 1),  # south-east
    "J": (-1, 1),  # south-west
    "H": (-1, -1),  # north-west
}

# Each Shift-diagonal must equal the pre-existing legacy diagonal it matches.
LEGACY_DIAGONAL_FOR_SHIFT_LETTER = {
    "K": "u",
    "L": "n",
    "J": "b",
    "H": "y",
}


# --- Movement bindings ------------------------------------------------------


@pytest.mark.parametrize(("char", "delta"), sorted(LETTER_BINDINGS.items()))
def test_letter_keys_as_str(char: str, delta: tuple[int, int]) -> None:
    assert translate_key(char) == Command(CommandKind.MOVE, delta[0], delta[1])


@pytest.mark.parametrize(("char", "delta"), sorted(LETTER_BINDINGS.items()))
def test_letter_keys_as_int(char: str, delta: tuple[int, int]) -> None:
    assert translate_key(ord(char)) == Command(CommandKind.MOVE, delta[0], delta[1])


@pytest.mark.parametrize(("char", "delta"), sorted(DIGIT_BINDINGS.items()))
def test_digit_keys_as_str(char: str, delta: tuple[int, int]) -> None:
    assert translate_key(char) == Command(CommandKind.MOVE, delta[0], delta[1])


@pytest.mark.parametrize(("char", "delta"), sorted(DIGIT_BINDINGS.items()))
def test_digit_keys_as_int(char: str, delta: tuple[int, int]) -> None:
    assert translate_key(ord(char)) == Command(CommandKind.MOVE, delta[0], delta[1])


@pytest.mark.parametrize(("code", "delta"), sorted(ARROW_BINDINGS.items()))
def test_arrow_keys(code: int, delta: tuple[int, int]) -> None:
    assert translate_key(code) == Command(CommandKind.MOVE, delta[0], delta[1])


def test_str_and_int_forms_agree() -> None:
    for char in list(LETTER_BINDINGS) + list(DIGIT_BINDINGS) + ["q", "Q"]:
        assert translate_key(char) == translate_key(ord(char))


# --- The direction of "up", stated on its own because it is the likely bug --


def test_north_is_negative_dy() -> None:
    north = Command(CommandKind.MOVE, 0, -1)
    assert translate_key("k") == north
    assert translate_key(ord("k")) == north
    assert translate_key(curses.KEY_UP) == north
    assert translate_key("8") == north


def test_south_is_positive_dy() -> None:
    south = Command(CommandKind.MOVE, 0, 1)
    assert translate_key("j") == south
    assert translate_key(curses.KEY_DOWN) == south
    assert translate_key("2") == south


def test_arrow_up_matches_k() -> None:
    assert translate_key(curses.KEY_UP) == translate_key("k")


def test_arrow_down_matches_j() -> None:
    assert translate_key(curses.KEY_DOWN) == translate_key("j")


def test_arrow_left_matches_h() -> None:
    assert translate_key(curses.KEY_LEFT) == translate_key("h")


def test_arrow_right_matches_l() -> None:
    assert translate_key(curses.KEY_RIGHT) == translate_key("l")


def test_diagonals_are_the_composition_of_their_cardinals() -> None:
    # y = north-west, u = north-east, b = south-west, n = south-east.
    north = translate_key("k")
    south = translate_key("j")
    west = translate_key("h")
    east = translate_key("l")
    for diagonal, vertical, horizontal in (
        ("y", north, west),
        ("u", north, east),
        ("b", south, west),
        ("n", south, east),
    ):
        command = translate_key(diagonal)
        assert (command.dx, command.dy) == (horizontal.dx, vertical.dy)


# --- v4: Shift-diagonals, rotated 45 degrees clockwise (CONTRACT-v4 §5.1) ---


@pytest.mark.parametrize(("code", "delta"), sorted(SHIFT_ARROW_BINDINGS.items()))
def test_shift_arrow_keys(code: int, delta: tuple[int, int]) -> None:
    assert translate_key(code) == Command(CommandKind.MOVE, delta[0], delta[1])


@pytest.mark.parametrize(("char", "delta"), sorted(SHIFT_LETTER_BINDINGS.items()))
def test_shift_letter_keys_as_str(char: str, delta: tuple[int, int]) -> None:
    assert translate_key(char) == Command(CommandKind.MOVE, delta[0], delta[1])


@pytest.mark.parametrize(("char", "delta"), sorted(SHIFT_LETTER_BINDINGS.items()))
def test_shift_letter_keys_as_int(char: str, delta: tuple[int, int]) -> None:
    assert translate_key(ord(char)) == Command(CommandKind.MOVE, delta[0], delta[1])


def test_key_sr_is_shift_up_and_yields_negative_dy() -> None:
    # THE NAMED TRAP: KEY_SR ("scroll reverse") is Shift+Up, which rotates 45
    # degrees clockwise to north-east — a NEGATIVE dy. If this were swapped
    # with KEY_SF, dy would come out positive here instead.
    command = translate_key(curses.KEY_SR)
    assert command.kind is CommandKind.MOVE
    assert command.dy < 0
    assert (command.dx, command.dy) == (1, -1)


def test_key_sf_is_shift_down_and_yields_positive_dy() -> None:
    # THE NAMED TRAP, other half: KEY_SF ("scroll forward") is Shift+Down,
    # which rotates 45 degrees clockwise to south-west — a POSITIVE dy.
    command = translate_key(curses.KEY_SF)
    assert command.kind is CommandKind.MOVE
    assert command.dy > 0
    assert (command.dx, command.dy) == (-1, 1)


def test_key_sr_and_key_sf_are_not_swapped_with_each_other() -> None:
    assert translate_key(curses.KEY_SR) != translate_key(curses.KEY_SF)
    assert translate_key(curses.KEY_SR).dy == -translate_key(curses.KEY_SF).dy


def test_shift_up_and_k_produce_equal_commands() -> None:
    assert translate_key(curses.KEY_SR) == translate_key("K")


def test_shift_right_and_l_produce_equal_commands() -> None:
    assert translate_key(curses.KEY_SRIGHT) == translate_key("L")


def test_shift_down_and_j_produce_equal_commands() -> None:
    assert translate_key(curses.KEY_SF) == translate_key("J")


def test_shift_left_and_h_produce_equal_commands() -> None:
    assert translate_key(curses.KEY_SLEFT) == translate_key("H")


@pytest.mark.parametrize(
    ("shift_letter", "legacy_letter"), sorted(LEGACY_DIAGONAL_FOR_SHIFT_LETTER.items())
)
def test_shift_diagonal_equals_legacy_letter_diagonal(
    shift_letter: str, legacy_letter: str
) -> None:
    assert translate_key(shift_letter) == translate_key(legacy_letter)


@pytest.mark.parametrize(
    ("shift_letter", "digit"),
    [("K", "9"), ("L", "3"), ("J", "1"), ("H", "7")],
)
def test_shift_diagonal_equals_legacy_digit_diagonal(
    shift_letter: str, digit: str
) -> None:
    assert translate_key(shift_letter) == translate_key(digit)


def test_shift_diagonals_cover_all_four_diagonal_directions() -> None:
    assert _deltas(SHIFT_LETTER_BINDINGS) == {(1, -1), (1, 1), (-1, 1), (-1, -1)}
    assert set(SHIFT_ARROW_BINDINGS.values()) == {(1, -1), (1, 1), (-1, 1), (-1, -1)}


# --- Exhaustiveness ---------------------------------------------------------


def _deltas(chars: object) -> set[tuple[int, int]]:
    return {
        (command.dx, command.dy)
        for command in (translate_key(c) for c in chars)  # type: ignore[union-attr]
        if command.kind is CommandKind.MOVE
    }


def test_letters_cover_all_eight_directions() -> None:
    assert _deltas(LETTER_BINDINGS) == ALL_EIGHT_DELTAS


def test_digits_cover_all_eight_directions() -> None:
    assert _deltas(DIGIT_BINDINGS) == ALL_EIGHT_DELTAS


def test_letters_and_digits_agree() -> None:
    assert _deltas(LETTER_BINDINGS) == _deltas(DIGIT_BINDINGS)


def test_arrows_cover_the_four_cardinals() -> None:
    assert _deltas(ARROW_BINDINGS) == {(-1, 0), (1, 0), (0, -1), (0, 1)}


def test_every_bound_key_in_the_table_is_expected() -> None:
    expected = {ord(c) for c in LETTER_BINDINGS}
    expected |= {ord(c) for c in DIGIT_BINDINGS}
    expected |= set(ARROW_BINDINGS)
    expected |= {ord("q"), ord("Q")}
    expected |= {ord(">"), ord("<")}
    # v4 additions: Shift-diagonals, auto-explore, walk-prefix.
    expected |= set(SHIFT_ARROW_BINDINGS)
    expected |= {ord(c) for c in SHIFT_LETTER_BINDINGS}
    expected |= {ord("E"), ord("w")}
    # v5 additions: fire, target-next (Tab).
    expected |= {ord("f"), curses.ascii.TAB}
    # The help screen, and the explicit-attack prefix.
    expected |= {ord("?"), ord("F")}
    assert set(keys_module._KEY_BINDINGS) == expected


# --- MOVE invariants --------------------------------------------------------


def test_no_move_command_is_stationary() -> None:
    for command in keys_module._KEY_BINDINGS.values():
        if command.kind is CommandKind.MOVE:
            assert (command.dx, command.dy) != (0, 0)


def test_every_move_delta_is_a_unit_step() -> None:
    for command in keys_module._KEY_BINDINGS.values():
        if command.kind is CommandKind.MOVE:
            assert command.dx in (-1, 0, 1)
            assert command.dy in (-1, 0, 1)


def test_non_move_commands_have_zero_deltas() -> None:
    for command in list(keys_module._KEY_BINDINGS.values()) + [UNKNOWN_COMMAND]:
        if command.kind is not CommandKind.MOVE:
            assert (command.dx, command.dy) == (0, 0)


# --- Quit -------------------------------------------------------------------


@pytest.mark.parametrize("key", ["q", "Q", ord("q"), ord("Q")])
def test_quit_keys(key: int | str) -> None:
    command = translate_key(key)
    assert command.kind is CommandKind.QUIT
    assert command == QUIT_COMMAND


# --- Stairs (CONTRACT-v3 §5): explicit command, not step-on-to-use ----------


def test_descend_as_str() -> None:
    command = translate_key(">")
    assert command.kind is CommandKind.DESCEND
    assert command.dx == 0
    assert command.dy == 0


def test_descend_as_int() -> None:
    command = translate_key(ord(">"))
    assert command.kind is CommandKind.DESCEND
    assert command.dx == 0
    assert command.dy == 0


def test_ascend_as_str() -> None:
    command = translate_key("<")
    assert command.kind is CommandKind.ASCEND
    assert command.dx == 0
    assert command.dy == 0


def test_ascend_as_int() -> None:
    command = translate_key(ord("<"))
    assert command.kind is CommandKind.ASCEND
    assert command.dx == 0
    assert command.dy == 0


def test_descend_str_and_int_forms_agree() -> None:
    assert translate_key(">") == translate_key(ord(">"))


def test_ascend_str_and_int_forms_agree() -> None:
    assert translate_key("<") == translate_key(ord("<"))


def test_descend_and_ascend_are_distinct_commands() -> None:
    assert translate_key(">") != translate_key("<")


def test_descend_and_ascend_do_not_collide_with_movement_or_quit() -> None:
    stair_commands = {translate_key(">"), translate_key("<")}
    other_commands = {
        translate_key(c) for c in list(LETTER_BINDINGS) + list(DIGIT_BINDINGS) + ["q", "Q"]
    }
    assert stair_commands.isdisjoint(other_commands)


# --- Auto-explore and walk-prefix (CONTRACT-v4 §5.1): new in v4 -------------


def test_auto_explore_as_str() -> None:
    command = translate_key("E")
    assert command.kind is CommandKind.AUTO_EXPLORE
    assert command.dx == 0
    assert command.dy == 0


def test_auto_explore_as_int() -> None:
    command = translate_key(ord("E"))
    assert command.kind is CommandKind.AUTO_EXPLORE
    assert command.dx == 0
    assert command.dy == 0


def test_walk_prefix_as_str() -> None:
    command = translate_key("w")
    assert command.kind is CommandKind.WALK_PREFIX
    assert command.dx == 0
    assert command.dy == 0


def test_walk_prefix_as_int() -> None:
    command = translate_key(ord("w"))
    assert command.kind is CommandKind.WALK_PREFIX
    assert command.dx == 0
    assert command.dy == 0


def test_auto_explore_str_and_int_forms_agree() -> None:
    assert translate_key("E") == translate_key(ord("E"))


def test_walk_prefix_str_and_int_forms_agree() -> None:
    assert translate_key("w") == translate_key(ord("w"))


def test_auto_explore_and_walk_prefix_are_distinct_commands() -> None:
    assert translate_key("E") != translate_key("w")


def test_lowercase_e_is_unknown_only_uppercase_e_is_auto_explore() -> None:
    assert translate_key("e") == UNKNOWN_COMMAND
    assert translate_key("E") != UNKNOWN_COMMAND


def test_uppercase_w_is_unknown_only_lowercase_w_is_walk_prefix() -> None:
    assert translate_key("W") == UNKNOWN_COMMAND
    assert translate_key("w") != UNKNOWN_COMMAND


def test_auto_explore_and_walk_prefix_do_not_collide_with_anything_else() -> None:
    new_commands = {translate_key("E"), translate_key("w")}
    other_commands = {
        translate_key(c)
        for c in list(LETTER_BINDINGS)
        + list(DIGIT_BINDINGS)
        + list(SHIFT_LETTER_BINDINGS)
        + ["q", "Q", ">", "<"]
    }
    assert new_commands.isdisjoint(other_commands)


# --- Fire and target-next (CONTRACT-v5 §5): new in v5 -----------------------


def test_fire_as_str() -> None:
    command = translate_key("f")
    assert command.kind is CommandKind.FIRE
    assert command.dx == 0
    assert command.dy == 0


def test_fire_as_int() -> None:
    command = translate_key(ord("f"))
    assert command.kind is CommandKind.FIRE
    assert command.dx == 0
    assert command.dy == 0


def test_fire_str_and_int_forms_agree() -> None:
    assert translate_key("f") == translate_key(ord("f"))


def test_target_next_is_curses_ascii_tab() -> None:
    command = translate_key(curses.ascii.TAB)
    assert command.kind is CommandKind.TARGET_NEXT
    assert command.dx == 0
    assert command.dy == 0


def test_target_next_is_code_nine_since_that_is_what_getch_returns() -> None:
    # getch() never hands back a name like curses.ascii.TAB, only the raw
    # integer code — assert the two agree, per the task brief.
    assert curses.ascii.TAB == 9
    command = translate_key(9)
    assert command.kind is CommandKind.TARGET_NEXT
    assert command.dx == 0
    assert command.dy == 0
    assert translate_key(9) == translate_key(curses.ascii.TAB)


def test_fire_and_target_next_are_distinct_commands() -> None:
    assert translate_key("f") != translate_key(curses.ascii.TAB)


def test_lowercase_f_fires_and_uppercase_f_attacks() -> None:
    # THE NAMED TRAP, and now sharper than when F was merely unbound: the two keys
    # are adjacent on the keyboard and do different things -- f shoots the bow at a
    # chosen target, F swings in a direction. Transposing them is the single most
    # likely bug in this table, so both directions are asserted explicitly.
    assert translate_key("f").kind is CommandKind.FIRE
    assert translate_key("F").kind is CommandKind.ATTACK
    assert translate_key("F") != translate_key("f")
    assert translate_key(ord("f")).kind is CommandKind.FIRE
    assert translate_key(ord("F")).kind is CommandKind.ATTACK
    # Both are argument-less intents; the direction arrives as the NEXT key.
    assert (translate_key("F").dx, translate_key("F").dy) == (0, 0)


def test_t_a_i_g_remain_unknown() -> None:
    # CONTRACT-v5 §5: "Verified unbound before assignment" — f, F, t, a, i, g
    # all currently map to UNKNOWN; only f gains a binding in v5.
    for char in "taig":
        assert translate_key(char) == UNKNOWN_COMMAND
        assert translate_key(char).kind is CommandKind.UNKNOWN


def test_fire_and_target_next_do_not_collide_with_anything_else() -> None:
    new_commands = {translate_key("f"), translate_key(curses.ascii.TAB)}
    other_commands = {
        translate_key(c)
        for c in list(LETTER_BINDINGS)
        + list(DIGIT_BINDINGS)
        + list(SHIFT_LETTER_BINDINGS)
        + ["q", "Q", ">", "<", "E", "w"]
    }
    assert new_commands.isdisjoint(other_commands)


# --- Unknown keys: ordinary input, never an error ---------------------------


@pytest.mark.parametrize(
    "key",
    [
        "5",  # numpad 5: there is no wait command (BRIEF Q8)
        ord("5"),
        27,  # ESC is not quit (BRIEF Q9)
        # H/J/K/L used to be UNKNOWN ("run" is out of scope) but are bound to
        # the Shift-diagonals as of v4 — see test_uppercase_hjkl_are_now_diagonals.
        "Y",  # uppercase of the remaining diagonal letters stay UNKNOWN
        "U",
        "B",
        "N",
        "Z",
        "z",
        "0",
        " ",
        "\n",
        "e",  # lowercase auto-explore is not bound — only "E" is
        "W",  # uppercase walk-prefix is not bound — only "w" is
        # v5: the still-unbound letters (CONTRACT-v5 §5). "F" is no longer among
        # them -- it is the explicit-attack prefix.
        "t",
        "a",
        "i",
        "g",
        -1,  # getch() with no input in non-blocking mode
        0,
        4,  # the integer four, not the numpad-4 key
        1,
        curses.KEY_HOME,
        curses.KEY_NPAGE,
        curses.KEY_RESIZE,
        10_000_000,
    ],
)
def test_unknown_keys_return_unknown_without_raising(key: int | str) -> None:
    assert translate_key(key) == UNKNOWN_COMMAND
    assert translate_key(key).kind is CommandKind.UNKNOWN


def test_uppercase_hjkl_are_now_diagonals() -> None:
    # v1-v3 treated uppercase H/J/K/L as UNKNOWN ("run" is out of scope). v4
    # repurposes them as the Shift-diagonal bindings (CONTRACT-v4 §5.1) — this
    # is the intended, contract-mandated change, not a regression.
    assert translate_key("K") == Command(CommandKind.MOVE, 1, -1)
    assert translate_key("L") == Command(CommandKind.MOVE, 1, 1)
    assert translate_key("J") == Command(CommandKind.MOVE, -1, 1)
    assert translate_key("H") == Command(CommandKind.MOVE, -1, -1)
    for char in "HJKL":
        assert translate_key(char) != UNKNOWN_COMMAND


def test_remaining_uppercase_movement_letters_are_still_unknown() -> None:
    # Y, U, B, N (uppercase of the other four diagonal letters) are not bound
    # to anything in v4 either.
    for char in "YUBN":
        assert translate_key(char) == UNKNOWN_COMMAND


def test_raw_integer_digits_are_not_numpad_keys() -> None:
    # ord('4') == 52; the int 4 is a control code, not a movement key. The
    # sole exception is 9, which is control code Tab (curses.ascii.TAB) and
    # is bound to TARGET_NEXT as of v5 — see test_target_next_is_curses_ascii_tab.
    assert ord("4") == 52
    for value in range(0, 10):
        if value == curses.ascii.TAB:
            continue
        assert translate_key(value) == UNKNOWN_COMMAND


def test_esc_is_not_quit() -> None:
    assert translate_key(27) == UNKNOWN_COMMAND


# --- Type errors ------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [None, 1.5, 3.0, "", "ab", "hjkl", b"h", [104], (104,), {"key": 104}, object()],
)
def test_wrong_type_raises_type_error(key: object) -> None:
    with pytest.raises(TypeError):
        translate_key(key)  # type: ignore[arg-type]


def test_bool_is_accepted_as_an_int_and_is_unknown() -> None:
    assert translate_key(True) == UNKNOWN_COMMAND
    assert translate_key(False) == UNKNOWN_COMMAND


# --- Command / CommandKind shape -------------------------------------------


def test_command_defaults() -> None:
    assert Command(CommandKind.QUIT) == Command(CommandKind.QUIT, 0, 0)
    assert Command(CommandKind.QUIT).dx == 0
    assert Command(CommandKind.QUIT).dy == 0


def test_command_is_frozen() -> None:
    command = translate_key("k")
    with pytest.raises(dataclasses.FrozenInstanceError):
        command.dx = 5  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        command.kind = CommandKind.QUIT  # type: ignore[misc]


def test_command_equality_is_by_value() -> None:
    assert Command(CommandKind.QUIT) == QUIT_COMMAND
    assert Command(CommandKind.UNKNOWN) == UNKNOWN_COMMAND
    assert Command(CommandKind.MOVE, 0, -1) == Command(CommandKind.MOVE, 0, -1)
    assert Command(CommandKind.MOVE, 0, -1) != Command(CommandKind.MOVE, 0, 1)


def test_command_is_hashable() -> None:
    assert len({translate_key("k"), translate_key(curses.KEY_UP)}) == 1


def test_command_kind_members() -> None:
    assert [member.name for member in CommandKind] == [
        "MOVE",
        "QUIT",
        "UNKNOWN",
        "DESCEND",
        "ASCEND",
        "AUTO_EXPLORE",
        "WALK_PREFIX",
        "FIRE",
        "TARGET_NEXT",
        "HELP",
        "ATTACK",
    ]


def test_command_kind_uses_auto_values() -> None:
    assert [member.value for member in CommandKind] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]


def test_command_kind_has_exactly_eleven_members() -> None:
    # The five from v3 (MOVE, QUIT, UNKNOWN, DESCEND, ASCEND), the two from v4
    # (AUTO_EXPLORE, WALK_PREFIX), the two from v5 (FIRE, TARGET_NEXT), and HELP.
    assert len(list(CommandKind)) == 11
    assert len(CommandKind) == 11


def test_module_constants_are_the_expected_kinds() -> None:
    assert QUIT_COMMAND.kind is CommandKind.QUIT
    assert UNKNOWN_COMMAND.kind is CommandKind.UNKNOWN


# --- Structural constraints: leaf module, stdlib only, no terminal ----------


def _keys_source() -> str:
    path = Path(keys_module.__file__)
    return path.read_text(encoding="utf-8")


def _imported_root_modules() -> set[str]:
    tree = ast.parse(_keys_source())
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


def test_keys_imports_nothing_from_the_roguelike_package() -> None:
    assert "roguelike" not in _imported_root_modules()


def test_keys_imports_only_the_standard_library() -> None:
    non_stdlib = {
        root
        for root in _imported_root_modules()
        if root not in sys.stdlib_module_names and root != "__future__"
    }
    assert non_stdlib == set()


def test_keys_uses_future_annotations() -> None:
    assert "__future__" in _imported_root_modules()


def test_keys_never_calls_a_terminal_mutating_curses_function() -> None:
    source = _keys_source()
    for forbidden in ("initscr", "wrapper", "newwin", "endwin", "setupterm"):
        assert f"curses.{forbidden}" not in source


def test_keys_references_curses_constants_rather_than_hardcoding_them() -> None:
    source = _keys_source()
    for constant in ("KEY_LEFT", "KEY_RIGHT", "KEY_UP", "KEY_DOWN"):
        assert f"curses.{constant}" in source
    for hardcoded in ("258", "259", "260", "261"):
        assert hardcoded not in source


def test_keys_references_shift_diagonal_constants_rather_than_hardcoding_them() -> None:
    # THE NAMED TRAP (CONTRACT-v4 §5.1): KEY_SR/KEY_SF are Shift+Up/Shift+Down
    # despite the "scroll" names. Must be referenced through curses, never as
    # the raw measured integers 337/336/402/393.
    source = _keys_source()
    for constant in ("KEY_SR", "KEY_SF", "KEY_SLEFT", "KEY_SRIGHT"):
        assert f"curses.{constant}" in source
    for hardcoded in ("337", "336", "393", "402"):
        assert hardcoded not in source


def test_keys_references_tab_constant_rather_than_hardcoding_it() -> None:
    # CONTRACT-v5 §5: Tab must be referenced as `curses.ascii.TAB`, never the
    # literal 9 — the same rule already applied to KEY_SR and friends. Walk
    # the AST rather than grepping for the substring "9", since "9" is also a
    # legitimate string literal elsewhere in the table (numpad north-east).
    source = _keys_source()
    assert "curses.ascii.TAB" in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, int) and not isinstance(value, bool) and value == 9:
                pytest.fail(
                    "found a literal integer 9 in keys.py; Tab must be "
                    "referenced as curses.ascii.TAB"
                )


def test_importing_keys_does_not_initialise_a_terminal() -> None:
    # curses.initscr() sets curses.LINES and curses.COLS as a side effect of
    # taking over the terminal. This module is imported at the top of this
    # file, so if it had initialised curses those names would now exist.
    assert not hasattr(curses, "LINES")
    assert not hasattr(curses, "COLS")


def test_keys_imports_cleanly_in_a_fresh_process_with_no_stdin() -> None:
    project_root = Path(keys_module.__file__).resolve().parents[1]
    code = (
        "import curses\n"
        "import roguelike.keys as k\n"
        "assert not hasattr(curses, 'LINES'), 'curses initialised at import time'\n"
        "assert k.translate_key('k').dy == -1\n"
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
