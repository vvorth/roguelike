# STATE — v5 (stats, inventory, combat, NPCs, levelling, status effects)

Resume point for an interrupted orchestration. Resume from the first task not marked `done`.
`in_progress` means "not started" — re-run it from its brief.

v1–v4 are **complete and frozen** — see `.plan/INTEGRATION.md`, `-v2.md`, `-v3.md`, `-v4.md`
(1982 tests passing). This file tracks the v5 increment only.

Phase: **planning complete, no tasks dispatched.** RESEARCH-v5, CONTRACT-v5 and PLAN-v5 are
written; CONTRACT-v5 is **frozen**. Task briefs (`.plan/tasks/T22.md` … `T28.md`) are **not yet
written** — that is Phase 3 and the next step.

Interpreter: v1–v4 used `.venv/bin/python` (3.14.6). This checkout has no `.venv`; the full
suite was re-verified on **system `python3` 3.11.15 with pytest 9.1.1 — 1982 passed, exit 0**.

| Task | Title | Wave | Depends on | Model | Status | Report | Verified |
|---|---|---|---|---|---|---|---|
| T22 | Stats, items, status effects | 1 | — | sonnet | **done** | `reports/T22.md` | **yes — 57 passed, exit 0** |
| T23 | Point-to-point line of sight | 1 | — | opus | **done** | `reports/T23.md` | **yes — 158 passed + frozen suite 55 passed, exit 0** |
| T24 | Input and event vocabulary | 1 | — | sonnet | **done** | `reports/T24.md` | **yes — 290 passed, exit 0** |
| T25 | Combat resolution | 2 | T22 | sonnet | **done** | `reports/T25.md` | **yes — 43 passed, exit 0** |
| T26 | NPCs, AI and spawning | 2 | T22, T23 | opus | **done** | `reports/T26.md` | **yes — 113 passed, exit 0** |
| T27 | Rendering NPCs and the stats row | 3 | T24, T26 | sonnet | **done** | `reports/T27.md` | **yes — 213 passed, exit 0** |
| T28 | Bump-to-attack, NPC turns, targeting, levelling | 4 | T22–T27 | opus | **done** | `reports/T28.md` | **yes — 608 passed, exit 0** |

A task is `done` only when its report exists **and** the orchestrator has personally run its
verification command and seen it exit 0.

## Verification commands

| Task | Command |
|---|---|
| T22 | `.venv/bin/python -m pytest tests/test_stats.py tests/test_items.py tests/test_status.py -q < /dev/null` |
| T23 | `.venv/bin/python -m pytest tests/test_fov.py -q < /dev/null` |
| T24 | `.venv/bin/python -m pytest tests/test_keys.py tests/test_events.py -q < /dev/null` |
| T25 | `.venv/bin/python -m pytest tests/test_combat.py -q < /dev/null` |
| T26 | `.venv/bin/python -m pytest tests/test_npc.py -q < /dev/null` |
| T27 | `.venv/bin/python -m pytest tests/test_render.py tests/test_style.py -q < /dev/null` |
| T28 | `.venv/bin/python -m pytest tests/test_movement.py tests/test_game.py -q < /dev/null` |
| Phase 5 | `.venv/bin/python -m pytest -q < /dev/null` + live curses session |

## Files frozen for v5 — nobody may edit

`roguelike/tiles.py`, `level.py`, `generator.py`, `world.py`, `dungeon.py`, `pathfind.py`,
`activity.py` and their test files.

Orchestrator-owned: `main.py`, `tests/test_integration.py`, `roguelike/__init__.py`,
`pytest.ini`, everything under `.plan/` except `.plan/reports/TNN.md`.

**Freeze safety verified, not assumed** (the lesson v3 taught and v4 recorded). Searched every
frozen suite for surfaces v5 changes:

- The only `__all__` assertions in frozen suites are `tests/test_dungeon.py:369` and
  `tests/test_activity.py:730`, each pinning **its own** module — v5 changes neither.
