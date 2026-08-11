# RESEARCH — v5: stats, inventory, melee/ranged combat, NPCs, leveling, status effects

Phase 0 recon. **No code written, no workers spawned.** Every number below is either measured
on the current build (v4, 1982 tests green) or a formula that has been **simulated**, not just
written down. Four architectural forks were put to the user before drafting; their answers are
folded in and repeated in §11.

**This document has been through a second pass.** The first draft's formulas were internally
consistent and completely unplayable — a fact that only surfaced by simulating fights rather
than reading the arithmetic. §0 records what changed and why; every later section is the
corrected version.

---

## 0. What the re-check found

The first draft proposed formulas that looked reasonable and were self-consistent. Simulating
them showed seven defects, one fatal. All numbers below are from actual runs (scripts are
reproducible from the formulas as stated).

| # | Defect | Evidence | Fix |
|---|---|---|---|
| 1 | **No healing exists anywhere in the design.** Never mentioned in the draft. | Player dies after a median of **2 kills out of 12**; **0.0%** of runs clear a floor. | §7 adds HP regeneration as a first-class mechanic. |
| 2 | **Flat `block = STR // 2` swamps the damage range.** Baseline block 5 vs bites of 1–5. | **Every species did exactly 1 damage** to a baseline player — all four mechanically identical. | `block = max(0, (STR - 10) // 2)` — block is advantage *over* baseline, 0 at baseline (§1). |
| 3 | **The attacker's STR modifier double-counted on natural attacks.** A rat's 1–3 bite already encodes rat-ness; subtracting a further 3 for STR 4 is the same fact twice. | Rat/bat/snake all floored to 1 damage. | STR modifies **wielded weapons only**; natural attacks use their range directly (§3). |
| 4 | **AGI triple-dipped**, contradicting the draft's own stated principle of one identity per stat. | AGI drove speed *and* evasion *and* to-hit. | To-hit is `clamp(90 - defender.evasion, 5, 95)` — the attacker AGI term is gone (§3). |
| 5 | **The jackal was unwinnable.** | Baseline player lost **98.8%** of 1v1 jackal fights under the draft's numbers. | Retuned to 8/13/5, bite 2–4 (§5); sweep table included. Two jackals remain unwinnable **by design**. |
| 6 | **XP formula off by one against its own prose.** Doc defined `xp_for_level(n)` as "XP to reach level n+1" then tested `xp >= xp_for_level(level + 1)`. | Reaching level 2 would have cost 100 XP, not the 25 the prose claims. | Loop tests `xp_for_level(level)` (§6). |
| 7 | **The Dijkstra-map optimisation was recommended on a false premise.** | At realistic hunter counts it is **slower**: crossover is ~12–15 simultaneous hunters. Per-hunter A\* is **0.5 ms**. | Keep plain per-NPC A\* — *zero* new pathfinding code. Map documented as a measured escape hatch (§8). |

Two claims from the first draft **survived verification unchanged**: the bestiary stat blocks
do fall out of the formulas with no special cases, and permissive FOV costs ~15 ms
(measured 14.888 ms at radius 20), which is the dominant per-turn cost and is already paid
today.

---

## 1. The one rule everything else follows: derive RNG, never store it

Today `step` and `advance` are **pure with zero randomness** — the only RNG in the project is
`generator.py`'s local `random.Random(seed)`. Combat, status effects and AI all need
randomness, and that must not compromise the purity the whole test suite relies on.

**Decision: no `Random` object is ever stored on `GameState` or on an `NPC`.** Every roll
derives a **fresh** `random.Random` from an integer mix of `(master_seed, turns, actor_id,
salt)`, in the style `dungeon.seed_for` already uses:

```python
def roll_seed(master_seed: int, turns: int, actor_id: int, salt: int) -> int:
    return (master_seed * 0x9E3779B1 + turns * 0x85EBCA77
            + actor_id * 0xC2B2AE35 + salt * 0x27D4EB2F) & 0x7FFFFFFF
```

*Why:* a stored `Random` is mutable — two `GameState`s built by `replace()` from one parent
would silently share and corrupt a single generator's stream. A derived seed sidesteps it
entirely: `step`/`advance` stay pure, `GameState` stays equality-comparable, no new mutable
field exists to get wrong.

**Measured: 7.05 µs** per fresh-`Random`-plus-one-draw (100k iterations). A busy tick with a
dozen rolls is under 0.1 ms — noise against the 100 ms activity tick.

