# PLAN — decomposition and execution waves

Tasks are cut along the seams defined in `CONTRACT.md`, not along intuition about "parts".
Each task owns a disjoint set of files (CONTRACT §9) and is specified entirely by the
contract sections it implements, so no two workers need to talk.

## Task list

| ID | Title | Owns | Implements | Depends on | Model |
|---|---|---|---|---|---|
| **T1** | Core types: tiles and level | `roguelike/tiles.py`, `roguelike/level.py`, `tests/test_level.py` | §1, §2 | — | opus¹ |
| **T4** | Input abstraction | `roguelike/keys.py`, `tests/test_keys.py` | §5 | — | opus¹ |
| **T2** | Procedural dungeon generator | `roguelike/generator.py`, `tests/test_generator.py` | §3 | T1 | opus¹ |
| **T3** | Renderer | `roguelike/render.py`, `tests/test_render.py` | §4 | T1 | opus¹ |
| **T5** | Movement and collision | `roguelike/movement.py`, `tests/test_movement.py` | §6 | T1 | opus¹ |
| **T6** | Game loop and curses lifecycle | `roguelike/game.py`, `tests/test_game.py` | §7 | T1, T2, T3, T4, T5 | **opus** |

### Model assignment policy

Implementation tasks with a fully specified brief → `sonnet`; tasks needing design judgment
or cross-cutting reasoning → `opus`; pure lookup, inventory, or mechanical scaffolding →
`haiku`. If a sonnet worker returns blocked twice on one task, re-dispatch once on opus
before escalating to the user.

¹ **T1–T5 inherited opus** — they were dispatched before the model-assignment policy was
issued, so no explicit `model` parameter was passed and they defaulted to the session model.
All five returned complete and verified on the first attempt; they are not being re-run.
Under the policy they would have been **sonnet**: each has a fully specified brief with
literal signatures and checkable acceptance criteria, and none required design judgment —
the judgment was spent in CONTRACT.md.

**T6 → opus, deliberately.** It is the one task the policy puts on opus on its merits: it is
the sole cross-cutting module (the only one importing from all five others, per §10), it is
the first worker to see every module at once and reconcile them, and it owns the curses
lifecycle and the clean-quit path where a subtle error is invisible to unit tests.

Six tasks. Fewer is not possible without coupling: T2/T3/T5 are three genuinely independent
consumers of the same core types, and merging any pair would serialise work that can run
concurrently while giving one worker two unrelated concerns.

T4 is numbered out of dependency order deliberately — it is the input *seam* (§5), and the
numbering follows the contract's section order, not the schedule.

## Dependency graph

```
        T1 (tiles, level)                    T4 (keys)
        ├──────┬──────────┐                     │
        ▼      ▼          ▼                     │
       T2     T3         T5                     │
   (generator)(render)(movement)                │
        └──────┴──────────┴─────────┬───────────┘
                                    ▼
                             T6 (game loop)
                                    │
                                    ▼
                    orchestrator: main.py + integration
```

Note the graph is *shallow*: T1 is the only real bottleneck, and T4 bypasses it entirely.

## Execution waves

**Wave 1 — 2 workers in parallel**
- T1 — core types. Blocks three tasks; highest priority.
- T4 — input. Depends on nothing; free parallelism.

**Wave 2 — 3 workers in parallel** *(after T1 verified)*
- T2 — generator
- T3 — renderer
- T5 — movement

These three share `Level` as input and share nothing else. No file overlap, no import
between them (CONTRACT §10).

**Wave 3 — 1 worker** *(after T2, T3, T4, T5 verified)*
- T6 — game loop. The only task that imports from every other.

**Wave 4 — orchestrator, no workers**
- `main.py`, `tests/test_integration.py`, end-to-end verification, `.plan/INTEGRATION.md`.

## Why these seams

- **T1 is separated from T2** because "what a level *is*" and "how a level is *built*" are
  different concerns with different tests. Fusing them would make the data structure's
  invariants untestable except through the generator.
- **T3 is separated from T6** because that separation *is* the "rendering decoupled from
  state" constraint. The renderer takes three primitives and returns strings; it cannot
  reach the game state even if it wanted to.
- **T4 is separated from T6** because it is the "testable without curses" constraint made
  structural — key translation is a pure table lookup with no terminal in sight.
- **T5 is separated from T6** because collision is a pure predicate over a level, and the
  turn-consumption rule is then a one-line consequence in the loop (`if result.moved`).

## Per-wave verification (run by the orchestrator, not the worker)

| Wave | Command |
|---|---|
| 1 | `.venv/bin/python -m pytest tests/test_level.py tests/test_keys.py` |
| 2 | `.venv/bin/python -m pytest tests/test_generator.py tests/test_render.py tests/test_movement.py` |
| 3 | `.venv/bin/python -m pytest tests/test_game.py` |
| 4 | `.venv/bin/python -m pytest` (full suite) + live curses smoke test |

A task becomes `done` only when its report exists **and** the orchestrator has personally run
its verification command and seen it exit 0.

## Risk register

| Risk | Mitigation |
|---|---|
| `(x, y)` vs `(y, x)` confusion — the top failure mode | CONTRACT §0.1 states it three ways; the swap is confined to one function; integration test asserts the player never occupies a wall |
| Generator produces a disconnected level | G8 is a flood-fill acceptance criterion in T2's own tests, re-verified independently at integration |
| Non-determinism via global `random` | CONTRACT §0.4 forbids it; T2 must assert determinism across two calls, integration asserts it across two processes |
| A worker "helpfully" adds monsters/items scaffolding | BRIEF non-goals are restated verbatim in every brief |
| Two workers touch one file | CONTRACT §9 ownership table; no file appears twice; waves keep T6 alone |
| Tests requiring a TTY | CONTRACT §0.3 + §12; integration runs the suite with stdin redirected from `/dev/null` |
