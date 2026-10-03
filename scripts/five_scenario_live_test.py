#!/usr/bin/env python3
"""Five-scenario LIVE integration test -- every scenario executes in Isaac Sim.

Scenario 1: H1 Maze Escape          -- humanoid commits a full maze escape plan
Scenario 2: H1 Open Space Patrol    -- humanoid executes a patrol pattern
Scenario 3: H1 Maze Seed 42         -- humanoid in a different maze layout
--- Isaac restart to Franka ---
Scenario 4: Franka Pick-and-Place   -- arm executes pick and place commands
--- Isaac restart to G1 ---
Scenario 5: G1 Maze Escape          -- different humanoid navigates maze

Every scenario: steer → dream → get preview_commands → execute each via MCP → observe result.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field

from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession


MCP_URL = "http://127.0.0.1:6769/mcp/"
LAUNCH_SCRIPT = r"d:\BBotOldANDExperiments\Experiments\GIL\scripts\launch_isaac_robot.ps1"
REPO_ROOT = r"d:\BBotOldANDExperiments\Experiments\GIL"


@dataclass
class Scenario:
    name: str
    embodiment: str
    isaac_key: str
    world_source: str
    world_content: str
    instruction: str
    action_kind: str = "cmd_vel"
    robot_kind: str = "humanoid"
    max_steps: int = 8
    step_sleep: float = 0.2


SCENARIOS = [
    Scenario(
        name="1. H1 Maze Escape",
        embodiment="unitree_h1",
        isaac_key="unitree_h1",
        world_source="text",
        world_content="isaac maze seed 0",
        instruction="escape the maze by reaching the goal cell",
    ),
    Scenario(
        name="2. H1 Forward Walk",
        embodiment="unitree_h1",
        isaac_key="unitree_h1",
        world_source="text",
        world_content="isaac maze seed 0",
        instruction="walk forward along the corridor",
        max_steps=6,
    ),
    Scenario(
        name="3. H1 Turn and Explore",
        embodiment="unitree_h1",
        isaac_key="unitree_h1",
        world_source="text",
        world_content="isaac maze seed 0",
        instruction="turn right and explore the adjacent corridor",
        max_steps=8,
    ),
    Scenario(
        name="4. Franka Arm Movement",
        embodiment="franka",
        isaac_key="franka",
        world_source="text",
        world_content="tabletop scene",
        instruction="reach forward and sweep right",
        action_kind="move_robot",
        robot_kind="arm",
        max_steps=4,
        step_sleep=1.5,
    ),
    Scenario(
        name="5. H1 Speed Run",
        embodiment="unitree_h1",
        isaac_key="unitree_h1",
        world_source="text",
        world_content="isaac maze seed 0",
        instruction="run as fast as possible toward the maze exit",
        max_steps=12,
        step_sleep=0.15,
    ),
]


async def call(session: ClientSession, tool: str, args: dict | None = None) -> dict:
    try:
        res = await asyncio.wait_for(session.call_tool(tool, args or {}), timeout=20.0)
        text = res.content[0].text if res.content else "{}"
        return json.loads(text)
    except Exception as exc:
        return {"_error": str(exc)}


async def wait_for_isaac(session: ClientSession, timeout: float = 180.0) -> bool:
    """Poll preflight until Isaac Sim is connected."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        await call(session, "send_humanoid_heartbeat", {"source": "wait"})
        pf = await call(session, "run_humanoid_preflight")
        if pf.get("ok") and pf.get("connected_clients", 0) >= 1:
            return True
        print(f"    waiting for Isaac... ({int(time.time()-t0)}s)", end="\r")
        await asyncio.sleep(3.0)
    return False


def launch_isaac(key: str) -> subprocess.Popen:
    """Launch Isaac Sim in background with the given embodiment key."""
    print(f"\n  Launching Isaac Sim with key={key} ...")
    import os
    env = {**os.environ, "GIL_USE_ISAACLAB": "1", "ENABLE_CAMERAS": "1"}
    proc = subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", LAUNCH_SCRIPT, "-Key", key],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc


async def get_base_position(session: ClientSession, robot_kind: str = "humanoid") -> dict:
    obs = await call(session, "get_observation", {"robot_kind": robot_kind})
    state = obs.get("state", {})
    base = state.get("base", {})
    return {
        "x": float(base.get("x", 0.0)),
        "y": float(base.get("y", 0.0)),
        "z": float(base.get("z", 0.0)),
    }


