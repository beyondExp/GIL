from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

OccupancyFn = Callable[[float, float], bool]


def wrap_pi(angle: float) -> float:
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle < -math.pi:
        angle += 2.0 * math.pi
    return angle


@dataclass
class Pose:
    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0


class UnicycleTwin:
    """Body-frame unicycle used as the imagination and sim twin.

    vx, vy are in the robot frame. This is what a real cmd_vel means.
    """

    def __init__(self, pose: Pose | None = None):
        self.pose = pose or Pose()
        self.collided = False
        self.steps = 0

    def copy(self) -> "UnicycleTwin":
        twin = UnicycleTwin(Pose(self.pose.x, self.pose.y, self.pose.yaw))
        twin.collided = self.collided
        twin.steps = self.steps
        return twin

    def step(
        self,
        vx: float,
        vy: float = 0.0,
        wz: float = 0.0,
        dt: float = 0.2,
        occupancy: OccupancyFn | None = None,
    ) -> bool:
        yaw = self.pose.yaw
        nx = self.pose.x + (vx * math.cos(yaw) - vy * math.sin(yaw)) * dt
        ny = self.pose.y + (vx * math.sin(yaw) + vy * math.cos(yaw)) * dt
        dist = math.hypot(nx - self.pose.x, ny - self.pose.y)
        n = 1
        if occupancy is not None:
            n = max(1, int(math.ceil(dist / 0.08)))
        sdt = dt / n
        saved = Pose(self.pose.x, self.pose.y, self.pose.yaw)
        for _ in range(n):
            yaw = self.pose.yaw
            px = self.pose.x + (vx * math.cos(yaw) - vy * math.sin(yaw)) * sdt
            py = self.pose.y + (vx * math.sin(yaw) + vy * math.cos(yaw)) * sdt
            pyaw = wrap_pi(self.pose.yaw + wz * sdt)
            if occupancy is not None and occupancy(px, py):
                self.pose = saved
                self.collided = True
                return False
            self.pose = Pose(px, py, pyaw)
        self.steps += 1
        return True

    def distance_to(self, x: float, y: float) -> float:
        dx = self.pose.x - x
        dy = self.pose.y - y
        return math.hypot(dx, dy)
