from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gil.core.config import RobotProfile
from gil.core.profiles import load_profile


@dataclass
class RobotSession:
    robot_id: str
    profile: RobotProfile
    goal: dict[str, Any] | None = None
    last_observation: dict[str, Any] = field(default_factory=dict)


class SessionStore:
    def __init__(self) -> None:
        self._robots: dict[str, RobotSession] = {}

    def attach(self, robot_id: str, profile: str | RobotProfile) -> RobotSession:
        loaded = profile if isinstance(profile, RobotProfile) else load_profile(profile)
        session = RobotSession(robot_id=robot_id, profile=loaded)
        self._robots[robot_id] = session
        return session

    def get(self, robot_id: str) -> RobotSession:
        if robot_id not in self._robots:
            raise KeyError(f"Unknown robot_id '{robot_id}'. Call connect_robot first.")
        return self._robots[robot_id]

    def require(self, robot_id: str | None) -> RobotSession:
        if not robot_id:
            if len(self._robots) == 1:
                return next(iter(self._robots.values()))
            raise KeyError("robot_id is required when multiple robots are attached.")
        return self.get(robot_id)

    def ids(self) -> list[str]:
        return list(self._robots.keys())
