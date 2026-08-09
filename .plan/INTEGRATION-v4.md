# INTEGRATION v4 — diagonals, multi-turn activities, pathfinding, auto-navigation

Status: **complete**. Full suite **1982 passed, exit 0, zero skips**. Live curses session
verified, including a paced activity and a mid-activity cancellation. v3 was 1666; v4 adds 316.

## What was assembled

```
roguelike/pathfind.py    T19  NEW    A*, octile, is_wide/degree/is_intersection
roguelike/activity.py    T20  NEW    Activity, frontier_cells, walk_step
roguelike/keys.py        T18  amend  Shift diagonals, AUTO_EXPLORE "E", WALK_PREFIX "w"
roguelike/events.py      T18  amend  eight activity messages
roguelike/world.py       T20  amend  is_planning_passable
roguelike/game.py        T21  amend  activity, awaiting_walk, advance, interruption, paced loop
roguelike/tiles.py, level.py, generator.py, render.py, fov.py, movement.py,
  style.py, dungeon.py          frozen — untouched, and needed no change
main.py                  orch  unchanged
tests/test_integration.py orch extended with a v4 section
```

| Suite | v3 | v4 |
|---|---|---|
| `test_keys.py` | 120 | **158** |
| `test_events.py` | 51 | **75** |
| `test_pathfind.py` | — | **53** |
| `test_world.py` | 26 | **38** |
| `test_activity.py` | — | **55** |
| `test_game.py` | 259 | **374** |
| `test_integration.py` | 85 | **104** |
| unchanged suites | 1125 | 1125 |
| **Total** | **1666** | **1982** |

## Run it

```bash
.venv/bin/python main.py --seed 1234
```

New in v4:

| Action | Keys |
|---|---|
| Move diagonally | Shift+arrows · `H` `J` `K` `L` · `y` `u` `b` `n` · numpad `7` `9` `1` `3` |
| Auto-explore the level | `E` |
| Walk until something happens | `w` then a direction |
| Travel to a known staircase | `>` or `<` while not standing on one |
| Stop any of the above | **any key** |

## The four requirements

### Diagonal movement
Numpad `1/3/7/9` and `yubn` already worked since v1. New: Shift+arrows and Shift+`hjkl`,
each rotated **45° clockwise** from the base direction — `K`/Shift+Up gives north-east, and so
on round. All four spellings of a diagonal produce value-equal commands.

### Multi-turn actions
Capped at ten turns per second, cancellable by any key, with a seam for future automatic
interruption. **One mechanism delivers the first two**: `stdscr.timeout(100)` blocks for at
most 100 ms, so no key means take a turn and a key cancels at once. No `sleep`, no busy-wait,
and no clock outside `run`.

### Pathfinding and auto-navigation
A\* with integer costs (10 orthogonal, 14 diagonal), using diagonals where they help.
Auto-explore reaches the whole level using only what the character has seen. `>`/`<` off the
stairs travels to the nearest **known** staircase. `w`+direction follows a corridor through its
turns and stops at a junction or before a room.

### Interruption seam
`interruption(before, after)` is called after every activity turn and returns `None` in every
case today, by your decision that opening a door should not stop a walk. The seam ships
called-and-tested; when monsters arrive it grows a branch.

## Verification

### End to end (104 tests, orchestrator-owned)
The v1–v3 properties all re-checked, plus a v4 section: that all four spellings of a diagonal
are the same command and actually move the player diagonally; that **a route planned by
`pathfind` is walkable step-for-step by the real movement rules** (including bumping a closed
door open and retrying, which is exactly what the activity layer does); that auto-explore
reaches ≥95%, stops, and never changes depth; that the frontier is a function of `explored`
alone; that travel reaches the staircase and that an unknown staircase only reports; that
`w`+direction starts and stops correctly; that any command clears an activity and no activity
survives a level change; and that **an activity never walks the player into a wall** — the v1
safety invariant, now that something other than the player is driving.

### Independent re-measurement by the orchestrator
Not taken from any worker's tests:

| Check | Result |
|---|---|
| Auto-explore coverage, seeds 1234 / 7 / 42 | **100.0% each**, 180 / 154 / 212 turns, all ending by frontier exhaustion |
| The same driver with `is_passable` instead of `is_planning_passable` | **12.5% in 0 turns** — the "stalls in the first room" failure, reproduced |
| `is_wide`, each of the four quadrants in turn | correct in all four; False for both 1-wide corridors |
| Shortest-path vs an orchestrator-written Dijkstra | 106/106, 286/286, 106/106 |
| Determinism across `PYTHONHASHSEED` 0/1/999 | identical path |
| Full-level search | **0.463 ms**, budget 5 ms |
| `KEY_SR` → NE, `KEY_SF` → SW | not inverted |

