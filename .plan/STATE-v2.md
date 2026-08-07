# STATE — v2 (colours, fog of war, visibility, door constraint)

Resume point for an interrupted orchestration. Resume from the first task not marked `done`.
`in_progress` means "not started" — re-run it from its brief.

v1 is **complete and frozen** — see `.plan/STATE.md` and `.plan/INTEGRATION.md`
(912 tests passing). This file tracks the v2 increment only.

Phase: **COMPLETE.** All six v2 tasks done and verified; integration finished.
Full suite **1300 passed, exit 0, zero skips**. Live colour curses session verified.
See `.plan/INTEGRATION-v2.md`.
Interpreter for all verification: `.venv/bin/python` (Python 3.14.6, pytest 9.1.1).

| Task | Title | Wave | Depends on | Model | Status | Report | Verified |
|---|---|---|---|---|---|---|---|
| T07 | Runtime passability seam | 1 | — | sonnet | **done** | `reports/T07.md` | **yes — 121 passed, exit 0** |
| T08 | Style vocabulary and palette | 1 | — | sonnet | **done** | `reports/T08.md` | **yes — 34 passed, exit 0** |
| T09 | Generator: door constraint | 1 | — | opus | **done** | `reports/T09.md` | **yes — 414 passed, exit 0** |
| T10 | Permissive field of view | 2 | T07 | opus | **done** | `reports/T10.md` | **yes — 97 passed, exit 0** |
| T11 | Renderer: colour and fog | 2 | T07, T08 | sonnet | **done** | `reports/T11.md` | **yes — 101 passed, exit 0** |
| T12 | Game state, FOV per move, bump-to-open | 3 | T07–T11 | opus | **done** | `reports/T12.md` | **yes — 167 passed, exit 0** |

A task is `done` only when its report exists **and** the orchestrator has personally run its
verification command and seen it exit 0.

## Verification commands

| Task | Command |
|---|---|
| T07 | `.venv/bin/python -m pytest tests/test_world.py tests/test_movement.py -q < /dev/null` |
| T08 | `.venv/bin/python -m pytest tests/test_style.py -q < /dev/null` |
| T09 | `.venv/bin/python -m pytest tests/test_generator.py -q < /dev/null` |
| T10 | `.venv/bin/python -m pytest tests/test_fov.py -q < /dev/null` |
| T11 | `.venv/bin/python -m pytest tests/test_render.py -q < /dev/null` |
| T12 | `.venv/bin/python -m pytest tests/test_game.py -q < /dev/null` |
| Phase 5 | `.venv/bin/python -m pytest -q < /dev/null` + live colour curses session |

## Files frozen for v2 — nobody may edit

`roguelike/level.py`, `roguelike/keys.py`, `tests/test_level.py`, `tests/test_keys.py`.
Orchestrator-owned: `main.py`, `tests/test_integration.py`, `roguelike/__init__.py`,
`pytest.ini`, everything under `.plan/` except `.plan/reports/TNN.md`.

## Log

- Research phase complete: `.plan/RESEARCH-v2.md`. Measured on the v1 build —
  door constraint violated by **13.1%** of doors (1090/8318 over 400 seeds) plus 2525
  door-adjacent-to-door; permissive FOV is a **strict superset** of shadowcasting
  (+12 cells, 0 the other way) at **15.29 ms/move**; the 100× faster hybrid **over-shows 6.8%**
  and was rejected for leaking map information; radius 20 vs 8 differs by only 27 cells
  because walls dominate indoors; terminal confirmed `COLORS=256`, `PAIRS=32767`.
- User decisions: turn-based FOV per move · doors **opaque with open/closed state** ·
  colours list ends at 6 · door fix **B (reroute)**.
- Consequence identified: open/closed doors break the v1 invariant that walkability is a pure
  function of `Level`. Resolved with a new `roguelike/world.py` seam (CONTRACT-v2 §13).
  Decided bump-to-open rather than an `o` command — no new keybinding, no new command kind.
