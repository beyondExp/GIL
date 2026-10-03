from __future__ import annotations

"""
Project-local IsaacLab tasks.

This package mirrors the structure of `isaaclab_tasks` but lives inside this repo so we can
add environments (e.g. maze escape) without forking IsaacLab itself.
"""

import gymnasium as gym


def _register_envs() -> None:
    # Manager-based RL environment with H1 in a maze.
    gym.register(
        id="Isaac-Maze-Escape-H1-v0",
        entry_point="isaaclab.envs:ManagerBasedRLEnv",
        disable_env_checker=True,
        kwargs={
            "env_cfg_entry_point": "gil_isaaclab_tasks.manager_based.maze_escape.config.h1.maze_env_cfg:MazeEscapeH1EnvCfg",
        },
    )
    gym.register(
        id="Isaac-Velocity-Flat-H1-Maze-v0",
        entry_point="isaaclab.envs:ManagerBasedRLEnv",
        disable_env_checker=True,
        kwargs={
            "env_cfg_entry_point": "gil_isaaclab_tasks.manager_based.maze_escape.config.h1.locomotion_maze_env_cfg:H1FlatMazeEnvCfg",
        },
    )


_register_envs()

