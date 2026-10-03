#!/usr/bin/env python3
"""Reset the humanoid episode via gil_controls MCP (safe reset)."""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


MCP_URL = os.getenv("GIL_CONTROLS_URL", "http://127.0.0.1:6769/mcp/")


async def call(session: ClientSession, tool: str, args: dict | None = None) -> dict:
    res = await session.call_tool(tool, args or {})
    text = res.content[0].text if res.content else "{}"
    return json.loads(text)


async def main() -> int:
    async with streamablehttp_client(MCP_URL, timeout=20, sse_read_timeout=20) as (read, write, _):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=10.0)
            hb = await call(session, "send_humanoid_heartbeat", {"source": "reset_humanoid"})
            dis = await call(session, "disable_humanoid_motion", {"reason": "reset"})
            rst = await call(session, "reset_humanoid_episode", {})
            await asyncio.sleep(3.0)
            obs = await call(session, "get_observation", {"robot_kind": "humanoid"})
            base = (obs.get("state") or {}).get("base") or {}
            print(json.dumps({"heartbeat": hb, "disable": dis, "reset": rst, "base": base}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

