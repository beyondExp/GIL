import argparse
import asyncio

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> None:
    ap = argparse.ArgumentParser(description="Send a single drive_humanoid command via MCP HTTP.")
    ap.add_argument("--url", default="http://127.0.0.1:6769/mcp/", help="MCP streamable-http URL")
    ap.add_argument("--vx", type=float, default=0.0)
    ap.add_argument("--vy", type=float, default=0.0)
    ap.add_argument("--wz", type=float, default=0.0)
    ap.add_argument("--duration_s", type=float, default=1.0)
    ap.add_argument("--mode", default="external", choices=["external", "goal"])
    ap.add_argument("--reason", default="custom")
    args = ap.parse_args()

    async with streamablehttp_client(args.url, timeout=20, sse_read_timeout=20) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool("set_humanoid_mode", {"mode": args.mode})
            res = await session.call_tool(
                "drive_humanoid",
                {
                    "vx": float(args.vx),
                    "vy": float(args.vy),
                    "wz": float(args.wz),
                    "duration_s": float(args.duration_s),
                    "reason": str(args.reason),
                },
            )
            if res.content:
                print(res.content[0].text)
            else:
                print(res)


if __name__ == "__main__":
    asyncio.run(main())





