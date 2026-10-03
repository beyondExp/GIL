#!/usr/bin/env python3
"""Smoke-test the closed-loop steer(commit=True) path."""

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
            await call(session, "send_humanoid_heartbeat", {"source": "steer_smoke"})
            res = await call(
                session,
                "steer",
                {
                    "instruction": "escape the maze by reaching the exit",
                    "embodiment": "unitree_h1",
                    "world_source": "text",
                    "world_content": "isaac maze seed 0",
                    "commit": True,
                },
            )
            print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

