from __future__ import annotations

import pytest

from gil.core.profiles import load_profile
from gil.learn.eval_harness import EpisodeResult, beats_baseline, evaluate
from gil.learn.lerobot_writer import EpisodeFrame, LeRobotWriter
from gil.orchestrator import Orchestrator
from gil.orchestrator.live_controls import SupervisorControls
from gil.world.kinematic import KinematicWorldModel
from gil.world.twin import UnicycleTwin

pytestmark = [pytest.mark.phase8, pytest.mark.integration]


def _wall(x: float = 1.0, y0: float = -4.0, y1: float = 4.0, step: float = 0.25) -> list[dict[str, float]]:
    out = []
    y = y0
    while y <= y1:
        out.append({"x": x, "y": y})
        y += step
    return out


def _open_obs():
    return {
        "base": {"x": 0.0, "y": 0.0, "yaw": 0.0},
        "objects": [{"label": "exit", "x": 1.5, "y": 0.0, "occupied": False}],
    }


def _blocked_obs():
    return {
        "base": {"x": 0.0, "y": 0.0, "yaw": 0.0},
        "objects": [{"label": "exit", "x": 2.0, "y": 0.0, "occupied": False}],
        "obstacles": _wall(x=1.0, y0=-8.0, y1=8.0, step=0.25),
    }


def _stack(twin: UnicycleTwin | None = None):
    profile = load_profile("unitree_h1_sim")
    twin = twin or UnicycleTwin()
    controls = SupervisorControls(profile, twin)

    def factory(_profile):
        return controls

    orch = Orchestrator(
        controls_factory=factory,
        world_model=KinematicWorldModel(profile=profile, cell_m=0.25, dt=0.2),
    )
    orch.connect_robot("unitree_h1_sim", robot_id="h1")
    return orch, controls, twin


def test_blocked_maze_fails_gate_and_does_not_move():
    orch, controls, twin = _stack()
    orch.set_goal("h1", {"x": 2.0, "y": 0.0, "radius": 0.4, "language": "reach exit"})
    result = orch.run_mission("h1", _blocked_obs(), n_dreams=8)
    assert result.executed is False
    assert twin.pose.x == pytest.approx(0.0)
    assert twin.pose.y == pytest.approx(0.0)
    motor = [row for row in controls.sent if row["source"] == "orchestrator"]
    assert motor == []


def test_open_maze_passes_gate_and_twin_moves_toward_goal():
    orch, controls, twin = _stack()
    orch.set_goal("h1", {"x": 1.5, "y": 0.0, "radius": 0.45, "language": "reach exit"})
    result = orch.run_mission("h1", _open_obs(), n_dreams=8)
    assert result.executed is True, result.reason
    assert result.gate and result.gate.ok
    assert twin.distance_to(1.5, 0.0) < 1.5
    assert twin.pose.x > 0.05
    assert any(row["source"] == "orchestrator" for row in controls.sent)
    assert all(row["source"] != "world_model" for row in controls.sent)


def test_real_safety_supervisor_blocks_world_model_on_live_twin():
    orch, controls, twin = _stack()
    orch.set_goal("h1", {"x": 1.5, "y": 0.0, "radius": 0.45})
    orch.run_mission("h1", _open_obs(), n_dreams=8)
    sneaky = controls.send("h1", {"type": "cmd_vel", "vx": 0.4}, source="groot")
    assert sneaky.success is False


def test_estop_prevents_execution_after_dreams():
    orch, controls, twin = _stack()
    controls.safety.set_estop(True, reason="test")
    orch.set_goal("h1", {"x": 1.5, "y": 0.0, "radius": 0.45})
    result = orch.run_mission("h1", _open_obs(), n_dreams=8)
    assert result.executed is False
    assert twin.pose.x == pytest.approx(0.0)


def test_executed_mission_is_recorded_as_lerobot_and_beats_scripted_baseline(tmp_path):
    orch, controls, twin = _stack()
    orch.set_goal("h1", {"x": 1.5, "y": 0.0, "radius": 0.45, "language": "reach exit"})
    result = orch.run_mission("h1", _open_obs(), n_dreams=8)
    assert result.executed is True

    writer = LeRobotWriter(tmp_path / "h1_maze", repo_id="gil/h1-maze")
    frames = [
        EpisodeFrame(0.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], "reach exit", robot_id="h1"),
        EpisodeFrame(
            1.0,
            [twin.pose.x, twin.pose.y, twin.pose.yaw],
            [0.2, 0.0, 0.0],
            "reach exit",
            done=True,
            success=True,
            robot_id="h1",
        ),
    ]
    writer.add_episode(frames)
    root = writer.close()
    assert (root / "meta" / "info.json").is_file()

    scripted = evaluate("maze", [EpisodeResult("maze", False, 90.0), EpisodeResult("maze", True, 40.0)])
    gated = evaluate("maze", [EpisodeResult("maze", True, 4.0)])
    assert beats_baseline(gated, scripted)
