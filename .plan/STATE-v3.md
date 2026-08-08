# STATE — v3 (multi-level dungeon, stairs, UI chrome, event messages)

Resume point for an interrupted orchestration. Resume from the first task not marked `done`.
`in_progress` means "not started" — re-run it from its brief.

v1 and v2 are **complete and frozen** — see `.plan/INTEGRATION.md` and
`.plan/INTEGRATION-v2.md` (1300 tests passing). This file tracks the v3 increment only.

Phase: **COMPLETE.** All five v3 tasks done and verified; integration finished.
Full suite **1666 passed, exit 0, zero skips**. Live curses session verified.
See `.plan/INTEGRATION-v3.md`.
Interpreter for all verification: `.venv/bin/python` (Python 3.14.6, pytest 9.1.1).

| Task | Title | Wave | Depends on | Model | Status | Report | Verified |
|---|---|---|---|---|---|---|---|
| T13 | Stair tiles and level stair fields | 1 | — | sonnet | **done** | `reports/T13.md` | **yes — 117 passed, exit 0** |
| T14 | Event vocabulary and stair commands | 1 | — | sonnet | **done** | `reports/T14.md` | **yes — 171 passed, exit 0** |
| T15 | Generator: anchor, spawn, stairs | 2 | T13 | opus | **done** | `reports/T15.md` | **yes — 564 passed, exit 0** |
| T16 | Renderer: chrome rows | 2 | T13 | sonnet | **done** | `reports/T16.md` | **yes — 119 passed, exit 0** |
| T17 | Dungeon, descent/ascent, messages | 3 | T13–T16 | opus | **done** | `reports/T17.md` | **yes — 356 passed, exit 0** |

A task is `done` only when its report exists **and** the orchestrator has personally run its
verification command and seen it exit 0.

## Verification commands

| Task | Command |
|---|---|
| T13 | `.venv/bin/python -m pytest tests/test_level.py -q < /dev/null` |
| T14 | `.venv/bin/python -m pytest tests/test_events.py tests/test_keys.py -q < /dev/null` |
| T15 | `.venv/bin/python -m pytest tests/test_generator.py -q < /dev/null` |
| T16 | `.venv/bin/python -m pytest tests/test_render.py -q < /dev/null` |
| T17 | `.venv/bin/python -m pytest tests/test_game.py tests/test_dungeon.py -q < /dev/null` |
| Phase 5 | `.venv/bin/python -m pytest -q < /dev/null` + live curses session |

## Files frozen for v3 — nobody may edit

`roguelike/world.py`, `roguelike/style.py`, `roguelike/fov.py`, `roguelike/movement.py` and
their four test files (268 tests). They need no change because `world.is_passable` is defined
via `WALKABLE` and `world.is_transparent` as "not WALL", so adding the stair tiles to
`WALKABLE` makes stairs passable and transparent automatically; and `style.role_for` already
returns `Role.TERRAIN` for every non-door tile, so stairs inherit the light-gray palette.
That is the v2 one-home-per-rule seam paying for itself a second time.

Orchestrator-owned: `main.py`, `tests/test_integration.py`, `roguelike/__init__.py`,
`pytest.ini`, everything under `.plan/` except `.plan/reports/TNN.md`.

## Expected transitional breakage — planned, each with an owner

| Breaks | Cause | Fixed by |
|---|---|---|
| `tests/test_level.py` exact-equality on `TILE_CHARS` / `WALKABLE` | two new tiles | T13 (same owner) |
| `tests/test_render.py` — `render_to_cells(..., status)` | `Chrome` replaces `status` | T16 (same owner) |
| `tests/test_game.py` — `new_game(level)` | now takes a master seed | T17 (same owner) |
| `tests/test_integration.py` | all of the above | orchestrator, wave 4 |

The full suite will be red from wave 1 until wave 4; per-task suites stay green.

## Log

- Research complete: `.plan/RESEARCH-v3.md`. Measured on the v2 build —
  **zero levels lack a valid spawn cell** (mean 282/level, min 168); **minimum coordinate seen
  is exactly 2**, the theoretical bound, proving an anchor room always fits; anchoring a
  mandatory room first lands the coordinate correctly **150/150** with **0 connectivity
  failures** at a cost of ~1 room/level (10.87 → 9.71); a 5-level chain over 60 master seeds
  (**300 levels**) had **zero link, connectivity or stair-validity failures** and was fully
  deterministic; `random.Random(str)` is stable across `PYTHONHASHSEED`; the UI budget
  `1 + 22 + 1 = 24` fits a classic terminal exactly; every level has **≥7 rooms** able to host
  a stair.
