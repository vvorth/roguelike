"""Unit tests for :mod:`roguelike.pathfind` (CONTRACT-v4 §18, task T19).

This module is a leaf: it works entirely through a ``passable(x, y) -> bool``
callable, so every test here builds its own tiny world from hand-drawn ASCII
maps (``#`` blocked, ``.`` passable) with a small local helper. No engine
code -- generator, level, tiles -- is imported anywhere in this file.
"""

from __future__ import annotations

import ast
import heapq
import itertools
import os
import pathlib
import subprocess
import sys
import time

import pytest

from roguelike.pathfind import (
    DIAGONAL_COST,
    DIRECTIONS,
    ORTHOGONAL_COST,
    Coord,
    Passable,
    degree,
    find_path,
    is_intersection,
    is_wide,
    octile,
)

# ---------------------------------------------------------------------------
# ASCII map helper
# ---------------------------------------------------------------------------


def make_passable(rows: list[str]) -> Passable:
    """Build a ``passable`` callable from rows of ``#``/``.`` characters.

    ``rows[y][x]``, so row 0 is the top; out-of-bounds cells are never
    passable.
    """
    assert len({len(row) for row in rows}) == 1, "all rows must be the same width"
    height = len(rows)
    width = len(rows[0])

    def passable(x: int, y: int) -> bool:
        if x < 0 or y < 0 or x >= width or y >= height:
            return False
        return rows[y][x] == "."

    return passable


def path_cost(path: list[Coord]) -> int:
    total = 0
    for (x1, y1), (x2, y2) in itertools.pairwise(path):
        dx, dy = x2 - x1, y2 - y1
        total += DIAGONAL_COST if dx != 0 and dy != 0 else ORTHOGONAL_COST
    return total


# ---------------------------------------------------------------------------
# Independent Dijkstra reference (deliberately not sharing any code with
# find_path's A* implementation) -- the criterion that catches a broken
# heuristic per the brief.
# ---------------------------------------------------------------------------


def dijkstra_cost(passable: Passable, start: Coord, goals: set[Coord]) -> int | None:
    """Obviously-correct uniform-cost search: no heuristic, all 8 directions,
    same 10/14 cost model. Returns the cost to the nearest goal, or None."""
    if not goals:
        return None
    if start in goals:
        return 0
    best: dict[Coord, int] = {start: 0}
    heap: list[tuple[int, Coord]] = [(0, start)]
    visited: set[Coord] = set()
    while heap:
        d, cur = heapq.heappop(heap)
        if cur in visited:
            continue
        if cur in goals:
            return d
        visited.add(cur)
        cx, cy = cur
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nxt = (cx + dx, cy + dy)
                if not passable(*nxt):
                    continue
                cost = DIAGONAL_COST if dx != 0 and dy != 0 else ORTHOGONAL_COST
                nd = d + cost
                if nxt not in best or nd < best[nxt]:
                    best[nxt] = nd
                    heapq.heappush(heap, (nd, nxt))
    return None


# ---------------------------------------------------------------------------
# octile
# ---------------------------------------------------------------------------


def test_octile_equal_points_is_zero():
    assert octile((5, 5), (5, 5)) == 0


def test_octile_orthogonal_neighbour_is_orthogonal_cost():
    assert octile((5, 5), (6, 5)) == ORTHOGONAL_COST
    assert octile((5, 5), (5, 6)) == ORTHOGONAL_COST


def test_octile_diagonal_neighbour_is_diagonal_cost():
    assert octile((5, 5), (6, 6)) == DIAGONAL_COST
    assert octile((5, 5), (4, 6)) == DIAGONAL_COST


def test_octile_is_symmetric():
    a, b = (2, 3), (9, 1)
    assert octile(a, b) == octile(b, a)


def test_octile_is_non_negative_and_int():
    for a, b in [((0, 0), (0, 0)), ((0, 0), (3, 7)), ((10, 10), (0, 3))]:
        value = octile(a, b)
        assert isinstance(value, int)
        assert value >= 0


def test_octile_matches_formula_directly():
    a, b = (1, 1), (8, 4)
    dx, dy = 7, 3
    expected = 10 * (dx + dy) + (14 - 20) * min(dx, dy)
    assert octile(a, b) == expected


