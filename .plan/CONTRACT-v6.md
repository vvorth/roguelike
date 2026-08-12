# CONTRACT v6 — inventory, weapons, damage types, shields, consumables and chests

**Frozen once written. Workers may not edit it.** A worker that believes it is wrong reports
that and stops; the orchestrator decides.

Amends `CONTRACT.md` (v1) through `CONTRACT-v5.md`. Everything in those stays binding unless
amended here.

Decisions from `.plan/RESEARCH-v6.md`, confirmed by the user: **full damage-type resistances**
(not flavour) · **no food** · **inventory on `i`** · **shields work against arrows, with a
chance to be hit anyway**. Orchestrator's recommendations, accepted by the user: **coarse
resistance tiers, applied to the raw roll**.

**Every number here was measured against the shipped modules.** §0 records the sweeps and the
two proposals they killed.

---

## §0 The measurements that set these numbers

### §0.1 Weapon damage is violently sensitive — the tiers are close together

Floor-clear rate by melee damage range, 1200 runs each, driven through the real
`stats.derive` and `combat.to_hit_chance`:

| damage | floors cleared |
|---|---|
| 1–4 | **2.2%** |
| 2–4 | 17.4% |
| **2–5 (shipped dagger)** | **45.6%** |
| 3–5 | 70.0% |
| 3–6 | 84.6% |
| 4–6 | 93.9% |
| 4–8 | 98.3% |

**One point of damage is worth 20–40 percentage points of survival.** RESEARCH-v6 proposed
crude 1–4 and fine 4–8; both are rejected here — 1–4 is unplayable and 4–8 trivialises the
game. §2 uses 2–4 / 2–5 / 3–5, which is the widest spread the band tolerates.

### §0.2 Flat damage reduction saturates — shields are a *chance*, never a subtraction

At +1 flat block, 58% of all monster damage rolls already floor to 1 and the player wins 100%
of jackal fights. **No item in this project may grant flat damage reduction.** (RESEARCH-v6 §0.)

### §0.3 Percentage resistances are unusable at these numbers

On a 2–5 roll: 25% and 33% resistance produce the identical set of outcomes, and 66% and 75%
are both flat 1. Above about half, every percentage is the same percentage. Worse, the same
50% resistance yields average damage from **1.25 to 2.50 — a 2× swing — purely from where in
the pipeline it is applied.**

**Hence coarse tiers, applied at one pinned point** (§3).

### §0.4 Shields, measured end to end

Floor-clear rate with the shipped dagger, by shield block chance:

| shield | chance | floors cleared |
|---|---|---|
| none | 0% | 45.6% |
| buckler | 10% | 60.9% |
| kite | 18% | 77.7% |
| tower | 25% | 85.8% |

RESEARCH-v6 proposed 15/25/35, measured at 72/86/94% — too strong. **Shipped: 10/18/25.**

### §0.5 Resistance is severe, and that is why a second weapon exists

With one species resisting the player's only damage type, floor clears drop **45.6% → 8.5%**.
Carrying a second weapon of another type returns it to normal. That is the intended pressure
and the reason inventory exists at all; it is *not* a reason to soften resistance.

---

## §25 (new) — items: `roguelike/items.py`

A leaf. Imports nothing from the project.

```python
class ItemKind(Enum):
    WEAPON = auto(); SHIELD = auto(); CONSUMABLE = auto()

class DamageType(Enum):
    SLASH = auto(); PIERCE = auto(); BLUNT = auto()

class Resistance(IntEnum):
    IMMUNE = 0; RESISTANT = 1; NORMAL = 2; VULNERABLE = 3

class Grade(IntEnum):
    CRUDE = 0; STANDARD = 1; FINE = 2

@dataclass(frozen=True)
class Weapon:
    name: str
    kind: WeaponKind                  # MELEE | RANGED — unchanged from v5
    damage_min: int
    damage_max: int
    range: int = 1
    damage_type: DamageType = DamageType.SLASH   # NEW, appended
    grade: Grade = Grade.STANDARD                # NEW, appended

@dataclass(frozen=True)
class Shield:
    name: str
    block_chance: int                 # percent
    grade: Grade = Grade.STANDARD

@dataclass(frozen=True)
class Consumable:
    name: str
    heal: int = 0                     # instant HP
    regen_turns: int = 0              # duration of a regeneration effect
    regen_magnitude: int = 0          # HP per tick of that effect

@dataclass(frozen=True)
class Inventory:
    carried: tuple[object, ...] = ()
    melee: Weapon | None = None
    ranged: Weapon | None = None
    shield: Shield | None = None

CARRY_LIMIT: int = 20

def equip(inventory: Inventory, item) -> Inventory: ...
def unequip(inventory: Inventory, slot: str) -> Inventory: ...
def add(inventory: Inventory, item) -> tuple[Inventory, bool]: ...
def drop(inventory: Inventory, index: int) -> tuple[Inventory, object | None]: ...
```

