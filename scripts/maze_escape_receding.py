#!/usr/bin/env python3
"""Live Isaac maze escape using receding-horizon dreaming.

Why: a single dreamed plan (e.g. 96 steps) often isn't long enough to reach the true exit
of a 9x9 maze. This script repeats:
  dream -> execute first chunk -> re-dream from new pose
until the robot is within the maze goal radius or progress stalls.

Safety: stops if distance-to-goal increases repeatedly.
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

# Control pacing
STEP_DT_S = 0.25
CHUNK_STEPS = 32  # execute this many preview cmds per dream
MAX_CHUNKS = 12  # upper bound on total runtime

# Stop conditions
STALL_EPS_M = 0.05  # if we don't get closer by this much, count as stall
MAX_BAD_CHUNKS = 2  # abort after N chunks moving away


async def call(session: ClientSession, tool: str, args: dict | None = None) -> dict:
    res = await session.call_tool(tool, args or {})
    text = res.content[0].text if res.content else "{}"
    return json.loads(text)


async def get_pos(session: ClientSession) -> tuple[float, float, float]:
    obs = await call(session, "get_observation", {"robot_kind": "humanoid"})
    base = (obs.get("state") or {}).get("base") or {}
    return float(base.get("x", 0.0) or 0.0), float(base.get("y", 0.0) or 0.0), float(base.get("z", 0.0) or 0.0)


def dist2d(a: tuple[float, float], b: tuple[float, float]) -> float:
    return float(math.hypot(a[0] - b[0], a[1] - b[1]))


async def execute_chunk(session: ClientSession, commands: list[dict], label: str) -> tuple[int, int]:
    ok = err = 0
    for i, cmd in enumerate(commands):
        await call(session, "send_humanoid_heartbeat", {"source": label})
        vx = float(cmd.get("vx", 0.0) or 0.0)
        vy = float(cmd.get("vy", 0.0) or 0.0)
        wz = float(cmd.get("wz", 0.0) or 0.0)
        res = await call(
            session,
            "execute_action",
            {
                "action_kind": "cmd_vel",
                "robot_kind": "humanoid",
                "vx": vx,
                "vy": vy,
                "wz": wz,
                "duration_s": STEP_DT_S,
                "reason": f"{label}_step_{i}",
            },
        )
        if res.get("status") == "success":
            ok += 1
        else:
            err += 1
            break
        await asyncio.sleep(STEP_DT_S)
    return ok, err


async def main() -> int:
    # Match Isaac defaults (run_isaac_h1_maze_real.ps1 + ingest_world text path)
    maze = generate_maze(MazeSpec(width=9, height=9, seed=0, cell_size=1.4))
    goal_xy = (float(maze.goal[0]), float(maze.goal[1]))
    goal_r = float(maze.spec.goal_radius)

    print(f"Goal: ({goal_xy[0]:+.2f}, {goal_xy[1]:+.2f}) r={goal_r:.2f}  (maze {maze.spec.width}x{maze.spec.height} cell={maze.spec.cell_size})")

    async with streamablehttp_client(MCP_URL, timeout=20, sse_read_timeout=20) as (read, write, _):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=10.0)

            print("Resetting episode...")
            await call(session, "send_humanoid_heartbeat", {"source": "maze_escape"})
            await call(session, "disable_humanoid_motion", {"reason": "reset"})
            await call(session, "reset_humanoid_episode")
            await asyncio.sleep(3.0)

            x0, y0, z0 = await get_pos(session)
            d0 = dist2d((x0, y0), goal_xy)
            print(f"Start: ({x0:+.2f}, {y0:+.2f}, z={z0:+.2f})  dist_to_exit={d0:.2f}m")

            bad = 0
            last_d = d0
            t0 = time.time()

            for chunk_idx in range(MAX_CHUNKS):
                label = f"chunk_{chunk_idx}"
                print(f"\n=== Dream {chunk_idx+1}/{MAX_CHUNKS} ===")
                steer = await call(
                    session,
                    "steer",
                    {
                        "instruction": "escape the maze by reaching the exit",
                        "embodiment": "unitree_h1",
                        "world_source": "text",
                        "world_content": "isaac maze seed 0",
                        "commit": False,
                    },
                )
                dream = steer.get("dream") or {}
                gate = dream.get("gate") or {}
                cmds = dream.get("preview_commands") or []
                print(f"  decision={steer.get('decision')} gate.ok={gate.get('ok')} cmds={len(cmds)}")
                if not cmds:
                    print("  No commands to execute; aborting.")
                    return 1

                # Enable motion + execute a chunk.
                await call(session, "send_humanoid_heartbeat", {"source": label})
                await call(session, "set_humanoid_mode", {"mode": "external"})
                en = await call(session, "enable_humanoid_motion", {"reason": label})
                if en.get("status") != "success":
                    print(f"  enable_motion failed: {en.get('error')}")
                    return 1
                await asyncio.sleep(0.4)

                chunk = list(cmds[:CHUNK_STEPS])
                ok, err = await execute_chunk(session, chunk, label)
                await call(session, "disable_humanoid_motion", {"reason": f"{label}_done"})

                x1, y1, z1 = await get_pos(session)
                d1 = dist2d((x1, y1), goal_xy)
                delta = float(last_d - d1)
                elapsed = time.time() - t0
                print(
                    f"  moved: ({x1:+.2f}, {y1:+.2f}, z={z1:+.2f})  "
                    f"dist_to_exit={d1:.2f}m  d={delta:+.2f}m  ok={ok} err={err}  t={elapsed:.0f}s"
                )

                if d1 <= goal_r:
                    print("\nPASS: reached exit radius")
                    return 0

                if delta < -STALL_EPS_M:
                    bad += 1
                    print(f"  WARNING: moved away from exit (bad_chunks={bad}/{MAX_BAD_CHUNKS})")
                    if bad >= MAX_BAD_CHUNKS:
                        print("FAIL: consistently moving away from exit; stopping for safety/debug")
                        return 1
                elif abs(delta) < STALL_EPS_M:
                    print("  WARNING: no meaningful progress this chunk; will re-dream")
                else:
                    bad = 0

                last_d = d1

            print("FAIL: max chunks reached without reaching exit")
            return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(asyncio.wait_for(main(), timeout=900.0)))

