# RESEARCH — v4: diagonals, multi-turn actions, pathfinding, auto-navigation

Phase 0 recon. **No code written, no workers spawned.** Every number is measured on the
current build (v3, 1666 tests green), not estimated.

---

## 1. Diagonal movement — half of this already exists

**Numpad `1`, `3`, `7`, `9` are already implemented** and have been since v1, along with
`yubn`. Verified on the current build:

| Key | Result | | Key | Result |
|---|---|---|---|---|
| `7` | (-1,-1) NW | | `y` | (-1,-1) NW |
| `9` | (1,-1) NE | | `u` | (1,-1) NE |
| `1` | (-1,1) SW | | `b` | (-1,1) SW |
| `3` | (1,1) SE | | `n` | (1,1) SE |

So requirement 1's first bullet is **done**; only the Shift bindings are new.

### Shifted arrows do reach curses — measured

The real question was whether a terminal even delivers Shift+Arrow distinguishably. Probed by
writing raw sequences into a pty and reading `getch` in the child:

| Key | Sequence sent | `getch` returns | curses name |
|---|---|---|---|
| Shift+Up | `ESC[1;2A` | **337** | `KEY_SR` |
| Shift+Down | `ESC[1;2B` | **336** | `KEY_SF` |
| Shift+Right | `ESC[1;2C` | **402** | `KEY_SRIGHT` |
| Shift+Left | `ESC[1;2D` | **393** | `KEY_SLEFT` |

`terminfo` on this machine carries `kUP`/`kDN`/`kLFT`/`kRIT`, which is what lets ncurses fold
those sequences into single key codes. Note the names for up/down are `KEY_SR`/`KEY_SF`
("scroll reverse/forward") — historically the shifted-arrow codes, and confusingly named, so
the contract must state them explicitly.

`H`, `J`, `K`, `L` arrive as plain ASCII 72/74/75/76, so Shift+hjkl needs nothing special.

### The 45°-clockwise mapping

| Base | Direction | Rotated 45° CW | Delta | Keys |
|---|---|---|---|---|
| Up | N | **NE** | (1,-1) | Shift+Up (`KEY_SR`), `K` |
| Right | E | **SE** | (1,1) | Shift+Right (`KEY_SRIGHT`), `L` |
| Down | S | **SW** | (-1,1) | Shift+Down (`KEY_SF`), `J` |
| Left | W | **NW** | (-1,-1) | Shift+Left (`KEY_SLEFT`), `H` |

Two things worth knowing rather than deciding:

- **This overrides a genre convention.** In NetHack and ADOM, `HJKL` means *run* in that
  direction. Since v4 also adds running (`w` + direction), nothing is lost functionally, but
  anyone with roguelike muscle memory will be surprised. You asked for it explicitly; recording
  it so it is a choice, not an accident.
- **Shifted arrows depend on terminfo.** On a terminal whose entry lacks `kUP`/`kDN`, the
  sequences arrive as raw `ESC [ 1 ; 2 A` and would read as garbage. `HJKL` always works, so
  the recommendation is: bind both, document `HJKL` as the portable path, and do **not** write
  a hand-rolled escape parser.

---

## 2. Multi-turn actions — one mechanism gives both requirements

You asked for a 10-turns-per-second cap *and* instant cancellation on any keypress. Those look
like two mechanisms (a timer and a poll). They are one: `stdscr.timeout(100)`.

Measured in a pty:

| Check | Result |
|---|---|
| `timeout(100)`, 1.0 s with no input | **9 ticks** (≈10/sec, as asked) |
| A key already waiting | returned in **0.00 ms**, not 100 ms |
| `timeout(-1)` for normal play | blocks as before |

So the loop asks for a key with a 100 ms deadline: if one arrives, the activity is cancelled
immediately; if not, one turn is taken. No `sleep`, no busy-wait, and cancellation latency is
zero. Normal (non-activity) play switches back to `timeout(-1)` so the game does not spin.

### Keeping `step` pure