- The other three `__all__` assertions (`test_render.py`, `test_fov.py`, `test_game.py`) are all
  owned by a v5 worker (T27, T23, T28).
- **`tests/test_activity.py` drives the real `fov.compute_visible`** at lines 769 and 808. It is
  frozen, so `compute_visible` must not change behaviour — recorded in CONTRACT-v5 §14 v5 as a
  hard constraint rather than a preference, and it is T23's headline risk.
- No frozen suite mentions `CommandKind`, `EventKind`, `GameState`, `MoveResult` or `Role`.

**No frozen file contains an assertion v5 invalidates.**

## Expected transitional breakage — planned, each with an owner

| Breaks | Cause | Fixed by |
|---|---|---|
| `tests/test_keys.py` — `CommandKind` 7 → 9 | `FIRE`, `TARGET_NEXT` | T24 (same owner) |
| `tests/test_events.py` — `EventKind` 16 → 28, `Event` fields | twelve kinds, two fields | T24 (same owner) |
| `tests/test_fov.py` — `fov.__all__` | `has_line_of_sight` | T23 (same owner) |
| `tests/test_style.py` — `Role` 3 → 4 | `Role.NPC` | T27 (same owner) |
| `tests/test_movement.py` — `MoveResult` fields | `blocked_by_npc` | T28 (same owner) |
| `tests/test_game.py` — `GameState` field list | three new fields | T28 (same owner) |
| `tests/test_integration.py` | the above | orchestrator, wave 5 |

## Log

- **Re-checked the v5 research with a stronger model at the user's request, and it did not
  survive contact.** The first draft's formulas were internally consistent and completely
  unplayable. Seven defects, found by *simulating* rather than reading:
  - **No healing existed anywhere in the design.** Simulated: the player dies after a median of
    **2 kills out of 12** and **0.0%** of runs clear a floor — levelling was dead content
    because you die before level 2. Fixed by adding regeneration (§22.4) and re-scaling HP.
  - **Flat `block = STR//2` floored every attack in the game to 1 damage**, making all four
    species mechanically identical. Fixed to `max(0, (STR-10)//2)`.
  - **The attacker's STR modifier was double-counted on natural attacks** — a rat's 1–3 bite
    already encodes rat-ness. Split into `strength_applies`.
  - **AGI triple-dipped** (speed + evasion + to-hit), contradicting the draft's own stated
    one-identity-per-stat principle. Attacker accuracy term removed.
  - **The jackal was unwinnable** — 98.8% player loss. Retuned via a measured sweep.
  - **The XP formula was off by one against its own prose** — level 2 would have cost 100 XP
    where the text promised 25.
  - **My own Dijkstra-map recommendation was wrong.** Measured, it is *slower* until ~12–15
    simultaneous hunters; per-hunter A\* is 0.5 ms and is already written and tested. Dropped in
    favour of writing no new pathfinding code.
- Two first-draft claims **survived verification unchanged**: the bestiary stat blocks fall out
  of the formulas with no special cases, and permissive FOV costs **14.888 ms** at radius 20.
- **The strongest performance finding held up**: a dedicated bounding-box LOS check costs
  **0.167 ms** against **7.3 ms** for an equivalent `compute_visible` call — **44× cheaper**
  across 30 checks (5.0 ms vs 218.8 ms). Reusing `compute_visible` for NPC awareness would blow
  the turn budget at ~14 simultaneous checks. This is why §14 v5 exists.
- **Permissive LOS is not symmetric** — measured, **2 of 720 pairs (0.28%)** disagree on who can
  see whom. CONTRACT-v5 §14 v5 fixes the argument order as `(observer, target)` so the asymmetry
  is defined behaviour rather than a coin flip.
- **A latent trap recorded for the Dijkstra escape hatch**, should it ever be taken: the obvious
  downhill rule (minimise `map[neighbour]`) is **wrong** on a 10/14 cost model and picked a
  suboptimal step in **60 of 300** positions. The correct rule minimises `step_cost +
  map[neighbour]`, verified optimal in **300/300**. Easy to write, looks right, produces NPCs
  that merely seem a little dim.
