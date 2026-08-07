# CONTRACT v2 — amendments for colours, fog of war, visibility, door state

**Frozen once written. Workers may not edit it.** A worker that believes it is wrong reports
that and stops; the orchestrator decides.

This document **amends** `CONTRACT.md` (v1). Everything in v1 stays binding unless amended
here. v1 §0 (conventions, `(x, y)`, determinism, immutability), §1 (tiles), §2 (`Room`,
`Level`), §5 (keys), §9 (ownership), §10 (imports), §12 (testing) all still apply as written
except where a section below says otherwise.

Decisions recorded in `.plan/RESEARCH-v2.md` and confirmed by the user:
turn-based FOV per move · doors opaque with **open/closed state** · colours list ends at 6 ·
door fix **B (reroute)**.

Two v1 BRIEF non-goals — "field of view / lighting" and "colour beyond basic terminal
defaults" — are **deliberately reversed** by this increment. Everything else in that non-goals
list still stands: no monsters, combat, items, inventory, save-load, multiple levels, stairs,
or sound.

---

## §0 amendments

### §0.6 The passability invariant has changed — read this

In v1, **walkability was a pure function of `Level`**. That is no longer true. A closed door is
terrain-walkable but currently impassable, and its state changes during play.

- `Level` stays **frozen** and **terrain-only**. Door open/closed state is **never** stored in
  `Level`, and `Level.is_walkable` keeps its exact v1 meaning: *terrain* walkability, with a
  `DOOR` cell always counting as walkable terrain.
- Runtime passability and transparency live in the new **§13 `world.py`**, and every consumer
  goes through it.
- The mutable set of open doors lives in `GameState` (§7) and is threaded explicitly as a
  parameter. No module reaches for it globally.

### §0.7 Sets are frozen and order-independent

`visible`, `explored` and `open_doors` are all `frozenset[tuple[int, int]]`. No function's
*output* may depend on iteration order of a set (v1 §0.4 determinism still binds).

---

## §1 amendment — tiles

`roguelike/tiles.py` gains exactly one name:

```python
DOOR_OPEN_CHAR: str = "'"
```

`TILE_CHARS[Tile.DOOR]` remains `"+"` and now means **closed** door. The glyph for an open door
is chosen at render time from `open_doors`; **`Tile` gains no new member.** The grid keeps a
single `Tile.DOOR`, so the generator, `Level` equality, and every v1 determinism test are
untouched.

`Tile` is still exactly `WALL`, `FLOOR`, `DOOR`.

---

## §3 amendment — generator, door constraint (G9 tightened)

**G9 is replaced.** Every `Tile.DOOR` cell must satisfy all of:

- **G9a** It lies on some room's `on_perimeter` (unchanged from v1).
- **G9b** It is **embedded in a wall run**: either both `(x-1, y)` and `(x+1, y)` are
  `Tile.WALL`, or both `(x, y-1)` and `(x, y+1)` are `Tile.WALL`.
- **G9c** The perpendicular axis is **passage**: if G9b matched vertically (walls above and
  below), then `(x-1, y)` and `(x+1, y)` are both non-`WALL`; if it matched horizontally, then
  `(x, y-1)` and `(x, y+1)` are both non-`WALL`.
- **G9d** No door is orthogonally adjacent to another door.

Out-of-bounds neighbours count as `Tile.WALL` for G9b/G9c. Measured baseline: the v1 generator
violates G9b/G9c on **13.1%** of doors and G9d on 2525 doors per 400 seeds.

**Fix approach is mandated: B — reroute.** Corridors must approach a room's wall
**perpendicularly**, entering through a perimeter cell that is embedded in a wall run.
Demoting a malformed door to `FLOOR` is **not acceptable** — it leaves rooms with no door
(measured: 6.3% of rooms).

**G4a (new).** Every room has **at least one** `Tile.DOOR` on its perimeter, except a
single-room level, which has none.

G1–G8, G10–G12 are unchanged and must still hold. **G8 (full connectivity) is the one that
must not regress** — reroute changes corridor shape, so the flood fill is the guard.

---

## §6 amendment — movement