`actor_id` is stable per NPC from spawn; the player is permanently `actor_id 0`. `salt`
separates independent roll *kinds* in one tick (to-hit vs damage vs poison) so they never
share a stream.

---

## 2. Stats — three primary, four derived

**Decision: `STR`, `AGI`, `VIT`.** The user named STR and AGI; a third is needed so HP comes
from somewhere without muddying the other two. Each primary has one derived identity:
STR → damage/block, AGI → speed/evasion, VIT → HP. **`BASELINE = 10`** for all three.

| Derived | Formula | At baseline | Changed from draft? |
|---|---|---|---|
| `max_hp` | `5 + VIT * 4` | **45** | **yes** — was `VIT * 2` (25); see §7 |
| `speed` | `100 + 10 * (AGI - 10)` | 100 | no |
| `evasion` % | `clamp(5 + (AGI - 10) * 3, 0, 60)` | 5 | no |
| `block` | `max(0, (STR - 10) // 2)` | **0** | **yes** — was `STR // 2` (5) |

All integer; no floats anywhere, matching `pathfind.py`'s 10/14 model and `dungeon.seed_for`.

**Why `block` is 0 at baseline.** Flat reduction only works when damage is much larger than
the reduction. At `STR // 2` the baseline block of 5 exceeded most bites outright, so
`max(1, ...)` floored *every* attack in the game to 1 damage and all four species became
indistinguishable. Redefining block as *advantage over baseline* makes it a felt reward of
levelling (§6) instead of a constant that erases the damage system.

> **Trap for CONTRACT-v5.** `(STR - 10) // 2` floors toward −∞ in Python: at STR 5 it is −3,
> not −2. Every odd sub-baseline STR differs from truncation. Pin the intended rounding
> explicitly, or a "harmless" refactor to `int((STR-10)/2)` silently rebalances the game.

**Decision: `Player` and `NPC` share one actor core** (`str_`, `agi`, `vit`, `hp`, `max_hp`,
`status_effects`). `combat.py` and `status.py` are written against that core once; `Player`
adds inventory/xp/level, `NPC` adds species/AI-state. Neither module knows which kind it holds.

---

## 3. Combat

**User decisions: manual cursor targeting** for ranged; **minimal depth** — one melee weapon,
one ranged weapon, to-hit vs evasion, damage minus block, no criticals, no armour slots.

### To-hit

```
to_hit% = clamp(90 - defender.evasion, 5, 95)
```

Baseline vs baseline: **85%**. The draft's `+ (attacker.AGI - 10)` term is **removed** — with
it, AGI drove speed *and* evasion *and* accuracy, making it strictly the best stat and
contradicting the one-identity-per-stat principle §2 states. AGI still double-dips (speed and
evasion), which is conventional and acceptable.

### Damage

```
weapon_damage  = max(1, roll(w.min, w.max) + (attacker.STR - 10)//2 - defender.block)   # wielded
natural_damage = max(1, roll(a.min, a.max)                          - defender.block)   # bites, claws
```

The STR modifier applies to **wielded weapons only**. A species' bite range already encodes how
strong it is; adding its STR modifier on top counts the same fact twice and was what drove
every animal's damage to the `max(1, ...)` floor. Ranged weapons also take no STR (a bow's
power is the bow's) — the one deliberate melee/ranged asymmetry retained.

### Melee — bump-to-attack

`try_move` already distinguishes "blocked by wall" from "blocked by closed door"
(bump-to-open). It grows a third case — "blocked by a hostile NPC" — resolved as an attack.
No new command, no new binding: walking into an occupied cell *is* the attack, the ADOM/NetHack
convention CONTRACT-v4 already cited precedent from.

### Ranged — a sub-mode following the `awaiting_walk` precedent

`awaiting_walk` (CONTRACT-v4 §7.4) already proves the pattern: a key opens a zero-turn sub-mode
that the next keystroke resolves or cancels. Ranged targeting reuses it rather than inventing a
second mechanism.

**Improvement over the draft: valid targets come from `state.visible`, which is already
computed every turn.** The draft called line-of-sight afresh for target legality. Reading
`visible` instead costs **nothing**, and — more importantly — makes "what I can shoot" exactly
"what I can see on screen" by construction. A separate LOS call could legally disagree with the
rendered frame (§8 measures that disagreement at 0.28% of pairs), producing the worst possible
bug class: a monster you can see but cannot shoot, with no visible reason.

