#!/usr/bin/env python3
"""Full maze walk -- the H1 walks the ENTIRE dreamed plan through the maze.

No resets, no shortcuts. The robot dreams a path, then walks every single step.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession


MCP_URL = "http://127.0.0.1:6769/mcp/"
STEP_INTERVAL = 0.25


async def call(session: ClientSession, tool: str, args: dict | None = None) -> dict:
    try:
        res = await asyncio.wait_for(session.call_tool(tool, args or {}), timeout=20.0)
        text = res.content[0].text if res.content else "{}"
        return json.loads(text)
    except Exception as exc:
        return {"_error": str(exc)}


async def get_pos(session: ClientSession) -> dict:
    obs = await call(session, "get_observation", {"robot_kind": "humanoid"})
    base = obs.get("state", {}).get("base", {})
    return {"x": float(base.get("x", 0)), "y": float(base.get("y", 0)), "z": float(base.get("z", 0))}


async def main() -> int:
    async with streamablehttp_client(MCP_URL, timeout=20, sse_read_timeout=20) as (read, write, _):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=10.0)

            # 1. Reset episode so robot starts fresh at maze spawn
            print("Resetting episode...")
            await call(session, "send_humanoid_heartbeat", {"source": "maze_walk"})
            await call(session, "reset_humanoid_episode")
            await asyncio.sleep(3.0)

            start_pos = await get_pos(session)
            print(f"Start: x={start_pos['x']:.3f} y={start_pos['y']:.3f} z={start_pos['z']:.3f}")

            # 2. Dream the maze escape plan
            print("\nDreaming escape plan...")
            steer = await call(session, "steer", {
                "instruction": "escape the maze by reaching the goal cell",
                "embodiment": "unitree_h1",
                "world_source": "text",
                "world_content": "isaac maze seed 0",
                "commit": False,
            })

            dream = steer.get("dream") or {}
            gate = dream.get("gate") or {}
            commands = dream.get("preview_commands", [])
            print(f"Decision: {steer.get('decision')}")
            print(f"Gate: ok={gate.get('ok')} reason={gate.get('reason')} gate_id={gate.get('gate_id')}")
            print(f"Plan: {len(commands)} steps to execute")

            if not commands:
                print("No commands to execute!")
                return 1

            # 3. Enable motion
            print("\nEnabling motion...")
            await call(session, "send_humanoid_heartbeat", {"source": "maze_walk"})
            await call(session, "set_humanoid_mode", {"mode": "external"})
            await call(session, "enable_humanoid_motion", {"reason": "full_maze_walk"})
            await asyncio.sleep(0.5)

            # 4. Execute EVERY step
            print(f"\n{'='*60}")
            print(f"  EXECUTING FULL PLAN ({len(commands)} steps)")
            print(f"{'='*60}\n")

            t0 = time.time()
            success_count = 0
            error_count = 0
            last_report = 0

            for i, cmd in enumerate(commands):
                await call(session, "send_humanoid_heartbeat", {"source": "maze_walk"})

                vx = float(cmd.get("vx", 0.0))
                vy = float(cmd.get("vy", 0.0))
                wz = float(cmd.get("wz", 0.0))

                result = await call(session, "execute_action", {
                    "action_kind": "cmd_vel",
                    "robot_kind": "humanoid",
                    "vx": vx, "vy": vy, "wz": wz,
                    "duration_s": STEP_INTERVAL,
                    "reason": f"maze_step_{i}",
                })

                ok = result.get("status") == "success"
                if ok:
                    success_count += 1
                else:
                    error_count += 1
                    err = result.get("error", "unknown")
                    if "fallen" in str(err).lower() or "stale" in str(err).lower():
                        print(f"\n  Step {i}: ROBOT FELL or stale state -- stopping")
                        break

                elapsed = time.time() - t0
                if elapsed - last_report >= 3.0 or i == len(commands) - 1:
                    pos = await get_pos(session)
                    dx = pos["x"] - start_pos["x"]
                    dy = pos["y"] - start_pos["y"]
                    dist = (dx**2 + dy**2) ** 0.5
                    action = "FWD" if abs(vx) > 0.1 else ("TURN" if abs(wz) > 0.1 else "IDLE")
                    print(f"  [{i+1:3d}/{len(commands)}] {action} vx={vx:+.2f} wz={wz:+.2f} | "
                          f"pos=({pos['x']:+.2f}, {pos['y']:+.2f}) | "
                          f"dist={dist:.2f}m | "
                          f"ok={success_count} err={error_count} | {elapsed:.0f}s")
                    last_report = elapsed

                await asyncio.sleep(STEP_INTERVAL)

            # 5. Stop and report
            await call(session, "disable_humanoid_motion", {"reason": "walk_complete"})
            end_pos = await get_pos(session)
            total_dx = end_pos["x"] - start_pos["x"]
            total_dy = end_pos["y"] - start_pos["y"]
            total_dist = (total_dx**2 + total_dy**2) ** 0.5
            elapsed = time.time() - t0

            print(f"\n{'='*60}")
            print(f"  WALK COMPLETE")
            print(f"{'='*60}")
            print(f"  Start:    ({start_pos['x']:+.3f}, {start_pos['y']:+.3f})")
            print(f"  End:      ({end_pos['x']:+.3f}, {end_pos['y']:+.3f})")
            print(f"  Distance: {total_dist:.3f}m")
            print(f"  Steps:    {success_count} ok / {error_count} err / {len(commands)} total")
            print(f"  Time:     {elapsed:.1f}s")

            metrics = await call(session, "get_metrics")
            print(f"  Metrics:  {json.dumps(metrics.get('counters', {}))}")

            if total_dist < 0.5:
                print(f"\n  FAIL: robot barely moved ({total_dist:.3f}m)")
                return 1
            print(f"\n  PASS: robot walked {total_dist:.2f}m through the maze")
            return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(asyncio.wait_for(main(), timeout=300.0)))
