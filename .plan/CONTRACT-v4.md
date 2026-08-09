# CONTRACT v4 — diagonals, multi-turn activities, pathfinding, auto-navigation

**Frozen once written. Workers may not edit it.** A worker that believes it is wrong reports
that and stops; the orchestrator decides.

Amends `CONTRACT.md` (v1), `CONTRACT-v2.md` and `CONTRACT-v3.md`. Everything in those stays
binding unless amended here.

Decisions from `.plan/RESEARCH-v4.md`, confirmed by the user: **auto-explore stops on the
current level** · **`>` with no known staircase just says so** · **opening a door does not
interrupt an activity**.

---

## §0 amendments

### §0.10 Timing lives in `run`, and nowhere else

The engine's turn logic is pure and the test suite depends on that. v4 adds actions that span
many turns at a paced rate, and **none of that pacing may leak into `step` or `advance`**.

- `step(state, command)` — pure, unchanged in character.
- `advance(state)` — **pure**, performs exactly one turn of the current activity.
- `run` — owns the clock, the keyboard, and nothing else.

No module may call `time.sleep`, read a clock, or busy-wait. Measured: `stdscr.timeout(100)`
delivers both the 10-turns-per-second cap (**9 ticks in 1.0 s**) and instant cancellation (a
waiting key returns in **0.00 ms**), so no other mechanism is needed or permitted.

### §0.11 Integer path costs

Path costs are **integers**: orthogonal `10`, diagonal `14`. Floats are forbidden in the
pathfinder, so ordering is exact and no epsilon or tie-break fudge can appear.

---

## §5 v4 — input

```python
class CommandKind(Enum):
    MOVE = auto(); QUIT = auto(); UNKNOWN = auto()
    DESCEND = auto(); ASCEND = auto()
    AUTO_EXPLORE = auto()      # NEW — "E"
    WALK_PREFIX  = auto()      # NEW — "w"
```

### §5.1 v4 New bindings

Diagonals, rotated **45° clockwise** from the base direction:

| Delta | Direction | Keys |
|---|---|---|
| ( 1, -1) | NE | `curses.KEY_SR` (Shift+Up), `K` |
| ( 1,  1) | SE | `curses.KEY_SRIGHT` (Shift+Right), `L` |
| (-1,  1) | SW | `curses.KEY_SF` (Shift+Down), `J` |
| (-1, -1) | NW | `curses.KEY_SLEFT` (Shift+Left), `H` |

*Measured on `xterm-256color`: Shift+Up→337 `KEY_SR`, Shift+Down→336 `KEY_SF`,
Shift+Right→402 `KEY_SRIGHT`, Shift+Left→393 `KEY_SLEFT`. The up/down names mean "scroll
reverse/forward" for historical reasons and are easy to get backwards — **`KEY_SR` is Shift+Up**.*

| Intent | Key |
|---|---|
| `AUTO_EXPLORE` | `E` |
| `WALK_PREFIX` | `w` |

- Constants must be referenced through `curses`, never hardcoded as 337/336/402/393.
- **Every v1–v3 binding is unchanged**, including numpad `1/3/7/9` and `yubn`, which already
  produce these same four diagonals. `5`, `ESC`, `Y`, `U`, `B`, `N` remain `UNKNOWN`.
- `AUTO_EXPLORE` and `WALK_PREFIX` carry `dx == dy == 0`.
- On a terminal whose terminfo lacks `kUP`/`kDN`/`kLFT`/`kRIT`, the shifted arrows arrive as raw
  escape bytes and are `UNKNOWN`. **Do not write an escape-sequence parser.** `HJKL` is the
  portable path.

---

## §13 v4 — `world.py` gains one predicate

```python
def is_planning_passable(
    level: Level, open_doors: frozenset[tuple[int, int]], x: int, y: int
) -> bool: ...
```

`is_passable(...) or is_closed_door(...)` — true for a cell a route may be planned *through*,
because bumping a closed door opens it.

This must live here, with the other passability rules, so the door rule keeps exactly one home.
`is_passable` and `is_transparent` are **unchanged**; nothing that decides an actual move may
use the planning predicate.

