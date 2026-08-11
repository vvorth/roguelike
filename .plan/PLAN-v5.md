# PLAN v5 — decomposition and execution waves

Seven tasks, four waves. Cut along CONTRACT-v5 seams; each owns a disjoint file set (§9 v5).

This is the largest increment the project has taken (v4 was four tasks). The decomposition is
driven by one fact: **the numbers are already decided.** RESEARCH-v5 simulated them and
CONTRACT-v5 froze them, so most of these tasks are careful implementation against a binding
table rather than open design — which is what makes seven parallel-ish tasks safe.

## Task list

| ID | Title | Owns | Implements | Depends on | Model |
|---|---|---|---|---|---|
| **T22** | Stats, items, status effects | `stats.py`, `items.py`, `status.py` + their tests | §20, §21, §22 | — | **sonnet** |
| **T23** | Point-to-point line of sight | `fov.py`, `tests/test_fov.py` | §14 v5 | — | **opus** |
| **T24** | Input and event vocabulary | `keys.py`, `events.py` + their tests | §5 v5, §16 v5 | — | **sonnet** |
| **T25** | Combat resolution | `combat.py`, `tests/test_combat.py` | §23 | T22 | **sonnet** |
| **T26** | NPCs, AI and spawning | `npc.py`, `tests/test_npc.py` | §24 | T22, T23 | **opus** |
| **T27** | Rendering NPCs and the stats row | `render.py`, `style.py` + their tests | §4 v5, §15 v5 | T24, T26 | **sonnet** |
| **T28** | Bump-to-attack, NPC turns, targeting, levelling | `movement.py`, `game.py` + their tests | §7 v5 | T22–T27 | **opus** |

### Model assignment (per policy)

- **T22 → sonnet.** Three leaf modules transcribing binding formula tables. The only subtlety is
  the floor-division rounding rule, and §0.13 states it explicitly with the failing example.
- **T23 → opus.** Additive in surface but not in difficulty: it reimplements exact
  doubled-integer segment geometry over a *different* opacity window. A subtly wrong bounding
  box produces a function that is right in open rooms and wrong near walls — and it must not
  perturb `compute_visible`, which a **frozen** test file drives end to end.
- **T24 → sonnet.** Two vocabulary tables, both binding, both copied character for character.
- **T25 → sonnet.** Pure arithmetic against frozen formulas, but the *tests* matter: this is
  where a plausible implementation reintroduces the attacker accuracy term or applies STR to
  natural attacks. §23 names both traps.
- **T26 → opus.** The largest judgment surface left: two AI states, an energy loop, occupancy,
  and spawn rules whose failure mode is an unwinnable game rather than a crash.
- **T27 → sonnet.** Glyphs, a colour role, one format string. Mechanical, with a binding palette.
- **T28 → opus.** The only cross-cutting task, as T21/T17/T12 were before it: three new state
  fields, a new sub-mode, world-tick ordering, and the death path.

## Dependency graph

```
   T22 (stats, items, status)     T23 (fov LOS)     T24 (keys, events)
        │            │                 │                    │
        │            └────────┬────────┘                    │
        ▼                     ▼                             │
   T25 (combat)          T26 (npc, AI)                       │
        │                     │                             │
        │                     └──────────┬──────────────────┘
        │                                ▼
        │                        T27 (render, style)
        │                                │
        └────────────────┬───────────────┘
                         ▼
              T28 (movement, game, loop)
                         │
                         ▼
       orchestrator: integration + INTEGRATION-v5.md
```

## Execution waves

**Wave 1 — 3 workers.** T22, T23, T24. All leaves, no shared files, no shared dependencies.

**Wave 2 — 2 workers.** T25 (needs stats), T26 (needs stats + LOS).

**Wave 3 — 1 worker.** T27 (needs the NPC species table for glyphs, and events for the stats row).

**Wave 4 — 1 worker.** T28.

**Wave 5 — orchestrator.** Extend `tests/test_integration.py`; re-verify v1–v4 properties; run
the live curses session including a fight, a ranged shot, a level-up and a death; write
`INTEGRATION-v5.md`. `main.py` is expected to need no change.

## Why these seams

- **`combat.py` returns an `AttackResult` and never imports `events`**, exactly as
  `movement.try_move` returns a `MoveResult`. That keeps it a pure calculator testable with two
  `Actor`s and no game, and keeps all wording in one table.
- **`npc.py` returns an `NpcAction` intent and never imports `combat`.** This is the same split
  that put the planners in `activity.py` and `advance` in `game.py` — and it is what stops the
  import graph from cycling. The AI decides; `game.py` executes.
- **`stats.py` owns `Actor`, and `Player`/`NPC` compose one rather than inheriting.** Frozen
  dataclass inheritance with defaults is a known trap, and composition lets `combat.py` be
  written once against one type — which is the whole point of the user's "both for NPC and
  player character" requirement.
- **Spawning lives in `npc.py`, not the generator.** `generator.py` and `dungeon.py` stay
  frozen, and a level's terrain stays independent of its population.
- **T28 is alone in its wave** for the reason its predecessors were: it is the only module that
  sees everything and owns turn semantics.

## Per-wave verification (orchestrator runs these)

