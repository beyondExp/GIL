from __future__ import annotations

import math
from typing import Any

from gil.core.config import RobotProfile
from gil.core.types import Goal
from gil.world.dream import Rollout
from gil.world.map import SceneMap
from gil.world.twin import UnicycleTwin, wrap_pi


class KinematicWorldModel:
    """Dream by rolling a unicycle twin against the occupancy map.

    This is the sim-in-the-loop stand-in for Cosmos/GR00T-Dreams: futures are
    computed, not canned. Swap this class for a video WFM without changing the orchestrator.
    """

    def __init__(self, profile: RobotProfile | None = None, cell_m: float = 0.25, dt: float = 0.2):
        self.profile = profile
        self.cell_m = cell_m
        self.dt = dt

    def dream(self, observation: dict[str, Any], goal: Goal, n: int) -> list[Rollout]:
        scene = SceneMap(profile=self.profile, cell_m=self.cell_m)
        scene.ingest(observation)
        pose = observation.get("base") or {}
        start = UnicycleTwin()
        start.pose.x = float(pose.get("x", 0.0))
        start.pose.y = float(pose.get("y", 0.0))
        start.pose.yaw = float(pose.get("yaw", 0.0))
        gx = float(goal.x if goal.x is not None else start.pose.x)
        gy = float(goal.y if goal.y is not None else start.pose.y)
        radius = float(goal.radius)
        vx = min(0.35, self.profile.safety.max_vx if self.profile else 0.35)

        recipes = [
            self._path_follow(scene, start, gx, gy, vx),
            self._greedy(start, gx, gy, vx, steps=24),
            self._greedy(start, gx, gy, vx, steps=24, heading_bias=1.2),
            self._greedy(start, gx, gy, vx, steps=24, heading_bias=-1.2),
            self._spin_then_go(start, gx, gy, vx, spin_wz=1.0),
            self._spin_then_go(start, gx, gy, vx, spin_wz=-1.0),
            self._backup(start, vx),
            self._greedy(start, gx, gy, vx=0.05, steps=8),
        ]
        out: list[Rollout] = []
        for commands in recipes[: max(1, n)]:
            out.append(self._evaluate(start, commands, scene, gx, gy, radius))
        while len(out) < n:
            out.append(self._evaluate(start, self._backup(start, vx), scene, gx, gy, radius))
        return out[:n]

    def _evaluate(self, start: UnicycleTwin, commands: list[dict[str, Any]], scene: SceneMap, gx: float, gy: float, radius: float) -> Rollout:
        twin = start.copy()
        physics_ok = True
        for command in commands:
            ok = twin.step(
                float(command.get("vx", 0.0)),
                float(command.get("vy", 0.0)),
                float(command.get("wz", 0.0)),
                float(command.get("dt", self.dt)),
                occupancy=scene.occupancy_at,
            )
            if not ok:
                physics_ok = False
                break
        success = physics_ok and twin.distance_to(gx, gy) <= radius
        score = 0.95 if success else (0.4 if physics_ok else 0.05)
        return Rollout(
            success=success,
            critic_score=score,
            commands=[{"type": "cmd_vel", **{k: command[k] for k in command if k in {"vx", "vy", "wz", "dt"}}} for command in commands],
            physics_ok=physics_ok,
            note="kinematic_twin",
        )

    def _path_follow(self, scene: SceneMap, start: UnicycleTwin, gx: float, gy: float, vx: float) -> list[dict[str, Any]]:
        path = scene.shortest_path((start.pose.x, start.pose.y), (gx, gy))
        if not path:
            return self._greedy(start, gx, gy, vx, steps=12)
        commands: list[dict[str, Any]] = []
        cursor = start.copy()
        for cell in path[1:]:
            tx = cell[0] * self.cell_m
            ty = cell[1] * self.cell_m
            for _ in range(16):
                if cursor.distance_to(tx, ty) < max(0.12, self.cell_m * 0.4):
                    break
                heading = math.atan2(ty - cursor.pose.y, tx - cursor.pose.x)
                err = wrap_pi(heading - cursor.pose.yaw)
                cmd = {
                    "type": "cmd_vel",
                    "vx": vx if abs(err) < 0.6 else 0.05,
                    "vy": 0.0,
                    "wz": max(-1.0, min(1.0, 2.2 * err)),
                    "dt": self.dt,
                }
                commands.append(cmd)
                cursor.step(cmd["vx"], 0.0, cmd["wz"], self.dt)
                if len(commands) > 80:
                    return commands
        return commands or self._greedy(start, gx, gy, vx, steps=8)

    def _greedy(self, start: UnicycleTwin, gx: float, gy: float, vx: float, steps: int = 20, heading_bias: float = 0.0) -> list[dict[str, Any]]:
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
            cursor.step(cmd["vx"], 0.0, cmd["wz"], self.dt)
            if cursor.distance_to(gx, gy) <= 0.4:
                break
        return commands

    def _spin_then_go(self, start: UnicycleTwin, gx: float, gy: float, vx: float, spin_wz: float) -> list[dict[str, Any]]:
        spin = [{"type": "cmd_vel", "vx": 0.0, "vy": 0.0, "wz": spin_wz, "dt": self.dt} for _ in range(4)]
        return spin + self._greedy(start, gx, gy, vx, steps=16)

    def _backup(self, start: UnicycleTwin, vx: float) -> list[dict[str, Any]]:
        return [{"type": "cmd_vel", "vx": -abs(vx), "vy": 0.0, "wz": 0.0, "dt": self.dt} for _ in range(6)]