```python
@dataclass(frozen=True)
class MoveResult:
    position: tuple[int, int]
    moved: bool
    blocked_by_door: tuple[int, int] | None = None

def try_move(
    level: Level,
    position: tuple[int, int],
    dx: int,
    dy: int,
    open_doors: frozenset[tuple[int, int]] = frozenset(),
) -> MoveResult: ...
```

- Passability is now decided by **`world.is_passable(level, open_doors, tx, ty)`**, not by
  `level.is_walkable`. This is the only behavioural change.
- If the target is a **closed door**, return
  `MoveResult(position, False, blocked_by_door=(tx, ty))` — not moved, and the caller is told
  which door to open. All other rejections keep `blocked_by_door=None`.
- Everything else in v1 §6 is unchanged: unchanged position on rejection, `(0, 0)` →
  `moved=False`, `dx`/`dy` outside `{-1, 0, 1}` → `ValueError`, no corner-cutting rule, purity,
  never raises for an ordinary illegal move.
- `is_blocked(level, x, y)` is **retained unchanged** (terrain-only) for v1 compatibility. New
  code uses `world.is_passable`.

---

## §7 amendment — game state and the turn loop

```python
@dataclass(frozen=True)
class GameState:
    level: Level
    player: tuple[int, int]
    explored: frozenset[tuple[int, int]]
    visible: frozenset[tuple[int, int]]
    open_doors: frozenset[tuple[int, int]]
    turns: int = 0
    running: bool = True
    radius: int = fov.DEFAULT_RADIUS

def new_game(level: Level, radius: int = fov.DEFAULT_RADIUS) -> GameState: ...
def step(state: GameState, command: Command) -> GameState: ...
def format_status(state: GameState) -> str: ...
def run(stdscr, level: Level) -> None: ...
def play(seed: int, width: int = 80, height: int = 22) -> None: ...
```

Field order is binding. `explored`/`visible`/`open_doors` precede `turns` because they have no
defaults.

- `new_game` — player at `level.player_start`, `open_doors=frozenset()`, `turns=0`,
  `running=True`, then computes the initial FOV: `visible = fov.compute_visible(...)` and
  `explored = visible`. **The initial state is not blank** — you see where you stand. "Nothing
  is explored" is the state *before* the first FOV, which is never observable.
- `step` stays **pure** and gains exactly three behaviours:
  - `MOVE` accepted → new position, `turns + 1`, **recompute FOV**, `explored |= visible`.
  - `MOVE` blocked by a closed door (`result.blocked_by_door is not None`) → **bump-to-open**:
    `open_doors | {door}`, `turns + 1`, position **unchanged**, **recompute FOV**,
    `explored |= visible`. Opening a door costs a turn and does not move you; the next move
    walks through.
  - `MOVE` blocked by anything else → state unchanged, **turns unchanged**. The v1 rule that a
    rejected move consumes no turn is **unchanged** and still binding.
  - `QUIT` / `UNKNOWN` / `running=False` → exactly as v1. No FOV recompute.
- FOV is recomputed **only** on an accepted move or a door opening — never on a rejected move,
  never on `UNKNOWN`.
- `format_status` — v1 format is **retained exactly**:
  `f"Seed: {seed}  Pos: ({x}, {y})  Turns: {turns}  [q] quit"`. Do not add FOV or door counts.
- `run` — calls `render.render_to_cells(...)` then `render.draw(stdscr, cells)`, and calls
  `render.init_colors()` once after curses is up, guarded against `curses.error`.
- `play` — unchanged signature; still the only place that initialises curses.

`GameState` is still imported by nothing outside `game.py`.

---

## §13 (new) — runtime world predicates: `roguelike/world.py`

The single home for "what is the world like *right now*". Both movement and FOV go through it,
so the door rule has exactly one implementation.

```python
def is_passable(
    level: Level, open_doors: frozenset[tuple[int, int]], x: int, y: int
) -> bool: ...

def is_transparent(
    level: Level, open_doors: frozenset[tuple[int, int]], x: int, y: int
) -> bool: ...

def is_closed_door(
    level: Level, open_doors: frozenset[tuple[int, int]], x: int, y: int
) -> bool: ...
```