*This is load-bearing: without it, every frontier behind a closed door is unreachable and
auto-explore stalls in the first room.*

---

## §18 (new) — pathfinding: `roguelike/pathfind.py`

Pure, stateless, no project state. Takes a `passable` callable so it never needs to know
whether it is planning over real terrain or only over explored terrain.

```python
Coord = tuple[int, int]
Passable = Callable[[int, int], bool]

ORTHOGONAL_COST: int = 10
DIAGONAL_COST: int = 14

DIRECTIONS: tuple[tuple[int, int], ...]      # the 8 deltas, in a FIXED order

def octile(a: Coord, b: Coord) -> int: ...
def find_path(passable: Passable, start: Coord,
              goals: AbstractSet[Coord]) -> list[Coord] | None: ...
def is_wide(passable: Passable, x: int, y: int) -> bool: ...
def degree(passable: Passable, x: int, y: int) -> int: ...
def is_intersection(passable: Passable, x: int, y: int) -> bool: ...
```

### §18.1 `find_path`

- Returns the full path **including `start`**, ending on a member of `goals`; `[start]` when
  `start` is already a goal; `None` when no goal is reachable or `goals` is empty.
- The path is **shortest** under the 10/14 cost model.
- **Deterministic**: identical inputs give an identical path, run to run and process to
  process. Ties must be broken by a total order — iterate `DIRECTIONS` in its fixed order and
  include the coordinate in the priority-queue key so no comparison is ambiguous.
- `start` itself need not be passable (the player may stand on a cell the predicate rejects);
  every *other* cell on the path must be.
- Diagonal moves are legal iff the destination is passable — **no corner-cutting rule**,
  matching §6.
- Pure: no mutation, no I/O, no global state, no caching. Never raises for unreachable goals.
- **Performance: ≤ 5 ms per call on an 80×22 map.** *Measured reference: 0.235 ms for a
  full-level search.* This budget is why **no caching or incremental replanning may be
  written** — re-planning every turn is affordable and simpler.

### §18.2 The topology predicates

- `is_wide(passable, x, y)` — true iff `(x, y)` belongs to **any** 2×2 block of passable cells.
  **All four quadrants must be checked.** *Measured against true room membership over 23,843
  cells: 99.98% accurate, zero room cells misread as thin. A two-quadrant version scores only
  96.4% — this is a real trap.*
- `degree(passable, x, y)` — the count of passable **orthogonal** neighbours (0–4).
- `is_intersection(passable, x, y)` — `not is_wide(...) and degree(...) >= 3`.
  *Measured: 139 such cells across 40 levels; doors are always degree 2 and never
  intersections.*

Imports: stdlib only. **No project imports at all** — `pathfind.py` is a leaf.

---

## §19 (new) — activity planning: `roguelike/activity.py`

The value type for an action in progress, and the pure planners that decide its next step.
`advance` itself lives in `game.py` (§7 v4), because it needs `GameState`.

```python
class ActivityKind(Enum):
    TRAVEL       = auto()
    AUTO_EXPLORE = auto()
    AUTO_WALK    = auto()

@dataclass(frozen=True)
class Activity:
    kind: ActivityKind
    goal: Coord | None = None                    # TRAVEL
    direction: tuple[int, int] = (0, 0)          # AUTO_WALK
    came_from: Coord | None = None               # AUTO_WALK

def frontier_cells(level: Level, explored: frozenset[Coord],
                   open_doors: frozenset[Coord]) -> frozenset[Coord]: ...

def walk_step(passable: Passable, position: Coord, came_from: Coord | None,
              direction: tuple[int, int]) -> tuple[Coord | None, str]: ...
```

### §19.1 `frontier_cells` — the no-cheating rule

A cell is a frontier iff it is **in `explored`**, satisfies `world.is_planning_passable`, and
either:
- touches (8-directionally) a cell that is **in bounds and not in `explored`**, or
- **is itself a closed door** — it hides whatever lies beyond regardless of what is mapped
  around it.

**The function must read `explored` and never the full grid** beyond bounds and tile identity
for cells already explored. This is requirement "explore from the perspective of the
character". A worker that consults unexplored terrain has failed the task, however good the
resulting coverage looks.

