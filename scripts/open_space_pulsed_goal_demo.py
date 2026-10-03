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


async def _wait_for_pose(
    session: ClientSession,
    *,
    min_z: float,
    target_xy: tuple[float, float] | None = None,
    target_tol: float = 0.9,
    stable_samples: int = 4,
    timeout_s: float = 25.0,
) -> dict[str, float]:
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
        ok = last["z"] >= float(min_z)
        if ok and target_xy is not None:
            ok = abs(last["x"] - float(target_xy[0])) <= float(target_tol) and abs(last["y"] - float(target_xy[1])) <= float(target_tol)
        if ok:
            good += 1
            if good >= int(stable_samples):
                return last
        else:
            good = 0
        await asyncio.sleep(0.12)
    return last


async def main() -> None:
    ap = argparse.ArgumentParser(description="Open-space pulsed goal demo (turn pulses + settle).")
    ap.add_argument("--url", default="http://127.0.0.1:6769/mcp/")
    ap.add_argument("--spawn_x", type=float, default=-3.55)
    ap.add_argument("--spawn_y", type=float, default=-3.55)
    ap.add_argument("--spawn_yaw", type=float, default=0.0)
    ap.add_argument("--goal_dx", type=float, default=1.0, help="Goal offset in +X from spawn.")
    ap.add_argument("--goal_dy", type=float, default=0.0, help="Goal offset in +Y from spawn.")
    ap.add_argument("--goal_radius", type=float, default=0.75)
    ap.add_argument("--runtime_s", type=float, default=80.0)
    ap.add_argument("--reset", action="store_true")

    ap.add_argument("--vx", type=float, default=0.10)
    ap.add_argument("--wz_pulse", type=float, default=0.30)
    ap.add_argument("--turn_err", type=float, default=0.55, help="Turn in place if |yaw_err| > this.")
    ap.add_argument("--pulse_s", type=float, default=0.18)
    ap.add_argument("--settle_s", type=float, default=0.55)
    ap.add_argument("--fall_z", type=float, default=0.45)
    ap.add_argument("--fall_persist_s", type=float, default=0.7)
    args = ap.parse_args()

    sx, sy, syaw = float(args.spawn_x), float(args.spawn_y), float(args.spawn_yaw)
    gx, gy = float(sx + float(args.goal_dx)), float(sy + float(args.goal_dy))

    async with streamablehttp_client(args.url, timeout=25, sse_read_timeout=25) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await _call(session, "set_humanoid_mode", {"mode": "external"})
            pre = await _call(session, "run_humanoid_preflight", {})
            print("preflight:", pre, flush=True)

            await _call(session, "stop_humanoid_now", {"reason": "pulsed_goal_pre_reset"})
            await _call(session, "disable_humanoid_motion", {"reason": "pulsed_goal_pre_reset"})
            if args.reset:
                await _call(session, "reset_humanoid_episode", {"x": sx, "y": sy, "yaw": syaw})
            p0 = await _wait_for_pose(session, min_z=0.70, target_xy=(sx, sy), target_tol=0.9, stable_samples=4, timeout_s=30.0)
            if abs(p0["x"] - sx) > 0.9 or abs(p0["y"] - sy) > 0.9 or p0["z"] < 0.70:
                print(f"ERROR: reset not stable. pose=({p0['x']:+.2f},{p0['y']:+.2f},{p0['z']:+.2f})", flush=True)
                return

            await _call(session, "enable_humanoid_motion", {"reason": "pulsed_goal"})
            await asyncio.sleep(0.6)

            t0 = time.time()
            last_print = 0.0
            low_z_since: float | None = None
            while (time.time() - t0) < float(args.runtime_s):
                await _call(session, "send_humanoid_heartbeat", {"source": "open_space_pulsed_goal_demo"})
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

                fell = (z == 0.0) or (0.0 < z < float(args.fall_z))
                if fell:
                    if low_z_since is None:
                        low_z_since = time.time()
                    if (time.time() - low_z_since) >= float(args.fall_persist_s):
                        print("Fall detected -> resetting.", flush=True)
                        await _call(session, "stop_humanoid_now", {"reason": "fall_detected"})
                        await _call(session, "disable_humanoid_motion", {"reason": "fall_detected"})
                        if args.reset:
                            await _call(session, "reset_humanoid_episode", {"x": sx, "y": sy, "yaw": syaw})
                            await _wait_for_pose(session, min_z=0.70, target_xy=(sx, sy), target_tol=0.9, stable_samples=4, timeout_s=30.0)
                            await _call(session, "enable_humanoid_motion", {"reason": "post_fall_reset"})
                            await asyncio.sleep(0.6)
                        low_z_since = None
                    await asyncio.sleep(0.2)
                    continue
                low_z_since = None

                desired = float(math.atan2(dy, dx))
                err = _wrap_pi(desired - yaw)

                # Turn pulse, then settle (no forward motion while turning).
                if abs(err) > float(args.turn_err):
                    wz = float(math.copysign(float(args.wz_pulse), err))
                    await _call(session, "drive_humanoid", {"vx": 0.0, "vy": 0.0, "wz": wz, "duration_s": float(args.pulse_s), "reason": "turn_pulse"})
                    await _call(session, "drive_humanoid", {"vx": 0.0, "vy": 0.0, "wz": 0.0, "duration_s": float(args.settle_s), "reason": "settle"})
                else:
                    # Small forward step, then settle.
                    await _call(session, "drive_humanoid", {"vx": float(args.vx), "vy": 0.0, "wz": 0.0, "duration_s": float(args.pulse_s), "reason": "forward_pulse"})
                    await _call(session, "drive_humanoid", {"vx": 0.0, "vy": 0.0, "wz": 0.0, "duration_s": float(args.settle_s), "reason": "settle"})

            await _call(session, "stop_humanoid_now", {"reason": "timeout"})
            print("Timed out before reaching goal.", flush=True)


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=200.0))

