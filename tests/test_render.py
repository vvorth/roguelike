"""Unit tests for :mod:`roguelike.render` (CONTRACT-v3 §4).

Everything here exercises the pure functions :func:`render_to_cells` and
:func:`to_lines` directly. A small hand-written fake window covers
:func:`draw`/:func:`init_colors`; no test initialises curses, and the suite passes with
no TTY attached (CONTRACT §0.3, §12) — every real ``curses.*`` call made by
``init_colors``/``draw`` fails immediately with ``curses.error`` (curses was never
``initscr``'d) and is swallowed internally, exactly the degrade-not-crash path the
contract requires.

Unlike ``render.py`` itself, this file *may* spell the glyphs out — pinning the mapping
against literals is the point of several of the tests below.
"""

from __future__ import annotations

import ast
import copy
import curses
from pathlib import Path

import pytest

from roguelike.level import Level, Room, freeze_grid
from roguelike.render import Cell, Chrome, draw, init_colors, render_to_cells, to_lines
from roguelike.style import Role, Visibility
from roguelike.tiles import DOOR_OPEN_CHAR, PLAYER_CHAR, TILE_CHARS, Tile

W = Tile.WALL
F = Tile.FLOOR
D = Tile.DOOR
SU = Tile.STAIRS_UP
SD = Tile.STAIRS_DOWN

RENDER_SOURCE = Path(__file__).resolve().parent.parent / "roguelike" / "render.py"


# --------------------------------------------------------------------------- helpers


def make_level(rows: list[list[Tile]], player_start: tuple[int, int] = (0, 0),
               rooms: tuple[Room, ...] = (), seed: int = 0) -> Level:
    """Build a Level from a list of rows written out visually (rows[y][x])."""
    return Level(
        len(rows[0]),
        len(rows),
        freeze_grid(rows),
        rooms,
        player_start,
        seed,
    )


def all_wall_level(width: int, height: int) -> Level:
    return make_level([[W] * width for _ in range(height)])


def all_coords(level: Level) -> frozenset[tuple[int, int]]:
    return frozenset(
        (x, y) for y in range(level.height) for x in range(level.width)
    )


EMPTY: frozenset[tuple[int, int]] = frozenset()


# A deliberately non-square, asymmetric 5x3 level.
#   x: 01234
SMALL_ROWS = [
    [W, W, W, W, W],   # y=0
    [W, F, F, D, W],   # y=1
    [W, W, W, W, W],   # y=2
]


def small_level() -> Level:
    return make_level(SMALL_ROWS, player_start=(1, 1))


def render(level, player_pos, visible=EMPTY, explored=EMPTY, open_doors=EMPTY, status=""):
    """Convenience wrapper: render_to_cells + to_lines in one call.

    ``status`` maps onto ``Chrome.message`` for backward-compatible call sites — most of
    v2's status-line tests are really exercising the message half of the new status row.
    """
    chrome = Chrome(message=status)
    return to_lines(render_to_cells(level, player_pos, visible, explored, open_doors, chrome))


# ------------------------------------------------------------------ shape of the frame


def test_row_count_is_height_plus_two():
    level = small_level()
    cells = render_to_cells(level, (1, 1), EMPTY, EMPTY, EMPTY, Chrome(message="hi"))
    assert len(cells) == level.height + 2 == 5


@pytest.mark.parametrize("width,height", [(1, 1), (5, 3), (7, 3), (3, 7), (80, 22)])
def test_every_row_has_exactly_width_cells(width, height):
    level = all_wall_level(width, height)
    cells = render_to_cells(level, (0, 0), EMPTY, EMPTY, EMPTY, Chrome(message="status text"))
    assert len(cells) == height + 2
    assert all(len(row) == width for row in cells)


def test_default_80x22_level_produces_a_24_row_frame():
    """The classic terminal budget (RESEARCH-v3 §5): stats(1) + map(22) + status(1) = 24."""
    level = all_wall_level(80, 22)
    cells = render_to_cells(level, (0, 0), EMPTY, EMPTY, EMPTY, Chrome())
    assert len(cells) == 24


@pytest.mark.parametrize("width,height", [(1, 1), (5, 3), (7, 3), (3, 7), (80, 22)])
def test_to_lines_every_string_is_exactly_width_chars(width, height):
    level = all_wall_level(width, height)
    lines = render(level, (0, 0), status="status text of some length")
    assert len(lines) == height + 2
    assert all(len(line) == width for line in lines)


@pytest.mark.parametrize(
    "message,expected",
    [
        ("", " " * 10),                                  # empty
        ("short", "short     "),                          # shorter than width (10)
        # exactly the width: with an empty status_right, one column is still reserved
        # as the message/status_right separator (CONTRACT-v3 §4.2's formula reserves it
        # unconditionally), so the last character is truncated away.
        ("exactly10!", "exactly10 "),
        ("far longer than ten characters", "far longe "),
    ],
)
def test_message_only_status_line_is_padded_or_truncated_to_width(message, expected):
    level = all_wall_level(10, 2)
    lines = render(level, (0, 0), status=message)
    status_line = lines[-1]
    assert len(status_line) == 10
    assert status_line == expected