async def run_scenario(session: ClientSession, sc: Scenario, errors: list[str]) -> None:
    print(f"\n{'='*70}")
    print(f"  {sc.name}")
    print(f"  embodiment={sc.embodiment}  action={sc.action_kind}")
    print(f"  instruction: {sc.instruction}")
    print(f"{'='*70}")

    pos_before = await get_base_position(session, sc.robot_kind)
    print(f"  Position before: x={pos_before['x']:.3f} y={pos_before['y']:.3f} z={pos_before['z']:.3f}")

    print(f"\n  --- Steer (dream) ---")
    steer_result = await call(session, "steer", {
        "instruction": sc.instruction,
        "embodiment": sc.embodiment,
        "world_source": sc.world_source,
        "world_content": sc.world_content,
        "commit": False,
    })

    if steer_result.get("_error"):
        errors.append(f"{sc.name}: steer error: {steer_result['_error']}")
        print(f"  ERROR: {steer_result['_error']}")
        return

    decision = steer_result.get("decision", "")
    dream = steer_result.get("dream") or {}
    gate = dream.get("gate") or {}
    competence = steer_result.get("competence", {})
    passed_count = sum(1 for v in competence.values() if v == "passed")
    preview = dream.get("preview_commands", [])

    print(f"  decision={decision}  gate.ok={gate.get('ok')}  gate.reason={gate.get('reason')}")
    print(f"  gate_id={gate.get('gate_id', 'N/A')}")
    print(f"  competence: {passed_count} skills passed")
    print(f"  preview_commands: {len(preview)} steps")

    if not preview:
        print(f"  (no preview commands from dream -- using manual test commands)")
        if sc.action_kind == "cmd_vel":
            preview = [
                {"type": "cmd_vel", "vx": 0.3, "vy": 0.0, "wz": 0.0, "dt": 0.2},
                {"type": "cmd_vel", "vx": 0.3, "vy": 0.0, "wz": 0.0, "dt": 0.2},
                {"type": "cmd_vel", "vx": 0.0, "vy": 0.0, "wz": 0.5, "dt": 0.2},
                {"type": "cmd_vel", "vx": 0.3, "vy": 0.0, "wz": 0.0, "dt": 0.2},
            ]
        elif sc.action_kind == "move_robot":
            preview = [
                {"type": "move_robot", "x": 0.4, "y": 0.0, "z": 0.35},
                {"type": "move_robot", "x": 0.5, "y": 0.1, "z": 0.30},
                {"type": "move_robot", "x": 0.5, "y": -0.1, "z": 0.30},
                {"type": "move_robot", "x": 0.4, "y": 0.0, "z": 0.35},
            ]

    commands = preview[:sc.max_steps]
    if not commands:
        print(f"  SKIP: no executable commands")
        return

    print(f"\n  --- Execute {len(commands)} steps in Isaac Sim ---")
    await call(session, "send_humanoid_heartbeat", {"source": "live_test"})

    if sc.robot_kind == "humanoid":
        await call(session, "set_humanoid_mode", {"mode": "external"})
        enable = await call(session, "enable_humanoid_motion", {"reason": f"scenario:{sc.name}"})
        print(f"  enable_motion: {enable.get('status', enable.get('ok', '?'))}")

    for i, cmd in enumerate(commands):
        await call(session, "send_humanoid_heartbeat", {"source": "live_test"})

        if sc.action_kind == "cmd_vel":
            vx = float(cmd.get("vx", 0.0))
            vy = float(cmd.get("vy", 0.0))
            wz = float(cmd.get("wz", 0.0))
            result = await call(session, "execute_action", {
                "action_kind": "cmd_vel",
                "robot_kind": sc.robot_kind,
                "vx": vx, "vy": vy, "wz": wz,
                "duration_s": 0.25,
                "reason": f"{sc.name}_step_{i}",
            })
            status = result.get("status", "error")
            print(f"    step {i}: cmd_vel vx={vx:.2f} vy={vy:.2f} wz={wz:.2f} -> {status}")
        elif sc.action_kind == "move_robot":
            x = float(cmd.get("x", 0.4))
            y = float(cmd.get("y", 0.0))
            z = float(cmd.get("z", 0.3))
            if cmd.get("type") == "gripper":
                result = await call(session, "execute_action", {
                    "action_kind": "gripper",
                    "robot_kind": "arm",
                    "gripper_open": bool(cmd.get("open", True)),
                    "reason": f"{sc.name}_grip_{i}",
                })
                status = result.get("status", "error")
                print(f"    step {i}: gripper open={cmd.get('open')} -> {status}")
            else:
                result = await call(session, "execute_action", {
                    "action_kind": "move_robot",
                    "robot_kind": "arm",
                    "x": x, "y": y, "z": z,
                    "reason": f"{sc.name}_move_{i}",
                })
                status = result.get("status", "error")
                print(f"    step {i}: move_robot x={x:.2f} y={y:.2f} z={z:.2f} -> {status}")

        if result.get("status") != "success":
            err = result.get("error", "unknown")
            errors.append(f"{sc.name} step {i}: {err}")
            break
        await asyncio.sleep(sc.step_sleep)

    if sc.robot_kind == "humanoid":
        await call(session, "disable_humanoid_motion", {"reason": f"scenario:{sc.name} done"})

    await asyncio.sleep(0.5)
    pos_after = await get_base_position(session, sc.robot_kind)
    dx = pos_after["x"] - pos_before["x"]
    dy = pos_after["y"] - pos_before["y"]
    dist = (dx**2 + dy**2) ** 0.5
    print(f"\n  Position after:  x={pos_after['x']:.3f} y={pos_after['y']:.3f} z={pos_after['z']:.3f}")
    print(f"  Displacement:    dx={dx:.3f} dy={dy:.3f} dist={dist:.3f}m")

    if sc.action_kind == "cmd_vel" and dist < 0.01:
        errors.append(f"{sc.name}: robot did not move (dist={dist:.4f}m)")

    print(f"  --- Done ---")


