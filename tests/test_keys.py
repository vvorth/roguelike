"""Unit tests for roguelike.keys (CONTRACT §5).

These tests import curses only to reference its KEY_* constants; nothing here
initialises a terminal, and the suite passes with stdin redirected from
/dev/null and no TTY attached.
"""

from __future__ import annotations

import ast
import curses
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


# --- Unknown keys: ordinary input, never an error ---------------------------


@pytest.mark.parametrize(
    "key",
    [
        "5",  # numpad 5: there is no wait command (BRIEF Q8)
        ord("5"),
        27,  # ESC is not quit (BRIEF Q9)
        "H",  # uppercase movement letters mean "run": out of scope
        "J",
        "K",
        "L",
        "Y",
        "U",
        "B",
        "N",
        "Z",
        "z",
        "0",
        " ",
        "\n",
        "\t",
        -1,  # getch() with no input in non-blocking mode
        0,
        4,  # the integer four, not the numpad-4 key
        1,
        9,
        curses.KEY_HOME,
        curses.KEY_NPAGE,
        curses.KEY_RESIZE,
        10_000_000,
    ],
)
def test_unknown_keys_return_unknown_without_raising(key: int | str) -> None:
    assert translate_key(key) == UNKNOWN_COMMAND
    assert translate_key(key).kind is CommandKind.UNKNOWN


def test_uppercase_movement_letters_are_unknown() -> None:
    for char in LETTER_BINDINGS:
        assert translate_key(char.upper()) == UNKNOWN_COMMAND


def test_raw_integer_digits_are_not_numpad_keys() -> None:
    # ord('4') == 52; the int 4 is a control code, not a movement key.
    assert ord("4") == 52
    for value in range(0, 10):
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
    ]


def test_command_kind_uses_auto_values() -> None:
    assert [member.value for member in CommandKind] == [1, 2, 3, 4, 5]


def test_command_kind_has_exactly_five_members() -> None:
    assert len(list(CommandKind)) == 5


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
