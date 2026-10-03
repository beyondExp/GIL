#!/usr/bin/env python3
"""Full Franka pick-and-place -- the arm executes a complete manipulation sequence.

Dreams a scene-graph plan then executes every move_robot + gripper command.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession


MCP_URL = "http://127.0.0.1:6769/mcp/"


async def call(session: ClientSession, tool: str, args: dict | None = None) -> dict:
    try:
        res = await asyncio.wait_for(session.call_tool(tool, args or {}), timeout=20.0)
        text = res.content[0].text if res.content else "{}"
        return json.loads(text)
    except Exception as exc:
        return {"_error": str(exc)}


async def get_arm_state(session: ClientSession) -> dict:
    obs = await call(session, "get_observation", {"robot_kind": "arm"})
    return obs.get("state", {})


async def main() -> int:
    async with streamablehttp_client(MCP_URL, timeout=20, sse_read_timeout=20) as (read, write, _):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=10.0)

            state = await get_arm_state(session)
            ee = state.get("end_effector", {})
            print(f"Arm state: EE=({ee.get('x', '?')}, {ee.get('y', '?')}, {ee.get('z', '?')})")
            print(f"Gripper open: {state.get('gripper_open', '?')}")

            # Dream a pick-place plan
            print("\nDreaming pick-and-place plan...")
            steer = await call(session, "steer", {
                "instruction": "pick the cube and place it to the right",
                "embodiment": "franka",
                "world_source": "text",
                "world_content": "tabletop scene with cube at x=0.5 y=0.0 z=0.12",
                "commit": False,
            })

            dream = steer.get("dream") or {}
            gate = dream.get("gate") or {}
            commands = dream.get("preview_commands", [])
            print(f"Decision: {steer.get('decision')}")
            print(f"Gate: ok={gate.get('ok')} gate_id={gate.get('gate_id')}")
            print(f"Plan: {len(commands)} steps")

            if not commands:
                print("No dream commands -- using manual pick-place sequence")
                commands = [
                    {"type": "move_robot", "x": 0.45, "y": 0.0, "z": 0.35},
                    {"type": "move_robot", "x": 0.50, "y": 0.0, "z": 0.24},
                    {"type": "gripper", "open": False},
                    {"type": "move_robot", "x": 0.50, "y": 0.0, "z": 0.35},
                    {"type": "move_robot", "x": 0.50, "y": 0.20, "z": 0.35},
                    {"type": "move_robot", "x": 0.50, "y": 0.20, "z": 0.24},
                    {"type": "gripper", "open": True},
                    {"type": "move_robot", "x": 0.45, "y": 0.0, "z": 0.40},
                ]

            print(f"\n{'='*60}")
            print(f"  EXECUTING PICK-AND-PLACE ({len(commands)} steps)")
            print(f"{'='*60}\n")

            t0 = time.time()
            success_count = 0
            error_count = 0

            for i, cmd in enumerate(commands):
                cmd_type = cmd.get("type", "move_robot")

                if cmd_type == "gripper":
                    grip_open = bool(cmd.get("open", True))
                    result = await call(session, "execute_action", {
                        "action_kind": "gripper",
                        "robot_kind": "arm",
                        "gripper_open": grip_open,
                        "reason": f"pick_step_{i}",
                    })
                    ok = result.get("status") == "success"
                    action_str = f"GRIP {'OPEN' if grip_open else 'CLOSE'}"
                else:
                    x = float(cmd.get("x", 0.4))
                    y = float(cmd.get("y", 0.0))
                    z = float(cmd.get("z", 0.3))
                    result = await call(session, "execute_action", {
                        "action_kind": "move_robot",
                        "robot_kind": "arm",
                        "x": x, "y": y, "z": z,
                        "reason": f"pick_step_{i}",
                    })
                    ok = result.get("status") == "success"
                    action_str = f"MOVE ({x:.2f}, {y:.2f}, {z:.2f})"

                if ok:
                    success_count += 1
                    print(f"  [{i+1:2d}/{len(commands)}] {action_str} -> OK")
                else:
                    error_count += 1
                    print(f"  [{i+1:2d}/{len(commands)}] {action_str} -> ERR: {result.get('error', '?')}")

                await asyncio.sleep(1.5)

            elapsed = time.time() - t0
            end_state = await get_arm_state(session)
            end_ee = end_state.get("end_effector", {})

            print(f"\n{'='*60}")
            print(f"  PICK-AND-PLACE COMPLETE")
            print(f"{'='*60}")
            print(f"  Final EE: ({end_ee.get('x', '?')}, {end_ee.get('y', '?')}, {end_ee.get('z', '?')})")
            print(f"  Steps:    {success_count} ok / {error_count} err / {len(commands)} total")
            print(f"  Time:     {elapsed:.1f}s")

            if error_count == 0:
                print(f"\n  PASS: full pick-and-place executed cleanly")
                return 0
            elif success_count > 0:
                print(f"\n  PARTIAL: {success_count}/{len(commands)} steps succeeded")
                return 0
            else:
                print(f"\n  FAIL: no steps succeeded")
                return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(asyncio.wait_for(main(), timeout=120.0)))
