# CONTRACT — frozen after Phase 1

**This file is frozen. Workers may not edit it.** A worker that believes the contract is
wrong writes that in its report and stops. The orchestrator decides.

Every public name and signature below is binding. Type hints are binding as documentation of
intent; runtime enforcement is only required where explicitly stated.

---

## §0. Universal conventions

### §0.1 Coordinates — read this twice

- Every coordinate is **`(x, y)`**. `x` = column, `y` = row. Origin `(0, 0)` = **top-left**.
- `x` grows **right**, `y` grows **down**.
- Therefore: **up is `dy = -1`**, down is `dy = +1`.
- Grid indexing is the sole inversion: **`grid[y][x]`**. The grid is a sequence of rows.
- `curses` uses `(y, x)`. The swap happens in **exactly one function**,
  `roguelike.render.draw`, at the `addstr` call site. No other module may reference
  `(y, x)` ordering.

Bounds are half-open: valid `x` is `0 <= x < level.width`, valid `y` is
`0 <= y < level.height`.

### §0.2 Python

Python 3.10+. **Standard library only** — no third-party imports in `roguelike/` or
`tests/`, ever. `from __future__ import annotations` at the top of every module.

### §0.3 curses

Importing `curses` is permitted in any module. **No module may call `curses.initscr`,
`curses.wrapper`, `curses.newwin`, or any other terminal-mutating curses function at import
time.** Only `roguelike/game.py` may initialise curses at all, and only inside `play()`.

The full test suite must pass with stdin/stdout redirected and no TTY attached.

### §0.4 Determinism

`roguelike/generator.py` must use a **local `random.Random(seed)` instance**. Calling
module-level `random.random()`, `random.randint()`, `random.choice()`, etc. is **forbidden**.
No use of `time`, `os.urandom`, `uuid`, `id()`, or set/dict iteration order in a way that
affects output.

### §0.5 Immutability

`Level`, `Room`, `Command`, `MoveResult`, and `GameState` are all
`@dataclass(frozen=True)`. Nothing in the system mutates them. State transitions return new
objects.

---

## §1. Tile vocabulary — `roguelike/tiles.py`  *(owned by T1)*

```python
class Tile(IntEnum):
    WALL  = 0
    FLOOR = 1
    DOOR  = 2

TILE_CHARS: dict[Tile, str] = {Tile.WALL: "#", Tile.FLOOR: ".", Tile.DOOR: "+"}
WALKABLE: frozenset[Tile] = frozenset({Tile.FLOOR, Tile.DOOR})
PLAYER_CHAR: str = "@"

def tile_char(tile: Tile) -> str: ...
def is_walkable_tile(tile: Tile) -> bool: ...
```

- `WALL` is not walkable. `FLOOR` and `DOOR` are walkable.
- `tile_char` raises `KeyError` for a value not in `TILE_CHARS`.
- These three tiles are the complete vocabulary. Do not add more.

---

## §2. Level data structure — `roguelike/level.py`  *(owned by T1)*

### §2.1 `Room`

```python
@dataclass(frozen=True)
class Room:
    x: int          # left column of the FLOOR area
    y: int          # top row of the FLOOR area
    width: int      # floor width  (>= 1)
    height: int     # floor height (>= 1)
```

**`Room` describes the floor rectangle only.** Its walls are the 1-cell ring immediately
outside it and are *not* part of the room's `x/y/width/height`.

```python
    @property
    def x2(self) -> int: ...          # inclusive right edge  == x + width - 1
    @property
    def y2(self) -> int: ...          # inclusive bottom edge == y + height - 1
    @property
    def center(self) -> tuple[int, int]: ...   # (x + width // 2, y + height // 2)

    def contains(self, x: int, y: int) -> bool: ...
    def on_perimeter(self, x: int, y: int) -> bool: ...
    def intersects(self, other: "Room", margin: int = 1) -> bool: ...
```

- `contains` — true iff `(x, y)` is inside the **floor** rectangle (inclusive of edges).
- `on_perimeter` — true iff `(x, y)` is on the 1-cell **wall ring** surrounding the floor
  rectangle (the four corners included). Mutually exclusive with `contains`.
