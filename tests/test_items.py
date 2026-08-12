"""Unit tests for :mod:`roguelike.items` (CONTRACT-v6 §25, task T29).

A leaf module: no engine, no level, no terminal involved.

Two halves, mirroring the module: the binding tables of §25.1 asserted row by row, and the
four pure pack functions with their edge cases (a full pack, an occupied slot, an item that
is not carried, an index off the end).
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

from roguelike.items import (
    BANDAGE,
    BUCKLER,
    CARRY_LIMIT,
    CLUB,
    DAGGER,
    KITE_SHIELD,
    LONGBOW,
    POTION_OF_HEALING,
    SHORTBOW,
    SLING,
    SWORD,
    TOWER_SHIELD,
    Consumable,
    DamageType,
    Grade,
    Inventory,
    ItemKind,
    Resistance,
    Shield,
    Weapon,
    WeaponKind,
    add,
    drop,
    equip,
    unequip,
)

# ---------------------------------------------------------------------------
# THE REFERENCE NUMBERS. Retuning either of these retunes the whole game.
#
# One point of melee damage is worth 20-40 percentage points of floor-clear survival
# (CONTRACT-v6 §0.1): 1-4 clears 2.2% of floors, 2-5 clears 45.6%, 4-8 clears 98.3%.
# ---------------------------------------------------------------------------


def test_the_dagger_is_2_to_5_and_must_never_be_retuned():
    assert DAGGER.damage_min == 2
    assert DAGGER.damage_max == 5


def test_the_shortbow_is_1_to_4_and_must_never_be_retuned():
    assert SHORTBOW.damage_min == 1
    assert SHORTBOW.damage_max == 4


# ---------------------------------------------------------------------------
# §25.1 weapons — every row, literally
# ---------------------------------------------------------------------------


def test_club_row():
    assert CLUB.name == "club"
    assert CLUB.kind is WeaponKind.MELEE
    assert (CLUB.damage_min, CLUB.damage_max) == (2, 4)
    assert CLUB.damage_type is DamageType.BLUNT
    assert CLUB.grade is Grade.CRUDE
    assert CLUB.range == 1


def test_dagger_row():
    assert DAGGER.name == "dagger"
    assert DAGGER.kind is WeaponKind.MELEE
    assert (DAGGER.damage_min, DAGGER.damage_max) == (2, 5)
    assert DAGGER.damage_type is DamageType.PIERCE
    assert DAGGER.grade is Grade.STANDARD
    assert DAGGER.range == 1


def test_sword_row():
    assert SWORD.name == "sword"
    assert SWORD.kind is WeaponKind.MELEE
    assert (SWORD.damage_min, SWORD.damage_max) == (3, 5)
    assert SWORD.damage_type is DamageType.SLASH
    assert SWORD.grade is Grade.FINE
    assert SWORD.range == 1


def test_sling_row():
    assert SLING.name == "sling"
    assert SLING.kind is WeaponKind.RANGED
    assert (SLING.damage_min, SLING.damage_max) == (2, 4)
    assert SLING.damage_type is DamageType.BLUNT
    assert SLING.grade is Grade.CRUDE
    assert SLING.range == 5


def test_shortbow_row():
    assert SHORTBOW.name == "shortbow"
    assert SHORTBOW.kind is WeaponKind.RANGED
    assert (SHORTBOW.damage_min, SHORTBOW.damage_max) == (1, 4)
    assert SHORTBOW.damage_type is DamageType.PIERCE
    assert SHORTBOW.grade is Grade.STANDARD
    assert SHORTBOW.range == 6


def test_longbow_row():
    assert LONGBOW.name == "longbow"
    assert LONGBOW.kind is WeaponKind.RANGED
    assert (LONGBOW.damage_min, LONGBOW.damage_max) == (3, 5)
    assert LONGBOW.damage_type is DamageType.PIERCE
    assert LONGBOW.grade is Grade.FINE
    assert LONGBOW.range == 8


def test_every_melee_weapon_has_range_1():
    for weapon in (CLUB, DAGGER, SWORD):
        assert weapon.range == 1, weapon.name


def test_every_weapon_damage_band_is_integer_and_ordered():
    for weapon in (CLUB, DAGGER, SWORD, SLING, SHORTBOW, LONGBOW):
        assert isinstance(weapon.damage_min, int) and not isinstance(weapon.damage_min, bool)
        assert isinstance(weapon.damage_max, int) and not isinstance(weapon.damage_max, bool)
        assert weapon.damage_min <= weapon.damage_max, weapon.name


# ---------------------------------------------------------------------------
# §25.1 shields — every row, literally. Measured 10/18/25, not 15/25/35 (§0.4).
# ---------------------------------------------------------------------------


def test_buckler_row():
    assert BUCKLER.name == "buckler"
    assert BUCKLER.block_chance == 10
    assert BUCKLER.grade is Grade.CRUDE


def test_kite_shield_row():
    assert KITE_SHIELD.name == "kite shield"
    assert KITE_SHIELD.block_chance == 18
    assert KITE_SHIELD.grade is Grade.STANDARD


def test_tower_shield_row():
    assert TOWER_SHIELD.name == "tower shield"
    assert TOWER_SHIELD.block_chance == 25
    assert TOWER_SHIELD.grade is Grade.FINE


def test_shield_block_chance_rises_with_grade():
    assert BUCKLER.block_chance < KITE_SHIELD.block_chance < TOWER_SHIELD.block_chance


def test_no_shield_grants_flat_damage_reduction():
    # §0.2: a shield is a chance, never a subtraction. There is no field for one.
    field_names = {f.name for f in dataclasses.fields(Shield)}
    assert field_names == {"name", "block_chance", "grade"}


# ---------------------------------------------------------------------------
# §25.1 consumables — both rows
# ---------------------------------------------------------------------------


def test_potion_of_healing_row():
    assert POTION_OF_HEALING.name == "potion of healing"
    assert POTION_OF_HEALING.heal == 10
    assert POTION_OF_HEALING.regen_turns == 0
    assert POTION_OF_HEALING.regen_magnitude == 0


def test_bandage_row():
    assert BANDAGE.name == "bandage"
    assert BANDAGE.heal == 0
    assert BANDAGE.regen_turns == 5
    assert BANDAGE.regen_magnitude == 3


def test_consumable_effect_fields_default_to_zero():
    nothing = Consumable("nothing")
    assert (nothing.heal, nothing.regen_turns, nothing.regen_magnitude) == (0, 0, 0)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


def test_item_kind_has_exactly_three_members():
    assert {member.name for member in ItemKind} == {"WEAPON", "SHIELD", "CONSUMABLE"}


def test_weapon_kind_has_exactly_melee_and_ranged():
    assert {member.name for member in WeaponKind} == {"MELEE", "RANGED"}


def test_damage_type_has_exactly_three_members():
    assert {member.name for member in DamageType} == {"SLASH", "PIERCE", "BLUNT"}


def test_resistance_has_exactly_four_members_with_contract_values():
    assert {member.name: int(member) for member in Resistance} == {
        "IMMUNE": 0,
        "RESISTANT": 1,
        "NORMAL": 2,
        "VULNERABLE": 3,
    }


def test_resistance_orders_immune_to_vulnerable():
    assert Resistance.IMMUNE < Resistance.RESISTANT < Resistance.NORMAL < Resistance.VULNERABLE


def test_grade_has_exactly_three_members_with_contract_values():
    assert {member.name: int(member) for member in Grade} == {
        "CRUDE": 0,
        "STANDARD": 1,
        "FINE": 2,
    }


def test_grade_orders_crude_to_fine():
    assert Grade.CRUDE < Grade.STANDARD < Grade.FINE


def test_item_kind_and_damage_type_are_not_ordered():
    # Plain Enums: no order, unlike Resistance and Grade.
    with pytest.raises(TypeError):
        ItemKind.WEAPON < ItemKind.SHIELD  # noqa: B015
    with pytest.raises(TypeError):
        DamageType.SLASH < DamageType.BLUNT  # noqa: B015


# ---------------------------------------------------------------------------
# The records: shape, defaults and the v5 call shape
# ---------------------------------------------------------------------------


def test_weapon_fields_are_the_v5_five_then_the_two_new_ones_appended():
    assert [f.name for f in dataclasses.fields(Weapon)] == [
        "name",
        "kind",
        "damage_min",
        "damage_max",
        "range",
        "damage_type",
        "grade",
    ]


def test_the_v5_weapon_call_shape_still_works():
    w = Weapon("x", WeaponKind.MELEE, 2, 5)
    assert w.range == 1
    assert w.damage_type is DamageType.SLASH
    assert w.grade is Grade.STANDARD


def test_dagger_equals_a_freshly_constructed_equivalent():
    assert DAGGER == Weapon(
        "dagger", WeaponKind.MELEE, 2, 5, range=1,
        damage_type=DamageType.PIERCE, grade=Grade.STANDARD,
    )


def test_shortbow_equals_a_freshly_constructed_equivalent():
    assert SHORTBOW == Weapon(
        "shortbow", WeaponKind.RANGED, 1, 4, range=6,
        damage_type=DamageType.PIERCE, grade=Grade.STANDARD,
    )


def test_weapon_range_defaults_to_1():
    assert Weapon("club", WeaponKind.MELEE, 1, 3).range == 1


def test_shield_grade_defaults_to_standard():
    assert Shield("plank", 5).grade is Grade.STANDARD


@pytest.mark.parametrize(
    "item",
    [
        Weapon("dagger", WeaponKind.MELEE, 2, 5),
        Shield("buckler", 10),
        Consumable("potion of healing", heal=10),
        Inventory(),
    ],
    ids=["weapon", "shield", "consumable", "inventory"],
)
def test_every_dataclass_is_frozen(item):
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.name = "tampered"  # type: ignore[misc]


def test_equal_items_are_equal_by_value():
    assert Weapon("dagger", WeaponKind.MELEE, 2, 5) == Weapon("dagger", WeaponKind.MELEE, 2, 5)
    assert Shield("buckler", 10, grade=Grade.CRUDE) == BUCKLER
    assert Consumable("bandage", regen_turns=5, regen_magnitude=3) == BANDAGE


# ---------------------------------------------------------------------------
# Inventory — the empty case and the cap
# ---------------------------------------------------------------------------


def test_carry_limit_is_20():
    assert CARRY_LIMIT == 20


def test_empty_inventory_carries_nothing_and_has_three_empty_slots():
    inv = Inventory()
    assert inv.carried == ()
    assert inv.melee is None
    assert inv.ranged is None
    assert inv.shield is None


def test_inventory_fields_are_exactly_carried_and_the_three_slots():
    assert [f.name for f in dataclasses.fields(Inventory)] == [
        "carried",
        "melee",
        "ranged",
        "shield",
    ]


def _full_pack() -> Inventory:
    return Inventory(carried=tuple(Consumable(f"trinket {n}") for n in range(CARRY_LIMIT)))


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


def test_add_appends_and_reports_true():
    inv, ok = add(Inventory(), DAGGER)
    assert ok is True
    assert inv.carried == (DAGGER,)


def test_add_appends_at_the_end_preserving_order():
    inv, _ = add(Inventory(carried=(DAGGER, CLUB)), SWORD)
    assert inv.carried == (DAGGER, CLUB, SWORD)


def test_add_fills_the_pack_up_to_carry_limit():
    inv = Inventory()
    for _ in range(CARRY_LIMIT):
        inv, ok = add(inv, BANDAGE)
        assert ok is True
    assert len(inv.carried) == CARRY_LIMIT


def test_add_to_a_full_pack_reports_false_and_changes_nothing():
    full = _full_pack()
    before = dataclasses.replace(full)
    result, ok = add(full, DAGGER)
    assert ok is False
    assert result == before
    assert full == before


def test_add_leaves_its_input_untouched():
    inv = Inventory(carried=(DAGGER,))
    before = dataclasses.replace(inv)
    add(inv, CLUB)
    assert inv == before


def test_add_does_not_touch_the_slots():
    inv = Inventory(carried=(), melee=DAGGER, ranged=SHORTBOW, shield=BUCKLER)
    result, _ = add(inv, CLUB)
    assert (result.melee, result.ranged, result.shield) == (DAGGER, SHORTBOW, BUCKLER)


# ---------------------------------------------------------------------------
# drop
# ---------------------------------------------------------------------------


def test_drop_removes_by_index_and_returns_the_item():
    inv, item = drop(Inventory(carried=(DAGGER, CLUB, SWORD)), 1)
    assert item is CLUB
    assert inv.carried == (DAGGER, SWORD)


def test_drop_of_the_only_item_empties_the_pack():
    inv, item = drop(Inventory(carried=(BANDAGE,)), 0)
    assert item is BANDAGE
    assert inv.carried == ()


@pytest.mark.parametrize("index", [2, 3, 99, -1, -5])
def test_drop_with_an_out_of_range_index_returns_none_and_changes_nothing(index):
    inv = Inventory(carried=(DAGGER, CLUB))
    before = dataclasses.replace(inv)
    result, item = drop(inv, index)
    assert item is None
    assert result == before
    assert inv == before


def test_drop_from_an_empty_pack_returns_none():
    inv = Inventory()
    result, item = drop(inv, 0)
    assert item is None
    assert result == inv


def test_drop_leaves_its_input_untouched():
    inv = Inventory(carried=(DAGGER, CLUB))
    before = dataclasses.replace(inv)
    drop(inv, 0)
    assert inv == before


def test_drop_does_not_touch_the_slots():
    inv = Inventory(carried=(CLUB,), melee=DAGGER, ranged=SHORTBOW, shield=BUCKLER)
    result, _ = drop(inv, 0)
    assert (result.melee, result.ranged, result.shield) == (DAGGER, SHORTBOW, BUCKLER)


def test_drop_removes_exactly_one_of_two_identical_items():
    inv, item = drop(Inventory(carried=(BANDAGE, BANDAGE)), 0)
    assert item is BANDAGE
    assert inv.carried == (BANDAGE,)


# ---------------------------------------------------------------------------
# equip
# ---------------------------------------------------------------------------


def test_equip_a_melee_weapon_fills_the_melee_slot():
    inv = equip(Inventory(carried=(DAGGER,)), DAGGER)
    assert inv.melee is DAGGER
    assert inv.ranged is None
    assert inv.carried == ()


def test_equip_a_ranged_weapon_fills_the_ranged_slot():
    inv = equip(Inventory(carried=(SHORTBOW,)), SHORTBOW)
    assert inv.ranged is SHORTBOW
    assert inv.melee is None
    assert inv.carried == ()


def test_equip_a_shield_fills_the_shield_slot():
    inv = equip(Inventory(carried=(BUCKLER,)), BUCKLER)
    assert inv.shield is BUCKLER
    assert inv.carried == ()


def test_equip_returns_the_displaced_item_to_carried():
    inv = equip(Inventory(carried=(SWORD,), melee=DAGGER), SWORD)
    assert inv.melee is SWORD
    assert inv.carried == (DAGGER,)


def test_equip_a_shield_returns_the_displaced_shield_to_carried():
    inv = equip(Inventory(carried=(TOWER_SHIELD,), shield=BUCKLER), TOWER_SHIELD)
    assert inv.shield is TOWER_SHIELD
    assert inv.carried == (BUCKLER,)


def test_equip_never_changes_the_pack_size():
    inv = Inventory(carried=(SWORD, CLUB, BANDAGE), melee=DAGGER)
    result = equip(inv, SWORD)
    assert len(result.carried) == len(inv.carried)


def test_equip_keeps_the_order_of_the_remaining_items():
    inv = equip(Inventory(carried=(BANDAGE, SWORD, CLUB)), SWORD)
    assert inv.carried == (BANDAGE, CLUB)


def test_equipping_a_melee_weapon_leaves_the_ranged_slot_alone():
    inv = equip(Inventory(carried=(CLUB,), melee=DAGGER, ranged=SHORTBOW), CLUB)
    assert inv.melee is CLUB
    assert inv.ranged is SHORTBOW
    assert inv.carried == (DAGGER,)


def test_equip_an_item_that_is_not_carried_raises_value_error():
    with pytest.raises(ValueError):
        equip(Inventory(carried=(CLUB,)), SWORD)


def test_equip_from_an_empty_pack_raises_value_error():
    with pytest.raises(ValueError):
        equip(Inventory(), DAGGER)


def test_equip_an_already_equipped_weapon_raises_because_it_is_not_carried():
    with pytest.raises(ValueError):
        equip(Inventory(carried=(), melee=DAGGER), DAGGER)


def test_equip_a_consumable_raises_value_error():
    with pytest.raises(ValueError):
        equip(Inventory(carried=(POTION_OF_HEALING,)), POTION_OF_HEALING)


def test_equip_removes_exactly_one_of_two_identical_weapons():
    inv = equip(Inventory(carried=(DAGGER, DAGGER)), DAGGER)
    assert inv.melee is DAGGER
    assert inv.carried == (DAGGER,)


def test_equip_leaves_its_input_untouched():
    inv = Inventory(carried=(SWORD,), melee=DAGGER)
    before = dataclasses.replace(inv)
    equip(inv, SWORD)
    assert inv == before


def test_equip_of_a_full_pack_stays_within_the_carry_limit():
    full = _full_pack()
    inv = dataclasses.replace(full, carried=full.carried[:-1] + (SWORD,), melee=DAGGER)
    result = equip(inv, SWORD)
    assert len(result.carried) == CARRY_LIMIT
    assert result.melee is SWORD
    assert result.carried[-1] is DAGGER


# ---------------------------------------------------------------------------
# unequip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slot", ["melee", "ranged", "shield"])
def test_unequip_empties_the_slot_and_returns_the_item_to_carried(slot):
    item = {"melee": DAGGER, "ranged": SHORTBOW, "shield": BUCKLER}[slot]
    inv = unequip(Inventory(**{slot: item}), slot)
    assert getattr(inv, slot) is None
    assert inv.carried == (item,)


@pytest.mark.parametrize("slot", ["melee", "ranged", "shield"])
def test_unequip_an_empty_slot_is_a_no_op(slot):
    inv = Inventory(carried=(CLUB,))
    result = unequip(inv, slot)
    assert result == inv


def test_unequip_appends_to_the_end_of_carried():
    inv = unequip(Inventory(carried=(BANDAGE, CLUB), melee=DAGGER), "melee")
    assert inv.carried == (BANDAGE, CLUB, DAGGER)


def test_unequip_leaves_the_other_slots_alone():
    inv = unequip(Inventory(melee=DAGGER, ranged=SHORTBOW, shield=BUCKLER), "ranged")
    assert inv.melee is DAGGER
    assert inv.ranged is None
    assert inv.shield is BUCKLER


def test_unequip_into_a_full_pack_is_a_no_op_and_keeps_the_cap_true():
    inv = dataclasses.replace(_full_pack(), melee=DAGGER)
    result = unequip(inv, "melee")
    assert result == inv
    assert len(result.carried) == CARRY_LIMIT
    assert result.melee is DAGGER


def test_unequip_with_an_unknown_slot_name_raises_value_error():
    with pytest.raises(ValueError):
        unequip(Inventory(melee=DAGGER), "head")


def test_unequip_leaves_its_input_untouched():
    inv = Inventory(carried=(CLUB,), melee=DAGGER)
    before = dataclasses.replace(inv)
    unequip(inv, "melee")
    assert inv == before


def test_equip_then_unequip_round_trips():
    start = Inventory(carried=(DAGGER,))
    assert unequip(equip(start, DAGGER), "melee") == start


# ---------------------------------------------------------------------------
# Purity, stated once over every function
# ---------------------------------------------------------------------------


def test_no_function_mutates_the_inventory_it_is_given():
    inv = Inventory(carried=(SWORD, BANDAGE), melee=DAGGER, ranged=SHORTBOW, shield=BUCKLER)
    before = dataclasses.replace(inv)

    equip(inv, SWORD)
    unequip(inv, "melee")
    add(inv, CLUB)
    drop(inv, 0)

    assert inv == before
    assert inv.carried == before.carried


def test_every_function_returns_an_inventory_not_the_same_object_when_it_changes_things():
    inv = Inventory(carried=(SWORD,))
    assert equip(inv, SWORD) is not inv
    assert add(inv, CLUB)[0] is not inv
    assert drop(inv, 0)[0] is not inv
    assert unequip(Inventory(melee=DAGGER), "melee") is not inv


# ---------------------------------------------------------------------------
# Import hygiene (CONTRACT-v6 §10 v6): items.py is a leaf
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


def test_items_public_surface_is_exactly_the_contract_surface():
    import roguelike.items as items

    assert set(items.__all__) == {
        "ItemKind",
        "WeaponKind",
        "DamageType",
        "Resistance",
        "Grade",
        "Weapon",
        "Shield",
        "Consumable",
        "Inventory",
        "CARRY_LIMIT",
        "CLUB",
        "DAGGER",
        "SWORD",
        "SLING",
        "SHORTBOW",
        "LONGBOW",
        "BUCKLER",
        "KITE_SHIELD",
        "TOWER_SHIELD",
        "POTION_OF_HEALING",
        "BANDAGE",
        "equip",
        "unequip",
        "add",
        "drop",
    }
    imported = {"annotations", "dataclass", "replace", "Enum", "IntEnum", "auto"}
    public = {name for name in vars(items) if not name.startswith("_")}
    assert public - set(items.__all__) <= imported