def test_status_shorter_than_width_is_space_padded():
    level = all_wall_level(10, 1)
    lines = render(level, (99, 99), status="ab")
    assert lines[-1] == "ab" + " " * 8


def test_status_row_cells_are_terrain_and_visible():
    level = all_wall_level(6, 2)
    cells = render_to_cells(level, (0, 0), EMPTY, EMPTY, EMPTY, Chrome(message="hello!"))
    for cell in cells[-1]:
        assert cell.role is Role.TERRAIN
        assert cell.visibility is Visibility.VISIBLE


# ------------------------------------------------------------------------------ chrome


def test_stats_row_reproduces_chrome_stats_padded_to_width():
    level = all_wall_level(8, 2)
    cells = render_to_cells(level, (0, 0), EMPTY, EMPTY, EMPTY, Chrome(stats="HP 10"))
    stats_line = "".join(c.char for c in cells[0])
    assert stats_line == "HP 10   "


def test_stats_row_is_all_spaces_with_default_chrome():
    level = all_wall_level(6, 3)
    cells = render_to_cells(level, (0, 0), EMPTY, EMPTY, EMPTY, Chrome())
    stats_line = "".join(c.char for c in cells[0])
    assert stats_line == " " * 6


def test_both_chrome_rows_are_terrain_and_visible_every_cell():
    level = all_wall_level(6, 3)
    cells = render_to_cells(
        level, (0, 0), EMPTY, EMPTY, EMPTY, Chrome(stats="x", message="y", status_right="z")
    )
    for cell in cells[0] + cells[-1]:
        assert cell.role is Role.TERRAIN
        assert cell.visibility is Visibility.VISIBLE


def test_status_row_message_left_status_right_flush_right_when_both_fit():
    level = all_wall_level(20, 2)
    cells = render_to_cells(
        level, (0, 0), EMPTY, EMPTY, EMPTY, Chrome(message="hi", status_right="Level 1  Seed 5")
    )
    line = "".join(c.char for c in cells[-1])
    assert len(line) == 20
    assert line == "hi" + " " * (20 - 2 - len("Level 1  Seed 5")) + "Level 1  Seed 5"


def test_status_row_truncates_message_when_it_would_collide_with_status_right():
    level = all_wall_level(15, 2)
    status_right = "RIGHT-STATUS"  # 12 chars
    long_message = "This message is much too long to fit"
    cells = render_to_cells(
        level, (0, 0), EMPTY, EMPTY, EMPTY, Chrome(message=long_message, status_right=status_right)
    )
    line = "".join(c.char for c in cells[-1])
    # width(15) - len(status_right)(12) - 1 == 2: the message is truncated to its first
    # 2 characters, one column is left as the separating space, then status_right.
    assert line == "Th RIGHT-STATUS"
    assert len(line) == 15
    # status_right always wins: it survives intact and flush right.
    assert line.endswith(status_right)


def test_status_right_alone_too_long_is_truncated_and_message_dropped():
    level = all_wall_level(10, 2)
    status_right = "Level 12  Seed 987654321"  # far longer than width=10
    cells = render_to_cells(
        level, (0, 0), EMPTY, EMPTY, EMPTY,
        Chrome(message="short msg", status_right=status_right),
    )
    line = "".join(c.char for c in cells[-1])
    assert len(line) == 10
    assert line == status_right[:10]
    assert "short msg" not in line


def test_empty_chrome_status_row_is_all_spaces():
    level = all_wall_level(9, 2)
    cells = render_to_cells(level, (0, 0), EMPTY, EMPTY, EMPTY, Chrome())
    line = "".join(c.char for c in cells[-1])
    assert line == " " * 9


# --------------------------------------------------------------- unexplored is blank


def test_fully_unexplored_level_is_all_blank():
    level = small_level()
    cells = render_to_cells(level, (99, 99), EMPTY, EMPTY, EMPTY, Chrome(message="s"))
    for row in cells[1:-1]:  # map rows only — chrome rows are always VISIBLE, not UNSEEN
        for cell in row:
            assert cell.char == " "
            assert cell.visibility is Visibility.UNSEEN
            assert cell.role is Role.TERRAIN


def test_fully_unexplored_to_lines_is_pure_spaces_and_leaks_no_shape():
    level = small_level()
    lines = render(level, (99, 99), status="s")
    for line in lines[1:-1]:  # map rows only
        assert line == " " * level.width
    joined = "".join(lines[1:-1])
    assert "#" not in joined
    assert "." not in joined
    assert "+" not in joined
    assert DOOR_OPEN_CHAR not in joined