- `intersects(other, margin=1)` — true iff the two floor rectangles come within `margin`
  cells of each other. `margin=1` (the default) means "overlapping, touching, or sharing a
  wall" and is what the generator uses for overlap rejection. `margin=0` means strict
  overlap only.
- `__post_init__` raises `ValueError` if `width < 1` or `height < 1`.

### §2.2 `Level`

```python
@dataclass(frozen=True)
class Level:
    width: int
    height: int
    grid: tuple[tuple[Tile, ...], ...]   # grid[y][x] — length height, each row length width
    rooms: tuple[Room, ...]
    player_start: tuple[int, int]        # (x, y)
    seed: int
```

Field order is binding — positional construction must work as written.

```python
    def in_bounds(self, x: int, y: int) -> bool: ...
    def tile_at(self, x: int, y: int) -> Tile: ...
    def is_walkable(self, x: int, y: int) -> bool: ...
```

- `in_bounds` — `0 <= x < width and 0 <= y < height`. Never raises.
- `tile_at` — **raises `IndexError`** if not `in_bounds`. Negative indices must raise, not
  wrap around.
- `is_walkable` — returns `False` if not `in_bounds`; otherwise
  `is_walkable_tile(tile_at(x, y))`. **Never raises.**

`__post_init__` validates and raises `ValueError` on failure:
- `width >= 1` and `height >= 1`
- `len(grid) == height` and every row has `len(row) == width`
- `in_bounds(*player_start)`

It does **not** require `player_start` to be walkable, and it does **not** require
`rooms` to be non-empty — degenerate levels must be constructible in tests (see BRIEF Q10).

### §2.3 Grid helper

```python
def freeze_grid(grid: list[list[Tile]]) -> tuple[tuple[Tile, ...], ...]: ...
def blank_grid(width: int, height: int, fill: Tile = Tile.WALL) -> list[list[Tile]]: ...
```

`blank_grid` returns a **mutable** `list[list[Tile]]` of `height` rows × `width` columns for
the generator to carve into; `freeze_grid` converts it for `Level` construction. Both raise
`ValueError` for non-positive dimensions.

---

## §3. Generator — `roguelike/generator.py`  *(owned by T2)*

```python
DEFAULT_WIDTH: int = 80
DEFAULT_HEIGHT: int = 22
MIN_ROOM_SIZE: int = 4       # minimum floor width and height of a room
MAX_ROOM_SIZE: int = 12      # maximum floor width and height of a room

def generate_level(
    seed: int,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    max_rooms: int = 12,
) -> Level: ...
```

`seed` is positional-first and mandatory. The algorithm is the worker's choice; these
guarantees are not:

**G1 — Determinism.** `generate_level(s, w, h, m)` called twice returns `Level` values that
compare equal in every field, including `rooms` order. Must hold across separate processes.

**G2 — Local RNG.** Uses `random.Random(seed)` only (§0.4).

**G3 — Border.** Every cell with `x == 0`, `y == 0`, `x == width - 1`, or `y == height - 1`
is `Tile.WALL`.

**G4 — At least one room.** `len(level.rooms) >= 1` always. Never returns a zero-room level.

**G5 — Rooms are disjoint.** For any two distinct rooms `a, b`: `not a.intersects(b)`
(default `margin=1`), so at least one wall cell separates them.

**G6 — Rooms are in bounds with a wall margin.** Every room's floor rect satisfies
`1 <= x`, `1 <= y`, `x2 <= width - 2`, `y2 <= height - 2`.

**G7 — Room floors are carved.** Every cell inside a room's floor rect is `Tile.FLOOR`.

**G8 — Full connectivity.** 4-directional flood fill over all walkable tiles, starting from
`player_start`, reaches **every** walkable tile in the grid. No isolated pockets, no
unreachable rooms, no orphan corridor stubs.

**G9 — Doors.** Every `Tile.DOOR` cell lies on some room's `on_perimeter`. Doors are
walkable. Zero doors is acceptable for a single-room level.

**G10 — Player start.** `player_start` is inside some room's floor rect and
`level.is_walkable(*player_start)` is `True`.

