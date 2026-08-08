# CONTRACT v3 — multi-level dungeon, stairs, UI chrome, event messages

**Frozen once written. Workers may not edit it.** A worker that believes it is wrong reports
that and stops; the orchestrator decides.

Amends `CONTRACT.md` (v1) and `CONTRACT-v2.md`. Everything in those stays binding unless
amended here.

Decisions from `.plan/RESEARCH-v3.md`, confirmed by the user: **deeper up-stairs ascend
normally** (level 1's quits) · **stair messages trigger on stepping onto the tile** ·
**stairs share the terrain colour**.

---

## §0 amendments

### §0.8 The map is no longer at row 0 — read this

The frame gains a chrome row above the map. **Map cell `(x, y)` now renders at
`cells[y + 1][x]`.** The `+1` offset exists in exactly one place, `render_to_cells`
(§4 v3), and nowhere else. Game logic, FOV, movement and the generator are unaffected —
they never index the frame.

This is the second-most-likely thing to break in this increment, after determinism. Tests
must pin it with a non-square level and a player at `x != y`.

### §0.9 Stairs need no change to `world.py`, `fov.py`, `movement.py` or `style.py`

Because `world.is_passable` is defined via `level.is_walkable` (which reads `WALKABLE` from
`tiles.py`) and `world.is_transparent` is defined as "not `WALL` and not a closed door",
adding the two stair tiles to `WALKABLE` makes them passable and transparent **automatically**.
`style.role_for` already returns `Role.TERRAIN` for every non-door tile, so stairs inherit the
light-gray palette with no change.

**These four files must not be edited in v3.** If a worker believes one must change, it reports
and stops.

---

## §1 v3 — tiles

```python
class Tile(IntEnum):
    WALL        = 0
    FLOOR       = 1
    DOOR        = 2
    STAIRS_UP   = 3
    STAIRS_DOWN = 4

TILE_CHARS: dict[Tile, str] = {
    Tile.WALL: "#", Tile.FLOOR: ".", Tile.DOOR: "+",
    Tile.STAIRS_UP: "<", Tile.STAIRS_DOWN: ">",
}
WALKABLE: frozenset[Tile] = frozenset(
    {Tile.FLOOR, Tile.DOOR, Tile.STAIRS_UP, Tile.STAIRS_DOWN}
)
STAIRS: frozenset[Tile] = frozenset({Tile.STAIRS_UP, Tile.STAIRS_DOWN})
DOOR_OPEN_CHAR: str = "'"        # unchanged
PLAYER_CHAR: str = "@"           # unchanged
```

Both stair tiles are walkable. `TILE_CHARS[Tile.DOOR]` stays `"+"`.

Two existing assertions in `tests/test_level.py` compare `TILE_CHARS` and `WALKABLE` for exact
equality and **must be updated** by the task that owns them — not deleted, extended.

---

## §2 v3 — `Level` gains stair fields

Three fields are **appended with defaults**, so every existing positional construction keeps
working:

```python
@dataclass(frozen=True)
class Level:
    width: int
    height: int
    grid: tuple[tuple[Tile, ...], ...]
    rooms: tuple[Room, ...]
    player_start: tuple[int, int]
    seed: int
    stairs_up: tuple[int, int] | None = None
    stairs_down: tuple[tuple[int, int], ...] = ()
    depth: int = 1
```

- `stairs_up` — where the up-staircase is, or `None` for a hand-built test level with no
  stairs.
- `stairs_down` — **a tuple, not a single coordinate.** Ships with exactly one entry; the tuple
  shape *is* the branching scaffolding (req 3). Nothing else needs to change when a second
  appears.
- `depth` — 1-based.

`__post_init__` additionally raises `ValueError` if: `depth < 1`; `stairs_up` is not `None` and
not in bounds; any `stairs_down` entry is not in bounds. It does **not** require stairs to
exist, so degenerate test levels stay constructible.

---

## §3 v3 — generator

```python
def generate_level(
    seed: int,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    max_rooms: int = 12,
    depth: int = 1,
    required_up: tuple[int, int] | None = None,
) -> Level: ...
```

### §3.1 Definitions

An **open spot** is a walkable, non-door cell all eight of whose neighbours are non-`WALL`.
Out-of-bounds neighbours count as `WALL`. This is requirement 1's "at least 1 tile away from
any wall", and it restricts spots to room interiors — corridors are one tile wide.

### §3.2 The anchor rule (requirements 7, 8, 9)

When `required_up` is given, the generator **places a room containing that coordinate in its
interior before any other room**, then places the rest by the existing rejection sampling. No
room is carved, reshaped or repaired after the fact.

*Measured over 150 pairs: the anchor lands as a valid open spot 150/150, with zero connectivity
failures and rooms/level falling 10.87 → 9.71.* The coordinate is always placeable because an
open spot satisfies `2 ≤ x ≤ width - 3` (measured minimum: exactly 2).

**Requirements 8 and 9 are therefore not implemented.** No room changes shape and no door is
orphaned, so there is nothing to repair. Do not write repair code "just in case".

### §3.3 New guarantees

All of G1–G12 (v1) and G9a–d / G4a (v2) still hold, with one amendment:

- **G7 amended.** Every cell inside a room's floor rect is `FLOOR` **or a stair tile**.

New:

- **G13** Both stair cells are **open spots** (§3.1).
- **G14** If `required_up` is given, `level.stairs_up == required_up`.
- **G15** `len(level.stairs_down) == 1`. (The field is a tuple for future branching; the
  generator ships exactly one.)
- **G16** `stairs_up != stairs_down[0]`, and when `len(rooms) > 1` the two sit in **different
  rooms**, so the player must cross the level. *Measured: every level has ≥7 rooms able to host
  a stair.*
- **G17** `level.player_start == level.stairs_up` — the spawn *is* the up-staircase (req 2).
- **G18** The grid contains exactly one `STAIRS_UP` cell, at `stairs_up`, and exactly one
  `STAIRS_DOWN` cell, at `stairs_down[0]`.
- **G19** `level.depth == depth`.
- **G20** The up-stair position is chosen from the open spots using the level's own
  `random.Random(seed)` when `required_up` is `None` (req 1: seed-determined, not a fixed
  corner). It must not simply be `rooms[0].center`.

Determinism (G1/G2) is unchanged and remains the hardest constraint: one local
`random.Random(seed)`, no module-level `random.*`.

### §3.4 Errors

v1 §3.1 unchanged, plus: `depth` not an `int` or `< 1` → `ValueError`; `required_up` not
`None` and not a 2-tuple of `int` → `TypeError`; `required_up` out of bounds or too close to
the border to anchor a room (outside `2 ≤ x ≤ width-3`, `2 ≤ y ≤ height-3`) → `ValueError`.

---

## §4 v3 — renderer chrome

```python
@dataclass(frozen=True)
class Chrome:
    stats: str = ""            # top row — reserved, blank for now
    message: str = ""          # bottom row, left
    status_right: str = ""     # bottom row, right

def render_to_cells(
    level: Level,
    player_pos: tuple[int, int],
    visible: frozenset[tuple[int, int]],
    explored: frozenset[tuple[int, int]],
    open_doors: frozenset[tuple[int, int]],
    chrome: Chrome,
) -> list[list[Cell]]: ...
```

`Chrome` replaces the bare `status: str` parameter. `Cell`, `to_lines`, `init_colors` and
`draw` are otherwise unchanged.

### §4.1 v3 Frame layout

Exactly **`level.height + 2`** rows, each exactly `level.width` cells:

| Row | Content |
|---|---|
| `0` | `chrome.stats`, padded/truncated to width |
| `1 … level.height` | the map — **cell `(x, y)` is at `cells[y + 1][x]`** (§0.8) |
| `level.height + 1` | `chrome.message` left-aligned, `chrome.status_right` right-aligned |

On a default 80×22 level that is **24 rows — exactly a classic terminal**, with the existing
clip guard handling anything shorter.

Both chrome rows are `Role.TERRAIN`, `Visibility.VISIBLE` in every cell.

### §4.2 v3 Status row composition

`message` and `status_right` share one row and must never overlap:

- If `len(message) + 1 + len(status_right) <= width`, place `status_right` flush right and
  `message` flush left, spaces between.
- Otherwise truncate `message` to `width - len(status_right) - 1` (never below 0).
  **`status_right` always wins** — the level and seed must stay readable.
- If `len(status_right) >= width`, truncate `status_right` to `width` and drop `message`.

Everything else in v2 §4 is retained: unexplored cells are blank, `visible` beats `explored`,
the door glyph switches on `open_doors`, an out-of-bounds player is not drawn, `draw` is the
only `(y, x)` site, and the pure functions never touch curses.

---

## §5 v3 — input

`CommandKind` gains two members; `Command` is unchanged.

```python
class CommandKind(Enum):
    MOVE = auto(); QUIT = auto(); UNKNOWN = auto()
    DESCEND = auto()      # ">"
    ASCEND  = auto()      # "<"
```

| Intent | Keys |
|---|---|
| Descend | `>` |
| Ascend | `<` |

Stairs are used by an **explicit command**, as in ADOM — not by stepping on them. The v2
binding table is otherwise unchanged; `DESCEND`/`ASCEND` carry `dx == dy == 0`.

---

## §16 (new) — events and messages: `roguelike/events.py`

Game logic emits **structured events**; wording lives in one table. Adding an event later is
one enum member, one table entry, one emission.

```python
class EventKind(Enum):
    DOOR_OPENED      = auto()
    STAIRS_HERE_UP   = auto()
    STAIRS_HERE_DOWN = auto()
    DESCENDED        = auto()
    ASCENDED         = auto()
    LEFT_DUNGEON     = auto()
    NO_STAIRS_DOWN   = auto()
    NO_STAIRS_UP     = auto()

@dataclass(frozen=True)
class Event:
    kind: EventKind
    depth: int | None = None

MESSAGES: dict[EventKind, str]

def message_for(events: Sequence[Event]) -> str: ...
```

### §16.1 Wording — binding

| Kind | Message |
|---|---|
| `DOOR_OPENED` | `The door opens.` |
| `STAIRS_HERE_UP` | `There is a staircase leading up here.` |
| `STAIRS_HERE_DOWN` | `There is a staircase leading down here.` |
| `DESCENDED` | `You descend to level {depth}.` |
| `ASCENDED` | `You climb up to level {depth}.` |
| `LEFT_DUNGEON` | `You climb out of the dungeon and give up. Farewell.` |
| `NO_STAIRS_DOWN` | `There are no stairs leading down here.` |
| `NO_STAIRS_UP` | `There are no stairs leading up here.` |

`{depth}` is filled from `Event.depth`; a kind whose template needs `depth` and receives `None`
raises `ValueError`. `message_for` joins multiple events with a single space in emission order
and returns `""` for an empty sequence. It never raises for an empty input and never truncates
— truncation is the renderer's job (§4.2).

There is deliberately **no** "you bump into a wall" event: it would fire on every misstep.

Imports: stdlib only. **No project imports at all** — `events.py` is a leaf.

---

## §17 (new) — depth and seed derivation: `roguelike/dungeon.py`

```python
def seed_for(master_seed: int, depth: int, branch: int = 0) -> int: ...

def level_for(
    master_seed: int,
    depth: int,
    required_up: tuple[int, int] | None = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> Level: ...
```

- `seed_for` is a pure integer mix, deterministic across processes, returning a non-negative
  `int`:
  `(master_seed * 0x9E3779B1 + depth * 0x85EBCA77 + branch * 0xC2B2AE35) & 0x7FFFFFFF`.
  The `branch` parameter is the scaffolding for req 3 and is always `0` today.
- `level_for` calls `generate_level(seed_for(master_seed, depth), width, height, depth=depth,
  required_up=required_up)`.
- Both pure, no curses, no caching. `depth < 1` → `ValueError`.

Imports: `roguelike.generator`, `roguelike.level`. Nothing else.

---

## §7 v3 — game state, descent and ascent

```python
@dataclass(frozen=True)
class LevelState:
    level: Level
    explored: frozenset[tuple[int, int]]
    open_doors: frozenset[tuple[int, int]]

@dataclass(frozen=True)
class GameState:
    master_seed: int
    depth: int
    level: Level
    player: tuple[int, int]
    explored: frozenset[tuple[int, int]]
    visible: frozenset[tuple[int, int]]
    open_doors: frozenset[tuple[int, int]]
    saved: dict[int, LevelState]
    turns: int = 0
    running: bool = True
    radius: int = fov.DEFAULT_RADIUS
    events: tuple[Event, ...] = ()
    outcome: str | None = None

def new_game(master_seed: int, width: int = 80, height: int = 22,
             radius: int = fov.DEFAULT_RADIUS) -> GameState: ...
def step(state: GameState, command: Command) -> GameState: ...
def format_stats(state: GameState) -> str: ...
def format_status_right(state: GameState) -> str: ...
def run(stdscr, state: GameState) -> GameState: ...
def play(seed: int, width: int = 80, height: int = 22) -> None: ...
```

`saved` maps depth → the state of a level the player has left. It is **never mutated**;
transitions build a new dict (`{**old, d: v}`). It is the only non-frozen-typed field and
exists because ascent must restore fog and open doors.

`new_game` now takes a **master seed**, not a `Level` — it generates level 1 itself via
`dungeon.level_for`, places the player on `stairs_up`, and computes the opening FOV.

### §7.1 `step` — pure, and the event rule

`step` returns a new `GameState`, never mutates the input, never touches curses, never draws.

**The event rule:** the returned state's `events` is the tuple produced by *this* command
whenever the command produced any event or consumed a turn. Otherwise the state is returned
**unchanged**, so the previous message stays on screen. That is requirement "stays until
another turn", and it falls out of the existing turn rule rather than needing a new one.

| Command | Behaviour |
|---|---|
| `running=False` | returned unchanged, every kind |
| `QUIT` | `running=False`; nothing else changes; no event |
| `UNKNOWN` | unchanged, **including `events`** |
| `MOVE` accepted | new position, `turns + 1`, recompute FOV, `explored \|= visible`; then if the new cell is a stair tile emit `STAIRS_HERE_UP`/`STAIRS_HERE_DOWN` |
| `MOVE` into closed door | bump-to-open exactly as v2, `turns + 1`, recompute FOV, emit `DOOR_OPENED` |
| `MOVE` blocked otherwise | unchanged, **turns and events untouched** |
| `DESCEND` on a `STAIRS_DOWN` cell | descend (below), `turns + 1`, emit `DESCENDED(depth=new)` |
| `DESCEND` elsewhere | emit `NO_STAIRS_DOWN`, **no turn**, nothing else changes |
| `ASCEND` on `STAIRS_UP`, `depth == 1` | `running=False`, `outcome` set, emit `LEFT_DUNGEON` |
| `ASCEND` on `STAIRS_UP`, `depth > 1` | ascend (below), `turns + 1`, emit `ASCENDED(depth=new)` |
| `ASCEND` elsewhere | emit `NO_STAIRS_UP`, **no turn**, nothing else changes |

**Descend.** Save the current level into `saved[depth]`. Take `target = level.stairs_down[0]`.
If `saved` already holds `depth + 1`, restore it; otherwise build it with
`dungeon.level_for(master_seed, depth + 1, required_up=target)`. Place the player at `target`
— which is the new level's `stairs_up` by G14 — recompute FOV against the new level's
`open_doors`, and union into that level's `explored`.

**Ascend.** Save the current level. Restore `saved[depth - 1]` (it always exists: you can only
be at depth *d* by having descended through *d-1*). Place the player on that level's
`stairs_down[0]` — the staircase they came down. Recompute FOV and union `explored`.

**Fog and doors are per level.** `explored`, `visible` and `open_doors` on `GameState` always
describe the *current* depth; other depths live in `saved`.

### §7.2 Chrome text

- `format_stats(state)` → `""`. The row is reserved (req 1); nothing is invented to fill it.
- `format_status_right(state)` → `f"Level {depth}  Seed {master_seed}"` — two spaces, master
  seed not the per-level derived seed, so the player can replay the run.
- The message half is `events.message_for(state.events)`; `game.py` does not format wording.
- v2's `format_status` is **removed**; nothing outside `game.py` used it.

### §7.3 The loop and game over

`run(stdscr, state)` calls `render.init_colors()` once, then loops
`render_to_cells(...)` → `draw` → `getch` → `translate_key` → `step` until `not running`, and
**returns the final `GameState`**.

`play` calls `curses.wrapper(run, new_game(...))` and, after the terminal is restored, prints
`state.outcome` when it is set. That is requirement 2's "clear message" — printed on a sane
screen, not into a torn-down curses window.

---

## §9 v3 — file ownership

| Path | Owner |
|---|---|
| `roguelike/tiles.py`, `roguelike/level.py`, `tests/test_level.py` | **T13** |
| `roguelike/events.py`, `tests/test_events.py`, `roguelike/keys.py`, `tests/test_keys.py` | **T14** |
| `roguelike/generator.py`, `tests/test_generator.py` | **T15** |
| `roguelike/render.py`, `tests/test_render.py` | **T16** |
| `roguelike/game.py`, `roguelike/dungeon.py`, `tests/test_game.py`, `tests/test_dungeon.py` | **T17** |
| `roguelike/world.py`, `roguelike/style.py`, `roguelike/fov.py`, `roguelike/movement.py` and their tests | **frozen — nobody may edit** (§0.9) |
| `main.py`, `tests/test_integration.py`, `.plan/**` | orchestrator |

---

## §10 v3 — import graph, still acyclic

```
tiles.py      ← (nothing)
events.py     ← (nothing)                       NEW, leaf
keys.py       ← (nothing but curses + stdlib)
level.py      ← tiles
world.py      ← tiles, level                    unchanged
style.py      ← tiles                           unchanged
generator.py  ← tiles, level
fov.py        ← level, world                    unchanged
movement.py   ← level, world                    unchanged
render.py     ← tiles, level, style
dungeon.py    ← generator, level                NEW
game.py       ← level, keys, movement, render, fov, world, dungeon, events
main.py       ← game
```

`render.py` must **not** import `events`, `fov`, `game`, `generator`, `movement` or `keys` — it
receives finished strings in `Chrome`. `events.py` imports nothing from the project.

---

## §11 v3 — error conventions (additions)

| Situation | Behaviour |
|---|---|
| `generate_level` `depth < 1` or non-int | `ValueError` / `TypeError` |
| `generate_level` `required_up` out of anchorable range | `ValueError` |
| `Level` with `depth < 1` or out-of-bounds stair | `ValueError` |
| `seed_for` / `level_for` with `depth < 1` | `ValueError` |
| `message_for(())` | `""`, never raises |
| `MESSAGES` template needs `depth` but event has `None` | `ValueError` |
| `DESCEND`/`ASCEND` when not on the matching stair | event emitted, **no turn**, no state change |
| `ASCEND` on `STAIRS_UP` at depth 1 | game ends with `outcome` set |

All v1 and v2 rows still apply.