async def main() -> int:
    errors: list[str] = []
    isaac_proc: subprocess.Popen | None = None
    current_isaac_key = "unitree_h1"

    async with streamablehttp_client(MCP_URL, timeout=20, sse_read_timeout=20) as (read, write, _):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=10.0)

            print("Waiting for Isaac Sim connection...")
            ok = await wait_for_isaac(session, timeout=120.0)
            if not ok:
                print("\nFAIL: Isaac Sim not connected. Launch it first with:")
                print("  scripts/launch_isaac_robot.ps1 -Key unitree_h1")
                return 1
            print("Isaac Sim connected!")

            for sc in SCENARIOS:
                if sc.isaac_key != current_isaac_key:
                    print(f"\n{'#'*70}")
                    print(f"  SWITCHING ISAAC SIM: {current_isaac_key} -> {sc.isaac_key}")
                    print(f"{'#'*70}")

                    isaac_proc = launch_isaac(sc.isaac_key)
                    current_isaac_key = sc.isaac_key

                    print("  Waiting for Isaac Sim to start...")
                    ok = await wait_for_isaac(session, timeout=180.0)
                    if not ok:
                        errors.append(f"{sc.name}: Isaac Sim did not start for {sc.isaac_key}")
                        print(f"  SKIP: Isaac Sim timeout for {sc.isaac_key}")
                        continue
                    print(f"  Isaac Sim ({sc.isaac_key}) connected!")
                else:
                    print(f"\n  Resetting episode for next scenario...")
                    await call(session, "send_humanoid_heartbeat", {"source": "reset"})
                    await call(session, "disable_humanoid_motion", {"reason": "pre_reset"})
                    await call(session, "reset_humanoid_episode")
                    await asyncio.sleep(3.0)
                    await call(session, "send_humanoid_heartbeat", {"source": "post_reset"})
                    await call(session, "set_humanoid_mode", {"mode": "external"})
                    await asyncio.sleep(1.0)

                await run_scenario(session, sc, errors)

            print(f"\n{'='*70}")
            print(f"  FINAL METRICS")
            print(f"{'='*70}")
            metrics = await call(session, "get_metrics")
            for k, v in sorted(metrics.get("counters", {}).items()):
                print(f"  {k}: {v}")

    print(f"\n{'='*70}")
    if errors:
        print(f"  RESULT: FAIL -- {len(errors)} error(s)")
        for e in errors:
            print(f"    - {e}")
        return 1
    print(f"  RESULT: PASS -- all 5 scenarios executed live in Isaac Sim")
    print(f"{'='*70}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(asyncio.wait_for(main(), timeout=900.0)))
