# INTEGRATION v2 — colours, fog of war, visibility, door constraint

Status: **complete**. Full suite **1300 passed, exit 0, zero skips**. Live colour curses
session verified. v1 was 912 tests; v2 adds 388.

## What was assembled

Three new modules, four amended, on top of the finished v1 engine.

```
roguelike/world.py       T07  NEW    is_passable / is_transparent / is_closed_door
roguelike/style.py       T08  NEW    Visibility, Role, Attr, role_for, attr_for
roguelike/fov.py         T10  NEW    compute_visible — permissive FOV
roguelike/tiles.py       T07  amend  + DOOR_OPEN_CHAR
roguelike/movement.py    T07  amend  open_doors param, blocked_by_door
roguelike/generator.py   T09  amend  corridor routing rewritten (door constraint)
roguelike/render.py      T11  rewrite Cell frame, colour, three visibility states
roguelike/game.py        T12  amend  explored/visible/open_doors, FOV per move, bump-to-open
roguelike/level.py            frozen  unchanged from v1
roguelike/keys.py             frozen  unchanged from v1
main.py                  orch  unchanged — imports only game.play
tests/test_integration.py orch rewritten for v2
```

| Suite | v1 | v2 |
|---|---|---|
| `test_level.py` | 85 | 85 |
| `test_keys.py` | 111 | 111 |
| `test_generator.py` | 381 | **414** |
| `test_movement.py` | 67 | **95** |
| `test_render.py` | 58 | **101** |
| `test_game.py` | 115 | **167** |
| `test_world.py` | — | **26** |
| `test_style.py` | — | **34** |
| `test_fov.py` | — | **97** |
| `test_integration.py` | 95 | **170** |
| **Total** | **912** | **1300** |

## Run it

```bash
.venv/bin/python main.py --seed 1234
```

Move with arrow keys, `hjkl` (`yubn` diagonals), or `1`–`9`. **Walk into a closed door to open
it** — that costs a turn and does not move you. Quit with `q`.

## The four requirements, and how each was met

### Visibility and explored area
1. Nothing explored initially — the map opens blank apart from where you stand. Measured live:
   **91 of 1760 cells painted** in the opening frame.
2. Radius 20, carried on `GameState.radius` as a parameter, not a constant.
3. Unobstructed cells within radius become visible and are accumulated into `explored`.
4. Visibility drives representation — three states, three treatments.
5. **"Visible if any side or corner is in direct eye sight"** — implemented as permissive FOV
   with eight sample points per cell. This is the requirement that chose the algorithm; see
   below.
6. Explored ground stays on screen, dimmed, so it reads as memory rather than sight.

### Colours
1. Player white **bold** (231 + `A_BOLD`).
2. Unexplored **not drawn at all** — a blank space, never a dimmed glyph.
3. Explored — darker shade (terrain 238, door 94).
4. Visible — natural colour (terrain 250, door 180).
5. Walls and floor light gray.
6. Doors light brown.

### Door constraint
Every door is now embedded in a wall run with passage on the perpendicular axis.

### Bugs found and fixed beyond the brief
Doors adjacent to other doors (2525 of them) and rooms reachable only through unmarked corner
gaps — both surfaced while measuring the reported defect, both now impossible.

## The door defect — measured before and after

Orchestrator's own measurement, 400 seeds, independent of the worker's tests:

| | v1 build | v2 build |
|---|---|---|
| Doors | 8318 | 6408 |
| **Malformed (G9b/G9c violations)** | **1090 (13.1%)** | **0** |
| **Doors adjacent to another door** | **2525** | **0** |
| **Multi-room rooms with no door** | **72** | **0** |

Fix **B (reroute)** was mandated over the one-line fix A, which was measured to leave 6.3% of
rooms with no door — trading a malformed door for a missing one. T09 replaced centre-to-centre
L-doglegs with BFS over free cells outside room "ring boxes", with rooms as graph nodes
reachable only through doors, making the guarantees **structural** rather than checked after
the fact.