- CONTRACT-v2.md written and **frozen**: amends §0, §1, §3 (G9a–d, G4a), §4 (renderer
  replaced), §6, §7, §9, §10, §11; adds §13 world, §14 fov, §15 style.
- PLAN-v2.md written: 6 tasks, 3 waves, model assignments per policy.
- Phase 3 complete: T07–T12 briefs written to `.plan/tasks/`.
- Gate passed. Wave 1 dispatched: T07 (sonnet), T08 (sonnet), T09 (opus).
- Wave 1 verified by orchestrator: T07 121 passed, T08 34 passed, T09 414 passed — all exit 0.
  Reports present. No contract deviations reported by any of the three.
- **Orchestrator's own independent re-measurement of the door defect, 400 seeds:**
  G9b/G9c violations **1090 → 0**; doors adjacent to a door **2525 → 0**; multi-room rooms
  with no door **72 → 0**. Door count 8318 → 6408 (fewer but all well-formed).
- **Orchestrator's own independent G8 check:** 900 levels across 3 map sizes and 300 seeds —
  **zero connectivity failures**. Room density 10.85/level at 80×22 (v1 was 10.70), so the
  BFS router did not thin the maps. Determinism spot-check passes.
- T09 rewrote corridor routing entirely: BFS over free cells outside room "ring boxes", with
  rooms as graph nodes reachable only through doors. G9a–d and G4a are now *structural*
  rather than checked after the fact. Consequence recorded by the worker: a room whose wall
  ring touches another's may be **dropped** (measured 0 per level at all ordinary sizes), so
  only `1 <= len(rooms) <= max_rooms` may be asserted, never equality.

## EXPECTED TRANSITIONAL FAILURE — full suite is red until T12

`tests/test_game.py::test_move_onto_a_door_is_allowed` **fails** (1 failed, 1032 passed).

This is correct and planned, not a regression. It is a **v1** test asserting that walking onto
a door moves the player. Under CONTRACT-v2 §6, `try_move` defaults to treating every door as
**closed**, so the move is now blocked and becomes a bump-to-open. `tests/test_game.py` is
owned by **T12** (Wave 3), whose brief requires rewriting it. The suite returns to green when
T12 lands. No other test in the 1033 is affected.

- Wave 2 dispatched: T10 (opus), T11 (sonnet).
- **T11 done and verified: 101 passed, exit 0.** Report present, no contract deviations.
  Worker flagged one real stdlib nuance, accepted: `curses.init_pair` raises `ValueError`
  (not `curses.error`) when curses was never initialised, because it validates the pair
  number against `curses.COLOR_PAIRS` which is `-1` pre-init. `init_colors` catches both,
  so the no-TTY guarantee holds.
- **T10 was interrupted mid-task by a session limit**, before writing its report. Both
  `roguelike/fov.py` and `tests/test_fov.py` had already landed on disk. Orchestrator ran the
  verification: **1 failed, 85 passed** —
  `test_sight_does_not_leak_through_a_long_diagonal_wall`.
  Analysis: the level is the main diagonal wall with the eye at its apex. The ray to the
  sample point `(5, 2)` crosses lattice point `(2, 1)` exactly; its diagonally-flanking cells
  are `(1, 1)` (wall) and `(2, 0)` (floor). CONTRACT-v2 §14.1 blocks a lattice crossing only
  when **both** flanking cells are opaque, so the ray passes and `(4, 2)` is legitimately
  visible — i.e. the blanket test assertion looks over-strict, not the algorithm. A 1-cell
  diagonal wall is deliberately permeable at its lattice points on the shallow side; that is a
  direct consequence of the contract's rule.
  Agent **resumed from its transcript** with this analysis, instructed to verify it
  independently and to fix `fov.py` instead if it finds a genuine leak — explicitly told not
  to just make the assertion pass.
