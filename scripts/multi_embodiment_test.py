#!/usr/bin/env python3
"""Multi-embodiment test.

Switches between H1, G1, and Franka in the same AgentDirector session.
Verifies world model selection, curriculum, and morphology-agnostic path.

No Isaac Sim required.  Exit 0 = pass, exit 1 = fail.
"""
from __future__ import annotations

import sys

from gil.orchestrator.director import AgentDirector
from gil.orchestrator.stage_machine import CompetenceLedger
from gil.world.kinematic3d import Kinematic3DWorldModel
from gil.world.scene_graph import SceneGraphWorldModel


EMBODIMENTS = [
    {
        "key": "unitree_h1",
        "expected_robot_type": "humanoid_biped",
        "expected_world_model": Kinematic3DWorldModel,
        "instruction": "escape the maze",
        "world_source": "text",
        "world_content": "maze seed 0",
    },
    {
        "key": "unitree_g1",
        "expected_robot_type": "humanoid_biped",
        "expected_world_model": Kinematic3DWorldModel,
        "instruction": "walk to the exit",
        "world_source": "text",
        "world_content": "maze seed 1",
    },
    {
        "key": "franka",
        "expected_robot_type": "manipulator_arm",
        "expected_world_model": SceneGraphWorldModel,
        "instruction": "pick the red cube",
        "world_source": "text",
        "world_content": "tabletop scene",
    },
]


def main() -> int:
    errors: list[str] = []
    ledger = CompetenceLedger()
    director = AgentDirector(ledger=ledger)

    for spec in EMBODIMENTS:
        key = spec["key"]
        print(f"\n{'='*60}")
        print(f"=== Embodiment: {key} ===")
        print(f"{'='*60}")

        pick = director.select_embodiment(key)
        if not pick.get("ok"):
            errors.append(f"{key}: select_embodiment failed")
            continue

        actual_type = pick["embodiment"]["robot_type"]
        expected_type = spec["expected_robot_type"]
        print(f"  robot_type: {actual_type} (expected {expected_type})")
        if actual_type != expected_type:
            errors.append(f"{key}: robot_type={actual_type}, expected {expected_type}")

        ingest = director.ingest_world(spec["world_source"], spec["world_content"])
        print(f"  world: generator={ingest['generator']} live={ingest['live']}")

        wm = director.orch.world_model
        expected_wm = spec["expected_world_model"]
        print(f"  world_model: {type(wm).__name__} (expected {expected_wm.__name__})")
        if not isinstance(wm, expected_wm):
            errors.append(f"{key}: world_model is {type(wm).__name__}, expected {expected_wm.__name__}")

        curriculum = pick["embodiment"].get("curriculum", [])
        print(f"  curriculum: {curriculum}")
        if not curriculum:
            errors.append(f"{key}: curriculum is empty")

        result = director.instruct(spec["instruction"])
        print(f"  instruct: ok={result.get('ok')}")

        snap = director.snapshot()
        print(f"  snapshot: stage={snap.runtime_stage} decision={snap.decision}")

        dream = director.dream(n=4)
        print(f"  dream: kept={dream.get('kept')} gate.ok={dream['gate']['ok']}")
        gate_id = dream["gate"].get("gate_id", "")
        if dream["gate"]["ok"] and not gate_id:
            errors.append(f"{key}: gate passed but no gate_id")

    print(f"\n{'='*60}")
    print(f"=== Session state after all switches ===")
    print(f"  final robot_id: {director.robot_id}")
    print(f"  final embodiment: {director.embodiment.key if director.embodiment else 'None'}")
    print(f"  sessions in store: {list(director.orch.sessions._robots.keys())}")
    print(f"  maps in store: {list(director.orch.maps.keys())}")

    from gil.core.metrics import METRICS
    snap = METRICS.snapshot()
    selected = snap["counters"].get("embodiment_selected", 0)
    print(f"  embodiment_selected count: {selected}")
    if selected < len(EMBODIMENTS):
        errors.append(f"embodiment_selected={selected}, expected >= {len(EMBODIMENTS)}")

    print()
    if errors:
        print(f"FAIL -- {len(errors)} error(s):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("PASS -- all embodiments switched successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
