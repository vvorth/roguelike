# CONTRACT v5 — stats, inventory, combat, NPCs, levelling, status effects

**Frozen once written. Workers may not edit it.** A worker that believes it is wrong reports
that and stops; the orchestrator decides.

Amends `CONTRACT.md` (v1), `CONTRACT-v2.md`, `CONTRACT-v3.md` and `CONTRACT-v4.md`. Everything
in those stays binding unless amended here.

Decisions from `.plan/RESEARCH-v5.md`, confirmed by the user: **energy-based scheduler** ·
**full off-FOV simulation on the current level** · **manual cursor targeting for ranged** ·
**minimal combat depth**.

**Every number in this contract was simulated, not estimated.** RESEARCH-v5 §0 records seven
defects found by simulating the first draft, one of which (no healing) made the game
unplayable — 0.0% of floors cleared. Numbers here are the corrected ones. A worker that finds
a number wrong reports it; it does not silently retune.

---

## §0 amendments

### §0.12 Randomness is derived, never stored

**No `random.Random` instance may be stored on `GameState`, `Player`, `NPC`, or any other
value type.** A stored generator is mutable: two states built by `replace()` from one parent
would share and corrupt a single stream, which is exactly what the frozen-dataclass discipline
exists to prevent.

Every roll derives a **fresh** generator:

```python
def roll_seed(master_seed: int, turns: int, actor_id: int, salt: int) -> int:
    return (master_seed * 0x9E3779B1 + turns * 0x85EBCA77
            + actor_id * 0xC2B2AE35 + salt * 0x27D4EB2F) & 0x7FFFFFFF
```

- `actor_id` is stable from spawn. **The player is permanently `actor_id == 0`**; NPCs are
  numbered from 1 in spawn order.
- `salt` separates independent roll *kinds* within one tick. The salts are fixed:
  `1` to-hit, `2` damage, `3` status application, `4` AI wander.
- `step` and `advance` remain **pure**. Same state in, same state out, forever.

*Measured: 7.05 µs per fresh-`Random`-plus-draw. A busy tick is under 0.1 ms.*

### §0.13 Integer stats, and the rounding trap

All stats, HP, damage, XP and energy are **`int`**. No floats anywhere, matching §0.11.

> **Binding rounding rule.** The STR damage modifier is `(STR - 10) // 2`, which floors toward
> −∞: at `STR 5` it is `-3`, **not** `-2`. Every odd sub-baseline STR differs from truncation.
> `int((STR - 10) / 2)` is **forbidden** — it silently rebalances the game.

---

## §20 (new) — stats: `roguelike/stats.py`

A leaf. Imports only `roguelike.status`.

```python
BASELINE: int = 10

@dataclass(frozen=True)
class Stats:
    str_: int
    agi: int
    vit: int

@dataclass(frozen=True)
class Derived:
    max_hp: int
    speed: int
    evasion: int
    block: int

@dataclass(frozen=True)
class Actor:
    """The shared core of the player and every NPC."""
    stats: Stats
    hp: int
    status_effects: tuple[StatusEffect, ...] = ()

def derive(stats: Stats) -> Derived: ...
```

### §20.1 The four derived formulas — binding

| Derived | Formula | At baseline 10/10/10 |
|---|---|---|
| `max_hp` | `5 + vit * 4` | **45** |
| `speed` | `100 + 10 * (agi - 10)` | 100 |
| `evasion` | `clamp(5 + (agi - 10) * 3, 0, 60)` | 5 |
| `block` | `max(0, (str_ - 10) // 2)` | **0** |

**`block` is zero at baseline, and that is the point.** Flat reduction only works when damage
is much larger than the reduction. An earlier `str_ // 2` gave baseline block 5 against bites
of 1–5, which floored **every attack in the game to 1 damage** and made all four species
mechanically identical. Block is advantage *over* baseline — a felt reward of levelling, not a
constant that erases the damage system. **A worker must not "fix" block back to a positive
baseline value.**

`derive` is pure, total, and never raises — negative or zero stats produce correspondingly
small derived values. `max_hp` may be ≤ 0 only for absurd inputs no caller produces; nothing
guards against it.

**Player and NPC compose an `Actor`; they do not inherit from one.** Frozen dataclass
inheritance with defaults is a known trap, and composition keeps `combat.py` written once
against one type.

---

## §21 (new) — items: `roguelike/items.py`

A leaf. Imports nothing from the project.

```python
class WeaponKind(Enum):
    MELEE  = auto()
    RANGED = auto()

@dataclass(frozen=True)
class Weapon:
    name: str
    kind: WeaponKind
    damage_min: int
    damage_max: int
    range: int = 1          # Chebyshev distance; MELEE is always 1

DAGGER:   Weapon = Weapon("dagger",   WeaponKind.MELEE,  2, 5, range=1)
SHORTBOW: Weapon = Weapon("shortbow", WeaponKind.RANGED, 1, 4, range=6)
```

