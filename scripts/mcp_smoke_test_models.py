import asyncio
import json
import logging

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    url = "http://127.0.0.1:6770/mcp/"
    async with streamablehttp_client(url, timeout=15, sse_read_timeout=15) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("MODEL_TOOLS:", json.dumps([t.name for t in tools.tools], indent=2))


if __name__ == "__main__":
    asyncio.run(main())







