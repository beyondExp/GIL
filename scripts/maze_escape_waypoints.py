#!/usr/bin/env python3
"""Live Isaac maze escape using waypoint goals (closed-loop goal mode).

This avoids open-loop cmd_vel drift by leveraging the Isaac extension's built-in
"goal" walker mode. We compute the maze shortest-path on the same DFS maze spec,
then send a sequence of intermediate (x,y) waypoints (cell centers).
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
import time

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from gil.world.maze3d import MazeSpec, generate_maze


MCP_URL = "http://127.0.0.1:6769/mcp/"

# Maze must match Isaac launch defaults (run_isaac_h1_maze_real.ps1)
MAZE_SPEC = MazeSpec(width=9, height=9, seed=0, cell_size=1.4)

# Waypoint tracking
# The H1 policy can be imprecise in tight corridors; accept a moderate waypoint radius.
WAYPOINT_RADIUS_M = 0.65
WAYPOINT_TIMEOUT_S = 90.0
HEARTBEAT_HZ = 5.0
# Treat <0.45m as a real fall; smaller dips can happen on uneven contacts.
MIN_UPRIGHT_Z_M = 0.45
MAX_RUN_CELLS = 3  # compress straight corridors to reduce stop/go overhead


async def call(session: ClientSession, tool: str, args: dict | None = None) -> dict:
    res = await session.call_tool(tool, args or {})
    text = res.content[0].text if res.content else "{}"
    return json.loads(text)


async def get_xy(session: ClientSession) -> tuple[float, float, float]:
    obs = await call(session, "get_observation", {"robot_kind": "humanoid"})
    base = (obs.get("state") or {}).get("base") or {}
    return float(base.get("x", 0.0) or 0.0), float(base.get("y", 0.0) or 0.0), float(base.get("z", 0.0) or 0.0)


def dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return float(math.hypot(a[0] - b[0], a[1] - b[1]))


async def main() -> int:
    maze = generate_maze(MAZE_SPEC)
    goal_xy = (float(maze.goal[0]), float(maze.goal[1]))

    async with streamablehttp_client(MCP_URL, timeout=20, sse_read_timeout=20) as (read, write, _):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=10.0)

            print("Resetting episode...")
            await call(session, "send_humanoid_heartbeat", {"source": "waypoints"})
            # Prevent the Isaac-side goal walker from self-driving while we reset/plan.
            await call(session, "set_humanoid_mode", {"mode": "external"})
            await call(session, "disable_humanoid_motion", {"reason": "reset"})
            await call(session, "reset_humanoid_episode")
            await asyncio.sleep(1.0)

            x0, y0, z0 = await get_xy(session)
            # Snap start to its inferred cell center to avoid planning from a drifted pose.
            scx, scy = maze.world_to_cell(float(x0), float(y0))
            start_xy = maze.cell_center(int(scx), int(scy))
            d0 = dist(start_xy, goal_xy)
            print(f"Start: ({x0:+.2f}, {y0:+.2f}, z={z0:+.2f}) dist_to_exit={d0:.2f}m")
            print(f"Exit:  ({goal_xy[0]:+.2f}, {goal_xy[1]:+.2f})")

            path = maze.shortest_cell_path(start_xy, goal_xy)
            if not path:
                print("FAIL: no cell path to exit")
                return 1
            # Compress the cell path into short straight runs to reduce waypoints without
            # making large corner-cuts that can clip walls and topple the robot.
            cells = list(path)
            compressed: list[tuple[int, int]] = []
            if len(cells) >= 2:
                i = 0
                while i < len(cells) - 1:
                    dx = int(cells[i + 1][0] - cells[i][0])
                    dy = int(cells[i + 1][1] - cells[i][1])
                    run_dir = (dx, dy)
                    j = i + 1
                    steps = 1
                    while j < len(cells) - 1 and steps < MAX_RUN_CELLS:
                        ndx = int(cells[j + 1][0] - cells[j][0])
                        ndy = int(cells[j + 1][1] - cells[j][1])
                        if (ndx, ndy) != run_dir:
                            break
                        j += 1
                        steps += 1
                    compressed.append(cells[j])
                    i = j
            waypoints = [maze.cell_center(cx, cy) for (cx, cy) in compressed]
            print(f"Path: {len(path)} cells -> {len(waypoints)} safe-waypoints (max_run={MAX_RUN_CELLS})")

            # Switch to goal mode once.
            await call(session, "send_humanoid_heartbeat", {"source": "waypoints"})
            await call(session, "set_humanoid_mode", {"mode": "goal"})
            en = await call(session, "enable_humanoid_motion", {"reason": "maze_escape_waypoints"})
            if en.get("status") != "success":
                print(f"FAIL: enable_motion: {en.get('error')}")
                return 1

            t0 = time.time()
            for i, (wx, wy) in enumerate(waypoints):
                await call(session, "send_humanoid_heartbeat", {"source": f"wp_{i}"})
                await call(session, "set_humanoid_goal", {"x": float(wx), "y": float(wy)})
                print(f"\nWP {i+1:02d}/{len(waypoints)}: goal=({wx:+.2f},{wy:+.2f})")

                t_wp = time.time()
                last_d = None
                within = 0
                away = 0
                while True:
                    await call(session, "send_humanoid_heartbeat", {"source": f"wp_{i}_hb"})
                    x, y, z = await get_xy(session)
                    if z != 0.0 and z < MIN_UPRIGHT_Z_M:
                        print(f"FAIL: fell (z={z:.2f} < {MIN_UPRIGHT_Z_M:.2f}); stopping")
                        await call(session, "disable_humanoid_motion", {"reason": "fall_detected"})
                        return 1
                    d_wp = dist((x, y), (wx, wy))
                    d_exit = dist((x, y), goal_xy)
                    if last_d is None or (time.time() - t_wp) < 1.0 or int((time.time() - t_wp) * 10) % 10 == 0:
                        print(f"  pos=({x:+.2f},{y:+.2f}) z={z:+.2f} d_wp={d_wp:.2f} d_exit={d_exit:.2f}")
                    if d_wp <= WAYPOINT_RADIUS_M:
                        within += 1
                        if within >= 3:
                            break
                    else:
                        within = 0
                    if (time.time() - t_wp) > WAYPOINT_TIMEOUT_S:
                        print("FAIL: waypoint timeout (stalled or blocked)")
                        await call(session, "disable_humanoid_motion", {"reason": "waypoint_timeout"})
                        return 1
                    # If we're consistently moving away from the waypoint, bail early.
                    # Single-step increases can happen while turning / oscillating in a tight corridor.
                    if last_d is not None:
                        if d_wp > last_d + 0.25:
                            away += 1
                        else:
                            away = max(0, away - 1)
                        if away >= 12 and (time.time() - t_wp) > 8.0:
                            print("FAIL: moving away from waypoint (likely wrong goal or stuck)")
                            await call(session, "disable_humanoid_motion", {"reason": "moving_away"})
                            return 1
                    last_d = d_wp
                    await asyncio.sleep(1.0 / HEARTBEAT_HZ)

            await call(session, "disable_humanoid_motion", {"reason": "done"})
            xf, yf, zf = await get_xy(session)
            df = dist((xf, yf), goal_xy)
            elapsed = time.time() - t0
            # "Exit" in the maze is the top-right cell. The locomotion policy may stop short of the
            # exact cell center, so also accept being inside the goal cell.
            goal_cell = maze.world_to_cell(float(goal_xy[0]), float(goal_xy[1]))
            cur_cell = maze.world_to_cell(float(xf), float(yf))
            if df <= float(maze.spec.goal_radius) or (cur_cell == goal_cell):
                print(f"\nPASS: reached exit in {elapsed:.0f}s (dist={df:.2f}m cell={cur_cell})")
                return 0
            print(f"\nDONE: waypoints complete but still dist_to_exit={df:.2f}m (elapsed {elapsed:.0f}s)")
            return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(asyncio.wait_for(main(), timeout=1800.0)))

