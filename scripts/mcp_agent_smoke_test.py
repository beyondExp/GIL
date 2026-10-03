from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> None:
    ap = argparse.ArgumentParser(description="Smoke test: AgentDirector tools over MCP (steer + preview).")
    ap.add_argument("--url", default="http://127.0.0.1:6769/mcp/")
    ap.add_argument(
        "--world",
        default=str(Path("tests/fixtures/hf_occupancy.json")),
        help="Local JSON occupancy fixture path, or HF ref (repo or repo:file).",
    )
    ap.add_argument("--embodiment", default="unitree_h1")
    ap.add_argument("--commit", action="store_true", help="Attempt a gated execute (requires autonomy token for hardware).")
    args = ap.parse_args()

    world = args.world
    if Path(world).exists():
        world = str(Path(world).resolve())

    async with streamablehttp_client(args.url, timeout=25, sse_read_timeout=25) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = {t.name for t in tools.tools}

            required = {"steer", "preview_plan", "list_embodiments", "select_embodiment", "ingest_world", "dream"}
            missing = sorted(required - tool_names)
            if missing:
                raise SystemExit(f"Missing required tools: {missing}")

            robots = await session.call_tool("list_embodiments", {})
            print("list_embodiments:", robots.content[0].text if robots.content else robots)

            # Do a full agent pass using the HF occupancy fixture (dream-only by default).
            res = await session.call_tool(
                "steer",
                {
                    "instruction": "escape to the exit",
                    "embodiment": args.embodiment,
                    "world_source": "huggingface",
                    "world_content": world,
                    "commit": bool(args.commit),
                },
            )
            txt = res.content[0].text if res.content else "{}"
            data = json.loads(txt)
            print("steer.executed:", data.get("executed"), "world:", data.get("world"), "kept:", data.get("dream", {}).get("kept"))

            preview = await session.call_tool("preview_plan", {})
            print("preview_plan:", preview.content[0].text if preview.content else preview)


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=30.0))

