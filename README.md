# roguelike

A terminal roguelike engine with ASCII graphics, in the spirit of [ADOM](https://www.adom.de/).
Procedurally generated dungeons, permissive field of view, fog of war, and 256-colour
rendering — in Python 3.10+, using **nothing but the standard library**.

This is an engine base, not a finished game: it generates a level, lets you walk around it
with the fog lifting as you explore, and quits cleanly. There are deliberately no monsters,
items or combat yet.

```
#############
#...........#
#...........#
#.....@.....#
#...........#
#...........#
###########+#

Seed: 1234  Pos: (6, 5)  Turns: 0  [q] quit
```

Everything beyond your line of sight is genuinely blank — the map is revealed by walking it.

## Quick start

The engine itself needs no packages, but Python **3.10 or newer** is required, and running the
tests needs `pytest`.

Check what you have first — a system `python3` older than 3.10 is common (macOS still ships
3.9), and the failure it produces is confusing:

```bash
python3 --version
```

If that is below 3.10, substitute a newer interpreter (`python3.12`, `python3.14`, …) in the
first command below:

```bash
git clone https://github.com/vvorth/roguelike.git
cd roguelike
python3 -m venv .venv
.venv/bin/python -m pip install pytest
.venv/bin/python main.py
```

Options:

```bash
.venv/bin/python main.py --seed 1234 --width 80 --height 22
```

`--seed` makes a level reproducible; the same seed always produces exactly the same dungeon.
Omit it and one is chosen at random, then printed when you quit so you can replay it.

> **Note:** the map is `--height` rows plus one status line, so a height of 22 needs a 23-row
> terminal. The defaults fit a classic 80×24.

## Controls

| Action | Keys |
|---|---|
| Move | Arrow keys · `hjkl` · numpad `1`–`9` |
| Move diagonally | `y` `u` `b` `n` · numpad `7` `9` `1` `3` |
| Open a door | Walk into it |
| Quit | `q` |

Walking into a closed door opens it. That costs a turn and does **not** move you — the next
step walks through. Walking into a wall is rejected and costs nothing.

## What it does

**Procedural generation.** Rectangular rooms joined by corridors, guaranteed fully connected —
every walkable tile is reachable from where you start. Generation is completely deterministic
from its seed, down to the byte, across processes.

**Permissive field of view.** A cell is visible if *any of its sides or corners* is in direct
line of sight. That rules out the usual roguelike algorithm (recursive shadowcasting tests tile
*centres*), and the difference is visible: centre-testing leaves holes in the walls of the room
you are standing in. Implemented with exact integer grid traversal — no floating point, so the
geometry has no epsilon to tune. Costs about 2 ms per move.

**Fog of war.** Three states, three treatments:

| State | Rendering |
|---|---|
| Never seen | Not drawn at all — blank |
| Seen before, not in view | Dimmed, so you know it is memory |
| In view now | Full natural colour |

**Doors.** A closed door blocks both movement and sight, so a room stays dark until you open
its door. `+` is closed, `'` is open.

**Colour.** 256-colour terminals get a light-gray palette for walls and floors, light brown for
doors, and bold white for the player, with darker shades for explored ground. Falls back
cleanly to 8-colour and monochrome.

## Design

Four constraints shaped the whole codebase, and each one is visible in the module layout:

**Rendering is decoupled from state.** No module both mutates state and draws. The renderer
receives primitives — a level, a position, three sets of coordinates, a status string — and
returns a frame of styled cells. It never sees the game state object, and it cannot: `Level` is
a frozen dataclass whose grid is a tuple of tuples, so mutation fails at the interpreter level
rather than at code review.

**Everything is testable without a terminal.** No module initialises curses at import time, and
the test suite never initialises it at all. Key handling is a pure lookup table; field of view
is a pure function; the turn loop's rules live in a pure `step()` that the thin curses shell
calls.

**One home per rule.** Runtime passability and transparency live only in `world.py`, so the
open-door rule cannot drift between movement and field of view. Glyphs live only in `tiles.py`,
colours only in `style.py`. The `(y, x)` coordinate order that curses requires appears in
exactly one function in the entire codebase — everything else is `(x, y)`.

**Determinism is structural.** The generator uses a local `random.Random(seed)` instance; the
global `random` module is forbidden, because it is shared mutable state that would make output
depend on test ordering.

### Modules

| Module | Responsibility |
|---|---|
| `tiles.py` | Tile vocabulary and glyphs |
| `level.py` | Frozen `Level` and `Room` data structures |
| `generator.py` | Seeded procedural dungeon generation |
| `world.py` | Runtime passability and transparency (door state) |
| `fov.py` | Permissive field of view |
| `movement.py` | Single-step movement and collision |
| `keys.py` | Key codes → movement/quit intents |
| `style.py` | Visibility states, roles, colour palette |
| `render.py` | Frame composition and the curses blitter |
| `game.py` | Turn loop, game state, curses lifecycle |

The import graph is acyclic and enforced by tests — `render.py` cannot import `fov.py`,
`keys.py` imports nothing from the package at all.

## Tests

```bash
.venv/bin/python -m pytest
```

1300 tests, covering each module in isolation plus an end-to-end suite that crosses module
boundaries: connectivity by independent flood fill, a scripted walk asserting the player never
enters a wall or leaves the map, turn accounting, fog-of-war progression, bump-to-open, and
determinism across separate processes.

No test requires a TTY; the suite runs headless.

## Project layout

```
main.py            CLI entry point
roguelike/         the engine
tests/             test suite
requirements.txt   runtime dependencies (intentionally empty)
.plan/             development record — see below
```

## Development record

`.plan/` holds the full history of how this was built: the interface contracts that fixed every
module boundary before any module was written, the task briefs, per-task reports, and
integration notes for both increments.

It is worth reading if you want the *reasoning* rather than the result — for example why
permissive field of view was chosen over a shadowcasting variant that was 100× faster
(it revealed walls around corners the player could not see), or why door state lives in the
game state rather than on the level.

- [`.plan/CONTRACT.md`](.plan/CONTRACT.md) · [`.plan/CONTRACT-v2.md`](.plan/CONTRACT-v2.md) — the frozen interface contracts
- [`.plan/RESEARCH-v2.md`](.plan/RESEARCH-v2.md) — measurements behind the field-of-view and colour decisions
- [`.plan/INTEGRATION.md`](.plan/INTEGRATION.md) · [`.plan/INTEGRATION-v2.md`](.plan/INTEGRATION-v2.md) — what was assembled, verified, and left as known gaps

## Not implemented

Deliberately out of scope, with no speculative stubs left behind: monsters, combat, items,
inventory, saving and loading, multiple levels, stairs, and sound. Terminal resizing is not
handled — the view clips safely rather than reflowing.

## Licence

[MIT](LICENSE) — use it, change it, ship it, sell it. Just keep the copyright notice.