So targets are `visible ∩ npcs`, filtered by weapon range, sorted by distance then coordinate
for a total order. `f` (**verified unbound**, along with `F`, `t`, `a`, `i`, `g`) enters the
mode; with no valid target it reports `NO_TARGET` and costs no turn, like every other
"nothing to do here" case. Movement keys cycle the cursor, `f` confirms and fires as one turn,
any other key cancels with no turn — exactly a mistyped `w`-prefix today. Exact cycling keys
are a CONTRACT-v5 detail; the *shape* is proven twice already.

### Death

HP ≤ 0 from any source is death, handled identically regardless of cause. An NPC is removed
from `GameState.npcs` and its `xp_value` credited; the player reaching 0 ends the run —
`running` clears, `outcome` is set, printed after the terminal is restored, exactly parallel to
the existing ascend-from-level-1 ending.

---

## 4. Inventory — static, two slots

**User framing: "static for now"**, read narrowly. No pickup, no drop, no ground items, no
inventory UI. Two equipment slots filled once at `new_game` from constants:

```python
@dataclass(frozen=True)
class Weapon:
    name: str
    kind: WeaponKind      # MELEE | RANGED
    damage_min: int
    damage_max: int
    range: int = 1        # Chebyshev; MELEE is always 1
```

An `items.py` leaf defines a dagger (2–5) and a shortbow (1–4, range 6). Ammo is infinite per
the user's requirement — there is no ammo count anywhere to go out of sync.

---

## 5. NPCs

**User decision: full simulation** on the current level; NPCs on levels the player has left are
frozen. **Decision:** `LevelState` gains `npcs: tuple[NPC, ...]` alongside `explored` and
`open_doors`, for exactly the reason those two exist — runtime state generation cannot
re-derive, which must survive a stairs round trip.

### AI — two states, one perception rule

`ai_state: WANDERING | HUNTING` plus `memory: int` (ticks since last seeing the player).

- **WANDERING:** coin-flip between staying put and stepping to a random passable orthogonal
  neighbour. Each action, checks LOS to the player within `PERCEPTION_RADIUS = 10`; on success
  becomes HUNTING.
- **HUNTING:** re-plans to the player's current position every action via `find_path` over real
  terrain (an NPC is not fogged — it lives here). Adjacent, it attacks instead of moving. No LOS
  this action increments `memory`; past `FORGET_TICKS = 5` it reverts to WANDERING.

Both reuse existing infrastructure wholesale — `find_path`, `is_planning_passable` (so an NPC
bumps a door open exactly like the player). **No new pathfinding code**, only new call sites.

### The tuned bestiary

The draft's jackal lost the baseline player **98.8%** of 1v1 fights. Sweep (600 fights each,
baseline player 10/10/10 with a dagger, at the corrected `max_hp = 5 + VIT*4` for both sides):

| STR/AGI/VIT | bite | HP | spd | ev | player win |
|---|---|---|---|---|---|
| 8/16/6 (draft) | 2–5 | 29 | 160 | 23 | **25.2%** |
| 8/14/6 | 2–5 | 29 | 140 | 17 | 54.8% |
| **8/13/5 (chosen)** | **2–4** | **25** | **130** | **14** | **97.0%** |
| 8/12/5 | 2–4 | 25 | 120 | 11 | 99.2% |

| Species | STR/AGI/VIT | HP | Speed | Evasion | Block | Attack | XP | 1v1 win | Note |
|---|---|---|---|---|---|---|---|---|---|
| Rat | 4/14/3 | 17 | 140 | 17 | 0 | 1–3 | 5 | 100% | nuisance |
| Jackal | 8/13/5 | 25 | 130 | 14 | 0 | 2–4 | 10 | 97.0% | the real threat |
| Giant bat | 3/18/2 | 13 | 180 | 29 | 0 | 1–2 | 8 | 100% | fast, fragile |
| Cave snake | 6/8/5 | 25 | 80 | 0 | 0 | 2–4 | 12 | 100% | slow; 30% `POISONED` |

*(All values at `max_hp = 5 + VIT*4`; every one still falls straight out of §2's formulas with
no special cases — re-verified after both the retune and the HP change.)*

