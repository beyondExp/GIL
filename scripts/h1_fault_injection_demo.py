import asyncio
import json

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def _print_tool(session: ClientSession, name: str, args: dict) -> None:
    res = await session.call_tool(name, args)
    text = res.content[0].text if res.content else "{}"
    print(f"{name}: {text}")


async def main() -> None:
    url = "http://127.0.0.1:6769/mcp/"
    async with streamablehttp_client(url, timeout=20, sse_read_timeout=20) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await _print_tool(session, "run_humanoid_preflight", {})
            await _print_tool(session, "enable_humanoid_motion", {"reason": "fault injection demo"})
            await _print_tool(session, "send_humanoid_heartbeat", {"source": "fault_injection_demo"})
            await _print_tool(
                session,
                "drive_humanoid",
                {"vx": 0.1, "vy": 0.0, "wz": 0.0, "duration_s": 0.2, "reason": "fault injection baseline"},
            )
            await _print_tool(session, "set_humanoid_estop", {"active": True, "reason": "fault injection"})
            await _print_tool(
                session,
                "drive_humanoid",
                {"vx": 0.1, "vy": 0.0, "wz": 0.0, "duration_s": 0.2, "reason": "should be rejected"},
            )
            await _print_tool(session, "get_humanoid_health", {})


if __name__ == "__main__":
    asyncio.run(main())
