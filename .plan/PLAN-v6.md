# PLAN v6 — decomposition and execution waves

Eight tasks, four waves. Cut along CONTRACT-v6 seams; each owns a disjoint file set (§9 v6).

## Task list

| ID | Title | Owns | Implements | Depends on | Model |
|---|---|---|---|---|---|
| **T29** | Item vocabulary and inventory | `items.py` + test | §25 | — | **opus** |
| **T30** | Input and event vocabulary | `keys.py`, `events.py` + tests | §5 v6, §16 v6 | — | **sonnet** |
| **T31** | Regeneration status effect | `status.py` + test | bandages (§25.1) | — | **sonnet** |
| **T32** | Resistance and the shield roll | `combat.py` + test | §23 v6, §26 | T29 | **opus** |
| **T33** | Species resistances | `npc.py` + test | §26.3 | T29 | **sonnet** |
| **T34** | Chests and depth-scaled loot | `loot.py` + test | §27 | T29 | **sonnet** |
| **T35** | Chest glyph and the inventory screen | `render.py`, `style.py` + tests | §7.17, §27 | T29 | **sonnet** |
| **T36** | Wiring: inventory state, commands, chests | `game.py`, `movement.py` + tests | §7 v6 | T29–T35 | **opus** |

### Model assignment

- **T29 → opus.** The foundation everything else consumes, and the one place a wrong shape is
  expensive to undo. `Inventory`'s four pure functions have real edge cases (a full pack, an
  occupied slot, an item not carried).
- **T30, T31, T33, T34 → sonnet.** Binding tables transcribed and tested.
- **T32 → opus.** The damage pipeline. §26.2 pins a step whose placement was measured at a 2×
  swing, and §23.5's draw order is load-bearing for reproducibility.
- **T35 → sonnet.** One glyph, one colour role, and a screen that reuses `render_text_page`.
- **T36 → opus.** The sole cross-cutting task, as T28/T21/T12 were — and it carries v6's one
  breaking change (`Player.melee` → `Player.inventory.melee`).

## Dependency graph

```
   T29 (items)        T30 (keys, events)      T31 (status)
     │  │  │  │
     │  │  │  └────────► T35 (render, style)
     │  │  └───────────► T34 (loot)
     │  └──────────────► T33 (npc)
     └─────────────────► T32 (combat)
                              │
     all of the above ────────┴──► T36 (game, movement)
                                        │
                        orchestrator: integration + INTEGRATION-v6.md
```

## Execution waves

**Wave 1 — 3 workers.** T29, T30, T31. Leaves, no shared files.

**Wave 2 — 4 workers.** T32, T33, T34, T35. All depend only on T29 and on nothing of each
other's. This is the widest parallel wave the project has run.

**Wave 3 — 1 worker.** T36.

**Wave 4 — orchestrator.** Extend `tests/test_integration.py`, re-establish the full-suite
baseline, run the live curses session (equip a weapon, block a hit, open a chest, drink a
potion), write `INTEGRATION-v6.md`.

## Why these seams

- **`items.py` owns `Resistance` and `DamageType`, not `stats.py`.** `stats.py` is frozen and
  describes a creature's body; a damage type is a property of a weapon. Keeping them together
  means `combat.py` imports one module for the whole damage vocabulary.
- **`loot.py` is separate from `items.py`.** One describes what an item *is*, the other what
  the dungeon *contains*. `loot.py` needs `Level`; `items.py` must stay a leaf.
- **Chests are placed by `loot.py` and stored on `LevelState`, never by the generator.** That
  is the `open_doors` precedent: `Level` is frozen terrain and an opened chest is not terrain.
- **T36 is alone in its wave** for the reason its predecessors were: it is the only module that
  sees everything, and it owns the turn semantics.

## Per-wave verification (orchestrator runs these)

| Wave | Command |
|---|---|
| 1 | `.venv/bin/python -m pytest tests/test_items.py tests/test_keys.py tests/test_events.py tests/test_status.py -q` |
| 2 | `.venv/bin/python -m pytest tests/test_combat.py tests/test_npc.py tests/test_loot.py tests/test_render.py tests/test_style.py -q` |
| 3 | `.venv/bin/python -m pytest tests/test_movement.py tests/test_game.py -q` |
| 4 | `.venv/bin/python -m pytest -q` + live curses session |

A task is `done` only when its report exists **and** the orchestrator has personally run its
verification command and seen it exit 0.

## Expected transitional breakage

| Breaks | Cause | Fixed by |
|---|---|---|
| `tests/test_items.py` — `Weapon` field count | `damage_type`, `grade` | **T29** |
| `tests/test_keys.py` — `CommandKind` 13 → 15 | `INVENTORY`, `PICK_UP` | **T30** |
| `tests/test_events.py` — `EventKind` count | fifteen new kinds | **T30** |
| `tests/test_status.py` — `StatusKind` count | regeneration | **T31** |
| `tests/test_combat.py` — `AttackResult` fields, `resolve_attack` signature | `blocked`, two new params | **T32** |
| `tests/test_npc.py` — `SpeciesData` fields | resistances | **T33** |
| `tests/test_game.py` — `Player` fields, `GameState` fields | **the breaking change** plus three new state fields | **T36** |
| `tests/test_integration.py` | all of the above | orchestrator, wave 4 |

Per-task suites stay green; the full suite is red from wave 1 until wave 4.

## Risk register

| Risk | Mitigation |
|---|---|
| **A worker "fixes" the dagger's 2–5**, which is the reference the whole balance is measured against | §25.1 says do not retune it; §0.1 shows one point of damage is worth 20–40 points of survival |
| **Resistance applied at the wrong point in the pipeline** — measured at a 2× damage swing | §26.2 pins it to the raw roll and spells out the formula; T32 must test the ordering explicitly |
| **`IMMUNE` resurrected to 1 damage by the `max(1, …)` floor** | §26.2 exempts it; T32 must assert `damage == 0` with `hit is True` |
| **A shield implemented as flat reduction** | §0.2 forbids it outright; T32's tests must assert damage is unchanged when a block does not fire |
| **The draw order changed**, breaking reproducibility | §23.5 fixes it at four steps; T32 must count draws for blocked, missed and poisoned attacks |
| **`Player.melee` reads left behind** after the move into `Inventory` | T36 owns every call site; the full suite is the check |
| **Chests placed by the generator**, breaking the frozen-terrain rule | §27.3 forbids `Tile.CHEST`; `generator.py` is frozen |
| Depth table implemented as a formula and drifting from §27.1 | T34 must assert every row of the table literally |

## Known limitations, accepted

- **Shields against arrows have no live caller.** Nothing shoots at the player and no monster
  carries a shield, so §23.6 ships tested and unexercised — the position `interruption` held in
  v4. Recorded as a choice.
- **`Resistance.IMMUNE` has no live user.** The tier exists and is tested; no shipped species
  is immune to anything.
- **A fine shield makes the current bestiary easy** (measured: 85.8% floor clears with a tower
  shield against 45.6% bare). The user accepted this explicitly in exchange for a mechanic that
  keeps scaling when tougher foes arrive.

## Out of scope

No food, no hunger clock. No armour slots. No weight or encumbrance. No item stacking. No
identification, curses or enchantments. No shops. No monster inventories or equipment drops —
chests are the only source. No `Tile.CHEST`. No ammunition tracking; arrows remain infinite.