- **Key finding: requirements 8 and 9 are not needed.** Placing the anchor room *first* means
  nothing is carved after the fact, so no room changes shape and no door is orphaned. Recorded
  in CONTRACT-v3 §3.2 with an explicit instruction not to write repair code.
- User decisions: **deeper up-stairs ascend normally** (level 1's quits) · stair messages fire
  on **stepping onto the tile** · stairs share the **terrain colour**.
- Consequences: sharing the terrain colour means `style.py` needs no change; the `world.py`
  seam means `fov.py` and `movement.py` need none either — four modules frozen outright.
  Ascending normally makes per-level persistence load-bearing.
- **Correction to the research doc:** `keys.py` *does* need changing. Stairs are used by an
  explicit command (`>` / `<`), as in ADOM — the research had wrongly listed `keys.py` as
  untouched. CONTRACT-v3 §5 v3 covers it and it is folded into T14.
- CONTRACT-v3.md written and **frozen**: amends §0 (new §0.8 row offset, §0.9 frozen modules),
  §1, §2, §3 (G13–G20, G7 amended), §4 (Chrome, two chrome rows), §5, §7, §9, §10, §11;
  adds §16 events and §17 dungeon.
- PLAN-v3.md written: 5 tasks, 3 waves, model assignments per policy.
- Phase 3 complete: T13–T17 briefs written to `.plan/tasks/`.
- Gate passed. Wave 1 dispatched: T13 (sonnet), T14 (sonnet).
- Wave 1 verified by orchestrator: T13 **117 passed** (85 pre-existing + 32 new), T14
  **171 passed** (51 events + 120 keys) — both exit 0. Reports present, no contract deviations.
- **Orchestrator independently verified the §0.9 architectural bet**, which is what the whole
  v3 change-set size rests on. With a hand-built level containing both stair tiles:
  `level.is_walkable` True · `world.is_passable` True · `world.is_transparent` True ·
  `movement.try_move` steps onto them · `fov.compute_visible` sees them ·
  `style.role_for` returns `TERRAIN` and the colour resolves to 250, identical to floor.
  **All four frozen modules delivered stair support with zero edits**, confirmed untouched by
  `git status`. Adding the tiles to `WALKABLE` was the entire mechanism.
- T14 declined to add `DESCEND_COMMAND` / `ASCEND_COMMAND` singletons, correctly reading the
  contract as not asking for them. Accepted.
- Wave 2 dispatched: T15 (opus), T16 (sonnet).
- Wave 2 verified by orchestrator: T15 **564 passed** (414 pre-existing + 150 new), T16
  **119 passed** — both exit 0. No contract deviations reported.
- T15 highlights: the anchor is placed with the *same* size distribution as an ordinary room
  and inserted as `rooms[0]` before rejection sampling, which is load-bearing — the router
  marks `placed[0]` connected from the start and only ever *drops* unreachable rooms, so the
  anchor can never be dropped. Room cost came in far better than the research predicted
  (**10.92 → 10.88**, not 10.87 → 9.71) because `_lay_out`'s retry loop absorbs the loss.
  The worker ran **six mutation tests** against its own new tests, all caught — including one
  that initially passed (an off-by-one in the anchorable range, indistinguishable from an
  incidental `ValueError`), fixed by asserting on the message. Ad-hoc stress: **9,720 levels**
  plus 400 chained levels, zero failures.
- T16 highlights: the `+1` row offset is pinned by a non-square level with `x != y` asserting
  both that the player *is* at `cells[y+1][x]` and *is not* at `cells[y][x]`.

## ORCHESTRATOR ERRATUM — CONTRACT-v3 §9 froze a file that v3 necessarily breaks

`tests/test_world.py::test_tile_still_has_exactly_three_members` asserted
`set(Tile) == {WALL, FLOOR, DOOR}`. T13 correctly invalidated it by adding the stair tiles per
§1 v3 — but §9 v3 lists `tests/test_world.py` as **frozen, nobody may edit**, so no worker
could repair it. T15 spotted this and reported it rather than reaching outside its ownership,
which is the correct behaviour.

**Cause: my error when writing the contract.** I checked for tile-count assertions by grepping
`len(Tile)`, `list(Tile)` and "exactly three"; this one is worded `set(Tile) == {...}` and the
grep missed it.

**Resolution (orchestrator decision):** I updated that one assertion myself, extending it to
the five-member v3 vocabulary and renaming it `test_tile_vocabulary_is_pinned` — the same
treatment T13 gave the two equality assertions it owned. The test's intent is preserved: the
world predicates are written against a known vocabulary, so a new tile must pass through here
deliberately. `roguelike/world.py` itself remains **untouched**, which was the substantive
point of freezing it. All four frozen suites green afterwards: world 26, style 36, fov 97,
movement 95.

## Accepted observation from T16

§4.2's formula `len(message) + 1 + len(status_right) <= width` reserves a separator column
**unconditionally**, so a full-width message loses its last character even when
`status_right` is empty. T16 applied the binding text literally and pinned it with a test.
**Accepted as-is** — `format_status_right` (§7.2) always returns a non-empty string at runtime,
so this is unreachable in play. Recorded rather than amended.

- Full suite at end of wave 2: **1544 passed, 18 failed** — failures confined *exactly* to
  `tests/test_game.py` (1, T17's file) and `tests/test_integration.py` (17, orchestrator's,
  wave 4). Every other suite green.
- Wave 3 dispatched: T17 (opus).
- **T17 was interrupted mid-task by a session limit** before writing its report, leaving
  `1 failed, 354 passed`. Orchestrator diagnosed the failure rather than assuming: the test
  asserted a 38-char message appears in full on a **40-column** map, but with `status_right`
  at 18 chars it needs 57 columns, so §4.2 correctly clips it — **the code was right and the
  test's expectation was wrong**. Agent resumed from its transcript with that analysis, told
  to verify independently and explicitly told not to weaken the assertion. It confirmed the
  diagnosis, moved the "message appears" test to an 80-column level and **kept** a 40-column
  test pinning the truncation rule.
- **T17 done and verified: 356 passed, exit 0** (259 game + 97 dungeon). Everything except
  `tests/test_integration.py`: **1581 passed**. Frozen source modules still untouched.
  The worker mutation-tested its own suite against **16 mutants**, all caught, including
  fog-reset-on-ascent, descend-regenerates-instead-of-restoring, and Chrome field swap.
- **Orchestrator's own independent check of the headline behaviours**, run before the agent
  finished and not relying on its tests: spawn is on `STAIRS_UP` with `explored == visible`
  non-empty; **descent keeps the player at the same coordinate** `(67,17)` with the new
  level's up-stair equal to the old down-stair; **fog and doors survive the round trip
  exactly in both directions** (L1 preserved on climbing back, L2 preserved on descending
  again); ascending at depth 1 ends the game with the farewell message.

## Further orchestrator errata — CONTRACT-v3 imprecisions T17 found

All three are faults in my contract, not in the code. Each was resolved sensibly by the
worker and is recorded here rather than papered over.

1. **§7.1 says "if the new cell is a stair tile", but §10 v3 denies `game.py` the `tiles`
   import needed to ask.** The prose and the import graph pull in opposite directions.
   Resolved by comparing coordinates (`player == level.stairs_down[0]`), which G18 makes
   exactly equivalent on any generated level. **Accepted** — the coordinate form is arguably
   the better design, since it keeps `game.py` free of tile vocabulary entirely.
2. **§7.2 says `game.py` does not format wording, while §7.1 requires `step` to set a string
   `outcome`.** Reconciled by calling `events.message_for` for the farewell — the one such
   call outside `run`. **Accepted**; §7.2's phrasing wrongly implies `run` is the only call
   site. `game.py` still contains no sentence, pinned by an AST test.
3. **§3.4 prose vs §11 v3 table on a non-int `depth`** — prose says `ValueError`, table says
   `ValueError`/`TypeError`. Shipped behaviour follows the table (`TypeError` for non-int,
   `ValueError` for `< 1`). Flagged by both T15 and T17; the only outright self-contradiction
   in CONTRACT-v3. **Table wins.**

- Phase 5 complete. `tests/test_integration.py` rewritten for the multi-level stack
  (85 tests). `main.py` needed no change — it imports only `play`, whose signature is
  unchanged. **Full suite 1666 passed, exit 0, zero skips** (v2 was 1300).
- Live colour curses session verified under an 80x24 pty: starts, paints, responds,
  announces the staircase, descends on `>` with `You descend to level 2.`, shows the opened
  door glyph, exits 0 on `q`, restores termios byte-identically. All five palette colours
  reached the wire (250, 238, 180, 94, 231). Fog confirmed live: **110 of 1760** map cells
  painted at open, 454 after descending.
- **ONE REAL DEFECT FOUND, documented not fixed:** on a terminal exactly as tall as the
  frame, the last character of the status row is never drawn, so `Level 1  Seed 1234`
  paints as `Level 1  Seed 123`. Confirmed real — a 25-row terminal shows it in full and
  the headless renderer produces the correct string. Cause is `draw`'s bottom-right-cell
  guard, which curses requires; v2 never hit it because its frame was 23 rows. It eats the
  last digit of the seed, so it is worse than cosmetic. Two clean fixes exist, both §4.2
  contract changes; left for the user to choose.
- `.plan/INTEGRATION-v3.md` written.
