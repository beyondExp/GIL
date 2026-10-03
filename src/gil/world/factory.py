from __future__ import annotations

from typing import Any

from gil.world.dream import MockWorldModel, WorldModel
from gil.world.ingest import WorldSpec
from gil.world.kinematic3d import Kinematic3DWorldModel
from gil.world.maze3d import generate_maze
from gil.world.physics_dream import PhysicsWorldModel
from gil.world.scene_graph import SceneGraphWorldModel


def world_model_for(
    spec: WorldSpec | None = None,
    *,
    robot_type: str = "humanoid_biped",
    observation: dict[str, Any] | None = None,
) -> WorldModel:
    """Pick an imagination backend from scene + morphology. Never sends motors."""
    obs = observation or (spec.observation if spec else {}) or {}
    maze = spec.maze if spec is not None else None
    generator = spec.generator if spec is not None else "maze_dfs"

    is_arm = robot_type in {"manipulator_arm", "mobile_manipulator"}
    has_arm_obs = obs.get("end_effector") is not None
    if is_arm or (has_arm_obs and maze is None):
        return SceneGraphWorldModel()

    if generator in {"maze_dfs", "hf_occupancy"} or maze is not None:
        model_maze = maze if maze is not None else generate_maze()
        if generator in {"isaac_clone", "physics"}:
            return PhysicsWorldModel(maze=model_maze)
        return Kinematic3DWorldModel(maze=model_maze)

    if generator in {"physics", "isaac_clone"}:
        return PhysicsWorldModel(maze=maze)

    return MockWorldModel()
