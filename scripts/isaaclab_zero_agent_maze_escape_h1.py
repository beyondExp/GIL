from __future__ import annotations

"""
Launch the project-local IsaacLab env `Isaac-Maze-Escape-H1-v0` with a zero-action agent.

This is intentionally self-contained:
- uses `isaaclab.app.AppLauncher` directly (does not import `isaaclab_tasks`, which eagerly imports all tasks)
- ensures IsaacLab sources are on `sys.path`
- imports `gil_isaaclab_tasks` so the custom env id is registered
"""

import argparse
import sys
from pathlib import Path

import gymnasium as gym
import torch


def _bootstrap_paths() -> None:
    repo = Path(__file__).resolve().parents[1]
    isaaclab_source = repo / "third_party" / "IsaacLab" / "source"
    # Ensure IsaacLab python packages are importable even when not installed as a wheel.
    #
    # IsaacLab is a mono-repo with multiple Python package roots:
    # - source/isaaclab/isaaclab (core)
    # - source/isaaclab_tasks/isaaclab_tasks (tasks + launch helpers)
    # - source/isaaclab_assets/isaaclab_assets (robot assets configs)
    #
    # Adding only `source/` is not sufficient because the package roots are one level deeper.
    for pkg_root in (
        "isaaclab",
        "isaaclab_assets",
        "isaaclab_tasks",
        "isaaclab_rl",
        "isaaclab_physx",
        "isaaclab_ovphysx",
        "isaaclab_newton",
        "isaaclab_contrib",
        "isaaclab_teleop",
        "isaaclab_visualizers",
    ):
        p = isaaclab_source / pkg_root
        if p.is_dir():
            sys.path.insert(0, str(p))
    # Ensure local `src/` packages are importable.
    sys.path.insert(0, str(repo / "src"))


_bootstrap_paths()

import gil_isaaclab_tasks  # noqa: F401  pylint: disable=wrong-import-position

from isaaclab.app import AppLauncher  # noqa: E402

parser = argparse.ArgumentParser(description="Zero agent for Isaac-Maze-Escape-H1-v0.")
parser.add_argument("--task", type=str, default="Isaac-Maze-Escape-H1-v0", help="Name of the task.")
parser.add_argument("--num_envs", type=int, default=None, help="Number of environments to simulate.")
parser.add_argument("--disable_fabric", action="store_true", default=False, help="Disable fabric and use USD I/O operations.")
AppLauncher.add_app_launcher_args(parser)
parser.set_defaults(visualizer="kit")
args_cli = parser.parse_args()

# Force-enable cameras for sensor rendering (some experience/config combos ignore the flag).
setattr(args_cli, "enable_cameras", True)


def main() -> None:
    torch.manual_seed(42)

    # Launch Isaac Sim (SimulationApp) via IsaacLab.
    print("[GIL][isaaclab] starting AppLauncher...", flush=True)
    app_launcher = AppLauncher(args_cli)
    _app = app_launcher.app
    print("[GIL][isaaclab] AppLauncher ready (SimulationApp running).", flush=True)

    # Ensure IsaacLab settings are backed by carb.settings and mark cameras enabled.
    try:
        from isaaclab.app.settings_manager import get_settings_manager  # noqa: E402

        sm = get_settings_manager()
        sm.initialize_carb_settings()
        sm.set("/isaaclab/cameras_enabled", True)
    except Exception as e:
        print(f"[GIL][isaaclab] settings init skipped: {e!r}", flush=True)

    # IMPORTANT: IsaacSim requires SimulationApp to be instantiated before importing
    # most Omniverse/Isaac modules. So we import the env config only after AppLauncher starts.
    print("[GIL][isaaclab] importing MazeEscapeH1EnvCfg...", flush=True)
    from gil_isaaclab_tasks.manager_based.maze_escape.config.h1.maze_env_cfg import (  # noqa: WPS433,E402
        MazeEscapeH1EnvCfg,
    )
    print("[GIL][isaaclab] imported MazeEscapeH1EnvCfg.", flush=True)

    # Build config (explicit, no hydra required).
    print("[GIL][isaaclab] building env config...", flush=True)
    env_cfg = MazeEscapeH1EnvCfg()
    if args_cli.num_envs is not None:
        env_cfg.scene.num_envs = int(args_cli.num_envs)
    if args_cli.device is not None:
        env_cfg.sim.device = str(args_cli.device)
    if bool(args_cli.disable_fabric):
        env_cfg.sim.use_fabric = False
    print(
        f"[GIL][isaaclab] making gym env id={args_cli.task} num_envs={env_cfg.scene.num_envs} device={env_cfg.sim.device}",
        flush=True,
    )

    env = gym.make(args_cli.task, cfg=env_cfg)
    print(f"[INFO]: Gym observation space: {env.observation_space}")
    print(f"[INFO]: Gym action space: {env.action_space}")
    print("[GIL][isaaclab] resetting env...", flush=True)
    env.reset()
    print("[GIL][isaaclab] env reset complete; stepping with zero actions.", flush=True)

    # Try to switch the viewport to the FPV camera if present.
    try:
        import omni.usd  # type: ignore
        import omni.kit.viewport.utility as vp_utils  # type: ignore

        stage = omni.usd.get_context().get_stage()
        fpv_path = None
        if stage is not None:
            for prim in stage.Traverse():
                if prim.GetName() == "FPVCamera":
                    fpv_path = str(prim.GetPath())
                    break
        if fpv_path:
            vp = vp_utils.get_active_viewport()
            if vp is not None:
                vp.camera_path = fpv_path
                print(f"[GIL][isaaclab] viewport camera set to {fpv_path}", flush=True)
    except Exception as e:
        print(f"[GIL][isaaclab] viewport camera switch skipped: {e!r}", flush=True)

    sim = env.unwrapped.sim
    actions = torch.zeros(env.action_space.shape, device=env.unwrapped.device)
    while True:
        if sim.visualizers:
            if not any(v.is_running() and not v.is_closed for v in sim.visualizers):
                break
        with torch.inference_mode():
            _obs, _rew, terminated, truncated, _info = env.step(actions)
            if bool(torch.any(terminated)) or bool(torch.any(truncated)):
                env.reset()

    env.close()
    _app.close()


if __name__ == "__main__":
    main()

