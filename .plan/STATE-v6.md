# STATE — v6 (inventory, weapons, damage types, shields, consumables, chests)

Resume point for an interrupted orchestration. Resume from the first task not marked `done`.
`in_progress` means "not started" — re-run it from its brief.

v1–v5 are complete; the suite stood at **2615 passed, 0 failed** when v6 began.
Interpreter: `.venv/bin/python` (Python 3.14.4, pytest 9.1.1).

| Task | Title | Wave | Depends on | Model | Status | Report | Verified |
|---|---|---|---|---|---|---|---|
| T29 | Item vocabulary and inventory | 1 | — | opus | **done** | `reports/T29.md` | **yes — 90 passed, exit 0** |
| T30 | Input and event vocabulary | 1 | — | sonnet | **done** | `reports/T30.md` | **yes — 362 passed, exit 0** |
| T31 | Regeneration status effect | 1 | — | sonnet | **done** | `reports/T31.md` | **yes — 31 passed, exit 0** |
| T32 | Resistance and the shield roll | 2 | T29 | **orchestrator** | **done** | `reports/T32.md` | **yes — 64 passed, exit 0** |
| T33 | Species resistances | 2 | T29 | sonnet | **done** | `reports/T33.md` | **yes — 142 passed, exit 0** |
| T34 | Chests and depth-scaled loot | 2 | T29 | sonnet | **done** | `reports/T34.md` | **yes — 62 passed, exit 0** |
| T35 | Chest glyph and inventory screen | 2 | T29 | sonnet | **done** | `reports/T35.md` | **yes — 253 passed, exit 0** |
| T36 | Wiring: inventory, commands, chests | 3 | T29–T35 | opus | **done** | `reports/T36.md` | **yes — 750 passed, exit 0** |

A task is `done` only when its report exists **and** the orchestrator has personally run its
verification command and seen it exit 0.

## Verification commands

| Task | Command |
|---|---|
| T29 | `.venv/bin/python -m pytest tests/test_items.py -q < /dev/null` |
| T30 | `.venv/bin/python -m pytest tests/test_keys.py tests/test_events.py -q < /dev/null` |
| T31 | `.venv/bin/python -m pytest tests/test_status.py -q < /dev/null` |
| T32 | `.venv/bin/python -m pytest tests/test_combat.py -q < /dev/null` |
| T33 | `.venv/bin/python -m pytest tests/test_npc.py -q < /dev/null` |
| T34 | `.venv/bin/python -m pytest tests/test_loot.py -q < /dev/null` |
| T35 | `.venv/bin/python -m pytest tests/test_render.py tests/test_style.py -q < /dev/null` |
| T36 | `.venv/bin/python -m pytest tests/test_movement.py tests/test_game.py -q < /dev/null` |
| Wave 4 | `.venv/bin/python -m pytest -q < /dev/null` + live curses session |

## Files frozen for v6 — nobody may edit

`tiles.py`, `level.py`, `generator.py`, `world.py`, `dungeon.py`, `pathfind.py`,
`activity.py`, `fov.py`, `stats.py` and their tests.

Orchestrator-owned: `main.py`, `tests/test_integration.py`, `roguelike/__init__.py`,
`pytest.ini`, everything under `.plan/` except `.plan/reports/TNN.md`.

**Freeze safety verified, not assumed.** `stats.py` is frozen and v6 changes nothing in it —
resistance lives in `items.py` and is applied in `combat.py`, deliberately, so the frozen
module describing a creature's *body* never learns about weapon damage types. No frozen suite
asserts on `Weapon`'s fields, `Player`'s fields or `AttackResult`'s fields; those assertions
live in `test_items.py`, `test_game.py` and `test_combat.py`, each owned by a v6 worker.

## Log

- **The research went through two rounds before anything was built.** The user chose full
  resistances over flavour-only, overriding RESEARCH-v6 §0's caution deliberately: resistance
  keeps scaling against stronger foes, and being untouchable to a jackal with a good shield is
  the intended feel. The cost — a fine shield makes *today's* bestiary easy — is recorded in
  CONTRACT-v6 as an accepted trade, not a warning.
