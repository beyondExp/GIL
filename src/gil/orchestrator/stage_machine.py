from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from gil.learn.curriculum import RobotType, curriculum_for
from gil.learn.eval_harness import maze_success
from gil.orchestrator.gate import GateDecision
from gil.world.dream import Rollout


RuntimeStage = Literal[
    "A0",
    "A1",
    "A2",
    "A3",
    "A4",
    "A5",
    "A6",
    "L1",
    "L2",
    "L3",
    "L4",
    "L5",
    "E1",
    "E2",
    "E3",
    "E4",
]

Decision = Literal["ready", "blocked", "learn", "unsafe", "execute"]
CompetenceStatus = Literal["passed", "failed", "unknown"]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class StageSnapshot:
    runtime_stage: RuntimeStage
    target_skill: str
    first_gap: str | None
    decision: Decision
    checks: list[Check] = field(default_factory=list)
    competence: dict[str, str] = field(default_factory=dict)
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "runtime_stage": self.runtime_stage,
            "target_skill": self.target_skill,
            "first_gap": self.first_gap,
            "decision": self.decision,
            "reason": self.reason,
            "competence": dict(self.competence),
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks],
        }


class CompetenceLedger:
    """robot_id + env_key + stage_id -> passed|failed|unknown."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str], CompetenceStatus] = {}

    def get(self, robot_id: str, env_key: str, stage_id: str) -> CompetenceStatus:
        return self._rows.get((robot_id, env_key, stage_id), "unknown")

    def set(self, robot_id: str, env_key: str, stage_id: str, status: CompetenceStatus) -> None:
        self._rows[(robot_id, env_key, stage_id)] = status

    def dump(self, robot_id: str, env_key: str) -> dict[str, str]:
        return {sid: st for (rid, env, sid), st in self._rows.items() if rid == robot_id and env == env_key}


_PHYSICAL_SKIP_IF_NOT_TARGET = {
    "language_grounding",
    "instruction_following_skills",
    "instruction_following_natural_env",
}


def instruction_to_skill(instruction: str, robot_type: RobotType = "humanoid_biped") -> str:
    text = (instruction or "").strip().lower()
    if any(w in text for w in ("stand", "idle", "balance")) and "walk" not in text:
        return "stand_idle"
    if "stop" in text or text.strip() in {"halt"}:
        return "stop_and_hold"
    if "turn" in text:
        return "locomotion_turning"
    if "maze" in text or "escape" in text:
        return "maze_escape"
    if "forward" in text and "walk" in text:
        return "locomotion_forward"
    if any(w in text for w in ("go to", "walk to", "reach", "over there", "corner", "goal")):
        return "local_goal_reaching"
    if robot_type in ("manipulator_arm", "mobile_manipulator") and any(w in text for w in ("pick", "grasp", "place")):
        return "pick_place" if "place" in text else "grasp"
    if robot_type == "humanoid_biped":
        return "local_goal_reaching" if text else "maze_escape"
    return "local_goal_reaching"


def first_gap(*, robot_type: RobotType, target_skill: str, ledger: CompetenceLedger, robot_id: str, env_key: str) -> str | None:
    ids = [s.id for s in curriculum_for(robot_type)]
    if target_skill not in ids:
        return target_skill
    chain: list[str] = []
    for sid in ids:
        chain.append(sid)
        if sid == target_skill:
            break
    for sid in chain:
        if sid in _PHYSICAL_SKIP_IF_NOT_TARGET and sid != target_skill:
            continue
        if ledger.get(robot_id, env_key, sid) != "passed":
            return sid
    return None


def evaluate_rollouts(skill_id: str, rollouts: list[Rollout], goal: dict[str, Any] | None = None) -> dict[str, Any]:
    n = len(rollouts)
    if n == 0:
        return {"ok": False, "n": 0, "success_rate": 0.0, "reason": "no_rollouts"}
    physics = sum(1 for r in rollouts if r.physics_ok)
    successes = sum(1 for r in rollouts if r.success and r.physics_ok)
    rate = successes / float(n)
    if skill_id in ("stand_idle", "safety_do_no_harm", "proprioception_calibration"):
        ok = physics / float(n) >= 0.5
    elif skill_id == "locomotion_forward":
        ok = physics / float(n) >= 0.5 and successes >= 1
    elif skill_id in ("local_goal_reaching", "maze_escape"):
        if goal:
            extra = 0
            for r in rollouts:
                pose = (r.extra or {}).get("pose") if hasattr(r, "extra") else None
                if isinstance(pose, dict) and maze_success(pose, goal, float(goal.get("radius") or 0.75)):
                    extra += 1
            if extra:
                rate = extra / float(n)
        ok = rate >= 0.25 and physics >= 1
    else:
        ok = rate >= 0.25
    return {"ok": ok, "n": n, "success_rate": rate, "physics_ok": physics, "successes": successes}


def probe_readiness(
    *,
    has_embodiment: bool,
    has_world: bool,
    has_instruction: bool,
    preflight_ok: bool,
    observation: dict[str, Any] | None,
    live_facts: dict[str, Any] | None = None,
    require_live_image: bool = False,
    min_upright_z: float = 0.55,
) -> tuple[RuntimeStage, Decision, list[Check], str]:
    facts = live_facts or {}
    checks: list[Check] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append(Check(name, ok, detail))

    if facts.get("estop"):
        add("estop", False, "e-stop active")
        return "A4", "unsafe", checks, "estop"
    add("A0_intent", has_instruction, "instruction/goal")
    if not has_instruction:
        return "A0", "blocked", checks, "no_instruction"
    add("A1_embodiment", has_embodiment)
    if not has_embodiment:
        return "A1", "blocked", checks, "no_embodiment"
    add("A2_world", has_world)
    if not has_world:
        return "A2", "blocked", checks, "no_world"

    connected = facts.get("connected")
    if connected is False:
        add("A4_connected", False)
        return "A4", "blocked", checks, "backend_disconnected"
    add("A4_preflight", preflight_ok)
    if not preflight_ok:
        return "A4", "blocked", checks, "preflight_failed"
    hb = facts.get("heartbeat_ok")
    if hb is False:
        add("A4_heartbeat", False)
        return "A4", "blocked", checks, "heartbeat_timeout"
    add("A4_heartbeat", True if hb is None else bool(hb))

    obs = observation or {}
    base = facts.get("base") if isinstance(facts.get("base"), dict) else obs.get("base") or {}
    has_pose = isinstance(base, dict) and ("x" in base or "z" in base)
    add("A3_pose", has_pose)
    if not has_pose:
        return "A3", "blocked", checks, "no_pose"
    if require_live_image:
        has_image = bool(facts.get("has_image"))
        add("A3_image", has_image)
        if not has_image:
            return "A3", "blocked", checks, "no_camera"
    z = float(base.get("z") or 1.0)
    upright = z >= min_upright_z or z == 0.0
    add("A5_stance", upright, f"z={z}")
    if not upright:
        return "A5", "blocked", checks, "fallen"
    add("A6_imagine", True)
    return "A6", "ready", checks, "imagine_allowed"


def decide_after_competence(
    *,
    a_decision: Decision,
    a_stage: RuntimeStage,
    a_reason: str,
    checks: list[Check],
    target_skill: str,
    gap: str | None,
    gate: GateDecision | None,
) -> StageSnapshot:
    if a_decision in ("blocked", "unsafe"):
        return StageSnapshot(a_stage, target_skill, gap, a_decision, checks, reason=a_reason)
    if gap and gap != target_skill:
        return StageSnapshot("L1", target_skill, gap, "learn", checks, reason=f"first_gap={gap}")
    if gap == target_skill:
        return StageSnapshot("L2", target_skill, gap, "learn", checks, reason=f"skill_unproven={gap}")
    if gate is not None and not gate.ok:
        return StageSnapshot("A6", target_skill, None, "blocked", checks, reason=gate.reason)
    return StageSnapshot("E3", target_skill, None, "execute", checks, reason="competence_and_gate")
