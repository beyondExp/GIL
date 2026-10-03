#!/usr/bin/env python3
"""Emergency stop for the humanoid via gil_controls MCP.

Uses the same StreamableHTTP MCP client used by other scripts, so it works even
when IDE tool discovery is unhealthy.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


MCP_URL = os.getenv("GIL_CONTROLS_URL", "http://127.0.0.1:6769/mcp/")


async def call(session: ClientSession, tool: str, args: dict | None = None) -> dict:
    try:
        res = await asyncio.wait_for(session.call_tool(tool, args or {}), timeout=10.0)
        text = res.content[0].text if res.content else "{}"
        return json.loads(text)
    except Exception as exc:
        return {"_error": str(exc)}


async def main() -> int:
    async with streamablehttp_client(MCP_URL, timeout=15, sse_read_timeout=15) as (read, write, _):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=10.0)
            stop = await call(session, "stop_humanoid_now", {"reason": "safety_stop"})
            dis = await call(session, "disable_humanoid_motion", {"reason": "safety_stop"})
            hb = await call(session, "send_humanoid_heartbeat", {"source": "stop_humanoid"})
            print(json.dumps({"stop": stop, "disable": dis, "heartbeat": hb}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

