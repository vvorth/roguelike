# STATE — v4 (diagonals, multi-turn activities, pathfinding, auto-navigation)

Resume point for an interrupted orchestration. Resume from the first task not marked `done`.
`in_progress` means "not started" — re-run it from its brief.

v1–v3 are **complete and frozen** — see `.plan/INTEGRATION.md`, `-v2.md`, `-v3.md`
(1666 tests passing). This file tracks the v4 increment only.

Phase: **COMPLETE.** All four v4 tasks done and verified; integration finished.
Full suite **1982 passed, exit 0, zero skips**. Live curses session verified.
See `.plan/INTEGRATION-v4.md`.
Interpreter for all verification: `.venv/bin/python` (Python 3.14.6, pytest 9.1.1).

| Task | Title | Wave | Depends on | Model | Status | Report | Verified |
|---|---|---|---|---|---|---|---|
| T18 | Diagonal keys and activity messages | 1 | — | sonnet | **done** | `reports/T18.md` | **yes — 233 passed, exit 0** |
| T19 | Pathfinding and map topology | 1 | — | sonnet | **done** | `reports/T19.md` | **yes — 53 passed, exit 0** |
| T20 | Planning passability and activity planners | 2 | T19 | opus | **done** | `reports/T20.md` | **yes — 93 passed, exit 0** |
| T21 | Activities, travel and the paced loop | 3 | T18–T20 | opus | **done** | `reports/T21.md` | **yes — 374 passed, exit 0** |

A task is `done` only when its report exists **and** the orchestrator has personally run its
verification command and seen it exit 0.

## Verification commands

| Task | Command |
|---|---|
| T18 | `.venv/bin/python -m pytest tests/test_keys.py tests/test_events.py -q < /dev/null` |
| T19 | `.venv/bin/python -m pytest tests/test_pathfind.py -q < /dev/null` |
| T20 | `.venv/bin/python -m pytest tests/test_world.py tests/test_activity.py -q < /dev/null` |
| T21 | `.venv/bin/python -m pytest tests/test_game.py -q < /dev/null` |
| Phase 5 | `.venv/bin/python -m pytest -q < /dev/null` + live curses session |

## Files frozen for v4 — nobody may edit

`roguelike/tiles.py`, `level.py`, `generator.py`, `render.py`, `fov.py`, `movement.py`,
`style.py`, `dungeon.py` and their test files. v4 touches input, planning and control flow
only — the map, the renderer and the physics are untouched.

Orchestrator-owned: `main.py`, `tests/test_integration.py`, `roguelike/__init__.py`,
`pytest.ini`, everything under `.plan/` except `.plan/reports/TNN.md`.

**Lesson applied from v3, and verified rather than assumed.** In v3 I froze
`tests/test_world.py` while it asserted `set(Tile) == {WALL, FLOOR, DOOR}`, which v3
necessarily broke, stranding a test no worker could repair. Before freezing anything here I
searched every frozen suite for surfaces v4 changes. Findings:

- `CommandKind` and `EventKind` are referenced **only** in `tests/test_keys.py` and
  `tests/test_events.py` (**T18 owns both**), `tests/test_game.py` (**T21**) and
  `tests/test_integration.py` (**orchestrator**). No frozen suite mentions either.
- The only `__all__` assertions in frozen suites — `tests/test_render.py`, `tests/test_fov.py`,
  `tests/test_dungeon.py` — each pin **their own** module, and v4 changes none of those three.
- `world.py`'s public surface is asserted in `tests/test_world.py`, which **T20 owns**.

**No frozen file contains an assertion v4 invalidates.**

## Expected transitional breakage — planned, each with an owner

| Breaks | Cause | Fixed by |
|---|---|---|
| `tests/test_keys.py` — member count, `HJKL` are `UNKNOWN` | two kinds, four bindings | T18 (same owner) |
| `tests/test_events.py` — `EventKind` member count | eight new kinds | T18 (same owner) |
| `tests/test_world.py` — module public surface | one new predicate | T20 (same owner) |
| `tests/test_game.py` — `GameState` field list | two new fields | T21 (same owner) |
| `tests/test_integration.py` | the above | orchestrator, wave 4 |

## Log