**`Weapon`'s two new fields are appended with defaults**, so every v5 construction — including
`DAGGER` and `SHORTBOW` as written — keeps working.

### §25.1 The item tables — binding

| Weapon | kind | damage | type | grade | range |
|---|---|---|---|---|---|
| club | MELEE | 2–4 | BLUNT | CRUDE | 1 |
| **dagger** | MELEE | **2–5** | PIERCE | STANDARD | 1 |
| sword | MELEE | 3–5 | SLASH | FINE | 1 |
| sling | RANGED | 2–4 | BLUNT | CRUDE | 5 |
| **shortbow** | RANGED | **1–4** | PIERCE | STANDARD | 6 |
| longbow | RANGED | 3–5 | PIERCE | FINE | 8 |

`DAGGER` and `SHORTBOW` keep their v5 damage exactly. **Do not retune them** — 2–5 is the
reference the whole balance is measured against (§0.1).

| Shield | block | grade |
|---|---|---|
| buckler | 10% | CRUDE |
| kite shield | 18% | STANDARD |
| tower shield | 25% | FINE |

| Consumable | effect |
|---|---|
| potion of healing | `heal=10` |
| bandage | `regen_turns=5, regen_magnitude=3` |

**There is no food and no hunger.** Do not add either.

### §25.2 Inventory rules
- `carried` holds at most `CARRY_LIMIT` (20). `add` returns `(inventory, False)` when full and
  changes nothing.
- Equipping moves an item from `carried` into its slot; whatever was in that slot returns to
  `carried`. Equipping a `Weapon` uses `melee` or `ranged` by its `WeaponKind`.
- **A slot may be `None`** — bare-handed is representable. `game.py` defines what that means
  (§7 v6).
- All four functions are **pure**: they return new values and mutate nothing.
- No weight, no encumbrance, no stacking, no `Item` base class beyond what is written above.

---

## §26 (new) — resistance and the damage pipeline

### §26.1 The multiplier — binding

| `Resistance` | multiplier |
|---|---|
| `IMMUNE` | 0 |
| `RESISTANT` | halved, rounding down |
| `NORMAL` | unchanged |
| `VULNERABLE` | doubled |

### §26.2 Where it applies — binding, and this is the 2× lever

**The multiplier is applied to the raw damage roll, before the strength modifier and before
block:**

```
damage = max(1, resisted(roll) + strength_modifier - block)     # unless IMMUNE
where resisted(roll) = roll * 2        for VULNERABLE
                     = roll // 2       for RESISTANT
                     = roll            for NORMAL
```

**`IMMUNE` yields damage 0 and bypasses the `max(1, …)` floor** — it is the one case where a
connecting attack does nothing, and the floor must not resurrect it to 1.

*Why here:* §0.3 measured a 2× spread in average damage purely from moving this step around.
Applying it to the raw roll keeps the weapon's own quality the thing being resisted, rather
than the wielder's strength — a resistant hide should blunt the blade, not the arm.

### §26.3 The bestiary's resistances — binding

| Species | resists | vulnerable to | everything else |
|---|---|---|---|
| rat | — | — | NORMAL |
| jackal | — | — | NORMAL |
| giant bat | — | **BLUNT** | NORMAL |
| cave snake | **PIERCE** | — | NORMAL |

Nothing is `IMMUNE`. The tier exists and is tested; no shipped species uses it.

*This is why the player starts with a PIERCE dagger and why a BLUNT club is worth carrying:
the cave snake resists the starting weapon.* §0.5 measured that as 45.6% → 8.5% floor clears
if the player has no alternative, which is the pressure inventory exists to relieve.

