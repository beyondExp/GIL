from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MazeSpec:
    """Matches robot_simulator/isaac_exts/gil.maze_env MazeConfig."""

    width: int = 9
    height: int = 9
    # Corridor width is approximately `cell_size - wall_thickness`.
    # Humanoid locomotion policies drift while turning; give them real margin.
    cell_size: float = 1.4
    wall_thickness: float = 0.05
    wall_height: float = 1.0
    origin_x: float = -4.0
    origin_y: float = -4.0
    wall_center_z: float = 0.5
    seed: int = 0
    # NOTE: `generate_maze()` will overwrite goal_{x,y} to match the top-right cell center.
    goal_x: float = 0.0
    goal_y: float = 0.0
    goal_z: float = 0.15
    goal_radius: float = 0.4
    robot_radius: float = 0.28
    robot_height: float = 1.7


@dataclass
class Box3D:
    name: str
    cx: float
    cy: float
    cz: float
    sx: float
    sy: float
    sz: float

    def aabb(self) -> tuple[float, float, float, float, float, float]:
        hx, hy, hz = self.sx / 2.0, self.sy / 2.0, self.sz / 2.0
        return (self.cx - hx, self.cy - hy, self.cz - hz, self.cx + hx, self.cy + hy, self.cz + hz)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Maze3D:
    spec: MazeSpec
    walls: list[Box3D]
    start: tuple[float, float, float]
    goal: tuple[float, float, float]
    v_walls: list[list[bool]]
    h_walls: list[list[bool]]

    def cell_center(self, cx: int, cy: int) -> tuple[float, float]:
        return (
            self.spec.origin_x + (cx + 0.5) * self.spec.cell_size,
            self.spec.origin_y + (cy + 0.5) * self.spec.cell_size,
        )

    def world_to_cell(self, x: float, y: float) -> tuple[int, int]:
        cx = int((x - self.spec.origin_x) / self.spec.cell_size)
        cy = int((y - self.spec.origin_y) / self.spec.cell_size)
        return cx, cy

    def neighbors(self, cx: int, cy: int) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        w, h = self.spec.width, self.spec.height
        if cx + 1 < w and not self.v_walls[cy][cx + 1]:
            out.append((cx + 1, cy))
        if cx - 1 >= 0 and not self.v_walls[cy][cx]:
            out.append((cx - 1, cy))
        if cy + 1 < h and not self.h_walls[cy + 1][cx]:
            out.append((cx, cy + 1))
        if cy - 1 >= 0 and not self.h_walls[cy][cx]:
            out.append((cx, cy - 1))
        return out

    def shortest_cell_path(self, start_xy: tuple[float, float], goal_xy: tuple[float, float]) -> list[tuple[int, int]] | None:
        sx, sy = self.world_to_cell(*start_xy)
        gx, gy = self.world_to_cell(*goal_xy)
        sx = max(0, min(self.spec.width - 1, sx))
        sy = max(0, min(self.spec.height - 1, sy))
        gx = max(0, min(self.spec.width - 1, gx))
        gy = max(0, min(self.spec.height - 1, gy))
        start, goal = (sx, sy), (gx, gy)
        if start == goal:
            return [start]
        frontier = [start]
        came: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
        while frontier:
            cur = frontier.pop(0)
            if cur == goal:
                break
            for nxt in self.neighbors(*cur):
                if nxt in came:
                    continue
                came[nxt] = cur
                frontier.append(nxt)
        if goal not in came:
            return None
        path = [goal]
        while came[path[-1]] is not None:
            path.append(came[path[-1]])  # type: ignore[arg-type]
        path.reverse()
        return path

    def sealed_start(self) -> "Maze3D":
        """Close every opening on the spawn cell so dreams cannot reach the exit."""
        v_walls = [row[:] for row in self.v_walls]
        h_walls = [row[:] for row in self.h_walls]
        v_walls[0][0] = True
        v_walls[0][1] = True
        h_walls[0][0] = True
        h_walls[1][0] = True
        walls = build_wall_boxes(self.spec, v_walls, h_walls)
        return Maze3D(
            spec=self.spec,
            walls=walls,
            start=self.start,
            goal=self.goal,
            v_walls=v_walls,
            h_walls=h_walls,
        )

    def to_observation(self, pose: dict[str, Any] | None = None) -> dict[str, Any]:
        base = pose or {"x": self.start[0], "y": self.start[1], "z": self.start[2], "yaw": 0.0}
        obstacles: list[dict[str, float]] = []
        for wall in self.walls:
            obstacles.append({"x": wall.cx, "y": wall.cy})
            if wall.sy >= wall.sx:
                obstacles.append({"x": wall.cx, "y": wall.cy + wall.sy * 0.28})
                obstacles.append({"x": wall.cx, "y": wall.cy - wall.sy * 0.28})
            else:
                obstacles.append({"x": wall.cx + wall.sx * 0.28, "y": wall.cy})
                obstacles.append({"x": wall.cx - wall.sx * 0.28, "y": wall.cy})
        return {
            "base": dict(base),
            "objects": [
                {
                    "label": "exit",
                    "x": self.goal[0],
                    "y": self.goal[1],
                    "z": self.goal[2],
                    "occupied": False,
                }
            ],
            "obstacles": obstacles,
            "scene_kind": "isaac_h1_maze",
        }

    def render_ascii(self, x: float, y: float, gx: float | None = None, gy: float | None = None) -> str:
        rx, ry = self.world_to_cell(x, y)
        gxy = self.world_to_cell(
            gx if gx is not None else self.goal[0],
            gy if gy is not None else self.goal[1],
        )
        lines: list[str] = []
        for cy in range(self.spec.height, -1, -1):
            top = []
            for cx in range(self.spec.width):
                top.append("+" + ("---" if self.h_walls[cy][cx] else "   "))
            top.append("+")
            lines.append("".join(top))
            if cy == 0:
                break
            row = []
            cell_y = cy - 1
            for cx in range(self.spec.width):
                row.append("|" if self.v_walls[cell_y][cx] else " ")
                if (cx, cell_y) == (rx, ry):
                    mark = "R"
                elif (cx, cell_y) == gxy:
                    mark = "G"
                else:
                    mark = " "
                row.append(f" {mark} ")
            row.append("|" if self.v_walls[cell_y][self.spec.width] else " ")
            lines.append("".join(row))
        return "\n".join(lines)

    def capsule_hits_wall(self, x: float, y: float, z: float = 0.85) -> bool:
        r = self.spec.robot_radius
        z_lo, z_hi = 0.15, self.spec.robot_height
        for wall in self.walls:
            minx, miny, minz, maxx, maxy, maxz = wall.aabb()
            if z_hi < minz or z_lo > maxz:
                continue
            nearest_x = min(max(x, minx), maxx)
            nearest_y = min(max(y, miny), maxy)
            if (x - nearest_x) ** 2 + (y - nearest_y) ** 2 <= r * r:
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "isaac_h1_maze",
            "backend": "scene3d",
            "spec": asdict(self.spec),
            "start": {"x": self.start[0], "y": self.start[1], "z": self.start[2]},
            "goal": {"x": self.goal[0], "y": self.goal[1], "z": self.goal[2], "radius": self.spec.goal_radius},
            "walls": [w.as_dict() for w in self.walls],
            "floor": {
                "cx": self.spec.origin_x + (self.spec.width * self.spec.cell_size) / 2.0,
                "cy": self.spec.origin_y + (self.spec.height * self.spec.cell_size) / 2.0,
                "cz": -0.02,
                "sx": self.spec.width * self.spec.cell_size + 1.2,
                "sy": self.spec.height * self.spec.cell_size + 1.2,
                "sz": 0.04,
            },
            "notes": "Same DFS maze as gil.maze_env in Isaac Sim. Dreams and the Three.js viewer share this geometry.",
        }

    def write_json(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path


def generate_maze(spec: MazeSpec | None = None) -> Maze3D:
    spec = spec or MazeSpec()
    rng = random.Random(spec.seed)
    visited = [[False for _ in range(spec.width)] for _ in range(spec.height)]
    v_walls = [[True for _ in range(spec.width + 1)] for _ in range(spec.height)]
    h_walls = [[True for _ in range(spec.width)] for _ in range(spec.height + 1)]

    def neighbors(cx: int, cy: int):
        dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        rng.shuffle(dirs)
        for dx, dy in dirs:
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < spec.width and 0 <= ny < spec.height and not visited[ny][nx]:
                yield nx, ny

    stack = [(0, 0)]
    visited[0][0] = True
    while stack:
        cx, cy = stack[-1]
        nxt = None
        for nx, ny in neighbors(cx, cy):
            nxt = (nx, ny)
            break
        if nxt is None:
            stack.pop()
            continue
        nx, ny = nxt
        if nx == cx + 1:
            v_walls[cy][cx + 1] = False
        elif nx == cx - 1:
            v_walls[cy][cx] = False
        elif ny == cy + 1:
            h_walls[cy + 1][cx] = False
        elif ny == cy - 1:
            h_walls[cy][cx] = False
        visited[ny][nx] = True
        stack.append((nx, ny))

    h_walls[0][0] = False
    h_walls[spec.height][spec.width - 1] = False
    # Keep goal tied to the grid scale so Isaac + planner remain consistent when cell_size changes.
    gx = spec.origin_x + (spec.width - 0.5) * spec.cell_size
    gy = spec.origin_y + (spec.height - 0.5) * spec.cell_size
    spec = MazeSpec(**{**asdict(spec), "goal_x": float(gx), "goal_y": float(gy)})
    walls = build_wall_boxes(spec, v_walls, h_walls)
    sx = spec.origin_x + 0.5 * spec.cell_size
    sy = spec.origin_y + 0.5 * spec.cell_size
    return Maze3D(
        spec=spec,
        walls=walls,
        start=(sx, sy, 0.0),
        goal=(spec.goal_x, spec.goal_y, spec.goal_z),
        v_walls=v_walls,
        h_walls=h_walls,
    )


def build_wall_boxes(spec: MazeSpec, v_walls: list[list[bool]], h_walls: list[list[bool]]) -> list[Box3D]:
    walls: list[Box3D] = []
    idx = 0
    for y in range(spec.height):
        for x in range(spec.width + 1):
            if not v_walls[y][x]:
                continue
            wx = spec.origin_x + x * spec.cell_size
            wy = spec.origin_y + (y + 0.5) * spec.cell_size
            walls.append(
                Box3D(
                    name=f"wall_v_{idx}",
                    cx=wx,
                    cy=wy,
                    cz=spec.wall_center_z,
                    sx=spec.wall_thickness,
                    sy=spec.cell_size + spec.wall_thickness,
                    sz=spec.wall_height,
                )
            )
            idx += 1
    for y in range(spec.height + 1):
        for x in range(spec.width):
            if not h_walls[y][x]:
                continue
            wx = spec.origin_x + (x + 0.5) * spec.cell_size
            wy = spec.origin_y + y * spec.cell_size
            walls.append(
                Box3D(
                    name=f"wall_h_{idx}",
                    cx=wx,
                    cy=wy,
                    cz=spec.wall_center_z,
                    sx=spec.cell_size + spec.wall_thickness,
                    sy=spec.wall_thickness,
                    sz=spec.wall_height,
                )
            )
            idx += 1
    return walls