**Inventory is static (user requirement).** There is no pickup, no drop, no ground item, no
inventory screen, and **no ammunition count anywhere** — ammo is infinite, so there is no
counter to go out of sync. The player is constructed at `new_game` holding exactly `DAGGER` and
`SHORTBOW` and this never changes. **Do not add a generator, a loot table, or an `Item` base
class.**

---

## §22 (new) — status effects: `roguelike/status.py`

A leaf. Imports nothing from the project.

```python
class StatusKind(Enum):
    POISONED = auto()

@dataclass(frozen=True)
class StatusEffect:
    kind: StatusKind
    remaining_turns: int
    magnitude: int

REGEN_TURNS: int = 10        # the player regains 1 HP every 10 world-ticks

def apply_effect(effects: tuple[StatusEffect, ...],
                 new: StatusEffect) -> tuple[StatusEffect, ...]: ...
def tick_effects(effects: tuple[StatusEffect, ...]) -> tuple[tuple[StatusEffect, ...], int]: ...
```

### §22.1 Application refreshes, never stacks

`apply_effect` returns the effects with `new` folded in:

- If no effect of that `kind` is present, append it.
- If one is present, **replace it only if `new.remaining_turns` is greater** than the existing
  `remaining_turns`. Otherwise return the input unchanged.
- **Magnitude never stacks.** There is never more than one entry of a given kind on an actor.

### §22.2 Ticking

`tick_effects` returns `(surviving_effects, total_damage)`:

- Each effect contributes `magnitude` damage and has `remaining_turns` reduced by 1.
- An effect reaching `remaining_turns == 0` is dropped.
- Order within the tuple is preserved. Pure; the input is never mutated.

### §22.3 Cadence — decoupled from energy

Status effects and regeneration tick **once per world-tick, unconditionally**, for every actor,
regardless of whether that actor's energy crossed the action threshold (§24.3). **Poison must
not dodge a tick by being slow.**

### §22.4 Regeneration applies to the player only

The player regains **1 HP every `REGEN_TURNS` world-ticks**, capped at `max_hp`. **NPCs do not
regenerate.** This is a deliberate asymmetry: NPC regeneration would make disengaging
pointless and adds no gameplay at this scope.

*Load-bearing, and measured. With no healing at all the player dies after a median of **2 kills
out of 12** and **0.0%** of runs clear a floor. With `REGEN_TURNS = 10` and six monsters per
level, **61.5%** of floors are cleared by a player who fights everything.*

---

## §23 (new) — combat: `roguelike/combat.py`

Pure. Imports `roguelike.stats`, `roguelike.items`, `roguelike.status`. **Does not import
`events`** — it returns a structured result and `game.py` turns it into events, exactly as
`movement.try_move` returns a `MoveResult` (§6).

```python
@dataclass(frozen=True)
class AttackResult:
    hit: bool
    damage: int                 # 0 when hit is False
    defender_hp: int            # after damage
    killed: bool
    poisoned: bool = False

def to_hit_chance(defender_evasion: int) -> int: ...

def resolve_attack(rng, attacker: Actor, defender: Actor,
                   damage_min: int, damage_max: int,
                   strength_applies: bool,
                   poison_chance: int = 0) -> AttackResult: ...
```

### §23.1 To-hit

```
to_hit% = clamp(90 - defender.evasion, 5, 95)
```

Baseline vs baseline: **85%**. There is **no attacker term**. An earlier draft added
`+ (attacker.agi - 10)`, which made AGI drive speed *and* evasion *and* accuracy — strictly the
best stat, and a contradiction of the one-identity-per-stat rule §20 states. **A worker must not
reintroduce an attacker accuracy term.**

The roll is `rng.randint(1, 100) <= to_hit%`.

### §23.2 Damage

```
strength_applies is True   (wielded weapon):
    damage = max(1, roll(min, max) + (attacker.str_ - 10)//2 - defender.block)

strength_applies is False  (natural attack — bite, claw):
    damage = max(1, roll(min, max)                          - defender.block)
```

- The roll is `rng.randint(damage_min, damage_max)`.
- **Natural attacks never take the STR modifier.** A species' bite range already encodes how
  strong it is; adding its STR modifier counts the same fact twice and was what drove every
  animal's damage to the `max(1, ...)` floor.
- **Ranged weapons pass `strength_applies=False`** even though they are wielded — a bow's power
  is the bow's. This is the one deliberate melee/ranged asymmetry.
- The `max(1, ...)` floor guarantees a confirmed hit is never a no-op.

### §23.3 Poison

When `poison_chance > 0` **and the attack hit**, roll `rng.randint(1, 100) <= poison_chance`.
On success `AttackResult.poisoned` is `True`; **`resolve_attack` does not apply the effect** —
the caller does, via `status.apply_effect`, so combat stays a pure calculator.

### §23.4 Death

`killed` is `defender_hp <= 0`. Combat does not remove anything or end anything; `game.py`
owns those consequences (§7 v5).

---