- `is_passable` — `level.is_walkable(x, y)` **and** not a closed door. `False` out of bounds.
- `is_transparent` — in bounds **and** tile is not `WALL` **and** not a closed door.
  `False` out of bounds.
- `is_closed_door` — in bounds **and** tile is `DOOR` **and** `(x, y) not in open_doors`.
- **All three never raise.** All three are pure. No module-level state, no caching.
- These two predicates genuinely differ and must not be collapsed: a `FLOOR` cell is both
  passable and transparent; a closed `DOOR` is neither; an open `DOOR` is both; a `WALL` is
  neither — but the *reason* differs, and future terrain (a window, a chasm) would separate
  them further.

Imports: `roguelike.level`, `roguelike.tiles`. No curses. No other project imports.

---

## §14 (new) — field of view: `roguelike/fov.py`

```python
DEFAULT_RADIUS: int = 20

def compute_visible(
    level: Level,
    open_doors: frozenset[tuple[int, int]],
    origin: tuple[int, int],
    radius: int = DEFAULT_RADIUS,
) -> frozenset[tuple[int, int]]: ...
```

### §14.1 The visibility rule — permissive, per user rule #5

> "a symbol is considered visible if any side or corner is in direct eye sight"

This **rules out centre-to-centre recursive shadowcasting**, which is the usual roguelike
algorithm. The required model is **permissive field of view**:

- The **eye** is the centre of the origin cell: `(ox + 0.5, oy + 0.5)`.
- A cell `(x, y)` has **eight sample points** — its four corners
  `(x, y)`, `(x+1, y)`, `(x, y+1)`, `(x+1, y+1)` and its four side midpoints
  `(x+0.5, y)`, `(x+0.5, y+1)`, `(x, y+0.5)`, `(x+1, y+0.5)`.
- `(x, y)` is **visible** iff it is within `radius` **and** at least one sample point is
  reachable from the eye by a straight segment that crosses **no opaque cell**, where opaque
  means `not world.is_transparent(...)`.
- Cells crossed are judged **excluding the origin cell and the target cell itself**. This is
  what makes an opaque cell visible — you see the face of a wall (**rule #5's whole point**).
- **Diagonal-corner rule:** where a segment passes exactly through a grid lattice point, it is
  blocked **only if both cells diagonally flanking that point are opaque.** This stops sight
  leaking through a diagonal join of two walls while still allowing corner peeking.

The segment-clearance test itself is the worker's choice (supercover, fine sampling, or exact
rational arithmetic) provided the guarantees below hold. Fine sampling must be dense enough
that no cell of the crossed run is skipped.

### §14.2 Guarantees

- **F1** `origin` is always in the result, even standing on a wall or off-map terrain.
- **F2** Every returned cell is in bounds and within Euclidean radius:
  `(x-ox)² + (y-oy)² <= radius²`.
- **F3** Pure and deterministic — same inputs, same result, always. No RNG, no set-order
  dependence, no mutation of any argument, no I/O, no curses.
- **F4 (superset property)** Any cell whose **centre** has unobstructed sight from the eye is
  visible. Permissive must never see *less* than centre-to-centre shadowcasting.
  *Measured on the v1 build: permissive found 142 cells vs shadowcasting's 130, with **zero**
  cells visible to shadowcasting but not permissive.*
- **F5** A cell with no clear segment to **any** of its eight sample points is **not** visible.
  A pillar must cast a shadow.
- **F6** Opaque cells can be visible. The walls of the room you stand in are fully visible,
  with **no holes** — the ragged-wall artifact is a defect.
- **F7** A **closed door is opaque**; an **open door is not**. A room behind a closed door is
  unseen; opening it reveals the room.
- **F8** `radius=0` returns exactly `{origin}`. Negative radius → `ValueError`.
- **F9** Cost: permissive is ~150× centre-only shadowcasting. Measured at radius 20 on 80×22:
  **~15 ms**, against a human-keypress budget of ~100 ms. Correctness is the priority; do not
  trade it for speed. A cheap approximation that reveals cells behind corners
  (**measured: 6.8% over-show**) is a **defect**, not an optimisation.

