# RESEARCH — v3: multi-level dungeon, stairs, UI chrome, event messages

Phase 0 recon. **No code written, no workers spawned.** Every number is measured on the
current build (v2, 1300 tests green), not estimated.

---

## 1. Headline finding — requirements 8 and 9 are not needed

You anticipated a hard problem: generate a level, discover there is no room where the stair
must go, then carve one and repair the damage — letting existing rooms change shape (req 8)
and deleting doors that lose their walls (req 9).

**That problem disappears if the mandatory room is placed first.** The generator already
places rooms by rejection sampling: draw a candidate, keep it if it does not intersect an
already-placed room. Seeding that list with an *anchor room* built around the required stair
coordinate means every later room is rejected against it. Nothing is ever carved after the
fact, so no room changes shape and no door is orphaned.

Prototyped and measured over 150 level pairs:

| | Result |
|---|---|
| Anchor coordinate lands as a valid spawn cell (floor, ≥1 tile from any wall) | **150 / 150** |
| G8 connectivity failures | **0** |
| Door guarantees G9a–d (generator self-check) | **all held** |
| Rooms per level, baseline → anchored | 10.87 → **9.71** |

The cost is about one room per level. That is the entire price of the feature.

### Why the anchor can always be placed

The stair coordinate on the level above is, by requirement 1, a floor cell at least one tile
from any wall. Room floors satisfy `1 ≤ x` and `x2 ≤ width - 2` (G6), so a cell one tile
inside a room's floor satisfies `2 ≤ x ≤ width - 3`. A room whose *interior* contains that
cell therefore always fits within the map margins. **Measured minimum coordinate over 300
levels: exactly 2** — the theoretical bound, hit but never crossed.

So the anchor is always constructible, and req 7's "special ruleset" is simply: *the anchor
room is placed before all others*.

I recommend dropping requirements 8 and 9 as implementation permissions that are no longer
needed. They stay valid as fallbacks if the anchor approach ever fails, but it does not.

---

## 2. Spawn and stair placement — measured

"Walkable floor tile, at least 1 tile away from any wall" reads as: a `FLOOR` cell whose
**eight neighbours are all non-wall**. Corridors are one tile wide and doors sit in walls, so
this restricts spawns to room interiors — which is what you want.

Over 300 generated levels:

| | Result |
|---|---|
| Levels with **zero** valid spawn cells | **0** |
| Valid cells per level | mean **281.8**, min 168, max 395 |
| Rooms containing ≥1 valid cell | mean 10.8, **min 7** |

Minimum 7 rooms per level can host a stair, so **the up-stair and the down-stair can always
be placed in different rooms** — worth doing, so the player has to cross the level.

Today `player_start` is `rooms[0].center`, which is deterministic but always the same kind of
spot. Requirement 1 asks for a seed-determined position, so this becomes a uniform choice
from the valid-cell list using the level's own RNG.

---

## 3. The descent chain — prototyped end to end

Five-level chains built for 60 master seeds — **300 levels**:

| Check | Failures |
|---|---|
| Level *N+1*'s up-stair equals level *N*'s down-stair | **0** |
| Every walkable tile reachable from the up-stair | **0** |
| Both stair cells are valid spawn cells | **0** |
| Identical chain on a second run | **identical** |

Sample chain, master seed 7 — the linkage is visible:

```
L1  up (67, 12)   down (24,  7)
L2  up (24,  7)   down (48, 16)
L3  up (48, 16)   down ( 4,  8)
L4  up ( 4,  8)   down (39, 11)
L5  up (39, 11)   down (64, 16)
```

### Seed derivation per depth

Each level needs its own seed derived from the master seed and depth, deterministically.
Verified: `random.Random("1234:3")` is stable across `PYTHONHASHSEED` values (CPython hashes
string seeds with SHA-512, not `hash()`). Both work; I recommend **explicit integer mixing**:

```python
def seed_for(master: int, depth: int, branch: int = 0) -> int:
    return (master * 0x9E3779B1 + depth * 0x85EBCA77 + branch * 0xC2B2AE35) & 0x7FFFFFFF
```

