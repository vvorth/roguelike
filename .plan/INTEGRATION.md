# INTEGRATION — Phase 5

Status: **complete**. Full suite **912 passed, exit 0**. Live curses session verified.

## What was assembled

Six worker-built modules plus two orchestrator-owned files, wired into a runnable game.

```
main.py                  orchestrator   CLI: --seed / --width / --height
roguelike/tiles.py       T01            Tile, TILE_CHARS, WALKABLE, PLAYER_CHAR
roguelike/level.py       T01            Room, Level (frozen), freeze_grid, blank_grid
roguelike/keys.py        T04            CommandKind, Command, translate_key
roguelike/generator.py   T02            generate_level(seed, width, height, max_rooms)
roguelike/render.py      T03            render_to_lines (pure), draw (curses blitter)
roguelike/movement.py    T05            MoveResult, try_move, is_blocked
roguelike/game.py        T06            GameState, new_game, step, format_status, run, play
tests/test_integration.py orchestrator  95 cross-module end-to-end tests
```

Every file in CONTRACT §9 has exactly one owner and exactly one author. No worker created a
file outside its allocation, and no worker edited another's — verified by file inventory after
each wave.

### Test totals

| Suite | Tests | Owner |
|---|---|---|
| `test_level.py` | 85 | T01 |
| `test_keys.py` | 111 | T04 |
| `test_generator.py` | 381 | T02 |
| `test_render.py` | 58 | T03 |
| `test_movement.py` | 67 | T05 |
| `test_game.py` | 115 | T06 |
| `test_integration.py` | 95 | orchestrator |
| **Total** | **912** | |

## Run it

```bash
.venv/bin/python main.py --seed 1234
```

Move with arrow keys, `hjkl` (`yubn` for diagonals), or `1`–`9`. Quit with `q`. The seed is
printed on exit so any level can be replayed.

## End-to-end verification (the checks no individual worker could run)

Each worker only ever saw its own module plus the frozen core types. These cross the seams:

1. **Connectivity, independently re-derived.** `tests/test_integration.py` implements its own
   BFS flood fill rather than trusting the generator's internal self-check, then asserts the
   reachable set from `player_start` *equals* the full walkable set — across 9 seeds and 3 map
   sizes, and again per-room cell-by-cell. A generator whose own connectivity check was buggy
   could not hide here.
2. **Scripted walk through the real input abstraction.** Raw key characters go through
   `keys.translate_key` into `game.step` — the same path `run` uses with `getch`, minus the
   terminal. After **every single keystroke** the player is asserted in bounds and on a
   walkable tile, over 144 keystrokes × 9 seeds. The script mixes `hjkl`, diagonals, numpad
   digits and an unbound key.
3. **Turn accounting across the seam.** `turns` is asserted equal to the count of accepted
   moves after every keystroke, plus an assertion that the walk *was* blocked at least once —
   otherwise the test would prove nothing.
4. **Reversibility.** Every accepted move is undone by its opposite and the player returns to
   the exact start position. This catches an asymmetric collision or a sign error that a
   one-directional test would miss.
5. **Layer agreement.** `game.step`'s result is compared against a direct `movement.try_move`
   call, proving `step` delegates rather than reimplements collision.
6. **Frame fidelity.** For every state in a walk, the rendered frame is checked cell by cell:
   `height + 1` lines, every line exactly `width` chars, exactly one `@`, and every non-player
   cell equal to `TILE_CHARS[level.tile_at(x, y)]`.
7. **Cross-process determinism.** The same seed is regenerated in three fresh interpreters
   under `PYTHONHASHSEED` 0, 1 and 424242, and the rendered maps must match byte for byte.
8. **Whole-stack immutability.** A `deepcopy` of the level taken before a 72-keystroke walk
   still compares equal afterwards, and `state.level is level` throughout.
9. **Clean failure.** `main.py --width 3 --height 3` exits 2 with a message and no traceback —
   and fails *before* curses is ever initialised.
10. **No terminal on import.** A subprocess imports `main` and all seven modules and asserts
    `curses.LINES` is unset, i.e. nothing initialised a terminal as an import side effect.

### Live curses session

Driven inside a real 80×24 pseudo-terminal (`pty`), out of band — deliberately **not** in the
pytest suite, since the project constraint is that headless runs never initialise curses.

| Check | Result |
|---|---|
| Session starts and paints a full frame | pass — 2034 bytes, map + status bar |
| Responds to movement keys | pass — redraws emitted per keystroke |
| Player moves correctly on screen | pass — `(6, 5)` → `(6, 8)` after `jjj`, matching the status bar |
| Blocked move consumes no turn, live | pass — `lll` into a wall left `Turns: 3` |
| Quits on `q` | pass — exit code 0 |
| Terminal restored | pass — termios attributes byte-identical to before the session |
| Canonical mode and echo restored | pass — `ICANON` and `ECHO` both set |
| No traceback on the session stream | pass |

