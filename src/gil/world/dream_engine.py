from __future__ import annotations

import os
from typing import Any

from gil.core.types import Goal
from gil.world.dream import Rollout, WorldModel
from gil.world.physics_dream import PhysicsWorldModel


def dream_rollouts(
    *,
    world_model: WorldModel,
    observation: dict[str, Any],
    goal: Goal,
    n: int,
) -> list[Rollout]:
    """Dream rollouts with optional multi-fidelity verification.

    Product invariant:
    - Coarse rollouts are always generated first (fast).
    - Optional fine rollouts re-check only the best candidates (slow).
    - Output remains a `list[Rollout]` compatible with existing gate/critic logic.
    """
    n = int(max(1, n))
    coarse = list(world_model.dream(observation, goal, n=n))

    enabled = str(os.getenv("GIL_DREAM_MULTI_FIDELITY", "0") or "0").strip().lower() in ("1", "true", "yes", "on")
    if not enabled or not coarse:
        return coarse

    k = int(os.getenv("GIL_DREAM_FINE_K", "2") or 2)
    k = max(0, min(k, len(coarse)))
    if k <= 0:
        return coarse

    maze = getattr(world_model, "maze", None)
    if maze is None:
        return coarse

    fine_model = PhysicsWorldModel(maze=maze)
    # Pick top-k by critic score (best-effort; stable ordering).
    ranked = sorted(range(len(coarse)), key=lambda i: float(getattr(coarse[i], "critic_score", 0.0) or 0.0), reverse=True)
    top_idx = ranked[:k]
    fine = list(fine_model.dream(observation, goal, n=k))
    for j, i in enumerate(top_idx):
        if j >= len(fine):
            break
        fine[j].note = f"fine_verify:{fine[j].note}"
        coarse[i] = fine[j]
    return coarse