## §24 (new) — NPCs: `roguelike/npc.py`

Imports `roguelike.stats`, `roguelike.status`, `roguelike.level`, `roguelike.world`,
`roguelike.pathfind`, `roguelike.fov`. **Must not import `game`, `combat`, `render`, `keys` or
`events`.**

```python
class Species(Enum):
    RAT = auto(); JACKAL = auto(); GIANT_BAT = auto(); CAVE_SNAKE = auto()

@dataclass(frozen=True)
class SpeciesData:
    name: str; glyph: str
    stats: Stats
    attack_min: int; attack_max: int
    xp_value: int
    poison_chance: int = 0

class AiState(Enum):
    WANDERING = auto(); HUNTING = auto()

@dataclass(frozen=True)
class NPC:
    actor_id: int
    species: Species
    actor: Actor
    position: Coord
    energy: int = 0
    ai_state: AiState = AiState.WANDERING
    memory: int = 0

class NpcActionKind(Enum):
    WAIT = auto(); MOVE = auto(); ATTACK = auto()

@dataclass(frozen=True)
class NpcAction:
    kind: NpcActionKind
    target: Coord | None = None

SPECIES_DATA: dict[Species, SpeciesData]
PERCEPTION_RADIUS: int = 10
FORGET_TICKS: int = 5
MONSTERS_PER_LEVEL: int = 6
SPAWN_SAFE_RADIUS: int = 8
SPAWN_MIN_SEPARATION: int = 5

def plan_action(rng, npc: NPC, level: Level, open_doors: frozenset[Coord],
                occupied: frozenset[Coord], player: Coord) -> NpcAction: ...
def spawn_npcs(rng, level: Level, first_actor_id: int = 1) -> tuple[NPC, ...]: ...
```

`plan_action` returns an **intent**; `game.py` executes it. This mirrors the
`activity.py` / `game.py` split exactly (§19) and is why `npc.py` never imports `combat`.

### §24.1 The bestiary — binding

| Species | Glyph | STR/AGI/VIT | HP | Speed | Evasion | Block | Attack | XP | Poison |
|---|---|---|---|---|---|---|---|---|---|
| Rat | `r` | 4/14/3 | 17 | 140 | 17 | 0 | 1–3 | 5 | — |
| Jackal | `j` | 8/13/5 | 25 | 130 | 14 | 0 | 2–4 | 10 | — |
| Giant bat | `B` | 3/18/2 | 13 | 180 | 29 | 0 | 1–2 | 8 | — |
| Cave snake | `s` | 6/8/5 | 25 | 80 | 0 | 0 | 2–4 | 12 | **30%** |

HP/Speed/Evasion/Block are **derived from the stats by `stats.derive`** and must not be stored
independently — every value above falls out of §20.1 with no special cases, and that is a
property worth testing.

*Measured 1v1 against a baseline player: rat 100%, jackal 97.0%, bat 100%, snake 100% player
wins. The jackal was retuned from an earlier 8/16/6 that the player lost **98.8%** of the time.*

> **Two jackals beat a baseline player 100% of the time** (600 runs, zero wins). Being
> surrounded is death — that is the intended roguelike shape, and it is why §24.4's spawn
> separation is a hard rule and not a nicety.

### §24.2 `plan_action` — the AI

**WANDERING:**
- Check line of sight to the player: `fov.has_line_of_sight(level, open_doors, npc.position,
  player)` **and** Chebyshev distance ≤ `PERCEPTION_RADIUS`. On success the caller switches the
  NPC to `HUNTING` with `memory = 0`.
- Otherwise: a coin flip (salt 4) between `WAIT` and `MOVE` to a uniformly chosen passable
  orthogonal neighbour. With no passable neighbour, `WAIT`.

**HUNTING:**
- Adjacent to the player (Chebyshev distance 1) → `ATTACK`.
- Otherwise `find_path` to the player over `world.is_planning_passable`, **not** restricted to
  `explored` — an NPC is not fogged; it lives here. Return `MOVE` to `path[1]`.
- No path → `WAIT`.
- If line of sight to the player failed this action, the caller increments `memory`; past
  `FORGET_TICKS` the NPC reverts to `WANDERING`.

**Occupancy:** a cell in `occupied` (any other NPC, or the player) is **not** a legal `MOVE`
target and must be excluded from the passability predicate handed to `find_path`. NPCs do not
stack and do not swap places.

**Doors:** an NPC bumps a closed door open by the same rule as the player (§7 v2), consuming
its action and emitting `DOOR_OPENED` **only if the door is in the player's `visible` set**
(§16.1).

### §24.3 Energy scheduling

`ENERGY_THRESHOLD: int = 100`, defined in `game.py` (§7 v5) since the loop owns it.

Every player action that consumes a turn **is one world-tick**. On each tick, for every NPC on
the current level, in `actor_id` order:

```python
npc.energy += npc.speed
while npc.energy >= ENERGY_THRESHOLD:
    <perform one action>
    npc.energy -= ENERGY_THRESHOLD
```

