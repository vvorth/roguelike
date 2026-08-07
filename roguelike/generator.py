"""Procedural dungeon generation — one seed in, one fully connected ``Level`` out.

Algorithm
---------
**Rooms**: random placement with rejection sampling. A bounded number of candidate
rectangles are drawn from the local RNG; a candidate is kept only if it lies inside the
map with a one-cell wall margin (G6) and does not :meth:`Room.intersects` any room already
kept at the default ``margin=1`` (G5). Note that ``margin=1`` expands *both* rectangles, so
the minimum legal gap between two room floor rects is **two** cells — every room owns its
own wall ring.

**Ring boxes — the invariant everything else rests on.** A room's *ring box* is its floor
rectangle grown by one cell on every side: the floor plus the wall ring. ``margin=1``
rejection is *exactly* a ring-box overlap test, so G5 makes the ring boxes of distinct
rooms **pairwise disjoint**. Consequently a cell outside every ring box is never adjacent
to any room floor, and a wall-ring cell of one room is never a wall-ring or floor cell of
another.

**Corridors**: corridors are routed through *free space* — every interior cell that lies
outside every ring box — and may cross a room's wall ring **only** at a chosen door, and
only perpendicularly. Routing is a breadth-first search over a small graph whose nodes are
free cells and whole rooms; a room is entered or left solely through a door, which is what
makes the approach perpendicular by construction (CONTRACT-v2 §3, fix B — reroute).

Each still-unconnected room is joined to the component containing ``rooms[0]`` by the
shortest such route. The search first tries free space alone; only if that fails may it
thread *through* an intervening room (entering by one door, leaving by another). A room
that cannot be reached at all is dropped from the level rather than left stranded —
``max_rooms`` is a ceiling, not a target, and G4 only requires one room.

**Doors**: a door is a wall-ring cell ``D`` of room ``R``, never a ring corner, whose
inward neighbour is ``R``'s floor and whose outward neighbour — the *mouth* — is a free
cell that the corridor occupies. The two cells flanking ``D`` along the wall lie in ``R``'s
ring box, so no corridor can ever carve them and no other room can own them; refusing to
pick two orthogonally adjacent doors on the same room then leaves them ``WALL``. That is
G9b, G9c and G9d proved from the construction rather than checked after the fact. Because
free space never touches a room floor, **every** way into a room is a door (G4a) — the
defect that ruled out simply demoting malformed doors to ``FLOOR``.

All coordinates are ``(x, y)`` with the origin top-left and ``y`` growing down; grid
storage is the sole inversion, ``grid[y][x]`` (CONTRACT §0.1).

Determinism (CONTRACT §0.4): every random choice comes from a single local
``random.Random(seed)`` instance created in :func:`generate_level`. The module-level
functions of the ``random`` module are never called, and nothing here consults the clock,
the OS entropy pool, object identity, or the iteration order of a set or dict.

Imports only from :mod:`roguelike.tiles` and :mod:`roguelike.level` (CONTRACT §10).
"""

from __future__ import annotations

import random

from roguelike.level import Level, Room, blank_grid, freeze_grid
from roguelike.tiles import Tile

__all__ = [
    "DEFAULT_WIDTH",
    "DEFAULT_HEIGHT",
    "MIN_ROOM_SIZE",
    "MAX_ROOM_SIZE",
    "generate_level",
]

DEFAULT_WIDTH: int = 80
DEFAULT_HEIGHT: int = 22
MIN_ROOM_SIZE: int = 4  # minimum floor width and height of a room
MAX_ROOM_SIZE: int = 12  # maximum floor width and height of a room

#: Candidate rectangles drawn per requested room. A ceiling on the work done, not on the
#: number of rooms: placing fewer rooms because the map filled up is correct (G4 still
#: guarantees at least one).
_ATTEMPTS_PER_ROOM: int = 20

#: Whole layouts drawn before settling for the best one. A layout is redrawn only when the
#: router had to strand a room, which is rare at ordinary map sizes.
_LAYOUT_ATTEMPTS: int = 8

