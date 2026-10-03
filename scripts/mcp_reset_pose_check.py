from __future__ import annotations

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


async def main() -> None:
    url = "http://127.0.0.1:6769/mcp/"
    async with streamablehttp_client(url, timeout=25, sse_read_timeout=25) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await _call(session, "set_humanoid_mode", {"mode": "external"})
            pre = await _call(session, "run_humanoid_preflight", {})
            print("preflight:", pre, flush=True)

            await _call(session, "disable_humanoid_motion", {"reason": "reset_pose_check"})
            st0 = await _call(session, "get_robot_state_for", {"robot_kind": "humanoid"})
            print("state0.base:", (st0.get("base") or {}), flush=True)

            # Use the maze interior spawn by default (cell 1,1 center for cell_size=1.4).
            res = await _call(session, "reset_humanoid_episode", {"x": -1.9, "y": -1.9, "yaw": 0.0})
            print("reset_humanoid_episode:", res, flush=True)

            # Give a moment for physics-step applied reset to land.
            t0 = time.time()
            while (time.time() - t0) < 4.0:
                await _call(session, "send_humanoid_heartbeat", {"source": "reset_pose_check"})
                st = await _call(session, "get_robot_state_for", {"robot_kind": "humanoid"})
                b = st.get("base") or {}
                x = float(b.get("x", 0.0) or 0.0)
                y = float(b.get("y", 0.0) or 0.0)
                z = float(b.get("z", 0.0) or 0.0)
                print(f"t={time.time()-t0:4.1f}s base=({x:+.2f},{y:+.2f},{z:+.2f}) yaw={float(b.get('yaw',0.0)):+.2f}", flush=True)
                await asyncio.sleep(0.3)


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=30.0))