At `speed == 100` this is exactly one action per tick. The loop is bounded by construction
(finite `speed` in, fixed threshold out); **no iteration cap may be added.**

**The player is not in the accumulator.** Every accepted keypress consumes exactly one turn and
executes immediately, unchanged from v1–v4. The player's `speed` is computed and displayed but
does not gate their own turn. *Rationale: a unified model defers the player's keypress pending
energy, which either eats keystrokes or needs buffering — an input-model change the minimal
scope forbids.*

**Order is a fixed tuple, never a set.** `GameState.npcs` is ordered by `actor_id` and iterated
in that order, the same discipline `pathfind.DIRECTIONS` enforces.

### §24.4 Spawning — deterministic, at generation time

`spawn_npcs` places **exactly `MONSTERS_PER_LEVEL` (6)** NPCs using the level's own seeded
`Random`, so a level's population is as reproducible as its rooms.

Binding placement rules:
- Only on cells passable with an empty `open_doors` set.
- **Never within `SPAWN_SAFE_RADIUS` (8, Chebyshev) of `level.player_start`.**
- **Never within `SPAWN_MIN_SEPARATION` (5, Chebyshev) of another spawned NPC.**
- Species chosen uniformly at random from the four.
- **`energy` is seeded to `rng.randrange(0, ENERGY_THRESHOLD)`**, not 0. Identical starting
  energy makes a pack move as one organism; staggering costs one line.
- If the rules cannot be satisfied after a bounded number of attempts, **place fewer NPCs**.
  Never relax the separation rules, and never loop forever.

*The two radius rules exist because two jackals are unwinnable (§24.1). A rule that can cluster
them by the staircase kills level-1 characters through no fault of their own.*

### §24.5 NPCs persist per level

`LevelState` (§7 v3) gains `npcs: tuple[NPC, ...]`, for exactly the reason `explored` and
`open_doors` are already there: runtime state that generation cannot re-derive and that must
survive a stairs round trip unchanged. **NPCs on a level the player has left are frozen** — they
do not act, do not heal, and do not move (user decision: full simulation applies to the
*current* level).

---

## §14 v5 — `fov.py` gains one function

The v4 freeze on `fov.py` does not bind v5. **`compute_visible` is unchanged** — no existing
test may be altered.

> **This is a hard constraint, not a preference — verified, not assumed.**
> `tests/test_activity.py` (a **frozen** file, which no v5 worker may edit) drives a complete
> auto-explore run against the real `fov.compute_visible` at lines 769 and 808. Any change to
> `compute_visible`'s behaviour breaks a test no v5 worker is permitted to repair — the exact
> failure mode v3 hit and v4's STATE recorded. `has_line_of_sight` is **additive only**.

```python
def has_line_of_sight(level: Level, open_doors: frozenset[Coord],
                      observer: Coord, target: Coord) -> bool: ...
```

- Same doubled-integer exact-segment geometry as `compute_visible`: eye at the **observer's**
  cell centre, the eight sample points on the **target** cell, clear iff any one segment crosses
  no opaque cell.
- Opacity is computed over the **bounding box of `observer` and `target`, grown by one cell** —
  never the whole radius disc. *This is the entire point: 0.167 ms per check versus 14.888 ms
  for a `compute_visible` call. Thirty naive checks cost 218.8 ms and blow the turn budget;
  thirty of these cost 5.0 ms.*
- `has_line_of_sight(a, a)` is `True`.
- No radius parameter — range limits are the caller's business (§24.2 applies
  `PERCEPTION_RADIUS`).

> **Binding argument order: `(observer, target)`.** Permissive line of sight is **not
> symmetric** — measured, **2 of 720 cell pairs (0.28%) disagree** on who can see whom, because
> the eye is at a cell *centre* while the target is tested at eight *boundary* samples. NPC
> awareness always asks `(npc_position, player_position)`. Swapping the arguments is a real
> behaviour change, not a refactor.

**Ranged targeting must NOT call this function** (§7 v5) — it reads `state.visible`, which is
already computed. Two sources of truth for "can I see it" would let the screen and the target
list disagree.

---

## §5 v5 — input

```python
class CommandKind(Enum):
    ...                        # all v4 members unchanged
    FIRE        = auto()       # NEW — "f"
    TARGET_NEXT = auto()       # NEW — Tab
```

| Intent | Key |
|---|---|
| `FIRE` | `f` |
| `TARGET_NEXT` | `Tab` (`curses.ascii.TAB` / `9`) |

- **Verified unbound before assignment:** `f`, `F`, `t`, `a`, `i`, `g` all currently map to
  `UNKNOWN`. `Tab` is code 9 and unbound.
- Both carry `dx == dy == 0`.
- **Every v1–v4 binding is unchanged.** `F`, `t`, `a`, `i`, `g` remain `UNKNOWN`.

---

## §16 v5 — events

