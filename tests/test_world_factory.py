"""Tests for pluggable world model factory."""
from __future__ import annotations

import pytest

from gil.world.factory import world_model_for
from gil.world.kinematic3d import Kinematic3DWorldModel
from gil.world.physics_dream import PhysicsWorldModel
from gil.world.scene_graph import SceneGraphWorldModel
from gil.world.dream import MockWorldModel
from gil.world.ingest import WorldSpec
from gil.world.maze3d import generate_maze
from gil.core.types import Goal

pytestmark = pytest.mark.phase4


class TestWorldModelFactory:
    def test_arm_gets_scene_graph(self):
        model = world_model_for(robot_type="manipulator_arm")
        assert isinstance(model, SceneGraphWorldModel)

    def test_observation_with_end_effector_gets_scene_graph(self):
        model = world_model_for(observation={"end_effector": {"x": 0.4, "y": 0.2, "z": 0.3}})
        assert isinstance(model, SceneGraphWorldModel)

    def test_maze_spec_gets_kinematic3d(self):
        maze = generate_maze()
        spec = WorldSpec(generator="maze_dfs", live=True, source="text", content="maze", maze=maze)
        model = world_model_for(spec)
        assert isinstance(model, Kinematic3DWorldModel)

    def test_no_spec_wheeled_defaults_to_kinematic(self):
        model = world_model_for(robot_type="wheeled_base")
        assert isinstance(model, Kinematic3DWorldModel)


class TestSceneGraphDream:
    def test_dream_with_objects(self):
        model = SceneGraphWorldModel()
        obs = {
            "objects": [{"name": "cube_red", "color": "red", "position": {"x": 0.5, "y": 0.0, "z": 0.12}}],
            "end_effector": {"x": 0.4, "y": 0.2, "z": 0.3},
        }
        goal = Goal(language="pick the red cube", x=0.6, y=0.0)
        rollouts = model.dream(obs, goal, n=4)
        assert len(rollouts) == 4
        assert any(r.success for r in rollouts)
        assert any("move_robot" in str(r.commands) for r in rollouts)

    def test_dream_no_objects(self):
        model = SceneGraphWorldModel()
        obs = {}
        goal = Goal(language="pick something", x=None, y=None)
        rollouts = model.dream(obs, goal, n=2)
        assert all(not r.success for r in rollouts)


class TestPhysicsDream:
    def test_with_maze(self):
        maze = generate_maze()
        model = PhysicsWorldModel(maze=maze)
        obs = maze.to_observation()
        goal = Goal(language="walk to exit", x=maze.goal[0], y=maze.goal[1])
        rollouts = model.dream(obs, goal, n=4)
        assert len(rollouts) == 4
        assert all("physics_headless:" in r.note for r in rollouts)

    def test_without_maze(self):
        model = PhysicsWorldModel()
        rollouts = model.dream({}, Goal(language="go"), n=2)
        assert len(rollouts) == 2
        assert all("unwired" in r.note for r in rollouts)