def test_unexplored_wall_and_door_cells_are_also_blank():
    """Even cells that are DOOR/WALL tiles render blank when neither visible nor explored."""
    level = small_level()
    cells = render_to_cells(level, (99, 99), EMPTY, EMPTY, EMPTY, Chrome())
    # (3, 1) is the DOOR cell in SMALL_ROWS; (0, 0) is a WALL cell — map row y is at
    # cells[y + 1] (§0.8).
    door_cell = cells[1 + 1][3]
    wall_cell = cells[0 + 1][0]
    assert door_cell.char == " " and door_cell.role is Role.TERRAIN
    assert wall_cell.char == " " and wall_cell.role is Role.TERRAIN


# ---------------------------------------------------------------- visible vs explored


def test_visible_cell_shows_true_glyph_and_visible_state():
    level = small_level()
    cells = render_to_cells(level, (99, 99), frozenset({(1, 1)}), EMPTY, EMPTY, Chrome())
    cell = cells[1 + 1][1]
    assert cell.char == TILE_CHARS[Tile.FLOOR]
    assert cell.visibility is Visibility.VISIBLE
    assert cell.role is Role.TERRAIN


def test_explored_only_cell_shows_true_glyph_and_explored_state():
    level = small_level()
    cells = render_to_cells(level, (99, 99), EMPTY, frozenset({(1, 1)}), EMPTY, Chrome())
    cell = cells[1 + 1][1]
    assert cell.char == TILE_CHARS[Tile.FLOOR]
    assert cell.visibility is Visibility.EXPLORED
    assert cell.role is Role.TERRAIN


def test_cell_in_both_visible_and_explored_is_visible():
    level = small_level()
    coord = frozenset({(1, 1)})
    cells = render_to_cells(level, (99, 99), coord, coord, EMPTY, Chrome())
    cell = cells[1 + 1][1]
    assert cell.visibility is Visibility.VISIBLE


def test_neighbouring_cells_can_differ_in_visibility():
    level = small_level()
    visible = frozenset({(1, 1)})
    explored = frozenset({(2, 1)})
    cells = render_to_cells(level, (99, 99), visible, explored, EMPTY, Chrome())
    assert cells[1 + 1][1].visibility is Visibility.VISIBLE
    assert cells[1 + 1][2].visibility is Visibility.EXPLORED
    assert cells[1 + 1][3].visibility is Visibility.UNSEEN
    assert cells[1 + 1][3].char == " "


# --------------------------------------------------------------------------- doors


def test_closed_door_visible_renders_plus_with_door_role():
    level = small_level()
    door = (3, 1)
    cells = render_to_cells(level, (99, 99), frozenset({door}), EMPTY, EMPTY, Chrome())
    cell = cells[1 + 1][3]
    assert cell.char == "+"
    assert cell.role is Role.DOOR
    assert cell.visibility is Visibility.VISIBLE


def test_open_door_visible_renders_apostrophe_with_door_role():
    level = small_level()
    door = (3, 1)
    cells = render_to_cells(level, (99, 99), frozenset({door}), EMPTY, frozenset({door}), Chrome())
    cell = cells[1 + 1][3]
    assert cell.char == DOOR_OPEN_CHAR == "'"
    assert cell.role is Role.DOOR
    assert cell.visibility is Visibility.VISIBLE


def test_closed_door_explored_renders_plus_with_door_role():
    level = small_level()
    door = (3, 1)
    cells = render_to_cells(level, (99, 99), EMPTY, frozenset({door}), EMPTY, Chrome())
    cell = cells[1 + 1][3]
    assert cell.char == "+"
    assert cell.role is Role.DOOR
    assert cell.visibility is Visibility.EXPLORED


def test_open_door_explored_renders_apostrophe_with_door_role():
    level = small_level()
    door = (3, 1)
    cells = render_to_cells(level, (99, 99), EMPTY, frozenset({door}), frozenset({door}), Chrome())
    cell = cells[1 + 1][3]
    assert cell.char == DOOR_OPEN_CHAR
    assert cell.role is Role.DOOR
    assert cell.visibility is Visibility.EXPLORED


def test_open_doors_set_is_ignored_for_cells_that_are_not_doors():
    """A stray coordinate in open_doors that isn't a DOOR tile changes nothing."""
    level = small_level()
    floor = (1, 1)
    cells = render_to_cells(
        level, (99, 99), frozenset({floor}), EMPTY, frozenset({floor}), Chrome()
    )
    cell = cells[1 + 1][1]
    assert cell.char == TILE_CHARS[Tile.FLOOR]
    assert cell.role is Role.TERRAIN


# --------------------------------------------------------------------------- stairs


