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
            try:
                data = json.loads(txt)
            except Exception:
                print(txt)
                return

            if "error" in data:
                print(json.dumps(data, indent=2))
                return

            # Print a small summary (avoid dumping huge base64)
            def _len(x):
                return 0 if not x else len(str(x))

            print(
                json.dumps(
                    {
                        "robot_kind": data.get("robot_kind"),
                        "image_len": _len(data.get("image")),
                        "image_wide_len": _len(data.get("image_wide")),
                        "has_camera_info": data.get("camera_info") is not None,
                        "has_camera_info_wide": data.get("camera_info_wide") is not None,
                    },
                    indent=2,
                )
            )


if __name__ == "__main__":
    asyncio.run(main())







