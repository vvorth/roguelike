"""Tests for roguelike.style (CONTRACT-v2 §15).

No curses initialisation anywhere in this file; the whole suite must pass with no
TTY attached and stdin redirected from /dev/null.
"""

from __future__ import annotations

import ast
import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

from roguelike.style import Attr, Role, Visibility, attr_for, role_for
from roguelike.tiles import Tile

# ---------------------------------------------------------------------------
# Enum membership
# ---------------------------------------------------------------------------


def test_visibility_has_exactly_three_members():
    names = {m.name for m in Visibility}
    assert names == {"UNSEEN", "EXPLORED", "VISIBLE"}


def test_role_has_exactly_three_members():
    names = {m.name for m in Role}
    assert names == {"TERRAIN", "DOOR", "PLAYER"}


# ---------------------------------------------------------------------------
# role_for
# ---------------------------------------------------------------------------


def test_role_for_wall_and_floor_are_terrain():
    assert role_for(Tile.WALL) is Role.TERRAIN
    assert role_for(Tile.FLOOR) is Role.TERRAIN


def test_role_for_door_is_door():
    assert role_for(Tile.DOOR) is Role.DOOR


@pytest.mark.parametrize("tile", list(Tile))
def test_role_for_player_overrides_every_tile(tile):
    assert role_for(tile, is_player=True) is Role.PLAYER


# ---------------------------------------------------------------------------
# attr_for — binding palette at colors=256
# ---------------------------------------------------------------------------


def test_terrain_visible_256():
    assert attr_for(Role.TERRAIN, Visibility.VISIBLE).color == 250


def test_terrain_explored_256():
    assert attr_for(Role.TERRAIN, Visibility.EXPLORED).color == 238


def test_door_visible_256():
    assert attr_for(Role.DOOR, Visibility.VISIBLE).color == 180


def test_door_explored_256():
    assert attr_for(Role.DOOR, Visibility.EXPLORED).color == 94


def test_player_visible_256():
    assert attr_for(Role.PLAYER, Visibility.VISIBLE) == Attr(231, bold=True)


def test_only_player_is_bold():
    combos = [
        (Role.TERRAIN, Visibility.VISIBLE),
        (Role.TERRAIN, Visibility.EXPLORED),
        (Role.DOOR, Visibility.VISIBLE),
        (Role.DOOR, Visibility.EXPLORED),
    ]
    for role, vis in combos:
        assert attr_for(role, vis).bold is False
    assert attr_for(Role.PLAYER, Visibility.VISIBLE).bold is True


@pytest.mark.parametrize("role", [Role.TERRAIN, Role.DOOR])
def test_explored_is_darker_than_visible(role):
    # Both TERRAIN (grayscale ramp) and DOOR (brown/orange ramp) sit on xterm
    # 256-colour ramps where a numerically lower index is a darker shade of the
    # same hue, so EXPLORED must be a strictly lower index than VISIBLE.
    visible = attr_for(role, Visibility.VISIBLE).color
    explored = attr_for(role, Visibility.EXPLORED).color
    assert explored < visible


# ---------------------------------------------------------------------------
# Caller-bug combinations raise ValueError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [Role.TERRAIN, Role.DOOR, Role.PLAYER])
def test_unseen_raises_for_every_role(role):
    with pytest.raises(ValueError):
        attr_for(role, Visibility.UNSEEN)


def test_player_explored_raises():
    with pytest.raises(ValueError):
        attr_for(Role.PLAYER, Visibility.EXPLORED)


# ---------------------------------------------------------------------------
# Capability ladder
# ---------------------------------------------------------------------------


def test_colors_8_no_color_exceeds_7_and_nothing_raises():
    for role in Role:
        for visibility in (Visibility.VISIBLE,) if role is Role.PLAYER else (
            Visibility.VISIBLE,
            Visibility.EXPLORED,
        ):
            attr = attr_for(role, visibility, colors=8)
            assert attr.color <= 7


def test_colors_8_terrain_and_door_are_distinguishable():
    terrain = attr_for(Role.TERRAIN, Visibility.VISIBLE, colors=8)
    door = attr_for(Role.DOOR, Visibility.VISIBLE, colors=8)
    assert terrain.color != door.color


@pytest.mark.parametrize("colors", [2, 0])
def test_mono_terminals_use_default_color_everywhere(colors):
    for role in Role:
        for visibility in (Visibility.VISIBLE,) if role is Role.PLAYER else (
            Visibility.VISIBLE,
            Visibility.EXPLORED,
        ):
            attr = attr_for(role, visibility, colors=colors)
            assert attr.color == -1


@pytest.mark.parametrize("colors", [256, 88, 16, 8, 2, 0])
def test_full_sweep_never_raises_and_color_is_int(colors):
    for role in Role:
        for visibility in Visibility:
            if visibility is Visibility.UNSEEN:
                continue
            if role is Role.PLAYER and visibility is Visibility.EXPLORED:
                continue
            attr = attr_for(role, visibility, colors=colors)
            assert isinstance(attr.color, int)


# ---------------------------------------------------------------------------
# Attr: frozen, value equality, purity
# ---------------------------------------------------------------------------


def test_attr_is_frozen():
    attr = Attr(250)
    with pytest.raises(dataclasses.FrozenInstanceError):
        attr.color = 100  # type: ignore[misc]


def test_attr_equality_by_value():
    assert Attr(250) == Attr(250)
    assert Attr(250).bold is False


def test_attr_for_is_pure():
    first = attr_for(Role.DOOR, Visibility.VISIBLE, colors=256)
    second = attr_for(Role.DOOR, Visibility.VISIBLE, colors=256)
    assert first == second


# ---------------------------------------------------------------------------
# Import hygiene
# ---------------------------------------------------------------------------


def test_style_module_imports_only_tiles_and_stdlib():
    style_path = Path(__file__).resolve().parent.parent / "roguelike" / "style.py"
    tree = ast.parse(style_path.read_text(), filename=str(style_path))

    module_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                module_names.append(node.module)

    assert "curses" not in module_names
    project_imports = [name for name in module_names if name.split(".")[0] == "roguelike"]
    assert project_imports == ["roguelike.tiles"] or all(
        name == "roguelike.tiles" for name in project_imports
    )
    for name in project_imports:
        assert name == "roguelike.tiles"


def test_importing_style_in_fresh_subprocess_with_no_tty_succeeds():
    result = subprocess.run(
        [sys.executable, "-c", "import roguelike.style"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(Path(__file__).resolve().parent.parent),
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode()
