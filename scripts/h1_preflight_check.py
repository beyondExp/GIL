import asyncio
import json

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> None:
    url = "http://127.0.0.1:6769/mcp/"
    async with streamablehttp_client(url, timeout=20, sse_read_timeout=20) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            preflight = await session.call_tool("run_humanoid_preflight", {})
            health = await session.call_tool("get_humanoid_health", {})
            print("preflight:")
            print(preflight.content[0].text if preflight.content else "{}")
            print("health:")
            print(health.content[0].text if health.content else "{}")


if __name__ == "__main__":
    asyncio.run(main())
