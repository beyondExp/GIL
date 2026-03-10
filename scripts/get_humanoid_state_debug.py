import asyncio
import json

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> None:
    url = "http://127.0.0.1:6769/mcp/"
    async with streamablehttp_client(url, timeout=20, sse_read_timeout=20) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("get_robot_state_for", {"robot_kind": "humanoid"})
            txt = res.content[0].text if res.content else ""
            data = json.loads(txt)
            sensors = data.get("sensors") or {}
            imu = sensors.get("imu")
            contacts = (sensors.get("contacts") or {})
            debug = sensors.get("_debug")
            print(
                json.dumps(
                    {
                        "keys": sorted(list(data.keys())),
                        "robot_kind": data.get("robot_kind"),
                        "base": data.get("base"),
                        "has_image": bool(data.get("last_image") or data.get("last_image_wide")),
                        "imu_present": bool(imu),
                        "contact_keys": sorted(list(contacts.keys())),
                        "contact_in_contact": {k: bool((contacts.get(k) or {}).get("in_contact")) for k in contacts.keys()},
                        "debug": debug,
                    },
                    indent=2,
                )
            )


if __name__ == "__main__":
    asyncio.run(main())


