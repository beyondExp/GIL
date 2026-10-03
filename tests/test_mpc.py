from __future__ import annotations

from gil.world.maze3d import generate_maze
from gil.world.mpc import MpcConfig, mpc_select_cmd


def test_mpc_select_cmd_returns_bounded_cmd() -> None:
    maze = generate_maze()
    # Aim one cell ahead from an interior-ish location.
    x, y = maze.cell_center(1, 1)
    target = maze.cell_center(2, 1)
    cfg = MpcConfig(rollouts=800, horizon_steps=6, dt_s=0.25, base_vx=0.10, wz_cap=0.25, inflate_m=0.12)
    cmd = mpc_select_cmd(maze=maze, x=float(x), y=float(y), yaw=0.0, target=target, cfg=cfg)
    assert abs(cmd.wz) <= cfg.wz_cap + 1e-6
    assert abs(cmd.vx) <= cfg.base_vx + 1e-6


def test_mpc_prefers_forward_progress_when_possible() -> None:
    maze = generate_maze()
    x, y = maze.cell_center(1, 1)
    target = maze.cell_center(2, 1)
    cfg = MpcConfig(rollouts=1200, horizon_steps=8, dt_s=0.25, base_vx=0.10, wz_cap=0.25, inflate_m=0.12)
    cmd = mpc_select_cmd(maze=maze, x=float(x), y=float(y), yaw=0.0, target=target, cfg=cfg)
    # If the corridor is open, the best first action should not be a pure backup.
    assert cmd.vx >= -1e-6