**Pack lethality is the real difficulty knob, and it is severe.** A baseline player beats one
jackal 97% of the time and **two jackals 0.0%** of the time (600 runs; not one win). Raising HP
made packs *more* lethal, not less — the NPCs gained the same multiplier, so each takes longer
to kill while the others keep biting. This is the correct roguelike shape — being surrounded is
death — but it makes spawn *placement* load-bearing: a rule that can cluster two jackals near
the up-staircase kills level-1 characters through no fault of their own. CONTRACT-v5 must state
a minimum spawn separation and a no-spawn radius around `player_start`. **A solo encounter and
a pair of the same animal are not the same difficulty; they are winnable and unwinnable.**

### Spawning

Placed once at level generation from that level's existing seeded `Random` — the direct analogue
of "static for now". **Six per level** (§7 shows why not twelve). Depth-scaled tables are
explicitly deferred, not stubbed.

---

## 6. Levelling

```
xp_to_next(L) = 25 * L * L        # XP to go from level L to L+1
while xp >= xp_to_next(level):
    xp -= xp_to_next(level)
    level += 1
    grow()
```

The draft's loop tested `xp_for_level(level + 1)` against a definition that said `n` was the
*current* level — reaching level 2 would have cost 100 XP where the prose promised 25. Fixed
above; note the loop now also **subtracts** the spent XP, so a single large kill crossing two
thresholds behaves correctly.

Growth is deterministic, no allocation UI (the user asked for formulas, not a choice screen):
`VIT += 1` every level, `STR += 1` on odd levels, `AGI += 1` on even. Derived stats are
recomputed through the **same** `derive_stats` as spawning, so there is no second HP formula to
drift.

**Current HP grows by exactly the max-HP delta, not a full heal** — a full heal would make
levelling a free heal-on-demand for anyone willing to grind. This preserves proportional
health across a level-up.

| From → to | XP | rats (5 xp) |
|---|---|---|
| 1 → 2 | 25 | 5 |
| 2 → 3 | 100 | 20 |
| 3 → 4 | 225 | 45 |
| 4 → 5 | 400 | 80 |

> **Known gap, stated honestly.** With a static bestiary, levelling **saturates**: by character
> level 5 the player wins 100% against all four species. Levelling feels good for two or three
> levels and then stops meaning anything. The fix is depth-scaled spawn tables, which the user
> scoped out of v5 — recording it as a real limitation of this increment rather than pretending
> the curve is balanced.

---

## 7. HP regeneration — the fatal omission