It keeps `seed` a plain `int` (which CONTRACT §3.1 requires), is self-evidently deterministic,
and the `branch` term is the scaffolding for req 3.

### Branching scaffolding (req 3)

Model the level as carrying `stairs_down: tuple[Coord, ...]` rather than a single coordinate.
Ship with **exactly one** entry. When a second is eventually generated, the branch index feeds
`seed_for`, so different stairs lead to genuinely different levels. Nothing else needs to
change — the data shape is the scaffolding.

---

## 4. Level persistence

Requirement 4 says stairs behave "the same as in ADOM", and ADOM levels persist: go back up
and the level is as you left it.

Terrain is free — generation is deterministic, so regenerating from `(master, depth)` yields
the identical map. **But `explored` and `open_doors` are runtime state and would be lost.**
So a per-level store is needed regardless, and once it exists, caching the `Level` too costs
nothing.

Proposal: the game holds `dict[depth, LevelState]` where `LevelState` bundles the level, its
`explored` set and its `open_doors` set. Roughly 1760 coordinates per level worst case — a
20-level dungeon is trivial in memory.

**Ascending** is the mirror of descending: arriving on level *N-1* places the player on the
down-stair they originally used.

---

## 5. UI layout — the budget works out exactly

| | Rows |
|---|---|
| Today | map 22 + status 1 = **23** |
| v3 | stats 1 + map 22 + status 1 = **24** |

That fits a classic 80×24 terminal **exactly**, with no change to the default map height. It
does consume the last spare row, so on a 24-row terminal there is now zero slack — the
existing clip guard already handles a shorter terminal by dropping the bottom rows.

```
 row 0        player stats (reserved — blank for now)
 rows 1..22   the map
 row 23       messages (left)                       Level 3  Seed 1234 (right)
```

The status row composes two independently-aligned halves. The right half is fixed width and
wins; the message is truncated if it would collide.

---

## 6. Event and message system

### Shape

Keep game logic free of wording. `step()` produces **structured events**; a separate table
turns them into text.

```python
class EventKind(Enum):
    DOOR_OPENED   = auto()
    STAIRS_HERE_DOWN = auto()
    STAIRS_HERE_UP   = auto()
    DESCENDED     = auto()
    ASCENDED      = auto()
    LEFT_DUNGEON  = auto()     # up-stair on level 1 — ends the game

@dataclass(frozen=True)
class Event:
    kind: EventKind
    depth: int | None = None

MESSAGES: dict[EventKind, str] = { ... }          # one home for all wording
def message_for(events: tuple[Event, ...]) -> str
```

Adding an event later is: one enum member, one table entry, one `step()` emission. That is the
expandability requirement met with no framework.

### Proposed vocabulary

| Event | Message |
|---|---|
| `DOOR_OPENED` | `The door opens.` |
| `STAIRS_HERE_DOWN` | `There is a staircase leading down here.` |
| `STAIRS_HERE_UP` | `There is a staircase leading up here.` |
| `DESCENDED` | `You descend to level {depth}.` |
| `ASCENDED` | `You climb up to level {depth}.` |
| `LEFT_DUNGEON` | `You climb out of the dungeon and give up on it. Farewell.` |

Deliberately **not** included: bumping a wall. ADOM does not message it, and it would fire on
every misstep — the noisiest possible message.

### Persistence rule — "until another turn"

Carry `events: tuple[Event, ...]` on the game state. They are **replaced on every
turn-consuming action** and left untouched otherwise. Because a rejected move consumes no
turn, walking into a wall leaves the previous message on screen — which is exactly the stated
behaviour, and it falls straight out of the existing turn rule rather than needing a new one.

