from __future__ import annotations

from typing import Any

from gil.core.types import Goal
from gil.world.dream import Rollout
from gil.world.kinematic3d import Kinematic3DWorldModel
from gil.world.maze3d import Maze3D


class PhysicsWorldModel:
    """Isaac-headless imagination backend.

    When a maze is available, uses the kinematic twin as a stand-in for a
    headless Isaac clone (same collision geometry, no motors). The protocol
    is identical so a true PhysX clone can replace this class later.
    """

    def __init__(self, maze: Maze3D | None = None, inner: Any | None = None) -> None:
        self.maze = maze
        self.inner = inner or (Kinematic3DWorldModel(maze=maze) if maze is not None else None)

    def dream(self, observation: dict[str, Any], goal: Goal, n: int) -> list[Rollout]:
        if self.inner is not None:
            rollouts = self.inner.dream(observation, goal, n)
            for r in rollouts:
                r.note = f"physics_headless:{r.note}"
            return rollouts
        return [
            Rollout(
                success=False,
                critic_score=0.0,
                physics_ok=False,
                note="physics_headless_unwired: no maze or Isaac clone attached",
            )
            for _ in range(max(1, n))
        ]