#: The four orthogonal neighbour offsets, as ``(dx, dy)``. ``dy = -1`` is up (§0.1).
_NEIGHBOURS: tuple[tuple[int, int], ...] = ((0, -1), (0, 1), (-1, 0), (1, 0))

#: Search-node tags. A node is either ``(_CELL, (x, y))`` — a free corridor cell — or
#: ``(_ROOM, index)`` — the whole floor of ``rooms[index]``, which is internally connected
#: and therefore collapses to a single node.
_CELL: int = 0
_ROOM: int = 1


# --------------------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------------------


def generate_level(
    seed: int,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    max_rooms: int = 12,
) -> Level:
    """Generate a fully connected dungeon level deterministically from ``seed``.

    ``max_rooms`` is a ceiling, not a target — the level may contain fewer rooms if the
    map filled up or a candidate room could not be reached, but never zero (G4).

    Raises:
        TypeError: if ``seed`` is not an ``int`` or is a ``bool``, or if ``width``,
            ``height`` or ``max_rooms`` is not an ``int``.
        ValueError: if ``max_rooms < 1``, or if ``width`` or ``height`` is too small to
            hold a single ``MIN_ROOM_SIZE`` room with its wall margin.
    """
    _validate_arguments(seed, width, height, max_rooms)

    rng = random.Random(seed)

    rooms, doors, corridor = _lay_out(rng, width, height, max_rooms)

    grid = blank_grid(width, height, Tile.WALL)
    for room in rooms:
        _carve_room_floor(grid, room)
    for x, y in corridor:
        grid[y][x] = Tile.FLOOR
    for x, y in doors:
        grid[y][x] = Tile.DOOR

    level = Level(
        width,
        height,
        freeze_grid(grid),
        tuple(rooms),
        rooms[0].center,
        seed,
    )
    _assert_guarantees(level)
    return level


# --------------------------------------------------------------------------------------
# Argument validation (CONTRACT §3.1)
# --------------------------------------------------------------------------------------


def _validate_arguments(
    seed: object, width: object, height: object, max_rooms: object
) -> None:
    """Enforce the §3.1 error table. ``bool`` is an ``int`` subclass, so ``seed`` needs an
    explicit rejection; the contract asks for that only on ``seed``."""
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError(f"seed must be an int, got {type(seed).__name__}")
    for name, value in (
        ("width", width),
        ("height", height),
        ("max_rooms", max_rooms),
    ):
        if not isinstance(value, int):
            raise TypeError(f"{name} must be an int, got {type(value).__name__}")

    if max_rooms < 1:  # type: ignore[operator]
        raise ValueError(f"max_rooms must be >= 1, got {max_rooms}")

    minimum = MIN_ROOM_SIZE + 2
    if width < minimum:  # type: ignore[operator]
        raise ValueError(
            f"width must be >= {minimum} to hold a {MIN_ROOM_SIZE}-wide room "
            f"with its wall margin, got {width}"
        )
    if height < minimum:  # type: ignore[operator]
        raise ValueError(
            f"height must be >= {minimum} to hold a {MIN_ROOM_SIZE}-tall room "
            f"with its wall margin, got {height}"
        )


# --------------------------------------------------------------------------------------
# Room placement
# --------------------------------------------------------------------------------------


def _place_rooms(
    rng: random.Random, width: int, height: int, max_rooms: int
) -> list[Room]:
    """Draw candidate rectangles and keep the ones that fit, in placement order.

    Every candidate already satisfies G6 by construction; G5 is enforced by rejection.
    The first candidate is always accepted, so the result is never empty.
    """
    max_room_width = min(MAX_ROOM_SIZE, width - 2)
    max_room_height = min(MAX_ROOM_SIZE, height - 2)

    rooms: list[Room] = []
    for _ in range(max_rooms * _ATTEMPTS_PER_ROOM):
        if len(rooms) >= max_rooms:
            break
        room_width = rng.randint(MIN_ROOM_SIZE, max_room_width)
        room_height = rng.randint(MIN_ROOM_SIZE, max_room_height)
        # x2 = x + room_width - 1 <= width - 2  =>  x <= width - 1 - room_width
        x = rng.randint(1, width - 1 - room_width)
        y = rng.randint(1, height - 1 - room_height)
        candidate = Room(x, y, room_width, room_height)
        if any(candidate.intersects(placed) for placed in rooms):
            continue
        rooms.append(candidate)

    if not rooms:  # pragma: no cover - the first candidate always fits
        rooms.append(Room(1, 1, MIN_ROOM_SIZE, MIN_ROOM_SIZE))
    return rooms