- **T10 done and verified: 97 passed, exit 0.** The worker retraced the geometry with an
  independent `Fraction`-based tracer sharing no code with `fov.py`, confirmed the
  implementation was right and the assertion wrong, and **replaced** the case with three
  tests (full-diagonal separation asserting `y - x <= 1`; an apex-eye test guarding the
  misreading; a named test pinning the corner-touch exemption) rather than deleting it.
  It chose **exact integer Amanatides–Woo DDA in doubled coordinates** — no floating point
  at all, so the lattice-point test the diagonal rule depends on is exact.
  Measured **2.06 ms/call** at r=20 on 80×22, against F9's ~15 ms estimate; the gain is the
  integer DDA plus a per-call local snapshot of `is_transparent` (scoped to the call, not a
  cache). Orchestrator re-ran its own F1/F6/F7/F8 checks against the final code: all hold.

### Accepted deviations and known gaps from T10 (orchestrator decisions)

- **Diagonal-corner rule applies only at lattice points strictly interior to the segment
  (`0 < t < 1`), not at an endpoint the segment terminates on.** §14.1 says "passes
  through"; a segment that *ends* at a lattice point does not pass through it. **Accepted.**
  Applying the rule at endpoints punches all four corners out of every room's wall ring —
  exactly the ragged-wall artifact F6 names as a defect, and F6 is an explicit stated
  guarantee. Visible cost: the single cell diagonally beyond a two-wall join is visible.
  Verified by the worker that nothing beyond it is (`max(y - x) == 1` on a full diagonal).
- **F4 is emergent, not a theorem.** The eight sample points are on the target's boundary
  and exclude its centre, so a target reachable only through a slit narrower than its own
  half-width can have a clear centre ray and eight blocked ones. Found **1 case in 760
  adversarial 45%-wall-noise sweeps; zero across 360 generated-level origins**. The worker
  deliberately did **not** add a ninth centre sample to force F4, because that would reveal
  cells the §14.1 rule does not grant — over-showing, which F9 calls a defect. **Accepted
  and recorded as a known gap.** Orchestrator's own F4 check: 0 violations over 489 cells.
- `compute_visible` includes the origin even when out of bounds (F1 beats F2). Unreachable
  from `game.py` — `Level.__post_init__` forbids an out-of-bounds `player_start`.

## SECOND EXPECTED TRANSITIONAL FAILURE — `tests/test_integration.py` no longer collects

`ImportError: cannot import name 'render_to_lines' from 'roguelike.render'`.

Expected and planned. T11 legitimately replaced that function per CONTRACT-v2 §4 (a
`list[str]` cannot carry colour). `tests/test_integration.py` is **orchestrator-owned** and is
rewritten in Wave 4. Until then the *full* suite cannot collect; per-task suites all pass.

- Wave 3 dispatched: T12 (opus). Verified: 167 passed, exit 0. No contract deviations; T12
  checked all nine modules' signatures at runtime and found them all matching CONTRACT-v2.
- Phase 5 complete. `tests/test_integration.py` rewritten by the orchestrator for v2
  (170 tests). `main.py` needed no change — it imports only `game.play`, whose signature is
  unchanged. **Full suite 1300 passed, exit 0, zero skips** (v1 was 912).
- Live colour curses session verified under an 80×24 pty: starts, paints, responds, exits 0
  on `q`, termios restored byte-identically with ICANON and ECHO back on. **All four palette
  colours reached the wire (250, 238, 180, 231).** Fog of war confirmed live — opening frame
  painted only **91 of 1760** map cells, rising to 106 after a 9-key walk. Bump-to-open
  confirmed live — the door glyph changed `+` → `'`.
- `.plan/INTEGRATION-v2.md` written. **Both transitional failures above are resolved.**
- Four bugs were found in the orchestrator's own throwaway verification tooling during
  Phase 5 (colour scan omitting per-key redraws; emulator fed in chunks that split escape
  sequences; `.pop()` on a frozenset; an assertion that walking always reveals more, which is
  false inside a single room at radius 20). In every case the product was verified correct
  headlessly before the tooling was touched. None was a defect in the engine.
