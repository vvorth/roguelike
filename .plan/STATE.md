# STATE

Resume point for an interrupted orchestration. Resume from the first task not marked `done`.
`in_progress` means "not started" — re-run it from its brief.

Phase: **COMPLETE.** All six tasks done and verified; integration finished.
Full suite **912 passed, exit 0**. Live curses session verified under a pty.
See `.plan/INTEGRATION.md`.
Interpreter for all verification: `.venv/bin/python` (Python 3.14.6, pytest 9.1.1).

| Task | Title | Wave | Depends on | Status | Report | Verified by orchestrator |
|---|---|---|---|---|---|---|
| T01 | Core types: tiles and level | 1 | — | **done** | `.plan/reports/T01.md` | **yes — 85 passed, exit 0** |
| T04 | Input abstraction | 1 | — | **done** | `.plan/reports/T04.md` | **yes — 111 passed, exit 0** |
| T02 | Procedural dungeon generator | 2 | T01 | **done** | `.plan/reports/T02.md` | **yes — 381 passed, exit 0** |
| T03 | Renderer | 2 | T01 | **done** | `.plan/reports/T03.md` | **yes — 58 passed, exit 0** |
| T05 | Movement and collision | 2 | T01 | **done** | `.plan/reports/T05.md` | **yes — 67 passed, exit 0** |
| T06 | Game loop and curses lifecycle | 3 | T01–T05 | **done** | `.plan/reports/T06.md` | **yes — 115 passed, exit 0** |

A task is `done` only when its report exists **and** the orchestrator has personally run its
verification command and seen it exit 0.

## Verification commands

| Task | Command |
|---|---|
| T01 | `.venv/bin/python -m pytest tests/test_level.py -q` |
| T04 | `.venv/bin/python -m pytest tests/test_keys.py -q < /dev/null` |
| T02 | `.venv/bin/python -m pytest tests/test_generator.py -q < /dev/null` |
| T03 | `.venv/bin/python -m pytest tests/test_render.py -q < /dev/null` |
| T05 | `.venv/bin/python -m pytest tests/test_movement.py -q < /dev/null` |
| T06 | `.venv/bin/python -m pytest tests/test_game.py -q < /dev/null` |
| Phase 5 | `.venv/bin/python -m pytest -q < /dev/null` + live curses smoke test |

## Orchestrator-owned files (no worker touches these)

`roguelike/__init__.py`, `tests/__init__.py`, `pytest.ini`, `.gitignore`, `main.py`,
`tests/test_integration.py`, everything under `.plan/` except `.plan/reports/TNN.md`.

## Log

- Phase 0–2 complete: BRIEF.md, CONTRACT.md, PLAN.md written. CONTRACT.md is frozen.
- Environment: system python3 is 3.9.6 (below the 3.10 floor, no pytest); created `.venv`
  from Homebrew python3.14 (3.14.6) with pytest 9.1.1 and working curses.
- Scaffolding written: `roguelike/__init__.py`, `tests/__init__.py`, `pytest.ini`,
  `.gitignore`.
- Phase 3 complete: all six briefs written to `.plan/tasks/`.
- Wave 1 dispatched: T01, T04 in parallel.
- Wave 1 verified by orchestrator: `tests/test_level.py` 85 passed exit 0;
  `tests/test_keys.py` 111 passed exit 0. Both reports present. No contract deviations
  reported by either worker. File ownership respected — no stray files created.
- **Finding propagated to Wave 2 (not a contract change):** `Room.intersects` expands both
  rects by `margin`, so at the default `margin=1` the minimum legal gap between two room
  floor rects is **2 cells**, not 1. This satisfies G5 and matches the T01 brief's stated
  acceptance criteria, but means rooms cannot share a wall. T02's dispatch prompt says so
  explicitly so the generator author does not fight it.
- Wave 2 dispatched: T02, T03, T05 in parallel.
- Wave 2 verified by orchestrator: generator 381 passed, render 58 passed, movement 67
  passed; full suite so far **702 passed, exit 0**. All three reports present, no contract
  deviations reported, file ownership respected.
- Model-assignment policy received mid-run. T01–T05 had already been dispatched and
  inherited opus; all passed first time and are not being re-run. Recorded in PLAN.md.
  T06 dispatched explicitly on **opus** — it is the sole cross-cutting module and owns the
  curses lifecycle.
- Wave 3 dispatched: T06 (opus). Verified: 115 passed, exit 0.
- Phase 5 complete: `main.py` and `tests/test_integration.py` written by the orchestrator.
  Integration suite passed on its first run (95 tests). Full suite 912 passed, exit 0.
  Live curses session verified under a pseudo-terminal: starts, renders, responds to keys,
  quits on `q` with exit 0, and restores termios byte-identically (ICANON and ECHO back on).
  `.plan/INTEGRATION.md` written. **No contract deviations found anywhere in the project.**

## Deferred edge cases (decided by orchestrator, no re-dispatch)

- **T02 open question 4 — `bool` for `width`/`height`/`max_rooms`.** CONTRACT §3.1 rejects a
  `bool` seed explicitly but says only "not `int` → `TypeError`" for the dimensions, and
  `bool` is an `int` subclass. T02 took the literal reading: `width=True` passes the type
  check then fails the size check with `ValueError`, and `max_rooms=True` behaves as `1`.
  Accepted as-is — the alternative contradicts the §11 row for too-small dimensions, and no
  caller in this codebase passes a bool. Recorded in INTEGRATION.md as a known gap.
- **T05 zero-delta precedence.** §6's "walkable target → moved=True" and "`(0,0)` →
  moved=False" overlap when standing on floor. T05 made the zero-delta rule win. Correct —
  §5 guarantees a `MOVE` command never carries `(0, 0)`, so the layers agree redundantly.
- **T03 status-bar clipping.** On a terminal exactly as tall as the map, the status line is
  the row that clips. Matches §4 as written; not changed.