`EventKind` gains **twelve** members (16 → 28); the existing sixteen are unchanged. `MESSAGES`
must still hold an entry for **every** `EventKind`.

| Kind | Message |
|---|---|
| `PLAYER_HIT_NPC` | `You hit the {name}.` |
| `PLAYER_MISSED_NPC` | `You miss the {name}.` |
| `NPC_HIT_PLAYER` | `The {name} hits you.` |
| `NPC_MISSED_PLAYER` | `The {name} misses you.` |
| `NPC_KILLED` | `You kill the {name}!` |
| `PLAYER_DIED` | `You die...` |
| `LEVELLED_UP` | `Welcome to level {level}.` |
| `POISONED` | `You feel sick.` |
| `POISON_DAMAGE` | `The poison burns.` |
| `NO_TARGET` | `There is nothing to shoot at.` |
| `TARGETING` | `Target: {name}. [Tab] next, [f] fire, any other key cancels.` |
| `SPOTTED_HOSTILE` | ~~`A {name} comes into view!`~~ → **`There is a {name} in view.`** — see the amendment below |

`Event` gains two optional fields alongside `depth`:

```python
@dataclass(frozen=True)
class Event:
    kind: EventKind
    depth: int | None = None
    name: str | None = None      # NEW — species name
    level: int | None = None     # NEW — character level
```

A template requiring `{name}` or `{level}` raises `ValueError` when the corresponding field is
`None`, exactly as `{depth}` already does. Fields a template does not use are ignored.

### §16.1 The message-line cap — new, and binding

`message_for` joins events with a space and is **unchanged**. But six NPCs acting in one tick
can produce a line far wider than 80 columns, which the renderer would silently clip.

**`game.py` caps `events` to at most `MAX_EVENTS: int = 3` before storing it on the state**,
keeping the highest-priority events. Priority, highest first:

1. `PLAYER_DIED`
2. `LEVELLED_UP`, `NPC_KILLED`
3. `NPC_HIT_PLAYER`, `POISONED`, `POISON_DAMAGE`
4. `PLAYER_HIT_NPC`, `PLAYER_MISSED_NPC`
5. everything else

Within a priority band, emission order is preserved. **Events for NPCs outside the player's
`visible` set are dropped before the cap is applied** — except those that affect the player
directly (`NPC_HIT_PLAYER`, `POISONED`), which are always kept, because the player always
perceives themselves. There is no ambient "you hear scurrying"; this project has no "you bump
into a wall" message either, for the same reason.

---

## §7 v5 — game state, NPC turns, targeting

```python
@dataclass(frozen=True)
class Player:
    actor: Actor
    melee: Weapon = items.DAGGER
    ranged: Weapon = items.SHORTBOW
    xp: int = 0
    level: int = 1
    regen_counter: int = 0

@dataclass(frozen=True)
class Targeting:
    targets: tuple[Coord, ...]     # sorted by (distance, coord) — a total order
    index: int = 0

@dataclass(frozen=True)
class LevelState:
    ...                            # v3 fields unchanged
    npcs: tuple[NPC, ...] = ()     # NEW

@dataclass(frozen=True)
class GameState:
    ...                                     # all v4 fields unchanged, in order
    player_actor: Player = <constructed>    # NEW
    npcs: tuple[NPC, ...] = ()              # NEW
    targeting: Targeting | None = None      # NEW

ENERGY_THRESHOLD: int = 100
MAX_EVENTS: int = 3

def advance_npcs(state: GameState) -> GameState: ...      # NEW, pure
def level_up(state: GameState) -> GameState: ...          # NEW, pure
```

All three new `GameState` fields are **appended with defaults**, so every v1–v4 construction
keeps working unchanged. This is the same discipline v4 used and it is what keeps
`tests/test_integration.py` green across the increment.

### §7.8 `advance_npcs` — one world-tick of every NPC

Pure. Called **once by `step` and once by `advance`, immediately after any action that consumed
a turn**, and never otherwise. A rejected move consumes no turn and therefore does not tick the
world — v1's headline rule is unchanged.

Order of operations within one tick:

1. **Status and regeneration first**, for the player and every NPC (§22.3, §22.4). Poison damage
   can kill; if the player dies here, stop and end the run.
2. **Then every NPC acts**, in `actor_id` order, by the energy rule (§24.3).
3. Dead NPCs are removed from `state.npcs`; their `xp_value` is credited (§7.10).
4. Events are filtered and capped (§16.1).

The player's field of view is recomputed by `_take_turn` as it always has been. **NPC movement
does not recompute it** — moving monsters do not change what terrain is visible.

### §7.9 Melee is bump-to-attack

`try_move` (§6) gains a third blocked case alongside wall and closed door: **blocked by an NPC**.

```python
@dataclass(frozen=True)
class MoveResult:
    ...                                  # v2 fields unchanged
    blocked_by_npc: Coord | None = None  # NEW
```