Connectivity was the silent-failure risk in that rewrite. Orchestrator's independent check:
**900 levels across 3 map sizes and 300 seeds — zero G8 failures.** Room density is unchanged
(10.85/level at 80×22, v1 was 10.70), so the router did not thin the maps.

## FOV — why permissive, measured

Rule #5 tests **sides and corners**; recursive shadowcasting tests **centres**. Three
algorithms were prototyped on real levels before any task was written:

| Algorithm | Cells (seed 1234, r=20) | Time/move | Faithful |
|---|---|---|---|
| Recursive shadowcasting | 130 | 0.10 ms | No — misses 12 cells |
| **Permissive** | **142** | **2.06 ms** (shipped) | **Yes** |
| Hybrid shadowcast + wall reveal | ~142 | 0.16 ms | No — **over-shows 6.8%** |

The hybrid was rejected for **leaking map information around corners the player cannot see**.
The shipped implementation came in at **2.06 ms**, far under the contract's ~15 ms estimate,
via exact integer Amanatides–Woo DDA in doubled coordinates — no floating point anywhere, so
the lattice-point test the diagonal-corner rule depends on is exact. Nothing was approximated
to get there.

Orchestrator's independent verification of the guarantees:

| Guarantee | Result |
|---|---|
| F6 no ragged walls | **0** wall-ring cells missing, corners included |
| F7 closed door opaque | far room **0** cells visible; opening it reveals **9** |
| F5 pillar shadow | 9 cells hidden behind |
| F4 superset of centre-only | **0 violations over 489 cells** |
| F1 / F8 | origin always in; `r=0` → `{origin}`; negative → `ValueError` |

## End-to-end verification (what no individual worker could run)

`tests/test_integration.py`, 170 tests, all crossing at least two task boundaries:

1. **Connectivity re-derived independently** — own BFS, not the generator's self-check.
2. **Door constraint re-derived independently** — G9b/G9c/G9d asserted from the finished grid.
3. **Scripted walk through the real key abstraction** — after every keystroke the player is in
   bounds, not in a wall, and on currently-**passable** terrain (which now depends on which
   doors are open).
4. **Turn accounting with three outcomes** — `turns == accepted moves + doors opened`, asserted
   after every keystroke, with a guard that the walk was actually blocked sometimes.
5. **Fog of war** — the map starts <50% explored; `explored` never shrinks and always contains
   `visible`; crossing a door strictly reveals more.
6. **`game.step` delegates rather than reimplements** — `state.visible` compared against a
   direct `compute_visible` call at every step.
7. **A rejected move does not recompute FOV** — asserted by identity, not equality, so a
   recompute returning an equal set would still fail.
8. **Bump-to-open end to end** — BFS to a reachable closed door, then: position unchanged,
   exactly one turn, exactly one door added, FOV recomputed, and the next move walks through.
9. **The unexplored map does not leak** — every unexplored cell renders as a space.
10. **Colour rules** — explored is numerically darker than visible for both roles; player bold.
11. **Cross-process determinism** — same seed, same frame, under three `PYTHONHASHSEED` values.
12. **Whole-stack immutability** — deepcopy comparison after a 72-keystroke walk.

### Live curses session

Real 80×24 pty, out of band (the pytest suite must never initialise curses).

| Check | Result |
|---|---|
| Starts, paints, exits 0 on `q` | pass |
| **Palette on the wire** | **250, 238, 180, 231** — all four, exactly the spec |
| **Fog of war** | opening **91/1760** cells painted; **106** after walking |
| **Bump-to-open live** | door glyph `+` → `'` after walking into it |
| Turn counter | `Turns: 9` after a 9-key walk |
| termios restored byte-identically | pass |
| ICANON / ECHO restored | pass |
| No traceback | pass |

Opening frame (seed 1234) — everything beyond the starting room is genuinely blank:

