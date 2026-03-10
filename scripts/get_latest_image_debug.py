import asyncio
import json

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> None:
    url = "http://127.0.0.1:6769/mcp/"
    async with streamablehttp_client(url, timeout=20, sse_read_timeout=20) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("get_latest_image", {})
            txt = res.content[0].text if res.content else ""
            data = json.loads(txt)
            imgs = data.get("images") or {}
            print(json.dumps({"robot_kind": data.get("robot_kind"), "extra_images": len(imgs), "paths": list(imgs.keys())}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())