- Four user decisions collected before drafting: energy-based scheduler · full off-FOV
  simulation on the current level · manual cursor targeting · minimal combat depth.
- CONTRACT-v5 written and **frozen**: adds §0.12 (derived RNG, never stored), §0.13 (integer
  stats and the floor-division trap), §20 `stats`, §21 `items`, §22 `status`, §23 `combat`,
  §24 `npc`; amends §14 (`has_line_of_sight`), §5 (input), §16 (events + a message-line cap),
  §7 (player, NPC turns, targeting, levelling, death), §4/§15 (NPC rendering), §9, §10, §11.
- **Caught while writing PLAN-v5: `interruption` must be wired up in v5, not deferred again.**
  A first draft of the plan listed it as out of scope. But RESEARCH-v4 §2 records it as a direct
  user requirement — *"automatically cancelled … (seeing a hostile, receiving damage, character
  state change)"* — deferred in v4 only because monsters and hit points did not exist. Both now
  do. Leaving it returning `None` would ship an auto-explore that walks into a jackal pack and
  keeps walking; since **two jackals beat a baseline player 100% of the time** (600 runs, zero
  wins), that is a guaranteed death with no chance to react. Now CONTRACT-v5 §7.14, with an
  amendment to v4 §7.5 so the interruption event is **appended** to the turn's events rather
  than substituted for them — otherwise `The jackal hits you.` would be discarded in favour of a
  bare `You stop.`
- PLAN-v5 written: 7 tasks, 4 waves plus orchestrator integration. Model assignments per policy;
  opus on T23 (exact geometry against a frozen consumer), T26 (AI whose failure mode is an
  unwinnable game, not a crash) and T28 (the sole cross-cutting module).
- Three limitations accepted and recorded as choices rather than oversights: levelling
  **saturates** by character level 5 against a static bestiary; the **NPC half of the status
  system has no live content** (nothing in v5 poisons an NPC — the same honest seam v4 shipped
  with `interruption`); and every tuning number is **simulated, not playtested**.

## Execution log (v5)

- **Environment changed mid-increment.** The session was resumed onto a different container:
  system `python3` is now **3.14.4 with no pytest**, where earlier in the same session it was
  3.11.15 with pytest installed. T22's worker hit this and correctly rebuilt `.venv`
  (Python 3.14.4, pytest 9.1.1) — which is the interpreter v1–v4 documented all along. **All
  verification commands in PLAN-v5, STATE-v5 and every task brief were switched to
  `.venv/bin/python`** so no further worker loses time rediscovering it. Consequence to note at
  integration: the 1982-passing v4 baseline was measured on the *old* container and must be
  re-established here.
- Wave 1 dispatched: T22 (sonnet), T23 (opus), T24 (sonnet) in parallel.
- **T22 done and verified: 57 passed, exit 0.** Orchestrator re-derived every number
  independently rather than trusting the worker's own suite, because three downstream tasks
  consume them: baseline `Derived(45, 100, 5, 0)`; **`block` is 0 at and below baseline** and
  first becomes 1 at STR 12 (the measured fatal bug is not back); all four bestiary stat blocks
  fall out of the formulas with no special cases; evasion clamps to 0 and 60; `apply_effect`
  leaves the tuple unchanged for a shorter *or equal* duration and never stacks; `tick_effects`
  still deals its damage on the tick that removes the effect.
- T22 raised one item for T25, correctly: the STR damage modifier faces the same
  floor-toward-−∞ rounding trap `block` does, but **unclamped** — so a low-STR attacker's
  modifier goes genuinely negative. Already covered by §0.13 and named in T25's brief.
