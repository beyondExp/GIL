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


def _dist(a: dict[str, float], b: dict[str, float]) -> float:
    dx = float(b["x"] - a["x"])
    dy = float(b["y"] - a["y"])
    return float(math.sqrt(dx * dx + dy * dy))


async def main() -> None:
    ap = argparse.ArgumentParser(description="Safe square walk: forward segments + gentle turn pulses, fail-fast on low base.z.")
    ap.add_argument("--url", default="http://127.0.0.1:6769/mcp/")
    ap.add_argument("--vx", type=float, default=0.08)
    ap.add_argument("--forward_s", type=float, default=2.4)
    ap.add_argument("--wz", type=float, default=0.25)
    ap.add_argument("--turn_s", type=float, default=1.0)
    ap.add_argument("--corners", type=int, default=4)
    ap.add_argument("--min_z", type=float, default=0.70)
    ap.add_argument("--reset", action="store_true", help="If set, reset episode before walking.")
    args = ap.parse_args()

    out: dict[str, Any] = {"ok": False, "events": []}

    async with streamablehttp_client(args.url, timeout=25, sse_read_timeout=25) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await _call(session, "set_humanoid_mode", {"mode": "external"})
            pre = await _call(session, "run_humanoid_preflight", {})
            out["preflight"] = pre
            if not bool(pre.get("ok")):
                out["error"] = "preflight_failed"
                print(json.dumps(out, indent=2))
                return

            await _call(session, "disable_humanoid_motion", {"reason": "walk_square_safe"})
            if bool(args.reset):
                await _call(session, "reset_humanoid_episode", {})
                await asyncio.sleep(1.2)
            await _call(session, "enable_humanoid_motion", {"reason": "walk_square_safe"})

            p0 = await _base(session)
            min_z_seen = float(p0["z"])
            out["pose0"] = p0

            total_dist = 0.0
            for i in range(int(args.corners)):
                await _call(session, "send_humanoid_heartbeat", {"source": "walk_square_safe"})

                # Forward segment
                seg0 = await _base(session)
                res_fwd = await _call(
                    session,
                    "drive_humanoid",
                    {"vx": float(args.vx), "vy": 0.0, "wz": 0.0, "duration_s": float(args.forward_s), "reason": f"walk_fwd_{i}"},
                )
                seg1 = await _base(session)
                min_z_seen = min(min_z_seen, float(seg1["z"]))
                d = _dist(seg0, seg1)
                total_dist += d
                out["events"].append({"segment": f"forward_{i}", "drive": res_fwd, "dist_m": d, "pose": seg1})
                if 0.0 < float(seg1["z"]) < float(args.min_z):
                    await _call(session, "stop_humanoid_now", {"reason": "walk_square_safe_low_z_forward"})
                    await _call(session, "disable_humanoid_motion", {"reason": "walk_square_safe_low_z_forward"})
                    out["error"] = "low_z_forward"
                    out["min_z_seen"] = min_z_seen
                    out["total_dist_m"] = total_dist
                    out["pose1"] = seg1
                    print(json.dumps(out, indent=2))
                    return

                await asyncio.sleep(0.4)

                # Gentle turn pulse
                seg2 = await _base(session)
                res_turn = await _call(
                    session,
                    "drive_humanoid",
                    {"vx": 0.0, "vy": 0.0, "wz": float(args.wz), "duration_s": float(args.turn_s), "reason": f"turn_{i}"},
                )
                seg3 = await _base(session)
                min_z_seen = min(min_z_seen, float(seg3["z"]))
                out["events"].append({"segment": f"turn_{i}", "drive": res_turn, "pose": seg3})
                if 0.0 < float(seg3["z"]) < float(args.min_z):
                    await _call(session, "stop_humanoid_now", {"reason": "walk_square_safe_low_z_turn"})
                    await _call(session, "disable_humanoid_motion", {"reason": "walk_square_safe_low_z_turn"})
                    out["error"] = "low_z_turn"
                    out["min_z_seen"] = min_z_seen
                    out["total_dist_m"] = total_dist
                    out["pose1"] = seg3
                    print(json.dumps(out, indent=2))
                    return

                await asyncio.sleep(0.5)

            p1 = await _base(session)
            out["pose1"] = p1
            out["min_z_seen"] = min_z_seen
            out["total_dist_m"] = total_dist
            out["ok"] = True
            await _call(session, "disable_humanoid_motion", {"reason": "walk_square_safe_done"})

    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=240.0))

