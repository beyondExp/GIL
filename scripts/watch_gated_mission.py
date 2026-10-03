"""Watch the GIL dream-then-act loop inside the Isaac H1 maze.

Default: local 3D maze twin (no Isaac needed). You see dreams, the gate, then motion.

Live robot (frontend maze viewer already running):
  python scripts/watch_gated_mission.py --live
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "gil_controls" / "src"))

from gil.core.profiles import load_profile
from gil.core.types import Goal
from gil.orchestrator import Orchestrator
from gil.orchestrator.live_controls import SupervisorControls
from gil.world.kinematic import KinematicWorldModel
from gil.world.kinematic3d import Kinematic3DWorldModel
from gil.world.map import SceneMap
from gil.world.maze3d import generate_maze
from gil.world.twin import UnicycleTwin


def _wall(x: float = 1.0, y0: float = -4.0, y1: float = 4.0, step: float = 0.25):
    y = y0
    out = []
    while y <= y1:
        out.append({"x": x, "y": y})
        y += step
    return out


def render_map(scene: SceneMap, pose, goal: Goal, trail: list[tuple[float, float]] | None = None) -> str:
    extent = 8
    grid = {}
    for cx, cy in scene.occupied:
        grid[(cx, cy)] = "#"
    if trail:
        for x, y in trail:
            grid[scene._cell(x, y)] = "*"
    rx, ry = scene._cell(float(pose.x), float(pose.y))
    gx, gy = scene._cell(float(goal.x or 0.0), float(goal.y or 0.0))
    grid[(gx, gy)] = "G"
    grid[(rx, ry)] = "R"
    lines = []
    for y in range(extent, -extent - 1, -1):
        row = []
        for x in range(-2, extent + 1):
            row.append(grid.get((x, y), "."))
        lines.append(" ".join(row))
    return "\n".join(lines)


def banner(title: str) -> None:
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def run_maze(sealed: bool) -> None:
    profile = load_profile("unitree_h1_sim")
    maze = generate_maze().sealed_start() if sealed else generate_maze()
    twin = UnicycleTwin()
    twin.pose.x, twin.pose.y = maze.start[0], maze.start[1]
    controls = SupervisorControls(profile, twin)
    orch = Orchestrator(
        controls_factory=lambda _p: controls,
        world_model=Kinematic3DWorldModel(maze=maze, profile=profile, dt=0.2),
    )
    orch.connect_robot("unitree_h1_sim", robot_id="h1")

    goal = {"x": maze.goal[0], "y": maze.goal[1], "radius": 0.45, "language": "escape the maze"}
    observation = maze.to_observation({"x": maze.start[0], "y": maze.start[1], "z": 0.0, "yaw": 0.0})
    orch.set_goal("h1", goal)
    g = Goal.from_dict(goal)
    scene_name = "SEALED SPAWN (gate should BLOCK)" if sealed else "ISAAC MAZE SEED 0 (gate should PASS)"

    banner(scene_name)
    print(f"Walls: {len(maze.walls)}  start=({maze.start[0]:.2f},{maze.start[1]:.2f})  goal=({maze.goal[0]:.2f},{maze.goal[1]:.2f})")
    print("Legend: R=robot  G=goal  |/-=walls")
    print("Before dreaming:\n")
    print(maze.render_ascii(twin.pose.x, twin.pose.y, g.x, g.y))

    wm = orch.world_model
    dreams = wm.dream(observation, g, n=8)
    print(f"\nDreams: {len(dreams)} imagined")
    for i, rollout in enumerate(dreams):
        kept_flag = rollout.physics_ok and rollout.success and rollout.critic_score >= 0.7
        mark = "KEEP" if kept_flag else "drop"
        print(
            f"  [{i}] {mark:4} physics_ok={rollout.physics_ok} "
            f"success={rollout.success} score={rollout.critic_score:.2f} "
            f"steps={len(rollout.commands)} ({rollout.note})"
        )

    result = orch.run_mission("h1", observation, n_dreams=8)
    print(f"\nGate: {'PASS' if result.executed else 'BLOCK'}  reason={result.reason}")
    if result.gate:
        scores = result.gate.scores
        print(
            f"  imagination={scores.get('imagination_success'):.2f}  "
            f"critic={scores.get('critic_score'):.2f}  "
            f"map={scores.get('map_coverage'):.2f}  "
            f"preflight={scores.get('preflight_ok')}"
        )

    if result.executed:
        print(f"\nExecuted {len(result.commands)} cmd_vel steps through SafetySupervisor")
        print(f"Final pose: x={twin.pose.x:.2f} y={twin.pose.y:.2f} yaw={twin.pose.yaw:.2f}")
    else:
        print("\nNo motor commands were sent. Twin stayed put.")
        print(f"Final pose: x={twin.pose.x:.2f} y={twin.pose.y:.2f}")

    print("\nAfter:\n")
    print(maze.render_ascii(twin.pose.x, twin.pose.y, g.x, g.y))


def run_flat(scenario: str) -> None:
    profile = load_profile("unitree_h1_sim")
    twin = UnicycleTwin()
    controls = SupervisorControls(profile, twin)
    orch = Orchestrator(
        controls_factory=lambda _p: controls,
        world_model=KinematicWorldModel(profile=profile, cell_m=0.25, dt=0.2),
    )
    orch.connect_robot("unitree_h1_sim", robot_id="h1")

    if scenario == "blocked":
        goal = {"x": 2.0, "y": 0.0, "radius": 0.4, "language": "reach the exit"}
        observation = {
            "base": {"x": 0.0, "y": 0.0, "yaw": 0.0},
            "objects": [{"label": "exit", "x": 2.0, "y": 0.0, "occupied": False}],
            "obstacles": _wall(),
        }
    else:
        goal = {"x": 1.5, "y": 0.0, "radius": 0.45, "language": "reach the exit"}
        observation = {
            "base": {"x": 0.0, "y": 0.0, "yaw": 0.0},
            "objects": [{"label": "exit", "x": 1.5, "y": 0.0, "occupied": False}],
        }

    orch.set_goal("h1", goal)
    scene = orch.maps["h1"]
    g = Goal.from_dict(goal)
    scene.ingest(observation)

    banner(f"FLAT OCCUPANCY: {scenario.upper()}")
    print("Legend: R=robot  G=goal  #=wall  *=path  .=free")
    print("Before dreaming:\n")
    print(render_map(scene, twin.pose, g))

    result = orch.run_mission("h1", observation, n_dreams=8)
    print(f"\nGate: {'PASS' if result.executed else 'BLOCK'}  reason={result.reason}")
    print(f"Final pose: x={twin.pose.x:.2f} y={twin.pose.y:.2f}")
    print("\nAfter:\n")
    print(render_map(scene, twin.pose, g, trail=[(twin.pose.x, twin.pose.y)]))


async def replay_live(url: str, commands: list[dict], duration_s: float = 0.15) -> None:
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url, timeout=20, sse_read_timeout=20) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool("set_humanoid_mode", {"mode": "external"})
            pre = await session.call_tool("run_humanoid_preflight", {})
            print("preflight:", pre.content[0].text if pre.content else pre)
            enable = await session.call_tool("enable_humanoid_motion", {"reason": "watch_gated_mission"})
            print("enable:", enable.content[0].text if enable.content else enable)
            await session.call_tool("send_humanoid_heartbeat", {"source": "watch"})
            sent = 0
            for cmd in commands[:80]:
                await session.call_tool("send_humanoid_heartbeat", {"source": "watch"})
                res = await session.call_tool(
                    "drive_humanoid",
                    {
                        "vx": float(cmd.get("vx", 0.0)),
                        "vy": float(cmd.get("vy", 0.0)),
                        "wz": float(cmd.get("wz", 0.0)),
                        "duration_s": float(cmd.get("dt", duration_s)),
                        "reason": "gated maze plan replay",
                    },
                )
                sent += 1
                if sent % 8 == 0:
                    st = await session.call_tool("get_robot_state_for", {"robot_kind": "humanoid"})
                    print("live state:", (st.content[0].text if st.content else "")[:240])
            await session.call_tool("stop_humanoid_now", {"reason": "watch_complete"})
            print(f"Replayed {sent} gated commands on {url}")


def plan_maze_mission():
    profile = load_profile("unitree_h1_sim")
    maze = generate_maze()
    twin = UnicycleTwin()
    twin.pose.x, twin.pose.y = maze.start[0], maze.start[1]
    controls = SupervisorControls(profile, twin)
    orch = Orchestrator(
        controls_factory=lambda _p: controls,
        world_model=Kinematic3DWorldModel(maze=maze, profile=profile, dt=0.2),
    )
    orch.connect_robot("unitree_h1_sim", robot_id="h1")
    goal = {"x": maze.goal[0], "y": maze.goal[1], "radius": 0.45, "language": "escape the maze"}
    orch.set_goal("h1", goal)
    result = orch.run_mission("h1", maze.to_observation(), n_dreams=8)
    cmds = [row["command"] for row in controls.sent if row["source"] == "orchestrator"]
    return result, cmds


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="Replay a gated maze plan on gil_controls MCP")
    ap.add_argument("--flat", action="store_true", help="Old 2D occupancy demo instead of the Isaac maze")
    ap.add_argument("--url", default="http://127.0.0.1:6769/mcp/")
    args = ap.parse_args()

    if args.flat:
        run_flat("blocked")
        run_flat("open")
    else:
        run_maze(sealed=True)
        run_maze(sealed=False)

    if args.live:
        banner("LIVE REPLAY ON GIL_CONTROLS")
        result, cmds = plan_maze_mission()
        if not result.executed or not cmds:
            print("Gate did not pass locally; refusing to touch the robot.")
            return 1
        print(f"Replaying {len(cmds)} gated maze steps to {args.url}")
        print("Isaac H1 must already be connected to gil_controls (ws://127.0.0.1:8766).")
        try:
            asyncio.run(replay_live(args.url, cmds))
        except Exception as exc:
            print("Live MCP not reachable:", exc)
            print("Start gil_controls, then scripts/run_isaac_h1_maze_real.ps1, then re-run with --live.")
            return 2
    else:
        banner("WATCH IT ON THE ROBOT")
        print("This run dreamed in a copy of the Isaac maze. Live execution is Isaac Sim, not Three.js.")
        print("  1. python gil_controls/src/main.py")
        print("  2. scripts/run_isaac_h1_maze_real.ps1")
        print("  3. python scripts/watch_gated_mission.py --live")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