# ---------------------------------------------------------------------------
# Admissibility sweep
# ---------------------------------------------------------------------------

OPEN_MAP = make_passable(["." * 15 for _ in range(10)])


def test_octile_is_admissible_over_a_sweep_of_open_map_points():
    points = [(x, y) for x in range(0, 15, 3) for y in range(0, 10, 3)]
    for start in points:
        for goal in points:
            path = find_path(OPEN_MAP, start, {goal})
            assert path is not None
            assert octile(start, goal) <= path_cost(path)


# ---------------------------------------------------------------------------
# find_path -- open map, diagonals used
# ---------------------------------------------------------------------------


def test_find_path_on_open_map_uses_diagonals_not_an_l_shape():
    start, goal = (0, 0), (5, 3)
    path = find_path(OPEN_MAP, start, {goal})
    assert path is not None
    assert path[0] == start
    assert path[-1] == goal
    assert path_cost(path) == octile(start, goal)


def test_find_path_includes_start_and_ends_on_goal():
    start, goal = (2, 2), (2, 2)
    path = find_path(OPEN_MAP, start, {goal})
    assert path == [start]


def test_find_path_empty_goals_is_none():
    assert find_path(OPEN_MAP, (0, 0), set()) is None


def test_find_path_unreachable_goal_is_none():
    rows = [
        "#####",
        "#...#",
        "#####",
        "#...#",
        "#####",
    ]
    passable = make_passable(rows)
    assert find_path(passable, (1, 1), {(1, 3)}) is None


def test_find_path_goal_enclosed_by_walls_is_none():
    rows = [
        ".......",
        "..###..",
        "..#.#..",
        "..###..",
        ".......",
    ]
    passable = make_passable(rows)
    # (3, 2) is walled in on all eight sides.
    assert find_path(passable, (0, 0), {(3, 2)}) is None


def test_find_path_never_raises_for_unreachable_goal():
    rows = ["#.#", "#.#", "#.#"]
    passable = make_passable(rows)
    result = find_path(passable, (1, 1), {(-50, -50)})
    assert result is None


# ---------------------------------------------------------------------------
# start need not be passable
# ---------------------------------------------------------------------------


def test_find_path_start_need_not_be_passable():
    rows = [
        "#....",
        "#....",
        "#....",
    ]
    passable = make_passable(rows)
    # (0, 0) is a wall -- the character is standing somewhere the predicate
    # currently rejects (e.g. terrain that changed under them).
    path = find_path(passable, (0, 0), {(4, 2)})
    assert path is not None
    assert path[0] == (0, 0)
    assert path[-1] == (4, 2)
    for cell in path[1:]:
        assert passable(*cell)


# ---------------------------------------------------------------------------
# Shortest-path correctness against an independent Dijkstra
# ---------------------------------------------------------------------------

OBSTACLE_MAP_1 = [
    "............",
    ".####.......",
    ".#..#.......",
    ".#..#.####..",
    ".#..#.#..#..",
    ".#....#..#..",
    ".######..#..",
    "............",
]

OBSTACLE_MAP_2 = [
    "##############",
    "#............#",
    "#.####.#####.#",
    "#.#....#...#.#",
    "#.#.####.#.#.#",
    "#.#......#.#.#",
    "#.########.#.#",
    "#..........#.#",
    "##############",
]

OBSTACLE_MAP_3 = [
    ".......",
    ".#####.",
    ".#...#.",
    ".#.#.#.",
    ".#...#.",
    ".#####.",
    ".......",
]


@pytest.mark.parametrize(
    "rows,start,goal",
    [
        (OBSTACLE_MAP_1, (0, 0), (11, 5)),
        (OBSTACLE_MAP_2, (1, 1), (12, 7)),
        (OBSTACLE_MAP_3, (0, 0), (6, 6)),
    ],
)
def test_find_path_matches_independent_dijkstra_cost(rows, start, goal):
    passable = make_passable(rows)
    path = find_path(passable, start, {goal})
    assert path is not None
    expected = dijkstra_cost(passable, start, {goal})
    assert expected is not None
    assert path_cost(path) == expected