**The first draft had no healing of any kind.** Nothing in it restored HP except a level-up's
max-HP delta. Simulating a full dungeon floor (12 monsters, ~180 turns of walking as measured
by RESEARCH-v4's auto-explore runs, 600 runs per cell):

```
player dies after a median of 2 kills out of 12
0.0% of runs clear the floor
median character level reached: 1  (so levelling is dead content — you die before level 2)
```

Adding regeneration alone does **not** rescue it — at 12 monsters per level, every regen rate
from 1 HP/30 turns to 1 HP/10 turns still yields **0.0%** floor clears. The ratio of
damage-taken-per-fight (~7 HP) to max HP (25) was simply wrong. The parameter sweep:

**Floor-clear rate, `max_hp = 5 + VIT*2` (25 baseline):**

| monsters/level | no regen | 1/20t | 1/10t | 1/5t | 1/3t |
|---|---|---|---|---|---|
| 4 | 9.2% | 26.2% | 48.2% | 77.8% | 87.3% |
| 6 | 0.2% | 2.7% | 9.7% | 35.5% | 70.2% |
| 8 | 0.0% | 0.0% | 0.5% | 5.3% | 35.7% |
| 12 | 0.0% | 0.0% | 0.0% | 0.0% | 1.8% |

**Floor-clear rate, `max_hp = 5 + VIT*4` (45 baseline):**

| monsters/level | no regen | 1/20t | 1/10t | 1/5t | 1/3t |
|---|---|---|---|---|---|
| 4 | 74.3% | 86.2% | 93.3% | 98.5% | 99.2% |
| **6** | 22.3% | 41.3% | **61.5%** | 86.3% | 97.8% |
| 8 | 1.8% | 4.3% | 16.3% | 49.7% | 84.2% |
| 12 | 0.0% | 0.0% | 0.0% | 2.2% | 25.2% |

**Recommendation: `max_hp = 5 + VIT * 4`, six monsters per level, regenerate 1 HP every 10
turns** → **61.5%** of floors cleared by a player who fights everything. The true rate is
higher, since a real player can take the stairs instead of fighting — which is exactly the
tension a roguelike wants.

Regeneration is one integer counter on the actor core, ticked in the same place status effects
are (§9), and it is the mechanism that makes the ~180 turns of auto-explore between fights
mean something.

---

## 8. Line of sight, and why the clever optimisation was wrong

### `fov.py` needs one additive function

`fov.py` is frozen for v4 only; that freeze does not bind v5. `compute_visible` is untouched.
Add:

```python
def has_line_of_sight(level, open_doors, a: Coord, b: Coord) -> bool
```

using the same doubled-integer exact-segment machinery, but computing opacity only over the two
points' bounding box. **Measured: 0.167 ms per check (5.0 ms for 30) versus 218.8 ms for 30
naive `compute_visible` calls — 44× cheaper.** Reusing `compute_visible` for NPC awareness would
blow the whole turn budget at ~14 simultaneous checks. This is the single strongest
performance finding in the document.

Only **NPC → player awareness** needs it. Ranged targeting reads `state.visible` instead (§3).

> **Permissive LOS is not symmetric.** Measured: **2 of 720 cell pairs (0.28%) disagree** on who
> can see whom — a consequence of the eye being at the origin's *centre* while the target is
> tested at eight *boundary* samples. CONTRACT-v5 must fix the argument order convention
> (recommended: `has_line_of_sight(observer, target)`, and NPC awareness always asks
> `(npc, player)`) so the asymmetry is a defined behaviour rather than a coin flip.

### The pathfinding optimisation that isn't

The first draft recommended replacing per-NPC A\* with a single Dijkstra map from the player.
Measured (median of 9 runs, goal = player, 80×22 level, 593 passable cells):

| hunters | N × A\* | Dijkstra map | cheaper |
|---|---|---|---|
| 1 | 0.49 ms | 5.90 ms | A\* |
| 3 | 1.53 ms | 6.07 ms | A\* |
| 5 | 2.31 ms | 6.14 ms | A\* |
| 10 | 4.74 ms | 6.15 ms | A\* |
| 15 | 8.26 ms | 6.03 ms | map |
| 30 | 14.94 ms | 6.19 ms | map |

**Per-hunter A\* costs ~0.5 ms.** With six monsters per level (§7) and only *hunting* ones
pathfinding, the realistic per-turn cost is **under 3 ms** — and A\* is *already written and
tested*. The crossover is ~12–15 simultaneous hunters, which this design never reaches.

**Recommendation: keep per-NPC `find_path`. Write no new pathfinding code.** The Dijkstra map
is recorded here as a measured escape hatch with a known trigger point, should NPC density ever
rise.

> **Trap, if that hatch is ever taken.** The obvious downhill rule — step to the neighbour with
> the smallest map value — is **wrong** on a 10/14 cost model: it ignores the cost of the step
> itself and picked a suboptimal move in **60 of 300** positions (20%). The correct rule
> minimises `step_cost + map[neighbour]`, which was verified **optimal in 300/300** positions.
> (It disagrees with A\*'s specific choice in 107 of them, but every one of those is an
> equal-cost tie broken differently — both are shortest paths.) This bug is easy to write, looks
> right, and produces NPCs that merely seem a little dim.

---

## 9. Status effects

A `status.py` leaf:

```python
class StatusKind(Enum):
    POISONED = auto()

@dataclass(frozen=True)
class StatusEffect:
    kind: StatusKind
    remaining_turns: int
    magnitude: int
```

Held in `status_effects: tuple[StatusEffect, ...]` on the shared actor core (§2), so ticking is
written once for the player and every NPC — exactly what the user asked for.

**Cadence: once per world-tick, unconditionally**, decoupled from the energy scheduler (§10).
Poison must not dodge a tick by being slow. HP regeneration (§7) ticks in the same place.

**Reapplication refreshes, never stacks:** a fresh application of a kind already present
replaces the entry only if it would last longer. Magnitude never stacks; there is never more
than one entry of a kind on an actor.

Cave snake: 30% chance on a connecting bite to apply `POISONED(remaining_turns=5, magnitude=2)`.
Each tick: `hp -= magnitude`, `remaining_turns -= 1`. Poison can kill, through the same death
path as combat damage — no separate code path, only a different `Event`.

> **Honest scope note.** Nothing in the v5 bestiary poisons an *NPC* — only the snake applies
> poison, and it bites only the player. So the NPC half of the status system ships
> called-and-tested but with no live content, the same honest position CONTRACT-v4 took with
> `interruption()`. Either accept it as a seam, or give one starting weapon a poison rider;
> CONTRACT-v5 should say which, rather than leave it ambiguous.

---

## 10. Turn scheduling — energy, player exempt

**User decision: energy-based scheduler**, so `speed` is mechanically real.

**Decision: the player is not in the accumulator.** Every accepted keypress consumes exactly one
turn and executes immediately, unchanged from v1–v4. That turn *is* one world-tick, and every
NPC on the level gains `speed` energy against `ENERGY_THRESHOLD = 100`:

```python
npc.energy += npc.speed
while npc.energy >= ENERGY_THRESHOLD:
    npc = act(npc, state)
    npc.energy -= ENERGY_THRESHOLD
```

At `speed = 100` that is one action per tick — identical to today's implicit model, so baseline
creatures are unaffected by the new machinery. A bat at 180 acts 1.8×/tick; a snake at 80 acts
0.8×. The loop is bounded by construction (finite `speed` in, fixed threshold out), so no
iteration cap is needed.

*Why exempt the player:* a fully unified model means a slow player's keypress is
accepted-but-deferred, which either eats keystrokes or needs buffering — a real input-model
change the "minimal scope" decision argues against. The player's `speed` is still computed and
displayed; it simply does not gate their own turn. A future haste/slow effect could change that
without touching this module's shape.

**Order is a fixed tuple, never a set.** `GameState.npcs` is ordered by `actor_id` and iterated
in that order, the same discipline `pathfind.DIRECTIONS` already enforces.

> **Spawn energy should be staggered.** If every NPC spawns at `energy = 0` they act in
> lockstep, and a pack moves as one organism. Seeding initial energy from the level's `Random`
> costs one line and desynchronises them.

---

## 11. What this touches

### New modules

| Module | Owns |
|---|---|
| `stats.py` | `STR`/`AGI`/`VIT`, `derive_stats()` (§2) |
| `items.py` | `Weapon`, `WeaponKind`, the two starting weapons |
| `status.py` | `StatusKind`, `StatusEffect`, ticking, regeneration |
| `npc.py` | `NPC`, `Species` table, the two AI planners |
| `combat.py` | to-hit, damage, resolving an attack — written once against the shared core |

### Amended modules

- **`fov.py`** — `+ has_line_of_sight` (§8), additive; no existing test touched.
- **`game.py`** — `GameState` gains `npcs` and `targeting`; `LevelState` gains `npcs`; a pure
  `advance_npcs(state)` runs after every turn-consuming action. `format_stats` — currently
  literally `""`, its docstring already saying *"reserved for player stats… none of them exist
  yet"* — is finally filled in.
- **`keys.py`** — one new `CommandKind`; `f` verified unbound.
- **`events.py`** — ~10 new members (hit, miss, kill, death, level-up, poison, no-target). One
  enum member, one table entry, one emission site each, exactly as CONTRACT-v3 §16 promises.
- **`render.py`/`style.py`** — NPC glyphs, a colour role per species, the stats row.

> **The message line needs a cap.** NPC events append to the turn's `events`, and
> `message_for` joins with spaces — it has never been called with more than one event. Six
> NPCs acting in one tick can produce a line far wider than 80 columns, which the renderer will
> silently clip. CONTRACT-v5 should cap the count or prioritise player-affecting events.

### Off-screen visibility rule

An NPC's action outside `visible` produces no message unless it affects the player directly.
No ambient "you hear scurrying" — the project has no "you bump into a wall" message either, for
the same reason. An attack that connects is always reported, consistent with
`compute_visible`'s "origin is always visible" rule.

### User decisions folded in

1. Energy-based scheduler (§10). 2. Full off-FOV simulation on the current level (§5).
3. Manual cursor targeting (§3). 4. Minimal combat depth (§3, §4).

### Left open for CONTRACT-v5

- Exact keys for cycling/confirming ranged targets.
- The `has_line_of_sight` argument-order convention (§8) — must be stated, not assumed.
- Minimum spawn separation and no-spawn radius around `player_start` (§5).
- Whether the NPC half of the status system ships as a seam or gains live content (§9).
- Message-line cap policy (§11).
- Every tuning number here (`PERCEPTION_RADIUS`, `FORGET_TICKS`, regen rate, monsters/level,
  the bestiary) is a **simulated** first pass, not a playtested one. They are defensible
  starting points, not balanced ones.
