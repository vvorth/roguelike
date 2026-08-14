# Working on this project

A terminal roguelike engine, Python 3.10+, **standard library only**. Six increments shipped;
**3029 tests, green**. Read this before changing anything — it is the practice, not just the
artifacts.

## Keeping this file current — a standing requirement

**When something worth knowing turns up, add it here.** This file is the only thing a fresh
session reads before it starts making decisions, and everything below was learned the expensive
way. Add an entry when you find:

- a rule that would not be obvious from the code, and that a future session could break silently;
- a trap you fell into, or nearly did — especially a test that *passed* while the code was wrong,
  or one that *failed* while the code was right;
- a number whose value is load-bearing and non-obvious;
- a change to the environment, the process, or the standing rules.

Do not add: anything the code, the contracts or `git log` already say plainly; anything that only
mattered to one conversation. Keep it short — a fact and why it matters. If an entry stops being
true, delete it. A stale instruction is worse than a missing one.

## The environment

**Use `.venv/bin/python`, never bare `python3`.** System python has no pytest, and the container
has changed under this project mid-increment before.

```
.venv/bin/python -m pytest -o addopts="" -q < /dev/null
```

`-o addopts=""` is needed because `pytest.ini` already sets `-q`, and doubling it suppresses the
summary line. The full suite takes about 2.5 minutes.

## How this project is built

Every increment follows the same phases, and the artifacts live in `.plan/`:

1. **RESEARCH** — measure on the *shipped modules*, decide nothing by taste. Record what was
   measured and what is only proposed.
2. **CONTRACT** — freeze every decision, with the measurement that justifies it. Workers may not
   edit it.
3. **PLAN** — cut tasks along contract seams so each owns a disjoint file set, and group them
   into waves by dependency.
4. **Task briefs** in `.plan/tasks/`, one per task, self-contained: a worker never talks to
   another worker.
5. **Execute in waves**, verifying each task before the next wave.
6. **INTEGRATION** — extend `tests/test_integration.py`, run a live curses session, write
   `.plan/INTEGRATION-vN.md`.

`.plan/STATE-vN.md` is the resume point if an increment is interrupted, and it carries the log of
what went wrong.

## Standing rules

**A task is done only when its report exists *and* you have personally run its verification
command and seen it exit 0.** Never mark a task done on a worker's say-so. Re-check the claims
that matter — this has caught real defects every increment.

**Measure before freezing a number.** Three near-fatal balance bugs were caught this way, and one
was caught only *after* shipping because the simulation had a bug in it.

**When a worker reports a contract defect, amend the contract — do not let them patch around it.**
Four amendments came from workers stopping and reporting. Each would have been a silent
inconsistency if they had "helpfully" fixed one side.

**Append new dataclass fields with defaults.** This is why `tests/test_integration.py` has stayed
green across six increments without being rewritten.

**No `random.Random` is ever stored on a state.** Every roll derives a fresh generator from
`(master_seed, turns, actor_id, salt)`. A stored generator is mutable and two states built by
`replace()` from one parent would corrupt each other's stream.

**Timing lives in `run` and nowhere else.** No `sleep`, no clock read, no busy-wait anywhere in
the codebase. `stdscr.timeout()` is the only pacing mechanism — it delivers both the tick rate and
instant cancellation.

**`step` and `advance` are pure.** Same state in, same state out. The whole engine is testable
headless because of this; do not weaken it.

## Traps that have already bitten

These are recorded because each one *looked right*:

- **Flat damage reduction saturates.** At +1 flat block, 58% of all monster damage rolls floor to
  1. No item may grant flat reduction; shields are a *chance to negate*. An early `block = STR//2`
  floored every attack in the game to 1 damage and made all four species identical.
- **Percentage resistances are unusable at these numbers.** On a 2–5 roll, 25% and 33% give the
  same outcomes; 66% and 75% are both flat 1. Hence coarse tiers.
- **Where resistance applies is a 2× lever.** The same 50% resistance yields average damage from
  1.25 to 2.50 depending only on its position in the pipeline. It is pinned to the raw roll.
- **One point of weapon damage is worth 20–40 points of survival.** 1–4 clears 2.2% of floors,
  2–5 clears 45.6%, 4–8 clears 98.3%. The dagger's 2–5 is the reference the whole balance rests
  on. **Do not retune it.**
- **A test comparing two rng-consuming calls per seed is invalid** if one path draws more than the
  other. The shield roll consumes a draw, so `shield_block=0` versus `=25` on the same seed
  legitimately produces different damage. Compare distributions instead. This test *failed against
  correct code*.
- **Permissive line of sight is not symmetric** — about 0.3% of cell pairs disagree. The argument
  order `(observer, target)` is binding.
- **`compute_visible` must not change behaviour.** `tests/test_activity.py` drives it end to end
  and is frozen in most increments.
- **A rejected move consumes no turn, and therefore the world must not tick.** v1's headline rule,
  alive through six increments. Check it after any change to the turn loop.

## Honest seams — tested, with no live caller

Do not "fix" these by inventing content for them; they are deliberate:

- `interruption`'s conditions existed before monsters did.
- `Resistance.IMMUNE` — no shipped species is immune to anything.
- `ranged_block_chance` and `NPC_SHIELD_BLOCKED` — nothing shoots at the player, no monster
  carries a shield.
- `opens_doors=True` — every species is an animal; no humanoid exists yet.
- The NPC half of the status system — nothing poisons a monster.

## Known limitations, accepted

- **Levelling saturates** by character level 5 against a static bestiary. The fix is depth-scaled
  spawn tables, deliberately out of scope.
- **A fine shield makes the current bestiary easy** (85.8% floor clears against 45.6% bare). The
  user accepted this in exchange for a mechanic that keeps scaling.
- **A chest can be placed on a down-staircase.** Harmless — both report themselves.
- Every tuning number is simulated, not playtested.

## Module map

```
tiles events keys pathfind items status   ← leaves, import nothing from the project
stats     ← status
level     ← tiles
world     ← tiles level
style     ← tiles
generator ← tiles level
fov       ← level world
movement  ← level world
render    ← tiles level style
dungeon   ← generator level
activity  ← level world pathfind
combat    ← stats items status
loot      ← items level
npc       ← stats status items level world pathfind fov
game      ← everything
main      ← game
```

The graph is acyclic and each module's docstring states what it may import. `game.py` is the only
module allowed to import widely, and the only one that holds game rules.