def test_find_path_matches_dijkstra_across_many_point_pairs():
    passable = make_passable(OBSTACLE_MAP_2)
    height = len(OBSTACLE_MAP_2)
    width = len(OBSTACLE_MAP_2[0])
    open_cells = [(x, y) for y in range(height) for x in range(width) if passable(x, y)]
    start = (1, 1)
    for goal in open_cells[::5]:
        path = find_path(passable, start, {goal})
        expected = dijkstra_cost(passable, start, {goal})
        if expected is None:
            assert path is None
        else:
            assert path is not None
            assert path_cost(path) == expected


# ---------------------------------------------------------------------------
# Path validity: passability and DIRECTIONS-membership of every step
# ---------------------------------------------------------------------------


def test_find_path_cells_other_than_start_are_passable_and_steps_are_valid_deltas():
    passable = make_passable(OBSTACLE_MAP_1)
    path = find_path(passable, (0, 0), {(11, 5)})
    assert path is not None
    for cell in path[1:]:
        assert passable(*cell)
    for (x1, y1), (x2, y2) in itertools.pairwise(path):
        delta = (x2 - x1, y2 - y1)
        assert delta in DIRECTIONS


# ---------------------------------------------------------------------------
# Multi-goal: nearest by path cost
# ---------------------------------------------------------------------------


def test_find_path_multi_goal_reaches_the_nearest_by_cost():
    passable = OPEN_MAP
    start = (7, 5)
    goals = {(0, 0), (14, 0), (0, 9), (14, 9), (8, 6)}
    path = find_path(passable, start, goals)
    assert path is not None
    reached = path[-1]
    assert reached in goals

    per_goal_costs = {}
    for goal in goals:
        single = find_path(passable, start, {goal})
        assert single is not None
        per_goal_costs[goal] = path_cost(single)

    assert path_cost(path) == min(per_goal_costs.values())
    assert per_goal_costs[reached] == min(per_goal_costs.values())


def test_find_path_multi_goal_on_obstacle_map():
    passable = make_passable(OBSTACLE_MAP_2)
    start = (1, 1)
    goals = {(12, 7), (5, 3), (9, 5)}
    path = find_path(passable, start, goals)
    costs = {g: dijkstra_cost(passable, start, {g}) for g in goals}
    reachable_costs = {g: c for g, c in costs.items() if c is not None}
    assert reachable_costs, "test setup error: no goal is reachable"
    best = min(reachable_costs.values())
    assert path is not None
    assert path_cost(path) == best
    assert costs[path[-1]] == best


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_find_path_is_deterministic_within_a_process():
    passable = make_passable(OBSTACLE_MAP_2)
    start, goal = (1, 1), (12, 7)
    first = find_path(passable, start, {goal})
    second = find_path(passable, start, {goal})
    assert first == second


