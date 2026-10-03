from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from gil.world.maze3d import Maze3D


Cell = tuple[int, int]
Edge = tuple[Cell, Cell]


def _norm_edge(a: Cell, b: Cell) -> tuple[Cell, Cell]:
    return (a, b) if a <= b else (b, a)


@dataclass
class CellDiscoveryMap:
    """
    A plug-and-play "mapping" artifact for mazes:
    - discovered cells
    - discovered connectivity (edges) between cells (openings)

    This is intentionally minimal: it matches how real robots often start (topological/local map)
    before full metric SLAM is in place.
    """

    width: int
    height: int
    seed: int = 0
    known_cells: set[Cell] = field(default_factory=set)
    known_edges: set[tuple[Cell, Cell]] = field(default_factory=set)
    observed_at_s: float = 0.0

    def is_known(self, cell: Cell) -> bool:
        return cell in self.known_cells

    def reveal_local(self, maze: Maze3D, *, at: Cell, radius_cells: int = 1) -> int:
        """
        Reveal a local neighborhood around `at` by BFS up to `radius_cells` steps.
        Returns number of newly discovered cells.
        """
        radius = max(0, int(radius_cells))
        q: list[tuple[Cell, int]] = [(tuple(at), 0)]
        seen: set[Cell] = set()
        new = 0
        while q:
            cur, d = q.pop(0)
            if cur in seen:
                continue
            seen.add(cur)
            if cur not in self.known_cells:
                self.known_cells.add(cur)
                new += 1
            if d >= radius:
                continue
            for nxt in maze.neighbors(cur[0], cur[1]):
                # record edge only if both endpoints are within reveal wavefront (we still store it optimistically)
                self.known_edges.add(_norm_edge(cur, tuple(nxt)))
                q.append((tuple(nxt), d + 1))
        return new

    def neighbors_known(self, cell: Cell) -> list[Cell]:
        out: list[Cell] = []
        cx, cy = int(cell[0]), int(cell[1])
        for a, b in self.known_edges:
            if a == (cx, cy) and b in self.known_cells:
                out.append(b)
            elif b == (cx, cy) and a in self.known_cells:
                out.append(a)
        return out

    def shortest_path_known(self, *, start: Cell, goal: Cell) -> list[Cell] | None:
        """BFS on the discovered graph only."""
        s = tuple(start)
        g = tuple(goal)
        if s not in self.known_cells or g not in self.known_cells:
            return None
        if s == g:
            return [s]
        frontier: list[Cell] = [s]
        came: dict[Cell, Cell | None] = {s: None}
        while frontier:
            cur = frontier.pop(0)
            if cur == g:
                break
            for nxt in self.neighbors_known(cur):
                if nxt in came:
                    continue
                came[nxt] = cur
                frontier.append(nxt)
        if g not in came:
            return None
        path = [g]
        while came[path[-1]] is not None:
            path.append(came[path[-1]])  # type: ignore[arg-type]
        path.reverse()
        return path

    def frontier_portals(self, maze: Maze3D) -> list[dict[str, Any]]:
        """
        Frontier "portals": discovered cells that have an opening to an undiscovered neighbor.
        Each portal includes:
          - from_cell: discovered cell
          - to_cell: undiscovered neighbor cell (still unknown)
        """
        portals: list[dict[str, Any]] = []
        for c in sorted(self.known_cells):
            for nxt in maze.neighbors(int(c[0]), int(c[1])):
                n = (int(nxt[0]), int(nxt[1]))
                if n not in self.known_cells:
                    portals.append({"from_cell": [int(c[0]), int(c[1])], "to_cell": [int(n[0]), int(n[1])]})
        return portals

    def to_artifact(self, *, maze: Maze3D, robot_cell: Cell | None = None) -> dict[str, Any]:
        return {
            "kind": "cell_discovery_map_v1",
            "maze": {
                "width": int(self.width),
                "height": int(self.height),
                "seed": int(self.seed),
                "cell_size_m": float(maze.spec.cell_size),
                "origin_x": float(maze.spec.origin_x),
                "origin_y": float(maze.spec.origin_y),
            },
            "known_cells": [[int(c[0]), int(c[1])] for c in sorted(self.known_cells)],
            "known_edges": [[[int(a[0]), int(a[1])], [int(b[0]), int(b[1])]] for a, b in sorted(self.known_edges)],
            "frontiers": self.frontier_portals(maze),
            "robot_cell": None if robot_cell is None else [int(robot_cell[0]), int(robot_cell[1])],
            "observed_at_s": float(self.observed_at_s),
        }

    def write_json(self, path: str | Path, *, maze: Maze3D, robot_cell: Cell | None = None) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_artifact(maze=maze, robot_cell=robot_cell), indent=2), encoding="utf-8")
        return p


def cell_from_world(maze: Maze3D, *, x: float, y: float) -> Cell:
    cx, cy = maze.world_to_cell(float(x), float(y))
    cx = max(0, min(maze.spec.width - 1, int(cx)))
    cy = max(0, min(maze.spec.height - 1, int(cy)))
    return (int(cx), int(cy))


def goal_cell(maze: Maze3D) -> Cell:
    return cell_from_world(maze, x=float(maze.goal[0]), y=float(maze.goal[1]))