| Wave | Command |
|---|---|
| 1 | `.venv/bin/python -m pytest tests/test_stats.py tests/test_items.py tests/test_status.py tests/test_fov.py tests/test_keys.py tests/test_events.py -q` |
| 2 | `.venv/bin/python -m pytest tests/test_combat.py tests/test_npc.py -q` |
| 3 | `.venv/bin/python -m pytest tests/test_render.py tests/test_style.py -q` |
| 4 | `.venv/bin/python -m pytest tests/test_movement.py tests/test_game.py -q` |
| 5 | `.venv/bin/python -m pytest -q` + live curses session |

A task is `done` only when its report exists **and** the orchestrator has personally run its
verification command and seen it exit 0.

> **Interpreter note.** v1–v4 used `.venv/bin/python` (3.14.6). This checkout has no `.venv`;
> the suite was re-verified on **system `python3` 3.11.15 with pytest 9.1.1 — 1982 passed,
> exit 0**. Either interpreter is acceptable; the commands above use `python3`.

## Expected transitional breakage

| Breaks | Cause | Fixed by |
|---|---|---|
| `tests/test_keys.py` — `CommandKind` count 7 → 9 | `FIRE`, `TARGET_NEXT` | **T24** (same owner) |
| `tests/test_events.py` — `EventKind` count 16 → 28, `Event` fields | twelve kinds, two fields | **T24** (same owner) |
| `tests/test_fov.py` — `fov.__all__` | `has_line_of_sight` | **T23** (same owner) |
| `tests/test_style.py` — `Role` count 3 → 4 | `Role.NPC` | **T27** (same owner) |
| `tests/test_movement.py` — `MoveResult` fields | `blocked_by_npc` | **T28** (same owner) |
| `tests/test_game.py` — `GameState` field list | three new fields | **T28** (same owner) |
| `tests/test_integration.py` | all of the above | **orchestrator**, wave 5 |

Per-task suites stay green; the full suite is red from wave 1 until wave 5.

## Risk register

| Risk | Mitigation |
|---|---|
| **A worker "fixes" `block` back to a positive baseline**, restoring the bug that floored every attack to 1 damage | §20.1 states the rule, the reason and the measurement; T22's brief names it as a forbidden change |
| **The attacker accuracy term is reintroduced** into to-hit, making AGI strictly the best stat | §23.1 forbids it explicitly; T25 must assert to-hit is independent of attacker stats |
| **STR applied to natural attacks**, re-flooring animal damage to 1 | §23.2 splits `strength_applies`; T25 must test a low-STR biter |
| **`compute_visible` perturbed by the LOS work** — breaks `tests/test_activity.py`, a frozen file no v5 worker may repair | §14 v5 states it as a hard constraint with the line numbers; T23 must diff-test `compute_visible` output over a whole level before and after |
| **LOS argument order swapped** — measurably wrong 0.28% of the time, invisible in casual testing | §14 v5 fixes `(observer, target)`; T23 must include an asymmetric-pair test |
| **Spawn clustering makes level 1 unwinnable** — two jackals beat a baseline player 100% of the time | §24.4 makes both radii hard rules; T26 must assert them over many seeds, and the orchestrator re-measures independently |
| **Energy loop desync or lockstep packs** | §24.4 seeds staggered energy; T26 must assert a speed-180 NPC acts twice in some tick and a speed-80 NPC skips one |
| **NPCs stack or swap places** | §24.2 occupancy rule; T26 must test two NPCs contending for one cell |
| **World ticks on a rejected move**, breaking v1's headline rule | §7.8 states it; T28 must assert walking into a wall does not advance NPCs |
| **The message line overflows 80 columns** with six NPCs acting | §16.1 caps at 3 with a priority order; T28 must test the cap and the priority |
| **Levelling XP off-by-one** (the defect found in the research draft) | §7.11 gives the exact loop including the subtraction; T28 must test a kill crossing two thresholds |
| **Poison kills via a separate code path** | §7.12 forbids it; T28 must test death by poison ending the run identically |
| **`interruption` left returning `None`** — auto-explore walks into a jackal pack and keeps going, a guaranteed death | §7.14 makes all three conditions live; T28 must test that a hostile entering view stops an activity |
| **The interruption event replaces the turn's events**, discarding `The jackal hits you.` for a bare `You stop.` | §7.14 amends v4 §7.5 to append rather than substitute; T28 must assert both messages survive |

## Known limitations, accepted for this increment

Recorded so they are choices rather than oversights:

- **Levelling saturates.** With a static bestiary the player wins 100% against all four species
  by character level 5. Depth-scaled spawn tables are the fix and are out of scope (§6 research).
- **The NPC half of the status system has no live content.** Only the cave snake applies poison
  and it only bites the player, so nothing in v5 poisons an NPC. The mechanism is shared and
  tested by direct construction — the same honest seam v4 shipped with `interruption()`.
- **Every tuning number is simulated, not playtested.** They are defensible starting points, not
  balanced ones.

## Out of scope

No item pickup, drop, or ground items. No ammunition tracking (infinite by requirement). No
armour slots, critical hits, or weapon variety beyond the two starting weapons. No stat
allocation UI. No monster factions, neutrals, or infighting. No depth-scaled spawn tables. No
NPC regeneration. No save-load to disk. No message history. No mouse. No auto-fight — the player
always chooses to attack.

**Not out of scope, contrary to a first draft of this plan: `interruption` is wired up in v5**
(§7.14). It was deferred in v4 only because the conditions the user named — *"seeing a hostile,
receiving damage, character state change"* — needed monsters and hit points, and both now exist.
Leaving it returning `None` would ship an auto-explore that walks into a jackal pack and keeps
walking, which is a guaranteed death with no chance to react.
