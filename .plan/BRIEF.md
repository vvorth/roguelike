# BRIEF — Terminal Roguelike Engine (base)

## Goal

A runnable, interactive ASCII roguelike *base*: launch it, get a procedurally generated
dungeon level, walk an `@` around it with the keyboard, quit cleanly. ADOM-like in feel.
This is an engine skeleton, not a game — it ends where gameplay would begin.

## In scope

1. **Procedural level generation** — one level: rectangular rooms joined by corridors,
   guaranteed fully connected, drawn as `#` walls, `.` floors, `+` doors.
2. **Player movement** — `@` driven by arrow keys, `hjkl` (+ `yubn` diagonals), and
   numpad `1-9`, giving 8-directional movement.
3. **Collision** — walls and map bounds reject the move; a rejected move costs no turn.
4. **Render loop** — map + player + a one-line status bar, redrawn each frame.
5. **Clean quit** — the terminal is always restored, including on exception.

## Non-goals (do not build, do not stub speculatively)

Monsters, combat, items, inventory, field of view / lighting, save-load, multiple levels,
stairs, sound, colour beyond terminal defaults. No `pass`-bodied placeholder classes for
any of these. No "future-proofing" hooks. If a worker feels the urge to add an `entities`
list "for later", the answer is no.

## Hard constraints

- Python 3.10+, **standard library only**. `curses` for terminal I/O.
- **Deterministic generation.** The generator takes a seed; identical seed ⇒ byte-identical
  level, forever.
- **Rendering decoupled from state.** No module both mutates state and draws.
- **Every module importable and testable without a live terminal.** Headless test runs must
  never initialise curses.
- `python -m pytest` at the project root passes with zero failures.

## System shape

Four data hops, and the seams between them are the whole design:

```
seed:int ──▶ generator ──▶ Level ──┬──▶ renderer ──▶ terminal
                                   │
keycode:int ──▶ input ──▶ Command ─┴──▶ movement ──▶ MoveResult ──▶ new player pos
```

`Level` is immutable. `Command` and `MoveResult` are immutable. The only mutable thing in
the system is the game loop's current `GameState`, and it is replaced, never edited.

## Ordering constraints

- Tile vocabulary and `Level` must exist before the generator, renderer, or movement can be
  written — all three are defined in terms of them. This is the only real dependency.
- The input layer depends on nothing; it can be built first, in parallel.
- The game loop depends on everything and must be built last.

## Environment finding (blocking, resolved before planning)

System `python3` is **3.9.6** — below the 3.10 floor, and lacking pytest. Homebrew
`python3.14` (3.14.6) is present with a working `curses`. A project venv has been created:

```
.venv/  →  Python 3.14.6, pytest 9.1.1, curses available
```

All verification commands in this project run through `.venv/bin/python`. With the venv
active, `python -m pytest` at the project root is exactly that command, so the stated
constraint holds. `.venv/` is gitignored.

## Open questions — and the decision made for each

Every one of these is resolved here. No worker decides any of them.

### Q1. Coordinate convention — the classic footgun
`curses.addch` takes `(y, x)`; game logic wants `(x, y)`. Mixing them is the single most
likely way this project breaks.
**Decision:** all game state, every signature, every tuple is **`(x, y)`**, origin top-left,
`x` = column growing right, `y` = row growing **down**. Grid storage is the one inversion:
`grid[y][x]`, a sequence of rows. The `(y, x)` swap happens in exactly one place — inside
the curses adapter, at the `addstr` call. Nowhere else, ever.
*Why:* `(x, y)` is the natural idiom for movement deltas and reads correctly in tests;
confining the swap to one function makes the footgun auditable.

### Q2. How rich is the tile vocabulary?
**Decision:** exactly three — `WALL`, `FLOOR`, `DOOR`. No separate "undug rock" / void tile;
everything not carved is `WALL`.
*Why:* the scope names three glyphs. A void tile only earns its keep with FOV or digging,
both explicitly out of scope.

### Q3. Enum tiles or raw characters in the grid?
**Decision:** `IntEnum`, with a separate character map. The grid stores `Tile`, never `str`.
*Why:* keeps the glyph a rendering concern. A grid of chars would leak presentation into
generation and make "which tiles are walkable" a string comparison.

### Q4. Is `Level` mutable?
**Decision:** frozen dataclass, grid stored as `tuple[tuple[Tile, ...], ...]`.
*Why:* this makes "the renderer must not mutate state" a *structural* guarantee rather than
a code-review promise — a renderer that tries to write to the grid raises `TypeError`. The
generator builds a mutable list-of-lists internally and freezes it at the end via a provided
helper.

