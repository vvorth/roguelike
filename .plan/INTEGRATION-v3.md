# INTEGRATION v3 — multi-level dungeon, stairs, UI chrome, event messages

Status: **complete**. Full suite **1666 passed, exit 0, zero skips**. Live curses session
verified. v2 was 1300 tests; v3 adds 366.

One real defect was found by the live session and is documented under **Known gaps** — the
last character of the status row is not drawn on a terminal exactly as tall as the frame.

## What was assembled

```
roguelike/events.py      T14  NEW    EventKind, Event, MESSAGES, message_for
roguelike/dungeon.py     T17  NEW    seed_for, level_for
roguelike/tiles.py       T13  amend  STAIRS_UP "<", STAIRS_DOWN ">", STAIRS, WALKABLE
roguelike/level.py       T13  amend  stairs_up, stairs_down (tuple), depth
roguelike/keys.py        T14  amend  CommandKind.DESCEND ">", ASCEND "<"
roguelike/generator.py   T15  amend  anchor room, seed-chosen spawn, stair placement
roguelike/render.py      T16  amend  Chrome, stats row, status row, height+2 frame
roguelike/game.py        T17  amend  depth, saved levels, descent/ascent, events, outcome
roguelike/world.py            frozen — unchanged, and needed no change
roguelike/style.py            frozen — unchanged, and needed no change
roguelike/fov.py              frozen — unchanged, and needed no change
roguelike/movement.py         frozen — unchanged, and needed no change
main.py                  orch  unchanged — imports only play(), whose signature is unchanged
tests/test_integration.py orch rewritten for the multi-level stack
```

| Suite | v2 | v3 |
|---|---|---|
| `test_level.py` | 85 | **117** |
| `test_keys.py` | 111 | **120** |
| `test_generator.py` | 414 | **564** |
| `test_render.py` | 101 | **119** |
| `test_game.py` | 167 | **259** |
| `test_events.py` | — | **51** |
| `test_dungeon.py` | — | **97** |
| `test_world.py` | 26 | 26 |
| `test_style.py` | 34 | 36 |
| `test_fov.py` | 97 | 97 |
| `test_movement.py` | 95 | 95 |
| `test_integration.py` | 170 | **85** |
| **Total** | **1300** | **1666** |

## Run it

```bash
.venv/bin/python main.py --seed 1234
```

Move with arrow keys, `hjkl` (`yubn` diagonals) or `1`–`9`. Walk into a door to open it.
Press `>` to descend a staircase, `<` to climb one. `<` on level 1 leaves the dungeon and ends
the run. `q` quits.

Real frames from the renderer, at 60×20:

```
|                                                            |   <- stats row, reserved
|              ###########                                   |
|              #....@....+                                   |
|              #.........#                                   |
|              +.........#                                   |
|              ###########                                   |
|                                          Level 1  Seed 1234|
```

After walking to the down-staircase and pressing `>`:

```
|                                                            |
|  ##############                                            |
|  #............+                                            |
|  #...@........#                                            |
|  ##############                                            |
|You descend to level 2.                   Level 2  Seed 1234|
```

**Descended at (6, 11), arrived at (6, 11), and level 2's up-staircase is (6, 11).** The player
does not move; the world changes underneath them.

## The four requirements

### Procedural multi-level generation
1. Spawn is seed-determined, on a floor cell at least one tile from any wall. **Measured: zero
   levels lack such a cell** (mean 282 per level, min 168).
2. The spawn **is** the up-staircase. Climbing it on level 1 ends the run with
   `You climb out of the dungeon and give up. Farewell.`, printed after the terminal is
   restored.
3. Exactly one down-staircase, in a **different room** from the up-staircase. `stairs_down` is
   a tuple — the shape is the branching scaffolding, and `seed_for(master, depth, branch)`
   already takes the branch index.
4. Descending generates the next level down, ADOM-style.
5. The level below is generated with its up-staircase **at exactly the coordinate descended
   from**.
6. The player keeps the same `(x, y)` across the transition.
7. Guaranteed by the **anchor rule**: a room containing that coordinate is placed *before all
   others*.
8. and 9. **Not implemented, and not needed** — see below.

### Requirements 8 and 9 were made unnecessary

You anticipated having to reshape rooms and delete orphaned doors after forcing a staircase
into place. Because the anchor room goes in *first*, nothing is ever carved after the fact:
no room changes shape and no door is orphaned. T15's implementation report gives the proof —
the anchor's placement interval is never empty, so there is no retry loop, no fallback and no
repair pass. The contract explicitly instructs workers **not** to write repair code.

Cost, measured: **10.92 → 10.88 rooms per level**, far better than the 10.87 → 9.71 the
research predicted, because the generator's existing layout-retry loop absorbs most of it.

### UI
Top row reserved and blank; bottom row carries the message on the left and `Level N  Seed S`
on the right, with the level and seed always winning if the two would collide.

### Events
Eight event kinds with wording in one table (`events.MESSAGES`). Adding one is a single enum
member, a single table entry and a single emission. Messages persist until another turn —
which falls out of the existing turn rule rather than needing new machinery, because a
rejected move consumes no turn and therefore leaves the state, including its events, untouched.

## Verification

