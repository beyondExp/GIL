import asyncio
import json

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> None:
    url = "http://127.0.0.1:6769/mcp/"
    async with streamablehttp_client(url, timeout=15, sse_read_timeout=15) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("get_robot_state", {})
            txt = res.content[0].text if res.content else ""
            try:
                data = json.loads(txt)
            except Exception:
                print(txt)
                return
            print(
                json.dumps(
                    {
                        "robot_kind": data.get("robot_kind"),
                        "base": data.get("base"),
                        "objects_keys": sorted(list((data.get("objects") or {}).keys())),
                        "has_image": bool(data.get("last_image") or data.get("last_image_wide")),
                    },
                    indent=2,
                )
            )


if __name__ == "__main__":
    asyncio.run(main())


