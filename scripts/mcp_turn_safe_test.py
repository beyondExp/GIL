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
            print("preflight:", pre, flush=True)
            en = await _call(session, "enable_humanoid_motion", {"reason": "turn_safe_test"})
            print("enable:", en, flush=True)

            st0 = await _call(session, "get_robot_state_for", {"robot_kind": "humanoid"})
            b0 = st0.get("base") or {}
            print("pose0:", b0, flush=True)

            await _call(session, "send_humanoid_heartbeat", {"source": "turn_safe_test"})
            res = await _call(
                session,
                "drive_humanoid",
                {"vx": 0.0, "vy": 0.0, "wz": 0.35, "duration_s": 1.2, "reason": "turn_safe"},
            )
            print("drive:", res, flush=True)

            st1 = await _call(session, "get_robot_state_for", {"robot_kind": "humanoid"})
            b1 = st1.get("base") or {}
            print("pose1:", b1, flush=True)

            await _call(session, "disable_humanoid_motion", {"reason": "turn_safe_test"})


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=45.0))