`step` resolves it as a melee attack with `state.player_actor.melee`, `strength_applies=True`.
It consumes a turn, does **not** move the player, and emits `PLAYER_HIT_NPC` or
`PLAYER_MISSED_NPC`. There is **no new command and no new binding** — walking into an occupied
cell *is* the attack, the ADOM/NetHack convention v4 already cited precedent from.

`try_move` needs the occupied set to detect this; it is passed in as a new parameter defaulting
to `frozenset()`, so every existing call site keeps working.

### §7.10 Ranged targeting — a zero-turn sub-mode

Modelled on `awaiting_walk` (§7.4), which already proves the pattern.

| Command | State | Behaviour |
|---|---|---|
| `FIRE` | no targeting | Build the target list. Empty → emit `NO_TARGET`, **no turn**. Otherwise set `Targeting(targets, 0)`, emit `TARGETING`, **no turn**. |
| `TARGET_NEXT` | targeting | `index = (index + 1) % len(targets)`, emit `TARGETING`, **no turn**. |
| `FIRE` | targeting | Fire at `targets[index]` with `ranged`, `strength_applies=False`. Clear targeting. **Consumes a turn.** |
| anything else | targeting | Clear targeting, **consume the command entirely**: no turn, no action, no event — exactly a mistyped `w`-prefix. |

**The target list is `state.visible ∩ {npc.position}`**, filtered to Chebyshev distance ≤
`ranged.range`, sorted by `(chebyshev_distance, coord)` for a total order. It **must not** call
`has_line_of_sight` (§14 v5): reading the already-computed `visible` set costs nothing and makes
"what I can shoot" identical to "what I can see" by construction.

**Targeting does not survive a level change or the target's death.** If the selected NPC is gone
when `FIRE` resolves, the list is rebuilt and the shot is cancelled with no turn.

### §7.11 Levelling

```
xp_to_next(L) = 25 * L * L          # XP to go from level L to L+1

while xp >= xp_to_next(level):
    xp -= xp_to_next(level)
    level += 1
    vit += 1
    if level is odd:  str_ += 1
    else:             agi += 1
    hp += (new max_hp - old max_hp)
```

- The loop **subtracts** the spent XP, so one large kill crossing two thresholds behaves
  correctly. *An earlier draft tested `xp_to_next(level + 1)` against a definition that said
  `L` was the current level — reaching level 2 would have cost 100 XP where it should cost 25.*
- Derived stats are recomputed through **the same `stats.derive`** as spawning. There is no
  second HP formula.
- **Current HP grows by exactly the max-HP delta — not a full heal.** A full heal would make
  levelling a free heal-on-demand for anyone willing to grind.
- No stat-allocation UI. The user asked for formulas, not a choice screen.

### §7.12 Death ends the run

The player reaching `hp <= 0` from any source — melee, ranged, poison — clears `running` and
sets `outcome` from `message_for`, exactly parallel to the existing `LEFT_DUNGEON` ending. The
farewell is printed by `play` after the terminal is restored. There is **no separate "died of
poison" code path**, only a different `Event`.

### §7.14 `interruption` becomes live — the v4 seam is now wired

CONTRACT-v4 §7.6 shipped `interruption(before, after) -> Event | None` returning `None` in every
case, because the conditions the user named needed monsters and hit points. **Both now exist, so
the requirement becomes live in v5.** The original wording:

> *"automatically cancelled by the game engine on certain events we will implement in the future
> (seeing a hostile, receiving damage, character state change)"* — user requirement,
> RESEARCH-v4 §2

All three conditions are computable from the two states `interruption` already receives. In
priority order:

1. **A hostile comes into view** — an NPC position in `after.visible` that was not in
   `before.visible`. Returns `Event(SPOTTED_HOSTILE, name=<species name>)`. When several appear
   at once, the one nearest the player by Chebyshev distance, ties broken by coordinate.
2. **The player took damage** — `after` player `hp` < `before` player `hp`. Returns
   `Event(INTERRUPTED)`.
3. **Character state changed** — the player's `status_effects` gained a kind not present before.
   Returns `Event(INTERRUPTED)`.

Otherwise `None`. **Opening a door still does not interrupt** (v4 user decision 3), and neither
does an NPC merely moving while already visible.

*This is not a nicety. Two jackals beat a baseline player 100% of the time (§24.1); an
auto-explore that walks into a pack and keeps walking is a death with no chance to react.*

**Amendment to §7.5 (v4):** a non-`None` `interruption` result clears the activity, and its
event is **appended to the turn's events, not substituted for them**. v4 replaced them, which
was harmless when the function always returned `None`; substituting now would discard
`The jackal hits you.` in favour of a bare `You stop.` The combined list is then filtered and
capped by §16.1.

`interruption` stays **one pure function**. Do not build a registry, an observer list, or a
plugin mechanism — v4 said so with one condition, and it still holds with three.

### §7.13 `format_stats` is finally filled in

`format_stats` currently returns `""`, its docstring saying the row is *"reserved for player
stats — hit points, level, that sort of thing, and none of them exist yet"*. It now returns:

```
HP {hp}/{max_hp}  Lv {level}  XP {xp}/{xp_to_next}  Str {s} Agi {a} Vit {v}
```

Two spaces between fields, as everywhere else. A plain `str`; fitting it to the terminal is the
renderer's job (§4.2).

---

## §4 / §15 v5 — rendering NPCs

- An NPC is drawn **only when its position is in `visible`**. NPCs are **never** drawn from
  `explored` — monsters move, and a remembered monster is a lie.
- The NPC glyph is drawn over the terrain, exactly as the player glyph is.
- The player glyph wins over an NPC glyph on the same cell (which §24.2's occupancy rule makes
  unreachable anyway).
- `style.py` gains `Role.NPC`. At 256 colours: rat `250`, jackal `173`, giant bat `140`, cave
  snake `70`. At 8 colours all NPCs are `_ANSI_RED`. Monochrome: terminal default.
- `Role.NPC` with `Visibility.EXPLORED` **raises `ValueError`**, like `Role.PLAYER` already
  does — an NPC is only ever drawn when visible, so asking is a caller bug.
- When targeting is active, the selected target's cell is drawn with `curses.A_REVERSE`. This is
  the only cursor; **no separate cursor glyph is drawn.**

---

## §9 v5 — file ownership

| Path | Owner |
|---|---|
| `roguelike/stats.py`, `roguelike/items.py`, `roguelike/status.py` + their tests | **T22** |
| `roguelike/fov.py`, `tests/test_fov.py` | **T23** |
| `roguelike/keys.py`, `roguelike/events.py`, `tests/test_keys.py`, `tests/test_events.py` | **T24** |
| `roguelike/combat.py`, `tests/test_combat.py` | **T25** |
| `roguelike/npc.py`, `tests/test_npc.py` | **T26** |
| `roguelike/render.py`, `roguelike/style.py`, `tests/test_render.py`, `tests/test_style.py` | **T27** |
| `roguelike/movement.py`, `roguelike/game.py`, `tests/test_movement.py`, `tests/test_game.py` | **T28** |
| `roguelike/tiles.py`, `level.py`, `generator.py`, `world.py`, `dungeon.py`, `pathfind.py`, `activity.py` and their tests | **frozen — nobody may edit** |
| `main.py`, `tests/test_integration.py`, `.plan/**` | orchestrator |

**Verified before freezing, not assumed** — the lesson v4's STATE recorded after v3 stranded a
test no worker could repair. Every frozen suite was searched for surfaces v5 changes:

- `tests/test_world.py` asserts `world.py`'s public surface — **v5 adds nothing to `world.py`**.
- `tests/test_pathfind.py` and `tests/test_activity.py` pin their own modules' `__all__` — v5
  changes neither.
- `tests/test_movement.py` asserts `MoveResult`'s fields — `movement.py` **is not frozen**, and
  T28 owns both it and its test.
- `tests/test_generator.py` and `tests/test_dungeon.py` do not mention NPCs; spawning lives in
  `npc.py` and is called by `game.py`, **not** by the generator.

**No frozen file contains an assertion v5 invalidates.** Searched, not assumed: the only
`__all__` assertions in frozen suites are `tests/test_dungeon.py:369` (pins `dungeon.__all__`,
which v5 does not touch) and `tests/test_activity.py:730` (pins `activity.__all__`, likewise).
The other three `__all__` assertions live in `tests/test_render.py`, `tests/test_fov.py` and
`tests/test_game.py` — **all three owned by a v5 worker** (T27, T23, T28).

### Baseline counts, measured on the v4 build

Workers should know exactly what they are changing from. Any test asserting a member count
must be updated to the right-hand column by its owner.

| Surface | v4 | v5 |
|---|---|---|
| `CommandKind` members | 7 | **9** |
| `EventKind` members / `MESSAGES` entries | 16 / 16 | **28 / 28** |
| `Role` members | `TERRAIN, DOOR, PLAYER` (3) | **4** (`+ NPC`) |
| `fov.__all__` | `DEFAULT_RADIUS, compute_visible` | **+ `has_line_of_sight`** |
| `MoveResult` fields | `position, moved, blocked_by_door` | **+ `blocked_by_npc`** |

*Key availability re-verified on this build: `f`, `F`, `t`, `a`, `i`, `g` and `Tab` (code 9,
`curses.ascii.TAB`) all currently translate to `UNKNOWN`.*

### Expected transitional breakage — planned, each with an owner

| Breaks | Cause | Fixed by |
|---|---|---|
| `tests/test_keys.py` — `CommandKind` member count | two new kinds | T24 (same owner) |
| `tests/test_events.py` — `EventKind` member count, `Event` fields | twelve new kinds (16 → 28), two new fields | T24 (same owner) |
| `tests/test_fov.py` — module `__all__` | one new function | T23 (same owner) |
| `tests/test_style.py` — `Role` member count | `Role.NPC` | T27 (same owner) |
| `tests/test_movement.py` — `MoveResult` fields | `blocked_by_npc` | T28 (same owner) |
| `tests/test_game.py` — `GameState` field list | three new fields | T28 (same owner) |
| `tests/test_integration.py` | all of the above | orchestrator, final wave |