*Measured with this definition: 100% of walkable tiles explored on all five sample seeds,
every run ending by frontier exhaustion.*

### §19.2 `walk_step` — auto-walk and corridor following

Returns `(next_coord, reason)`. `next_coord` is `None` when the walk must stop, and `reason` is
one of the string constants `"blocked"`, `"intersection"`, `"opening"`, or `""` when moving.

- **In the open** (`is_wide(passable, *position)`): the next cell is `position + direction`.
  Stop with `"blocked"` if it is not passable. Wide areas never follow turns.
- **In a corridor** (not wide): candidates are the passable **orthogonal** neighbours excluding
  `came_from`.
  - exactly one candidate → move there,
  - more than one → stop, `"intersection"`,
  - none → stop, `"blocked"`.
- **Stop before a room**: if the chosen next cell `is_wide`, stop with `"opening"` **without
  moving onto it**.
- The first step of a walk has `came_from is None` and uses `direction`; subsequent steps in a
  corridor follow the corridor and may turn.

Imports: `roguelike.level`, `roguelike.world`, `roguelike.pathfind`. No curses.

---

## §16 v4 — events

`EventKind` gains eight members; the existing eight are unchanged.

| Kind | Message |
|---|---|
| `WALK_WHICH_WAY` | `Walk in which direction?` |
| `TRAVELLING` | `You travel towards the staircase.` |
| `ARRIVED` | `You arrive at the staircase.` |
| `EXPLORED_EVERYTHING` | `You have explored everything you can reach here.` |
| `NOTHING_FURTHER` | `There is nowhere further to go.` |
| `STOPPED_AT_JUNCTION` | `You stop at a junction.` |
| `STOPPED_AT_OPENING` | `You stop before the opening.` |
| `INTERRUPTED` | `You stop.` |

`MESSAGES` must still hold an entry for **every** `EventKind`. `NO_STAIRS_DOWN` /
`NO_STAIRS_UP` keep their v3 wording and are still used when nothing of that kind is known.

---

## §7 v4 — game state, activities and the paced loop

```python
@dataclass(frozen=True)
class GameState:
    ...                                   # all v3 fields unchanged, in order
    activity: Activity | None = None      # NEW
    awaiting_walk: bool = False           # NEW

def advance(state: GameState) -> GameState: ...          # NEW, pure
def interruption(before: GameState, after: GameState) -> Event | None: ...   # NEW, pure
```

Both new fields are appended with defaults, so every existing construction keeps working.

### §7.4 `step` additions

All v3 behaviour is retained. New cases:

| Command | Behaviour |
|---|---|
| `WALK_PREFIX` | `awaiting_walk=True`, **no turn**, emit `WALK_WHICH_WAY` |
| `MOVE` while `awaiting_walk` | clear the prefix, start `Activity(AUTO_WALK, direction=(dx,dy))`, **no turn yet** — `advance` takes every step |
| any other command while `awaiting_walk` | clear the prefix, **consume the command entirely**: no turn, no action, no event |
| `AUTO_EXPLORE` | start `Activity(AUTO_EXPLORE)`, no turn |
| `DESCEND` off-stairs, a `STAIRS_DOWN` cell **is** in `explored` | start `Activity(TRAVEL, goal=nearest)`, emit `TRAVELLING`, no turn |
| `DESCEND` off-stairs, none explored | emit `NO_STAIRS_DOWN`, no turn (v3 behaviour) |
| `ASCEND` off-stairs | the same two cases against `STAIRS_UP` |

**Any command delivered to `step` while an activity is running clears that activity first.**
The loop normally cancels before calling `step`, but `step` must not depend on that.

### §7.5 `advance` — pure, one turn of the activity

- No activity → return the state unchanged.
- Compute the next cell:
  - `AUTO_WALK` → `activity.walk_step(...)`, and carry `came_from` forward.
  - `TRAVEL` → `pathfind.find_path` over `world.is_planning_passable` restricted to `explored`,
    goals `{activity.goal}`; take the first step. **Re-plan every turn** — it is affordable.
  - `AUTO_EXPLORE` → path to the nearest member of `activity.frontier_cells(...)`.