### End-to-end (85 tests, orchestrator-owned)
Connectivity re-derived with an independent flood fill; both staircases proven to be valid
spawn cells *and* within the anchorable range; the v2 door rules re-checked against the
rewritten generator; five-level chains asserting each level's up-staircase equals the previous
level's down-staircase; cross-process seed determinism; a scripted walk asserting the player is
never in a wall and always on currently-passable ground; turn accounting across three outcome
kinds; fog monotonicity; `state.visible` compared against a direct `compute_visible` call; the
`y + 1` map offset; unexplored ground proven not to leak a glyph; stair and door glyphs in the
frame; and the 24-row budget.

### The persistence round trip
The highest-risk behaviour, checked independently of T17's own tests before its report arrived:
descend, explore, open doors, climb back — **level 1's `explored` and `open_doors` restored
exactly**, the saved `Level` returned as the same object, the player arriving on the staircase
they used; then descend again and **level 2's state also exactly preserved**. Fog does not
reset in either direction.

### Live curses session (80×24 pty)

| Check | Result |
|---|---|
| Starts, paints, exits 0 on `q` | pass |
| Stats row blank and reserved | pass |
| Fog of war | **110 / 1760** cells painted at open, 454 after descending |
| Stepping onto the staircase announces it | pass — `There is a staircase leading down here.` |
| `>` descends and reports it | pass — `You descend to level 2.` |
| Opened-door glyph `'` on screen | pass |
| Palette on the wire | **250, 238, 180, 94, 231** — all five |
| termios restored byte-identically | pass |
| ICANON / ECHO restored | pass |

## Deviations found

**None from any worker.** T17 — the first to see the whole v3 stack — re-derived every public
signature from the shipped code and found them all matching CONTRACT-v3.

Three **contract** imprecisions were found, all mine, all resolved sensibly and recorded in
`.plan/STATE-v3.md`:

1. **§7.1 says "if the new cell is a stair tile", but §10 v3 denies `game.py` the `tiles`
   import needed to ask.** Resolved by comparing coordinates, which G18 makes exactly
   equivalent — arguably the better design, since `game.py` stays free of tile vocabulary.
2. **§7.2 forbids `game.py` from formatting wording, while §7.1 requires it to set a string
   `outcome`.** Resolved by calling `events.message_for` for the farewell.
3. **§3.4 prose contradicts the §11 v3 table** on a non-int `depth`. The table wins:
   `TypeError` for non-int, `ValueError` for `< 1`.

And one **process** error of mine, caught by T15: CONTRACT-v3 §9 froze `tests/test_world.py`,
which contained `set(Tile) == {WALL, FLOOR, DOOR}` — an assertion v3 necessarily invalidates,
leaving no worker able to repair it. My grep for tile-count assertions missed that wording.
I extended it to the five-member vocabulary myself; `roguelike/world.py` remains untouched,
which was the substantive point of the freeze. T15 reported it rather than reaching outside
its ownership, which is exactly right.

## Known gaps

1. **The last character of the status row is not drawn on a terminal exactly as tall as the
   frame.** On 80×24, `Level 1  Seed 1234` paints as `Level 1  Seed 123`. **Confirmed real,
   not a tooling artefact:** a 25-row terminal shows it in full, and the headless renderer
   produces the correct string. The cause is `draw`'s bottom-right-cell guard, which is
   *required* — writing the last cell of the last row raises in curses. v2 never hit this
   because its frame was 23 rows on a 24-row terminal; v3's frame is exactly 24.
   **This matters more than it looks: it eats the last digit of the seed**, which is the one
   thing you need to replay a run. Two clean fixes, both contract changes:
   - right-align `status_right` one column short of the edge (costs nothing visible), or
   - drop `DEFAULT_HEIGHT` to 21 so the frame is 23 rows (costs one map row).
   Not fixed here because it needs a §4.2 amendment and the choice is the user's.
2. **A narrow level truncates messages.** The longest message is 38 characters; beside an
   18-character `Level N  Seed S` it needs 57 columns. At 40 columns it clips to
   `There are no stairs l`. Contractual — the level and seed always win — but it means 80
   columns is the *minimum* at which every message fits, not a generous default.
3. **Leaving the dungeon draws no final frame.** `run` exits the loop as soon as the game
   stops, so the farewell is printed by `play` after the terminal is restored, per §7.3.
4. **`saved` grows without bound** — one `LevelState` per depth visited. Measured trivial;
   eviction would break persistence.
5. **`GameState` is unhashable** because `saved` is a `dict`. Nothing needs it.
6. **`bool` dimensions** (v1 gap, unchanged) and **no `KEY_RESIZE` handling** (v1/v2 gap).
7. **Scope held.** No monsters, combat, items, inventory, save-load to disk, sound, score, or
   branch generation. Nothing invented for the stats row.

## A note on the verification tooling

The pty screen emulator used for the live session is a display aid with known limits: it does
not model curses' `erase()` against its own screen model, so the "after descending" picture it
prints superimposes frames from both levels. The authoritative frame is the renderer's own
output (shown above), and the authoritative behavioural checks are the 1666 headless tests plus
the targeted 24-vs-25-row experiment. Where the emulator and the tests disagreed, the emulator
was wrong every time — as it was in v2.

## Verify from scratch

```bash
.venv/bin/python -m pytest          # 1666 passed
.venv/bin/python main.py --seed 1234
```
