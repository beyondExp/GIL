"""End-to-end mission: perceive -> map -> dream -> gate -> execute."""

from __future__ import annotations

import pytest

from gil.learn.eval_harness import EpisodeResult, beats_baseline, evaluate
from gil.orchestrator import Orchestrator
from gil.world.dream import MockWorldModel

pytestmark = pytest.mark.phase5


def test_gated_policy_beats_scripted_baseline_in_imagination():
    orch = Orchestrator(world_model=MockWorldModel(successes=8, critic_score=0.92))
    orch.connect_robot("unitree_h1_sim", robot_id="h1")
    orch.set_goal("h1", {"x": 3.5, "y": 3.5, "radius": 0.6, "language": "escape the maze"})
    observation = {
        "base": {"x": 0.0, "y": 0.0, "yaw": 0.0},
        "objects": [{"label": "exit", "x": 3.5, "y": 3.5}],
    }
    result = orch.run_mission("h1", observation)
    assert result.executed is True

    scripted = evaluate("maze", [EpisodeResult("maze", False, 90.0), EpisodeResult("maze", True, 40.0)])
    gated = evaluate("maze", [EpisodeResult("maze", True, 12.0, extra={"gate": result.reason})])
    assert beats_baseline(gated, scripted)