- T25 dispatched (sonnet) as soon as T22 cleared, since it depends on T22 alone.
- **T24 done and verified: 290 passed, exit 0.** Orchestrator independently checked all twelve
  new messages **character for character**, and ran its own full v1–v4 binding sweep from a
  hand-written table: all eight deltas from every key, `KEY_SR` still Shift+Up and **not
  inverted**, `K == u == 9`, `q/Q/>/</E/w` intact, and `5/Y/U/B/N/e/W/ESC` still `UNKNOWN`.
  `set(MESSAGES) == set(EventKind)` with 28 each. Multi-event joining works — v5 is the first
  caller to need it.
- T24 made one change worth recording: two pre-existing "unknown key" fixtures in
  `tests/test_keys.py` listed `"\t"` and bare `9`, both of which are now legitimately
  `TARGET_NEXT` (`ord("\t") == 9 == curses.ascii.TAB`). It removed those two entries with
  explanatory comments rather than weakening the sweep — the orchestrator's independent sweep
  confirms every other binding survived.

- **T25 done and verified: 43 passed, exit 0.** Orchestrator tested **both named traps directly**
  rather than trusting the worker's suite, because each is a previously-measured fatal bug:
  - *No attacker accuracy term*: an AGI 3 attacker and an AGI 18 attacker disagreed on hit/miss
    in **0 of 400** seeded rolls. To-hit genuinely depends on the defender alone.
  - *STR not applied to natural attacks*: a rat (STR 4, modifier −3) biting for 1–3 produces the
    **full `{1, 2, 3}` range**, not the floored `{1}` the old formula gave. The same rat
    *wielding* a 1–3 weapon does floor to `{1}`, which is correct — the modifier applies there.
  - Independent confirmation of the split: raising STR 10 → 16 lifts wielded damage 3.49 → 6.49
    and leaves natural damage **identically** 3.49.
  - Block subtracts exactly 3 against a STR-16 defender; a miss draws exactly one value from the
    rng and a poisoned hit exactly three, so the documented draw order holds; poison fires on
    29.4% of hits at `poison_chance=30` and never on a miss.
- T25 imports **only** `roguelike.stats` — fewer than §10 v5 permits, which is fine (the caller
  passes damage bounds and poison chance as plain ints). No forbidden import, no float literal.
- **T25 flagged a real integration hazard**, correctly: `resolve_attack` cannot tell that a
  weapon is ranged, so **T28 must pass `strength_applies=False` for the shortbow**. `combat.py`
  cannot enforce that asymmetry itself. Already an explicit acceptance criterion in T28's brief;
  the orchestrator will check it at integration.

- **T23 done and verified: 158 passed, exit 0 — and `tests/test_activity.py` still 55 passed,
  unedited.** That frozen suite was the headline risk of the whole increment, so the orchestrator
  ran it directly and checked `git diff` twice: the **only** deleted line in `fov.py` is the old
  `__all__`, so `compute_visible`, `_segment_is_clear`, `_SAMPLE_OFFSETS` and `DEFAULT_RADIUS`
  are provably untouched. The work is genuinely additive.
- Orchestrator's independent checks: **0 disagreements in 8,378** comparisons of
  `has_line_of_sight` against `compute_visible` over whole radius-20 discs from 12 origins — the
  check that proves the geometry was reused rather than reimplemented differently. Door
  occlusion confirmed by finding a cell that can see past a door **only** when it is open.
  **65× faster** than the naive approach here (0.134 ms per call vs 262 ms for 30
  `compute_visible` calls); the research predicted 44×.
- **Asymmetry confirmed and, importantly, shown not to be T23's doing.** On `generate_level(1)`,
  `(54,16) → (57,9)` is `True` while `(57,9) → (54,16)` is `False` — and **`compute_visible`
  reports exactly the same asymmetry in both directions**, so it is inherent to permissive FOV,
  not introduced here. Independently measured rate: **4 of 2,992 pairs (0.13%)**, the same order
  as the research's 0.28%. §14 v5's binding `(observer, target)` argument order is therefore
  doing real work.
