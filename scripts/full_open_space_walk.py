#!/usr/bin/env python3
"""H1 open space forward walk -- walk forward in a straight line for as long as the dream plans.

Tests the dream → execute loop without maze walls.
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


async def run_walk(session: ClientSession, label: str, instruction: str, world_content: str) -> dict:
    """Run one dream → execute → report cycle. Returns summary dict."""
    print(f"\n{'='*60}")
    print(f"  SCENARIO: {label}")
    print(f"{'='*60}")

    await call(session, "send_humanoid_heartbeat", {"source": label})
    await call(session, "reset_humanoid_episode")
    await asyncio.sleep(3.0)

    start = await get_pos(session)
    print(f"  Start: ({start['x']:+.3f}, {start['y']:+.3f})")

    print(f"  Dreaming: '{instruction}'...")
    steer = await call(session, "steer", {
        "instruction": instruction,
        "embodiment": "unitree_h1",
        "world_source": "text",
        "world_content": world_content,
        "commit": False,
    })

    dream = steer.get("dream") or {}
    gate = dream.get("gate") or {}
    commands = dream.get("preview_commands", [])
    print(f"  Gate: ok={gate.get('ok')} | {len(commands)} commands")

    if not commands:
        print(f"  No commands -- decision={steer.get('decision')}")
        return {"label": label, "ok": False, "reason": "no_commands"}

    await call(session, "send_humanoid_heartbeat", {"source": label})
    await call(session, "set_humanoid_mode", {"mode": "external"})
    await call(session, "enable_humanoid_motion", {"reason": label})
    await asyncio.sleep(0.5)

    t0 = time.time()
    ok_count = 0
    err_count = 0
    last_report = 0

    for i, cmd in enumerate(commands):
        await call(session, "send_humanoid_heartbeat", {"source": label})
        vx = float(cmd.get("vx", 0.0))
        vy = float(cmd.get("vy", 0.0))
        wz = float(cmd.get("wz", 0.0))

        result = await call(session, "execute_action", {
            "action_kind": "cmd_vel",
            "robot_kind": "humanoid",
            "vx": vx, "vy": vy, "wz": wz,
            "duration_s": STEP_INTERVAL,
            "reason": f"{label}_step_{i}",
        })

        if result.get("status") == "success":
            ok_count += 1
        else:
            err_count += 1
            err = str(result.get("error", ""))
            if "fallen" in err.lower() or "stale" in err.lower():
                print(f"  Step {i}: ABORT ({err})")
                break

        elapsed = time.time() - t0
        if elapsed - last_report >= 4.0 or i == len(commands) - 1:
            pos = await get_pos(session)
            dx, dy = pos["x"] - start["x"], pos["y"] - start["y"]
            dist = (dx**2 + dy**2) ** 0.5
            print(f"  [{i+1:3d}/{len(commands)}] vx={vx:+.2f} wz={wz:+.2f} | "
                  f"pos=({pos['x']:+.2f}, {pos['y']:+.2f}) dist={dist:.2f}m | {elapsed:.0f}s")
            last_report = elapsed

        await asyncio.sleep(STEP_INTERVAL)

    await call(session, "disable_humanoid_motion", {"reason": f"{label}_done"})
    end = await get_pos(session)
    total = ((end["x"] - start["x"])**2 + (end["y"] - start["y"])**2) ** 0.5
    elapsed = time.time() - t0

    print(f"  End: ({end['x']:+.3f}, {end['y']:+.3f}) | total={total:.2f}m | {ok_count}ok/{err_count}err | {elapsed:.0f}s")
    return {"label": label, "ok": True, "dist": total, "steps_ok": ok_count, "steps_err": err_count}


async def main() -> int:
    scenarios = [
        ("maze_escape",      "escape the maze by following the corridors to the exit",
         "isaac maze seed 0"),
        ("open_space_walk",   "walk forward in a straight line as far as possible",
         "flat open space"),
        ("turn_and_explore",  "turn 90 degrees right then walk straight",
         "flat open space"),
    ]

    results = []
    async with streamablehttp_client(MCP_URL, timeout=20, sse_read_timeout=20) as (read, write, _):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=10.0)

            for label, instruction, world in scenarios:
                r = await run_walk(session, label, instruction, world)
                results.append(r)
                await asyncio.sleep(2.0)

    print(f"\n{'='*60}")
    print(f"  SUMMARY -- 3 H1 Scenarios")
    print(f"{'='*60}")
    all_ok = True
    for r in results:
        passed = r.get("ok") and r.get("dist", 0) > 0.3
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_ok = False
        dist = r.get("dist", 0)
        print(f"  [{status}] {r['label']:25s} dist={dist:.2f}m  steps={r.get('steps_ok', 0)}ok/{r.get('steps_err', 0)}err")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(asyncio.wait_for(main(), timeout=600.0)))