- **Then the orchestrator asked what "sweeping resistance" actually meant, and measuring it
  killed two proposals:**
  - **Percentage resistances are unusable at these damage numbers.** On a 2–5 roll, 25% and 33%
    give the identical outcome set, and 66% and 75% are both flat 1 — above about half, every
    percentage is the same percentage. Worse, the *same* 50% resistance yields average damage
    from **1.25 to 2.50 — a 2× swing — purely from where in the pipeline it is applied.**
    Hence coarse tiers (IMMUNE / RESISTANT / NORMAL / VULNERABLE) applied at one pinned point,
    the raw roll, before strength and block (§26.2).
  - **The proposed weapon tiers were both wrong.** Measured floor-clear rates: 1–4 clears
    **2.2%**, 2–5 clears **45.6%**, 4–8 clears **98.3%**. One point of damage is worth 20–40
    percentage points of survival. RESEARCH-v6's crude 1–4 and fine 4–8 are rejected; shipped
    tiers are 2–4 / 2–5 / 3–5.
- Shields re-measured end to end and **cut from the proposed 15/25/35 to 10/18/25** — the
  proposal measured at 72/86/94% floor clears against a 45.6% bare baseline, which was too
  strong even by the user's accepted trade.
- Resistance measured realistically (one species resisting, not all): **45.6% → 8.5%** floor
  clears when the player has no alternative weapon type. That severity is the point — it is
  the pressure that makes carrying a second weapon matter, and it is why the starting dagger
  is PIERCE and the cave snake resists PIERCE.
- CONTRACT-v6 written and **frozen**: §0 (the measurements), §25 `items`, §26 resistance and
  the damage pipeline, §23 v6 (combat gains resistance and the shield roll), §27 `loot`,
  §5 v6, §16 v6, §7 v6, §9 v6, §10 v6, §11 v6.
- PLAN-v6 written: 8 tasks, 3 waves plus integration. **Wave 2 is four workers wide — the
  widest this project has run** — because T32/T33/T34/T35 all depend on T29 alone and share no
  files.
- Wave 1 dispatched: T29 (opus), T30 (sonnet), T31 (sonnet).
- **One decision was deliberately left to a worker.** T31 must change `tick_effects`'s return
  shape to carry healing as well as damage. The brief recommends a third value over a signed
  net — a net of zero is ambiguous between "nothing happened" and "2 damage and 2 healing",
  which the game loop words differently — but the choice is T31's, and its report is where two
  downstream tasks will read it.

- **T31 done and verified: 31 passed, exit 0.** It took the recommended shape:

  ```python
  def tick_effects(effects) -> tuple[tuple[StatusEffect, ...], int, int]:
      return surviving_effects, total_damage, total_healing
  ```

  **T32 and T36 must read this**: three values, not a signed net. A net of zero cannot tell
  "nothing happened" from "2 damage and 2 healing", and the game loop words those differently.
- Orchestrator checks, run independently: `StatusKind` is exactly
  `{POISONED, ENRAGED, REGENERATING}`; `REGEN_TURNS` still 3; regeneration heals its magnitude
  per tick for exactly its duration and **still heals on the tick that removes it**, the rule
  poison already followed; poison and regeneration coexist and stay separable (2 damage and
  3 healing, not a net of 1); refresh-not-stack holds for the new kind; every v5 poison
  behaviour is unchanged; inputs unmutated.
- **T31 made one decision the contract did not cover and flagged it**: `ENRAGED` entries tick
  down and expire but contribute to neither total. Correct — it is a behavioural flag, not a
  damage-over-time effect — and it is now tested, where before nothing exercised that path.

- **T29 done and verified: 90 passed, exit 0.** Orchestrator re-checked every table
  independently: all six weapons, all three shields (10/18/25, the measured values, not the
  rejected 15/25/35), both consumables, and **the dagger still 2–5 / shortbow still 1–4** —
  the reference the whole balance rests on. `Resistance` and `Grade` order correctly; the v5
  `Weapon(...)` call shape still constructs; equip/unequip/add/drop are pure and a full pack
  refuses without changing anything; `items.py` imports nothing but `__future__`, `dataclasses`
  and `enum`.