If one turn produces several events (stepping onto a stair, say, emits a move *and* a "stairs
here"), they are joined in emission order and truncated to fit.

---

## 7. Contract impact — this is the largest increment so far

### Tile vocabulary is no longer three tiles

`Tile` gains `STAIRS_UP` (`<`) and `STAIRS_DOWN` (`>`), following the genre convention. Both
are walkable and transparent. This touches `tiles.py`, `world.py`, `style.py`, `render.py`,
`fov.py` and the generator.

### `Level` gains stair fields

`stairs_up: tuple[int, int]` and `stairs_down: tuple[tuple[int, int], ...]`. Still frozen.

### `generate_level` signature changes

It needs the depth and, for depth > 1, the required up-stair coordinate:

```python
def generate_level(seed, width=80, height=22, max_rooms=12,
                   required_up: tuple[int, int] | None = None) -> Level
```

New guarantees: the up-stair is at `required_up` when given; both stair cells are valid spawn
cells; stairs sit in different rooms when the level has more than one.

### Provisional layout

| File | Change |
|---|---|
| `roguelike/events.py` | **new** — `EventKind`, `Event`, message table |
| `roguelike/dungeon.py` | **new** — depth ↔ level store, seed derivation, descend/ascend |
| `roguelike/tiles.py` | amend — two stair tiles |
| `roguelike/level.py` | amend — stair fields (**first change since v1**) |
| `roguelike/generator.py` | amend — anchor room, spawn choice, stair placement |
| `roguelike/style.py` | amend — stairs role and colour |
| `roguelike/world.py` | amend — stairs passable and transparent |
| `roguelike/render.py` | amend — two chrome rows, status composition |
| `roguelike/game.py` | amend — depth, events, descend/ascend, game-over |

Roughly **6–7 tasks in 3 waves**.

---

## 8. Open questions — ANSWERED

| # | Question | Decision |
|---|---|---|
| 1 | Up-stair below level 1 | **Ascends normally.** Level 1's up-stair quits; on level 2+ an up-stair returns you to the previous level, arriving on the down-stair you came from |
| 2 | "Pass by stairs" trigger | **Stepping onto the stair tile** — quiet and conventional |
| 3 | Stair colour | **Same light gray as walls and floor** (250 visible / 238 explored) |

### Consequence of decision 3 — one fewer file to touch

`style.role_for` already returns `Role.TERRAIN` for every tile that is not a door:

```python
if tile is Tile.DOOR:
    return Role.DOOR
return Role.TERRAIN
```

So the two new stair tiles inherit `Role.TERRAIN` and the light-gray palette **automatically**.
`style.py` needs no change at all, and neither does the palette. Stairs are found by their
glyph (`<`, `>`), which is what the genre does anyway.

### Consequence of decision 1 — persistence is now required, not optional

Ascending means returning to a level you have already explored, so the per-level store in §4
is load-bearing rather than a nicety: `explored` and `open_doors` must survive the round trip
or the fog would reset every time you climb a staircase.

### Consequence of decision 2 — no new state needed

Triggering on the stair tile itself means the event is derived from the player's position at
the end of a turn. No "seen stairs" set, no discovery tracking, nothing extra on the state.

---

## 9. Revised module layout

| File | Change |
|---|---|
| `roguelike/events.py` | **new** — `EventKind`, `Event`, message table |
| `roguelike/dungeon.py` | **new** — depth ↔ level store, seed derivation, descend/ascend |
| `roguelike/tiles.py` | amend — `STAIRS_UP` `<`, `STAIRS_DOWN` `>` |
| `roguelike/level.py` | amend — `stairs_up`, `stairs_down` (first change since v1) |
| `roguelike/generator.py` | amend — anchor room, seed-chosen spawn, stair placement |
| `roguelike/world.py` | amend — stairs passable and transparent |
| `roguelike/render.py` | amend — stats row, status row composition |
| `roguelike/game.py` | amend — depth, events, descend/ascend, game over |
| `roguelike/style.py` | **no change** — stairs inherit `Role.TERRAIN` |
| `roguelike/keys.py`, `movement.py`, `fov.py` | **no change** |

`fov.py` needs nothing because it asks `world.is_transparent`, and `movement.py` needs nothing
because it asks `world.is_passable` — both rules have exactly one home, so making stairs
walkable and see-through is a change in `world.py` alone. That is the §13 seam from v2 paying
for itself.
