from __future__ import annotations

from typing import Any

from gil.world.maze3d import Maze3D, MazeSpec, build_wall_boxes


def occupancy_grid_to_maze(
    grid: list[list[int]],
    *,
    cell_size: float = 1.4,
    origin_x: float | None = None,
    origin_y: float | None = None,
    occupied_is: int = 1,
    start_cell: tuple[int, int] | None = None,
    goal_cell: tuple[int, int] | None = None,
) -> Maze3D:
    """Turn a 2D occupancy grid (0 free / 1 occupied by default) into a Maze3D.

    Adjacent free cells share an opening. Occupied cells stay walled off so
    Kinematic3DWorldModel can dream against the same AABBs Isaac will use
    once a USD/collider exists.
    """
    if not grid or not grid[0]:
        raise ValueError("occupancy grid is empty")
    height = len(grid)
    width = len(grid[0])
    if any(len(row) != width for row in grid):
        raise ValueError("occupancy grid rows must be the same length")

    free = [(x, y) for y in range(height) for x in range(width) if int(grid[y][x]) != occupied_is]
    if len(free) < 2:
        raise ValueError("occupancy grid needs at least two free cells")

    ox = float(origin_x) if origin_x is not None else -0.5 * width * cell_size
    oy = float(origin_y) if origin_y is not None else -0.5 * height * cell_size
    spec = MazeSpec(
        width=width,
        height=height,
        cell_size=cell_size,
        origin_x=ox,
        origin_y=oy,
        seed=-1,
        goal_x=0.0,
        goal_y=0.0,
    )
    v_walls = [[True for _ in range(width + 1)] for _ in range(height)]
    h_walls = [[True for _ in range(width)] for _ in range(height + 1)]
    for y in range(height):
        for x in range(width):
            if int(grid[y][x]) == occupied_is:
                continue
            if x + 1 < width and int(grid[y][x + 1]) != occupied_is:
                v_walls[y][x + 1] = False
            if y + 1 < height and int(grid[y + 1][x]) != occupied_is:
                h_walls[y + 1][x] = False

    if start_cell is None:
        start_cell = free[0]
    if goal_cell is None:
        goal_cell = free[-1]
    if start_cell not in free or goal_cell not in free:
        raise ValueError("start_cell/goal_cell must be free cells")
    walls = build_wall_boxes(spec, v_walls, h_walls)
    start = (ox + (start_cell[0] + 0.5) * cell_size, oy + (start_cell[1] + 0.5) * cell_size, 0.0)
    goal = (ox + (goal_cell[0] + 0.5) * cell_size, oy + (goal_cell[1] + 0.5) * cell_size, spec.goal_z)
    spec = MazeSpec(
        width=width,
        height=height,
        cell_size=cell_size,
        origin_x=ox,
        origin_y=oy,
        seed=-1,
        goal_x=goal[0],
        goal_y=goal[1],
        goal_z=goal[2],
    )
    return Maze3D(
        spec=spec,
        walls=walls,
        start=start,
        goal=goal,
        v_walls=v_walls,
        h_walls=h_walls,
    )


def maze_text_to_grid(text: str) -> tuple[list[list[int]], tuple[int, int], tuple[int, int]]:
    """Parse a newline-separated maze into an occupancy grid.

    Accepts common encodings where:
    - '1' / '#' are walls (occupied)
    - '0' / ' ' are free
    - 'S' is start (free)
    - 'E' / 'G' is goal (free)
    """
    raw = (text or "").strip("\n")
    lines = [ln.rstrip("\r") for ln in raw.splitlines() if ln.strip("\r").strip() != ""]
    if not lines:
        raise ValueError("maze text is empty")
    width = max(len(ln) for ln in lines)
    grid: list[list[int]] = []
    start: tuple[int, int] | None = None
    goal: tuple[int, int] | None = None
    for y, ln in enumerate(lines):
        row: list[int] = []
        for x in range(width):
            ch = ln[x] if x < len(ln) else " "
            if ch in ("1", "#"):
                row.append(1)
            else:
                row.append(0)
            if ch == "S":
                start = (x, y)
            if ch in ("E", "G"):
                goal = (x, y)
        grid.append(row)
    if start is None or goal is None:
        raise ValueError("maze text must contain both 'S' and ('E' or 'G')")
    return grid, start, goal

def payload_to_grid(payload: dict[str, Any]) -> tuple[list[list[int]], dict[str, Any]]:
    """Accept InteriorGS-style occupancy JSON or a simple `{grid: [[...]]}` file."""
    meta: dict[str, Any] = {}
    grid = payload.get("grid") or payload.get("occupancy") or payload.get("map")
    if isinstance(grid, dict):
        payload = {**payload, **grid}
        grid = payload.get("grid") or payload.get("data")
    if isinstance(grid, list) and grid and isinstance(grid[0], list):
        meta["cell_size"] = float(payload.get("cell_size") or payload.get("resolution") or 0.9)
        if "origin" in payload and isinstance(payload["origin"], (list, tuple)) and len(payload["origin"]) >= 2:
            meta["origin_x"] = float(payload["origin"][0])
            meta["origin_y"] = float(payload["origin"][1])
        occupied_is = payload.get("occupied_is")
        if occupied_is is not None:
            meta["occupied_is"] = int(occupied_is)
        return [[int(v) for v in row] for row in grid], meta
    raise ValueError("JSON does not contain a 2D occupancy grid")