@pytest.mark.parametrize(
    "tile,expected_char", [(SU, TILE_CHARS[Tile.STAIRS_UP]), (SD, TILE_CHARS[Tile.STAIRS_DOWN])]
)
@pytest.mark.parametrize("vis_set_name", ["visible", "explored"])
def test_stair_tiles_render_their_glyph_at_visible_and_explored(tile, expected_char, vis_set_name):
    level = make_level([[tile]])
    coord = frozenset({(0, 0)})
    visible = coord if vis_set_name == "visible" else EMPTY
    explored = coord if vis_set_name == "explored" else EMPTY
    cells = render_to_cells(level, (99, 99), visible, explored, EMPTY, Chrome())
    cell = cells[1][0]  # map row 0 -> cells[1]
    assert cell.char == expected_char
    assert cell.role is Role.TERRAIN
    assert cell.visibility is (
        Visibility.VISIBLE if vis_set_name == "visible" else Visibility.EXPLORED
    )


def test_stairs_up_glyph_is_less_than_sign():
    level = make_level([[SU]])
    cells = render_to_cells(level, (99, 99), frozenset({(0, 0)}), EMPTY, EMPTY, Chrome())
    assert cells[1][0].char == "<"


def test_stairs_down_glyph_is_greater_than_sign():
    level = make_level([[SD]])
    cells = render_to_cells(level, (99, 99), frozenset({(0, 0)}), EMPTY, EMPTY, Chrome())
    assert cells[1][0].char == ">"


# --------------------------------------------------------------------------- roles


def test_wall_and_floor_are_terrain_role():
    level = make_level([[W, F]])
    cells = render_to_cells(level, (99, 99), frozenset({(0, 0), (1, 0)}), EMPTY, EMPTY, Chrome())
    assert cells[1][0].role is Role.TERRAIN
    assert cells[1][1].role is Role.TERRAIN


def test_door_is_door_role():
    level = make_level([[D]])
    cells = render_to_cells(level, (99, 99), frozenset({(0, 0)}), EMPTY, EMPTY, Chrome())
    assert cells[1][0].role is Role.DOOR


def test_player_is_player_role():
    level = make_level([[F]])
    cells = render_to_cells(level, (0, 0), EMPTY, EMPTY, EMPTY, Chrome())
    assert cells[1][0].role is Role.PLAYER


# ------------------------------------------------------------------------- the player


def test_row_offset_is_pinned_by_non_square_level_and_x_ne_y():
    """The one dangerous change (CONTRACT-v3 §0.8): map cell (x, y) is at cells[y+1][x].

    A non-square level with a player at x != y is required so neither a transposition
    nor a plain off-by-one can pass: cells[y+1][x] must hold the player, and cells[y][x]
    — the naive, wrong location — must not.
    """
    level = all_wall_level(7, 3)  # non-square: width != height
    px, py = 5, 1  # x != y
    cells = render_to_cells(level, (px, py), all_coords(level), EMPTY, EMPTY, Chrome())
    assert len(cells) == level.height + 2 == 5
    correct = cells[py + 1][px]
    assert correct.char == PLAYER_CHAR == "@"
    assert correct.role is Role.PLAYER
    assert correct.visibility is Visibility.VISIBLE
    # the naive (un-offset) location must NOT hold the player.
    assert cells[py][px].char != PLAYER_CHAR
    assert cells[py][px].role is not Role.PLAYER


def test_player_glyph_lands_at_row_y_plus_one_col_x():
    """x != y so the axes cannot be silently swapped, on top of the row offset."""
    level = all_wall_level(7, 5)
    px, py = 5, 2
    cells = render_to_cells(level, (px, py), all_coords(level), EMPTY, EMPTY, Chrome())
    assert cells[py + 1][px].char == PLAYER_CHAR == "@"
    assert cells[py + 1][px].role is Role.PLAYER
    assert cells[py + 1][px].visibility is Visibility.VISIBLE
    # the transposed cell (both indices are in range) must NOT hold the player
    assert cells[px][py].char != PLAYER_CHAR


def test_exactly_one_player_cell_in_map_region_when_in_bounds():
    level = make_level([[F] * 6 for _ in range(4)])
    cells = render_to_cells(
        level, (4, 1), all_coords(level), EMPTY, EMPTY, Chrome(message="no at sign")
    )
    player_cells = [
        cell for row in cells[1:-1] for cell in row if cell.role is Role.PLAYER
    ]
    assert len(player_cells) == 1


def test_player_renders_over_floor():
    rows = [[F, F, F], [F, F, F]]
    level = make_level(rows)
    lines = render(level, (1, 1), visible=all_coords(level))
    assert lines[1:3] == ["...", ".@."]


def test_player_renders_over_wall():
    rows = [[W, W, W], [W, W, W]]
    level = make_level(rows)
    lines = render(level, (2, 0), visible=all_coords(level))
    assert lines[1:3] == ["##@", "###"]


def test_player_renders_over_door():
    rows = [[D, D], [D, D]]
    level = make_level(rows)
    lines = render(level, (0, 1), visible=all_coords(level))
    assert lines[1:3] == ["++", "@+"]