---

## §23 v6 — combat gains resistance and the shield roll

```python
@dataclass(frozen=True)
class AttackResult:
    ...                        # v5 fields unchanged
    blocked: bool = False      # NEW — a shield turned it

def resolve_attack(rng, attacker, defender, damage_min, damage_max,
                   strength_applies, poison_chance=0,
                   resistance=Resistance.NORMAL,       # NEW, appended
                   shield_block=0,                     # NEW, appended
                   ) -> AttackResult: ...
```

### §23.5 Draw order — extended, and still binding
1. the to-hit roll (always),
2. **the shield roll** (only if the attack hit **and** `shield_block > 0`),
3. the damage roll (only if the attack hit and was not blocked),
4. the poison roll (only if damage was dealt and `poison_chance > 0`).

A blocked attack therefore consumes exactly two draws and deals no damage and no poison —
a shield stops the venom with the fang.

### §23.6 Ranged blocking — §7.1 of the research, binding

```python
def ranged_block_chance(shield_block: int, defender_agi: int, attacker_agi: int) -> int:
    return clamp(shield_block + (defender_agi - attacker_agi) * 2, 5, 75)
```

- The coefficient is **2**: at 1 the stat gap is decoration, at 3 it swamps the shield.
- **The cap of 75 is the user's "there has to be a chance to be hit anyway" requirement** — an
  arrow lands a quarter of the time even at best. The floor of 5 keeps a small shield from
  being literally worthless.
- The caller decides which chance to pass; `resolve_attack` just rolls what it is given.

> **No live caller in v6.** Nothing shoots at the player and no monster carries a shield, so
> this function ships tested and unexercised by real play — the position `interruption` held
> in v4. Recorded so the gap is a choice.

---

## §27 (new) — chests and loot: `roguelike/loot.py`

A near-leaf: imports only `roguelike.items` and `roguelike.level`.

```python
@dataclass(frozen=True)
class Chest:
    position: Coord
    contents: tuple[object, ...]
    opened: bool = False

CHEST_CHANCE: int = 12          # percent, per level
CHEST_CHANCE_DEEP: int = 8      # percent, from DEEP_FROM onwards
DEEP_FROM: int = 10
CHEST_SAFE_RADIUS: int = 8      # Chebyshev, from player_start

def chest_chance(depth: int) -> int: ...
def grade_weights(depth: int) -> tuple[int, int, int]: ...     # crude, standard, fine
def place_chest(rng, level: Level, depth: int) -> Chest | None: ...
```

### §27.1 Grade by depth — binding

| depth | crude | standard | fine |
|---|---|---|---|
| 1–3 | 80% | 19% | **1%** |
| 4–6 | 55% | 40% | **5%** |
| 7–9 | 30% | 60% | **10%** |
| 10+ | 15% | 70% | **15%** |

A table, not a curve, so one row can be tuned without re-deriving anything.

### §27.2 Placement
- At most **one chest per level**, at `chest_chance(depth)` percent.
- Only on a cell passable with no door open, at least `CHEST_SAFE_RADIUS` from
  `player_start`, and not on a cell holding a monster.
- Drawn from the level's own seeded `Random`, like monsters — a level's contents are as
  reproducible as its rooms.
- Contents: **1–3 items**, each rolled independently against `grade_weights(depth)`.

### §27.3 Chests live outside `Level`
`LevelState.chests` alongside `npcs`. `Level` is frozen terrain and an opened chest is not
terrain — this is the `open_doors` precedent (CONTRACT-v2 §0.6) and it is not negotiable.

**There is no `Tile.CHEST`.** The generator is untouched.

---

## §5 v6 — input

```python
class CommandKind(Enum):
    ...                        # all v5 members unchanged
    INVENTORY = auto()         # NEW — "i"
    PICK_UP   = auto()         # NEW — "g"
```

| Intent | Key |
|---|---|
| `INVENTORY` | `i` |
| `PICK_UP` | `g` |

Verified unbound: `i`, `g`, `e`, `d`, `t`. Every v1–v5 binding is unchanged.

