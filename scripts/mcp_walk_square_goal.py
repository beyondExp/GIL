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


async def _base(session: ClientSession) -> dict[str, float]:
    st = await _call(session, "get_robot_state_for", {"robot_kind": "humanoid"})
    b = st.get("base") or {}
    return {
        "x": float(b.get("x", 0.0) or 0.0),
        "y": float(b.get("y", 0.0) or 0.0),
        "z": float(b.get("z", 0.0) or 0.0),
        "yaw": float(b.get("yaw", 0.0) or 0.0),
    }


def _rot(yaw: float, dx: float, dy: float) -> tuple[float, float]:
    cy = math.cos(float(yaw))
    sy = math.sin(float(yaw))
    return (cy * dx - sy * dy, sy * dx + cy * dy)


async def main() -> None:
    ap = argparse.ArgumentParser(description="Walk a square in goal mode (absolute world goals).")
    ap.add_argument("--url", default="http://127.0.0.1:6769/mcp/")
    ap.add_argument("--side_m", type=float, default=1.0)
    ap.add_argument("--goal_tol_m", type=float, default=0.55)
    ap.add_argument("--min_z", type=float, default=0.70)
    ap.add_argument("--timeout_s", type=float, default=35.0)
    ap.add_argument("--laps", type=int, default=1)
    ap.add_argument("--reset", action="store_true", help="Reset episode and wait upright before starting.")
    args = ap.parse_args()

    out: dict[str, Any] = {"ok": False, "corners": [], "events": []}

    async with streamablehttp_client(args.url, timeout=25, sse_read_timeout=25) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await _call(session, "set_humanoid_mode", {"mode": "goal"})
            pre = await _call(session, "run_humanoid_preflight", {})
            out["preflight"] = pre
            if not bool(pre.get("ok")):
                out["error"] = "preflight_failed"
                print(json.dumps(out, indent=2))
                return

            await _call(session, "disable_humanoid_motion", {"reason": "walk_square_goal_setup"})
            if bool(args.reset):
                await _call(session, "stop_humanoid_now", {"reason": "walk_square_goal_reset"})
                await _call(session, "reset_humanoid_episode", {})
                # Reset forces the Isaac extension into external mode; re-assert goal mode.
                await _call(session, "set_humanoid_mode", {"mode": "goal"})
                # Wait until upright enough to start.
                t0 = time.time()
                while (time.time() - t0) < 20.0:
                    await _call(session, "send_humanoid_heartbeat", {"source": "walk_square_goal_reset_wait"})
                    b = await _base(session)
                    if float(b["z"]) >= float(args.min_z):
                        break
                    await asyncio.sleep(0.25)

            await _call(session, "enable_humanoid_motion", {"reason": "walk_square_goal"})

            p0 = await _base(session)
            out["pose0"] = p0
            if 0.0 < float(p0["z"]) < float(args.min_z):
                out["error"] = "not_upright_at_start"
                print(json.dumps(out, indent=2))
                return
            yaw0 = float(p0["yaw"])
            side = float(args.side_m)

            # Define square corners in robot frame relative to current pose (no teleport required):
            # forward, then left, then back, then right.
            offsets = [
                (side, 0.0),
                (side, side),
                (0.0, side),
                (0.0, 0.0),
            ]
            corners = []
            for dx, dy in offsets:
                wx, wy = _rot(yaw0, dx, dy)
                corners.append({"x": float(p0["x"] + wx), "y": float(p0["y"] + wy)})
            out["corners"] = corners

            for lap in range(int(args.laps)):
                for i, c in enumerate(corners):
                    gx = float(c["x"])
                    gy = float(c["y"])
                    await _call(session, "send_humanoid_heartbeat", {"source": "walk_square_goal"})
                    res = await _call(session, "set_humanoid_goal", {"x": gx, "y": gy})
                    out["events"].append({"lap": lap, "corner": i, "set_goal": res, "goal": {"x": gx, "y": gy}})

                    t0 = time.time()
                    while (time.time() - t0) < float(args.timeout_s):
                        await _call(session, "send_humanoid_heartbeat", {"source": "walk_square_goal"})
                        b = await _base(session)
                        if 0.0 < float(b["z"]) < float(args.min_z):
                            await _call(session, "stop_humanoid_now", {"reason": "walk_square_goal_low_z"})
                            await _call(session, "disable_humanoid_motion", {"reason": "walk_square_goal_low_z"})
                            out["error"] = "low_z"
                            out["pose_fail"] = b
                            print(json.dumps(out, indent=2))
                            return
                        dx2 = float(b["x"] - gx)
                        dy2 = float(b["y"] - gy)
                        dist = float(math.sqrt(dx2 * dx2 + dy2 * dy2))
                        if dist <= float(args.goal_tol_m):
                            out["events"].append({"lap": lap, "corner": i, "reached": True, "dist_m": dist, "pose": b})
                            break
                        await asyncio.sleep(0.25)
                    else:
                        out["error"] = "timeout"
                        out["pose_fail"] = await _base(session)
                        print(json.dumps(out, indent=2))
                        return

            out["ok"] = True
            out["pose1"] = await _base(session)
            await _call(session, "disable_humanoid_motion", {"reason": "walk_square_goal_done"})

    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=600.0))

