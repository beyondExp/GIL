from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass

import isaaclab.sim as sim_utils
from isaaclab.assets import AssetBaseCfg, RigidObjectCfg, RigidObjectCollectionCfg
from isaaclab.envs import ManagerBasedRLEnvCfg
from isaaclab.managers import EventTermCfg as EventTerm
from isaaclab.managers import ObservationGroupCfg as ObsGroup
from isaaclab.managers import ObservationTermCfg as ObsTerm
from isaaclab.managers import RewardTermCfg as RewTerm
from isaaclab.managers import SceneEntityCfg
from isaaclab.managers import TerminationTermCfg as DoneTerm
from isaaclab.scene import InteractiveSceneCfg
from isaaclab.sensors import CameraCfg
from isaaclab.sim import SimulationCfg
from isaaclab.terrains import TerrainImporterCfg
from isaaclab.utils.assets import ISAAC_NUCLEUS_DIR, ISAACLAB_NUCLEUS_DIR
from isaaclab.utils.configclass import configclass

import isaaclab_tasks.manager_based.navigation.mdp as mdp

from isaaclab_assets import H1_MINIMAL_CFG  # isort: skip


# NOTE: This IsaacLab task intentionally avoids importing `gil.*`.
# Importing a submodule like `gil.world.maze3d` executes `gil.world.__init__` which
# pulls in the broader GIL stack (settings, auth, etc.). That makes the task runner
# fragile inside the pip-installed Isaac Sim Python environment.
@dataclass(frozen=True)
class MazeSpec:
    width: int = 9
    height: int = 9
    cell_size: float = 2.0
    wall_thickness: float = 0.05
    wall_height: float = 1.0
    origin_x: float = -4.0
    origin_y: float = -4.0
    wall_center_z: float = 0.5
    seed: int = 0
    goal_x: float = 0.0
    goal_y: float = 0.0
    goal_z: float = 0.15
    goal_radius: float = 0.4


@dataclass
class Box3D:
    name: str
    cx: float
    cy: float
    cz: float
    sx: float
    sy: float
    sz: float


@dataclass
class Maze3D:
    spec: MazeSpec
    walls: list[Box3D]
    start: tuple[float, float, float]
    goal: tuple[float, float, float]


def _build_wall_boxes(spec: MazeSpec, v_walls: list[list[bool]], h_walls: list[list[bool]]) -> list[Box3D]:
    walls: list[Box3D] = []
    idx = 0
    for y in range(spec.height):
        for x in range(spec.width + 1):
            if not v_walls[y][x]:
                continue
            wx = spec.origin_x + x * spec.cell_size
            wy = spec.origin_y + (y + 0.5) * spec.cell_size
            walls.append(
                Box3D(
                    name=f"wall_v_{idx}",
                    cx=float(wx),
                    cy=float(wy),
                    cz=float(spec.wall_center_z),
                    sx=float(spec.wall_thickness),
                    sy=float(spec.cell_size + spec.wall_thickness),
                    sz=float(spec.wall_height),
                )
            )
            idx += 1
    for y in range(spec.height + 1):
        for x in range(spec.width):
            if not h_walls[y][x]:
                continue
            wx = spec.origin_x + (x + 0.5) * spec.cell_size
            wy = spec.origin_y + y * spec.cell_size
            walls.append(
                Box3D(
                    name=f"wall_h_{idx}",
                    cx=float(wx),
                    cy=float(wy),
                    cz=float(spec.wall_center_z),
                    sx=float(spec.cell_size + spec.wall_thickness),
                    sy=float(spec.wall_thickness),
                    sz=float(spec.wall_height),
                )
            )
            idx += 1
    return walls


def generate_maze(spec: MazeSpec | None = None) -> Maze3D:
    spec = spec or MazeSpec()
    rng = random.Random(spec.seed)
    visited = [[False for _ in range(spec.width)] for _ in range(spec.height)]
    v_walls = [[True for _ in range(spec.width + 1)] for _ in range(spec.height)]
    h_walls = [[True for _ in range(spec.width)] for _ in range(spec.height + 1)]

    def neighbors(cx: int, cy: int):
        dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        rng.shuffle(dirs)
        for dx, dy in dirs:
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < spec.width and 0 <= ny < spec.height and not visited[ny][nx]:
                yield nx, ny

    stack = [(0, 0)]
    visited[0][0] = True
    while stack:
        cx, cy = stack[-1]
        nxt = None
        for nx, ny in neighbors(cx, cy):
            nxt = (nx, ny)
            break
        if nxt is None:
            stack.pop()
            continue
        nx, ny = nxt
        if nx == cx + 1:
            v_walls[cy][cx + 1] = False
        elif nx == cx - 1:
            v_walls[cy][cx] = False
        elif ny == cy + 1:
            h_walls[cy + 1][cx] = False
        elif ny == cy - 1:
            h_walls[cy][cx] = False
        visited[ny][nx] = True
        stack.append((nx, ny))

    # Create entrance/exit openings.
    h_walls[0][0] = False
    h_walls[spec.height][spec.width - 1] = False

    # Keep goal tied to the grid scale.
    gx = spec.origin_x + (spec.width - 0.5) * spec.cell_size
    gy = spec.origin_y + (spec.height - 0.5) * spec.cell_size
    spec = MazeSpec(**{**asdict(spec), "goal_x": float(gx), "goal_y": float(gy)})

    walls = _build_wall_boxes(spec, v_walls, h_walls)
    sx = spec.origin_x + 0.5 * spec.cell_size
    sy = spec.origin_y + 0.5 * spec.cell_size
    return Maze3D(spec=spec, walls=walls, start=(float(sx), float(sy), 0.0), goal=(spec.goal_x, spec.goal_y, spec.goal_z))


