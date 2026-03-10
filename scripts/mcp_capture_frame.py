import asyncio
import base64
import json
import os
import time
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def _decode_data_url(data_url: str) -> bytes:
    # expected: data:image/jpeg;base64,....
    if not data_url:
        return b""
    if "," in data_url:
        _, b64 = data_url.split(",", 1)
    else:
        b64 = data_url
    return base64.b64decode(b64)


async def main() -> None:
    url = "http://127.0.0.1:6769/mcp/"
    out_dir = Path("artifacts") / "mcp_frames"
    out_dir.mkdir(parents=True, exist_ok=True)

    async with streamablehttp_client(url, timeout=30, sse_read_timeout=30) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            res_state = await session.call_tool("get_robot_state_for", {"robot_kind": "humanoid"})
            base = (json.loads(res_state.content[0].text).get("base") or {}) if res_state.content else {}

            res_img = await session.call_tool("get_latest_image", {})
            data = json.loads(res_img.content[0].text) if res_img.content else {}
            img = data.get("image") or ""
            img_wide = data.get("image_wide") or ""

            ts = time.strftime("%Y%m%d_%H%M%S")
            stem = f"{ts}_x{base.get('x',0):.2f}_y{base.get('y',0):.2f}_yaw{base.get('yaw',0):.2f}"
            p1 = out_dir / f"{stem}.jpg"
            p2 = out_dir / f"{stem}_wide.jpg"

            if img:
                p1.write_bytes(_decode_data_url(img))
                print(f"wrote {p1}")
            else:
                print("no image")
            if img_wide:
                p2.write_bytes(_decode_data_url(img_wide))
                print(f"wrote {p2}")
            else:
                print("no image_wide")


if __name__ == "__main__":
    asyncio.run(main())





