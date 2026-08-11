# RESEARCH — v6: inventory, weapons, shields, consumables and chests

Phase 0 recon. **No code written, no workers spawned.** Covers the five research items:
inventory management · wielding varied weapons and damage types · shields · healing items ·
chests with depth-scaled loot. They are one increment because they share one seam — the
player finally *carries* things — and splitting them would mean designing that seam twice.

> **Method note, and it is the whole reason to trust these numbers.** RESEARCH-v5's balance
> sweep reimplemented `max_hp` and got it wrong: it passed the HP multiplier for the player
> but let monsters keep the function's default, modelling them at half their real hit points.
> The published "61.5% of floors cleared" was actually **2.2%**, and that shipped. **Every
> figure below is measured by importing the shipped modules** — `stats.derive`,
> `combat.resolve_attack`, `npc.SPECIES_DATA`, `items.DAGGER` — and never by restating a
> formula. Where a number is a *proposal* rather than a measurement it says so.

---

## 0. The finding that constrains four of the five features

**Flat damage reduction saturates almost immediately against this bestiary**, because monster
damage is 1–4 and the `max(1, …)` floor is one point.

Share of each species' damage rolls that get floored to 1, by the defender's block:

| block | rat | jackal | giant bat | cave snake | mean |
|---|---|---|---|---|---|
| 0 | 33% | 0% | 50% | 0% | **21%** |
| 1 | 67% | 33% | 100% | 33% | **58%** |
| 2 | 100% | 67% | 100% | 67% | **83%** |
| 3 | 100% | 100% | 100% | 100% | **100%** |

And in whole fights against a jackal (2000 seeds each, driven through the real
`combat.to_hit_chance`):

| extra block | player wins | HP left on average |
|---|---|---|
| +0 | 97.1% | 17.2 |
| +1 | 100.0% | 26.0 |
| +2 | 100.0% | 32.3 |
| +3 | 100.0% | 35.5 |

**A shield worth +1 flat block already ends the fight at 100% and nearly doubles the health
you walk away with.** This is the same failure mode as the original v5 `block = STR//2` bug,
which floored *every attack in the game* to 1 damage — it is not a coincidence, it is what
flat reduction does when the numbers are this small.

**Consequence for v6:** shields must not grant flat block, and neither may armour if it ever
arrives. §3 proposes the alternative. It also caps how much weapon variety is meaningful —
§2.

---

## 1. Inventory

### What exists

`items.py` is deliberately minimal: a frozen `Weapon`, two module constants, no container, no
ammunition, no `Item` base class. `Player` holds `melee` and `ranged` as two fields. Nothing
generates, drops or varies an item.

### The seam

```python
@dataclass(frozen=True)
class Item:                       # the common shape
    name: str
    glyph: str
    kind: ItemKind                # WEAPON | SHIELD | CONSUMABLE

@dataclass(frozen=True)
class Inventory:
    carried: tuple[Item, ...] = ()
    melee: Weapon | None = None
    ranged: Weapon | None = None
    shield: Shield | None = None
```

Three design points, each with a reason:

- **`Inventory` is its own frozen value, not three more fields on `Player`.** Equipping is
  then one function `equip(inventory, item) -> Inventory` that the turn loop calls, and
  `Player` does not grow a slot every time an item type is added.
- **Slots are `| None`.** Fighting bare-handed has to be representable, or dropping your only
  dagger is unimplementable. A `None` melee slot means an unarmed attack (proposed 1–2
  damage, `strength_applies=True`).
- **No weight, no encumbrance, no stacking.** None were asked for, and each is a system with
  its own balance surface. A carried tuple with a cap (proposed **20 items**) is enough to
  make "choose what to wield" a real choice without inventing a second economy.

### The UI question, and why it is the expensive part

Everything else here is data. The *screen* is the work: a list, a cursor, "wield this / drink
this / drop this". The project has one paginated full-screen view already —
`render.render_text_page`, built for the help screen — and it takes finished lines and a
`Chrome`, which is exactly what an inventory screen is. **Reusing it is the recommendation**;
the alternative is a second full-screen layout that has to learn the same clipping rules.