def _lay_out(
    rng: random.Random, width: int, height: int, max_rooms: int
) -> tuple[list[Room], list[tuple[int, int]], list[tuple[int, int]]]:
    """Draw a room layout and route it, redrawing while any room comes out stranded.

    Two rooms whose wall rings *touch* can never be joined: the cell between their floors
    belongs to both rings, and making it a door of one leaves an unmarked gap in the
    other, while making it a door of both breaks G9d. The router therefore drops a room it
    cannot reach (G8 admits no unreachable pocket), and a layout that strands rooms is
    simply a worse layout — so redraw and keep the best one seen. On the default 80x22 the
    first draw succeeds for all but roughly one seed in a hundred; the retries matter on
    maps so thin that every room's ring spans the short axis.
    """
    best: tuple[list[Room], list[tuple[int, int]], list[tuple[int, int]]] | None = None
    for _ in range(_LAYOUT_ATTEMPTS):
        placed = _place_rooms(rng, width, height, max_rooms)
        layout = _plan_corridors(rng, placed, width, height)
        if best is None or len(layout[0]) > len(best[0]):
            best = layout
        if len(layout[0]) == len(placed):
            break
    assert best is not None  # _LAYOUT_ATTEMPTS >= 1, so the loop always runs
    return best


def _carve_room_floor(grid: list[list[Tile]], room: Room) -> None:
    """Set every cell of ``room``'s floor rectangle to ``Tile.FLOOR`` (G7)."""
    for y in range(room.y, room.y2 + 1):
        row = grid[y]
        for x in range(room.x, room.x2 + 1):
            row[x] = Tile.FLOOR


# --------------------------------------------------------------------------------------
# Free space and door candidates
# --------------------------------------------------------------------------------------


def _blocked_mask(rooms: list[Room], width: int, height: int) -> list[list[bool]]:
    """Return ``blocked[y][x]``: ``True`` where a corridor cell may never be carved.

    A corridor may not sit on the map border (G3) and may not enter any room's *ring box*
    — the floor rectangle plus the one-cell wall ring around it. Everything else is *free
    space*. Because the ring boxes are pairwise disjoint (G5), a free cell is never
    orthogonally adjacent to a room floor: the only way into a room is a door.
    """
    blocked = [[False] * width for _ in range(height)]
    for x in range(width):
        blocked[0][x] = True
        blocked[height - 1][x] = True
    for row in blocked:
        row[0] = True
        row[width - 1] = True
    for room in rooms:
        for y in range(max(0, room.y - 1), min(height, room.y2 + 2)):
            row = blocked[y]
            for x in range(max(0, room.x - 1), min(width, room.x2 + 2)):
                row[x] = True
    return blocked


def _door_candidates(
    room: Room, blocked: list[list[bool]], width: int, height: int
) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """Return every legal ``(door, mouth)`` pair for ``room``.

    ``door`` is a wall-ring cell whose inward neighbour is room floor; ``mouth`` is the
    free cell one step further out, which the corridor must occupy. Ring **corners** are
    excluded by construction — the loops run over the floor extent only — so a door always
    has room floor on one side and its mouth on the other (G9c), and the two cells
    flanking it along the wall are ring cells of the same room (G9b).

    A candidate survives only if its mouth is genuine free space, which also keeps doors
    and corridors off the border (G3).
    """
    pairs = [
        # top wall / bottom wall — the corridor arrives vertically
        *(((x, room.y - 1), (x, room.y - 2)) for x in range(room.x, room.x2 + 1)),
        *(((x, room.y2 + 1), (x, room.y2 + 2)) for x in range(room.x, room.x2 + 1)),
        # left wall / right wall — the corridor arrives horizontally
        *(((room.x - 1, y), (room.x - 2, y)) for y in range(room.y, room.y2 + 1)),
        *(((room.x2 + 1, y), (room.x2 + 2, y)) for y in range(room.y, room.y2 + 1)),
    ]
    return [
        (door, mouth)
        for door, mouth in pairs
        if 0 <= mouth[0] < width
        and 0 <= mouth[1] < height
        and not blocked[mouth[1]][mouth[0]]
    ]


