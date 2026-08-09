# PLAN v4 — decomposition and execution waves

Four tasks, three waves. Cut along CONTRACT-v4 seams; each owns a disjoint file set (§9 v4).

## Task list

| ID | Title | Owns | Implements | Depends on | Model |
|---|---|---|---|---|---|
| **T18** | Diagonal keys and activity messages | `keys.py`, `events.py`, `tests/test_keys.py`, `tests/test_events.py` | §5 v4, §16 v4 | — | **sonnet** |
| **T19** | Pathfinding and map topology | `pathfind.py`, `tests/test_pathfind.py` | §18 | — | **sonnet** |
| **T20** | Planning passability and activity planners | `world.py`, `activity.py`, `tests/test_world.py`, `tests/test_activity.py` | §13 v4, §19 | T19 | **opus** |
| **T21** | Activities, travel and the paced loop | `game.py`, `tests/test_game.py` | §7 v4 | T18, T19, T20 | **opus** |

### Model assignment (per policy)

- **T18 → sonnet.** Two leaf vocabulary modules, a binding key table with measured constants
  and a binding wording table. Transcription with tests.
- **T19 → sonnet.** The brief fixes integer costs, a fixed direction order, the exact
  four-quadrant `is_wide` rule and a measured performance target. The judgment was spent in
  research; what remains is careful implementation and adversarial testing.
- **T20 → opus.** This is where a plausible implementation silently underperforms: a frontier
  rule that forgets closed doors leaves auto-explore stalled in the first room, and one that
  peeks at unexplored terrain passes every coverage test while violating the core requirement.
  Neither failure looks like a bug.
- **T21 → opus.** The only cross-cutting task: a new state machine (`activity`, `awaiting_walk`)
  layered on the existing turn rules, plus the paced loop and the interrupt seam.

## Dependency graph

```
   T18 (keys, events)          T19 (pathfind)
        │                           │
        │                           ▼
        │                      T20 (world, activity)
        │                           │
        └─────────────┬─────────────┘
                      ▼
              T21 (game, loop)
                      │
                      ▼
      orchestrator: integration + INTEGRATION-v4.md
```

## Execution waves

**Wave 1 — 2 workers.** T18, T19. Both are leaves with no shared files.

**Wave 2 — 1 worker.** T20 (needs `pathfind`).

**Wave 3 — 1 worker.** T21.

**Wave 4 — orchestrator.** Extend `tests/test_integration.py` with end-to-end auto-explore,
travel and auto-walk; re-verify the v1–v3 properties; run the live curses session including a
paced activity and a mid-activity cancellation; write `INTEGRATION-v4.md`. `main.py` needs no
change.

## Why these seams

- **`pathfind.py` takes a `passable` callable and imports nothing from the project.** That is
  what lets it be planned over *explored terrain* by the activity layer and over *real terrain*
  by anything else, without knowing the difference — and it makes the whole module testable
  against hand-drawn ASCII maps with no engine at all.
- **`activity.py` holds the planners but not `advance`.** `advance` needs `GameState`, which
  would drag `game.py` into the import graph and make a cycle. Splitting the pure planners out
  keeps auto-explore and corridor-following unit-testable without constructing a game.
- **The planning-passable predicate goes in `world.py`, not the pathfinder**, so the closed-door
  rule keeps exactly one home — the same reason `is_passable` and `is_transparent` live there.
- **T21 is alone in wave 3** for the same reason T12 and T17 were: it is the only module that
  sees everything, and it owns the turn semantics.

## Per-wave verification (orchestrator runs these)

| Wave | Command |
|---|---|
| 1 | `.venv/bin/python -m pytest tests/test_keys.py tests/test_events.py tests/test_pathfind.py -q` |
| 2 | `.venv/bin/python -m pytest tests/test_world.py tests/test_activity.py -q` |
| 3 | `.venv/bin/python -m pytest tests/test_game.py -q` |
| 4 | `.venv/bin/python -m pytest -q` + live curses session |

A task is `done` only when its report exists **and** the orchestrator has personally run its
verification command and seen it exit 0.

## Expected transitional breakage

| Breaks | Cause | Fixed by |
|---|---|---|
| `tests/test_keys.py` — `CommandKind` member count, `H`/`J`/`K`/`L` asserted `UNKNOWN` | two new kinds, four new bindings | **T18** (same owner) |
| `tests/test_events.py` — `EventKind` member count | eight new kinds | **T18** (same owner) |
| `tests/test_world.py` — module public surface | one new predicate | **T20** (same owner) |
| `tests/test_game.py` — `GameState` field list | two new fields | **T21** (same owner) |
| `tests/test_integration.py` | the above | **orchestrator**, wave 4 |

Per-task suites stay green; the full suite is red from wave 1 until wave 4.

## Risk register

| Risk | Mitigation |
|---|---|
| **Auto-explore stalls behind closed doors** — the single most likely silent failure | §13 v4 makes `is_planning_passable` mandatory and §19.1 makes a closed door a frontier; T20 must assert coverage on real levels, and the orchestrator re-measures independently |
| **Auto-explore cheats** by reading unexplored terrain — passes every coverage test | §19.1 states the rule; T20 must assert that `frontier_cells` output is unchanged when unexplored terrain is altered |
| `is_wide` implemented over two quadrants instead of four | Measured trap: 96.4% vs 99.98%. Pinned in §18.2 and in T19's criteria |
| A\* non-determinism from ambiguous tie-breaks | Integer costs (§0.11), fixed `DIRECTIONS` order, coordinate in the heap key; cross-process determinism test |
| Pacing leaks into `step`/`advance` | §0.10 forbids clocks outside `run`; T21 must assert by source inspection that no module calls `time.sleep` |
| The cancelling key also acts as a command | §7.7 states it is consumed; T21 must test it |
| Activity survives a level change | §7.5 clears it; T21 must test descend-during-travel |

## Out of scope

No monsters, combat, items, inventory, save-load to disk, sound. No live interrupt conditions —
`interruption` returns `None` today by decision, and the seam is what ships. No path caching or
incremental replanning: re-planning every turn is affordable and simpler. No auto-descend during
auto-explore. No travel between levels. No mouse. No message history.
