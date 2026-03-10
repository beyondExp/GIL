import argparse
import asyncio
import json
import math
import time

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def _dist(a: dict, gx: float, gy: float) -> float:
    try:
        x = float(a.get("x", 0.0))
        y = float(a.get("y", 0.0))
    except Exception:
        return float("inf")
    return float(((x - gx) ** 2 + (y - gy) ** 2) ** 0.5)


async def main() -> None:
    ap = argparse.ArgumentParser(description="Reset maze episode and attempt to navigate to goal using goal mode.")
    ap.add_argument("--url", default="http://127.0.0.1:6769/mcp/", help="MCP streamable-http URL")
    ap.add_argument("--goal_x", type=float, default=3.5)
    ap.add_argument("--goal_y", type=float, default=3.5)
    ap.add_argument("--goal_radius", type=float, default=0.5)
    ap.add_argument("--runtime_s", type=float, default=60.0)
    ap.add_argument("--poll_s", type=float, default=0.5)
    ap.add_argument("--print_every", type=int, default=4)
    args = ap.parse_args()

    async with streamablehttp_client(args.url, timeout=25, sse_read_timeout=25) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Reset episode (maze + robot).
            r = await session.call_tool("reset_humanoid_episode", {})
            if r.content:
                print(r.content[0].text)

            # Set goal and switch to goal-following mode.
            r = await session.call_tool("set_humanoid_goal", {"x": float(args.goal_x), "y": float(args.goal_y)})
            if r.content:
                print(r.content[0].text)
            r = await session.call_tool("set_humanoid_mode", {"mode": "goal"})
            if r.content:
                print(r.content[0].text)

            t0 = time.time()
            i = 0
            while (time.time() - t0) < float(args.runtime_s):
                res = await session.call_tool("get_robot_state_for", {"robot_kind": "humanoid"})
                txt = res.content[0].text if res.content else "{}"
                try:
                    st = json.loads(txt) or {}
                except Exception:
                    st = {}
                base = st.get("base") or {}
                d = _dist(base, float(args.goal_x), float(args.goal_y))
                if i % max(1, int(args.print_every)) == 0:
                    try:
                        print(
                            f"t={time.time()-t0:5.1f}s "
                            f"x={float(base.get('x',0.0)):+.3f} y={float(base.get('y',0.0)):+.3f} yaw={float(base.get('yaw',0.0)):+.3f} "
                            f"d_goal={d:.3f}"
                        )
                    except Exception:
                        print(f"t={time.time()-t0:5.1f}s d_goal={d:.3f}")

                if d <= float(args.goal_radius):
                    print(f"Reached goal radius {args.goal_radius:.3f} (d={d:.3f}).")
                    return

                i += 1
                await asyncio.sleep(float(args.poll_s))

            print("Timed out before reaching goal.")


if __name__ == "__main__":
    asyncio.run(main())





