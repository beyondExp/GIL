from __future__ import annotations

import pytest

from gil.orchestrator import Orchestrator
from gil.orchestrator.controls import FakeControls
from gil.world.critic import PhysicsCritic
from gil.world.dream import MockWorldModel, Rollout

pytestmark = pytest.mark.phase5


def _obs():
    return {"base": {"x": 0.0, "y": 0.0}, "objects": [{"label": "exit", "x": 1.0, "y": 0.0}]}


def test_failed_dreams_do_not_move_the_robot():
    orch = Orchestrator(world_model=MockWorldModel(successes=0, failures=8, critic_score=0.1, physics_ok=False))
    orch.connect_robot("unitree_h1_sim", robot_id="h1")
    orch.set_goal("h1", {"x": 1.0, "y": 0.0, "language": "reach the exit"})
    result = orch.run_mission("h1", _obs())
    assert result.executed is False
    assert result.reason in {"imagination_unreliable", "critic_rejected", "map_coverage_low"}
    assert orch._controls["h1"].sent == []


def test_gate_pass_executes_via_orchestrator_not_world_model():
    orch = Orchestrator(world_model=MockWorldModel(successes=8, critic_score=0.95))
    orch.connect_robot("unitree_h1_sim", robot_id="h1")
    orch.set_goal("h1", {"x": 1.0, "y": 0.0})
    result = orch.run_mission("h1", _obs())
    assert result.executed is True
    assert result.gate and result.gate.ok
    sent = orch._controls["h1"].sent
    assert sent
    assert all(item["source"] in {"orchestrator", "safety"} for item in sent)
    assert any(item["command"].get("type") == "cmd_vel" for item in sent)


def test_critic_drops_physically_invalid_rollouts():
    critic = PhysicsCritic(min_score=0.7)
    bad = Rollout(success=True, critic_score=0.99, physics_ok=False, commands=[{"type": "cmd_vel"}])
    good = Rollout(success=True, critic_score=0.9, physics_ok=True, commands=[{"type": "cmd_vel", "vx": 0.1}])
    from gil.core.types import Goal

    kept = critic.filter([bad, good], Goal(x=1, y=0))
    assert len(kept) == 1
    assert kept[0].physics_ok is True


def test_controls_still_refuse_world_model_after_a_passing_gate():
    profile_controls = None

    def factory(profile):
        nonlocal profile_controls
        profile_controls = FakeControls(profile)
        return profile_controls

    orch = Orchestrator(controls_factory=factory, world_model=MockWorldModel(successes=8))
    orch.connect_robot("unitree_h1_sim", robot_id="h1")
    orch.set_goal("h1", {"x": 1.0, "y": 0.0})
    orch.run_mission("h1", _obs())
    sneaky = profile_controls.send("h1", {"type": "cmd_vel", "vx": 0.4}, source="cosmos")
    assert sneaky.success is False
