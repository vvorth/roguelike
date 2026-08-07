# PLAN v2 — decomposition and execution waves

Tasks cut along CONTRACT-v2 seams. Each owns a disjoint set of files (§9 v2). Six tasks,
three waves — the same shape as v1, which held with zero contract deviations.

## Task list

| ID | Title | Owns | Implements | Depends on | Model |
|---|---|---|---|---|---|
| **T07** | Runtime passability seam | `world.py` (new), `tiles.py` (amend), `movement.py` (amend), `tests/test_world.py`, `tests/test_movement.py` | §13, §1, §6 | — | **sonnet** |
| **T08** | Style vocabulary and palette | `style.py`, `tests/test_style.py` | §15 | — | **sonnet** |
| **T09** | Generator: door constraint | `generator.py` (amend), `tests/test_generator.py` | §3 (G9a–d, G4a) | — | **opus** |
| **T10** | Permissive field of view | `fov.py`, `tests/test_fov.py` | §14 | T07 | **opus** |
| **T11** | Renderer: colour and fog | `render.py` (rewrite), `tests/test_render.py` | §4 | T07, T08 | **sonnet** |
| **T12** | Game state, FOV per move, bump-to-open | `game.py` (amend), `tests/test_game.py` | §7 | T07, T08, T09, T10, T11 | **opus** |

### Model assignment rationale (per policy)

- **T07, T08, T11 → sonnet.** Fully specified briefs: literal signatures, a binding palette
  table, explicit cell-selection rules, checkable acceptance criteria. The judgment was spent
  writing CONTRACT-v2; these are transcription with tests.
- **T09 → opus.** Not a patch. Corridor routing must change so doors land embedded in wall
  runs, while preserving **G8 connectivity** and **G1/G2 determinism** — the two guarantees
  most easily broken by a routing change, and the ones whose failure is silent.
- **T10 → opus.** The permissive FOV rule has real geometric subtlety: eight sample points, the
  diagonal-corner lattice rule, and the F4 superset property. A plausible-looking
  implementation that over-shows by a few percent leaks map information and passes naive tests.
- **T12 → opus.** Cross-cutting: the only module importing from all others, and it owns
  bump-to-open, the FOV-recompute trigger, and the "rejected move consumes no turn" rule that
  must survive the new door branch.

## Dependency graph

```
   T07 (world/tiles/movement)      T08 (style)      T09 (generator)
        │            │                  │                  │
        ▼            └──────┐    ┌──────┘                  │
   T10 (fov)                ▼    ▼                         │
        │              T11 (render)                        │
        └────────────────┬──┴───────────────────────────────┘
                         ▼
                  T12 (game loop)
                         │
                         ▼
        orchestrator: integration + INTEGRATION-v2.md
```

## Execution waves

**Wave 1 — 3 workers in parallel.** T07, T08, T09. No shared files, no imports between them.
T09 is fully independent of the visibility work and could ship alone.

**Wave 2 — 2 workers in parallel.** T10 (needs `world`), T11 (needs `world` + `style`).
Disjoint files; `render` must not import `fov` (§10).

**Wave 3 — 1 worker.** T12.

**Wave 4 — orchestrator.** Extend `tests/test_integration.py`, re-verify the v1 end-to-end
properties still hold, run the live curses session with colour, write `INTEGRATION-v2.md`.

## Why these seams

- **T07 bundles `world` + `movement` + the tiles glyph** because they are one idea: the runtime
  passability rule and its first consumer. `movement.py`'s change is literally "use
  `world.is_passable` instead of `level.is_walkable`" — splitting it into its own task would
  create a two-line worker and a needless dependency edge.
- **T09 is isolated** because the door fix is orthogonal to visibility. It touches no file any
  other v2 task touches, so it can run, fail, and be re-dispatched without blocking anything.
- **T10 is separated from T11** because FOV computes *what is visible* and the renderer decides
  *how visible things look*. Fusing them would put geometry and presentation in one module and
  break §10 (`render` must not import `fov`).
- **T12 is last and alone** for the same reason T06 was in v1: it is the only module that sees
  everything, and it owns the turn semantics.

## Per-wave verification (orchestrator runs these, not the worker)

| Wave | Command |
|---|---|
| 1 | `.venv/bin/python -m pytest tests/test_world.py tests/test_movement.py tests/test_style.py tests/test_generator.py -q` |
| 2 | `.venv/bin/python -m pytest tests/test_fov.py tests/test_render.py -q` |
| 3 | `.venv/bin/python -m pytest tests/test_game.py -q` |
| 4 | `.venv/bin/python -m pytest -q` (full suite) + live colour curses session |

A task is `done` only when its report exists **and** the orchestrator has personally run its
verification command and seen it exit 0.

## Regression risk — what v2 can break in v1

The v1 suite is 912 tests. This increment changes four files that suite covers.

| Risk | Mitigation |
|---|---|
| **`tests/test_render.py` must be rewritten** — v1 asserts `render_to_lines(level, pos, status)`, a signature that no longer exists | T11 owns the file and rewrites it; §4.2 `to_lines` preserves the plain-text assertions |
| **`tests/test_game.py` must be rewritten** — `GameState` gains three fields | T12 owns the file |
| **Generator reroute silently breaks connectivity (G8)** | G8 flood fill over ≥20 seeds stays in T09's suite; orchestrator re-verifies independently in `test_integration.py` |
| **Generator reroute breaks determinism (G1/G2)** | T09 keeps the cross-process determinism test; any change to RNG draw *order* changes every level, which is allowed, but must stay reproducible |
| **FOV over-shows and leaks map info** | F4/F5/F6 guarantees; T10 must assert a pillar casts a shadow and that no cell behind a closed door is visible |
| **v1 integration tests assume no fog** | Orchestrator owns `test_integration.py` and updates it in Wave 4 |
| Two workers touch one file | §9 v2 ownership table; no file appears twice; `level.py` and `keys.py` are frozen out entirely |

## Out of scope, restated

No monsters, combat, items, inventory, save-load, multiple levels, stairs, sound. No explicit
`o`-to-open command (bump-to-open covers it). No light sources, no coloured lighting, no
per-tile brightness beyond the three visibility states. No `KEY_RESIZE` handling.