def test_player_renders_over_unexplored_wall_and_overrides_blank():
    """Player is drawn even when their own cell is neither visible nor explored."""
    level = all_wall_level(3, 2)
    cells = render_to_cells(level, (1, 0), EMPTY, EMPTY, EMPTY, Chrome())
    cell = cells[0 + 1][1]
    assert cell.char == PLAYER_CHAR
    assert cell.role is Role.PLAYER
    assert cell.visibility is Visibility.VISIBLE
    # every other map cell is still blank
    other_cells = [c for row in cells[1:-1] for c in row if c is not cell]
    assert all(c.char == " " for c in other_cells)


@pytest.mark.parametrize("pos", [(-1, 0), (0, -1), (5, 0), (0, 3), (-1, -1), (99, 99)])
def test_out_of_bounds_player_is_not_drawn_and_does_not_raise(pos):
    level = small_level()          # 5 wide, 3 high
    cells = render_to_cells(level, pos, all_coords(level), EMPTY, EMPTY, Chrome(message="status"))
    flat = [c for row in cells[1:-1] for c in row]
    assert all(c.role is not Role.PLAYER for c in flat)
    assert PLAYER_CHAR not in "".join(c.char for c in flat)


@pytest.mark.parametrize("pos", [(-1, 0), (0, -1), (5, 0), (0, 3)])
def test_out_of_bounds_player_matches_boundary_values_from_brief(pos):
    level = small_level()          # width=5, height=3
    cells = render_to_cells(level, pos, EMPTY, EMPTY, EMPTY, Chrome())
    flat = [c for row in cells[1:-1] for c in row]
    assert all(c.role is not Role.PLAYER for c in flat)


def test_in_bounds_corners_are_drawable():
    level = all_wall_level(4, 3)
    for pos in [(0, 0), (3, 0), (0, 2), (3, 2)]:
        cells = render_to_cells(level, pos, EMPTY, EMPTY, EMPTY, Chrome())
        assert cells[pos[1] + 1][pos[0]].char == PLAYER_CHAR
        flat = [c for row in cells[1:-1] for c in row]
        assert sum(1 for c in flat if c.char == PLAYER_CHAR) == 1


# ------------------------------------------------------------------------------ purity


def test_repeated_calls_are_equal():
    level = small_level()
    visible = frozenset({(1, 1), (2, 1)})
    explored = frozenset({(3, 1)})
    chrome = Chrome(message="status")
    first = render_to_cells(level, (2, 1), visible, explored, EMPTY, chrome)
    second = render_to_cells(level, (2, 1), visible, explored, EMPTY, chrome)
    assert first == second


def test_level_is_unchanged_by_rendering():
    level = make_level(SMALL_ROWS, player_start=(1, 1), rooms=(Room(1, 1, 2, 1),), seed=42)
    before = copy.deepcopy(level)
    render_to_cells(level, (1, 1), all_coords(level), EMPTY, EMPTY, Chrome(message="anything"))
    render_to_cells(
        level, (99, 99), EMPTY, all_coords(level), EMPTY, Chrome(message="anything else")
    )
    assert level == before
    assert level.grid == before.grid


def test_input_sets_and_tuple_are_not_mutated():
    level = small_level()
    pos = (1, 1)
    visible = frozenset({(1, 1)})
    explored = frozenset({(2, 1)})
    open_doors = frozenset({(3, 1)})
    chrome = Chrome(stats="s1", message="s2", status_right="s3")
    render_to_cells(level, pos, visible, explored, open_doors, chrome)
    assert pos == (1, 1)
    assert visible == frozenset({(1, 1)})
    assert explored == frozenset({(2, 1)})
    assert open_doors == frozenset({(3, 1)})
    assert chrome == Chrome(stats="s1", message="s2", status_right="s3")


def test_returns_a_fresh_grid_each_call():
    level = small_level()
    a = render_to_cells(level, (1, 1), EMPTY, EMPTY, EMPTY, Chrome(message="s"))
    b = render_to_cells(level, (1, 1), EMPTY, EMPTY, EMPTY, Chrome(message="s"))
    assert a is not b
    a[0][0] = Cell("X", Role.TERRAIN, Visibility.VISIBLE)
    assert render_to_cells(level, (1, 1), EMPTY, EMPTY, EMPTY, Chrome(message="s"))[0][0].char == " "


# ------------------------------------------------------------------- exact frame text


def test_hand_built_level_matches_literal_strings_fully_visible():
    """Pins grid[y][x] -> line mapping, including both chrome rows. Fails loudly if rows
    or columns are transposed, or if the +1 row offset (§0.8) is missing."""
    level = small_level()
    chrome = Chrome(stats="HP 9", message="ok", status_right="Lv1")
    lines = to_lines(
        render_to_cells(level, (2, 1), all_coords(level), EMPTY, EMPTY, chrome)
    )
    # width is 5: "ok" + 1 + "Lv1" is 6 chars, one too many, so message truncates to
    # its first character ("o") to leave room for the mandatory separator and the
    # (always-wins) status_right.
    assert lines == [
        "HP 9 ",   # stats row (row 0)
        "#####",   # map row y=0 -> cells[1]
        "#.@+#",   # map row y=1 -> cells[2]
        "#####",   # map row y=2 -> cells[3]
        "o Lv1",   # status row
    ]


