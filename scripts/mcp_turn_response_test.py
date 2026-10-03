from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def _call(session: ClientSession, name: str, args: dict[str, Any]) -> dict[str, Any]:
    res = await session.call_tool(name, args)
    txt = res.content[0].text if res.content else "{}"
    try:
        out = json.loads(txt)
        return out if isinstance(out, dict) else {"value": out}
    except Exception:
        return {"_raw": txt}


async def main() -> None:
    url = "http://127.0.0.1:6769/mcp/"
    async with streamablehttp_client(url, timeout=25, sse_read_timeout=25) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await _call(session, "set_humanoid_mode", {"mode": "external"})
            pre = await _call(session, "run_humanoid_preflight", {})
            print("preflight:", pre)
            en = await _call(session, "enable_humanoid_motion", {"reason": "turn_response_test"})
            print("enable:", en)

            st0 = await _call(session, "get_robot_state_for", {"robot_kind": "humanoid"})
            yaw0 = float((st0.get("base") or {}).get("yaw", 0.0))
            print("yaw0:", yaw0)

            await _call(session, "send_humanoid_heartbeat", {"source": "turn_response_test"})
            await _call(
                session,
                "drive_humanoid",
                {"vx": 0.0, "vy": 0.0, "wz": 1.0, "duration_s": 2.0, "reason": "turn_in_place"},
            )
            st1 = await _call(session, "get_robot_state_for", {"robot_kind": "humanoid"})
            yaw1 = float((st1.get("base") or {}).get("yaw", 0.0))
            print("yaw1:", yaw1, "dyaw:", yaw1 - yaw0)

            await _call(session, "disable_humanoid_motion", {"reason": "turn_response_test"})


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=30.0))

