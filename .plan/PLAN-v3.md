# PLAN v3 — decomposition and execution waves

Five tasks, three waves. Cut along CONTRACT-v3 seams; each owns a disjoint file set (§9 v3).

## Task list

| ID | Title | Owns | Implements | Depends on | Model |
|---|---|---|---|---|---|
| **T13** | Stair tiles and level stair fields | `tiles.py`, `level.py`, `tests/test_level.py` | §1 v3, §2 v3 | — | **sonnet** |
| **T14** | Event vocabulary and stair commands | `events.py`, `keys.py`, `tests/test_events.py`, `tests/test_keys.py` | §16, §5 v3 | — | **sonnet** |
| **T15** | Generator: anchor room, spawn, stairs | `generator.py`, `tests/test_generator.py` | §3 v3 (G13–G20) | T13 | **opus** |
| **T16** | Renderer: chrome rows | `render.py`, `tests/test_render.py` | §4 v3 | T13 | **sonnet** |
| **T17** | Dungeon, descent/ascent, messages | `game.py`, `dungeon.py`, `tests/test_game.py`, `tests/test_dungeon.py` | §7 v3, §17 | T13–T16 | **opus** |

### Model assignment (per policy)

- **T13, T14, T16 → sonnet.** Fully specified: literal enum members, a binding wording table,
  an explicit row-by-row frame layout. The judgment was spent writing the contract.
- **T15 → opus.** The anchor room must be placed without breaking **G8 connectivity** or
  **G1/G2 determinism** — the two guarantees that fail silently — while satisfying seven new
  guarantees. This is the same profile as v2's T09, which needed opus.
- **T17 → opus.** The only cross-cutting task: a multi-level state machine with per-level fog
  persistence, four new command outcomes, the event-replacement rule, and the game-over path.

## Dependency graph

```
   T13 (tiles, level)              T14 (events, keys)
     │            │                        │
     ▼            ▼                        │
   T15          T16                        │
 (generator)  (render)                     │
     └────────────┴────────────┬───────────┘
                               ▼
                        T17 (dungeon, game)
                               │
                               ▼
              orchestrator: integration + INTEGRATION-v3.md
```

## Execution waves

**Wave 1 — 2 workers.** T13, T14. No shared files; `events.py` and `keys.py` are leaves.

**Wave 2 — 2 workers.** T15, T16. Both need the new tiles; disjoint files; `render` must not
import `generator`.

**Wave 3 — 1 worker.** T17.

**Wave 4 — orchestrator.** Extend `tests/test_integration.py` with a multi-level descent chain,
re-verify the v1/v2 properties, run the live curses session, write `INTEGRATION-v3.md`.
`main.py` needs no change — `play(seed, width, height)` keeps its signature.

## Why these seams

- **T13 is alone in wave 1's critical path** because every other task is expressed in terms of
  the new tiles and the new `Level` fields. It is also the only task that edits a file untouched
  since v1.
- **T14 bundles events and keys** because both are leaf vocabulary modules with no project
  imports, both are small, and both are additive. Splitting them would create two two-file
  workers and no extra parallelism.
- **T15 is separated from T17** because "how a level is built" and "how levels connect" are
  different concerns. The generator never learns what a dungeon is; it takes a `required_up`
  coordinate and a depth as plain data.
- **T16 is separated from everything** because the renderer receives finished strings in
  `Chrome`. It cannot reach the event system or the game state even if it wanted to — which is
  what keeps "rendering is decoupled from state" true as the UI grows.
- **T17 owns `dungeon.py` and `game.py` together** because seed derivation, the level store and
  the turn loop are one concern: running a multi-level game. Splitting them would add a wave
  for a module of two functions.

## Per-wave verification (orchestrator runs these)

| Wave | Command |
|---|---|
| 1 | `.venv/bin/python -m pytest tests/test_level.py tests/test_events.py tests/test_keys.py -q` |
| 2 | `.venv/bin/python -m pytest tests/test_generator.py tests/test_render.py -q` |
| 3 | `.venv/bin/python -m pytest tests/test_game.py tests/test_dungeon.py -q` |
| 4 | `.venv/bin/python -m pytest -q` + live curses session |

A task is `done` only when its report exists **and** the orchestrator has personally run its
verification command and seen it exit 0.

## Expected transitional breakage

v3 changes signatures that earlier suites call. These failures are **planned**, and each has a
named owner:

| Breaks | Cause | Fixed by |
|---|---|---|
| `tests/test_level.py` — `TILE_CHARS ==` and `WALKABLE ==` exact-equality | two new tiles | **T13** (same owner) |
| `tests/test_render.py` — `render_to_cells(..., status)` | `Chrome` replaces `status` | **T16** (same owner) |
| `tests/test_game.py` — `new_game(level)` | now takes a master seed | **T17** (same owner) |
| `tests/test_integration.py` | all of the above | **orchestrator**, wave 4 |

The full suite will be red from wave 1 until wave 4. Per-task suites stay green. Nothing else
is affected — `world`, `style`, `fov`, `movement` and their 268 tests are untouched by design
(§0.9).

## Risk register

| Risk | Mitigation |
|---|---|
| **Anchor room breaks G8 connectivity** | Prototyped: 0 failures over 150 pairs and 300 chain levels. T15 keeps flood-fill over ≥30 seeds; orchestrator re-verifies independently |
| **Anchor changes RNG draw order and breaks determinism** | Levels *may* differ from v2 — that is allowed. Reproducibility is not: cross-process determinism test retained |
| **The `y + 1` map offset** (§0.8) | T16 must pin it with a non-square level and `x != y`; the integration suite re-checks |
| **Fog resets on ascent** | T17 must assert a round trip preserves `explored` and `open_doors` exactly |
| **Stairs placed where the next level cannot anchor** | G13 forces both stair cells to be open spots, which are provably within `2 ≤ c ≤ dim-3` |
| Two workers touch one file | §9 v3 ownership table; four modules frozen outright |

## Out of scope

No monsters, combat, items, inventory, save-load to disk, sound. No branch generation — the
`stairs_down` tuple is the scaffolding and ships with one entry. No content in the stats row.
No message history or scrollback: one line, replaced each turn.