Keys — **`i` for the inventory screen is confirmed by the user**; `e` (equip) and `d` (drop)
are proposed and verified unbound. `w` and `q` are already taken by walk and quit.

---

## 2. Weapons and damage types

### Measured window

A baseline player has **45 HP, block 0**, and the dagger does 2–5. Against the bestiary:

| species | HP | dagger hits to kill |
|---|---|---|
| rat | 17 | 4.9 |
| jackal | 25 | 7.1 |
| giant bat | 13 | 3.7 |
| cave snake | 25 | 7.1 |

Seven hits for a jackal is already slow. **A weapon tier much below the dagger is not worth
carrying, and one much above it trivialises the bestiary** — the usable band is roughly 1–4
at the bottom and 4–9 at the top before a jackal dies in two hits.

Proposed tiers (three, not five — the band does not support five):

| tier | melee example | damage | ranged example | damage |
|---|---|---|---|---|
| crude | club | 1–4 | sling | 1–3 |
| standard | **dagger 2–5** (unchanged) | 2–5 | **shortbow 1–4** (unchanged) | 1–4 |
| fine | sword | 4–8 | longbow | 3–6 |

### Damage types — **decided: full resistances** (§7.1)

`DamageType` (SLASH / PIERCE / BLUNT) on `Weapon`, and a per-species resistance table that
actually changes damage. The user chose this over flavour-only, accepting §0's saturation
cost on the current bestiary in exchange for a mechanic that keeps scaling.

**What still needs sweeping before the contract freezes it:** a resistance expressed as a
percentage runs into §0's arithmetic from the other side — 50% off a 1–4 roll is 0–2, and the
`max(1, …)` floor eats most of it. Two shapes to measure:

* **percentage** — simple, but saturates against small numbers exactly as flat block does;
* **a damage multiplier applied before the floor**, with resistances kept coarse (immune /
  resistant / normal / vulnerable) rather than arbitrary percentages.

The second is the recommendation to *test first*, not to adopt untested. Either way the
bestiary needs at least one species that resists something, or the mechanic ships dead —
proposed: the cave snake resists PIERCE (arrows slide off scales), the giant bat is
vulnerable to BLUNT.

---

## 3. Shields — and why they cannot be flat block

§0 is decisive: +1 flat block wins 100% of jackal fights. So a shield must reduce damage
*sometimes*, not *always*.

**Proposal: a shield is a chance to negate an incoming hit entirely.**

```python
@dataclass(frozen=True)
class Shield:
    name: str
    glyph: str
    block_chance: int      # percent, rolled once per incoming attack
```

- Rolled after the attacker hits and before damage — a fourth draw in
  `combat.resolve_attack`'s fixed order, which the contract already pins.
- `AttackResult` gains `blocked: bool`, and `game.py` words it.
- Proposed values: buckler 15%, kite shield 25%, tower shield 35%.

Why this shape:

- **It does not saturate.** A 25% shield removes 25% of incoming damage on average whatever
  the damage numbers are, so it stays meaningful if the bestiary ever hits harder — flat
  reduction does not.
- **It is visible.** "Your shield turns the blow." is a message; "you took 2 instead of 3" is
  arithmetic nobody sees.
- **It composes with `Condition`.** A blocked hit is a hit that did not move your health band,
  which is the thing the player and the fleeing AI both read.

**Unverified:** 15/25/35 are proposals, not measurements. They should be swept the way §0's
table was swept before the contract freezes them.

---

## 4. Healing items

### Measured worth

Regeneration is 1 HP per 3 turns, and a baseline bar is 45 HP.

| potion | HP | equals this much resting | share of a full bar |
|---|---|---|---|
| small | 5 | 15 turns | 11% |
| standard | 10 | 30 turns | 22% |
| strong | 20 | 60 turns | 44% |

Since `R` already rests to full and costs only turns, **a healing item's value is not the HP —
it is the HP obtained without spending turns.** That reframes the whole category: potions
matter in a fight, and are nearly worthless outside one. Which is correct, and worth stating
in the contract so nobody "fixes" it by making them stronger.

Three kinds, deliberately different in *when* they are good rather than in size:

| item | effect | why it is distinct |
|---|---|---|
| **potion of healing** | +10 HP instantly, one turn | the emergency; the only one worth drinking mid-fight |
| **bandage** | +15 HP over 5 turns (a regeneration status effect) | cheap, but you must disengage — pairs with fleeing monsters |
| **food ration** | cures nothing, but see below | — |

**Food is the one that does not fit, and I recommend against it.** There is no hunger clock in
this game. A ration with nothing to cure is an item that exists to be ignored. Adding hunger
is a whole subsystem — a timer, a starvation death path, and a balance pass against `R` which
currently rests free of charge for 100+ turns. **Either add hunger deliberately as its own
increment, or drop food.** Recommendation: drop it from v6 and decide hunger separately.

**Bandage-as-status is worth noting as a design win**: `status.py` already has the exact
shape — a kind, a duration, a magnitude, ticked once per world-tick. A regeneration effect is
`POISONED` with the sign flipped, and it makes `StatusKind` finally hold something that is not
a punishment.

---

## 5. Chests and loot

### Frequency

A level has 6 monsters and ~180 turns of exploring.

| chance per level | a chest every … |
|---|---|
| 5% | 20 levels |
| 10% | 10 levels |
| 15% | 6.7 levels |
| 25% | 4 levels |

"Very low" was the requirement. **Recommendation: 12% per level, scaling to a floor of 8% by
depth 10** — roughly one chest per eight levels early, rarer later, so a chest stays an event
rather than a supply line.

### Depth scaling and "extremely low chance of higher grade"

Proposed, as a table rather than a formula, so it can be read and tuned:

| depth | crude | standard | fine |
|---|---|---|---|
| 1–3 | 80% | 19% | **1%** |
| 4–6 | 55% | 40% | **5%** |
| 7–9 | 30% | 60% | **10%** |
| 10+ | 15% | 70% | **15%** |

A table beats a formula here for the same reason the bestiary is a table: someone will want to
tune one row without re-deriving a curve.

### Where a chest lives

- A new `Tile.CHEST`, or an entity list? **Recommendation: an entity list.** `Tile` is frozen
  terrain the generator produces and `Level` is immutable — an opened chest changes, and
  terrain cannot. It would be the `open_doors` problem again, and that was solved by keeping
  mutable state *outside* `Level` (CONTRACT-v2 §0.6). Chests follow that precedent:
  `LevelState.chests`, alongside `npcs`.
- Placement uses the level's own seeded `Random` at generation, like monsters, with the same
  no-spawn radius around `player_start`.
- Opening is a bump, like a door — or an explicit command. **Bump is wrong here**: bumping a
  chest you meant to walk past would open it, and unlike a door there is no way to close it.
  Recommendation: opening is deliberate.

---

## 6. What this touches

| Module | Change |
|---|---|
| `items.py` | `Item`, `ItemKind`, `Shield`, `Consumable`, `Inventory`, the tier tables |
| `status.py` | a regeneration effect kind (bandages) |
| `combat.py` | one more roll: the shield's negate chance; `AttackResult.blocked` |
| `game.py` | inventory state, equip/use/drop commands, chest opening, the inventory screen's lines |
| `render.py` | chest glyph; the inventory screen reuses `render_text_page` |
| `keys.py`, `events.py` | new commands and wording |
| `generator.py` / `dungeon.py` | **untouched** — chests are placed by the game, not the generator, exactly as monsters are |

---

## 7. Decisions — all four settled by the user

1. **Damage types: full resistances, not flavour.** The user's reasoning, recorded because it
   overrides §0's caution deliberately rather than by oversight: *resistance will not saturate
   against stronger foes, and being untouchable to a jackal with a good shield makes sense.*
   That is a design position, not a miss — it accepts that a good shield trivialises **the
   current bestiary** in exchange for a mechanic that keeps working when tougher things arrive.
   **The consequence to state plainly in CONTRACT-v6: today's four species are the whole game,
   so a fine shield will make the shipped content easy until deeper foes exist.** §0's measured
   table is the evidence for how quickly that happens; it is now an accepted cost, not a
   warning.
2. **No food.** Dropped. Hunger stays un-designed and is not a v6 concern.
3. **Inventory on `i`.** Confirmed free (verified: `i`, `e`, `d`, `g`, `t` all map to
   `UNKNOWN`).