---

## §10 v5 — import graph, still acyclic

```
tiles, events, keys, pathfind, items, status     ← leaves
stats.py     ← status                                       NEW
level        ← tiles
world        ← tiles, level
style        ← tiles
generator    ← tiles, level
fov          ← level, world
movement     ← level, world
render       ← tiles, level, style
dungeon      ← generator, level
activity     ← level, world, pathfind
combat.py    ← stats, items, status                         NEW
npc.py       ← stats, status, level, world, pathfind, fov   NEW
game.py      ← level, keys, movement, render, fov, world, dungeon,
               events, pathfind, activity, stats, items, status,
               combat, npc
main.py      ← game
```

- `combat.py` must **not** import `events`, `npc`, `level` or `game`.
- `npc.py` must **not** import `combat`, `game`, `render`, `keys` or `events`.
- `items.py` and `status.py` import nothing from the project.

---

## §11 v5 — error conventions (additions)

| Situation | Behaviour |
|---|---|
| `derive` with any int stats | never raises |
| `apply_effect` with a shorter-lasting duplicate | input returned unchanged |
| `tick_effects` on an empty tuple | `((), 0)` |
| `resolve_attack` when `damage_min > damage_max` | `ValueError` |
| `resolve_attack` on an already-dead defender | resolves normally; `killed` stays `True` |
| `has_line_of_sight(a, a)` | `True` |
| `has_line_of_sight` out of bounds | `False`, never raises |
| `plan_action` with no passable neighbour | `NpcAction(WAIT)` |
| `plan_action` when no path to the player exists | `NpcAction(WAIT)` |
| `spawn_npcs` when placement rules cannot be met | fewer NPCs; never loops forever, never relaxes the rules |
| `FIRE` with no valid target | `NO_TARGET`, no turn |
| `FIRE` when the selected target died | list rebuilt, shot cancelled, no turn |
| `TARGET_NEXT` with no targeting active | `UNKNOWN` — nothing happens |
| `advance_npcs` with no NPCs | the **status and regeneration phase still runs** (§7.8 step 1); the state is returned unchanged only if that phase changed nothing — see the amendment below |
| Player dies during the status tick | run ends immediately; remaining NPCs do not act |

All v1–v4 rows still apply.

---

## §11.1 (amendment) — the empty-level defect

**Issued after T28 reported it. This corrects a genuine contradiction in this contract, not a
worker's mistake.**

§11 v5 originally read *"`advance_npcs` with no NPCs — state returned unchanged"*, which
contradicts §7.8's *"status and regeneration first, for the player and every NPC"*. T28
implemented the literal §11 row, as it was required to, and flagged the consequence rather than
quietly departing from the contract. Measured on the resulting build:

- **Regeneration freezes on a cleared floor**: 40 world-ticks at 10 hp left the player on 10 hp,
  where the same 40 ticks with monsters alive healed to 14.
- **Poison freezes permanently**: a 5-turn poison still read `remaining_turns=5` after 10 ticks —
  never damaging, never expiring.

This matters because regeneration is *the* mechanism that makes v5 playable at all: RESEARCH-v5
§7 measured **0.0%** of floors cleared without it against **61.5%** with it, and a large share of
the ~180 exploration turns per level happen **after** the monsters are dead. Freezing regen there
silently reverts the balance to the unplayable version.

**Binding correction.** In `advance_npcs`:

1. The **status-effect and regeneration phase always runs** for the player, whether or not any
   NPC exists on the level. It is not gated on `state.npcs`.
2. Only the **NPC action phase** is skipped when there are no NPCs.
3. Returning the identical state object remains correct when nothing at all changed — an
   unhurt, unpoisoned player on an empty level — but it must be a *consequence* of no change,
   never a precondition checked before the status phase.

Everything else in §7.8's ordering is unchanged.

---

## §16.2 (amendment) — `SPOTTED_HOSTILE`'s wording

**Issued after T30 flagged the drift. This corrects the contract to match shipped behaviour;
the code was already right.**

§7.14 originally interrupted an activity when a hostile *newly* entered view, and
`A {name} comes into view!` described that exactly. The rule was later widened, at the user's
request, to interrupt whenever **any** hostile is visible — at which point the old wording was
simply false: it fired for a monster that had been on screen for ten turns.

The shipped message is **`There is a {name} in view.`**, which is true in both cases. The code
and its tests were changed at the time; this contract row was not, and stayed stale until a v6
worker read both and reported the mismatch rather than "fixing" one to match the other.

Also amended alongside that change and recorded here for completeness: `interruption` now
answers **damage and status changes before the hostile check**, so a bitten player reads
`The jackal hits you. You stop.` rather than being told about a jackal they have been watching.
