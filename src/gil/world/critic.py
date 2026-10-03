from __future__ import annotations

from gil.core.types import Goal
from gil.world.dream import Rollout


class PhysicsCritic:
    """Stand-in for Cosmos Reason. Rejects implausible or unsuccessful dreams."""

    def __init__(self, min_score: float = 0.7):
        self.min_score = min_score

    def score(self, rollout: Rollout, goal: Goal) -> float:
        if not rollout.physics_ok:
            return 0.0
        if not rollout.success:
            return min(rollout.critic_score, 0.4)
        if not rollout.commands:
            return 0.0
        return max(rollout.critic_score, 0.0)

    def filter(self, rollouts: list[Rollout], goal: Goal) -> list[Rollout]:
        kept: list[Rollout] = []
        for rollout in rollouts:
            scored = Rollout(
                success=rollout.success,
                critic_score=self.score(rollout, goal),
                commands=list(rollout.commands),
                physics_ok=rollout.physics_ok,
                note=rollout.note,
            )
            if scored.physics_ok and scored.critic_score >= self.min_score and scored.success:
                kept.append(scored)
        return kept
