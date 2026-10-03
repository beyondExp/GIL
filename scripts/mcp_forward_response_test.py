from __future__ import annotations

import asyncio
import json
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

async def _wait_for_pose(session: ClientSession, *, min_z: float, stable_samples: int = 4, timeout_s: float = 12.0) -> dict[str, float]:
    t0 = asyncio.get_event_loop().time()
    last = {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0}
    good = 0
    while (asyncio.get_event_loop().time() - t0) < timeout_s:
        st = await _call(session, "get_robot_state_for", {"robot_kind": "humanoid"})
        b = st.get("base") or {}
        last = {
            "x": float(b.get("x", 0.0)),
            "y": float(b.get("y", 0.0)),
            "z": float(b.get("z", 0.0)),
            "yaw": float(b.get("yaw", 0.0)),
        }
        if last["z"] >= float(min_z):
            good += 1
            if good >= int(stable_samples):
                return last
        else:
            good = 0
        await asyncio.sleep(0.12)
    return last


async def main() -> None:
    url = "http://127.0.0.1:6769/mcp/"
    async with streamablehttp_client(url, timeout=25, sse_read_timeout=25) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            await _call(session, "set_humanoid_mode", {"mode": "external"})
            pre = await _call(session, "run_humanoid_preflight", {})
            print("preflight:", pre, flush=True)

            # Reset to a known standing pose before measuring translation.
            await _call(session, "disable_humanoid_motion", {"reason": "forward_response_test_pre_reset"})
            await _call(session, "reset_humanoid_episode", {})
            await _wait_for_pose(session, min_z=0.70, stable_samples=4, timeout_s=12.0)
            await _call(session, "enable_humanoid_motion", {"reason": "forward_response_test"})

            st0 = await _call(session, "get_robot_state_for", {"robot_kind": "humanoid"})
            b0 = st0.get("base") or {}
            x0 = float(b0.get("x", 0.0))
            y0 = float(b0.get("y", 0.0))
            z0 = float(b0.get("z", 0.0))
            print(f"pose0: x={x0:+.3f} y={y0:+.3f} z={z0:+.3f}", flush=True)

            await _call(session, "send_humanoid_heartbeat", {"source": "forward_response_test"})
            await _call(
                session,
                "drive_humanoid",
                {"vx": 0.18, "vy": 0.0, "wz": 0.0, "duration_s": 2.0, "reason": "forward_2s"},
            )

            st1 = await _call(session, "get_robot_state_for", {"robot_kind": "humanoid"})
            b1 = st1.get("base") or {}
            x1 = float(b1.get("x", 0.0))
            y1 = float(b1.get("y", 0.0))
            z1 = float(b1.get("z", 0.0))
            dx = x1 - x0
            dy = y1 - y0
            dist = (dx * dx + dy * dy) ** 0.5
            print(f"pose1: x={x1:+.3f} y={y1:+.3f} z={z1:+.3f}", flush=True)
            print(f"delta: dx={dx:+.3f} dy={dy:+.3f} dist={dist:.3f}", flush=True)

            await _call(session, "disable_humanoid_motion", {"reason": "forward_response_test"})


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=30.0))