Imports: `roguelike.level`, `roguelike.world`, and `math`. No curses.

---

## §15 (new) — style vocabulary: `roguelike/style.py`

Pure data and pure functions. **No curses in this module** — curses pair allocation lives in
`render.py` (§4), which already owns the terminal side.

```python
class Visibility(Enum):
    UNSEEN   = auto()   # never seen — not drawn at all
    EXPLORED = auto()   # seen before, not in view now — dimmed
    VISIBLE  = auto()   # in view now — natural colour

class Role(Enum):
    TERRAIN = auto()    # wall and floor
    DOOR    = auto()
    PLAYER  = auto()

@dataclass(frozen=True)
class Attr:
    color: int          # 256-colour index; -1 means "terminal default"
    bold: bool = False

def role_for(tile: Tile, is_player: bool = False) -> Role: ...
def attr_for(role: Role, visibility: Visibility, colors: int = 256) -> Attr: ...
```

### §15.1 Palette — binding

| Role | VISIBLE | EXPLORED |
|---|---|---|
| `TERRAIN` (`#`, `.`) — light gray | **250** | **238** |
| `DOOR` (`+`, `'`) — light brown | **180** | **94** |
| `PLAYER` (`@`) — white, **bold** | **231** + bold | n/a — always visible |

`UNSEEN` is never drawn (§4), so it has no colour. Requesting `attr_for(..., UNSEEN)` raises
`ValueError` — drawing an unseen cell is a caller bug.

*Verified on the target terminal: `COLORS=256`, `COLOR_PAIRS=32767`,
`can_change_color=True`, and all five pairs allocate successfully.*

### §15.2 Capability ladder — must degrade, never crash

| `colors` | TERRAIN / DOOR | EXPLORED |
|---|---|---|
| `>= 256` | the palette above | the palette above |
| `>= 8` | `COLOR_WHITE` / `COLOR_YELLOW` | same colour, dim |
| `< 8` (mono) | default colour | default colour, dim |

The `Attr` returned for a degraded terminal uses `color = -1` (terminal default) where no
colour is available; the *dim* signal is carried by `render.py` mapping `EXPLORED` to
`curses.A_DIM` whenever `colors < 256`. Capability is detected **once at startup**, never per
frame.

Imports: `roguelike.tiles`. No curses. No other project imports.

---

## §4 replacement — renderer

v1's `render_to_lines(level, player_pos, status) -> list[str]` **cannot survive**: it has no
way to receive visibility state, and `list[str]` cannot carry colour. Replaced by:

```python
@dataclass(frozen=True)
class Cell:
    char: str
    role: Role
    visibility: Visibility

def render_to_cells(
    level: Level,
    player_pos: tuple[int, int],
    visible: frozenset[tuple[int, int]],
    explored: frozenset[tuple[int, int]],
    open_doors: frozenset[tuple[int, int]],
    status: str,
) -> list[list[Cell]]: ...

def to_lines(cells: list[list[Cell]]) -> list[str]: ...

def init_colors(colors: int | None = None) -> None: ...

def draw(stdscr, cells: list[list[Cell]]) -> None: ...
```

### §4.1 `render_to_cells` — pure, all the logic, all the tests

- Returns exactly `level.height + 1` rows, each of exactly `level.width` `Cell`s: the map, then
  one status row.
- **Cell selection per map cell `(x, y)`:**
  - in `visible` → `Visibility.VISIBLE`, glyph from the tile.
  - else in `explored` → `Visibility.EXPLORED`, glyph from the tile.
  - else → `Visibility.UNSEEN`, **`char = " "`**, `role = TERRAIN`. **Unexplored area is not
    drawn at all** — blank, not a dimmed glyph.
- **Door glyph:** `TILE_CHARS[Tile.DOOR]` (`"+"`) when closed, `DOOR_OPEN_CHAR` (`"'"`) when
  `(x, y) in open_doors`. Role is `DOOR` either way.
- **Player** is drawn at `player_pos` with `PLAYER_CHAR`, `Role.PLAYER`, `Visibility.VISIBLE`,
  overriding the tile — even on a wall. Out-of-bounds `player_pos` is **not drawn, no
  exception** (v1 rule retained).
