"""Unit tests for :mod:`roguelike.items` (CONTRACT-v5 §21, task T22).

A leaf module: no engine, no level, no terminal involved.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

from roguelike.items import DAGGER, SHORTBOW, Weapon, WeaponKind

# ---------------------------------------------------------------------------
# DAGGER / SHORTBOW — exact values
# ---------------------------------------------------------------------------


def test_dagger_values():
    assert DAGGER.name == "dagger"
    assert DAGGER.kind is WeaponKind.MELEE
    assert DAGGER.damage_min == 2
    assert DAGGER.damage_max == 5
    assert DAGGER.range == 1


def test_shortbow_values():
    assert SHORTBOW.name == "shortbow"
    assert SHORTBOW.kind is WeaponKind.RANGED
    assert SHORTBOW.damage_min == 1
    assert SHORTBOW.damage_max == 4
    assert SHORTBOW.range == 6


def test_dagger_equals_a_freshly_constructed_equivalent():
    assert DAGGER == Weapon("dagger", WeaponKind.MELEE, 2, 5, range=1)


def test_shortbow_equals_a_freshly_constructed_equivalent():
    assert SHORTBOW == Weapon("shortbow", WeaponKind.RANGED, 1, 4, range=6)


def test_weapon_range_defaults_to_1():
    w = Weapon("club", WeaponKind.MELEE, 1, 3)
    assert w.range == 1


# ---------------------------------------------------------------------------
# WeaponKind
# ---------------------------------------------------------------------------


def test_weapon_kind_has_exactly_melee_and_ranged():
    assert {member.name for member in WeaponKind} == {"MELEE", "RANGED"}


# ---------------------------------------------------------------------------
# Frozen dataclass
# ---------------------------------------------------------------------------


def test_weapon_is_frozen():
    w = Weapon("dagger", WeaponKind.MELEE, 2, 5, range=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        w.damage_max = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Import hygiene (CONTRACT-v5 §10/§21): items.py imports nothing from the project
# ---------------------------------------------------------------------------


def _module_source_and_tree() -> tuple[str, ast.Module]:
    path = pathlib.Path(__file__).resolve().parent.parent / "roguelike" / "items.py"
    source = path.read_text()
    return source, ast.parse(source)


def _module_imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                names.add(node.module)
    return names


def test_items_imports_nothing_from_the_project_and_no_curses():
    _, tree = _module_source_and_tree()
    imports = _module_imports(tree)
    assert not any(name == "roguelike" or name.startswith("roguelike.") for name in imports)
    assert "curses" not in imports


def test_items_does_not_import_curses_module_directly():
    source, _ = _module_source_and_tree()
    assert "import curses" not in source


def test_items_contains_no_float_literals():
    _, tree = _module_source_and_tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            pytest.fail(f"float literal found in items.py: {node.value!r}")