**Inventory-screen keys are not `CommandKind` members.** Inside the screen, the letters `a`–`t`
select a carried item and `e`/`d` act on it; that is a sub-mode reading raw keys, exactly as
look mode reads direction keys, and it must not consume the global namespace.

---

## §16 v6 — events

New members; `MESSAGES` must hold an entry for every one.

| Kind | Message |
|---|---|
| `SHIELD_BLOCKED` | `Your shield turns the blow.` |
| `NPC_SHIELD_BLOCKED` | `The {name} blocks with its shield.` |
| `RESISTED` | `The {name} shrugs it off.` |
| `VULNERABLE_HIT` | `It tears into the {name}!` |
| `IMMUNE_HIT` | `The {name} is unharmed.` |
| `PICKED_UP` | `You pick up the {name}.` |
| `NOTHING_TO_PICK_UP` | `There is nothing here to pick up.` |
| `PACK_FULL` | `You cannot carry any more.` |
| `EQUIPPED` | `You ready the {name}.` |
| `DROPPED` | `You drop the {name}.` |
| `DRANK` | `You drink the {name}.` |
| `BANDAGED` | `You bind your wounds.` |
| `CHEST_HERE` | `There is a chest here.` |
| `CHEST_OPENED` | `The chest holds: {name}` |
| `CHEST_EMPTY` | `The chest is empty.` |

---

## §7 v6 — game state and the new commands

```python
@dataclass(frozen=True)
class Player:
    actor: Actor
    inventory: Inventory = ...        # REPLACES melee/ranged fields
    xp: int = 0
    level: int = 1
    regen_counter: int = 0

@dataclass(frozen=True)
class LevelState:
    ...                               # v5 fields unchanged
    chests: tuple[Chest, ...] = ()    # NEW

@dataclass(frozen=True)
class GameState:
    ...                               # all v5 fields unchanged, in order
    chests: tuple[Chest, ...] = ()    # NEW
    inventory_open: bool = False      # NEW
    inventory_cursor: int = 0         # NEW
```

> **This is the one breaking change in v6.** `Player.melee` and `Player.ranged` move into
> `Inventory`. Every read of `player.melee` becomes `player.inventory.melee`. It is deliberate:
> keeping them on `Player` *and* adding a shield slot would put equipment in two places.

### §7.15 Bare-handed
A `None` melee slot attacks for **1–2 BLUNT**, `strength_applies=True`. A `None` ranged slot
means `f` reports `NO_TARGET`-style refusal rather than firing.

### §7.16 The new commands
| Command | Behaviour |
|---|---|
| `PICK_UP` (`g`) | Take one item from the chest on this cell. **Costs a turn.** Nothing here → `NOTHING_TO_PICK_UP`, no turn. Pack full → `PACK_FULL`, no turn. |
| `INVENTORY` (`i`) | Open the screen. **No turn**, ever — like the help and look screens. |

### §7.17 The inventory screen
A full-screen page built with `render.render_text_page`, exactly as the help screen is. Inside
it: a letter selects an item, `e` equips or uses it, `d` drops it, any other key closes.
**Equipping and drinking cost a turn; opening, browsing and closing cost none.**

### §7.18 Chests
- A chest is opened by `PICK_UP` while standing on it, one item per turn.
- Stepping onto a chest's cell emits `CHEST_HERE`, like a staircase.
- An emptied chest stays on the map with `opened=True` and reports `CHEST_EMPTY`.

---

## §9 v6 — file ownership

| Path | Owner |
|---|---|
| `roguelike/items.py`, `tests/test_items.py` | **T29** |
| `roguelike/keys.py`, `roguelike/events.py` + their tests | **T30** |
| `roguelike/status.py`, `tests/test_status.py` | **T31** |
| `roguelike/combat.py`, `tests/test_combat.py` | **T32** |
| `roguelike/npc.py`, `tests/test_npc.py` | **T33** |
| `roguelike/loot.py`, `tests/test_loot.py` | **T34** |
| `roguelike/render.py`, `roguelike/style.py` + their tests | **T35** |
| `roguelike/game.py`, `roguelike/movement.py` + their tests | **T36** |
| `tiles.py`, `level.py`, `generator.py`, `world.py`, `dungeon.py`, `pathfind.py`, `activity.py`, `fov.py`, `stats.py` and their tests | **frozen** |
| `main.py`, `tests/test_integration.py`, `.plan/**` | orchestrator |

