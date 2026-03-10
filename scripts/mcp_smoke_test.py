import asyncio
import json
import logging

from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession


async def main() -> None:
    logging.basicConfig(level=logging.DEBUG)
    url = "http://127.0.0.1:6769/mcp/"
    print(f"Connecting to {url} ...", flush=True)
    async with streamablehttp_client(url, timeout=10, sse_read_timeout=10) as (read, write, get_session_id):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=10.0)
            tools = await asyncio.wait_for(session.list_tools(), timeout=10.0)
            tool_names = [t.name for t in tools.tools]
            print("TOOLS:", json.dumps(tool_names, indent=2), flush=True)

            # Try a minimal command (will move only if Isaac Sim bridge is connected + timeline playing).
            if "set_humanoid_mode" in tool_names:
                res = await asyncio.wait_for(session.call_tool("set_humanoid_mode", {"mode": "external"}), timeout=10.0)
                print("set_humanoid_mode:", res.content[0].text if res.content else res, flush=True)
            if "drive_humanoid" in tool_names:
                res = await asyncio.wait_for(
                    session.call_tool(
                        "drive_humanoid",
                        {"vx": 0.2, "vy": 0.0, "wz": 0.0, "duration_s": 0.1, "reason": "mcp smoke test"},
                    ),
                    timeout=10.0,
                )
                print("drive_humanoid:", res.content[0].text if res.content else res, flush=True)


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=15.0))


