import asyncio
import json

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def _get_base(session: ClientSession) -> dict:
    res = await session.call_tool("get_robot_state", {})
    txt = res.content[0].text if res.content else ""
    data = json.loads(txt) if txt else {}
    return data.get("base") or {}


async def main() -> None:
    url = "http://127.0.0.1:6769/mcp/"
    async with streamablehttp_client(url, timeout=20, sse_read_timeout=20) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool("set_humanoid_mode", {"mode": "external"})

            base0 = await _get_base(session)
            print("base_before:", json.dumps(base0, indent=2))

            # Forward for 5s
            await session.call_tool(
                "drive_humanoid",
                {"vx": 0.6, "vy": 0.0, "wz": 0.0, "duration_s": 5.0, "reason": "demo forward"},
            )
            base1 = await _get_base(session)
            print("base_after_forward:", json.dumps(base1, indent=2))

            # Turn in place for 3s
            await session.call_tool(
                "drive_humanoid",
                {"vx": 0.0, "vy": 0.0, "wz": 1.0, "duration_s": 3.0, "reason": "demo rotate"},
            )
            base2 = await _get_base(session)
            print("base_after_rotate:", json.dumps(base2, indent=2))


if __name__ == "__main__":
    asyncio.run(main())