**G11 — Seed recorded.** `level.seed == seed`.

**G12 — Dimensions honoured.** `level.width == width`, `level.height == height`.

### §3.1 Errors

- `seed` not an `int`, or a `bool` → **`TypeError`**.
- `width`/`height`/`max_rooms` not `int` → `TypeError`.
- `max_rooms < 1` → **`ValueError`**.
- `width` or `height` too small to place a single `MIN_ROOM_SIZE` room with its wall margin
  (i.e. `width < MIN_ROOM_SIZE + 2` or `height < MIN_ROOM_SIZE + 2`) → **`ValueError`**.

Never returns a `Level` violating G1–G12; raise instead.

---

## §4. Renderer — `roguelike/render.py`  *(owned by T3)*

Two layers. The pure one carries the logic; the curses one is a dumb blitter.

```python
def render_to_lines(
    level: Level,
    player_pos: tuple[int, int],
    status: str,
) -> list[str]: ...
```

- Returns exactly `level.height + 1` strings: `level.height` map rows, then one status row.
- Every returned string is **exactly `level.width` characters** — padded with spaces or
  truncated. This includes the status line.
- Map cell `(x, y)` renders as `TILE_CHARS[level.tile_at(x, y)]`, except that `player_pos`
  renders as `PLAYER_CHAR`.
- The player is drawn even if standing on a wall or an unexpected tile. If `player_pos` is
  out of bounds it is simply not drawn — **no exception**.
- **No-mutation guarantee:** takes only these three values, returns only a new list of new
  strings, touches no global state, performs no I/O, and does not import `curses`. `Level`
  being frozen makes this structural.
- Pure: same inputs ⇒ same output, always.

```python
def draw(stdscr, level: Level, player_pos: tuple[int, int], status: str) -> None: ...
```

- Calls `render_to_lines`, then blits to `stdscr`. This is the **only** place in the codebase
  where `(y, x)` ordering appears.
- Clips to the window: draws only rows `< max_y` and truncates each line to `max_x`.
- Guards the bottom-right cell (writing it raises in curses); swallows `curses.error`.
- Calls `stdscr.erase()` before drawing and `stdscr.refresh()` after.
- Returns `None`. Mutates nothing but the screen.

`draw` is exercised only by the integration smoke test; `render_to_lines` carries the unit
tests.

---

## §5. Input abstraction — `roguelike/keys.py`  *(owned by T4)*

Turns raw key codes into intent, with zero terminal involvement.

```python
class CommandKind(Enum):
    MOVE    = auto()
    QUIT    = auto()
    UNKNOWN = auto()

@dataclass(frozen=True)
class Command:
    kind: CommandKind
    dx: int = 0
    dy: int = 0

QUIT_COMMAND: Command    = Command(CommandKind.QUIT)
UNKNOWN_COMMAND: Command = Command(CommandKind.UNKNOWN)

def translate_key(key: int | str) -> Command: ...
```

- `key` is an `int` (as returned by `curses.getch()`) **or** a length-1 `str` (convenience for
  tests). A `str` is converted with `ord()`. Any other type, or a `str` of length != 1,
  raises `TypeError`.
- Unrecognised keys return `UNKNOWN_COMMAND` — **never raise**.
- A `MOVE` command always has `dx, dy ∈ {-1, 0, 1}` and never `(0, 0)`.

### §5.1 Binding table — binding

Remember `dy = -1` is **up** (§0.1).

| Intent | dx, dy | Keys |
|---|---|---|
| West      | (-1,  0) | `h`, `4`, `curses.KEY_LEFT` |
| East      | ( 1,  0) | `l`, `6`, `curses.KEY_RIGHT` |
| North     | ( 0, -1) | `k`, `8`, `curses.KEY_UP` |
| South     | ( 0,  1) | `j`, `2`, `curses.KEY_DOWN` |
| North-west| (-1, -1) | `y`, `7` |
| North-east| ( 1, -1) | `u`, `9` |
| South-west| (-1,  1) | `b`, `1` |
| South-east| ( 1,  1) | `n`, `3` |
| Quit      | —        | `q`, `Q` |