### Live curses session (80×24 pty)

| Check | Result |
|---|---|
| Auto-explore runs paced | **~12 turns in 2.01 s (~6/sec)** — under the 10/sec cap |
| A keypress cancels | first redraw **5 ms** later, not 100 |
| Idle after cancelling | **0 bytes** — the activity genuinely stopped |
| Quits cleanly, termios restored, ICANON/ECHO back | pass |
| No traceback | pass |

The observed ~6 turns/sec rather than a flat 10 is expected: each turn also pays for planning
and a redraw on top of the 100 ms tick. The requirement was a **cap**, and it holds.

## Deviations found

**None from any worker.** T21 — the first to see the whole v4 stack — re-derived every public
signature from the shipped code and found no behavioural discrepancy.

One harmless wording inconsistency in my contract, found by T21: §7.5 writes
`activity.walk_step(...)` as though `Activity` had methods, while §19 declares them as
module-level functions. The code follows §19, which is correct; only the prose misleads.

### Process notes worth keeping

- **Three of the four workers were cut off by API errors** after finishing their code but
  before writing their reports. Each was resumed from transcript to write the report only,
  with an explicit instruction not to redo the implementation. No task was marked `done` on
  code alone — the standing rule (report present **and** orchestrator ran the verification)
  held every time, and each resumed worker's code was independently re-verified first.
- **T19's report never reached disk**: its resumed run returned the text in its reply but made
  zero tool calls. **T20 noticed and flagged it** rather than reaching outside its ownership,
  which is exactly right. I transcribed the agent's verbatim text into `.plan/reports/T19.md`
  with a note saying so.
- **T20's open question became T21's most valuable test.** T20 asked that `advance` subtract
  the player's own cell from the frontier goal set, since otherwise `find_path` returns
  `[start]` and the activity stalls silently. T21 implemented *and tested* it — and reported
  that removing the guard is caught by that one test and by none of the other 373.
- **The v3 freezing mistake did not recur.** Before freezing anything I searched every frozen
  suite for surfaces v4 changes and confirmed none contained an invalidated assertion. It held:
  no frozen file needed an orchestrator repair this time.

## Known gaps

1. **The status row still loses its last character on a terminal exactly as tall as the frame**
   — the v3 defect, unchanged. `Seed 1234` paints as `Seed 123`. Two clean fixes are recorded
   in `.plan/INTEGRATION-v3.md`; both are §4.2 contract changes and remain the user's call.
2. **Shifted arrows depend on terminfo.** On a terminal whose entry lacks `kUP`/`kDN`/`kLFT`/
   `kRIT`, they arrive as raw escape bytes and read as `UNKNOWN`. `HJKL` always works and is
   the portable path. No escape parser was written, deliberately.
3. **`HJKL` overrides a genre convention** — in NetHack and ADOM those mean *run*. Nothing is
   lost functionally, since `w`+direction runs, but roguelike muscle memory will be surprised.
4. **`interruption` has no live conditions.** By decision. It is called every activity turn and
   pinned closed by a test, so any change is deliberate.
5. **The 100 ms tick is a private constant**, not configurable. Making it tunable is a contract
   change.
6. **Auto-explore does not descend**, by decision — `E` finishes a level and hands back control.
7. **No help screen.** Three new bindings (`E`, `w`+direction, `>`/`<` off the stairs) are
   documented only in the README.
8. Carried forward unchanged: `bool` dimensions accepted by the generator, no `KEY_RESIZE`
   handling, no monsters/items/combat/save-load.

## A note on the verification tooling

Both of the live session's initial "failures" were my own measurement bugs, not the product's:
I searched the status bar for a `Turns:` counter that **v3 removed**, and I measured
"cancellation latency" with a fixed drain window that always took its full duration. The real
signal was present in the first run (2572 redraw bytes while running, 0 after cancel); the
corrected instrument then reported 5 ms and ~6 turns/sec. As in v2 and v3, where the throwaway
tooling and the test suite disagreed, the tooling was wrong.

## Verify from scratch

```bash
.venv/bin/python -m pytest          # 1982 passed
.venv/bin/python main.py --seed 1234
```
