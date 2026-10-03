from __future__ import annotations

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
    a = float(a)
    while a > math.pi:
        a -= 2.0 * math.pi
    while a < -math.pi:
        a += 2.0 * math.pi
    return a


async def main() -> None:
    url = "http://127.0.0.1:6769/mcp/"
    async with streamablehttp_client(url, timeout=60, sse_read_timeout=60) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await _call(session, "set_humanoid_mode", {"mode": "external"})
            pre = await _call(session, "run_humanoid_preflight", {})
            print("preflight:", pre, flush=True)

            await _call(session, "disable_humanoid_motion", {"reason": "cmdvel_dir_check"})
            await _call(session, "reset_humanoid_episode", {"x": -1.9, "y": -1.9, "yaw": 0.0})
            await asyncio.sleep(0.8)
            await _call(session, "enable_humanoid_motion", {"reason": "cmdvel_dir_check"})

            st0 = await _call(session, "get_robot_state_for", {"robot_kind": "humanoid"})
            b0 = st0.get("base") or {}
            x0, y0, yaw0 = float(b0.get("x", 0.0)), float(b0.get("y", 0.0)), float(b0.get("yaw", 0.0))
            print(f"base0=({x0:+.3f},{y0:+.3f}) yaw0={yaw0:+.3f}", flush=True)

            # Forward pulse
            await _call(
                session,
                "drive_humanoid",
                {"vx": 0.10, "vy": 0.0, "wz": 0.0, "duration_s": 1.0, "reason": "cmdvel_dir_check_forward"},
            )
            t0 = time.time()
            while (time.time() - t0) < 1.6:
                await _call(session, "send_humanoid_heartbeat", {"source": "cmdvel_dir_check"})
                await asyncio.sleep(0.2)

            st1 = await _call(session, "get_robot_state_for", {"robot_kind": "humanoid"})
            b1 = st1.get("base") or {}
            x1, y1, yaw1 = float(b1.get("x", 0.0)), float(b1.get("y", 0.0)), float(b1.get("yaw", 0.0))
            dx, dy = x1 - x0, y1 - y0
            dyaw = _wrap_pi(yaw1 - yaw0)
            print(f"after_fwd=({x1:+.3f},{y1:+.3f}) yaw={yaw1:+.3f}  d=({dx:+.3f},{dy:+.3f}) dyaw={dyaw:+.3f}", flush=True)

            # Turn pulse
            await _call(
                session,
                "drive_humanoid",
                {"vx": 0.0, "vy": 0.0, "wz": 0.25, "duration_s": 1.0, "reason": "cmdvel_dir_check_turn"},
            )
            t1 = time.time()
            while (time.time() - t1) < 1.6:
                await _call(session, "send_humanoid_heartbeat", {"source": "cmdvel_dir_check"})
                await asyncio.sleep(0.2)

            st2 = await _call(session, "get_robot_state_for", {"robot_kind": "humanoid"})
            b2 = st2.get("base") or {}
            yaw2 = float(b2.get("yaw", 0.0))
            dyaw2 = _wrap_pi(yaw2 - yaw1)
            print(f"after_turn yaw={yaw2:+.3f}  dyaw={dyaw2:+.3f}", flush=True)


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=120.0))