def test_non_square_level_rows_are_rows():
    """7 wide x 3 high: a transposition bug cannot survive this."""
    rows = [
        [F, W, W, W, W, W, W],
        [W, W, F, W, W, W, W],
        [W, W, W, W, W, W, F],
    ]
    level = make_level(rows)
    lines = render(level, (99, 99), visible=all_coords(level), status="")
    assert len(lines) == 5
    assert all(len(line) == 7 for line in lines)
    assert lines[0] == " " * 7           # stats row
    assert lines[1:4] == [
        ".######",
        "##.####",
        "######.",
    ]
    assert lines[4] == " " * 7           # status row


def test_tall_narrow_level_rows_are_rows():
    rows = [
        [F, W, W],
        [W, W, W],
        [W, W, W],
        [W, W, F],
        [W, W, W],
    ]
    level = make_level(rows)
    lines = render(level, (99, 99), visible=all_coords(level), status="")
    assert lines == ["   ", ".##", "###", "###", "##.", "###", "   "]


def test_wall_floor_door_glyphs_come_from_tiles():
    assert TILE_CHARS[Tile.WALL] == "#"
    assert TILE_CHARS[Tile.FLOOR] == "."
    assert TILE_CHARS[Tile.DOOR] == "+"
    rows = [[W, F, D]]
    level = make_level(rows)
    lines = render(level, (99, 99), visible=all_coords(level), status="")
    assert lines[1] == "#.+"


def test_each_cell_uses_tile_chars_for_its_tile_when_visible():
    rows = [
        [W, F, D, F],
        [D, D, W, F],
    ]
    level = make_level(rows)
    lines = render(level, (99, 99), visible=all_coords(level), status="")
    for y, row in enumerate(rows):
        for x, tile in enumerate(row):
            assert lines[y + 1][x] == TILE_CHARS[tile]


# ------------------------------------------------------------------- degenerate levels


def test_all_wall_level_with_no_rooms_does_not_raise():
    level = Level(6, 4, freeze_grid([[W] * 6 for _ in range(4)]), (), (0, 0), 1)
    assert level.rooms == ()
    lines = render(level, (99, 99), status="")
    assert lines == ["      ", "      ", "      ", "      ", "      ", "      "]


def test_all_wall_level_no_rooms_fully_visible():
    level = Level(6, 4, freeze_grid([[W] * 6 for _ in range(4)]), (), (0, 0), 1)
    lines = render(level, (99, 99), visible=all_coords(level), status="")
    assert lines == ["      ", "######", "######", "######", "######", "      "]


def test_one_by_one_level():
    level = make_level([[F]])
    assert render(level, (0, 0), visible=frozenset({(0, 0)}), status="ignored") == [" ", "@", " "]
    assert render(level, (1, 0), visible=frozenset({(0, 0)}), status="") == [" ", ".", " "]


# ------------------------------------------------------ source-level contract assertions


def _render_source() -> str:
    return RENDER_SOURCE.read_text(encoding="utf-8")


@pytest.mark.parametrize("glyph", ["#", ".", "+", "'", "<", ">", "@"])
def test_render_source_contains_no_glyph_literals(glyph):
    src = _render_source()
    assert f'"{glyph}"' not in src
    assert f"'{glyph}'" not in src


@pytest.mark.parametrize("color_number", [250, 238, 180, 94, 231])
def test_render_source_contains_no_bare_palette_colour_literals(color_number):
    """None of the binding palette's 256-colour indices (style.py §15.1) may be
    re-spelled here; they must only ever be reached via style.attr_for."""
    src = _render_source()
    assert str(color_number) not in src


