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
    target_tol: float = 0.60,
    stable_samples: int = 4,
    timeout_s: float = 12.0,
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
    ap = argparse.ArgumentParser(description="Open-space unicycle goal demo (external mode, turn-in-place-first).")
    ap.add_argument("--url", default="http://127.0.0.1:6769/mcp/")
    ap.add_argument("--goal_x", type=float, default=3.0)
    ap.add_argument("--goal_y", type=float, default=0.0)
    ap.add_argument("--goal_radius", type=float, default=0.85)
    ap.add_argument("--runtime_s", type=float, default=90.0)
    ap.add_argument("--dt", type=float, default=0.18)
    ap.add_argument("--vx", type=float, default=0.10)
    ap.add_argument("--wz_cap", type=float, default=0.30)
    ap.add_argument("--k_heading", type=float, default=0.8)
    ap.add_argument("--turn_in_place_err", type=float, default=0.28)
    ap.add_argument("--fall_z", type=float, default=0.45)
    ap.add_argument("--fall_persist_s", type=float, default=0.6, help="How long low/zero z must persist to count as a fall.")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    gx, gy = float(args.goal_x), float(args.goal_y)

    async with streamablehttp_client(args.url, timeout=25, sse_read_timeout=25) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            await _call(session, "set_humanoid_mode", {"mode": "external"})
            pre = await _call(session, "run_humanoid_preflight", {})
            print("preflight:", pre, flush=True)

            # Hard stop before we start resetting/teleporting.
            await _call(session, "stop_humanoid_now", {"reason": "open_space_unicycle_goal_pre_reset"})
            await _call(session, "disable_humanoid_motion", {"reason": "open_space_unicycle_goal"})
            if args.reset:
                # Open-space: reset to a neutral, obstacle-free location.
                # Right after Isaac startup the physics callback that applies reset can come up late,
                # so we retry for a bit before giving up.
                ok = False
                for attempt in range(4):
                    rr = await _call(session, "reset_humanoid_episode", {"x": 0.0, "y": 0.0, "yaw": 0.0})
                    if attempt == 0:
                        print(f"reset: {rr}", flush=True)
                    p_reset = await _wait_for_pose(
                        session,
                        min_z=0.70,
                        target_xy=(0.0, 0.0),
                        target_tol=0.75,
                        stable_samples=4,
                        timeout_s=25.0,
                    )
                    if abs(p_reset["x"]) <= 0.75 and abs(p_reset["y"]) <= 0.75 and p_reset["z"] >= 0.70:
                        ok = True
                        break
                    await asyncio.sleep(0.4)
            else:
                ok = True
                p_reset = await _wait_for_pose(session, min_z=0.70, stable_samples=2, timeout_s=12.0)
            if abs(p_reset["x"]) > 0.75 or abs(p_reset["y"]) > 0.75 or p_reset["z"] < 0.70:
                print(f"ERROR: reset did not converge. pose=({p_reset['x']:+.2f},{p_reset['y']:+.2f},{p_reset['z']:+.2f})", flush=True)
                return

            en = await _call(session, "enable_humanoid_motion", {"reason": "open_space_unicycle_goal"})
            print("enable:", en, flush=True)
            # Let the policy settle a moment before we start commanding turns.
            await asyncio.sleep(0.6)

            t0 = time.time()
            last_print = 0.0
            low_z_since: float | None = None
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

                # Fall safety (treat z==0 as invalid; require persistence to avoid transient samples).
                fell = (0.0 < z < float(args.fall_z)) or (z == 0.0)
                if fell:
                    if low_z_since is None:
                        low_z_since = time.time()
                    if (time.time() - low_z_since) >= float(args.fall_persist_s):
                        await _call(session, "stop_humanoid_now", {"reason": "fall_detected"})
                        await _call(session, "disable_humanoid_motion", {"reason": "fall_detected"})
                        if args.reset:
                            # Retry reset a couple times; after hard falls, DC handles can be briefly flaky.
                            ok = False
                            for attempt in range(3):
                                rr = await _call(session, "reset_humanoid_episode", {"x": 0.0, "y": 0.0, "yaw": 0.0})
                                if attempt == 0:
                                    print(f"reset_after_fall: {rr}", flush=True)
                                p = await _wait_for_pose(
                                    session,
                                    min_z=0.70,
                                    target_xy=(0.0, 0.0),
                                    target_tol=0.90,
                                    stable_samples=4,
                                    timeout_s=14.0,
                                )
                                if abs(p["x"]) <= 0.90 and abs(p["y"]) <= 0.90 and p["z"] >= 0.70:
                                    ok = True
                                    break
                                await asyncio.sleep(0.25)
                            await _call(session, "enable_humanoid_motion", {"reason": "post_fall_reset"})
                            if not ok:
                                print("WARN: reset did not converge to (0,0) within timeout; continuing anyway.", flush=True)
                        low_z_since = None
                        continue
                    await asyncio.sleep(float(args.dt))
                    continue
                low_z_since = None

                desired = float(math.atan2(dy, dx))
                err = _wrap_pi(desired - yaw)
                wz = float(max(-float(args.wz_cap), min(float(args.wz_cap), float(args.k_heading) * err)))
                vx = 0.0 if abs(err) > float(args.turn_in_place_err) else float(args.vx)

                await _call(session, "send_humanoid_heartbeat", {"source": "open_space_unicycle_goal_demo"})
                await _call(
                    session,
                    "drive_humanoid",
                    {"vx": vx, "vy": 0.0, "wz": wz, "duration_s": float(args.dt), "reason": "unicycle_step"},
                )

            await _call(session, "stop_humanoid_now", {"reason": "timeout"})
            print("Timed out before reaching goal.", flush=True)


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=180.0))

