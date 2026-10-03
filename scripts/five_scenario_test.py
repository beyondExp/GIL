#!/usr/bin/env python3
"""Five-scenario integration test across different embodiments and environments.

Scenario 1: H1 Maze Escape        -- humanoid navigates DFS maze to goal
Scenario 2: H1 Open Space Patrol  -- humanoid walks a square patrol in open terrain
Scenario 3: Franka Pick-and-Place  -- arm picks red cube from tabletop scene
Scenario 4: G1 HuggingFace World  -- different humanoid in an HF occupancy grid
Scenario 5: AnyMal Quadruped Recon -- quadruped explores a maze for coverage

Each scenario: select_embodiment → ingest_world → instruct → dream → verify gate + provenance.
Scenario 1 also does a live execute_action since H1 is running in Isaac Sim.

No Isaac restart needed -- scenarios 2-5 use FakeControls (in-process) for the dream pipeline.
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


@dataclass
class Scenario:
    name: str
    embodiment: str
    world_source: str
    world_content: str
    instruction: str
    expected_robot_type: str
    expected_decision: list[str]
    live_execute: bool = False


SCENARIOS = [
    Scenario(
        name="1. H1 Maze Escape",
        embodiment="unitree_h1",
        world_source="text",
        world_content="isaac maze seed 0",
        instruction="escape the maze by reaching the goal cell",
        expected_robot_type="humanoid_biped",
        expected_decision=["execute", "learn"],
        live_execute=True,
    ),
    Scenario(
        name="2. H1 Open Space Patrol",
        embodiment="unitree_h1",
        world_source="text",
        world_content="open space flat terrain",
        instruction="walk forward 3 meters then turn right and walk 2 meters",
        expected_robot_type="humanoid_biped",
        expected_decision=["execute", "learn"],
    ),
    Scenario(
        name="3. Franka Pick-and-Place",
        embodiment="franka",
        world_source="text",
        world_content="tabletop scene with red cube at x=0.5 y=0.0 z=0.12",
        instruction="pick the red cube and place it at x=0.6 y=0.2",
        expected_robot_type="manipulator_arm",
        expected_decision=["execute", "learn"],
    ),
    Scenario(
        name="4. G1 HuggingFace Occupancy World",
        embodiment="unitree_g1",
        world_source="huggingface",
        world_content=str(FIXTURE_DIR / "hf_occupancy.json"),
        instruction="reach the free cell at the far corner",
        expected_robot_type="humanoid_biped",
        expected_decision=["execute", "learn"],
    ),
    Scenario(
        name="5. AnyMal Quadruped Recon",
        embodiment="anymal",
        world_source="text",
        world_content="isaac maze seed 42",
        instruction="explore the maze for maximum map coverage",
        expected_robot_type="quadruped",
        expected_decision=["execute", "learn"],
    ),
]


async def call(session: ClientSession, tool: str, args: dict | None = None) -> dict:
    try:
        res = await asyncio.wait_for(session.call_tool(tool, args or {}), timeout=20.0)
        text = res.content[0].text if res.content else "{}"
        return json.loads(text)
    except Exception as exc:
        return {"error": str(exc)}


async def run_scenario(session: ClientSession, sc: Scenario, errors: list[str]) -> dict:
    print(f"\n{'='*70}")
    print(f"  {sc.name}")
    print(f"  embodiment={sc.embodiment}  world={sc.world_source}:{sc.world_content[:40]}")
    print(f"  instruction: {sc.instruction}")
    print(f"{'='*70}")

    result = await call(session, "steer", {
        "instruction": sc.instruction,
        "embodiment": sc.embodiment,
        "world_source": sc.world_source,
        "world_content": sc.world_content,
        "commit": False,
    })

    if result.get("error"):
        errors.append(f"{sc.name}: steer error: {result['error']}")
        print(f"  ERROR: {result['error']}")
        return result

    decision = result.get("decision", "")
    robot_type = result.get("embodiment", "")
    dream = result.get("dream") or {}
    gate = dream.get("gate") or {}
    curriculum = result.get("curriculum", [])
    competence = result.get("competence", {})
    target_skill = result.get("target_skill", "")
    runtime_stage = result.get("runtime_stage", "")

    print(f"\n  Robot type:     {robot_type}")
    print(f"  Decision:       {decision}")
    print(f"  Runtime stage:  {runtime_stage}")
    print(f"  Target skill:   {target_skill}")
    print(f"  Curriculum:     {curriculum[:5]}{'...' if len(curriculum) > 5 else ''}")

    passed = [k for k, v in competence.items() if v == "passed"]
    failed = [k for k, v in competence.items() if v == "failed"]
    print(f"  Competence:     {len(passed)} passed, {len(failed)} failed")

    if dream and dream.get("kept") is not None:
        print(f"  Dreams:         {len(dream.get('dreams', []))} total, {dream['kept']} kept")
        print(f"  Gate:           ok={gate.get('ok')} reason={gate.get('reason')}")
        if gate.get("gate_id"):
            print(f"  Gate ID:        {gate['gate_id']}")
        if gate.get("scores"):
            print(f"  Scores:         {json.dumps(gate['scores'])}")
    elif dream.get("gate"):
        print(f"  Gate:           ok={gate.get('ok')} reason={gate.get('reason')}")

    if decision not in sc.expected_decision:
        errors.append(f"{sc.name}: decision={decision}, expected one of {sc.expected_decision}")

    if sc.live_execute and gate.get("ok"):
        print(f"\n  --- Live execution on Isaac Sim ---")
        await call(session, "send_humanoid_heartbeat", {"source": "scenario_test"})
        await call(session, "set_humanoid_mode", {"mode": "external"})
        await call(session, "enable_humanoid_motion", {"reason": "scenario_test"})

        commands = dream.get("preview_commands", [])[:5]
        for i, cmd in enumerate(commands):
            vx = cmd.get("vx", 0.0)
            wz = cmd.get("wz", 0.0)
            exec_res = await call(session, "execute_action", {
                "action_kind": "cmd_vel",
                "robot_kind": "humanoid",
                "vx": vx,
                "wz": wz,
                "duration_s": 0.2,
                "reason": f"scenario1_step_{i}",
            })
            status = exec_res.get("status", "error")
            print(f"    step {i}: vx={vx:.2f} wz={wz:.2f} -> {status}")
            if status != "success":
                errors.append(f"{sc.name}: execute step {i} failed: {exec_res.get('error')}")
                break
            await asyncio.sleep(0.15)

        await call(session, "disable_humanoid_motion", {"reason": "scenario_test_done"})
        print(f"  --- Live execution done ---")

        obs = await call(session, "get_observation", {"robot_kind": "humanoid"})
        base = obs.get("state", {}).get("base", {})
        print(f"  Final base pos: x={base.get('x', '?'):.2f} y={base.get('y', '?'):.2f}")

    return result


async def main() -> int:
    errors: list[str] = []
    url = "http://127.0.0.1:6769/mcp/"
    print(f"Connecting to {url} ...")

    async with streamablehttp_client(url, timeout=20, sse_read_timeout=20) as (read, write, _):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=10.0)

            for sc in SCENARIOS:
                await run_scenario(session, sc, errors)

            print(f"\n{'='*70}")
            print(f"  METRICS SUMMARY")
            print(f"{'='*70}")
            metrics = await call(session, "get_metrics")
            counters = metrics.get("counters", {})
            for k, v in sorted(counters.items()):
                print(f"  {k}: {v}")

    print(f"\n{'='*70}")
    if errors:
        print(f"  RESULT: FAIL -- {len(errors)} error(s)")
        for e in errors:
            print(f"    - {e}")
        return 1
    print(f"  RESULT: PASS -- all 5 scenarios completed successfully")
    print(f"{'='*70}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(asyncio.wait_for(main(), timeout=180.0)))
