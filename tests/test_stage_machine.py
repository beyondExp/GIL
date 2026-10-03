from __future__ import annotations

from gil.learn.curriculum import curriculum_for
from gil.orchestrator.gate import GateDecision
from gil.orchestrator.stage_machine import (
    CompetenceLedger,
    decide_after_competence,
    evaluate_rollouts,
    first_gap,
    instruction_to_skill,
    probe_readiness,
)
from gil.world.dream import Rollout


def test_instruction_maps_to_catalog_skills() -> None:
    assert instruction_to_skill("escape the maze") == "maze_escape"
    assert instruction_to_skill("walk to the far corner") == "local_goal_reaching"
    assert instruction_to_skill("walk forward") == "locomotion_forward"
    assert instruction_to_skill("just stand") == "stand_idle"
    ids = {s.id for s in curriculum_for("humanoid_biped")}
    assert instruction_to_skill("go to the exit") in ids


def test_first_gap_never_maze_before_walk() -> None:
    ledger = CompetenceLedger()
    gap = first_gap(
        robot_type="humanoid_biped",
        target_skill="maze_escape",
        ledger=ledger,
        robot_id="h1",
        env_key="maze0",
    )
    assert gap == "safety_do_no_harm"
    for sid in (
        "safety_do_no_harm",
        "proprioception_calibration",
        "stand_idle",
        "balance_recovery",
        "stand_up_from_fall",
        "locomotion_forward",
        "stop_and_hold",
        "locomotion_turning",
        "collision_avoidance",
        "local_goal_reaching",
    ):
        ledger.set("h1", "maze0", sid, "passed")
    assert (
        first_gap(
            robot_type="humanoid_biped",
            target_skill="maze_escape",
            ledger=ledger,
            robot_id="h1",
            env_key="maze0",
        )
        == "maze_escape"
    )


def test_ledger_roundtrip() -> None:
    ledger = CompetenceLedger()
    assert ledger.get("h1", "e", "stand_idle") == "unknown"
    ledger.set("h1", "e", "stand_idle", "passed")
    assert ledger.get("h1", "e", "stand_idle") == "passed"
    assert ledger.dump("h1", "e")["stand_idle"] == "passed"


def test_probe_blocks_without_embodiment() -> None:
    stage, decision, checks, reason = probe_readiness(
        has_embodiment=False,
        has_world=True,
        has_instruction=True,
        preflight_ok=True,
        observation={"base": {"x": 0.0, "y": 0.0, "z": 0.9}},
    )
    assert stage == "A1"
    assert decision == "blocked"
    assert reason == "no_embodiment"
    assert any(not c.ok for c in checks)


def test_probe_blocks_fallen() -> None:
    stage, decision, _checks, reason = probe_readiness(
        has_embodiment=True,
        has_world=True,
        has_instruction=True,
        preflight_ok=True,
        observation={"base": {"x": 0.0, "y": 0.0, "z": 0.2}},
    )
    assert stage == "A5"
    assert decision == "blocked"
    assert reason == "fallen"


def test_probe_ready_for_imagine() -> None:
    stage, decision, _checks, reason = probe_readiness(
        has_embodiment=True,
        has_world=True,
        has_instruction=True,
        preflight_ok=True,
        observation={"base": {"x": 0.0, "y": 0.0, "z": 0.9}},
    )
    assert stage == "A6"
    assert decision == "ready"
    assert reason == "imagine_allowed"


def test_evaluate_local_goal_from_dreams() -> None:
    good = [Rollout(success=True, critic_score=0.9, physics_ok=True) for _ in range(4)]
    bad = [Rollout(success=False, critic_score=0.1, physics_ok=False) for _ in range(4)]
    assert evaluate_rollouts("local_goal_reaching", good)["ok"] is True
    assert evaluate_rollouts("local_goal_reaching", bad)["ok"] is False


def test_decide_learn_on_first_gap() -> None:
    snap = decide_after_competence(
        a_decision="ready",
        a_stage="A6",
        a_reason="imagine_allowed",
        checks=[],
        target_skill="maze_escape",
        gap="locomotion_forward",
        gate=GateDecision(True, "pass"),
    )
    assert snap.decision == "learn"
    assert snap.runtime_stage == "L1"
    assert snap.first_gap == "locomotion_forward"
