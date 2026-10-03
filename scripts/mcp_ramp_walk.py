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
    ap = argparse.ArgumentParser(description="Ramp walk test: increase vx/wz until tipping (base.z < min_z) or motion rejected.")
    ap.add_argument("--url", default="http://127.0.0.1:6769/mcp/")
    ap.add_argument("--reset", action="store_true", help="Reset before ramping.")
    ap.add_argument("--min_z", type=float, default=0.70)

    ap.add_argument("--vx0", type=float, default=0.06)
    ap.add_argument("--vx_step", type=float, default=0.02)
    ap.add_argument("--vx_max", type=float, default=0.22)
    ap.add_argument("--forward_s", type=float, default=2.2)

    ap.add_argument("--wz0", type=float, default=0.18)
    ap.add_argument("--wz_step", type=float, default=0.03)
    ap.add_argument("--wz_max", type=float, default=0.45)
    ap.add_argument("--turn_s", type=float, default=0.9)

    ap.add_argument("--levels", type=int, default=10, help="Maximum ramp levels.")
    ap.add_argument("--segments_per_level", type=int, default=4, help="Forward+turn pairs per level.")
    args = ap.parse_args()

    report: dict[str, Any] = {
        "ok": False,
        "preflight": {},
        "min_z_threshold": float(args.min_z),
        "levels": [],
        "max_stable": None,
        "failure": None,
    }

    async with streamablehttp_client(args.url, timeout=25, sse_read_timeout=25) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await _call(session, "set_humanoid_mode", {"mode": "external"})
            pre = await _call(session, "run_humanoid_preflight", {})
            report["preflight"] = pre
            if not bool(pre.get("ok")):
                report["failure"] = {"type": "preflight_failed", "details": pre}
                print(json.dumps(report, indent=2))
                return

            await _call(session, "disable_humanoid_motion", {"reason": "ramp_walk"})
            if bool(args.reset):
                await _call(session, "reset_humanoid_episode", {})
                await asyncio.sleep(1.2)
            await _call(session, "enable_humanoid_motion", {"reason": "ramp_walk"})

            z_min_seen = 9e9
            stable: dict[str, Any] | None = None

            for level in range(int(args.levels)):
                vx = min(float(args.vx_max), float(args.vx0) + float(level) * float(args.vx_step))
                wz = min(float(args.wz_max), float(args.wz0) + float(level) * float(args.wz_step))

                level_row: dict[str, Any] = {
                    "level": level,
                    "vx": vx,
                    "wz": wz,
                    "segments": [],
                    "ok": False,
                }

                for seg in range(int(args.segments_per_level)):
                    await _call(session, "send_humanoid_heartbeat", {"source": "ramp_walk"})

                    p0 = await _base(session)
                    z_min_seen = min(z_min_seen, float(p0["z"]))
                    if 0.0 < float(p0["z"]) < float(args.min_z):
                        await _call(session, "stop_humanoid_now", {"reason": "ramp_walk_low_z_pre"})
                        await _call(session, "disable_humanoid_motion", {"reason": "ramp_walk_low_z_pre"})
                        report["failure"] = {"type": "low_z_pre", "level": level, "segment": seg, "pose": p0}
                        report["min_z_seen"] = z_min_seen
                        report["max_stable"] = stable
                        print(json.dumps(report, indent=2))
                        return

                    # forward
                    res_fwd = await _call(
                        session,
                        "drive_humanoid",
                        {"vx": vx, "vy": 0.0, "wz": 0.0, "duration_s": float(args.forward_s), "reason": f"ramp_fwd_L{level}_S{seg}"},
                    )
                    p1 = await _base(session)
                    z_min_seen = min(z_min_seen, float(p1["z"]))
                    d = _dist(p0, p1)
                    level_row["segments"].append({"segment": f"forward_{seg}", "drive": res_fwd, "dist_m": d, "pose": p1})
                    if str(res_fwd.get("status")) == "error":
                        report["failure"] = {"type": "motion_rejected_forward", "level": level, "segment": seg, "drive": res_fwd, "pose": p1}
                        report["min_z_seen"] = z_min_seen
                        report["max_stable"] = stable
                        print(json.dumps(report, indent=2))
                        return
                    if 0.0 < float(p1["z"]) < float(args.min_z):
                        await _call(session, "stop_humanoid_now", {"reason": "ramp_walk_low_z_forward"})
                        await _call(session, "disable_humanoid_motion", {"reason": "ramp_walk_low_z_forward"})
                        report["failure"] = {"type": "low_z_forward", "level": level, "segment": seg, "pose": p1, "drive": res_fwd}
                        report["min_z_seen"] = z_min_seen
                        report["max_stable"] = stable
                        print(json.dumps(report, indent=2))
                        return

                    await asyncio.sleep(0.35)

                    # turn
                    res_turn = await _call(
                        session,
                        "drive_humanoid",
                        {"vx": 0.0, "vy": 0.0, "wz": wz, "duration_s": float(args.turn_s), "reason": f"ramp_turn_L{level}_S{seg}"},
                    )
                    p2 = await _base(session)
                    z_min_seen = min(z_min_seen, float(p2["z"]))
                    level_row["segments"].append({"segment": f"turn_{seg}", "drive": res_turn, "pose": p2})
                    if str(res_turn.get("status")) == "error":
                        report["failure"] = {"type": "motion_rejected_turn", "level": level, "segment": seg, "drive": res_turn, "pose": p2}
                        report["min_z_seen"] = z_min_seen
                        report["max_stable"] = stable
                        print(json.dumps(report, indent=2))
                        return
                    if 0.0 < float(p2["z"]) < float(args.min_z):
                        await _call(session, "stop_humanoid_now", {"reason": "ramp_walk_low_z_turn"})
                        await _call(session, "disable_humanoid_motion", {"reason": "ramp_walk_low_z_turn"})
                        report["failure"] = {"type": "low_z_turn", "level": level, "segment": seg, "pose": p2, "drive": res_turn}
                        report["min_z_seen"] = z_min_seen
                        report["max_stable"] = stable
                        print(json.dumps(report, indent=2))
                        return

                    await asyncio.sleep(0.45)

                # level passed
                level_row["ok"] = True
                report["levels"].append(level_row)
                stable = {"level": level, "vx": vx, "wz": wz, "min_z_seen": z_min_seen}

                # tiny pause between levels
                await asyncio.sleep(0.6)

            report["ok"] = True
            report["max_stable"] = stable
            report["min_z_seen"] = z_min_seen
            await _call(session, "disable_humanoid_motion", {"reason": "ramp_walk_done"})

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=900.0))

