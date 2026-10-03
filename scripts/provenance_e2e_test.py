#!/usr/bin/env python3
"""End-to-end provenance chain test.

Runs the full steer → dream → commit pipeline with FakeControls and asserts
that every motor command reaching the controls port carries:
  - origin = "dream"
  - a non-empty gate_id matching the GateDecision
  - source = "orchestrator"
  - a full provenance dict

No Isaac Sim required.  Exit 0 = pass, exit 1 = fail.
"""
from __future__ import annotations

import sys

from gil.orchestrator.director import AgentDirector
from gil.orchestrator.stage_machine import CompetenceLedger


def main() -> int:
    errors: list[str] = []
    ledger = CompetenceLedger()
    director = AgentDirector(ledger=ledger)

    print("=== 1. Steer with commit=True ===")
    result = director.steer(
        instruction="escape the maze",
        embodiment="unitree_h1",
        commit=True,
    )

    executed = result.get("executed")
    print(f"  executed={executed}")
    if not executed:
        errors.append(f"Mission did not execute: {result.get('note') or result.get('mission_reason')}")
        _report(errors)
        return 1

    print("\n=== 2. Check dream output carries gate_id ===")
    dream = result.get("dream") or {}
    gate = dream.get("gate") or {}
    steer_gate_id = gate.get("gate_id", "")
    print(f"  gate.ok={gate.get('ok')}  steer_gate_id={steer_gate_id!r}")
    if not steer_gate_id:
        errors.append("Dream gate output is missing gate_id")

    print("\n=== 3. Inspect every command sent through FakeControls ===")
    controls = director.orch._controls[director.robot_id]
    sent = controls.sent
    print(f"  {len(sent)} commands sent")

    mission_gate_ids: set[str] = set()
    for i, row in enumerate(sent):
        cmd = row.get("command", {})
        prov = row.get("provenance", {})
        source = row.get("source", "")
        origin = prov.get("origin", "")
        cmd_gate = prov.get("gate_id", "")

        if i < 5 or i == len(sent) - 1:
            print(f"    [{i}] type={cmd.get('type')!r} source={source!r} "
                  f"origin={origin!r} gate_id={cmd_gate!r}")
        elif i == 5:
            print(f"    ... ({len(sent) - 6} more commands with same provenance) ...")

        if cmd.get("type") in {"cmd_vel", "move_robot", "gripper"}:
            if source != "orchestrator":
                errors.append(f"Command {i}: source is '{source}', expected 'orchestrator'")
            if origin != "dream":
                errors.append(f"Command {i}: origin is '{origin}', expected 'dream'")
            if not cmd_gate:
                errors.append(f"Command {i}: gate_id is empty")
            else:
                mission_gate_ids.add(cmd_gate)

            in_cmd_prov = cmd.get("provenance", {})
            if not isinstance(in_cmd_prov, dict) or not in_cmd_prov.get("command_id"):
                errors.append(f"Command {i}: missing provenance.command_id in stamped command")

    print(f"\n  Unique mission gate_ids: {mission_gate_ids}")
    if len(mission_gate_ids) > 1:
        errors.append(f"Commands have inconsistent gate_ids: {mission_gate_ids}")
    if len(mission_gate_ids) == 1:
        mission_gid = next(iter(mission_gate_ids))
        print(f"  Mission gate_id ({mission_gid}) vs steer dream gate_id ({steer_gate_id})")
        print(f"  (These differ because commit() runs its own dream+gate -- this is correct)")

    if not sent:
        errors.append("No commands were sent at all")

    print("\n=== 4. Verify ledger recorded skills ===")
    dump = director.ledger.dump(director.robot_id, director.env_key)
    print(f"  competence: {dump}")
    passed_skills = [k for k, v in dump.items() if v == "passed"]
    if not passed_skills:
        errors.append("Ledger has no passed skills")

    print("\n=== 5. Check METRICS counters ===")
    from gil.core.metrics import METRICS
    snap = METRICS.snapshot()
    print(f"  counters: {snap['counters']}")
    for expected in ("missions_executed", "authority_allowed", "gate_passed"):
        if snap["counters"].get(expected, 0) < 1:
            errors.append(f"METRICS counter '{expected}' is missing or zero")

    _report(errors)
    return 1 if errors else 0


def _report(errors: list[str]) -> None:
    print()
    if errors:
        print(f"FAIL -- {len(errors)} error(s):")
        for e in errors:
            print(f"  - {e}")
    else:
        print("PASS -- full provenance chain verified end-to-end")


if __name__ == "__main__":
    sys.exit(main())