- **T23 challenged an acceptance criterion, and it was right to.** The brief demanded a case that
  *fails* without the one-cell snapshot margin; T23 reported that no such case exists and pinned
  the box with a spy test instead of quietly deviating — exactly the behaviour the process wants.
  The orchestrator verified this and **initially got it wrong**: a first spy counted
  `is_transparent` calls, which measure snapshot *population* (widened by the margin by
  definition) and appeared to refute the claim at 57,358/193,049. Re-instrumented to record what
  the segment traversal actually *looks up* in the opacity dict, the answer is **0 of 34,579
  lookups outside the tight bounding box across two levels and 1,000 pairs — claim CONFIRMED.**
  The margin is defence in depth, never load-bearing.
  **Contract note:** §14 v5 keeps the margin. It costs a little snapshot work and cannot change
  an answer, and amending a frozen contract mid-increment for a micro-optimisation is the worse
  trade. Recorded here so a future reader knows the requirement is belt-and-braces, not a
  correctness dependency.

- **T26's first worker was killed mid-task by an API session limit**, after writing
  `roguelike/npc.py` in full but before its tests or report — further along than its final
  message suggested. Nothing else was lost (the four finished tasks stayed green at 548 passed).
  A **second worker was dispatched to write only the two missing files**, explicitly instructed
  to treat `npc.py` as finished output and not rewrite it. It did exactly that: `npc.py` is
  **byte-identical** (18,644 bytes) before and after, and no test revealed a contract violation.
  This is the same recovery shape v4 used when two workers died before writing reports.
- **T26 done and verified: 113 passed, exit 0**, stable across `PYTHONHASHSEED` 0 and 9999.
  Orchestrator checks, run independently of the worker's suite:
  - Bestiary matches §24.1 exactly, and all four stat blocks **derive with no special cases**.
  - **Spawn rules over 720 NPCs in 120 runs on three levels: 0 safe-radius violations, 0
    separation violations**, `actor_id`s sequential from 1, every `hp` at full derived `max_hp`.
  - **Lockstep trap avoided: 95 distinct starting energies** spanning 0–99.
  - Deterministic on repeated seeds; on a level too small it returns **fewer** NPCs (4) in
    0.32 ms rather than hanging or relaxing a radius.
  - AI: attacks from all eight adjacent cells; a distant hunt steps adjacent-and-strictly-closer;
    a walled-off player gives `WAIT` not an exception; a wandering NPC that spots the player
    **hunts the same turn and draws no randomness doing it**; perception correctly fails beyond
    radius 10 and through a wall; **a closed door blocks perception and the same door open does
    not**; occupied cells are never targeted; `plan_action` has no `explored` parameter, so it
    structurally cannot be fogged.
- **Planning cost re-measured by the orchestrator: median 5.08 ms per tick for six simultaneous
  hunters (0.85 ms each), max 15.91 ms — 9.8x inside the 50 ms budget.** The worker measured a
  median of 8.57 ms and flagged it as above RESEARCH-v5's 0.5 ms/hunter reference; both readings
  are comfortably within budget, and the gap is explained by spawn placement putting hunters far
  from the player, near A\*'s worst case. **No optimisation is warranted** — the Dijkstra-map
  escape hatch stays unused, as §8 of the research concluded.
- **T26 raised one rule T28 must honour:** `plan_action` is pure and sees one NPC at a time, so
  "two NPCs never take the same cell" only holds if **`game.py` folds each accepted move into
  `occupied` before planning the next NPC's action**. The contention guarantee lives in the
  caller, not the planner. This is the T26-to-T28 equivalent of v4's `- {player}` frontier guard,
  and it must be tested.