- Movement letters are **lowercase only**. Uppercase `H`, `J`, `K`, `L`, etc. are `UNKNOWN`
  (they mean "run" in some roguelikes — out of scope).
- Numpad `5` → `UNKNOWN`. There is no wait command (BRIEF Q8).
- `ESC` is **not** quit (BRIEF Q9).
- `curses.KEY_*` constants must be referenced through `curses`, not hardcoded.
  Importing `curses` for this is explicitly allowed (§0.3).
- The digit keys are the ASCII characters `'1'`–`'9'`, i.e. `ord('4') == 52`, **not** the
  integers `1`–`9`. `curses.KEY_A1`-style keypad codes are out of scope.

---

## §6. Movement and collision — `roguelike/movement.py`  *(owned by T5)*

```python
@dataclass(frozen=True)
class MoveResult:
    position: tuple[int, int]   # new position if moved, else the ORIGINAL position
    moved: bool

def try_move(
    level: Level,
    position: tuple[int, int],
    dx: int,
    dy: int,
) -> MoveResult: ...
```

- Target is `(position[0] + dx, position[1] + dy)`.
- If `level.is_walkable(*target)` → `MoveResult(target, True)`.
- Otherwise → `MoveResult(position, False)` with `position` **unchanged and identical** to
  the input tuple. This covers walls, out-of-bounds, and the map border uniformly, because
  `is_walkable` returns `False` off-map (§2.2).
- `dx == 0 and dy == 0` → `MoveResult(position, False)`. Not an error.
- `dx` or `dy` outside `{-1, 0, 1}` → **`ValueError`**. Movement is single-step only.
- Diagonal moves are legal iff the destination is walkable. **No corner-cutting rules**
  (BRIEF Q7).
- Pure: never mutates `level` or `position`, never raises for an ordinary illegal move,
  performs no I/O.

The caller increments the turn counter **iff `result.moved` is `True`** — this is how
"a rejected move consumes no turn" is realised.

```python
def is_blocked(level: Level, x: int, y: int) -> bool: ...
```
Convenience predicate: `not level.is_walkable(x, y)`. Never raises.

---

## §7. Game loop — `roguelike/game.py`  *(owned by T6)*

Owns the turn loop and the curses lifecycle. Does **not** draw — it calls the renderer.

```python
@dataclass(frozen=True)
class GameState:
    level: Level
    player: tuple[int, int]    # (x, y)
    turns: int = 0
    running: bool = True

def new_game(level: Level) -> GameState: ...
def step(state: GameState, command: Command) -> GameState: ...
def format_status(state: GameState) -> str: ...
def run(stdscr, level: Level) -> None: ...
def play(seed: int, width: int = 80, height: int = 22) -> None: ...
```

- `new_game(level)` → `GameState(level, level.player_start, turns=0, running=True)`.
- **`step(state, command)` is pure** — returns a **new** `GameState`, never mutates the
  input, never touches curses, never draws.
  - `CommandKind.QUIT` → same state with `running=False`. Turns unchanged.
  - `CommandKind.UNKNOWN` → state returned unchanged (may be the same object).
  - `CommandKind.MOVE` → delegates to `movement.try_move`. On `moved=True`, new position and
    `turns + 1`. On `moved=False`, position and `turns` both unchanged.
  - `step` on a state with `running=False` returns it unchanged.
- `format_status(state)` → `f"Seed: {seed}  Pos: ({x}, {y})  Turns: {turns}  [q] quit"`.
  Returns a plain `str`; padding/truncation is the renderer's job (§4).
- `run(stdscr, level)` — the loop: `render.draw(...)`, `stdscr.getch()`, `translate_key`,
  `step`, repeat until `not state.running`. Configures `curses.curs_set(0)` and
  `stdscr.keypad(True)` defensively (both wrapped against `curses.error`). Returns when the
  player quits.
- `play(seed, width, height)` — generates the level and calls `curses.wrapper(run, level)`.
  **This is the only place in the codebase that initialises curses.** `curses.wrapper`
  guarantees `endwin()` on both normal return and exception, which is the clean-quit path;
  a `KeyboardInterrupt` must also leave the terminal restored.