4. **Shields work against arrows, but a shot must always be able to land**, with the chance
   depending on both sides' stats. Researched below in §7.1.

### §7.1 Shields against ranged attacks — the researched formula

**Requirement:** a shield helps against arrows; the archer's and the defender's stats both
matter; **there is always a chance to be hit anyway.**

Only three primary stats exist (STR / AGI / VIT) and four derived ones. AGI is the only one
that means anything for "did the arrow get past the shield" — STR is arm strength and VIT is
constitution, and `evasion` is already spent on the to-hit roll, so reusing it would make AGI
count twice in the same exchange.

**Proposed formula:**

```
ranged_block% = clamp(shield.block_chance + (defender.AGI - attacker.AGI) * 2, 5, 75)
```

Rolled only if the attack already hit, exactly like the melee block roll, so the shield is a
second line of defence rather than a second dodge.

**Why the coefficient is 2** — swept across the three candidates:

| coeff | buckler, AGI gap −8 → +8 | behaviour |
|---|---|---|
| 1 | 7% → 23% | the gap barely matters; stats are decoration |
| **2** | **5% → 31%** | **a 4-point AGI edge is worth ~8 points of block — noticeable, not decisive** |
| 3 | 5% → 39% | the gap swamps the shield; a buckler on a fast character beats a tower shield on a slow one |

**Why the cap is 75 and the floor is 5** — the cap is precisely the "always a chance to be
hit" requirement:

| cap | an arrow still lands… | |
|---|---|---|
| 60% | 40% of the time | safe but the shield feels weak |
| **75%** | **25% of the time** | **chosen** |
| 85% | 15% of the time | thin |
| 90% | 10% of the time | fails the requirement in practice |

The floor of 5 does the same job from the other side: a slow character with a small shield
against a fast archer still blocks occasionally, so the item is never literally worthless.

**Behaviour across the AGI values that actually exist** (archers are hypothetical — nothing
shoots yet):

| defender | shield | vs AGI 18 | vs AGI 13 | vs AGI 8 |
|---|---|---|---|---|
| baseline, AGI 10 | buckler | 5% | 9% | 19% |
| baseline, AGI 10 | tower | 19% | 29% | 39% |
| levelled, AGI 14 | kite | 17% | 27% | 37% |
| agile, AGI 18 | tower | 35% | 45% | 55% |

Over twenty shots, a tower shield takes an agile character from 20 hits to about 10, and a
baseline one from 20 to 14. The shield always matters and never decides the exchange alone.

> **Honest scope note: this is untestable live in v6.** No monster has a ranged attack, and no
> monster carries a shield, so neither direction of this rule can fire in a real game. It
> ships as a tested pure function with no live caller — the same position `interruption` held
> in v4 and `ENRAGED` holds now. The alternative is to leave shields melee-only and add this
> when an archer exists; the user asked for it, so it is specified, and this note is here so
> the gap is a recorded choice.

## 8. What is measured, what is proposed, and what is still unswept

**Measured against the shipped modules** — safe to build on:

- §0's flat-block saturation table and the jackal win-rate table
- §2's dagger-hits-to-kill window
- §4's potion-versus-resting equivalence
- §7.1's shield-versus-ranged coefficient, cap and floor sweeps

**Proposals, not measurements** — nothing here is evidence:

- shield base block chances (15/25/35)
- weapon tier damage ranges, other than the two shipped weapons
- chest frequency (12%) and the depth/grade table
- potion and bandage magnitudes
- the 20-item carry cap

**Still unswept and load-bearing — do this before CONTRACT-v6 freezes anything:**

1. **The resistance shape.** §2 sets out two candidates (percentage versus coarse multiplier).
   The percentage form runs straight into §0's arithmetic and may well be unusable; this needs
   the same sweep §0 got. It is the single largest open risk in the increment, because the
   user has chosen resistances and the obvious implementation is the one most likely to break.
2. **Shield base chances against the *whole* fight**, not just per-arrow. §7.1 measured the
   per-shot maths; the end-to-end effect on floor-clear rate is unmeasured.

§0 is the cautionary example for all of this: one sweep showed the obvious shield design wins
100% of jackal fights, and that sweep cost minutes against shipping a broken mechanic.
