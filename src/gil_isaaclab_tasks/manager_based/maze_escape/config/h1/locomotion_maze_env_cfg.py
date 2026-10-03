from __future__ import annotations

"""H1 locomotion template with the maze scene swapped in.

This reuses Isaac Lab's velocity-tracking H1 stack (actions, rewards, observations)
and only replaces the terrain/scene geometry with GIL maze walls.
"""

from isaaclab.assets import RigidObjectCfg, RigidObjectCollectionCfg
from isaaclab.sensors import CameraCfg
from isaaclab.utils.configclass import configclass

from gil_isaaclab_tasks.manager_based.maze_escape.config.h1.maze_env_cfg import (
    _MAZE,
    _safe_physx_cfgs,
)
import isaaclab.sim as sim_utils

try:
    from isaaclab_tasks.manager_based.locomotion.velocity.config.h1.flat_env_cfg import H1FlatEnvCfg
except ImportError:  # pragma: no cover - only available inside Isaac Lab
    H1FlatEnvCfg = object  # type: ignore[misc, assignment]


@configclass
class H1FlatMazeEnvCfg(H1FlatEnvCfg):  # type: ignore[valid-type]
    """Isaac-Velocity-Flat-H1 with maze walls and a safe cell spawn."""

    def __post_init__(self):
        super().__post_init__()
        rigid_props, collision_props = _safe_physx_cfgs()
        walls: dict[str, RigidObjectCfg] = {}
        for i, w in enumerate(_MAZE.walls):
            name = f"wall_{i:03d}"
            walls[name] = RigidObjectCfg(
                prim_path=f"{{ENV_REGEX_NS}}/{name}",
                spawn=sim_utils.CuboidCfg(
                    size=(float(w.sx), float(w.sy), float(w.sz)),
                    rigid_props=rigid_props,
                    collision_props=collision_props,
                ),
                init_state=RigidObjectCfg.InitialStateCfg(
                    pos=(float(w.cx), float(w.cy), float(w.cz)),
                    rot=(0.0, 0.0, 0.0, 1.0),
                ),
            )
        self.scene.maze = RigidObjectCollectionCfg(rigid_objects=walls)
        try:
            self.scene.robot.spawn.rigid_props.linear_damping = 0.05
            self.scene.robot.spawn.rigid_props.angular_damping = 0.8
        except Exception:
            pass
        sx, sy, _sz = _MAZE.start
        if hasattr(self, "events") and hasattr(self.events, "reset_base"):
            self.events.reset_base.params["pose_range"] = {
                "x": (float(sx), float(sx)),
                "y": (float(sy), float(sy)),
                "yaw": (1.57, 1.57),
            }
            self.events.reset_base.params["velocity_range"] = {
                "x": (0.0, 0.0),
                "y": (0.0, 0.0),
                "z": (0.0, 0.0),
                "roll": (0.0, 0.0),
                "pitch": (0.0, 0.0),
                "yaw": (0.0, 0.0),
            }

        # Add FPV + chase cameras so GIL can run vision-closed-loop while using the velocity-commanded locomotion stack.
        # Keep frames small for websocket streaming.
        try:
            self.scene.fpv_cam = CameraCfg(
                prim_path="{ENV_REGEX_NS}/Robot/FPVCamera",
                spawn=sim_utils.PinholeCameraCfg(
                    focal_length=24.0,
                    horizontal_aperture=20.955,
                    clipping_range=(0.05, 200.0),
                ),
                width=320,
                height=180,
                data_types=["rgb"],
                update_latest_camera_pose=True,
                offset=CameraCfg.OffsetCfg(
                    pos=(0.25, 0.0, 0.20),
                    rot=(0.0, 0.0, 0.0, 1.0),
                    convention="world",
                ),
            )
            self.scene.chase_cam = CameraCfg(
                prim_path="{ENV_REGEX_NS}/Robot/ChaseCamera",
                spawn=sim_utils.PinholeCameraCfg(
                    focal_length=18.0,
                    horizontal_aperture=20.955,
                    clipping_range=(0.05, 250.0),
                ),
                width=320,
                height=180,
                data_types=["rgb"],
                update_latest_camera_pose=True,
                offset=CameraCfg.OffsetCfg(
                    pos=(-2.5, 0.0, 1.6),
                    rot=(0.0, 0.0, 0.0, 1.0),
                    convention="world",
                ),
            )
        except Exception:
            pass