`GameState` lives here and **nothing else imports it** — the renderer deliberately takes
primitives instead (BRIEF Q14).

---

## §8. Entry point — `main.py`  *(owned by the orchestrator, Phase 5)*

CLI: `--seed`, `--width`, `--height`. Defaults to a random seed when `--seed` is omitted
(the *choice* of seed may be random; generation from it stays deterministic). Calls
`roguelike.game.play`. No worker touches this file.

---

## §9. File layout and ownership — one owner per file

| Path | Owner |
|---|---|
| `roguelike/__init__.py` | orchestrator (scaffolding, already written) |
| `pytest.ini`, `.gitignore` | orchestrator (scaffolding, already written) |
| `tests/__init__.py` | orchestrator (scaffolding, already written) |
| `roguelike/tiles.py` | **T1** |
| `roguelike/level.py` | **T1** |
| `tests/test_level.py` | **T1** |
| `roguelike/keys.py` | **T4** |
| `tests/test_keys.py` | **T4** |
| `roguelike/generator.py` | **T2** |
| `tests/test_generator.py` | **T2** |
| `roguelike/render.py` | **T3** |
| `tests/test_render.py` | **T3** |
| `roguelike/movement.py` | **T5** |
| `tests/test_movement.py` | **T5** |
| `roguelike/game.py` | **T6** |
| `tests/test_game.py` | **T6** |
| `main.py` | orchestrator |
| `tests/test_integration.py` | orchestrator |
| `.plan/**` | orchestrator (workers write only `.plan/reports/TNN.md`) |

A worker creates and edits **only** the files listed against its own task, plus its report.
No worker creates a file not listed above.

---

## §10. Import graph — binding, and acyclic

```
tiles.py     ← (nothing)
level.py     ← tiles
keys.py      ← (nothing but curses + stdlib)
generator.py ← tiles, level
render.py    ← tiles, level
movement.py  ← level            (may import tiles)
game.py      ← level, keys, movement, render, generator
main.py      ← game
```

Any import not on this list is a contract deviation. In particular: `render.py` must not
import `movement`, `keys`, `game`, or `generator`; `movement.py` must not import `render`
or `keys`; `keys.py` must not import anything from `roguelike`.

---

## §11. Error and edge conventions — summary

| Situation | Behaviour |
|---|---|
| `tile_at` out of bounds | `IndexError` |
| `is_walkable` out of bounds | `False`, no raise |
| `try_move` into wall or off-map | `MoveResult(unchanged_pos, False)`, no raise |
| `try_move` with `dx`/`dy` outside `{-1,0,1}` | `ValueError` |
| `translate_key` unknown key | `UNKNOWN_COMMAND`, no raise |
| `translate_key` wrong type | `TypeError` |
| `generate_level` non-int / bool seed | `TypeError` |
| `generate_level` dimensions too small | `ValueError` |
| `generate_level` `max_rooms < 1` | `ValueError` |
| `Level` grid/dimension mismatch | `ValueError` from `__post_init__` |
| `Room` with `width < 1` or `height < 1` | `ValueError` from `__post_init__` |
| Zero-room `Level` constructed directly | allowed |
| `generate_level` producing zero rooms | impossible — raises instead |
| `render_to_lines` with out-of-bounds player | player omitted, no raise |
| Terminal smaller than the map | `draw` clips, no raise |

---

## §12. Test and verification conventions

- Tests live in `tests/`, named `test_<module>.py`, using plain `pytest` — no fixtures
  shared across task boundaries, no `conftest.py` (nobody owns it).
- Tests must not initialise curses and must pass with no TTY.
- Each task's tests must pass **in isolation**:
  `.venv/bin/python -m pytest tests/test_<module>.py`
- The interpreter for every command in this project is `.venv/bin/python` (Python 3.14.6,
  pytest 9.1.1) — see BRIEF, "Environment finding". Do not invoke bare `python` or `python3`;
  the system interpreter is 3.9 and will fail.
- No worker runs the full suite; other tasks' files may not exist yet. Run only your own.