- **T29 made four decisions where §25 is silent, and reported all four**: `equip` on a
  `Consumable` raises (so T36 must dispatch "use" before calling it), `unequip` into a full
  pack is a no-op so the cap stays true, an unknown slot name raises, and a negative `drop`
  index counts as out of range. All tested.
- **T30 done and verified: 362 passed, exit 0.** `CommandKind` 15, `EventKind` 51,
  `set(MESSAGES) == set(EventKind)`. Orchestrator ran its own full v1–v5 binding sweep — all
  eight deltas from every key, `KEY_SR` still not inverted, and `q/Q/>/</E/w/f/Tab/?/a/x/R`
  intact — plus all fifteen new messages character for character. **`e`, `d` and `t` verified
  still `UNKNOWN`**, which the inventory screen depends on.
- **T30 caught a documentation drift and correctly refused to resolve it.** CONTRACT-v5 §16
  still listed `SPOTTED_HOSTILE` as `A {name} comes into view!` while the code says
  `There is a {name} in view.` That was **the orchestrator's own omission**: the message was
  reworded when the interrupt rule widened from "newly visible" to "any visible hostile", and
  the code and tests were updated but the contract row was not. Now fixed as **CONTRACT-v5
  §16.2**, an amendment recording that the code was right and the contract was stale. The
  worker reporting rather than silently aligning one to the other is exactly the behaviour the
  process wants.
- Wave 2 dispatched, four workers wide: T32 (opus), T33, T34, T35 (sonnet).

- **Wave 2 was wiped out mid-flight by a *weekly* API limit** — all four workers died at once.
  Salvage: T32 had written only its docstring, imports and `__all__`; T33 had added the
  `SpeciesData.resistances` field and left three tests failing; T34 and T35 had written
  nothing at all. The limit reset at 04:00 UTC and T33/T34/T35 were relaunched, T33 with an
  explicit account of the partial state it was inheriting so it would finish rather than
  restart.
- **T32 was finished by the orchestrator in the main thread** rather than re-dispatched: it is
  the most delicate task in the increment and the critical path, and a fourth concurrent agent
  against an uncertain limit was the worse bet. Recorded as orchestrator-authored in its report.
- **T32 done and verified: 64 passed, exit 0.** Both measured traps verified directly:
  - *Resistance is on the raw roll.* With STR 16 (+3), dagger 2–5, block 2 and `RESISTANT`, the
    three candidate placements give **{2,3}** (raw roll — the contract), **{1,2}** (after the
    STR modifier) and **{1,2,3}** (after block). Measured {2,3}. The test names all three, so
    moving the step fails loudly.
  - *`IMMUNE` is not resurrected by the `max(1, …)` floor*: damage 0 with `hit is True`.
  - Draw order confirmed by counting: miss 1, blocked 2, hit 2, hit+poison 3, `IMMUNE` 1.
- **A test-design trap found and recorded while writing T32.** The obvious "a shield never
  subtracts" test — same seed, `shield_block=0` versus `25`, assert equal damage — **is wrong
  and fails**, reporting 253 mismatches: the shield roll consumes a draw and shifts the rng
  stream, so the two calls legitimately roll different damage. The correct check is
  distributional (identical value set `{2,3,4,5}`, means 3.515 vs 3.516). Both assertions ship
  with a comment explaining why the per-seed form is invalid, since it is exactly the mistake
  the next reader would make.
- **T33 done and verified: 142 passed, exit 0.** The §26.3 table checked across all 4 species ×
  3 damage types; nothing is `IMMUNE`; **every existing number byte-unchanged** (stats, attack
  ranges, xp, poison, flee chances 2/5/3/1, glyphs, names, hostility) and derived values still
  come from `stats.derive`.
  The lookup is **`resistance_of(species, damage_type) -> Resistance`**, two positional
  arguments, defaulting to `NORMAL` for anything absent — T36 reads this.
- **The intended pressure is confirmed working**: the starting dagger is PIERCE, the cave snake
  is RESISTANT to PIERCE, and the club is BLUNT and NORMAL against it. Carrying a second weapon
  is the answer to the wall, which is the whole reason inventory exists.

