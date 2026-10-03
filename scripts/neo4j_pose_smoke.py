from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from gil.memory.neo4j_memory import Neo4jSpatialMemory
from gil.memory.types import BeliefState, Pose


async def _call(session: ClientSession, name: str, args: dict[str, Any]) -> dict[str, Any]:
    res = await session.call_tool(name, args)
    txt = res.content[0].text if res.content else "{}"
    try:
        out = json.loads(txt)
        return out if isinstance(out, dict) else {"value": out}
    except Exception:
        return {"_raw": txt}


async def main() -> None:
    ap = argparse.ArgumentParser(description="Smoke test: write humanoid base pose observations to Neo4j.")
    ap.add_argument("--url", default="http://127.0.0.1:6769/mcp/")
    ap.add_argument("--robot_id", default="humanoid")
    ap.add_argument("--n", type=int, default=5)
    args = ap.parse_args()

    mem = Neo4jSpatialMemory.from_env()
    mem.ensure_schema()

    try:
        async with streamablehttp_client(args.url, timeout=25, sse_read_timeout=25) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for i in range(int(args.n)):
                    st = await _call(session, "get_robot_state_for", {"robot_kind": "humanoid"})
                    b = st.get("base") or {}
                    pose = Pose(
                        x=float(b.get("x", 0.0)),
                        y=float(b.get("y", 0.0)),
                        z=float(b.get("z", 0.0)),
                        yaw=float(b.get("yaw", 0.0)),
                        frame="world",
                    )
                    belief = BeliefState(
                        robot_id=str(args.robot_id),
                        pose=pose,
                        observed_at_s=float(time.time()),
                        source="mcp:get_robot_state_for",
                        health={"backend": st.get("backend"), "mode": st.get("mode")},
                    )
                    obs_id = mem.record_belief(belief, episode_id="pose_smoke")
                    print(f"[{i}] wrote obs_id={obs_id} pose=({pose.x:+.2f},{pose.y:+.2f},{pose.z:+.2f}) yaw={pose.yaw:+.2f}")
                    await asyncio.sleep(0.4)
        print("Counts:", mem.dump_debug_counts())
    finally:
        mem.close()


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=60.0))

