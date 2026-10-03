#!/usr/bin/env python3
"""Live Isaac Sim integration test.

Connects to the GIL controls MCP server and runs:
1. Tool discovery (verify new unified tools exist)
2. get_observation (unified) + get_robot_state (legacy)
3. Heartbeat + preflight
4. steer (dream-then-act pipeline) without commit
5. execute_action cmd_vel (unified) with a small forward command
6. get_metrics

Requires: MCP server on 127.0.0.1:6769, Isaac Sim connected via WebSocket.
"""
from __future__ import annotations

import asyncio
import json
import sys

from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession


async def call(session: ClientSession, tool: str, args: dict | None = None, *, label: str = "") -> dict:
    """Call an MCP tool and return parsed JSON."""
    tag = label or tool
    try:
        res = await asyncio.wait_for(session.call_tool(tool, args or {}), timeout=15.0)
        text = res.content[0].text if res.content else "{}"
        data = json.loads(text)
        return data
    except Exception as exc:
        print(f"  [{tag}] ERROR: {exc}")
        return {"error": str(exc)}


async def main() -> int:
    errors: list[str] = []
    url = "http://127.0.0.1:6769/mcp/"
    print(f"Connecting to {url} ...")

    async with streamablehttp_client(url, timeout=15, sse_read_timeout=15) as (read, write, _):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=10.0)
            tools_resp = await asyncio.wait_for(session.list_tools(), timeout=10.0)
            tool_names = sorted(t.name for t in tools_resp.tools)

            print(f"\n=== 1. Tool discovery ({len(tool_names)} tools) ===")
            print(f"  {tool_names}")
            for required in ("execute_action", "get_observation", "get_metrics",
                             "list_embodiments", "steer", "dream"):
                if required not in tool_names:
                    errors.append(f"Missing MCP tool: {required}")
                    print(f"  MISSING: {required}")

            print("\n=== 2. Heartbeat + preflight ===")
            await call(session, "send_humanoid_heartbeat", {"source": "live_test"})
            preflight = await call(session, "run_humanoid_preflight")
            print(f"  preflight: {json.dumps(preflight, indent=2)}")

            print("\n=== 3. get_observation (unified) ===")
            obs = await call(session, "get_observation", {"robot_kind": "humanoid"})
            morph = obs.get("morphology", "")
            has_state = bool(obs.get("state"))
            has_image = bool(obs.get("image"))
            print(f"  morphology={morph} has_state={has_state} has_image={has_image}")
            if not has_state:
                errors.append("get_observation returned no state")

            print("\n=== 4. get_robot_state (legacy) ===")
            state = await call(session, "get_robot_state")
            print(f"  keys: {list(state.keys())[:10]}")

            print("\n=== 5. list_embodiments ===")
            embods = await call(session, "list_embodiments")
            robots = embods.get("robots", [])
            print(f"  {len(robots)} robots: {[r.get('key') for r in robots]}")
            if not robots:
                errors.append("list_embodiments returned no robots")

            print("\n=== 6. steer (dream without commit) ===")
            steer_result = await call(session, "steer", {
                "instruction": "escape the maze",
                "embodiment": "unitree_h1",
                "commit": False,
            })
            decision = steer_result.get("decision", "")
            dream = steer_result.get("dream") or {}
            gate = dream.get("gate") or {}
            print(f"  decision={decision}")
            print(f"  gate.ok={gate.get('ok')} gate.reason={gate.get('reason')}")
            if dream.get("kept"):
                print(f"  kept={dream['kept']} dreams={len(dream.get('dreams', []))}")
            if steer_result.get("executed"):
                errors.append("steer with commit=False should not execute")

            print("\n=== 7. execute_action cmd_vel (unified) ===")
            await call(session, "send_humanoid_heartbeat", {"source": "live_test"})
            await call(session, "set_humanoid_mode", {"mode": "external"})
            enable = await call(session, "enable_humanoid_motion", {"reason": "live_test"})
            print(f"  enable_motion: {enable.get('status', enable.get('ok'))}")
            exec_result = await call(session, "execute_action", {
                "action_kind": "cmd_vel",
                "robot_kind": "humanoid",
                "vx": 0.15,
                "vy": 0.0,
                "wz": 0.0,
                "duration_s": 0.25,
                "reason": "live_integration_test",
            })
            print(f"  execute_action: {json.dumps(exec_result)}")
            if exec_result.get("status") != "success":
                errors.append(f"execute_action failed: {exec_result.get('error')}")

            await call(session, "disable_humanoid_motion", {"reason": "live_test_done"})

            print("\n=== 8. get_metrics ===")
            metrics = await call(session, "get_metrics")
            print(f"  counters: {metrics.get('counters', {})}")
            print(f"  gauges: {metrics.get('gauges', {})}")

    print()
    if errors:
        print(f"FAIL -- {len(errors)} error(s):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("PASS -- live Isaac integration test complete")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(asyncio.wait_for(main(), timeout=120.0)))
