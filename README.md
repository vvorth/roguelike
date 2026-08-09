# roguelike

A terminal roguelike engine with ASCII graphics, in the spirit of [ADOM](https://www.adom.de/).
A procedurally generated multi-level dungeon, permissive field of view, fog of war, openable
doors, automatic navigation and 256-colour rendering — in Python 3.10+, using **nothing but
the standard library**.

This is an engine base, not a finished game: it generates a dungeon you can descend through,
with the fog lifting as you explore, and quits cleanly. There are deliberately no monsters,
items or combat yet.

```
                                                            
              ###########                                   
              #....@....+                                   
              #.........#                                   
              +.........#                                   
              ###########                                   
                                          Level 1  Seed 1234
```

Everything beyond your line of sight is genuinely blank — the map is revealed by walking it.
Find the down-staircase, press `>`, and the level below is built with its up-staircase exactly
where you were standing:

```
  ##############                                            
  #............+                                            
  #...@........#                                            
  ##############                                            
You descend to level 2.                   Level 2  Seed 1234
```

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

> **Note:** the frame is a stats row, `--height` map rows, and a status row, so a height of 22
> needs a 24-row terminal. The defaults fit a classic 80×24 exactly.

## Controls

| Action | Keys |
|---|---|
| Move | Arrow keys · `hjkl` · numpad `1`–`9` |
| Move diagonally | Shift+arrows · `H` `J` `K` `L` · `y` `u` `b` `n` · numpad `7` `9` `1` `3` |
| Open a door | Walk into it |
| Descend / climb a staircase | `>` / `<` while standing on one |
| **Travel to a known staircase** | `>` / `<` while *not* standing on one |
| **Explore the level** | `E` |
| **Walk until something happens** | `w`, then a direction |
| **Stop whatever is running** | any key |
| Quit | `q` |

Walking into a closed door opens it. That costs a turn and does **not** move you — the next
step walks through. Walking into a wall is rejected and costs nothing.

The Shift diagonals are rotated 45° clockwise from the base direction, so Shift+Up and `K` both
go north-east. Every spelling of a diagonal — Shift, `yubn`, numpad — is the same command.

You start on the up-staircase of level 1. Climbing it leaves the dungeon and ends the run —
there is no win condition yet, so that is how you give up.

> **Note:** Shift+arrows need a terminal whose terminfo carries the shifted-cursor keys (most
> do, including `xterm-256color`). `HJKL` always works and is the portable path.

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

**A dungeon that lines up.** Descending generates the level below with its up-staircase at
*exactly* the coordinate you left from, so the player never moves — the world changes around
them. That is guaranteed by placing the room containing that coordinate **before** any other
room, which means nothing ever has to be carved, reshaped or repaired afterwards. Levels
persist: climb back up and the fog you lifted and the doors you opened are exactly as you left
them.

**Automatic navigation.** `E` explores the level: it walks to the nearest frontier — an
explored cell touching something unseen — until there is nothing left to reach, then stops. It
plans using **only what the character has actually seen**, never the real map, so it opens
doors to find out what is behind them rather than knowing already. `>` or `<` pressed away
from a staircase walks you to the nearest one you have found. `w` then a direction walks until
something interesting happens: in a corridor it follows the turns and stops at a junction or
just before a room opens out; in the open it goes straight until blocked.

Anything multi-turn runs at a capped ten turns per second and **stops the instant you press any
key**. Both come from a single `timeout` on the input read — there is no timer thread, no sleep
and no busy-wait. A hook is in place for the engine to interrupt an activity by itself once
there is something worth interrupting for.

**Pathfinding.** A\* over eight directions with integer costs (10 orthogonal, 14 diagonal), so
routes prefer straight lines and only cut a corner where it genuinely helps. A full-level
search takes about half a millisecond, which is why routes are re-planned every single turn
rather than cached — the simpler design is the affordable one here.

**A message line.** Events are structured values; the wording lives in one table, so adding a
message is one enum member and one table entry. A message stays until another turn — and
because a rejected move costs no turn, bumping a wall leaves the last message on screen.

**Colour.** 256-colour terminals get a light-gray palette for walls and floors, light brown for
doors, and bold white for the player, with darker shades for explored ground. Falls back
cleanly to 8-colour and monochrome.

## Design

Four constraints shaped the whole codebase, and each one is visible in the module layout:

**Rendering is decoupled from state.** No module both mutates state and draws. The renderer
receives primitives — a level, a position, three sets of coordinates, and three finished
strings — and returns a frame of styled cells. It never sees the game state object, and it
cannot format a message: the wording arrives already composed. `Level` is
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
| `keys.py` | Key codes → movement / stairs / activity / quit intents |
| `style.py` | Visibility states, roles, colour palette |
| `render.py` | Frame composition and the curses blitter |
| `game.py` | Turn loop, game state, level persistence, curses lifecycle |
| `dungeon.py` | Per-depth seed derivation and level construction |
| `events.py` | Event vocabulary and the message wording table |
| `pathfind.py` | A\* and the corridor/room topology tests |
| `activity.py` | Frontier search and corridor following |

The import graph is acyclic and enforced by tests — `render.py` cannot import `fov.py`, and
`keys.py`, `events.py` and `pathfind.py` import nothing from the package at all. `pathfind.py`
works through a `passable(x, y)` callable, which is what lets the same code plan over the real
map for one caller and over only the explored map for another.

## Tests

```bash
.venv/bin/python -m pytest
```

1982 tests, covering each module in isolation plus an end-to-end suite that crosses module
boundaries: connectivity by independent flood fill, a scripted walk asserting the player never
enters a wall or leaves the map, turn accounting, fog-of-war progression, bump-to-open,
multi-level descent chains, the fog-and-doors round trip, auto-explore coverage, that a planned
route is walkable step-for-step by the real movement rules, and determinism across separate
processes.

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

- [`.plan/CONTRACT.md`](.plan/CONTRACT.md) · [`.plan/CONTRACT-v2.md`](.plan/CONTRACT-v2.md) · [`.plan/CONTRACT-v3.md`](.plan/CONTRACT-v3.md) · [`.plan/CONTRACT-v4.md`](.plan/CONTRACT-v4.md) — the frozen interface contracts
- [`.plan/RESEARCH-v2.md`](.plan/RESEARCH-v2.md) · [`.plan/RESEARCH-v3.md`](.plan/RESEARCH-v3.md) · [`.plan/RESEARCH-v4.md`](.plan/RESEARCH-v4.md) — measurements behind the field-of-view, colour, stair-anchoring and auto-navigation decisions
- [`.plan/INTEGRATION.md`](.plan/INTEGRATION.md) · [`.plan/INTEGRATION-v2.md`](.plan/INTEGRATION-v2.md) · [`.plan/INTEGRATION-v3.md`](.plan/INTEGRATION-v3.md) · [`.plan/INTEGRATION-v4.md`](.plan/INTEGRATION-v4.md) — what was assembled, verified, and left as known gaps

## Not implemented

Deliberately out of scope, with no speculative stubs left behind: monsters, combat, items,
inventory, saving and loading to disk, and sound. There is no win condition — the dungeon goes
down indefinitely. Auto-explore finishes a level and hands back control; it never descends on
its own. Dungeon branching is scaffolded but not generated: a level carries a tuple
of down-staircases and the seed derivation already takes a branch index, but exactly one is
placed. Terminal resizing is not handled — the view clips safely rather than reflowing.

One known cosmetic defect: on a terminal exactly as tall as the frame, the final character of
the status row is never drawn (curses cannot write the bottom-right cell), so `Seed 1234`
shows as `Seed 123`. A 25-row terminal shows it in full.

## Licence

[MIT](LICENSE) — use it, change it, ship it, sell it. Just keep the copyright notice.