The engine's turn logic is a pure function and the whole test suite depends on that. Timing must
not leak into it. Proposal:

- `GameState.activity: Activity | None` — a small frozen value saying what is in progress
  (auto-explore, travel to a coordinate, auto-walk in a direction).
- `advance(state) -> GameState` — **pure**, performs exactly one turn of the current activity
  and clears `activity` when it finishes or is blocked. Fully testable headless; the whole of
  auto-explore can be unit-tested with no terminal.
- `run` owns the clock and the keyboard, and nothing else.

### The interrupt seam you asked for

Requirement: "automatically cancelled by the game engine on certain events we will implement in
the future (seeing a hostile, receiving damage, character state change)".

Nothing today can trigger those. The cheapest honest scaffolding is a single pure predicate:

```python
def interruption(before: GameState, after: GameState) -> Event | None: ...
```

called by `advance` after each turn; returning an `Event` stops the activity and reports why.
Today it returns `None` always, with the door-opening case as the one live example if you want
it (see open questions). When monsters arrive, that function grows a branch — no new
architecture, no observer registry.

---

## 3. Pathfinding — A\* with octile distance, and it is cheap

Measured on a full 80×22 level, start to the far staircase:

| | Result |
|---|---|
| A\* with 8 directions, octile heuristic | **0.235 ms** per full-level search |
| Path length with diagonals | **58 steps** |
| Shortest 4-directional path | 65 steps |

Diagonals save ~11% of the walk and cost nothing measurable. At 10 turns/second, a 0.235 ms
search is 0.2% of one turn's budget, so **re-planning every single turn is affordable** — no
path caching or incremental replanning needed, and none should be written.

Diagonal cost is √2 and orthogonal 1, so paths prefer straight lines and only cut corners where
it genuinely helps. The engine has no corner-cutting restriction (a diagonal move is legal iff
the destination is passable), so the pathfinder and the movement rules already agree.

---

## 4. Auto-explore — frontier search, and it reaches 100%

The rule you gave is the important one: **no peeking at the unexplored map**. The classic
robotics answer fits exactly — repeatedly walk to the nearest *frontier*, where a frontier is an
explored, reachable cell that touches something unexplored.

Prototyped against the real engine, driving every move through the real `step`:

| Seed | Walkable explored | Turns | Re-plans | Stop reason |
|---|---|---|---|---|
| 1234 | **100.0%** | 165 | 43 | nothing left to explore |
| 7 | **100.0%** | 158 | 36 | nothing left to explore |
| 42 | **100.0%** | 222 | 38 | nothing left to explore |
| 2026 | **100.0%** | 116 | 35 | nothing left to explore |
| 0 | **100.0%** | 218 | 42 | nothing left to explore |

Mean 0.36 s of compute for a whole level (~2 ms per turn, against a 100 ms budget), and every
run terminated by exhausting frontiers rather than hitting a cap.

Two details that make it work, both non-obvious:

- **A closed door must count as traversable when planning**, or the frontier behind it is
  permanently unreachable and auto-explore stalls in the first room. Bumping opens it, so
  planning through it is honest.
- **A closed door is itself a frontier**, because it hides whatever is beyond regardless of
  what is mapped around it.

Coverage is 100% because the current generator has no genuinely hidden areas. Your "even if
there are hidden rooms known to the game engine" clause costs nothing today and is satisfied by
construction — the algorithm only ever reads `explored`.

---

## 5. Travel to the nearest known staircase

`>` or `<` pressed while *not* standing on the matching staircase becomes a travel order:
search `explored` for stair cells of that kind, A\* to the nearest, and walk there as an
activity, stopping **on** the stair.

The interesting case is when no such staircase has been discovered yet — see open questions.

---

## 6. Auto-walk and corridor following — a purely local rule works

You asked that following a corridor stop when it "widens (into a room, stop just before
entering)" or at "a join/intersection". Both need a definition of *corridor* that does not read
`level.rooms`, since that is engine knowledge.

**Proposed test: a cell is _wide_ if it belongs to any 2×2 block of walkable cells.** Measured
against true room membership over 40 levels, 23,843 cells:

| | Count |
|---|---|
| Room cells correctly wide | 21,813 |
| Room cells wrongly thin | **0** |
| Corridor cells correctly thin | 2,026 |
| Corridor cells wrongly wide | **4** |
| **Accuracy** | **99.98%** |

Zero false negatives, and the four false positives are places where two corridors run alongside
each other — genuine local ambiguity, not a flaw. (An earlier two-quadrant version of this test
scored only 96.4%; all four quadrants must be checked.)

Degree — the count of walkable orthogonal neighbours — separates the rest:

| Degree | Corridor cells | Room cells | Doors |
|---|---|---|---|
| 2 | 1891 | 1312 | 635 |
| 3 | **139** | 8933 | 0 |
| 4 | 0 | 11568 | 0 |

So: **thin + degree ≥ 3 is an intersection** (139 of them across 40 levels — they exist and are
detectable). Doors are always degree 2, a direct consequence of the v3 door constraint.

### The resulting rule

Starting from `w` then a direction:

- **In the open** (current cell wide): step in the given direction until the next cell is not
  passable.
- **In a corridor** (current cell thin): step to the single thin walkable neighbour that is not
  where you came from. Stop when there is more than one candidate (**intersection**), or when
  the next cell is wide (**stop before entering the room**), or when there is nowhere to go.
- **Doors**: bump to open and keep going, in both modes.

`w` + direction is necessarily a **two-keystroke prefix** — a terminal cannot observe key
release, so "press w, release w, press direction" is exactly a prefix command.

---

## 7. Contract impact

| File | Change |
|---|---|
| `roguelike/pathfind.py` | **new** — A\*, octile, the wide/thin/intersection predicates |
| `roguelike/activity.py` | **new** — `Activity` values and the pure `advance` |
| `roguelike/keys.py` | amend — shifted arrows, `HJKL`, `w` prefix, `E` |
| `roguelike/events.py` | amend — travel/explore/interrupt messages |
| `roguelike/game.py` | amend — `activity` field, prefix state, `advance` wiring, timed loop |
| `roguelike/world.py` | possibly amend — a "planning-passable" predicate that admits closed doors |
| `roguelike/render.py`, `level.py`, `tiles.py`, `generator.py`, `fov.py`, `movement.py`, `style.py` | **no change** |

Roughly **5–6 tasks in 3 waves**. Notably the renderer and the generator are untouched: this
increment is all input, planning and control flow.

---

## 8. Open questions — ANSWERED

| # | Question | Decision |
|---|---|---|
| 1 | Auto-explore when the level is done | **Stop on the current level.** Report it and hand control back; never descend on its own |
| 2 | `>` with no down-staircase discovered | **Say so and do nothing.** No movement, no turn consumed |
| 3 | Does opening a door interrupt an activity | **No.** The door opens, costs its turn, reports `The door opens.`, and the activity continues |

### Consequences

- **Each command does one thing.** `E` explores, `>` travels or reports, `w`+direction walks.
  None of them silently becomes another, which keeps every activity's stopping condition
  something the player can predict before pressing the key.
- **The interrupt seam ships with no live conditions.** Decision 3 means `interruption(before,
  after)` returns `None` in every case today. That is the honest state of it: the seam exists,
  is called every turn, is tested with a stub condition, and grows a branch when monsters
  arrive. Nothing pretends to be more finished than it is.
- **Auto-explore still opens doors** — it must, or frontiers behind them are unreachable. It
  simply does not *stop* for them.

### Remaining decisions I am making myself

- **Stop conditions get their own messages**, so the player always knows why an activity ended:
  finished, blocked, arrived, interrupted by a keypress.
- **A keypress that cancels an activity is consumed by the cancellation** and does not also act
  as a command. Otherwise a panicked keypress would cancel *and* move you.
- **Activities do not survive a level change.** Descending clears any activity.
- **`w` followed by a non-direction key** cancels the prefix and reports nothing — it is a
  typo, not an error.