```
#############
#...........#
#...........#
#.....@.....#
#...........#
#...........#
###########+#

Seed: 1234  Pos: (6, 5)  Turns: 0  [q] quit
```

## Deviations found

**None from any worker.** T12 — the only worker to see the whole v2 stack — verified every
public signature, field order, default and `__all__` against CONTRACT-v2 at runtime and found
them all matching, with no workaround written anywhere.

Two accepted readings, both recorded in `.plan/STATE-v2.md`:

- **T10, diagonal-corner rule at endpoints.** §14.1 blocks a segment that "passes through" a
  lattice point; a segment that *ends* at one does not pass through it. Applying the rule at
  endpoints punches all four corners out of every room's wall ring — the exact ragged-wall
  artifact F6 names as a defect. F6 is the explicit guarantee, so it wins. Cost: the single
  cell diagonally beyond a two-wall join is visible; verified nothing beyond it is.
- **T09, rooms may be dropped.** A room whose wall ring touches another's admits no legal door
  under the tightened G9, so it is dropped rather than left stranded. Measured **0 per level**
  at every ordinary map size. Assert only `1 <= len(rooms) <= max_rooms`, never equality.

### Bugs found in the orchestrator's own tooling (not the project)

Recorded because each one initially looked like a product bug:

1. **Smoke test scanned `raw + tail` for colours**, omitting the per-key redraws where explored
   cells are first drawn — so colour 238 appeared absent when it was simply never sampled.
2. **Screen emulator fed in 0.12 s chunks**, splitting escape sequences across `feed()` calls
   and corrupting its state.
3. Integration test called `.pop()` on a `frozenset`.
4. Integration test asserted "walking reveals more of the map" — **wrong**: at radius 20 a room
   is fully visible the moment you stand in it, so walking *within* one room legitimately
   reveals nothing. Replaced with the property that actually holds: crossing a door reveals
   more.

In every case the product was verified correct headlessly before the tooling was changed.

## Known gaps

1. **F4 is emergent, not a theorem.** The eight sample points are on the target's boundary and
   exclude its centre, so a target reachable only through a slit narrower than its own
   half-width can have a clear centre ray and eight blocked ones. Found **1 case in 760
   adversarial 45%-wall-noise sweeps; zero across 360 generated-level origins**. A ninth centre
   sample would force F4 but would reveal cells §14.1 does not grant — over-showing, which F9
   calls a defect. Left as is, deliberately.
2. **The corner-touch cell is visible through a diagonal wall join** (deviation above). If
   play-testing makes it look wrong, the fix is a contract decision, not a code change — the
   alternative costs F6. Pinned by name in `tests/test_fov.py` so any change is loud.
3. **Bumping a door has thin feedback.** You do not move; only the `+` → `'` glyph and the turn
   counter show the turn was spent. There is no message log (out of scope).
4. **A nearly-blank opening frame is correct**, not a bug — but a tester expecting v1's fully
   drawn map will report it.
5. **`bool` dimensions** (v1 gap, unchanged): `generate_level(1, width=True)` raises
   `ValueError` rather than `TypeError`.
6. **No terminal-size check and no `KEY_RESIZE` handling** (v1 gaps, unchanged). `draw` clips
   safely every frame, so both degrade rather than fail.
7. **Two contract numbers are now stale, both in the safe direction:** §14.2 F9 estimates
   ~15 ms per FOV (actual ~2 ms), and §3's door baseline describes the defect that is now
   fixed. Neither affects behaviour.
8. **Scope held.** No monsters, combat, items, inventory, save-load, multiple levels, stairs,
   sound. No `o`-to-open command — bump-to-open covers it. No light sources or per-cell
   brightness beyond the three visibility states.

## Verify from scratch

```bash
.venv/bin/python -m pytest          # 1300 passed
.venv/bin/python main.py --seed 1234
```

The `.venv` is required: system `python3` is 3.9.6, below the 3.10 floor. It holds Python
3.14.6 and pytest 9.1.1.