The captured opening frame (seed 1234) shows rooms, corridors, `+` doors and the `@` at the
status bar's reported position:

```
################################################################################
##################........######################################################
#...........######........##.....################......#####.........###########
#.....@.....+...##........++.....################......#####.........###########
######+########.##........##.....########....####......+...+.........+......+###
#.........###....+.+......++.....+..............................+#########....##
################################################################################
Seed: 1234  Pos: (6, 5)  Turns: 0  [q] quit
```

## Deviations found

**None.** No worker reported a contract deviation, and T06 — the only worker to see all six
modules at once — reported that every signature, field order, default, exception type and
documented edge case matched CONTRACT.md as written, with no adaptation needed at any seam.
The integration suite was written against the contract, not against the code, and passed on
its first run. That is the strongest available evidence the contract was specified tightly
enough for workers who never spoke.

Two verification-tooling bugs were found and fixed during Phase 5, both **in the orchestrator's
own throwaway test harness, not in the project**:
- The pty smoke test tried to read the turn counter out of the byte stream. Curses does
  *differential* screen updates and never re-emits a whole status line, so this was
  unreadable by construction. Replaced with a responsiveness check; turn-counter correctness
  is owned by the headless suite, which tests it exhaustively.
- The screen-reconstruction tool treated `\n` as CR+LF. Curses disables `ONLCR`, so a bare
  linefeed moves down while *preserving the column* — a real redraw is
  `ESC[6;7H . \n \x08 @`. The tool was placing every diffed glyph in column 0 and appeared to
  show a misplaced player. The game was correct throughout.

## Decisions taken at integration (recorded, not re-dispatched)

- **`bool` for `width`/`height`/`max_rooms`** (T02 open question 4). §3.1 rejects a `bool`
  *seed* explicitly but says only "not `int` → `TypeError`" for the dimensions, and `bool` is
  an `int` subclass. T02 took the literal reading: `width=True` passes the type check and then
  fails the size check with `ValueError`; `max_rooms=True` behaves as `1`. Accepted — the
  stricter reading would contradict the §11 row for too-small dimensions, and no caller in the
  codebase passes a bool. See "Known gaps".
- **Zero-delta precedence** (T05). §6's "walkable target → `moved=True`" and "`(0,0)` →
  `moved=False`" overlap when standing on floor. T05 made the zero-delta rule win. Correct:
  §5 guarantees a `MOVE` never carries `(0, 0)`, so the two layers agree redundantly rather
  than one relying on the other.
- **Status-bar clipping** (T03, T06). On a terminal exactly as tall as the map, the status
  line is the row that clips. Matches §4 as written; left unchanged. `main.py` documents that
  a height-`H` map needs an `H+1`-row terminal.
- **`player_start` room identity.** It is `rooms[0].center` today, but G10 promises only
  "inside some room and walkable". The integration tests assert the guarantee, not the
  implementation.

## Known gaps

These are bounded and deliberate. None blocks the stated scope.

1. **`bool` dimensions.** `generate_level(1, width=True)` raises `ValueError` ("too small")
   rather than `TypeError`. A one-line change to `_validate_arguments` if the stricter reading
   is ever wanted; it would require a CONTRACT §3.1 amendment first.
2. **`max_rooms` is not exposed on the CLI.** `play()`'s signature is fixed by §7 to
   `(seed, width, height)`, so threading it through would be a contract change.
3. **No terminal-size check before launch.** A terminal smaller than the map renders a clipped
   view rather than warning. `render.draw` clips safely and never raises (§4, BRIEF Q15), so
   this degrades rather than fails — but it is a usability gap, not a designed feature.
4. **No resize handling.** `KEY_RESIZE` is not bound; it falls through to `UNKNOWN` and is
   ignored, so the frame does not re-lay-out on resize. Out of scope as written, and safe
   because `draw` re-clips every frame.
5. **`Ctrl-C` prints a short message and exits 130.** The terminal is restored first by
   `curses.wrapper`, so this is clean, but it is not a graceful in-game confirm.
6. **Scope boundaries hold.** No monsters, combat, items, inventory, FOV, save-load, multiple
   levels, stairs, sound, or colour — and no speculative stubs for any of them. T06 asserts by
   source inspection that `game.py` has no `entities`, `score`, `game_over`, `menu`, `save` or
   `load` attribute.

## Resume/verify from scratch

```bash
.venv/bin/python -m pytest          # 912 passed
.venv/bin/python main.py --seed 1234
```

The `.venv` is required: system `python3` is 3.9.6, below the project's 3.10 floor. It holds
Python 3.14.6 and pytest 9.1.1 (see BRIEF, "Environment finding").