- Research complete: `.plan/RESEARCH-v4.md`. Measured on the v3 build —
  **numpad `1/3/7/9` and `yubn` diagonals already existed since v1**, so only the Shift
  bindings are new; shifted arrows *do* reach curses as `KEY_SR` 337 / `KEY_SF` 336 /
  `KEY_SRIGHT` 402 / `KEY_SLEFT` 393, probed through a pty; **`stdscr.timeout(100)` supplies
  both the 10-turns-per-second cap (9 ticks in 1.0 s) and instant cancellation (a waiting key
  returns in 0.00 ms)** — one mechanism, no sleep; A\* with integer 10/14 costs takes
  **0.235 ms** across a full level and diagonals cut a path from 65 to 58 steps, so re-planning
  every turn is affordable and caching is forbidden; frontier auto-explore using **only
  `explored`** reached **100% of walkable tiles on all five sample seeds**, ~180 turns, always
  ending by frontier exhaustion; and a purely local **four-quadrant 2×2 `is_wide` test**
  distinguishes corridor from room with **99.98%** accuracy and zero room cells misread — a
  two-quadrant version scores only 96.4%, a measured trap now pinned in the contract.
- Two door details are load-bearing for auto-explore and are stated as such: a closed door must
  be **traversable when planning** (else frontiers behind doors are unreachable and explore
  stalls in the first room) and is **itself a frontier** (else explore walks past shut doors and
  declares the level done).
- User decisions: auto-explore **stops on the current level** · `>` with no known staircase
  **just says so** · opening a door **does not interrupt** an activity.
- Consequence recorded: with decision 3, `interruption(before, after)` returns `None` in every
  case today. The seam ships called-and-tested but with no live condition, which is the honest
  state of it.
- Smaller decisions taken by the orchestrator: each stop condition gets its own message; a
  cancelling keypress is **consumed** by the cancellation and does not also act as a command;
  activities do not survive a level change; `w` followed by a non-direction key clears the
  prefix and consumes the key.
- CONTRACT-v4.md written and **frozen**: adds §0.10 (timing only in `run`), §0.11 (integer path
  costs), §18 `pathfind`, §19 `activity`; amends §5 (input), §13 (`is_planning_passable`),
  §16 (events), §7 (activities and the paced loop), §9, §10, §11.
- PLAN-v4.md written: 4 tasks, 3 waves, model assignments per policy.
- Phase 3 complete: T18–T21 briefs written to `.plan/tasks/`.
- Gate passed. Wave 1 dispatched: T18 (sonnet), then T19 (sonnet).
- **Both Wave 1 workers were cut off by API errors after finishing their code but before
  writing their reports.** Both were resumed from transcript to write the report only, with an
  explicit instruction not to redo the implementation. Neither task was marked `done` on code
  alone — the standing rule (report present AND orchestrator ran the verification) held.
- **T18 done and verified: 233 passed, exit 0.** Orchestrator independently checked the named
  trap and the mapping: `curses.KEY_SR` → (1,-1) north-east and `KEY_SF` → (-1,1) south-west,
  **not inverted**; `KEY_SRIGHT` → (1,1), `KEY_SLEFT` → (-1,-1); `K`/`L`/`J`/`H` give the same
  four diagonals and each equals its legacy counterpart (`K` == `u` == `9`, and so on);
  `E` → AUTO_EXPLORE, `w` → WALK_PREFIX with dx == dy == 0, while `e` and `W` stay UNKNOWN;
  CommandKind has 7 members, EventKind 16, every one with wording; all v1–v3 bindings intact.
- T18 folded the Shift diagonals into the **existing per-delta rows** rather than adding new
  ones, so a Shift key and its legacy counterpart produce value-equal `Command`s. It renamed
  the private `_STAIR_BINDINGS` to `_NO_ARG_BINDINGS`; private, so not contract-visible.
- **T19 code verified by the orchestrator, report pending: 53 passed, exit 0.** Independent
  checks: the **four-quadrant `is_wide` trap is avoided** — True for a block open in each of
  the four quadrants in turn, False for both 1-wide corridors; **shortest-path correctness
  confirmed against an orchestrator-written Dijkstra** on three obstacle maps (106/106,
  286/286, 106/106), which is the check that separates "returns a path" from "returns the
  shortest path"; **deterministic** across `PYTHONHASHSEED` 0/1/999 in separate processes; no
  float arithmetic; **0.463 ms** per full-level search against the 5 ms budget.
- Known transitional failure, correctly diagnosed by both workers and left alone by both:
  `tests/test_game.py::test_no_extra_command_kind_was_invented` hardcodes the old five-member
  `CommandKind` set and is invalidated by T18. `tests/test_game.py` is **T21's** file per §9 v4.

- **T19 done: report written, 53 passed, exit 0.** Two facts it recorded that T20 depends on:
  `find_path` **never calls `passable` on `start`** (only on cells expanded into), so a start
  cell that an explored-only predicate would reject does not block the search; and the
  multi-goal heuristic is `min(octile(node, g) for g in goals)` recomputed per node, so cost
  scales as O(nodes x |goals|) — fine for a frontier set on 80x22, but not free.
  Ties break on `(f_score, coord, g_score)` with integer scores, so ordering is total and the
  same inputs always return the identical path — which is what makes per-turn re-planning safe.