- **T35 done and verified: 253 passed, exit 0.** Chest glyph `~`, checked disjoint from every
  tile, species, player and projectile glyph; `Role.CHEST` at 220 visible / 178 explored, an
  xterm gold ramp distinct from every other role. `style.py` still imports no `curses`;
  `render.py` still imports none of `items`, `loot`, `npc`, `game`, `combat`, `stats`, `events`
  — chest positions arrive as a plain `frozenset` parameter, appended last with a default, and
  both the 6-argument and 7-argument pre-v6 call shapes still render.
- **T35 took the recommended visibility decision and gave the right reason**: a chest **is**
  drawn from `explored`, unlike a monster. A monster seen an hour ago has moved, so drawing it
  from memory is a lie; a chest has not moved, so remembering it is the same kind of fact
  terrain memory already keeps — and it is what makes a remembered chest worth walking back to.
  Verified: drawn when visible, drawn when merely explored, **not** drawn when never seen. A
  monster and the player still win a shared cell.
- **T35 built no second full-screen layout**, as instructed: the inventory screen is
  `render_text_page` — the help screen's function — with different lines, and `render_text_page`
  itself was left untouched.
- **The final renderer call for T36** is
  `render_to_cells(level, player_pos, visible, explored, open_doors, chrome, npcs, target,
  projectile, chests)` where `chests` is a `frozenset[Coord]`.

- **T34 done and verified: 62 passed, exit 0.** Every row of the §27.1 table literal and
  summing to 100; `fine` monotonic and 1% at depth 1; placement over 600 attempts on four
  levels with **zero** violations of passability, the safe radius or the 1–3 content count;
  deterministic on a repeated seed; returns `None` in 0.01 ms on a level with no legal cell;
  imports only `items` and `level`.
- **T34 solved a real problem cleverly, and the orchestrator re-verified the claim rather than
  trusting it.** `loot.py` may not import `world.py` or `tiles.py`, so it cannot ask whether a
  cell is a door. It proved that `is_walkable(x, y) and not any(room.on_perimeter(x, y) …)` is
  exactly "passable with no door open", from the generator's own invariants. Independently
  checked against the real `world.is_passable` over **183,285 walkable cells on 300 levels: 0
  disagreements.**
- **T34 found a genuine contradiction in CONTRACT-v6 and reported rather than improvising.**
  §27.2 required a chest not to be placed on a monster's cell, but §27's binding signature is
  `place_chest(rng, level, depth)` — no monster list — and §10 v6 forbids `loot.py` from
  importing `npc.py`. The rule was unsatisfiable by the module that owned it. **Now
  CONTRACT-v6 §27.4**: the constraint moves to `game.py`, which places monsters after chests
  and already owns the actor-occupancy invariant.
- **§27.5 also issued**, adopting T34's other decision: `Consumable` has no `grade`, so the
  weights cannot select one — consumables are drawn at every depth. Chests being the only item
  source in v6, any other rule would make potions and bandages unobtainable outright.
- **Wave 2 complete. All seven modules done and verified — 942 tests across them.**
- Wave 3 dispatched: T36 (opus), the sole cross-cutting task.

- **T36 done and verified: 627 + 123 passed.** Full suite **3006 passed, 0 failed**.
  `tests/test_integration.py` needed no change to keep passing, because every new field was
  appended with a default — the discipline held for a sixth increment.
- **T36 found the third contract defect**: §5 v6's `a`–`t` selection letters contradicted
  §7.17's `e`/`d` keys. Resolved as **CONTRACT-v6 §5.1**.
- Orchestrator's own end-to-end checks: resistance reaches combat through the real `step`
  (dagger 1.51 vs club 3.00 against the cave snake; 3.51 vs 6.02 against the vulnerable bat);
  bare-handed deals exactly 1–2; no monster starts on a chest; the inventory costs no turn;
  and **a rejected move still returns the same state object** with every monster untouched.
- Wave 4 complete: `tests/test_integration.py` extended (104 → 137), live curses session run,
  `.plan/INTEGRATION-v6.md` written.

## Next step

**v6 is complete.** See `.plan/INTEGRATION-v6.md`.
