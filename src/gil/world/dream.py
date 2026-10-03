from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from gil.core.types import Goal


@dataclass
class Rollout:
    success: bool
    critic_score: float
    commands: list[dict[str, Any]] = field(default_factory=list)
    physics_ok: bool = True
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    rollout_id: str = ""


class WorldModel(Protocol):
    def dream(self, observation: dict[str, Any], goal: Goal, n: int) -> list[Rollout]:
        ...


class MockWorldModel:
    """Deterministic stand-in for Cosmos 3 / GR00T-Dreams. Swap the class, keep the protocol."""

    def __init__(self, *, successes: int = 8, failures: int = 0, critic_score: float = 0.9, physics_ok: bool = True):
        self.successes = successes
        self.failures = failures
        self.critic_score = critic_score
        self.physics_ok = physics_ok

    def dream(self, observation: dict[str, Any], goal: Goal, n: int) -> list[Rollout]:
        out: list[Rollout] = []
        gx = float(goal.x if goal.x is not None else 1.0)
        gy = float(goal.y if goal.y is not None else 0.0)
        pose = observation.get("base") or {}
        x = float(pose.get("x", 0.0))
        y = float(pose.get("y", 0.0))
        for i in range(n):
            success = i < self.successes
            physics = self.physics_ok and success
            commands = [
                {
                    "type": "cmd_vel",
                    "vx": 0.2 if success else 0.0,
                    "vy": 0.0,
                    "wz": 0.0,
                    "target": {"x": gx, "y": gy},
                    "from": {"x": x, "y": y},
                }
            ]
            out.append(
                Rollout(
                    success=success and physics,
                    critic_score=self.critic_score if physics else 0.1,
                    commands=commands,
                    physics_ok=physics,
                    note="mock_dream",
                )
            )
        for _ in range(self.failures):
            if len(out) >= n:
                break
            out.append(Rollout(success=False, critic_score=0.1, physics_ok=False, note="forced_failure"))
        return out[:n]
