# RESEARCH — v2: colours, fog of war, visibility, door constraint

Phase 0 recon for the second increment. **No code written, no workers spawned.** Every number
below is measured on the current build, not estimated.

---

## 1. The door bug — confirmed, root cause identified

### Measurement

Scanned **400 seeds / 8318 doors** against the stated constraint ("opposite sides of a door
must be adjacent to walls with their sides"):

| Reading | Doors satisfying | Violations |
|---|---|---|
| **Loose** — walls on one axis (`W·W` horizontally *or* vertically) | 7228 (86.9%) | 1090 |
| **Strict** — walls on one axis **and** passage on the other | 7228 (86.9%) | 1090 |

**The two readings coincide exactly.** Every door that has walls on one axis also has open
floor on the other, so there is no ambiguity to resolve — one rule, one number: **13.1% of
doors are malformed.**

Separately: **2525 doors are adjacent to another door**, which is the same defect seen from a
different angle.

### Root cause

Every violating door sits at a **corner junction**, not in a wall run:

```
seed 0, bad door at (58,16)     seed 2, bad door at (32,14)
   .........                       ####.....
   .........                       ####.....
   ####+....   ← door              +...+....   ← door, floor on BOTH sides
   #########                       #####....
```

`_carve_corridor` (`roguelike/generator.py:253`) turns a corridor cell into a door whenever
`_is_wall_piercing` says it lies on a room perimeter and steps into that room's floor. That
test never checks the cells *flanking* the door along the wall. Corridors are centre-to-centre
L-shaped doglegs, so when an elbow lands near a room corner the path clips the perimeter
diagonally and produces a door with open floor on one side.

### Two fixes, measured

| Option | Change | Cost |
|---|---|---|
| **A — demote** | Reject the door in `_is_wall_piercing`; carve `FLOOR` instead | 1 line. But **6.3% of rooms (271/4280) end up with no door at all** — entered through an unmarked corner gap |
| **B — reroute (root cause)** | Make corridors approach room walls **perpendicularly**, entering through a perimeter cell whose flanking wall cells are both `WALL` | Real change to `_connect_rooms`. Every room keeps a proper door; no corner gaps |

**Recommendation: B.** A is a one-line patch that trades a malformed door for a missing one —
it satisfies the constraint's letter while making the map worse. B fixes what actually
produces the defect. It is contained to the generator and G8 connectivity is unaffected either
way (the cells stay walkable).

This also tightens CONTRACT §3 **G9**, which currently says only "every door lies on some
room's perimeter". It gains: *and has wall cells on both sides along one axis, and walkable
cells on both sides along the other.*

---

## 2. FOV algorithm — rule #5 is the deciding constraint

> "a symbol is considered visible if any side or corner is in direct eye sight"

That sentence rules out the standard roguelike algorithm. Recursive shadowcasting tests
**tile centres**; rule #5 tests **sides and corners**. That is the textbook definition of
**permissive field of view**. Three candidates were prototyped and measured on real levels.

| Algorithm | Cells visible (seed 1234, r=20) | Time / move | Faithful to rule #5? |
|---|---|---|---|
| Recursive shadowcasting | 130 | **0.10 ms** | No — misses 12 cells |
| **Permissive (8 points per cell)** | **142** | **15.29 ms** | **Yes — by construction** |
| Hybrid: shadowcast + reveal touching walls | ~142 | 0.16 ms | **No** — see below |

### Permissive is a strict superset

Measured against shadowcasting: **12 cells visible only to permissive, 0 visible only to
shadowcasting.** The extra cells are wall corners and the corridor mouth:

```
legend: @ player | X both | + ONLY permissive | # unseen wall
  XXXXXXXXXXXXX+####........####################
  XXXXXXXXXXXXXXXXX#........##.....#############
  XXXXXX@XXXXXXXXXX+........++.....#############
  XXXXXXXXXXXXXXXXX+........##.....#############
  XXXXXXXXXXXXX+#.##........##.....#############
  #####XXX+++####.##........##.....#############
```

Those `+` cells matter visually: shadowcasting leaves **holes in the walls of the room you are
standing in**, the classic ragged-wall artifact. Rule #5 exists precisely to avoid it.

### Why the fast hybrid was rejected

Reveal every wall touching a visible floor cell — 100× faster than permissive. But over 40
seeds it **over-shows 6.8%** and **misses 6.1%** of rule-#5 cells. Over-showing is
disqualifying: it reveals wall segments *around corners the player cannot see*, leaking map
information. A cheap approximation that leaks is worse than a slow rule.

### Cost is a non-issue

15.29 ms per move sounds heavy next to 0.10 ms, but the budget here is a **human keypress**,
not a frame. Nothing is perceptible below ~100 ms, so permissive uses ~15% of the budget with
no monsters competing for it. **Correctness wins; take permissive.**

Caveat for later: this is 150× the cost of shadowcasting, so if per-entity FOV ever arrives
(monsters are currently out of scope) the choice must be revisited. Noted, not designed for.

### Radius 20 barely differs from radius 8

| Radius | Cells visible |
|---|---|
| 8 | 115 |
| 20 | 142 |

Indoors, **walls dominate — not the radius**. Radius 20 on an 80×22 map is larger than the map
is tall, so it only bites down long corridors. This is fine and matches "may change it later";
flagging it so the number is a deliberate choice rather than an accident. The radius must be a
parameter, not a constant.

### Symmetry

My permissive prototype shows 1.3% asymmetric pairs (A sees B but B does not see A). **Irrelevant
today** — with no monsters, nothing needs to see the player back. It would matter the moment
anything else gets an FOV.

---

## 3. Colours — capability confirmed on this terminal

Probed under a real pty (`TERM=xterm-256color`):

```
has_colors=True   COLORS=256   COLOR_PAIRS=32767   can_change_color=True
use_default_colors() → OK      all 5 proposed pairs allocated OK
```

256 colours means "darker shade of the original" can be a genuinely different colour rather
than a dim attribute. Proposed palette, all verified allocatable:

| Element | Visible | Explored (dimmed) |
|---|---|---|
| Wall `#`, floor `.` — light gray | **250** | **238** |
| Door `+` — light brown | **180** | **94** |
| Player `@` — white **bold** | **231** + `A_BOLD` | n/a (always visible) |
| Unexplored | not drawn — blank space | — |

Fallback matters: an 8-colour or monochrome terminal must degrade, not crash. Recommended
ladder — 256-colour palette → 8-colour (`COLOR_WHITE`/`COLOR_YELLOW`) with `A_DIM` for
explored → monochrome (`A_BOLD` visible, `A_DIM` explored). Detected once at startup via
`curses.COLORS`, never per frame.

---

## 4. Architectural impact — this breaks the frozen contract in two places

Not a problem, but it must be explicit and versioned rather than quietly patched.

### 4.1 The renderer signature must change (§4)

Today: `render_to_lines(level, player_pos, status) -> list[str]`

Two independent reasons it can no longer hold:
1. It has no way to receive **visible** and **explored** state.
2. `list[str]` **cannot carry colour.** A string has no per-cell attributes.

Proposal: the renderer returns a **frame of styled cells** (`char` + `style` enum), with a
`to_plain_lines()` helper preserving the existing plain-text view. That keeps every §4 test
that asserts exact strings working, and keeps the pure/blitter split — `draw` gains only the
`color_pair` lookup. The no-mutation guarantee is untouched.

### 4.2 `explored` is persistent mutable state, and `Level` is frozen (§0.5, §2.2)

`Level` is a frozen dataclass with a tuple grid, deliberately, so the renderer *cannot* mutate
it. Explored-ness must not go there.

Proposal: it lives in **`GameState`** as `explored: frozenset[tuple[int, int]]`, alongside a
derived `visible: frozenset[tuple[int, int]]`. This fits the existing design exactly — `step`
is already a pure function returning a new state, so `explored = old.explored | visible` is a
one-line transition, and rewinding/testing stays trivial. At 1760 cells the frozenset rebuild
is free.

### 4.3 Two BRIEF non-goals are being reversed

BRIEF v1 lists "field of view / lighting" and "colour beyond basic terminal defaults" as
explicit non-goals. This request reverses both. That is entirely the user's call — recording it
as a **deliberate scope change**, not a contract violation, so the history stays honest.

### 4.4 Provisional module layout

| File | Owner | Change |
|---|---|---|
| `roguelike/fov.py` | **new task** | `compute_visible(level, origin, radius) -> frozenset` — pure, no curses |
| `roguelike/style.py` | **new task** | Style vocabulary, palette, capability detection |
| `roguelike/render.py` | rewrite | Styled frame; `draw` gains colour pairs |
| `roguelike/game.py` | amend | `GameState.explored` / `.visible`; recompute FOV in `step` |
| `roguelike/generator.py` | amend | Door constraint (fix B) |
| `CONTRACT.md` | **v2** | §3 G9, §4, §7 amended; new §13 FOV, §14 style |

Roughly **5 tasks in 3 waves** — `fov.py` and `style.py` are independent and parallel; render
and game depend on both; the generator fix is independent of all of it.

---

## 5. Open questions — ANSWERED

| # | Question | Decision |
|---|---|---|
| 1 | "Real time" semantics | **Turn-based, FOV recomputed per move.** `getch` stays blocking; the existing pure `step` design survives intact |
| 2 | Do doors block sight? | **Opaque, and doors gain open/closed state.** Closed blocks sight and movement; open blocks neither |
| 3 | Colours item 7 | **Nothing** — the list ended at 6 |
| 4 | Door constraint fix | **B — reroute corridors perpendicular to walls** |

Radius 20 taken as given, parameterised, with §2's finding noted.

---

## 6. Consequence of open/closed doors — a new seam is needed

Decision 2 is the one with architectural reach, and it breaks an invariant the v1 contract
leans on throughout.

### The invariant it breaks

Today, **walkability is a pure function of `Level`**: `Level.is_walkable(x, y)` answers
completely from the frozen terrain, and `movement.try_move` is built on exactly that one
predicate — which is what collapsed walls, borders and off-map into a single rejection branch
and kept `movement.py` at ~40 lines.

A closed door is *terrain-walkable but currently impassable*, and its state changes during
play. So passability is no longer derivable from `Level` alone. Door state cannot live in
`Level` either — `Level` is frozen by design, generated once.

### Resolution: one home for both runtime predicates

FOV and movement need the same two questions answered about the world *as it currently
stands*, so they should not each grow their own copy of the rule:

```python
# roguelike/world.py  — pure, no curses, no state of its own
def is_passable(level: Level, open_doors: frozenset[tuple[int,int]], x: int, y: int) -> bool
def is_transparent(level: Level, open_doors: frozenset[tuple[int,int]], x: int, y: int) -> bool
```

- `is_passable` — terrain walkable **and** not a closed door.
- `is_transparent` — not a wall **and** not a closed door. (These now differ, which is exactly
  why FOV needs its own predicate rather than reusing `is_walkable`.)

`Level.is_walkable` keeps its current meaning — *terrain* walkability — and every existing
test of it stays valid. `movement.try_move` and `fov.compute_visible` both gain an
`open_doors` parameter. State stays in `GameState` alongside `explored`, and `step` stays pure.

### Opening a door: bump-to-open, no new keybinding

Two ways to open a door: an explicit `o`+direction command, or walking into it. **Recommending
bump-to-open** — moving into a closed door opens it, consumes the turn, and leaves the player
in place; the next move walks through. It is what ADOM and NetHack do by default, it needs no
new key and no new command kind, and it delivers the full open/closed semantics you asked for.
An explicit `o` command remains a trivial later addition if you want it.

This means `MoveResult` gains a way to say *"blocked by a closed door at (x, y)"* so `step` can
convert that into an open — a small, contained amendment to CONTRACT §6.

### Tile vocabulary

Door glyphs follow the genre convention: **`+` closed, `'` open**. This is a rendering concern
driven by state, not a new `Tile` member — the grid keeps one `Tile.DOOR` and the glyph is
chosen at render time from `open_doors`. That keeps the generator, `Level` equality, and every
existing determinism test untouched.

### Revised module layout

| File | Owner | Change |
|---|---|---|
| `roguelike/world.py` | **new** | `is_passable`, `is_transparent` — the shared runtime seam |
| `roguelike/fov.py` | **new** | `compute_visible(level, open_doors, origin, radius) -> frozenset` |
| `roguelike/style.py` | **new** | Style vocabulary, 256/8/mono palette ladder, capability detection |
| `roguelike/render.py` | rewrite | Styled frame; visible / explored / unexplored; open-door glyph |
| `roguelike/game.py` | amend | `GameState.explored` / `.visible` / `.open_doors`; FOV per move; bump-to-open |
| `roguelike/movement.py` | amend | `open_doors` parameter; closed-door rejection reason |
| `roguelike/generator.py` | amend | Door constraint (fix B) |
| `CONTRACT.md` | **v2** | §3 G9, §4, §6, §7 amended; new §13 world, §14 FOV, §15 style |

**~6 tasks in 3 waves.** `world.py`, `style.py` and the generator fix are independent and
parallel; `fov.py` depends on `world.py`; `render`, `movement` and `game` depend on the rest.
