from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from gil.learn.eval_harness import maze_success
from gil.world.dream import Rollout


@dataclass
class TrainReport:
    ok: bool
    method: str
    skill_id: str
    rounds: int = 0
    note: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


class SkillTrainer(Protocol):
    def train(
        self,
        skill_id: str,
        *,
        rollouts: list[Rollout],
        observation: dict[str, Any] | None = None,
        goal: dict[str, Any] | None = None,
    ) -> TrainReport:
        ...


class ImaginationTrainer:
    """Evaluate imagination until metrics pass. Does not update policy weights."""

    def train(
        self,
        skill_id: str,
        *,
        rollouts: list[Rollout],
        observation: dict[str, Any] | None = None,
        goal: dict[str, Any] | None = None,
    ) -> TrainReport:
        _ = observation
        n = len(rollouts)
        successes = sum(1 for r in rollouts if r.success and r.physics_ok)
        if skill_id in {"local_goal_reaching", "maze_escape"} and goal:
            pose_ok = 0
            has_pose = False
            for r in rollouts:
                extra = getattr(r, "extra", None) or {}
                pose = extra.get("pose") if isinstance(extra, dict) else None
                if isinstance(pose, dict):
                    has_pose = True
                    if maze_success(pose, goal, float(goal.get("radius") or 0.75)):
                        pose_ok += 1
            if has_pose:
                ok = (pose_ok / float(n) >= 0.25) if n else False
            else:
                ok = successes >= 1 and n > 0
        else:
            ok = successes >= 1 and n > 0
        return TrainReport(
            ok=ok,
            method="imagination_eval",
            skill_id=skill_id,
            rounds=1,
            note="Imagination eval only — no weight update.",
            metrics={"n_rollouts": n, "successes": successes},
        )


class PolicyTrainer:
    """Registered hook for RL / IL / diffusion. Safe no-op until a backend is attached."""

    def __init__(self, backend: Any | None = None) -> None:
        self.backend = backend

    def train(
        self,
        skill_id: str,
        *,
        rollouts: list[Rollout],
        observation: dict[str, Any] | None = None,
        goal: dict[str, Any] | None = None,
    ) -> TrainReport:
        if self.backend is None:
            fallback = ImaginationTrainer().train(
                skill_id, rollouts=rollouts, observation=observation, goal=goal
            )
            fallback.method = "policy_unwired_fallback_imagination"
            fallback.note = "No policy backend registered; evaluated imagination only."
            return fallback
        result = self.backend.fit(skill_id, rollouts=rollouts, observation=observation, goal=goal)
        if isinstance(result, TrainReport):
            return result
        return TrainReport(ok=bool(result), method="policy", skill_id=skill_id)


def default_trainer() -> SkillTrainer:
    return ImaginationTrainer()
