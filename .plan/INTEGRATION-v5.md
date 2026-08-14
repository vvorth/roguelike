# INTEGRATION — v5: stats, combat, monsters, levelling, status effects

**Complete.** Full suite **2615 passed, 0 failed** at the close of the increment (v4 ended at
1982). Live curses sessions were run throughout.

> **Written after the fact.** This record was reconstructed from `.plan/STATE-v5.md`, the seven
> task reports and the commit history, some time after v5 shipped — it was the one increment
> whose integration note was never written at the time. Every figure below is taken from those
> sources or re-measured against the shipped code; nothing is recalled from memory. It is filed
> here because the gap was real and the alternative was leaving v5 the only undocumented
> increment.

## The seven tasks

| Task | Owner | Suite | Verified |
|---|---|---|---|
| T22 stats, items, status | sonnet | 57 | yes |
| T23 point-to-point line of sight | opus | 158 | yes |
| T24 input and event vocabulary | sonnet | 290 | yes |
| T25 combat resolution | sonnet | 43 | yes |
| T26 NPCs, AI and spawning | opus | 113 | yes |
| T27 rendering NPCs and the stats row | sonnet | 213 | yes |
| T28 bump-to-attack, NPC turns, targeting, levelling | opus | 603 | yes |

## The increment's defining event: the research was wrong

RESEARCH-v5's first draft was internally consistent and **completely unplayable**. Simulating it
rather than reading it found seven defects, one fatal:

- **No healing existed anywhere.** The player died after a median of **2 kills out of 12** and
  **0.0%** of runs cleared a floor — levelling was dead content, since you die before level 2.
- **Flat `block = STR // 2` floored every attack in the game to 1 damage**, making all four
  species mechanically identical.
- The attacker's STR modifier was **double-counted on natural attacks**.
- **AGI triple-dipped** — speed, evasion *and* accuracy.
- The jackal lost the player **98.8%** of 1v1 fights.
- The XP formula was **off by one against its own prose**.
- A proposed Dijkstra-map optimisation was **slower** than per-NPC A\* at realistic counts.

All were corrected before implementation, and RESEARCH-v5 §0 records each with its evidence.

## And the correction had a bug of its own

**This is the most important thing in this document.** The corrected sweep passed the HP
multiplier for the player but let monsters keep the function's default, modelling them at
`5 + VIT*2` while `stats.derive` gives everything `5 + VIT*4`. The published "61.5% of floors
cleared" was really **2.2%** — and that shipped.

It was caught later, while checking whether monster fleeing would disturb the balance.
`REGEN_TURNS` went 10 → 3, which measures **61.9%**: what the research had meant all along.

The lesson is written into `CLAUDE.md` and was applied throughout v6: **measure by importing the
shipped modules, never by restating a formula.**

## What was verified end to end

- **Permissive line of sight is 44–70× cheaper** as a dedicated point-to-point check than reusing
  `compute_visible` (0.13 ms against 262 ms for 30 checks). Reusing the whole-disc function would
  have blown the turn budget at about fourteen simultaneous monsters.
- **Line of sight is not symmetric** — measured at 2 of 720 cell pairs. The `(observer, target)`
  argument order is binding as a result.
- **`compute_visible` was not perturbed**: `tests/test_activity.py`, frozen and driving it end to
  end, stayed green, and the only deleted line in `fov.py` was its `__all__`.
- **Spawn rules hold**: 720 monsters across 120 runs with zero violations of the safe radius or
  the minimum separation, and 95 distinct starting energies so packs do not move in lockstep.
- **Two jackals beat a baseline player 100% of the time** (600 runs, zero wins) against 97% for
  one. Being surrounded is death — which is why the spawn separation rule is not a nicety.
- **NPC planning costs a median 5.08 ms per tick** for six simultaneous hunters, 9.8× inside
  budget. No optimisation was warranted, and the Dijkstra escape hatch stays unused.

## Two contract defects, both the orchestrator's

- **§11.1 — the empty-level defect.** §11 v5 said `advance_npcs` returns unchanged with no NPCs,
  contradicting §7.8's "status and regeneration first". Measured on the pre-fix build: 40 ticks
  at 10 hp on a cleared floor left the player at 10 hp, and a 5-turn poison still read
  `remaining_turns=5` after 10 ticks. Since most of a level's turns are walked *after* its
  monsters are dead, this silently restored the unplayable balance. T28 implemented the literal
  rule, as it was required to, and escalated rather than improvising.
- **§7.14 — `interruption` had to be wired up, not deferred again.** It was recorded as a direct
  user requirement in RESEARCH-v4 and deferred only because monsters did not exist. A first draft
  of PLAN-v5 wrongly listed it as out of scope; leaving it returning `None` would have shipped an
  auto-explore that walks into a jackal pack and keeps walking.

## Known limitations recorded at the time

- **Levelling saturates** by character level 5 against a static bestiary.
- **The NPC half of the status system has no live content** — nothing in v5 poisons a monster.
- Every tuning number is simulated, not playtested.

## Postscript

Several v5 decisions were revised in later work, at the user's direction, and are noted here so
this record is not read as current:

- monster fleeing was added, then cut from ~75% of fights to ~17% for the jackal;
- `interruption` was widened from "a hostile newly appears" to "any hostile is visible", and
  automatic movement is now refused outright while one is in view;
- `SPOTTED_HOSTILE`'s wording changed accordingly (CONTRACT-v5 §16.2);
- `REGEN_TURNS` is 3, not the 10 this increment shipped.