- **T27 done and verified: 213 passed, exit 0.** Orchestrator checks: `Role` has 4 members; the
  binding species palette is exact (rat 250, jackal 173, giant bat 140, cave snake 70), red at 8
  colours, `-1` monochrome; `(Role.NPC, EXPLORED)` and `(Role.NPC, UNSEEN)` both raise.
  **The headline rule holds** — rendering the same NPC with its cell in `visible` versus in
  `explored` only differs in exactly one cell, which shows `j` in the first case and the floor
  glyph `.` in the second, so a monster is never drawn from memory. Target highlight sets
  `reverse` on exactly one cell and leaves its glyph `j` untouched; with no target, no cell is
  reversed. The player glyph wins a shared cell. **The pre-v5 six-argument call still renders**,
  so nothing downstream breaks.
- T27 added `species` as an optional parameter to `attr_for` and a `NpcGlyph(position, glyph,
  species)` value to `render.py`, carrying species as a **plain lower-case string** rather than
  importing `npc.py` — which is what keeps the renderer usable without the monster module, as
  §10 v5 requires. `Cell` gains `species` and `reverse`, both defaulted.
- **The exact call T28 must make** (from T27's report):
  `render_to_cells(level, player_pos, visible, explored, open_doors, chrome, npcs, target)`
  with `npcs = tuple(NpcGlyph(n.position, SPECIES_DATA[n.species].glyph,
  SPECIES_DATA[n.species].name) for n in state.npcs)` and
  `target = state.targeting.targets[state.targeting.index] if state.targeting else None`.

- **T28 done and verified: 608 passed, exit 0.** Full suite at end of wave 4:
  **2488 passed, 17 failed — every failure in `tests/test_integration.py`**, the orchestrator's
  file, exactly as PLAN-v5 predicted.
- **T28 escalated three items rather than improvising. One was a genuine contract defect —
  mine.** See `.plan/reports/T28.md` and CONTRACT-v5 §11.1 for the full record.
  1. *Levelling parity*: §7.11 and the T28 brief contradicted each other. Settled against the
     RESEARCH-v5 simulation that produced every published balance number — it computes parity on
     the **new** level, identical to the contract. **The brief's acceptance criteria were the
     orchestrator's error**; the worker followed the contract and was right. No code change.
  2. *Message ordering*: selecting by priority band while emitting in emission order is the
     correct reading of §16.1. `"You hit the rat. You kill the rat! Welcome to level 2."` reads
     causally; priority order would announce the level-up before the kill that caused it. Kept.
  3. *The empty-level defect*: §11 v5 said `advance_npcs` returns unchanged with no NPCs, which
     contradicted §7.8's "status and regeneration first". Measured on the pre-fix build:
     **40 ticks at 10 hp on a cleared floor left the player at 10 hp** (14 with monsters alive),
     and **a 5-turn poison still read `remaining_turns=5` after 10 ticks** — frozen, never
     damaging, never expiring. Since RESEARCH-v5 §7 measured **0.0%** floor clears without
     regeneration against **61.5%** with it, and much of the ~180 exploration turns per level
     fall *after* the monsters are dead, this silently restored the unplayable balance — and it
     would never have failed a test, only felt unfair. **CONTRACT-v5 §11.1 issued**; the fix was
     removing one early-return guard. Re-verified: 10 → 14 hp over 40 ticks on a cleared floor.
- **T28's worker was cut off by a session limit twice** — the second time after applying the fix
  and its seven new tests, while correcting a test comment that turned out to already be right.
  The orchestrator verified the fix directly and wrote the report addendum, marked as
  orchestrator-authored (the v4 precedent for a dead worker's record).
- **All seven v5 tasks are done and independently verified.** Wave 5 (integration) is next.

## Next step

Phase 3 is complete — all seven briefs are written. **T22 and T24 are done and verified; T23
and T25 are in flight.**

- **T26** unblocks the moment T23 is verified (it needs `fov.has_line_of_sight`).
- **T27** unblocks when T26 lands (it needs the species glyphs; T24 is already done).
- **T28** needs everything.
- Then orchestrator integration: extend `tests/test_integration.py`, re-establish the full-suite
  baseline **on this container**, run the live curses session (a fight, a ranged shot, a
  level-up, a death), and write `INTEGRATION-v5.md`.