def _orthogonally_adjacent(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Return ``True`` iff ``a`` and ``b`` are 4-neighbours (diagonals do not count)."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1


# --------------------------------------------------------------------------------------
# Corridor planning — fix B, reroute
# --------------------------------------------------------------------------------------


def _plan_corridors(
    rng: random.Random, placed: list[Room], width: int, height: int
) -> tuple[list[Room], list[tuple[int, int]], list[tuple[int, int]]]:
    """Choose the doors and corridor cells that join the rooms into one component.

    Returns ``(rooms, doors, corridor)`` where ``rooms`` is the sub-list of ``placed``
    reachable from ``placed[0]`` — the rest are dropped rather than left stranded, which
    is what turns G8 from a hope into a structural property — and ``doors``/``corridor``
    are the cells to carve. Nothing is written to the grid here; planning is pure
    geometry, so a dropped room simply never gets carved.
    """
    blocked = _blocked_mask(placed, width, height)

    # The RNG choice: which of several equally short routes and equally good doors wins.
    candidates: list[list[tuple[tuple[int, int], tuple[int, int]]]] = []
    for room in placed:
        pairs = _door_candidates(room, blocked, width, height)
        rng.shuffle(pairs)
        candidates.append(pairs)

    # mouth cell -> the (room index, door cell) pairs that open onto it.
    openings: dict[tuple[int, int], list[tuple[int, tuple[int, int]]]] = {}
    for index, pairs in enumerate(candidates):
        for door, mouth in pairs:
            openings.setdefault(mouth, []).append((index, door))

    connected = [False] * len(placed)
    connected[0] = True
    chosen: list[list[tuple[int, int]]] = [[] for _ in placed]
    doors: list[tuple[int, int]] = []
    corridor: list[tuple[int, int]] = []
    corridor_seen: set[tuple[int, int]] = set()

    def usable(index: int, door: tuple[int, int]) -> bool:
        """A door may not sit orthogonally beside another door of the same room (G9d).

        Doors of *different* rooms can never be adjacent: a door's mouth must be free
        space, and every other room's doors lie inside that room's disjoint ring box.
        """
        return not any(
            _orthogonally_adjacent(existing, door) for existing in chosen[index]
        )

    def search(target: int):
        """Shortest route from the connected component to ``placed[target]``.

        Breadth-first over a graph of free cells plus whole-room nodes — a room's floor is
        an internally connected rectangle, so it collapses to one node, and the only edges
        touching it are its doors. That is what makes every approach perpendicular.

        Sources are the corridor cells carved so far plus every already-connected room;
        the single target is ``placed[target]``. Returns ``(cells, picks)`` — the corridor
        cells and the ``(room, door)`` pairs to carve — or ``None`` if unreachable.
        """
        parent: dict[tuple[int, object], tuple[int, object] | None] = {}
        crossed: dict[tuple[int, object], tuple[int, tuple[int, int]] | None] = {}
        queue: list[tuple[int, object]] = []

        def seed_node(node: tuple[int, object]) -> None:
            if node not in parent:
                parent[node] = None
                crossed[node] = None
                queue.append(node)

        for cell in corridor:
            seed_node((_CELL, cell))
        for index, is_connected in enumerate(connected):
            if is_connected:
                seed_node((_ROOM, index))

        head = 0
        while head < len(queue):
            node = queue[head]
            head += 1
            kind, payload = node

            if kind == _ROOM:
                # Leaving a connected room: every door of it opens a new corridor mouth.
                for door, mouth in candidates[payload]:
                    step = (_CELL, mouth)
                    if step in parent or not usable(payload, door):
                        continue
                    parent[step] = node
                    crossed[step] = (payload, door)
                    queue.append(step)
                continue

            x, y = payload
            for dx, dy in _neighbour_order(node, parent):
                nx, ny = x + dx, y + dy
                step = (_CELL, (nx, ny))
                if step in parent or blocked[ny][nx]:
                    continue
                parent[step] = node
                crossed[step] = None
                queue.append(step)
            # Arriving at the target: this mouth pierces its wall perpendicularly.
            for index, door in openings.get(payload, ()):
                if index == target and usable(index, door):
                    step = (_ROOM, index)
                    parent[step] = node
                    crossed[step] = (index, door)
                    return _unwind(step, parent, crossed)

        return None

    # Sweep until a sweep connects nothing new: a room that free space could not reach
    # earlier may become reachable once a later corridor has been carved past it.
    progress = True
    while progress:
        progress = False
        for index in range(1, len(placed)):
            if connected[index]:
                continue
            route = search(index)
            if route is None:
                continue  # unreachable: dropped below rather than left stranded (G8)
            cells, picks = route
            for cell in cells:
                if cell not in corridor_seen:
                    corridor_seen.add(cell)
                    corridor.append(cell)
            for room_index, door in picks:
                if door not in chosen[room_index]:
                    chosen[room_index].append(door)
                    doors.append(door)
            connected[index] = True
            progress = True

    rooms = [room for index, room in enumerate(placed) if connected[index]]
    return rooms, doors, corridor


def _neighbour_order(node, parent):
    """Return the four neighbour offsets with the direction we arrived from first.

    Pure tie-breaking: it never changes a route's length, only which of the equally short
    routes breadth-first search settles on — long straight runs rather than staircases.
    """
    source = parent[node]
    if source is None or source[0] != _CELL:
        return _NEIGHBOURS
    (x, y), (px, py) = node[1], source[1]
    ahead = (x - px, y - py)
    return (ahead, *(step for step in _NEIGHBOURS if step != ahead))


def _unwind(node, parent, crossed):
    """Walk ``parent`` back to the source, returning the route as
    ``(corridor cells, (room, door) picks)`` in forward order."""
    chain = []
    while node is not None:
        chain.append(node)
        node = parent[node]
    chain.reverse()

    cells = [payload for kind, payload in chain if kind == _CELL]
    picks = [crossed[step] for step in chain if crossed[step] is not None]
    return cells, picks


# --------------------------------------------------------------------------------------
# Self-check — never hand back a level that violates the contract
# --------------------------------------------------------------------------------------


def _is_wall(level: Level, x: int, y: int) -> bool:
    """Return ``True`` iff ``(x, y)`` is ``Tile.WALL``, counting out of bounds as wall
    (CONTRACT-v2 §3, "Out-of-bounds neighbours count as ``Tile.WALL``")."""
    if not level.in_bounds(x, y):
        return True
    return level.tile_at(x, y) is Tile.WALL


def _assert_guarantees(level: Level) -> None:
    """Re-derive G3-G10 plus G9a-G9d and G4a from the finished ``Level`` and raise rather
    than return a level that breaks any of them (CONTRACT §3: "Never returns a ``Level``
    violating G1-G12"; CONTRACT-v2 §3 replaces G9 and adds G4a).

    G1/G2 are properties of the code, G11/G12 of the constructor arguments; the rest are
    properties of the output and are cheap enough to verify every time.
    """
    width, height, rooms = level.width, level.height, level.rooms

    for x in range(width):  # G3
        if level.tile_at(x, 0) is not Tile.WALL or level.tile_at(
            x, height - 1
        ) is not Tile.WALL:
            raise RuntimeError(f"G3 violated: border column {x} is not WALL")
    for y in range(height):  # G3
        if level.tile_at(0, y) is not Tile.WALL or level.tile_at(
            width - 1, y
        ) is not Tile.WALL:
            raise RuntimeError(f"G3 violated: border row {y} is not WALL")

    if not rooms:  # G4
        raise RuntimeError("G4 violated: level has no rooms")

    for i, room in enumerate(rooms):
        for other in rooms[i + 1 :]:  # G5
            if room.intersects(other):
                raise RuntimeError(f"G5 violated: {room} intersects {other}")
        if room.x < 1 or room.y < 1 or room.x2 > width - 2 or room.y2 > height - 2:
            raise RuntimeError(f"G6 violated: {room} escapes the wall margin")
        for y in range(room.y, room.y2 + 1):  # G7
            for x in range(room.x, room.x2 + 1):
                if level.tile_at(x, y) is not Tile.FLOOR:
                    raise RuntimeError(f"G7 violated: ({x}, {y}) in {room} is not FLOOR")

    _assert_doors(level)

    if not any(room.contains(*level.player_start) for room in rooms) or not (
        level.is_walkable(*level.player_start)
    ):
        raise RuntimeError(f"G10 violated: player_start {level.player_start} is invalid")

    reached = _flood_fill(level, level.player_start)  # G8
    walkable = sum(
        1
        for y in range(height)
        for x in range(width)
        if level.is_walkable(x, y)
    )
    if len(reached) != walkable:
        raise RuntimeError(
            f"G8 violated: flood fill reached {len(reached)} of {walkable} walkable cells"
        )


def _assert_doors(level: Level) -> None:
    """Check the tightened door constraint: G9a-G9d and G4a (CONTRACT-v2 §3)."""
    rooms = level.rooms
    doors = [
        (x, y)
        for y in range(level.height)
        for x in range(level.width)
        if level.tile_at(x, y) is Tile.DOOR
    ]
    door_cells = set(doors)
    with_a_door = [False] * len(rooms)

    for x, y in doors:
        owners = [i for i, room in enumerate(rooms) if room.on_perimeter(x, y)]
        if not owners:  # G9a
            raise RuntimeError(f"G9a violated: door ({x}, {y}) is on no room perimeter")
        for i in owners:
            with_a_door[i] = True

        # G9b — embedded in a wall run along exactly one axis.
        walls_above_below = _is_wall(level, x, y - 1) and _is_wall(level, x, y + 1)
        walls_left_right = _is_wall(level, x - 1, y) and _is_wall(level, x + 1, y)
        if not (walls_above_below or walls_left_right):
            raise RuntimeError(
                f"G9b violated: door ({x}, {y}) is not embedded in a wall run"
            )
        # G9c — and the perpendicular axis is passage.
        if walls_above_below and (
            _is_wall(level, x - 1, y) or _is_wall(level, x + 1, y)
        ):
            raise RuntimeError(f"G9c violated: door ({x}, {y}) has no east-west passage")
        if walls_left_right and (
            _is_wall(level, x, y - 1) or _is_wall(level, x, y + 1)
        ):
            raise RuntimeError(
                f"G9c violated: door ({x}, {y}) has no north-south passage"
            )

        for dx, dy in _NEIGHBOURS:  # G9d
            if (x + dx, y + dy) in door_cells:
                raise RuntimeError(
                    f"G9d violated: doors ({x}, {y}) and "
                    f"({x + dx}, {y + dy}) are adjacent"
                )

    if len(rooms) == 1:  # G4a — a single-room level has no corridors, so no doors
        if doors:
            raise RuntimeError(f"G4a violated: single-room level has {len(doors)} doors")
    elif not all(with_a_door):
        missing = rooms[with_a_door.index(False)]
        raise RuntimeError(f"G4a violated: {missing} has no door on its perimeter")


def _flood_fill(level: Level, origin: tuple[int, int]) -> set[tuple[int, int]]:
    """Return every walkable cell reachable from ``origin`` by 4-directional steps.

    The returned set is only ever measured by size and membership, never iterated in a way
    that could affect the generated level (§0.4).
    """
    if not level.is_walkable(*origin):
        return set()
    seen = {origin}
    stack = [origin]
    while stack:
        x, y = stack.pop()
        for dx, dy in _NEIGHBOURS:
            step = (x + dx, y + dy)
            if step not in seen and level.is_walkable(*step):
                seen.add(step)
                stack.append(step)
    return seen
