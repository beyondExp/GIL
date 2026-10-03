from __future__ import annotations

import argparse
import asyncio
import json
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


async def _wait_for_pose(session: ClientSession, *, min_z: float, stable_samples: int = 4, timeout_s: float = 12.0) -> dict[str, float]:
    t0 = time.time()
    last = {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0}
    good = 0
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
            good += 1
            if good >= int(stable_samples):
                return last
        else:
            good = 0
        await asyncio.sleep(0.12)
    return last


async def main() -> None:
    ap = argparse.ArgumentParser(description="Open-space straight-walk demo (external mode, no turning).")
    ap.add_argument("--url", default="http://127.0.0.1:6769/mcp/")
    ap.add_argument("--vx", type=float, default=0.14)
    ap.add_argument("--duration_s", type=float, default=6.0)
    ap.add_argument("--min_z", type=float, default=0.55)
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    async with streamablehttp_client(args.url, timeout=25, sse_read_timeout=25) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            await _call(session, "set_humanoid_mode", {"mode": "external"})
            pre = await _call(session, "run_humanoid_preflight", {})
            print("preflight:", pre, flush=True)

            await _call(session, "disable_humanoid_motion", {"reason": "open_space_straight_walk"})
            if args.reset:
                await _call(session, "reset_humanoid_episode", {})
            p0 = await _wait_for_pose(session, min_z=float(args.min_z), stable_samples=4, timeout_s=12.0)

            en = await _call(session, "enable_humanoid_motion", {"reason": "open_space_straight_walk"})
            print("enable:", en, flush=True)

            print(f"pose0: x={p0['x']:+.3f} y={p0['y']:+.3f} z={p0['z']:+.3f} yaw={p0['yaw']:+.3f}", flush=True)

            await _call(session, "send_humanoid_heartbeat", {"source": "open_space_straight_walk"})
            await _call(
                session,
                "drive_humanoid",
                {"vx": float(args.vx), "vy": 0.0, "wz": 0.0, "duration_s": float(args.duration_s), "reason": "walk_straight"},
            )

            st1 = await _call(session, "get_robot_state_for", {"robot_kind": "humanoid"})
            b1 = st1.get("base") or {}
            p1 = {
                "x": float(b1.get("x", 0.0)),
                "y": float(b1.get("y", 0.0)),
                "z": float(b1.get("z", 0.0)),
                "yaw": float(b1.get("yaw", 0.0)),
            }
            dx = p1["x"] - p0["x"]
            dy = p1["y"] - p0["y"]
            dist = float((dx * dx + dy * dy) ** 0.5)

            print(f"pose1: x={p1['x']:+.3f} y={p1['y']:+.3f} z={p1['z']:+.3f} yaw={p1['yaw']:+.3f}", flush=True)
            print(f"delta: dx={dx:+.3f} dy={dy:+.3f} dist={dist:.3f}", flush=True)
            print(f"upright: {bool(p1['z'] >= float(args.min_z))}", flush=True)

            await _call(session, "disable_humanoid_motion", {"reason": "open_space_straight_walk_done"})


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=60.0))