**Freeze safety, verified not assumed.** `stats.py` is frozen this time and nothing in v6
changes it — resistance lives in `items.py` and is applied in `combat.py`. No frozen suite
asserts on `Weapon`'s field count, `Player`'s fields, or `AttackResult`'s fields; those live in
`test_items.py`, `test_game.py` and `test_combat.py`, all owned by a v6 worker.

---

## §10 v6 — import graph, still acyclic

```
tiles, events, keys, pathfind, items, status     ← leaves
stats        ← status
level        ← tiles
world        ← tiles, level
style        ← tiles
generator    ← tiles, level
fov          ← level, world
movement     ← level, world
render       ← tiles, level, style
dungeon      ← generator, level
activity     ← level, world, pathfind
combat       ← stats, items, status
loot         ← items, level                                    NEW
npc          ← stats, status, items, level, world, pathfind, fov
game         ← everything above
```

`loot.py` must not import `game`, `npc`, `combat` or `render`.

---

## §11 v6 — error conventions (additions)

| Situation | Behaviour |
|---|---|
| `equip` an item not in `carried` | `ValueError` |
| `add` to a full pack | `(inventory, False)`, nothing changed |
| `drop` with an out-of-range index | `(inventory, None)`, nothing changed |
| `resolve_attack` with `Resistance.IMMUNE` | `damage == 0`, `hit` still `True`, floor not applied |
| `ranged_block_chance` for any inputs | clamped to 5–75, never raises |
| `place_chest` when no legal cell exists | `None` |
| `chest_chance(depth)` for any depth ≥ 1 | 12 below `DEEP_FROM`, 8 at or above |
| `PICK_UP` on a cell with no chest | `NOTHING_TO_PICK_UP`, no turn |
| bare-handed `f` | refusal, no turn |

All v1–v5 rows still apply.

---

## §27.4 (amendment) — chests and monsters cannot both be placed by `loot.py`

**Issued after T34 reported it. This corrects a contradiction in this contract.**

§27.2 requires that a chest is "not on a cell holding a monster", but the binding signature in
§27 is `place_chest(rng, level, depth)` — no monster list — and §10 v6 forbids `loot.py` from
importing `npc.py`. The rule as written is unsatisfiable by the module that owns it.

**Binding correction: the constraint moves to the caller.** `loot.py` places a chest knowing
only the terrain, and **`game.py` places monsters after chests, treating an occupied chest cell
the way it treats any other occupied cell.** `game.py` already owns the occupancy invariant for
actors (CONTRACT-v5 §24.2), so this puts the whole "who is standing where" question in one
place instead of two.

A chest and a monster sharing a cell is not a crash either way — the renderer draws the monster
over the chest (T35), and `PICK_UP` reads the chest under the player. The ordering rule exists
so the *starting* state is never one where a monster is sitting on the level's only chest.

## §5.1 (amendment) — the inventory screen's selection letters

**Issued after T36 reported it. This corrects a contradiction between two sections of this
contract.**

§5 v6 says the letters **`a`–`t`** select a carried item inside the inventory screen. §7.17 says
**`e` equips and `d` drops**. Both cannot be true of one keystroke: if `d` selected the fourth
item, nothing could ever be dropped.

**Binding resolution, adopting T36's:** §7.17 wins, because it describes the screen the player
actually operates. Selection letters are

```python
ITEM_LETTERS = "abcfghijklmnopqrstuv"      # a-v, skipping d and e
```

— twenty letters for `CARRY_LIMIT` twenty items, so **the letter printed beside an item is the
key that selects it** and nothing in a full pack is unreachable.

The rejected alternative was strict `a`–`t` with `d` and `e` reserved, which leaves the fourth
and fifth items permanently unselectable. That is a worse failure: it is silent, and it only
bites once a pack is half full.

## §27.5 (amendment) — consumables have no grade, and are always in the pool

`Consumable` carries no `grade` field (§25), so the §27.1 weights cannot select one. T34's
decision, adopted: **consumables are drawn at every depth regardless of grade weighting.**
Chests are the only source of items in v6, so excluding them from any grade band would make
potions and bandages unobtainable outright.
