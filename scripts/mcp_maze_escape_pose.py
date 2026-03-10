import argparse
import asyncio
import json
import math
import time

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def _wrap_pi(a: float) -> float:
    while a > math.pi:
        a -= 2.0 * math.pi
    while a < -math.pi:
        a += 2.0 * math.pi
    return a


def _dist_xy(x: float, y: float, gx: float, gy: float) -> float:
    return float(((x - gx) ** 2 + (y - gy) ** 2) ** 0.5)


async def main() -> None:
    ap = argparse.ArgumentParser(description="Pose-only maze escape: steer toward goal; stall -> recovery turn.")
    ap.add_argument("--url", default="http://127.0.0.1:6769/mcp/")
    ap.add_argument("--runtime_s", type=float, default=90.0)
    ap.add_argument("--step_s", type=float, default=0.25)
    ap.add_argument("--vx", type=float, default=0.35)
    ap.add_argument("--wz_max", type=float, default=1.0)
    ap.add_argument("--k_heading", type=float, default=1.8, help="Heading P-gain (rad/s per rad error).")
    ap.add_argument("--goal_x", type=float, default=3.5)
    ap.add_argument("--goal_y", type=float, default=3.5)
    ap.add_argument("--goal_radius", type=float, default=0.6)
    ap.add_argument("--stall_dist_m", type=float, default=0.015, help="If moved less than this over a step window, treat as stall.")
    ap.add_argument("--stall_window_s", type=float, default=1.5)
    ap.add_argument("--recovery_turn_s", type=float, default=1.0)
    ap.add_argument("--recovery_wz", type=float, default=-1.0, help="Negative is left in current setup (verified).")
    ap.add_argument("--print_every", type=int, default=4)
    ap.add_argument("--reset", action="store_true", help="Reset episode before starting.")
    args = ap.parse_args()

    async with streamablehttp_client(args.url, timeout=25, sse_read_timeout=25) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            if args.reset:
                r = await session.call_tool("reset_humanoid_episode", {})
                if r.content:
                    print(r.content[0].text)

            # External driving.
            await session.call_tool("set_humanoid_mode", {"mode": "external"})

            t0 = time.time()
            last_progress_t = t0
            last_x = None
            last_y = None

            i = 0
            while (time.time() - t0) < float(args.runtime_s):
                res = await session.call_tool("get_robot_state_for", {"robot_kind": "humanoid"})
                txt = res.content[0].text if res.content else "{}"
                try:
                    st = json.loads(txt) or {}
                except Exception:
                    st = {}
                base = st.get("base") or {}
                try:
                    x = float(base.get("x", 0.0))
                    y = float(base.get("y", 0.0))
                    yaw = float(base.get("yaw", 0.0))
                except Exception:
                    x, y, yaw = 0.0, 0.0, 0.0

                d_goal = _dist_xy(x, y, float(args.goal_x), float(args.goal_y))
                if i % max(1, int(args.print_every)) == 0:
                    print(f"t={time.time()-t0:5.1f}s x={x:+.3f} y={y:+.3f} yaw={yaw:+.3f} d_goal={d_goal:.3f}")

                if d_goal <= float(args.goal_radius):
                    print(f"Reached goal radius {args.goal_radius:.3f} (d={d_goal:.3f}).")
                    return

                # Progress / stall detection (distance moved).
                now = time.time()
                if last_x is not None and last_y is not None:
                    moved = _dist_xy(x, y, last_x, last_y)
                    if moved >= float(args.stall_dist_m):
                        last_progress_t = now

                last_x, last_y = x, y

                if (now - last_progress_t) >= float(args.stall_window_s):
                    # Recovery: turn in place to find a new corridor.
                    await session.call_tool(
                        "drive_humanoid",
                        {
                            "vx": 0.0,
                            "vy": 0.0,
                            "wz": float(args.recovery_wz),
                            "duration_s": float(args.recovery_turn_s),
                            "reason": "stall recovery turn",
                        },
                    )
                    last_progress_t = time.time()
                    await asyncio.sleep(0.05)
                    i += 1
                    continue

                # Heading to goal (world frame).
                goal_heading = math.atan2(float(args.goal_y) - y, float(args.goal_x) - x)
                err = _wrap_pi(goal_heading - yaw)

                wz = float(args.k_heading) * float(err)
                wz = max(-float(args.wz_max), min(float(args.wz_max), wz))

                # If we're very misaligned, slow forward speed a bit.
                vx = float(args.vx) * (0.35 if abs(err) > 1.0 else 1.0)

                await session.call_tool(
                    "drive_humanoid",
                    {
                        "vx": float(vx),
                        "vy": 0.0,
                        "wz": float(wz),
                        "duration_s": float(args.step_s),
                        "reason": "pose-nav step",
                    },
                )
                i += 1

            print("Timed out before reaching goal.")


if __name__ == "__main__":
    asyncio.run(main())





