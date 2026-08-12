"""Unit tests for :mod:`roguelike.npc` (CONTRACT-v5 §24, task T26).

Most maps here are hand-drawn from character rows, so every expected decision can be
read straight off the map in the test itself. The real generator appears only where the
claim genuinely needs real levels — the spawn sweep and the planning-cost measurement.

Two failure modes in this module produce an *unplayable game* rather than a crash, and
they get the heaviest tests here:

* **Spawn clustering.** Two jackals beat a baseline player 100% of the time (600 runs,
  zero wins — CONTRACT-v5 §24.1), so a spawn that drops a pair beside the player's
  staircase kills level-1 characters through no fault of their own. The two radius rules
  are swept over 30+ seeds and several generated levels.
* **Lockstep packs.** If every monster spawns with ``energy = 0`` they all act on the
  same ticks and a group moves as one organism, so the staggering is asserted directly.

Nothing here initialises curses, and nothing needs a TTY.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
from roguelike.npc import wants_to_flee
from roguelike.status import StatusEffect, StatusKind
import inspect
import itertools
import os
import pathlib
import random
import subprocess
import sys
import time

import pytest

from roguelike import npc as npc_module
from roguelike.generator import generate_level
from roguelike.items import DamageType, Resistance
from roguelike.level import Level, freeze_grid
from roguelike.npc import (
    FORGET_TICKS,
    MONSTERS_PER_LEVEL,
    NPC,
    PERCEPTION_RADIUS,
    SPAWN_MIN_SEPARATION,
    SPAWN_SAFE_RADIUS,
    SPECIES_DATA,
    AiState,
    NpcAction,
    NpcActionKind,
    Species,
    SpeciesData,
    plan_action,
    resistance_of,
    spawn_npcs,
)
from roguelike.stats import Actor, Stats, derive
from roguelike.tiles import Tile
from roguelike.world import is_passable, is_planning_passable

CHAR_TO_TILE = {
    "#": Tile.WALL,
    ".": Tile.FLOOR,
    "+": Tile.DOOR,
    "<": Tile.STAIRS_UP,
    ">": Tile.STAIRS_DOWN,
}

NO_DOORS: frozenset[tuple[int, int]] = frozenset()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_level(
    rows: list[str], player_start: tuple[int, int] = (1, 1), seed: int = 0
) -> Level:
    """Build a ``Level`` from character rows. ``rows[y][x]``, so row 0 is the top."""
    assert len({len(row) for row in rows}) == 1, "all rows must be the same width"
    grid = [[CHAR_TO_TILE[c] for c in row] for row in rows]
    return Level(len(grid[0]), len(grid), freeze_grid(grid), (), player_start, seed)


def make_npc(
    position: tuple[int, int],
    ai_state: AiState = AiState.HUNTING,
    species: Species = Species.RAT,
    actor_id: int = 1,
) -> NPC:
    """A monster at full health, for feeding to :func:`plan_action`."""
    data = SPECIES_DATA[species]
    return NPC(
        actor_id=actor_id,
        species=species,
        actor=Actor(stats=data.stats, hp=derive(data.stats).max_hp),
        position=position,
        ai_state=ai_state,
    )


def chebyshev(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


class ForbiddenRandom:
    """An ``rng`` that fails loudly on any draw at all.

    Hunting is documented to consume no randomness (CONTRACT-v5 §24.2 gives the coin flip
    to ``WANDERING`` only), which matters for seed reuse: the caller derives one generator
    per NPC per tick, and a hunter that silently drew from it would desynchronise nothing
    visible but would make the wander stream depend on what the neighbours were doing.
    """

    def randrange(self, *args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("plan_action drew randomness while hunting")

    def choice(self, *args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("plan_action drew randomness while hunting")

    def random(self, *args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("plan_action drew randomness while hunting")


# A 14x3 open room inside its wall ring: wide enough for a wandering monster to have
# three legal neighbours, so a wander is distinguishable from a hunt by its target.
OPEN_ROOM = [
    "################",
    "#..............#",
    "#..............#",
    "#..............#",
    "################",
]

# Two parallel corridors joined at both ends. The only route from (1, 1) to (7, 1) along
# the top can be plugged at (4, 1), forcing a detour the long way round.
RING = [
    "#########",
    "#.......#",
    "#.#####.#",
    "#.#####.#",
    "#.......#",
    "#########",
]

# A left-hand room whose single exit is the door at (3, 2), opening onto a corridor.
DOOR_ROOM = [
    "##########",
    "#..#.....#",
    "#..+.....#",
    "#..#.....#",
    "##########",
]

# (2, 1) cannot see (4, 1): the wall at (3, 1) is in the way. They are still connected,
# the long way round through (3, 3).
WALL_BETWEEN = [
    "######",
    "#..#.#",
    "#..#.#",
    "#....#",
    "######",
]

# The same shape with a door instead of that wall, so perception can be flipped by
# ``open_doors`` alone with every coordinate held fixed.
DOOR_BETWEEN = [
    "######",
    "#..+.#",
    "#..#.#",
    "#....#",
    "######",
]

# Two sealed columns: the player at (5, 1) is unreachable from (1, 1).
SEALED = [
    "#######",
    "#..#..#",
    "#..#..#",
    "#######",
]


def wander_outcomes(
    npc: NPC,
    level: Level,
    open_doors: frozenset[tuple[int, int]],
    occupied: frozenset[tuple[int, int]],
    player: tuple[int, int],
    seeds: int = 60,
) -> set[tuple[NpcActionKind, tuple[int, int] | None]]:
    """Every distinct action seen over ``seeds`` different generators."""
    return {
        (action.kind, action.target)
        for action in (
            plan_action(random.Random(seed), npc, level, open_doors, occupied, player)
            for seed in range(seeds)
        )
    }


# ---------------------------------------------------------------------------
# The bestiary (CONTRACT-v5 §24.1)
# ---------------------------------------------------------------------------


def test_species_has_exactly_four_members():
    assert [s.name for s in Species] == [
        "RAT",
        "JACKAL",
        "GIANT_BAT",
        "CAVE_SNAKE",
    ]


def test_species_data_has_exactly_one_entry_per_species():
    assert set(SPECIES_DATA) == set(Species)
    assert len(SPECIES_DATA) == 4


@pytest.mark.parametrize(
    "species, name, glyph, stats, attack, xp, poison, flee",
    [
        (Species.RAT, "rat", "r", (4, 14, 3), (1, 3), 5, 0, 2),
        (Species.JACKAL, "jackal", "j", (8, 13, 5), (2, 4), 10, 0, 5),
        (Species.GIANT_BAT, "giant bat", "B", (3, 18, 2), (1, 2), 8, 0, 3),
        (Species.CAVE_SNAKE, "cave snake", "s", (6, 8, 5), (2, 4), 12, 30, 1),
    ],
)
def test_species_data_fields_match_the_binding_table(
    species, name, glyph, stats, attack, xp, poison, flee
):
    # The whole existing bestiary, re-asserted literally so a stray edit to any one
    # number is loud (T33 brief: "do not change any existing stat ... every existing
    # number in npc.py stays exactly as it is").
    data = SPECIES_DATA[species]
    assert isinstance(data, SpeciesData)
    assert data.name == name
    assert data.glyph == glyph
    assert data.stats == Stats(str_=stats[0], agi=stats[1], vit=stats[2])
    assert (data.attack_min, data.attack_max) == attack
    assert data.xp_value == xp
    assert data.poison_chance == poison
    assert data.flee_chance == flee
    assert data.hostile is True


def test_species_names_are_lower_case_because_messages_interpolate_them():
    # Messages read "The {name} hits you." (CONTRACT-v5 §24.1).
    for data in SPECIES_DATA.values():
        assert data.name == data.name.lower()


def test_species_glyphs_are_single_distinct_characters():
    glyphs = [data.glyph for data in SPECIES_DATA.values()]
    assert all(len(g) == 1 for g in glyphs)
    assert len(set(glyphs)) == 4


@pytest.mark.parametrize(
    "species, max_hp, speed, evasion, block",
    [
        (Species.RAT, 17, 140, 17, 0),
        (Species.JACKAL, 25, 130, 14, 0),
        (Species.GIANT_BAT, 13, 180, 29, 0),
        (Species.CAVE_SNAKE, 25, 80, 0, 0),
    ],
)
def test_derived_values_fall_out_of_stats_derive_with_no_special_cases(
    species, max_hp, speed, evasion, block
):
    derived = derive(SPECIES_DATA[species].stats)
    assert derived.max_hp == max_hp
    assert derived.speed == speed
    assert derived.evasion == evasion
    assert derived.block == block


def test_species_data_does_not_store_derived_values():
    # HP/speed/evasion/block must live in stats.derive alone, or two HP formulas start
    # to drift (CONTRACT-v5 §24.1).
    fields = {f.name for f in dataclasses.fields(SpeciesData)}
    assert fields == {
        "name",
        "glyph",
        "stats",
        "attack_min",
        "attack_max",
        "xp_value",
        "poison_chance",
        # Whether bumping into this creature attacks it, and how readily it breaks
        # off a losing fight. Not derived values -- facts about the species, like
        # its glyph.
        "hostile",
        "flee_chance",
        # How this species takes each DamageType (CONTRACT-v6 §26.3) -- also a fact
        # about the species, not a derived value. combat.py applies the multiplier;
        # this module only says how much.
        "resistances",
    }
    assert fields.isdisjoint({"max_hp", "hp", "speed", "evasion", "block"})


def test_only_the_cave_snake_is_poisonous():
    poisonous = {s for s, d in SPECIES_DATA.items() if d.poison_chance != 0}
    assert poisonous == {Species.CAVE_SNAKE}
    assert SPECIES_DATA[Species.CAVE_SNAKE].poison_chance == 30


def test_attack_ranges_are_ordered_positive_integers():
    for data in SPECIES_DATA.values():
        assert isinstance(data.attack_min, int)
        assert isinstance(data.attack_max, int)
        assert 0 < data.attack_min <= data.attack_max


# ---------------------------------------------------------------------------
# Resistances (CONTRACT-v6 §26.3, task T33)
# ---------------------------------------------------------------------------

#: The §26.3 table, spelled out for all four species x all three damage types. Anything
#: not the giant bat/BLUNT or cave snake/PIERCE cell is NORMAL.
RESISTANCE_TABLE = {
    (Species.RAT, DamageType.SLASH): Resistance.NORMAL,
    (Species.RAT, DamageType.PIERCE): Resistance.NORMAL,
    (Species.RAT, DamageType.BLUNT): Resistance.NORMAL,
    (Species.JACKAL, DamageType.SLASH): Resistance.NORMAL,
    (Species.JACKAL, DamageType.PIERCE): Resistance.NORMAL,
    (Species.JACKAL, DamageType.BLUNT): Resistance.NORMAL,
    (Species.GIANT_BAT, DamageType.SLASH): Resistance.NORMAL,
    (Species.GIANT_BAT, DamageType.PIERCE): Resistance.NORMAL,
    (Species.GIANT_BAT, DamageType.BLUNT): Resistance.VULNERABLE,
    (Species.CAVE_SNAKE, DamageType.SLASH): Resistance.NORMAL,
    (Species.CAVE_SNAKE, DamageType.PIERCE): Resistance.RESISTANT,
    (Species.CAVE_SNAKE, DamageType.BLUNT): Resistance.NORMAL,
}


@pytest.mark.parametrize("species, damage_type", list(RESISTANCE_TABLE))
def test_resistance_of_matches_the_binding_table(species, damage_type):
    assert resistance_of(species, damage_type) == RESISTANCE_TABLE[(species, damage_type)]


def test_only_the_giant_bat_and_cave_snake_have_any_exception_at_all():
    # Everything else -- rat and jackal, wholly -- reads NORMAL for every type, so their
    # `resistances` dicts hold no exceptions (CONTRACT-v6 §26.3: "absence means NORMAL").
    assert SPECIES_DATA[Species.RAT].resistances == {}
    assert SPECIES_DATA[Species.JACKAL].resistances == {}


def test_giant_bat_is_vulnerable_to_blunt_and_only_blunt():
    resistances = SPECIES_DATA[Species.GIANT_BAT].resistances
    assert resistances == {DamageType.BLUNT: Resistance.VULNERABLE}


def test_cave_snake_resists_pierce_and_only_pierce():
    resistances = SPECIES_DATA[Species.CAVE_SNAKE].resistances
    assert resistances == {DamageType.PIERCE: Resistance.RESISTANT}


def test_nothing_in_the_shipped_bestiary_is_immune():
    # The tier exists and is tested elsewhere (items.py); no shipped species uses it
    # (CONTRACT-v6 §26.3).
    for species in Species:
        for damage_type in DamageType:
            assert resistance_of(species, damage_type) != Resistance.IMMUNE
    for data in SPECIES_DATA.values():
        assert Resistance.IMMUNE not in data.resistances.values()


def test_resistance_of_defaults_to_normal_for_anything_not_in_the_table():
    for species in Species:
        for damage_type in DamageType:
            if (species, damage_type) not in RESISTANCE_TABLE or RESISTANCE_TABLE[
                (species, damage_type)
            ] is Resistance.NORMAL:
                assert resistance_of(species, damage_type) == Resistance.NORMAL


def test_resistance_of_never_mutates_or_indexes_the_dict_directly():
    # A caller should never need `.get(..., Resistance.NORMAL)` by hand.
    before = copy.deepcopy(SPECIES_DATA)
    for species in Species:
        for damage_type in DamageType:
            resistance_of(species, damage_type)
    assert SPECIES_DATA == before


def test_resistance_of_signature():
    parameters = list(inspect.signature(resistance_of).parameters)
    assert parameters == ["species", "damage_type"]


# ---------------------------------------------------------------------------
# Constants (CONTRACT-v5 §24.2, §24.4)
# ---------------------------------------------------------------------------


def test_tuning_constants_are_exactly_the_contract_values():
    assert PERCEPTION_RADIUS == 10
    assert FORGET_TICKS == 5
    assert MONSTERS_PER_LEVEL == 6
    assert SPAWN_SAFE_RADIUS == 8
    assert SPAWN_MIN_SEPARATION == 5


def test_tuning_constants_are_integers():
    for value in (
        PERCEPTION_RADIUS,
        FORGET_TICKS,
        MONSTERS_PER_LEVEL,
        SPAWN_SAFE_RADIUS,
        SPAWN_MIN_SEPARATION,
    ):
        assert isinstance(value, int) and not isinstance(value, bool)


def test_the_three_ai_states_and_three_action_kinds():
    assert [s.name for s in AiState] == ["WANDERING", "HUNTING", "FLEEING"]
    assert [k.name for k in NpcActionKind] == ["WAIT", "MOVE", "ATTACK"]


def test_npc_defaults_match_the_contract_signature():
    fields = {f.name: f for f in dataclasses.fields(NPC)}
    assert list(fields) == [
        "actor_id",
        "species",
        "actor",
        "position",
        "energy",
        "ai_state",
        "memory",
    ]
    assert fields["energy"].default == 0
    assert fields["ai_state"].default is AiState.WANDERING
    assert fields["memory"].default == 0
    # No path cache, no hostile flag, no inventory (CONTRACT-v5 §24).
    assert "path" not in fields
    assert "hostile" not in fields
    assert "inventory" not in fields


def test_npc_action_target_defaults_to_none():
    assert NpcAction(NpcActionKind.WAIT).target is None


# ---------------------------------------------------------------------------
# plan_action — HUNTING
# ---------------------------------------------------------------------------


ADJACENT_OFFSETS = [
    (dx, dy)
    for dx, dy in itertools.product((-1, 0, 1), repeat=2)
    if (dx, dy) != (0, 0)
]


@pytest.mark.parametrize("offset", ADJACENT_OFFSETS)
def test_hunting_attacks_the_player_from_all_eight_adjacent_cells(offset):
    level = make_level(OPEN_ROOM)
    player = (7, 2)
    position = (player[0] + offset[0], player[1] + offset[1])
    assert chebyshev(position, player) == 1
    action = plan_action(
        ForbiddenRandom(),
        make_npc(position, AiState.HUNTING),
        level,
        NO_DOORS,
        frozenset({player}),
        player,
    )
    assert action == NpcAction(NpcActionKind.ATTACK, player)


@pytest.mark.parametrize("offset", ADJACENT_OFFSETS)
def test_wandering_with_the_player_adjacent_and_in_sight_also_attacks(offset):
    # Spotting the player and then shuffling sideways would be a visible glitch: the
    # perception check must produce a hunting action *this same turn*.
    level = make_level(OPEN_ROOM)
    player = (7, 2)
    position = (player[0] + offset[0], player[1] + offset[1])
    action = plan_action(
        ForbiddenRandom(),
        make_npc(position, AiState.WANDERING),
        level,
        NO_DOORS,
        frozenset({player}),
        player,
    )
    assert action == NpcAction(NpcActionKind.ATTACK, player)


def test_hunting_at_distance_moves_one_real_step_closer():
    level = make_level(OPEN_ROOM)
    player = (12, 2)
    start = (1, 2)
    action = plan_action(
        ForbiddenRandom(),
        make_npc(start, AiState.HUNTING),
        level,
        NO_DOORS,
        frozenset({player}),
        player,
    )
    assert action.kind is NpcActionKind.MOVE
    assert action.target is not None
    assert chebyshev(action.target, start) == 1
    assert chebyshev(action.target, player) < chebyshev(start, player)
    assert is_planning_passable(level, NO_DOORS, *action.target)


@pytest.mark.parametrize("start", [(1, 1), (1, 3), (14, 1), (14, 3), (10, 1)])
def test_hunting_step_is_adjacent_and_strictly_closer_from_many_starts(start):
    level = make_level(OPEN_ROOM)
    player = (7, 2)
    action = plan_action(
        random.Random(0),
        make_npc(start, AiState.HUNTING),
        level,
        NO_DOORS,
        frozenset({player}),
        player,
    )
    assert action.kind is NpcActionKind.MOVE
    assert chebyshev(action.target, start) == 1
    assert chebyshev(action.target, player) < chebyshev(start, player)


def test_hunting_a_walled_off_player_waits_and_never_raises():
    level = make_level(SEALED)
    player = (5, 1)
    action = plan_action(
        ForbiddenRandom(),
        make_npc((1, 1), AiState.HUNTING),
        level,
        NO_DOORS,
        frozenset({player}),
        player,
    )
    assert action == NpcAction(NpcActionKind.WAIT)
    assert action.target is None


def test_hunting_draws_no_randomness_at_all():
    # ForbiddenRandom raises on any draw; the three cases above cover attack, move and
    # the no-path wait, so this simply restates that all three ran clean.
    level = make_level(OPEN_ROOM)
    for start in ((6, 2), (1, 2)):
        plan_action(
            ForbiddenRandom(),
            make_npc(start, AiState.HUNTING),
            level,
            NO_DOORS,
            frozenset({(7, 2)}),
            (7, 2),
        )


# ---------------------------------------------------------------------------
# plan_action — WANDERING and perception
# ---------------------------------------------------------------------------


def test_wandering_hunts_the_moment_the_player_is_in_plain_sight_within_radius():
    level = make_level(OPEN_ROOM)
    start, player = (1, 2), (11, 2)
    assert chebyshev(start, player) == PERCEPTION_RADIUS
    outcomes = wander_outcomes(
        make_npc(start, AiState.WANDERING), level, NO_DOORS, frozenset({player}), player
    )
    # Exactly one outcome across 60 different generators: this is not a coin flip.
    assert outcomes == {(NpcActionKind.MOVE, (2, 2))}


def test_wandering_ignores_a_player_one_cell_beyond_the_perception_radius():
    level = make_level(OPEN_ROOM)
    start, player = (1, 2), (12, 2)
    assert chebyshev(start, player) == PERCEPTION_RADIUS + 1
    # Line of sight succeeds down the clear room; only the radius rejects it.
    from roguelike.fov import has_line_of_sight

    assert has_line_of_sight(level, NO_DOORS, start, player)

    outcomes = wander_outcomes(
        make_npc(start, AiState.WANDERING), level, NO_DOORS, frozenset({player}), player
    )
    assert (NpcActionKind.WAIT, None) in outcomes
    # Moves away from the player prove this is a wander, not a hunt.
    assert (NpcActionKind.MOVE, (1, 1)) in outcomes
    assert (NpcActionKind.MOVE, (1, 3)) in outcomes
    assert outcomes <= {
        (NpcActionKind.WAIT, None),
        (NpcActionKind.MOVE, (1, 1)),
        (NpcActionKind.MOVE, (1, 3)),
        (NpcActionKind.MOVE, (2, 2)),
    }
    assert not any(kind is NpcActionKind.ATTACK for kind, _ in outcomes)


def test_wandering_ignores_a_player_behind_a_wall_even_at_distance_two():
    level = make_level(WALL_BETWEEN)
    start, player = (2, 1), (4, 1)
    assert chebyshev(start, player) == 2
    outcomes = wander_outcomes(
        make_npc(start, AiState.WANDERING), level, NO_DOORS, frozenset({player}), player
    )
    assert (NpcActionKind.WAIT, None) in outcomes
    # (1, 1) is directly away from the player: a hunter would never choose it.
    assert (NpcActionKind.MOVE, (1, 1)) in outcomes
    assert outcomes <= {
        (NpcActionKind.WAIT, None),
        (NpcActionKind.MOVE, (1, 1)),
        (NpcActionKind.MOVE, (2, 2)),
    }


def test_wandering_moves_only_to_legal_orthogonal_neighbours():
    level = make_level(OPEN_ROOM)
    start = (1, 2)
    orthogonal = {(0, 2), (2, 2), (1, 1), (1, 3)}
    outcomes = wander_outcomes(
        make_npc(start, AiState.WANDERING),
        level,
        NO_DOORS,
        frozenset({(12, 2)}),
        (12, 2),
    )
    for kind, target in outcomes:
        if kind is NpcActionKind.MOVE:
            assert target in orthogonal
            assert is_planning_passable(level, NO_DOORS, *target)
            # (0, 2) is the wall ring and must never be chosen.
            assert target != (0, 2)


def test_wandering_with_no_legal_neighbour_waits():
    # A one-cell closet: every orthogonal neighbour is wall.
    level = make_level(["###", "#.#", "###"])
    outcomes = wander_outcomes(
        make_npc((1, 1), AiState.WANDERING),
        level,
        NO_DOORS,
        frozenset(),
        (1, 1000),
    )
    assert outcomes == {(NpcActionKind.WAIT, None)}


def test_wandering_boxed_in_by_other_actors_waits():
    level = make_level(OPEN_ROOM)
    start = (1, 2)
    occupied = frozenset({(2, 2), (1, 1), (1, 3), (12, 2)})
    outcomes = wander_outcomes(
        make_npc(start, AiState.WANDERING), level, NO_DOORS, occupied, (12, 2)
    )
    assert outcomes == {(NpcActionKind.WAIT, None)}


def test_a_closed_door_blocks_perception_and_an_open_one_does_not():
    """Same coordinates, same everything — only ``open_doors`` varies."""
    level = make_level(DOOR_BETWEEN)
    start, player, door = (2, 1), (4, 1), (3, 1)
    assert chebyshev(start, player) == 2 <= PERCEPTION_RADIUS

    seen = wander_outcomes(
        make_npc(start, AiState.WANDERING),
        level,
        frozenset({door}),
        frozenset({player}),
        player,
    )
    # Door open: perception succeeds, and every generator produces the same hunt step.
    assert seen == {(NpcActionKind.MOVE, door)}

    unseen = wander_outcomes(
        make_npc(start, AiState.WANDERING),
        level,
        NO_DOORS,
        frozenset({player}),
        player,
    )
    # Door closed: a coin flip and a random neighbour, including moves away.
    assert (NpcActionKind.WAIT, None) in unseen
    assert (NpcActionKind.MOVE, (1, 1)) in unseen
    assert len(unseen) > 1


# ---------------------------------------------------------------------------
# Occupancy (CONTRACT-v5 §24.2): NPCs neither stack nor swap places
# ---------------------------------------------------------------------------


def test_a_hunter_routes_around_an_occupied_cell_rather_than_through_it():
    level = make_level(RING)
    player, start, blocker = (7, 1), (1, 1), (4, 1)
    free = plan_action(
        ForbiddenRandom(),
        make_npc(start, AiState.HUNTING),
        level,
        NO_DOORS,
        frozenset({player}),
        player,
    )
    assert free == NpcAction(NpcActionKind.MOVE, (2, 1))

    blocked = plan_action(
        ForbiddenRandom(),
        make_npc(start, AiState.HUNTING),
        level,
        NO_DOORS,
        frozenset({player, blocker}),
        player,
    )
    assert blocked.kind is NpcActionKind.MOVE
    # It went the long way round; it did not walk into the blocker's corridor arm.
    assert blocked.target == (1, 2)
    assert blocked.target != blocker


def test_a_move_target_is_never_a_cell_in_occupied():
    level = make_level(RING)
    player = (7, 1)
    ring_cells = [
        (x, y)
        for y in range(level.height)
        for x in range(level.width)
        if is_passable(level, NO_DOORS, x, y)
    ]
    for start in ring_cells:
        if start == player:
            continue
        for blocker in ring_cells:
            if blocker in (start, player):
                continue
            occupied = frozenset({player, blocker})
            action = plan_action(
                random.Random(0),
                make_npc(start, AiState.HUNTING),
                level,
                NO_DOORS,
                occupied,
                player,
            )
            if action.kind is NpcActionKind.MOVE:
                assert action.target not in occupied


def test_the_only_route_being_occupied_gives_wait_and_never_that_cell():
    level = make_level(["#########", "#.......#", "#########"])
    player, blocker = (5, 1), (2, 1)
    action = plan_action(
        ForbiddenRandom(),
        make_npc((1, 1), AiState.HUNTING),
        level,
        NO_DOORS,
        frozenset({player, blocker}),
        player,
    )
    assert action == NpcAction(NpcActionKind.WAIT)


def test_two_npcs_contending_for_one_corridor_cell_do_not_both_get_it():
    """The bottleneck is the door at (3, 2); both monsters want it.

    ``game.py`` plans in ``actor_id`` order and folds each accepted move into
    ``occupied`` before planning the next NPC (CONTRACT-v5 §24.3), which is the
    convention modelled here. The second monster must not be handed a cell the first
    one has just taken.
    """
    level = make_level(DOOR_ROOM)
    player = (8, 2)
    first = make_npc((2, 1), AiState.HUNTING, actor_id=1)
    second = make_npc((2, 3), AiState.HUNTING, actor_id=2)
    door = (3, 2)

    action_one = plan_action(
        ForbiddenRandom(),
        first,
        level,
        NO_DOORS,
        frozenset({second.position, player}),
        player,
    )
    assert action_one == NpcAction(NpcActionKind.MOVE, door)

    moved_first = dataclasses.replace(first, position=action_one.target)
    action_two = plan_action(
        ForbiddenRandom(),
        second,
        level,
        NO_DOORS,
        frozenset({moved_first.position, player}),
        player,
    )
    assert action_two.target != door
    assert action_two == NpcAction(NpcActionKind.WAIT)


def test_npcs_never_swap_places_while_hunting():
    level = make_level(OPEN_ROOM)
    player = (12, 2)
    neighbour = (2, 2)
    action = plan_action(
        ForbiddenRandom(),
        make_npc((1, 2), AiState.HUNTING),
        level,
        NO_DOORS,
        frozenset({neighbour, player}),
        player,
    )
    assert action.kind is NpcActionKind.MOVE
    assert action.target != neighbour


def test_npcs_never_swap_places_while_wandering():
    level = make_level(OPEN_ROOM)
    neighbour = (2, 2)
    outcomes = wander_outcomes(
        make_npc((1, 2), AiState.WANDERING),
        level,
        NO_DOORS,
        frozenset({neighbour, (12, 2)}),
        (12, 2),
    )
    for kind, target in outcomes:
        if kind is NpcActionKind.MOVE:
            assert target != neighbour


# ---------------------------------------------------------------------------
# Doors are routable; NPCs are not fogged
# ---------------------------------------------------------------------------


def test_a_hunter_whose_only_path_runs_through_a_closed_door_moves_towards_it():
    level = make_level(DOOR_BETWEEN)
    player, door = (4, 1), (3, 1)
    assert not is_passable(level, NO_DOORS, *door)
    assert is_planning_passable(level, NO_DOORS, *door)
    action = plan_action(
        ForbiddenRandom(),
        make_npc((2, 1), AiState.HUNTING),
        level,
        NO_DOORS,
        frozenset({player}),
        player,
    )
    assert action == NpcAction(NpcActionKind.MOVE, door)


def test_a_hunter_in_a_room_whose_only_exit_is_a_closed_door_moves_to_the_door():
    level = make_level(DOOR_ROOM)
    player, door = (8, 2), (3, 2)
    action = plan_action(
        ForbiddenRandom(),
        make_npc((1, 1), AiState.HUNTING),
        level,
        NO_DOORS,
        frozenset({player}),
        player,
    )
    assert action.kind is NpcActionKind.MOVE
    assert chebyshev(action.target, door) <= 1


def test_plan_action_takes_no_explored_argument():
    parameters = list(inspect.signature(plan_action).parameters)
    assert parameters == [
        "rng",
        "npc",
        "level",
        "open_doors",
        "occupied",
        "player",
    ]
    assert "explored" not in parameters
    assert "visible" not in parameters


def test_npcs_are_not_fogged_and_plan_through_unexplored_terrain():
    """An NPC lives on this level and knows it — the opposite of the travel activity."""
    level = make_level(RING)
    player, start = (7, 1), (1, 1)

    explored_nothing: frozenset[tuple[int, int]] = frozenset()
    explored_everything = frozenset(
        (x, y) for y in range(level.height) for x in range(level.width)
    )
    assert explored_nothing != explored_everything

    # There is no parameter that could carry either set — the function genuinely cannot
    # depend on one.
    for explored in (explored_nothing, explored_everything):
        with pytest.raises(TypeError):
            plan_action(
                random.Random(0),
                make_npc(start, AiState.HUNTING),
                level,
                NO_DOORS,
                frozenset({player}),
                player,
                explored=explored,
            )

    first = plan_action(
        ForbiddenRandom(),
        make_npc(start, AiState.HUNTING),
        level,
        NO_DOORS,
        frozenset({player}),
        player,
    )
    second = plan_action(
        ForbiddenRandom(),
        make_npc(start, AiState.HUNTING),
        level,
        NO_DOORS,
        frozenset({player}),
        player,
    )
    assert first == second
    # And the step it chose lies in terrain nothing has explored.
    assert first.kind is NpcActionKind.MOVE
    assert first.target not in explored_nothing


# ---------------------------------------------------------------------------
# spawn_npcs — placement rules (CONTRACT-v5 §24.4)
# ---------------------------------------------------------------------------


SWEEP_LEVELS = [(80, 22), (60, 30), (100, 25)]


def _sweep_spawns():
    """(level, npcs) for 30 seeds on the default size plus 10 each on two other sizes."""
    for seed in range(30):
        level = generate_level(seed)
        yield level, spawn_npcs(random.Random(seed * 7 + 1), level)
    for width, height in SWEEP_LEVELS[1:]:
        for seed in range(100, 110):
            level = generate_level(seed, width=width, height=height)
            yield level, spawn_npcs(random.Random(seed), level)


SWEEP = None


def sweep():
    global SWEEP
    if SWEEP is None:
        SWEEP = list(_sweep_spawns())
    return SWEEP


def test_the_seed_sweep_covers_at_least_thirty_seeds_and_several_levels():
    runs = sweep()
    assert len(runs) >= 50
    assert len({level.seed for level, _ in runs}) >= 30
    assert len({(level.width, level.height) for level, _ in runs}) >= 3


def test_spawn_places_exactly_six_npcs_on_every_real_level():
    for level, npcs in sweep():
        assert len(npcs) == MONSTERS_PER_LEVEL, level.seed
        assert isinstance(npcs, tuple)


def test_every_spawn_position_is_passable_with_no_door_open():
    for level, npcs in sweep():
        for monster in npcs:
            x, y = monster.position
            assert is_passable(level, NO_DOORS, x, y), (level.seed, monster.position)
            assert level.tile_at(x, y) is not Tile.DOOR


def test_no_spawn_lands_within_the_safe_radius_of_the_player_start():
    """The staircase-ambush rule. Two jackals by the stairs is a guaranteed death."""
    worst = None
    for level, npcs in sweep():
        for monster in npcs:
            distance = chebyshev(monster.position, level.player_start)
            assert distance >= SPAWN_SAFE_RADIUS, (
                level.seed,
                monster.position,
                level.player_start,
                distance,
            )
            worst = distance if worst is None else min(worst, distance)
    assert worst is not None and worst >= SPAWN_SAFE_RADIUS


def test_no_two_spawns_land_within_the_minimum_separation():
    """The pack rule. A pair dropped side by side is unwinnable, not merely unfair."""
    worst = None
    for level, npcs in sweep():
        for a, b in itertools.combinations(npcs, 2):
            distance = chebyshev(a.position, b.position)
            assert distance >= SPAWN_MIN_SEPARATION, (
                level.seed,
                a.position,
                b.position,
                distance,
            )
            worst = distance if worst is None else min(worst, distance)
    assert worst is not None and worst >= SPAWN_MIN_SEPARATION


def test_spawn_positions_are_all_distinct():
    for level, npcs in sweep():
        positions = [monster.position for monster in npcs]
        assert len(set(positions)) == len(positions), level.seed


def test_actor_ids_are_distinct_sequential_and_start_at_one():
    # The player is permanently actor_id 0 (CONTRACT-v5 §0.12).
    for _, npcs in sweep():
        ids = [monster.actor_id for monster in npcs]
        assert ids == list(range(1, len(npcs) + 1))
        assert len(set(ids)) == len(ids)
        assert 0 not in ids


def test_first_actor_id_is_honoured():
    level = generate_level(11)
    npcs = spawn_npcs(random.Random(5), level, first_actor_id=42)
    assert [monster.actor_id for monster in npcs] == list(range(42, 42 + len(npcs)))


def test_first_actor_id_defaults_to_one():
    assert inspect.signature(spawn_npcs).parameters["first_actor_id"].default == 1


def test_every_spawned_npc_starts_at_full_health_with_no_status_effects():
    for _, npcs in sweep():
        for monster in npcs:
            data = SPECIES_DATA[monster.species]
            assert monster.actor.stats == data.stats
            assert monster.actor.hp == derive(data.stats).max_hp
            assert monster.actor.status_effects == ()


def test_every_spawned_npc_starts_wandering_with_no_memory():
    for _, npcs in sweep():
        for monster in npcs:
            assert monster.ai_state is AiState.WANDERING
            assert monster.memory == 0


def test_species_are_drawn_from_all_four():
    seen = {monster.species for _, npcs in sweep() for monster in npcs}
    assert seen == set(Species)


# ---------------------------------------------------------------------------
# The lockstep-pack trap: starting energy must be staggered
# ---------------------------------------------------------------------------


def test_spawned_energy_is_staggered_and_never_all_zero():
    energies = [monster.energy for _, npcs in sweep() for monster in npcs]
    assert energies, "the sweep produced no monsters"
    assert all(isinstance(e, int) and not isinstance(e, bool) for e in energies)
    assert all(e in range(0, 100) for e in energies)
    # The trap: identical starting energy makes a pack act on the same ticks and move as
    # one organism (CONTRACT-v5 §24.4).
    assert not all(e == 0 for e in energies)
    assert len(set(energies)) > 1
    # Not merely "not all zero" — a real spread over the whole range.
    assert len(set(energies)) > 20
    assert min(energies) < 25 and max(energies) > 75


def test_a_single_level_spawns_monsters_on_different_schedules():
    # Per level, not just in aggregate: six monsters that share one energy are a pack
    # that moves as one organism on that level.
    distinct_counts = []
    for _, npcs in sweep():
        distinct_counts.append(len({monster.energy for monster in npcs}))
    assert max(distinct_counts) > 1
    # The overwhelming majority of levels must have staggered monsters.
    assert sum(1 for c in distinct_counts if c > 1) >= len(distinct_counts) - 1


# ---------------------------------------------------------------------------
# spawn_npcs — hostile maps terminate with fewer NPCs
# ---------------------------------------------------------------------------


def test_spawn_on_a_tiny_level_places_none_and_returns_promptly():
    # Every cell is inside the safe radius of the player start, so nothing is legal.
    level = make_level(["....." for _ in range(5)], player_start=(2, 2))
    started = time.perf_counter()
    npcs = spawn_npcs(random.Random(0), level)
    elapsed = time.perf_counter() - started
    assert npcs == ()
    assert len(npcs) < MONSTERS_PER_LEVEL
    assert elapsed < 1.0, f"spawn_npcs took {elapsed:.3f}s on a 5x5 level"


def test_spawn_on_a_one_cell_level_places_none():
    level = make_level(["."], player_start=(0, 0))
    assert spawn_npcs(random.Random(0), level) == ()


def test_spawn_in_a_narrow_corridor_places_fewer_than_six_and_never_hangs():
    """A 20-cell corridor cannot hold six monsters five cells apart. It must not spin."""
    level = make_level(
        ["#" * 22, "#" + "." * 20 + "#", "#" * 22], player_start=(1, 1)
    )
    started = time.perf_counter()
    results = [spawn_npcs(random.Random(seed), level) for seed in range(50)]
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0, f"50 hostile-map spawns took {elapsed:.3f}s"
    for npcs in results:
        assert len(npcs) < MONSTERS_PER_LEVEL
        # The rules are never relaxed to make the count up.
        for monster in npcs:
            assert chebyshev(monster.position, level.player_start) >= SPAWN_SAFE_RADIUS
        for a, b in itertools.combinations(npcs, 2):
            assert chebyshev(a.position, b.position) >= SPAWN_MIN_SEPARATION
    assert any(npcs for npcs in results), "the corridor should hold at least one monster"


def test_spawn_on_a_level_with_no_passable_cells_at_all():
    level = make_level(["####", "####"], player_start=(0, 0))
    started = time.perf_counter()
    assert spawn_npcs(random.Random(3), level) == ()
    assert time.perf_counter() - started < 1.0


def test_spawn_never_raises_across_a_range_of_hostile_shapes():
    shapes = [
        ["#" * 12] + ["#" + "." * 10 + "#"] + ["#" * 12],
        ["." * 3] * 3,
        ["." * 40],
        ["." for _ in range(40)],
        ["#.#" for _ in range(20)],
    ]
    started = time.perf_counter()
    for rows in shapes:
        level = make_level(rows, player_start=(0, 0))
        for seed in range(10):
            npcs = spawn_npcs(random.Random(seed), level)
            assert len(npcs) <= MONSTERS_PER_LEVEL
    assert time.perf_counter() - started < 2.0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_spawn_is_identical_for_the_same_seed_and_level():
    level = generate_level(7)
    first = spawn_npcs(random.Random(99), level)
    second = spawn_npcs(random.Random(99), level)
    assert first == second
    assert [m.position for m in first] == [m.position for m in second]
    assert [m.energy for m in first] == [m.energy for m in second]
    assert [m.species for m in first] == [m.species for m in second]


def test_different_seeds_generally_produce_different_populations():
    level = generate_level(7)
    populations = {
        tuple(m.position for m in spawn_npcs(random.Random(seed), level))
        for seed in range(20)
    }
    assert len(populations) > 1


def test_spawn_is_deterministic_across_pythonhashseed():
    """A set-iteration-order dependency inside spawn_npcs must fail this."""
    project_root = pathlib.Path(__file__).resolve().parent.parent
    script = (
        "import random\n"
        "from roguelike.generator import generate_level\n"
        "from roguelike.npc import spawn_npcs\n"
        "for seed in (0, 1, 2, 3, 4):\n"
        "    level = generate_level(seed)\n"
        "    npcs = spawn_npcs(random.Random(seed), level)\n"
        "    print([(n.actor_id, n.species.name, n.position, n.energy, n.actor.hp)\n"
        "           for n in npcs])\n"
    )
    outputs = []
    for hash_seed in ("0", "1234"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = hash_seed
        env["PYTHONPATH"] = str(project_root)
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(project_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout.strip())
    assert outputs[0] == outputs[1]
    assert outputs[0] != ""


def test_plan_action_is_deterministic_across_pythonhashseed():
    project_root = pathlib.Path(__file__).resolve().parent.parent
    script = (
        "import random\n"
        "from roguelike.generator import generate_level\n"
        "from roguelike.npc import (NPC, AiState, SPECIES_DATA, Species,\n"
        "                           plan_action, spawn_npcs)\n"
        "level = generate_level(5)\n"
        "npcs = spawn_npcs(random.Random(5), level)\n"
        "occupied = frozenset([n.position for n in npcs] + [level.player_start])\n"
        "for n in npcs:\n"
        "    hunter = NPC(n.actor_id, n.species, n.actor, n.position, n.energy,\n"
        "                 AiState.HUNTING, 0)\n"
        "    action = plan_action(random.Random(1), hunter, level, frozenset(),\n"
        "                         occupied, level.player_start)\n"
        "    print(action.kind.name, action.target)\n"
    )
    outputs = []
    for hash_seed in ("0", "9876"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = hash_seed
        env["PYTHONPATH"] = str(project_root)
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(project_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout.strip())
    assert outputs[0] == outputs[1]
    assert outputs[0] != ""


# ---------------------------------------------------------------------------
# Purity and immutability
# ---------------------------------------------------------------------------


def test_plan_action_mutates_none_of_its_arguments():
    level = make_level(RING)
    monster = make_npc((1, 1), AiState.HUNTING)
    open_doors = frozenset()
    occupied = frozenset({(7, 1), (4, 1)})
    player = (7, 1)

    before = copy.deepcopy((monster, level, open_doors, occupied, player))
    plan_action(random.Random(0), monster, level, open_doors, occupied, player)
    assert (monster, level, open_doors, occupied, player) == before


def test_spawn_npcs_mutates_none_of_its_arguments():
    level = generate_level(4)
    before = copy.deepcopy(level)
    spawn_npcs(random.Random(4), level)
    assert level == before


def test_spawn_npcs_does_not_mutate_species_data():
    before = copy.deepcopy(SPECIES_DATA)
    spawn_npcs(random.Random(4), generate_level(4))
    assert SPECIES_DATA == before


def test_repeated_calls_do_not_cache_or_drift():
    level = make_level(OPEN_ROOM)
    monster = make_npc((1, 2), AiState.HUNTING)
    player = (12, 2)
    results = [
        plan_action(
            random.Random(0), monster, level, NO_DOORS, frozenset({player}), player
        )
        for _ in range(20)
    ]
    assert len(set(results)) == 1


@pytest.mark.parametrize(
    "instance, field",
    [
        (SPECIES_DATA[Species.RAT], "name"),
        (SPECIES_DATA[Species.RAT], "stats"),
        (NpcAction(NpcActionKind.WAIT), "kind"),
        (NpcAction(NpcActionKind.MOVE, (1, 1)), "target"),
    ],
)
def test_frozen_dataclasses_reject_assignment(instance, field):
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, field, None)


@pytest.mark.parametrize(
    "field", ["actor_id", "species", "position", "energy", "ai_state", "memory"]
)
def test_npc_rejects_assignment(field):
    monster = make_npc((1, 1))
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(monster, field, None)


@pytest.mark.parametrize("cls", [SpeciesData, NPC, NpcAction])
def test_every_dataclass_in_the_module_is_frozen(cls):
    assert dataclasses.is_dataclass(cls)
    assert cls.__dataclass_params__.frozen


# ---------------------------------------------------------------------------
# Performance: six hunters on a full 80x22 level
# ---------------------------------------------------------------------------


def test_planning_six_hunters_on_a_full_level_is_well_under_fifty_milliseconds():
    """Reference: ~0.5 ms per hunter (RESEARCH-v5 §8), so ~3 ms for six."""
    level = generate_level(21)
    player = level.player_start
    spawned = spawn_npcs(random.Random(21), level)
    assert len(spawned) == MONSTERS_PER_LEVEL
    hunters = tuple(
        dataclasses.replace(monster, ai_state=AiState.HUNTING) for monster in spawned
    )
    occupied = frozenset([m.position for m in hunters] + [player])

    def one_pass() -> float:
        started = time.perf_counter()
        for hunter in hunters:
            action = plan_action(
                random.Random(0), hunter, level, NO_DOORS, occupied, player
            )
            assert action is not None
        return (time.perf_counter() - started) * 1000

    best = min(one_pass() for _ in range(5))
    assert best < 50.0, f"six hunters took {best:.2f} ms"


def test_wandering_is_cheap_too():
    level = generate_level(22)
    spawned = spawn_npcs(random.Random(22), level)
    occupied = frozenset(m.position for m in spawned)
    far_away = (level.width + 500, level.height + 500)
    started = time.perf_counter()
    for monster in spawned:
        plan_action(random.Random(1), monster, level, NO_DOORS, occupied, far_away)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms < 50.0, f"six wanderers took {elapsed_ms:.2f} ms"


# ---------------------------------------------------------------------------
# Import hygiene and integer arithmetic (CONTRACT-v5 §10 v5, §0.13)
# ---------------------------------------------------------------------------


def _module_path() -> pathlib.Path:
    return pathlib.Path(npc_module.__file__)


def _module_imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                names.add(node.module)
    return names


def test_npc_imports_only_the_seven_permitted_project_modules():
    # roguelike.items joined the permitted set in v6 (CONTRACT-v6 §10 v6): npc.py may
    # import it for DamageType/Resistance, since a species' resistance profile is a fact
    # about the bestiary, not about combat's damage pipeline (§26.3 vs §26.2).
    tree = ast.parse(_module_path().read_text())
    imports = _module_imports(tree)
    project = {name for name in imports if name.split(".")[0] == "roguelike"}
    permitted = {
        "roguelike.stats",
        "roguelike.status",
        "roguelike.level",
        "roguelike.world",
        "roguelike.pathfind",
        "roguelike.fov",
        "roguelike.items",
    }
    assert project <= permitted, sorted(project - permitted)


def test_npc_does_import_items_for_the_resistance_types():
    # Not merely permitted -- required, since resistance_of and SpeciesData.resistances
    # are typed in terms of DamageType/Resistance.
    tree = ast.parse(_module_path().read_text())
    imports = _module_imports(tree)
    assert "roguelike.items" in imports


def test_npc_does_not_import_the_forbidden_modules():
    tree = ast.parse(_module_path().read_text())
    imports = _module_imports(tree)
    for forbidden in (
        "roguelike.combat",
        "roguelike.game",
        "roguelike.render",
        "roguelike.keys",
        "roguelike.events",
        "combat",
        "game",
        "render",
        "keys",
        "events",
    ):
        assert forbidden not in imports, forbidden


def test_npc_never_imports_curses():
    source = _module_path().read_text()
    tree = ast.parse(source)
    assert "curses" not in _module_imports(tree)
    assert "import curses" not in source


def test_npc_imports_are_standard_library_only_outside_the_project():
    tree = ast.parse(_module_path().read_text())
    imports = _module_imports(tree)
    non_project = {name for name in imports if name.split(".")[0] != "roguelike"}
    assert non_project <= {
        "__future__",
        "dataclasses",
        "enum",
        "typing",
        "random",
        "collections.abc",
    }, sorted(non_project)


def test_npc_creates_no_random_instance_of_its_own():
    """CONTRACT-v5 §0.12: randomness is derived by the caller and passed in."""
    source = _module_path().read_text()
    assert "random.Random(" not in source
    assert "Random(" not in source.replace('"Random"', "").replace("'Random'", "")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            assert name not in {"seed", "getrandbits"}, name


def test_npc_contains_no_float_literals():
    tree = ast.parse(_module_path().read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            pytest.fail(f"float literal found in npc.py: {node.value!r}")


def test_npc_contains_no_true_division():
    tree = ast.parse(_module_path().read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            pytest.fail("true division (/) found in npc.py; all arithmetic is integer")


def test_npc_writes_no_pathfinding_of_its_own():
    """No Dijkstra map, no flow field, no path cache (CONTRACT-v5 §24.2).

    Checked structurally rather than by keyword, since the module's own docstring
    explains *why* there is no Dijkstra map: any search of its own would need a priority
    queue or a deque, and the one real search must come from ``pathfind.find_path``.
    """
    tree = ast.parse(_module_path().read_text())
    imports = _module_imports(tree)
    assert "heapq" not in imports
    assert "collections" not in imports
    assert "queue" not in imports

    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "find_path" in called

    # No cached-path state: nothing may be stored between calls.
    assigned_at_module_level = {
        target.id
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    }
    for name in assigned_at_module_level - {"__all__"}:
        value = getattr(npc_module, name, None)
        assert not isinstance(value, (list, set, bytearray)), name


def test_npc_exports_exactly_the_contract_surface():
    assert set(npc_module.__all__) == {
        "Species",
        "SpeciesData",
        "AiState",
        "NPC",
        "NpcActionKind",
        "NpcAction",
        "SPECIES_DATA",
        "PERCEPTION_RADIUS",
        "FORGET_TICKS",
        "MONSTERS_PER_LEVEL",
        "SPAWN_SAFE_RADIUS",
        "SPAWN_MIN_SEPARATION",
        "wants_to_flee",
        "plan_action",
        "spawn_npcs",
        # NEW in v6 (§26.3, T33): how a species takes a damage type.
        "resistance_of",
    }


def test_spawn_npcs_signature_matches_the_contract():
    parameters = list(inspect.signature(spawn_npcs).parameters)
    assert parameters == ["rng", "level", "first_actor_id"]


# --- Fleeing: breaking off a fight you are losing ----------------------------


def _hurt(species: Species, hp: int, state: AiState = AiState.HUNTING) -> NPC:
    data = SPECIES_DATA[species]
    return NPC(1, species, Actor(data.stats, hp), (5, 5), ai_state=state)


def _healthy_player() -> Actor:
    return Actor(Stats(10, 10, 10), derive(Stats(10, 10, 10)).max_hp)


def _flee_rate(npc: NPC, player: Actor, rolls: int = 400) -> float:
    return sum(wants_to_flee(random.Random(s), npc, player) for s in range(rolls)) / rolls


def test_an_unhurt_monster_never_flees():
    full = derive(SPECIES_DATA[Species.JACKAL].stats).max_hp
    assert _flee_rate(_hurt(Species.JACKAL, full), _healthy_player()) == 0.0


def test_a_lightly_hurt_monster_never_flees():
    # A scratch is not a reason to run: the threshold is BADLY_WOUNDED.
    full = derive(SPECIES_DATA[Species.JACKAL].stats).max_hp
    assert _flee_rate(_hurt(Species.JACKAL, full - 1), _healthy_player()) == 0.0


def test_a_badly_hurt_monster_flees_at_roughly_its_species_rate():
    for species in Species:
        data = SPECIES_DATA[species]
        full = derive(data.stats).max_hp
        rate = _flee_rate(_hurt(species, max(1, full // 5)), _healthy_player())
        assert abs(rate * 100 - data.flee_chance) <= 6, (species, rate)


def test_a_monster_does_not_flee_from_an_equally_hurt_player():
    # "It can see I am healthy and it is not" -- both halves matter.
    full = derive(SPECIES_DATA[Species.JACKAL].stats).max_hp
    dying_player = Actor(Stats(10, 10, 10), 1)
    assert _flee_rate(_hurt(Species.JACKAL, max(1, full // 5)), dying_player) == 0.0


def test_an_enraged_monster_never_flees():
    full = derive(SPECIES_DATA[Species.JACKAL].stats).max_hp
    data = SPECIES_DATA[Species.JACKAL]
    angry = NPC(
        1,
        Species.JACKAL,
        Actor(data.stats, max(1, full // 5), (StatusEffect(StatusKind.ENRAGED, 10, 0),)),
        (5, 5),
        ai_state=AiState.HUNTING,
    )
    assert _flee_rate(angry, _healthy_player()) == 0.0


def test_wit_shows_in_the_flee_rates():
    # The jackal is the sharpest animal in the bestiary and disengages most readily;
    # the cave snake barely does at all.
    rates = {s: SPECIES_DATA[s].flee_chance for s in Species}
    assert rates[Species.JACKAL] > rates[Species.RAT] > rates[Species.CAVE_SNAKE]
    assert all(0 <= r <= 100 for r in rates.values())


def test_a_fleeing_monster_moves_further_away():
    level = _corridor(12)
    npc = _hurt(Species.JACKAL, 3, AiState.FLEEING)
    npc = dataclasses.replace(npc, position=(6, 1))
    player = (4, 1)
    action = plan_action(random.Random(0), npc, level, frozenset(), frozenset({player}), player)
    assert action.kind is NpcActionKind.MOVE
    before = max(abs(npc.position[0] - player[0]), abs(npc.position[1] - player[1]))
    after = max(abs(action.target[0] - player[0]), abs(action.target[1] - player[1]))
    assert after > before


def test_a_cornered_fleeing_monster_turns_and_fights():
    # Without this a fleeing monster in a dead end is a permanently harmless
    # punching bag, and an animal with its back to the wall does not behave that way.
    level = _corridor(2)
    npc = dataclasses.replace(_hurt(Species.JACKAL, 3, AiState.FLEEING), position=(1, 1))
    action = plan_action(
        random.Random(0), npc, level, frozenset(), frozenset({(2, 1)}), (2, 1)
    )
    assert action.kind is NpcActionKind.ATTACK


def test_fleeing_draws_no_randomness():
    level = _corridor(12)
    npc = dataclasses.replace(_hurt(Species.JACKAL, 3, AiState.FLEEING), position=(6, 1))
    targets = {
        plan_action(random.Random(s), npc, level, frozenset(), frozenset({(4, 1)}), (4, 1)).target
        for s in range(50)
    }
    assert len(targets) == 1


def _corridor(length: int):
    """A 1-tall open corridor `length` cells long, walled all round."""
    rows = ["#" * (length + 2), "#" + "." * length + "#", "#" * (length + 2)]
    grid = [[{"#": Tile.WALL, ".": Tile.FLOOR}[c] for c in r] for r in rows]
    return Level(len(rows[0]), len(rows), freeze_grid(grid), (), (1, 1), 0)