# Keep the maze spec centralized so both the scene and command config agree.
_MAZE_SPEC = MazeSpec(
    width=5,
    height=5,
    cell_size=2.0,
    wall_thickness=0.05,
    wall_height=1.0,
    origin_x=-4.0,
    origin_y=-4.0,
    wall_center_z=0.5,
    seed=0,
    goal_radius=0.4,
)
_MAZE = generate_maze(_MAZE_SPEC)


def _root_z_below_minimum(env, minimum_height: float, asset_cfg: SceneEntityCfg) -> "torch.Tensor":
    """Robust fall detector returning shape (num_envs,)."""
    import torch  # local import to keep module import lightweight

    asset = env.scene[asset_cfg.name]
    root_pos = asset.data.root_pos_w.torch  # expected (..., 3) but backend shapes vary
    z = root_pos[..., 2]
    z_flat = z.reshape(z.shape[0], -1)
    z_min = torch.min(z_flat, dim=1).values
    return z_min < float(minimum_height)


def _safe_physx_cfgs():
    """
    Rigid + collision property cfgs live in isaaclab_physx.
    In non-IsaacSim lint contexts (plain python), those imports may be unavailable.
    """

    try:
        rigid = sim_utils.RigidBodyPropertiesCfg(kinematic_enabled=True, disable_gravity=True)
    except Exception:
        rigid = None
    try:
        coll = sim_utils.CollisionPropertiesCfg()
    except Exception:
        coll = None
    return rigid, coll


