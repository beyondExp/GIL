from __future__ import annotations

import math
from typing import Any

from gil.core.config import RobotProfile
from gil.core.types import Goal
from gil.world.dream import Rollout
from gil.world.maze3d import Maze3D, MazeSpec, generate_maze
from gil.world.twin import UnicycleTwin, wrap_pi


class Kinematic3DWorldModel:
    """Dream inside a copy of the Isaac maze (capsule vs wall AABBs).

    This is imagination, not the robot. Live execution is Isaac Sim PhysX
    (`gil.h1_maze_walker`). Marble / NuRec / Cosmos Transfer can later
    *build* the USD this copy is taken from; they still never publish cmd_vel.
    """

    def __init__(self, maze: Maze3D | None = None, profile: RobotProfile | None = None, dt: float = 0.2):
        self.maze = maze or generate_maze(MazeSpec())
        self.profile = profile
        self.dt = dt

    def dream(self, observation: dict[str, Any], goal: Goal, n: int) -> list[Rollout]:
        pose = observation.get("base") or {}
        start = UnicycleTwin()
        start.pose.x = float(pose.get("x", self.maze.start[0]))
        start.pose.y = float(pose.get("y", self.maze.start[1]))
        start.pose.yaw = float(pose.get("yaw", 0.0))
        gx = float(goal.x if goal.x is not None else self.maze.goal[0])
        gy = float(goal.y if goal.y is not None else self.maze.goal[1])
        radius = float(goal.radius or self.maze.spec.goal_radius)
        vx = min(0.55, self.profile.safety.max_vx if self.profile else 0.55)
        recipes = [
            self._follow_cells(start, gx, gy, vx),
            self._follow_cells(start, gx, gy, max(0.25, vx * 0.7)),
            self._greedy(start, gx, gy, vx, steps=48),
            self._greedy(start, gx, gy, vx, steps=48, heading_bias=0.6),
            self._greedy(start, gx, gy, vx, steps=48, heading_bias=-0.6),
            self._spin_then_go(start, gx, gy, vx, 1.0),
            self._spin_then_go(start, gx, gy, vx, -1.0),
            self._backup(start, vx),
        ]
        out = [self._evaluate(start, cmds, gx, gy, radius) for cmds in recipes[: max(1, n)]]
        while len(out) < n:
            out.append(self._evaluate(start, self._backup(start, vx), gx, gy, radius))
        return out[:n]

    def _blocked(self, x: float, y: float) -> bool:
        return self.maze.capsule_hits_wall(x, y)

    def _evaluate(self, start: UnicycleTwin, commands: list[dict[str, Any]], gx: float, gy: float, radius: float) -> Rollout:
        twin = start.copy()
        physics_ok = True
        for command in commands:
            ok = twin.step(
                float(command.get("vx", 0.0)),
                float(command.get("vy", 0.0)),
                float(command.get("wz", 0.0)),
                float(command.get("dt", self.dt)),
                occupancy=self._blocked,
            )
            if not ok:
                physics_ok = False
                break
        success = physics_ok and twin.distance_to(gx, gy) <= radius
        score = 0.95 if success else (0.35 if physics_ok else 0.05)
        return Rollout(
            success=success,
            critic_score=score,
            commands=[
                {
                    "type": "cmd_vel",
                    "vx": c.get("vx", 0.0),
                    "vy": c.get("vy", 0.0),
                    "wz": c.get("wz", 0.0),
                    "dt": c.get("dt", self.dt),
                }
                for c in commands
            ],
            physics_ok=physics_ok,
            note="kinematic3d_isaac_maze",
        )

    def _follow_cells(self, start: UnicycleTwin, gx: float, gy: float, vx: float) -> list[dict[str, Any]]:
        path = self.maze.shortest_cell_path((start.pose.x, start.pose.y), (gx, gy))
        if not path:
            return self._greedy(start, gx, gy, vx, steps=20)
        waypoints = [self.maze.cell_center(cx, cy) for cx, cy in path[1:]]
        waypoints.append((gx, gy))
        commands: list[dict[str, Any]] = []
        cursor = start.copy()
        max_wz = 1.0
        for tx, ty in waypoints:
            heading = math.atan2(ty - cursor.pose.y, tx - cursor.pose.x)
            err = wrap_pi(heading - cursor.pose.yaw)
            if abs(err) > 0.02:
                dt_turn = abs(err) / max_wz
                wz = math.copysign(max_wz, err)
                cmd = {"type": "cmd_vel", "vx": 0.0, "vy": 0.0, "wz": wz, "dt": dt_turn}
                commands.append(cmd)
                if not cursor.step(0.0, 0.0, wz, dt_turn, occupancy=self._blocked):
                    return commands
            dist = cursor.distance_to(tx, ty)
            if dist > 0.12:
                dt_go = dist / max(vx, 1e-3)
                cmd = {"type": "cmd_vel", "vx": vx, "vy": 0.0, "wz": 0.0, "dt": dt_go}
                commands.append(cmd)
                if not cursor.step(vx, 0.0, 0.0, dt_go, occupancy=self._blocked):
                    return commands
            if cursor.distance_to(gx, gy) <= 0.32:
                break
        return commands or self._greedy(start, gx, gy, vx, steps=16)

    def _greedy(self, start: UnicycleTwin, gx: float, gy: float, vx: float, steps: int = 30, heading_bias: float = 0.0) -> list[dict[str, Any]]:
        commands: list[dict[str, Any]] = []
        cursor = start.copy()
        for _ in range(steps):
            heading = math.atan2(gy - cursor.pose.y, gx - cursor.pose.x) + heading_bias
            err = wrap_pi(heading - cursor.pose.yaw)
            cmd = {
                "type": "cmd_vel",
                "vx": vx if abs(err) < 0.8 else 0.05,
                "vy": 0.0,
                "wz": max(-1.0, min(1.0, 2.0 * err)),
                "dt": self.dt,
            }
            commands.append(cmd)
            if not cursor.step(cmd["vx"], 0.0, cmd["wz"], self.dt, occupancy=self._blocked):
                break
            if cursor.distance_to(gx, gy) <= 0.35:
                break
        return commands

    def _spin_then_go(self, start: UnicycleTwin, gx: float, gy: float, vx: float, spin_wz: float) -> list[dict[str, Any]]:
        spin = [{"type": "cmd_vel", "vx": 0.0, "vy": 0.0, "wz": spin_wz, "dt": self.dt} for _ in range(5)]
        return spin + self._greedy(start, gx, gy, vx, steps=28)

    def _backup(self, start: UnicycleTwin, vx: float) -> list[dict[str, Any]]:
        return [{"type": "cmd_vel", "vx": -abs(vx), "vy": 0.0, "wz": 0.0, "dt": self.dt} for _ in range(8)]