- Perform the move by the **same rules as a `MOVE` command**, so a closed door is bumped open,
  costs its turn, emits `DOOR_OPENED`, and the activity **continues** (user decision 3).
- Then call `interruption(before, after)`; a non-`None` result clears the activity and its
  event is the state's events.
- Finishing conditions clear the activity and emit exactly one event:
  `TRAVEL` arrived → `ARRIVED`; no path → `NOTHING_FURTHER`;
  `AUTO_EXPLORE` no frontier → `EXPLORED_EVERYTHING`; no path to any frontier →
  `EXPLORED_EVERYTHING`;
  `AUTO_WALK` → `NOTHING_FURTHER` / `STOPPED_AT_JUNCTION` / `STOPPED_AT_OPENING` per the
  `walk_step` reason.
- **`AUTO_EXPLORE` never descends** and never changes depth (user decision 1).
- An activity does not survive a level change; descending or ascending clears it.

### §7.6 `interruption` — the seam for future events

Returns `None` in **every** case today. Opening a door explicitly does **not** interrupt
(user decision 3). It is called by `advance` after every activity turn so the call site exists
and is tested; when monsters arrive it grows a branch. Do not build a registry, an observer
list, or a plugin mechanism — it is one function.

### §7.7 `run` — the paced loop

```
if state.activity is not None:
    stdscr.timeout(100)
    ch = stdscr.getch()
    if ch != -1:
        state = <activity cleared, INTERRUPTED emitted>   # the key is CONSUMED
    else:
        state = advance(state)
else:
    stdscr.timeout(-1)
    state = step(state, translate_key(stdscr.getch()))
```

- **The cancelling keypress is consumed by the cancellation** and must not also act as a
  command — otherwise a panicked keypress would stop the walk *and* move you.
- Every frame is drawn as in v3. `run` still contains no game rules.
- `timeout` is the only pacing mechanism; no `time.sleep` anywhere.

---

## §9 v4 — file ownership

| Path | Owner |
|---|---|
| `roguelike/keys.py`, `roguelike/events.py`, `tests/test_keys.py`, `tests/test_events.py` | **T18** |
| `roguelike/pathfind.py`, `tests/test_pathfind.py` | **T19** |
| `roguelike/world.py`, `roguelike/activity.py`, `tests/test_world.py`, `tests/test_activity.py` | **T20** |
| `roguelike/game.py`, `tests/test_game.py` | **T21** |
| `roguelike/tiles.py`, `level.py`, `generator.py`, `render.py`, `fov.py`, `movement.py`, `style.py`, `dungeon.py` and their tests | **frozen — nobody may edit** |
| `main.py`, `tests/test_integration.py`, `.plan/**` | orchestrator |

Eight modules are frozen. v4 touches input, planning and control flow only.

---

## §10 v4 — import graph, still acyclic

```
tiles.py, events.py, keys.py, pathfind.py   ← leaves
level.py     ← tiles
world.py     ← tiles, level
style.py     ← tiles
generator.py ← tiles, level
fov.py       ← level, world
movement.py  ← level, world
render.py    ← tiles, level, style
dungeon.py   ← generator, level
activity.py  ← level, world, pathfind                      NEW
game.py      ← level, keys, movement, render, fov, world,
               dungeon, events, pathfind, activity
main.py      ← game
```

`pathfind.py` imports **nothing** from the project — it works through a `passable` callable.
`activity.py` must not import `game`, `render`, `keys` or `events`.

---

## §11 v4 — error conventions (additions)

| Situation | Behaviour |
|---|---|
| `find_path` with empty `goals` | `None`, never raises |
| `find_path` unreachable goal | `None`, never raises |
| `octile` | non-negative `int`, never raises |
| `is_wide` / `degree` / `is_intersection` out of bounds | decided solely by `passable`; never raise |
| `walk_step` with `direction == (0, 0)` in the open | `(None, "blocked")` |
| `advance` with no activity | state returned unchanged |
| `WALK_PREFIX` then a non-direction key | prefix cleared, command consumed, no turn, no event |
| `AUTO_EXPLORE` with nothing to explore | immediate `EXPLORED_EVERYTHING`, no turn |

All v1–v3 rows still apply.