### Q5. Out-of-bounds access — raise or return `WALL`?
Both conventions are defensible and silently mixing them causes bugs where an off-map read
looks like a wall and a real bug goes unnoticed.
**Decision:** split by intent.
- `Level.tile_at(x, y)` **raises `IndexError`** off-map — it is a lookup, and an off-map
  lookup is a caller bug that should be loud.
- `Level.is_walkable(x, y)` **returns `False`** off-map and never raises — it is a predicate,
  and "can I step there?" has a correct answer outside the map.
Collision code calls `is_walkable`. Rendering iterates in-bounds by construction.

### Q6. Corridor style and door placement
**Decision:** the contract fixes *guarantees*, not the algorithm. The generator may choose
L-shaped doglegs, BSP, or anything else, provided: rooms never overlap and are separated by
at least one wall cell; every room is reachable from every other; doors appear **only** on a
room's wall perimeter, where a corridor pierces it; the map's outermost ring is always
`WALL`.
*Why:* over-specifying the algorithm buys nothing testable. Connectivity is verified by
flood fill, which is algorithm-agnostic.

### Q7. Diagonal corner-cutting
Many roguelikes forbid moving diagonally between two walls.
**Decision:** allowed. A diagonal move is legal iff the *destination* tile is walkable.
*Why:* corner rules are a gameplay tuning decision, and gameplay is out of scope. One rule,
one predicate, trivially testable.

### Q8. Numpad `5` / a "wait" action
**Decision:** `5` maps to `UNKNOWN`. There is no wait command.
*Why:* waiting is only meaningful once something else acts during your turn — monsters, out
of scope. `UNKNOWN` is already a no-op that costs no turn, so nothing is lost.

### Q9. What signals quit?
**Decision:** `q` and `Q` only. Not `ESC`.
*Why:* `ESC` is the prefix of every arrow-key escape sequence; binding it invites a timing
race with no benefit at this scope.

### Q10. Zero-room levels and impossible dimensions
**Decision:** `generate_level` **never** returns a level with zero rooms — if the requested
dimensions cannot fit even one room, it raises `ValueError`. But the `Level` *type* tolerates
`rooms=()` so degenerate levels can be hand-built in tests, and the renderer must draw an
all-wall level without crashing.
*Why:* separates "the generator's contract with callers" from "the data type's tolerance for
test fixtures". A generator that returns an unplayable level is a bug; a type that refuses to
represent one is untestable.

### Q11. Invalid seeds
**Decision:** `seed` must be an `int`; anything else raises `TypeError`. Negative and zero
seeds are valid. `bool` is rejected despite being an `int` subclass.
*Why:* seeds get passed in from CLI parsing and test parametrisation; a silent `hash()`
fallback would make determinism depend on `PYTHONHASHSEED`.

### Q12. Global `random` vs a local `Random`
**Decision:** the generator uses a **local `random.Random(seed)` instance**. Touching the
module-level `random.*` functions is forbidden.
*Why:* the global generator is shared mutable state — test ordering, or any other library,
would silently break reproducibility. This is the single most important rule for the
determinism constraint.

### Q13. Rejected-move semantics
**Decision:** `try_move` returns a `MoveResult` carrying the **unchanged** position and
`moved=False`. The turn counter increments only when `moved` is `True`.
*Why:* makes "consumes no turn" a property the caller can't get wrong, and keeps `try_move`
pure — it never raises for an illegal move, since illegal moves are ordinary input.

### Q14. Where does the status bar text come from?
**Decision:** the renderer receives the status line as a **plain string** and is handed
`(level, player_pos, status)` — never a `GameState`. The game loop composes the string.
*Why:* keeps the renderer free of any dependency on the loop's state type, so the two can be
written by workers who never speak, and the renderer stays testable with three primitives.
Content: `Seed: N  Pos: (x, y)  Turns: N  [q] quit`.

### Q15. Terminal smaller than the map
**Decision:** the curses adapter **clips** — it draws the lines and columns that fit and
never raises. Including the classic bottom-right-cell write, which is guarded.
*Why:* crashing on a resized terminal violates the clean-quit requirement.

### Q16. Where does the curses lifecycle live?
**Decision:** one module (`game.py`) owns `curses.wrapper`, and it is the only module that
may *initialise* curses. Importing `curses` at module top-level is permitted anywhere —
importing does not touch the terminal; only `initscr()` does. The headless-testability
constraint is therefore stated precisely as: **no module may call `initscr`, `wrapper`, or
any terminal-mutating curses function at import time.**
*Why:* the naive reading ("never import curses") would force hardcoding `KEY_UP = 259` magic
numbers, which is worse.

### Q17. Map size
**Decision:** default 80 × 22 map + 1 status line = 23 rows, inside a classic 80×24 terminal.
Dimensions are parameters, not constants.
