from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from gil.learn.curriculum import RobotType, curriculum_for

Kind = Literal["humanoid", "arm", "mobile"]


@dataclass(frozen=True)
class IsaacRobot:
    key: str
    display_name: str
    usd_rel: str
    profile_id: str
    kind: Kind
    locomotion: str
    notes: str = ""
    # NVIDIA Isaac Lab gym id to play (template). Empty = USD-only / not Lab.
    lab_task: str = ""
    # Gym id whose published RSL-RL checkpoint matches this body.
    pretrained_task: str = ""
    robot_type: RobotType = "humanoid_biped"
    # Workstation Kit/Lab launcher (scripts/launch_isaac_robot.ps1). Not a live USD hot-swap.
    kit_script: str = "scripts/run_isaac_h1_maze_real.ps1"
    kit_args: str = "-HumanoidVariant h1 -ForcePolicy -CameraCapture -CameraCollage"


# NVIDIA-shipped bodies + GIL maze overlay. Lab tasks are Isaac Lab 2.x template IDs.
ISAAC_ROBOTS: tuple[IsaacRobot, ...] = (
    IsaacRobot(
        "unitree_h1",
        "Unitree H1",
        "/Isaac/Robots/Unitree/H1/h1.usd",
        "unitree_h1_sim",
        "humanoid",
        "h1_policy",
        "Isaac Lab velocity template + published RSL-RL policy. GIL default.",
        lab_task="Isaac-Velocity-Flat-H1-Maze-v0",
        pretrained_task="Isaac-Velocity-Flat-H1-v0",
        robot_type="humanoid_biped",
        kit_script="scripts/run_isaac_h1_maze_real.ps1",
        kit_args="-HumanoidVariant h1 -ForcePolicy -CameraCapture -CameraCollage",
    ),
    IsaacRobot(
        "unitree_h1_2",
        "Unitree H1-2",
        "/Isaac/Robots/Unitree/H1_2/h1_2.usd",
        "unitree_h1_sim",
        "humanoid",
        "fallback",
        "Workstation: Unitree H1-2 USD + unitree_h1_2_sensors.json (run_isaac_h1_2_maze / -UseLabRobot).",
        lab_task="",
        pretrained_task="",
        robot_type="humanoid_biped",
        kit_script="scripts/run_isaac_h1_maze_real.ps1",
        kit_args="-HumanoidVariant h1_2 -UseLabRobot -CameraCapture -CameraCollage",
    ),
    IsaacRobot(
        "unitree_g1",
        "Unitree G1",
        "/Isaac/Robots/Unitree/G1_23dof/g1.usd",
        "unitree_h1_sim",
        "humanoid",
        "g1_policy",
        "Factory G1 is Isaac Lab Isaac-Velocity-Flat-G1-v0 (isaaclab_play_h1.py), not Kit Nucleus overlay.",
        lab_task="Isaac-Velocity-Flat-G1-v0",
        pretrained_task="Isaac-Velocity-Flat-G1-v0",
        robot_type="humanoid_biped",
        kit_script="scripts/isaaclab_play_h1.py",
        kit_args="--task Isaac-Velocity-Flat-G1-v0",
    ),
    IsaacRobot(
        "nvidia_humanoid",
        "NVIDIA Humanoid",
        "/Isaac/Robots/NVIDIA/Humanoid/humanoid.usd",
        "unitree_h1_sim",
        "humanoid",
        "fallback",
        "Classic Isaac humanoid USD.",
        lab_task="",
        pretrained_task="",
        robot_type="humanoid_biped",
    ),
    IsaacRobot(
        "franka",
        "Franka Panda",
        "/Isaac/Robots/Franka/franka.usd",
        "arm_sim",
        "arm",
        "arm",
        "Isaac Lab reach/lift templates. Not a maze walker.",
        lab_task="Isaac-Reach-Franka-v0",
        pretrained_task="Isaac-Reach-Franka-v0",
        robot_type="manipulator_arm",
        kit_script="scripts/isaaclab_play_h1.py",
        kit_args="--task Isaac-Stack-Cube-Franka-IK-Rel-Visuomotor-v0 --num_envs 1",
    ),
    IsaacRobot(
        "jetbot",
        "Jetbot",
        "/Isaac/Robots/Jetbot/jetbot.usd",
        "unitree_h1_sim",
        "mobile",
        "cmd_vel",
        "Differential drive. GIL cmd_vel contract.",
        lab_task="",
        pretrained_task="",
        robot_type="wheeled_base",
    ),
    IsaacRobot(
        "nova_carter",
        "Nova Carter",
        "/Isaac/Robots/NVIDIA/Carter/nova_carter.usd",
        "unitree_h1_sim",
        "mobile",
        "cmd_vel",
        "NVIDIA Carter. GIL cmd_vel contract.",
        lab_task="",
        pretrained_task="",
        robot_type="wheeled_base",
    ),
    IsaacRobot(
        "anymal",
        "ANYmal C",
        "/Isaac/Robots/ANYbotics/anymal_c/anymal_c.usd",
        "unitree_h1_sim",
        "mobile",
        "anymal_policy",
        "Isaac Lab ANYmal-C velocity template.",
        lab_task="Isaac-Velocity-Flat-Anymal-C-v0",
        pretrained_task="Isaac-Velocity-Flat-Anymal-C-v0",
        robot_type="quadruped",
    ),
    IsaacRobot(
        "go2",
        "Unitree Go2",
        "/Isaac/Robots/Unitree/Go2/go2.usd",
        "unitree_h1_sim",
        "mobile",
        "go2_policy",
        "Isaac Lab Go2 velocity template.",
        lab_task="Isaac-Velocity-Flat-Unitree-Go2-v0",
        pretrained_task="Isaac-Velocity-Flat-Unitree-Go2-v0",
        robot_type="quadruped",
    ),
)


def list_isaac_robots() -> list[dict[str, str]]:
    rows = []
    for r in ISAAC_ROBOTS:
        row = {k: str(v) for k, v in asdict(r).items()}
        row["curriculum"] = ",".join(s.id for s in curriculum_for(r.robot_type))
        rows.append(row)
    return rows


def launch_spec(robot: IsaacRobot) -> dict[str, str]:
    """How the workstation must spawn this body (reload Isaac; carb hot-swap is not a factory load)."""
    return {
        "script": robot.kit_script,
        "args": robot.kit_args,
        "command": f"{robot.kit_script} {robot.kit_args}".strip(),
    }


def get_isaac_robot(key: str) -> IsaacRobot:
    needle = (key or "").strip().lower().replace(" ", "_").replace("-", "_")
    for robot in ISAAC_ROBOTS:
        if robot.key == needle or robot.display_name.lower().replace(" ", "_") == needle:
            return robot
        if needle in robot.key or needle in robot.usd_rel.lower():
            return robot
    raise KeyError(f"Unknown Isaac robot '{key}'. Known: {[r.key for r in ISAAC_ROBOTS]}")