- Full suite at end of Wave 1: **1780 passed, 1 failed** — the single failure is
  `tests/test_game.py::test_no_extra_command_kind_was_invented`, T21's file, as planned.
- Wave 2 dispatched: T20 (opus).

- **T19's report file was missing.** Its resumed run returned the report verbatim in its reply
  but made **zero tool calls**, so nothing was written to disk. **T20 caught this and flagged
  it** — exactly the right behaviour, reporting rather than reaching outside its ownership.
  The orchestrator transcribed the agent's verbatim text into `.plan/reports/T19.md` with a
  note at the top saying so. The record is the agent's own words; only the writing was mine.
- **T20 done and verified: 93 passed, exit 0** (38 world + 55 activity). Frozen source modules
  confirmed untouched.
- **Orchestrator's own end-to-end auto-explore run**, written independently of T20's tests and
  driving the real `step`: **100.0% of walkable tiles on seeds 1234, 7 and 42** (180 / 154 /
  212 turns), every run ending by frontier exhaustion. `is_planning_passable` confirmed to
  differ from `is_passable` on exactly the closed door.
- **Both silent failure modes confirmed catchable.** Re-running my own driver with the *wrong*
  predicate (`is_passable` instead of `is_planning_passable`) collapses coverage to **12.5% in
  0 turns** — the "stalls in the first room" mode, exactly as predicted in research. T20
  independently measured 8–18% for the same mutation, and verified its no-cheating tests
  **fail** against a deliberately cheating implementation rather than merely passing against
  an honest one.
- T20 raised one item T21 must honour: **`advance` must subtract the player's own cell from the
  frontier goal set**, or `find_path` returns `[start]`, there is no `path[1]`, and the
  activity stalls silently. Does not arise at radius 20 today, but it is a one-line guard.
- Wave 3 dispatched: T21 (opus).

- **T21 done and verified: 374 passed, exit 0. Full suite 1963 passed, exit 0** — including
  `tests/test_integration.py`, which did **not** break: both new `GameState` fields are
  appended with defaults, so every v3 construction in it still works. Frozen source modules
  confirmed untouched; `game.py` contains no clock (the only `sleep`/`time` strings are in
  comments).
- T21 ran **six mutation checks** against its own suite, all caught — including feeding the
  cancelling key to `step`, `step` not clearing a running activity, `timeout(50)` instead of
  100, and `TRAVEL` planning over unexplored ground.
- **T20's requested guard is not merely present but tested:** removing the `- {player}`
  subtraction from the frontier goal set makes
  `test_auto_explore_does_not_stall_when_the_player_stands_on_a_frontier` fail with
  `IndexError`. T21 notes it was the one rule none of the other 373 tests caught — a good
  illustration of why cross-worker open questions get carried forward explicitly.
- T21 reports **every v4 module matches CONTRACT-v4 as written**; it found no behavioural
  discrepancy anywhere in the stack. One harmless wording inconsistency: §7.5 writes
  `activity.walk_step(...)` as though `Activity` had methods, while §19 declares them as
  module-level functions — which is what exists and what T21 calls.
- Phase 5 complete. `tests/test_integration.py` extended with a v4 section (85 -> 104 tests).
  `main.py` needed no change. **Full suite 1982 passed, exit 0, zero skips** (v3 was 1666).
- Live curses session verified under an 80x24 pty: auto-explore ran **~12 turns in 2.01 s
  (~6/sec, under the 10/sec cap)**, a keypress produced the next redraw **5 ms** later rather
  than 100, **0 bytes** followed the cancel (the activity genuinely stopped), and the session
  quit cleanly with termios restored.
- **Both initial "failures" in that session were my own measurement bugs**, not the product's:
  I searched the status bar for a `Turns:` counter that v3 had removed, and measured
  "cancellation latency" with a fixed drain window that always took its full duration. The
  real signal was in the first run all along (2572 redraw bytes while running, 0 after
  cancel). Corrected instrument, same conclusion.
- One integration test of mine failed first time for a real reason worth recording: I planned
  a route with `is_passable` and an empty `open_doors`, so every door blocked and there was no
  route. The correct test plans with `is_planning_passable` and bumps the door open mid-walk —
  which is exactly what the activity layer does, and makes the test a better check of that
  seam than the version I first wrote.
- `.plan/INTEGRATION-v4.md` written.
