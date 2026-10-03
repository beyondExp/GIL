from __future__ import annotations

import pytest

from gil.core.profiles import load_profile
from gil.orchestrator import Orchestrator
from gil.orchestrator.live_controls import SupervisorControls
from gil.world.kinematic3d import Kinematic3DWorldModel
from gil.world.maze3d import generate_maze
from gil.world.twin import UnicycleTwin

pytestmark = [pytest.mark.phase8, pytest.mark.integration]


def _stack(maze):
    profile = load_profile("unitree_h1_sim")
    twin = UnicycleTwin()
    twin.pose.x, twin.pose.y = maze.start[0], maze.start[1]
    controls = SupervisorControls(profile, twin)
    orch = Orchestrator(
        controls_factory=lambda _p: controls,
        world_model=Kinematic3DWorldModel(maze=maze, profile=profile, dt=0.2),
    )
    orch.connect_robot("unitree_h1_sim", robot_id="h1")
    return orch, controls, twin


def test_isaac_maze_gate_pass_moves_the_twin_along_corridors():
    maze = generate_maze()
    orch, controls, twin = _stack(maze)
    orch.set_goal("h1", {"x": maze.goal[0], "y": maze.goal[1], "radius": 0.45, "language": "escape the maze"})
    result = orch.run_mission("h1", maze.to_observation(), n_dreams=8)
    assert result.executed is True, result.reason
    assert twin.distance_to(maze.goal[0], maze.goal[1]) < 1.2
    assert any(row["source"] == "orchestrator" for row in controls.sent)
    assert all(row["source"] != "world_model" for row in controls.sent)


def test_sealed_isaac_maze_blocks_the_gate_and_the_twin():
    maze = generate_maze().sealed_start()
    orch, controls, twin = _stack(maze)
    start = (twin.pose.x, twin.pose.y)
    orch.set_goal("h1", {"x": maze.goal[0], "y": maze.goal[1], "radius": 0.45, "language": "escape the maze"})
    result = orch.run_mission("h1", maze.to_observation(), n_dreams=8)
    assert result.executed is False
    assert twin.pose.x == pytest.approx(start[0])
    assert twin.pose.y == pytest.approx(start[1])
    assert [row for row in controls.sent if row["source"] == "orchestrator"] == []