@configclass
class MazeEscapeSceneCfg(InteractiveSceneCfg):
    """H1 + a procedurally generated maze (static walls)."""

    # Flat ground.
    terrain = TerrainImporterCfg(
        prim_path="/World/ground",
        terrain_type="plane",
        terrain_generator=None,
        max_init_terrain_level=None,
        collision_group=-1,
        physics_material=sim_utils.RigidBodyMaterialCfg(
            friction_combine_mode="multiply",
            restitution_combine_mode="multiply",
            static_friction=1.0,
            dynamic_friction=1.0,
        ),
        # NOTE: Avoid custom MDL materials here for maximum compatibility with
        # different Isaac Sim builds (Kit command signatures can differ).
        visual_material=None,
        debug_vis=False,
    )

    # Robot.
    robot = H1_MINIMAL_CFG.replace(prim_path="{ENV_REGEX_NS}/Robot")

    # Robot cameras (minimal suite).
    fpv_cam = CameraCfg(
        prim_path="{ENV_REGEX_NS}/Robot/FPVCamera",
        spawn=sim_utils.PinholeCameraCfg(
            focal_length=24.0,
            horizontal_aperture=20.955,
            clipping_range=(0.05, 200.0),
        ),
        # Keep streamed frames small; the controls websocket defaults to ~1MiB max message size.
        # 960x540 RGBA payloads can exceed this once base64-encoded.
        width=320,
        height=180,
        data_types=["rgb"],
        update_latest_camera_pose=True,
        offset=CameraCfg.OffsetCfg(
            # Approximate head/torso mount. Refine once we bind to the actual H1 link frames.
            # Keep FPV below wall tops so maze walls are visible.
            pos=(0.25, 0.0, 0.20),
            rot=(0.0, 0.0, 0.0, 1.0),
            convention="world",
        ),
    )

    chase_cam = CameraCfg(
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

    # Static maze walls (rigid object collection).
    maze: RigidObjectCollectionCfg = RigidObjectCollectionCfg(rigid_objects={})

    # Lights.
    sky_light = AssetBaseCfg(
        prim_path="/World/skyLight",
        spawn=sim_utils.DomeLightCfg(
            intensity=750.0,
            texture_file=f"{ISAAC_NUCLEUS_DIR}/Materials/Textures/Skies/PolyHaven/kloofendal_43d_clear_puresky_4k.hdr",
        ),
    )

    def __post_init__(self) -> None:
        super().__post_init__()

        # Start with a single environment as a "template environment" users can extend later.
        self.num_envs = 1
        self.env_spacing = 20.0

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
        self.maze = RigidObjectCollectionCfg(rigid_objects=walls)

        # Extra yaw damping vs the stock H1 USD (angular_damping=0.0).
        try:
            self.robot.spawn.rigid_props.linear_damping = 0.05
            self.robot.spawn.rigid_props.angular_damping = 0.8
        except Exception:
            pass


@configclass
class CommandsCfg:
    """Command terms for the MDP."""

    pose_command = mdp.UniformPose2dCommandCfg(
        asset_name="robot",
        simple_heading=False,
        # Keep one fixed goal for "escape the maze" (re-sample extremely rarely).
        resampling_time_range=(9999.0, 9999.0),
        debug_vis=True,
        ranges=mdp.UniformPose2dCommandCfg.Ranges(
            pos_x=(float(_MAZE.goal[0]), float(_MAZE.goal[0])),
            pos_y=(float(_MAZE.goal[1]), float(_MAZE.goal[1])),
            heading=(0.0, 0.0),
        ),
    )


@configclass
class ActionsCfg:
    """Action terms for the MDP (trainable low-level controller)."""

    joint_pos = mdp.JointPositionActionCfg(asset_name="robot", joint_names=[".*"], scale=0.5, use_default_offset=True)


@configclass
class ObservationsCfg:
    """Observation specifications for the MDP."""

    @configclass
    class PolicyCfg(ObsGroup):
        # observation terms (order preserved)
        base_lin_vel = ObsTerm(func=mdp.base_lin_vel)
        base_ang_vel = ObsTerm(func=mdp.base_ang_vel)
        projected_gravity = ObsTerm(func=mdp.projected_gravity)
        joint_pos = ObsTerm(func=mdp.joint_pos_rel)
        joint_vel = ObsTerm(func=mdp.joint_vel_rel)
        actions = ObsTerm(func=mdp.last_action)

        def __post_init__(self):
            self.enable_corruption = False
            self.concatenate_terms = True

    policy: PolicyCfg = PolicyCfg()


@configclass
class EventsCfg:
    """Configuration for events."""

    reset_base = EventTerm(
        func=mdp.reset_root_state_uniform,
        mode="reset",
        params={
            # Keep start near the canonical maze spawn (cell 0,0 center).
            "pose_range": {"x": (float(_MAZE.start[0]), float(_MAZE.start[0])), "y": (float(_MAZE.start[1]), float(_MAZE.start[1])), "yaw": (-math.pi, math.pi)},
            "velocity_range": {
                "x": (-0.0, 0.0),
                "y": (-0.0, 0.0),
                "z": (-0.0, 0.0),
                "roll": (-0.0, 0.0),
                "pitch": (-0.0, 0.0),
                "yaw": (-0.0, 0.0),
            },
        },
    )

    reset_robot_joints = EventTerm(
        func=mdp.reset_joints_by_scale,
        mode="reset",
        params={"position_range": (1.0, 1.0), "velocity_range": (0.0, 0.0)},
    )


@configclass
class RewardsCfg:
    """Reward terms for the MDP."""

    termination_penalty = RewTerm(func=mdp.is_terminated, weight=-400.0)
    action_rate_l2 = RewTerm(func=mdp.action_rate_l2, weight=-0.01)


@configclass
class TerminationsCfg:
    """Termination terms for the MDP."""

    time_out = DoneTerm(func=mdp.time_out, time_out=True)
    fall = DoneTerm(func=_root_z_below_minimum, params={"minimum_height": 0.55, "asset_cfg": SceneEntityCfg("robot")})


@configclass
class MazeEscapeH1EnvCfg(ManagerBasedRLEnvCfg):
    """Manager-based environment: H1 commanded to reach the maze exit pose."""

    sim: SimulationCfg = SimulationCfg(dt=0.005, render_interval=2)
    scene: MazeEscapeSceneCfg = MazeEscapeSceneCfg()

    observations: ObservationsCfg = ObservationsCfg()
    actions: ActionsCfg = ActionsCfg()
    # Disable command manager for now (goal/pose commands rely on robot root pose tensor shapes
    # that differ across backends). This keeps the env runnable for live smoke tests.
    commands = None
    rewards: RewardsCfg = RewardsCfg()
    terminations: TerminationsCfg = TerminationsCfg()
    events: EventsCfg = EventsCfg()

    # Number of simulation steps per control step.
    decimation: int = 4

    episode_length_s: float = 60.0

