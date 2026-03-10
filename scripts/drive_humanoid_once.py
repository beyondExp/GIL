import asyncio

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> None:
    url = "http://127.0.0.1:6769/mcp/"
    async with streamablehttp_client(url, timeout=15, sse_read_timeout=15) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool("set_humanoid_mode", {"mode": "external"})
            res = await session.call_tool(
                "drive_humanoid",
                {"vx": 0.5, "vy": 0.0, "wz": 0.0, "duration_s": 2.0, "reason": "manual test"},
            )
            # Print tool return payload
            if res.content:
                print(res.content[0].text)
            else:
                print(res)


if __name__ == "__main__":
    asyncio.run(main())