- **Status row:** every cell `Role.TERRAIN`, `Visibility.VISIBLE`, padded or truncated to
  exactly `level.width`.
- Pure: no mutation of any argument, no I/O, no `curses`, no global state.
- Glyphs come from `roguelike.tiles` — **do not hardcode** `#`, `.`, `+`, `'`, `@` anywhere in
  `render.py` (v1 rule retained and extended to `'`).

### §4.2 `to_lines` — plain-text view

`[[c.char for c in row] joined]` — one string per row, each exactly the row length. Used by
tests and by anything that wants the frame without colour. Never raises.

### §4.3 `init_colors` / `draw` — the only curses in the renderer

- `init_colors(colors=None)` — call once after curses is initialised. Calls
  `curses.start_color()` and `curses.use_default_colors()`, detects `curses.COLORS` when
  `colors is None`, and allocates one pair per `(Role, Visibility)` from `style.attr_for`.
  Every curses call is wrapped against `curses.error`; a terminal without colour must leave the
  renderer working in monochrome, not raise.
- `draw(stdscr, cells)` — `erase()`, blit, `refresh()`. `UNSEEN` cells write a space.
  Attributes come from the pairs allocated by `init_colors`, plus `A_BOLD` for the player and
  `A_DIM` for `EXPLORED` on sub-256-colour terminals.
- **`draw` remains the only place in the codebase where `(y, x)` ordering appears.** Clipping
  and the bottom-right-cell guard from v1 §4 are retained exactly.
- `draw` does no layout and no visibility logic; it is a blitter over `cells`.

---

## §9 replacement — file ownership (v2)

| Path | Owner |
|---|---|
| `roguelike/world.py` | **T07** |
| `roguelike/tiles.py` (amend: `DOOR_OPEN_CHAR`) | **T07** |
| `roguelike/movement.py` (amend) | **T07** |
| `tests/test_world.py`, `tests/test_movement.py` | **T07** |
| `roguelike/style.py`, `tests/test_style.py` | **T08** |
| `roguelike/generator.py` (amend), `tests/test_generator.py` | **T09** |
| `roguelike/fov.py`, `tests/test_fov.py` | **T10** |
| `roguelike/render.py` (rewrite), `tests/test_render.py` | **T11** |
| `roguelike/game.py` (amend), `tests/test_game.py` | **T12** |
| `roguelike/level.py`, `tests/test_level.py`, `tests/test_keys.py`, `roguelike/keys.py` | **unchanged — nobody may edit** |
| `main.py`, `tests/test_integration.py`, `.plan/**` | orchestrator |

`roguelike/level.py` and `roguelike/keys.py` are **untouched by this increment**. If a worker
believes one must change, it reports and stops.

---

## §10 replacement — import graph (v2), still acyclic

```
tiles.py      ← (nothing)
level.py      ← tiles                          [unchanged]
keys.py       ← (nothing but curses + stdlib)  [unchanged]
world.py      ← tiles, level                   NEW
style.py      ← tiles                          NEW
generator.py  ← tiles, level
fov.py        ← level, world                   NEW
movement.py   ← level, world                   (world is new here)
render.py     ← tiles, level, style
game.py       ← level, keys, movement, render, generator, fov, world
main.py       ← game
```

Still binding: `render.py` must **not** import `movement`, `keys`, `game`, `generator`, or
`fov`. `fov.py` must not import `render` or `game`. `style.py` imports only `tiles`.
`world.py` imports only `tiles` and `level`.

---

## §11 amendment — error conventions (additions only)

| Situation | Behaviour |
|---|---|
| `world.*` out of bounds | `False`, never raises |
| `compute_visible` negative radius | `ValueError` |
| `compute_visible` radius 0 | `{origin}` |
| `attr_for(..., Visibility.UNSEEN)` | `ValueError` |
| `init_colors` on a colourless terminal | no raise; monochrome fallback |
| `draw` on an `UNSEEN` cell | writes a space |
| `try_move` into a closed door | `MoveResult(pos, False, blocked_by_door=(x, y))` |

All v1 §11 rows still apply.
