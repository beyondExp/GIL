from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def _call(session: ClientSession, name: str, args: dict[str, Any]) -> dict[str, Any]:
    res = await session.call_tool(name, args)
    txt = res.content[0].text if res.content else "{}"
    try:
        out = json.loads(txt)
        return out if isinstance(out, dict) else {"value": out}
    except Exception:
        return {"_raw": txt}


def _wrap_pi(a: float) -> float:
    return float((a + math.pi) % (2 * math.pi) - math.pi)

async def _wait_for_pose(session: ClientSession, *, min_z: float, timeout_s: float = 12.0) -> dict[str, float]:
    t0 = time.time()
    last = {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0}
    while (time.time() - t0) < timeout_s:
        st = await _call(session, "get_robot_state_for", {"robot_kind": "humanoid"})
        b = st.get("base") or {}
        last = {
            "x": float(b.get("x", 0.0)),
            "y": float(b.get("y", 0.0)),
            "z": float(b.get("z", 0.0)),
            "yaw": float(b.get("yaw", 0.0)),
        }
        if last["z"] >= float(min_z):
            return last
        await asyncio.sleep(0.1)
    return last


async def main() -> None:
    ap = argparse.ArgumentParser(description="Open-space H1 goal demo (no maze walls).")
    ap.add_argument("--url", default="http://127.0.0.1:6769/mcp/")
    ap.add_argument("--goal_x", type=float, default=3.0)
    ap.add_argument("--goal_y", type=float, default=3.0)
    ap.add_argument("--goal_radius", type=float, default=0.75)
    ap.add_argument("--runtime_s", type=float, default=120.0)
    ap.add_argument("--fall_z", type=float, default=0.45)
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    gx, gy = float(args.goal_x), float(args.goal_y)

    async with streamablehttp_client(args.url, timeout=25, sse_read_timeout=25) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Use goal mode (H1 policy is noticeably more stable than raw cmd_vel in many cases).
            await _call(session, "set_humanoid_mode", {"mode": "goal"})
            pre = await _call(session, "run_humanoid_preflight", {})
            print("preflight:", pre, flush=True)

            # Keep motion disabled during reset/settle.
            await _call(session, "disable_humanoid_motion", {"reason": "open_space_demo"})
            if args.reset:
                await _call(session, "reset_humanoid_episode", {})
                await _wait_for_pose(session, min_z=float(args.fall_z), timeout_s=12.0)

            en = await _call(session, "enable_humanoid_motion", {"reason": "open_space_demo"})
            print("enable:", en, flush=True)
            await _call(session, "set_humanoid_goal", {"x": gx, "y": gy})

            t0 = time.time()
            last_print = 0.0
            while (time.time() - t0) < float(args.runtime_s):
                st = await _call(session, "get_robot_state_for", {"robot_kind": "humanoid"})
                b = st.get("base") or {}
                x = float(b.get("x", 0.0))
                y = float(b.get("y", 0.0))
                z = float(b.get("z", 0.0))
                yaw = float(b.get("yaw", 0.0))

                dx = gx - x
                dy = gy - y
                d = float((dx * dx + dy * dy) ** 0.5)

                now = time.time()
                if (now - last_print) > 1.0:
                    last_print = now
                    print(f"t={now-t0:5.1f}s  x={x:+.2f} y={y:+.2f} z={z:+.2f} yaw={yaw:+.2f}  d_goal={d:.2f}", flush=True)

                if d <= float(args.goal_radius):
                    await _call(session, "stop_humanoid_now", {"reason": "goal_reached"})
                    print("Reached goal radius.", flush=True)
                    return

                # Fall safety
                if 0.0 < z < float(args.fall_z):
                    await _call(session, "stop_humanoid_now", {"reason": "fall_detected"})
                    await _call(session, "disable_humanoid_motion", {"reason": "fall_detected"})
                    if args.reset:
                        await _call(session, "reset_humanoid_episode", {})
                        await _wait_for_pose(session, min_z=float(args.fall_z), timeout_s=12.0)
                        await _call(session, "enable_humanoid_motion", {"reason": "post_fall_reset"})
                        await _call(session, "set_humanoid_goal", {"x": gx, "y": gy})
                    continue

                await _call(session, "send_humanoid_heartbeat", {"source": "open_space_goal_demo"})
                await asyncio.sleep(0.20)

            await _call(session, "stop_humanoid_now", {"reason": "timeout"})
            print("Timed out before reaching goal.", flush=True)


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=300.0))