def _imported_modules(src: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                modules.add(node.module)
    return modules


def test_render_imports_only_permitted_modules():
    modules = _imported_modules(_render_source())
    assert modules == {
        "__future__",
        "curses",
        "dataclasses",
        "roguelike.level",
        "roguelike.style",
        "roguelike.tiles",
    }


@pytest.mark.parametrize(
    "forbidden",
    [
        "roguelike.movement",
        "roguelike.keys",
        "roguelike.game",
        "roguelike.generator",
        "roguelike.fov",
        "roguelike.events",
    ],
)
def test_render_does_not_import_sibling_modules(forbidden):
    assert forbidden not in _imported_modules(_render_source())
    assert forbidden not in _render_source()


def test_render_never_calls_terminal_mutating_curses_functions_at_import_scope():
    """initscr/wrapper/newwin (and friends) must never appear anywhere in the module —
    init_colors/draw may only call curses functions that a caller-initialised terminal
    supports, never the ones that initialise curses itself."""
    banned = {"initscr", "wrapper", "newwin", "setupterm", "filter", "newterm"}
    src = _render_source()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            value = node.func.value
            if isinstance(value, ast.Name) and value.id == "curses":
                assert node.func.attr not in banned, f"curses.{node.func.attr} called"


def _function_body_calls_curses(src: str, func_name: str) -> set[str]:
    """Return the set of curses.<attr> attribute names called inside function func_name."""
    tree = ast.parse(src)
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                    value = inner.func.value
                    if isinstance(value, ast.Name) and value.id == "curses":
                        calls.add(inner.func.attr)
    return calls


def test_draw_never_allocates_colour_pairs():
    """Pair allocation is init_colors's job, once, never draw's, never per frame."""
    src = _render_source()
    draw_calls = _function_body_calls_curses(src, "draw")
    assert "init_pair" not in draw_calls
    assert "start_color" not in draw_calls
    assert "use_default_colors" not in draw_calls
    assert "color_pair" not in draw_calls


def test_init_colors_is_where_pair_allocation_happens():
    src = _render_source()
    init_calls = _function_body_calls_curses(src, "init_colors")
    assert "init_pair" in init_calls


def test_module_has_no_import_time_side_effects():
    """Re-importing in a fresh interpreter must not touch a terminal."""
    import importlib
    import roguelike.render as module

    importlib.reload(module)
    assert callable(module.render_to_cells)
    assert callable(module.to_lines)
    assert callable(module.init_colors)
    assert callable(module.draw)


def test_public_surface():
    import roguelike.render as module

    assert module.__all__ == [
        "Cell",
        "Chrome",
        "render_to_cells",
        "to_lines",
        "init_colors",
        "draw",
    ]


def test_chrome_is_a_frozen_dataclass_with_default_empty_fields():
    import dataclasses

    assert dataclasses.is_dataclass(Chrome)
    chrome = Chrome()
    assert chrome.stats == ""
    assert chrome.message == ""
    assert chrome.status_right == ""
    with pytest.raises(dataclasses.FrozenInstanceError):
        chrome.stats = "x"  # type: ignore[misc]


def test_cell_is_a_frozen_dataclass():
    import dataclasses

    assert dataclasses.is_dataclass(Cell)
    cell = Cell("@", Role.PLAYER, Visibility.VISIBLE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        cell.char = "#"  # type: ignore[misc]


# ------------------------------------------------------------------ draw (fake window)


class FakeWindow:
    """A minimal stand-in for a curses window. Records calls; never touches a terminal.

    Mimics the real thing where it matters: writing the bottom-right cell raises
    ``curses.error``, as does any write outside the window.
    """

    def __init__(self, max_y: int, max_x: int) -> None:
        self._max_y = max_y
        self._max_x = max_x
        self.calls: list[tuple[int, int, str, int]] = []
        self.erased = 0
        self.refreshed = 0

    def getmaxyx(self) -> tuple[int, int]:
        return (self._max_y, self._max_x)

    def erase(self) -> None:
        self.erased += 1

    def refresh(self) -> None:
        self.refreshed += 1

    def addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        if not (0 <= y < self._max_y) or not (0 <= x < self._max_x):
            raise curses.error("out of window")
        if x + len(text) > self._max_x:
            raise curses.error("string too long")
        if y == self._max_y - 1 and x + len(text) >= self._max_x:
            raise curses.error("cannot write bottom-right cell")
        self.calls.append((y, x, text, attr))


def _one_row_cells(width: int, char: str = "#") -> list[list[Cell]]:
    return [[Cell(char, Role.TERRAIN, Visibility.VISIBLE) for _ in range(width)]]


def test_draw_returns_none_and_erases_then_refreshes():
    level = small_level()
    cells = render_to_cells(level, (1, 1), all_coords(level), EMPTY, EMPTY, Chrome(message="status"))
    win = FakeWindow(24, 80)
    assert draw(win, cells) is None
    assert win.erased == 1
    assert win.refreshed == 1


def test_draw_blits_cells_row_by_row_matching_to_lines():
    level = small_level()
    cells = render_to_cells(level, (2, 1), all_coords(level), EMPTY, EMPTY, Chrome(message="ok"))
    win = FakeWindow(24, 80)
    draw(win, cells)
    expected_lines = to_lines(cells)
    blitted = ["".join(text for (yy, _, text, _) in win.calls if yy == y) for y in range(len(cells))]
    assert blitted == expected_lines


def test_draw_clips_rows_to_window_height():
    level = all_wall_level(4, 10)      # 12 rows of frame (stats + 10 map + status)
    cells = render_to_cells(level, (0, 0), all_coords(level), EMPTY, EMPTY, Chrome(message="s"))
    win = FakeWindow(3, 40)
    draw(win, cells)
    assert set(y for y, _, _, _ in win.calls) == {0, 1, 2}


def test_draw_truncates_columns_to_window_width():
    level = all_wall_level(20, 2)
    cells = render_to_cells(level, (0, 0), all_coords(level), EMPTY, EMPTY, Chrome(message="s"))
    win = FakeWindow(10, 6)
    draw(win, cells)
    assert all(x < 6 for _, x, _, _ in win.calls)


def test_draw_never_writes_the_bottom_right_cell():
    level = all_wall_level(8, 3)       # 5 frame rows (stats + 3 map + status)
    cells = render_to_cells(level, (0, 0), all_coords(level), EMPTY, EMPTY, Chrome(message="status!!"))
    win = FakeWindow(5, 8)             # window exactly as tall/wide as the frame
    draw(win, cells)
    assert (4, 7) not in [(y, x) for y, x, _, _ in win.calls]


def test_draw_survives_a_one_by_one_window():
    level = all_wall_level(5, 5)
    cells = render_to_cells(level, (0, 0), all_coords(level), EMPTY, EMPTY, Chrome(message="s"))
    win = FakeWindow(1, 1)
    draw(win, cells)
    assert win.refreshed == 1


def test_draw_survives_a_zero_sized_window():
    level = all_wall_level(5, 5)
    cells = render_to_cells(level, (0, 0), all_coords(level), EMPTY, EMPTY, Chrome(message="s"))
    win = FakeWindow(0, 0)
    draw(win, cells)
    assert win.calls == []
    assert win.refreshed == 1


def test_draw_swallows_curses_errors_from_addstr():
    class ExplodingWindow(FakeWindow):
        def addstr(self, y, x, text, attr=0):
            raise curses.error("nope")

    level = small_level()
    cells = render_to_cells(level, (1, 1), all_coords(level), EMPTY, EMPTY, Chrome(message="s"))
    win = ExplodingWindow(24, 80)
    draw(win, cells)      # must not raise
    assert win.refreshed == 1


def test_draw_survives_erase_and_getmaxyx_and_refresh_raising():
    class AllExplodingWindow(FakeWindow):
        def erase(self):
            raise curses.error("nope")

        def getmaxyx(self):
            raise curses.error("nope")

        def refresh(self):
            raise curses.error("nope")

    win = AllExplodingWindow(24, 80)
    cells = _one_row_cells(3)
    draw(win, cells)  # must not raise despite every call failing


def test_draw_unseen_cell_writes_a_space():
    level = small_level()
    cells = render_to_cells(level, (99, 99), EMPTY, EMPTY, EMPTY, Chrome(message="s"))
    win = FakeWindow(24, 80)
    draw(win, cells)
    # map rows occupy y in [1, level.height] (the stats row is y=0, per §0.8).
    map_calls = [c for c in win.calls if 1 <= c[0] <= level.height]
    assert all(text == " " for _, _, text, _ in map_calls)


def test_draw_does_not_mutate_the_level():
    level = make_level(SMALL_ROWS, player_start=(1, 1), rooms=(Room(1, 1, 2, 1),), seed=3)
    before = copy.deepcopy(level)
    cells = render_to_cells(level, (2, 1), all_coords(level), EMPTY, EMPTY, Chrome(message="s"))
    draw(FakeWindow(24, 80), cells)
    assert level == before


def test_draw_tolerates_being_called_before_init_colors():
    import roguelike.render as render_module

    saved = dict(render_module._CELL_ATTRS)
    render_module._CELL_ATTRS = {}
    try:
        cells = _one_row_cells(3)
        win = FakeWindow(24, 80)
        draw(win, cells)
        assert win.calls == [(0, 0, "#", 0), (0, 1, "#", 0), (0, 2, "#", 0)]
    finally:
        render_module._CELL_ATTRS = saved


# --------------------------------------------------------------------- init_colors


def test_init_colors_does_not_raise_with_no_tty():
    init_colors()  # colors=None: detection itself must not raise either


def test_init_colors_does_not_raise_on_a_colourless_terminal():
    init_colors(colors=0)


def test_init_colors_then_draw_still_produces_output():
    init_colors(colors=0)
    level = small_level()
    cells = render_to_cells(level, (99, 99), frozenset({(1, 1)}), EMPTY, EMPTY, Chrome(message="s"))
    win = FakeWindow(24, 80)
    draw(win, cells)
    assert len(win.calls) > 0
    assert win.refreshed == 1


def test_init_colors_populates_module_level_attr_table_for_every_combo():
    import roguelike.render as render_module

    init_colors(colors=256)
    expected_keys = {
        (Role.TERRAIN, Visibility.VISIBLE),
        (Role.TERRAIN, Visibility.EXPLORED),
        (Role.DOOR, Visibility.VISIBLE),
        (Role.DOOR, Visibility.EXPLORED),
        (Role.PLAYER, Visibility.VISIBLE),
    }
    assert set(render_module._CELL_ATTRS.keys()) == expected_keys


def test_init_colors_can_be_called_multiple_times():
    init_colors(colors=256)
    init_colors(colors=8)
    init_colors(colors=0)  # must not raise or accumulate state incorrectly
