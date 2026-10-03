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
    ap = argparse.ArgumentParser(description="Smoke test: create episode + write beliefs/keyframe/map pointers to Neo4j.")
    ap.add_argument("--url", default="http://127.0.0.1:6769/mcp/")
    ap.add_argument("--robot_id", default="humanoid")
    ap.add_argument("--stage_id", default="stand_idle")
    args = ap.parse_args()

    mem = Neo4jSpatialMemory.from_env()
    mem.ensure_schema()

    episode_id = mem.start_episode(robot_id=args.robot_id, stage_id=args.stage_id, started_at_s=time.time(), meta={"kind": "smoke"})
    print("episode_id:", episode_id, flush=True)

    try:
        # Record a map artifact pointer (placeholder local path).
        mem.upsert_map_artifact(
            robot_id=args.robot_id,
            episode_id=episode_id,
            map_artifact={
                "map_id": f"{episode_id}-toy-occupancy",
                "kind": "occupancy_grid",
                "created_at_s": time.time(),
                "frame": "world",
                "blob": {"uri": f"E:/GIL/maps/{episode_id}/occupancy.json", "storage": "local_path"},
                "extra": {"note": "placeholder; dense TSDF/features will live outside Neo4j"},
            },
        )

        async with streamablehttp_client(args.url, timeout=25, sse_read_timeout=25) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                st = await _call(session, "get_robot_state_for", {"robot_kind": "humanoid"})
                b = st.get("base") or {}
                pose = Pose(
                    x=float(b.get("x", 0.0)),
                    y=float(b.get("y", 0.0)),
                    z=float(b.get("z", 0.0)),
                    yaw=float(b.get("yaw", 0.0)),
                    frame="world",
                )
                mem.record_belief(
                    BeliefState(
                        robot_id=args.robot_id,
                        pose=pose,
                        observed_at_s=float(time.time()),
                        source="mcp:get_robot_state_for",
                        health={"backend": st.get("backend"), "mode": st.get("mode")},
                    ),
                    episode_id=episode_id,
                )

                # Record a keyframe pointer (placeholder URIs).
                mem.record_keyframe(
                    robot_id=args.robot_id,
                    episode_id=episode_id,
                    keyframe_id=f"{episode_id}-kf0",
                    observed_at_s=time.time(),
                    frame="world",
                    pose={"x": pose.x, "y": pose.y, "z": pose.z, "yaw": pose.yaw},
                    intrinsics={"note": "fill from camera_info later"},
                    rgb={"uri": f"E:/GIL/maps/{episode_id}/rgb_000.jpg", "storage": "local_path"},
                    depth={"uri": f"E:/GIL/maps/{episode_id}/depth_000.png", "storage": "local_path"},
                )

        mem.end_episode(episode_id=episode_id, ended_at_s=time.time(), outcome={"ok": True})
        print("Counts:", mem.dump_debug_counts())
    finally:
        mem.close()


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=60.0))

