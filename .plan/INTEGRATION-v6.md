# INTEGRATION — v6: inventory, weapons, damage types, shields, consumables, chests

**Complete.** Full suite **3006 passed, 0 failed, zero skips** (v5 ended at 2615). Live curses
session verified under an 80×24 pty.

## The eight tasks

| Task | Owner | Suite | Verified by the orchestrator |
|---|---|---|---|
| T29 items and inventory | opus | 90 | yes |
| T30 keys and events | sonnet | 362 | yes |
| T31 regeneration effect | sonnet | 31 | yes |
| T32 resistance and shield roll | **orchestrator** | 64 | yes |
| T33 species resistances | sonnet | 142 | yes |
| T34 chests and loot | sonnet | 62 | yes |
| T35 chest glyph and screen | sonnet | 253 | yes |
| T36 wiring | opus | 627 + 123 | yes |

Every one was verified by running its suite **and** re-checking its load-bearing claims
independently, never on the worker's report alone.

## What measuring first bought

The research proposed numbers; sweeping them against the shipped modules **killed two proposals
before a line was written**:

- **Percentage resistances are unusable at these damage numbers.** On a 2–5 roll, 25% and 33%
  give the identical outcome set and 66% and 75% are both flat 1. Worse, the same 50%
  resistance yields average damage from **1.25 to 2.50 — a 2× swing — purely from where in the
  pipeline it sits.** Hence coarse tiers at one pinned point.
- **The proposed weapon tiers were both wrong.** Measured: 1–4 clears **2.2%** of floors, 2–5
  clears **45.6%**, 4–8 clears **98.3%**. One point of damage is worth 20–40 percentage points
  of survival. Shipped tiers are 2–4 / 2–5 / 3–5.
- Shields were cut from the proposed 15/25/35 to **10/18/25** on the same evidence.

## Three contract defects, all found by workers reporting rather than improvising

1. **§5.1 — the selection letters contradicted themselves.** §5 v6 said `a`–`t` selects an
   item; §7.17 said `e` equips and `d` drops. Both cannot be true of one keystroke. Resolved to
   `ITEM_LETTERS = "abcfghijklmnopqrstuv"`, so the printed letter *is* the key and nothing in a
   full pack is unreachable. The rejected alternative left items four and five silently
   unselectable.
2. **§27.4 — chests could not avoid monsters.** §27.2 required it, but `place_chest`'s binding
   signature has no monster list and §10 v6 forbids `loot.py` importing `npc.py`. Unsatisfiable
   by the module that owned it. The constraint moved to `game.py`, which places monsters after
   chests.
3. **§27.5 — consumables have no grade**, so the depth weights cannot select one. They are drawn
   at every depth; chests being the only item source, any other rule would make potions
   unobtainable outright.

Plus **CONTRACT-v5 §16.2**, correcting a stale wording row the orchestrator had left behind
when the interrupt rule widened in the previous increment.

## The seams, verified end to end by the orchestrator

- **Resistance reaches combat through the real turn loop.** Dagger (PIERCE) against the cave
  snake averages **1.51** damage; the club (BLUNT) averages **3.00**. Against the giant bat,
  vulnerable to BLUNT: dagger **3.51**, club **6.02**. A worker could unit-test resistance
  perfectly and still leave it unwired — only an end-to-end attack proves it arrives.
- **The design's central pressure works**: the club is worse than the dagger in general and
  better against the one thing that resists the dagger. That is why an inventory exists.
- **Bare-handed** deals exactly 1–2.
- **No monster starts on a chest** across every sampled seed. Chests appear at 14.5% against a
  contracted 12% (400 samples; within noise).
- **`loot.py`'s door detection was re-verified, not trusted.** Unable to import `world.py` or
  `tiles.py`, T34 proved that `is_walkable and not on_perimeter` is exactly "passable with no
  door open". Checked against the real `world.is_passable` over **183,285 walkable cells on 300
  levels: zero disagreements.**
- **v1's headline rule survives its sixth increment**: a rejected move consumes no turn and the
  world does not tick — the same state object comes back, with every monster's position, energy
  and hit points untouched.

## A test-design trap, recorded because it is easy to repeat

The obvious "a shield never subtracts" test — same seed, `shield_block=0` versus `25`, assert
equal damage — **is wrong and fails**, reporting 253 mismatches against correct code. The shield
roll consumes a draw and shifts the rng stream, so the two calls legitimately roll different
damage. The correct check is distributional: identical value set `{2,3,4,5}`, means 3.515 versus
3.516. Both assertions ship with a comment explaining why.

## Live session (80×24 pty)

Starts and draws the map; the stats row carries the health band; `i` opens an inventory screen
reading `Melee: dagger  Ranged: shortbow  Shield: -`; `?` lists the new bindings; `x` describes
the player. Auto-explore ran, bumped a door open, sighted a monster and stopped with
`The door opens. There is a giant bat in view.` — v5's interrupt rule and v6's rendering in one
frame. Quit cleanly.

## Known limitations, accepted and recorded

- **`ranged_block_chance` and `NPC_SHIELD_BLOCKED` have no live caller.** Nothing shoots at the
  player and no monster carries a shield. Both are wired at the one site that could ever reach
  them and tested directly — the position `interruption` held in v4.
- **`Resistance.IMMUNE` has no live user.** The tier exists and is tested; no shipped species is
  immune to anything.
- **A chest can be placed on a down-staircase.** `place_chest` excludes doors and the safe
  radius, not stairs. Harmless — both report themselves — but likely unintended by §27.2.
- **A fine shield makes the current bestiary easy** (85.8% floor clears against 45.6% bare). The
  user accepted this explicitly in exchange for a mechanic that keeps scaling.
- **Chest placement subtracts monsters after the fact** rather than excluding cells during
  placement, because `spawn_npcs` has no forbidden-cell parameter. Fine for one chest; an
  `occupied` parameter is the clean fix if v7 wants several.

## Out of scope, unchanged

No food, no hunger. No armour, weight, encumbrance or stacking. No identification, curses or
enchantments. No shops. No monster drops — chests are the only source. No `Tile.CHEST`; the
generator was never touched. Ammunition remains infinite.