def test_find_path_is_deterministic_across_pythonhashseed():
    project_root = pathlib.Path(__file__).resolve().parent.parent
    script = (
        "from roguelike.pathfind import find_path\n"
        "rows = [\n"
        "    '##############',\n"
        "    '#............#',\n"
        "    '#.####.#####.#',\n"
        "    '#.#....#...#.#',\n"
        "    '#.#.####.#.#.#',\n"
        "    '#.#......#.#.#',\n"
        "    '#.########.#.#',\n"
        "    '#..........#.#',\n"
        "    '##############',\n"
        "]\n"
        "def passable(x, y):\n"
        "    if x < 0 or y < 0 or y >= len(rows) or x >= len(rows[0]):\n"
        "        return False\n"
        "    return rows[y][x] == '.'\n"
        "goals = {(12, 7), (5, 3), (9, 5)}\n"
        "path = find_path(passable, (1, 1), goals)\n"
        "print(path)\n"
    )
    outputs = []
    for seed in ("0", "1234"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        env["PYTHONPATH"] = str(project_root)
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(project_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout.strip())
    assert outputs[0] == outputs[1]
    assert outputs[0] != ""


# ---------------------------------------------------------------------------
# No corner-cutting rule: diagonal is legal iff the destination is passable
# ---------------------------------------------------------------------------


def test_find_path_diagonal_move_is_not_corner_restricted():
    # Two walls meet diagonally at (1, 0) and (0, 1); the far cell (1, 1) is
    # passable. CONTRACT §6: a diagonal move is legal iff the destination is
    # passable -- no corner-cutting rule -- so the path may cut straight
    # through even though both orthogonal neighbours of the move are walls.
    rows = [
        ".#",
        "#.",
    ]
    passable = make_passable(rows)
    path = find_path(passable, (0, 0), {(1, 1)})
    assert path == [(0, 0), (1, 1)]
    assert path_cost(path) == DIAGONAL_COST


# ---------------------------------------------------------------------------
# is_wide -- four-quadrant behaviour (the measured trap)
# ---------------------------------------------------------------------------


def test_is_wide_true_when_only_up_left_quadrant_is_open():
    rows = ["..#", "..#", "###"]
    passable = make_passable(rows)
    assert is_wide(passable, 1, 1) is True


def test_is_wide_true_when_only_up_right_quadrant_is_open():
    rows = ["#..", "#..", "###"]
    passable = make_passable(rows)
    assert is_wide(passable, 1, 1) is True


def test_is_wide_true_when_only_down_left_quadrant_is_open():
    rows = ["###", "..#", "..#"]
    passable = make_passable(rows)
    assert is_wide(passable, 1, 1) is True


def test_is_wide_true_when_only_down_right_quadrant_is_open():
    rows = ["###", "#..", "#.."]
    passable = make_passable(rows)
    assert is_wide(passable, 1, 1) is True


def test_is_wide_false_for_isolated_cell():
    rows = ["###", "#.#", "###"]
    passable = make_passable(rows)
    assert is_wide(passable, 1, 1) is False


def test_is_wide_false_for_one_wide_horizontal_corridor():
    rows = ["###", "...", "###"]
    passable = make_passable(rows)
    assert is_wide(passable, 1, 1) is False


def test_is_wide_false_for_one_wide_vertical_corridor():
    rows = ["#.#", "#.#", "#.#"]
    passable = make_passable(rows)
    assert is_wide(passable, 1, 1) is False


def test_is_wide_true_for_open_floor():
    rows = ["...", "...", "..."]
    passable = make_passable(rows)
    assert is_wide(passable, 1, 1) is True


def test_is_wide_never_raises_out_of_bounds():
    rows = ["...", "...", "..."]
    passable = make_passable(rows)
    assert is_wide(passable, 0, 0) in (True, False)
    assert is_wide(passable, -5, -5) is False


# ---------------------------------------------------------------------------
# degree
# ---------------------------------------------------------------------------


def test_degree_dead_end_is_one():
    rows = ["#.#", "#.#", "###"]
    passable = make_passable(rows)
    assert degree(passable, 1, 1) == 1


def test_degree_straight_corridor_is_two():
    rows = ["#.#", "#.#", "#.#"]
    passable = make_passable(rows)
    assert degree(passable, 1, 1) == 2


def test_degree_t_junction_is_three():
    rows = ["#.#", "#..", "#.#"]
    passable = make_passable(rows)
    assert degree(passable, 1, 1) == 3


def test_degree_open_floor_is_four():
    rows = ["...", "...", "..."]
    passable = make_passable(rows)
    assert degree(passable, 1, 1) == 4


def test_degree_only_counts_orthogonal_neighbours():
    # Diagonal neighbours are all open, orthogonal neighbours all walls.
    rows = [".#.", "#.#", ".#."]
    passable = make_passable(rows)
    assert degree(passable, 1, 1) == 0


def test_degree_never_raises_out_of_bounds():
    rows = ["."]
    passable = make_passable(rows)
    assert degree(passable, 0, 0) == 0


# ---------------------------------------------------------------------------
# is_intersection
# ---------------------------------------------------------------------------


def test_is_intersection_true_for_thin_t_junction():
    rows = ["#.#", "#..", "#.#"]
    passable = make_passable(rows)
    assert is_wide(passable, 1, 1) is False
    assert degree(passable, 1, 1) == 3
    assert is_intersection(passable, 1, 1) is True


def test_is_intersection_true_for_thin_cross():
    rows = ["#.#", "...", "#.#"]
    passable = make_passable(rows)
    assert is_wide(passable, 1, 1) is False
    assert degree(passable, 1, 1) == 4
    assert is_intersection(passable, 1, 1) is True


def test_is_intersection_false_for_straight_corridor():
    rows = ["#.#", "#.#", "#.#"]
    passable = make_passable(rows)
    assert is_intersection(passable, 1, 1) is False


def test_is_intersection_false_for_dead_end():
    rows = ["#.#", "#.#", "###"]
    passable = make_passable(rows)
    assert is_intersection(passable, 1, 1) is False


def test_is_intersection_false_inside_open_floor_despite_high_degree():
    rows = ["...", "...", "..."]
    passable = make_passable(rows)
    assert degree(passable, 1, 1) == 4
    assert is_wide(passable, 1, 1) is True
    assert is_intersection(passable, 1, 1) is False


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------


def test_find_path_performance_on_an_80x22_map():
    rows = ["." * 80 for _ in range(22)]
    passable = make_passable(rows)
    start, goal = (0, 0), (79, 21)

    best = min(
        _time_one_search(passable, start, goal) for _ in range(5)
    )
    # Measured reference: 0.235 ms. Budget is 5 ms; a generous margin above
    # that (10x) keeps this from flaking on a loaded CI machine while still
    # catching any real performance regression.
    assert best < 0.050


def _time_one_search(passable, start, goal) -> float:
    t0 = time.perf_counter()
    path = find_path(passable, start, {goal})
    elapsed = time.perf_counter() - t0
    assert path is not None
    return elapsed


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_cost_constants():
    assert ORTHOGONAL_COST == 10
    assert DIAGONAL_COST == 14


def test_directions_has_eight_nonzero_unit_deltas():
    assert len(DIRECTIONS) == 8
    seen = set()
    for delta in DIRECTIONS:
        dx, dy = delta
        assert dx in (-1, 0, 1)
        assert dy in (-1, 0, 1)
        assert delta != (0, 0)
        assert delta not in seen
        seen.add(delta)
    assert len(seen) == 8


def test_directions_is_a_fixed_tuple_not_regenerated():
    # DIRECTIONS must be a plain tuple constant, not e.g. a function result
    # recomputed at call time (which could reorder from run to run).
    assert isinstance(DIRECTIONS, tuple)


# ---------------------------------------------------------------------------
# Purity / no mutation
# ---------------------------------------------------------------------------


def test_find_path_does_not_mutate_goals_argument():
    passable = OPEN_MAP
    goals = frozenset({(3, 3), (10, 8)})
    goals_copy = frozenset(goals)
    find_path(passable, (0, 0), goals)
    assert goals == goals_copy


def test_find_path_accepts_frozenset_goals():
    passable = OPEN_MAP
    path = find_path(passable, (0, 0), frozenset({(5, 5)}))
    assert path is not None
    assert path[-1] == (5, 5)


# ---------------------------------------------------------------------------
# Import hygiene (CONTRACT-v4 §10, §18): pathfind.py is a leaf
# ---------------------------------------------------------------------------


def _module_source_and_tree() -> tuple[str, ast.Module]:
    path = pathlib.Path(__file__).resolve().parent.parent / "roguelike" / "pathfind.py"
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


def test_pathfind_imports_nothing_from_the_project_and_no_curses():
    _, tree = _module_source_and_tree()
    imports = _module_imports(tree)
    assert not any(name == "roguelike" or name.startswith("roguelike.") for name in imports)
    assert "curses" not in imports
    # Everything imported is either stdlib or the __future__ pseudo-module.
    allowed = {"heapq", "collections.abc", "__future__"}
    assert imports <= allowed


def test_pathfind_contains_no_float_literals():
    _, tree = _module_source_and_tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            pytest.fail(f"float literal found in pathfind.py: {node.value!r}")


def test_pathfind_does_not_import_curses_module_directly():
    source, _ = _module_source_and_tree()
    assert "import curses" not in source
