from __future__ import annotations

import os
from typing import Any

from gil.core.logging_setup import get_logger
from gil.core.metrics import METRICS
from gil.core.types import Goal
from gil.hardware.isaac_robots import IsaacRobot, get_isaac_robot, launch_spec, list_isaac_robots
from gil.learn.curriculum import curriculum_for
from gil.learn.trainer import ImaginationTrainer, SkillTrainer
from gil.orchestrator.gate import GateDecision
from gil.orchestrator.mission import MissionResult, _imagination_success
from gil.orchestrator.perception import PerceptionClient
from gil.orchestrator.service import Orchestrator
from gil.orchestrator.stage_machine import (
    CompetenceLedger,
    StageSnapshot,
    decide_after_competence,
    evaluate_rollouts,
    first_gap,
    instruction_to_skill,
    probe_readiness,
)
from gil.world.factory import world_model_for
from gil.world.ingest import WorldSpec, ingest_world
from gil.world.kinematic3d import Kinematic3DWorldModel
from gil.world.maze3d import generate_maze

log = get_logger("orchestrator.director")

_BOOTSTRAP_SKILLS = (
    "safety_do_no_harm",
    "proprioception_calibration",
    "stand_idle",
    "balance_recovery",
    "stand_up_from_fall",
    "locomotion_forward",
    "stop_and_hold",
    "locomotion_turning",
    "collision_avoidance",
)


def _make_ledger() -> CompetenceLedger:
    path = os.getenv("GIL_COMPETENCE_PATH", "").strip()
    if path == ":memory:":
        return CompetenceLedger()
    try:
        from gil.memory.competence import FileCompetenceLedger
        return FileCompetenceLedger(path or None)
    except Exception:
        return CompetenceLedger()


class AgentDirector:
    """The process the AI agent steers.

    Ladder A (readiness) then B (competence) / L (learn) then E (execute).
    World models never send cmd_vel.
    """

    def __init__(
        self,
        orchestrator: Orchestrator | None = None,
        *,
        require_live_image: bool = False,
        trainer: SkillTrainer | None = None,
        perception: PerceptionClient | None = None,
        ledger: CompetenceLedger | None = None,
    ):
        maze = generate_maze()
        self.orch = orchestrator or Orchestrator(world_model=Kinematic3DWorldModel(maze=maze))
        self.embodiment: IsaacRobot | None = None
        self.world: WorldSpec | None = None
        self.robot_id = "h1"
        self.env_key = "maze0"
        self.last_dreams: list[Any] = []
        self.last_kept: list[Any] = []
        self.last_gate: GateDecision | None = None
        self.last_snapshot: StageSnapshot | None = None
        self.ledger = ledger or _make_ledger()
        self.require_live_image = require_live_image
        self.learn_budget = 3
        self.trainer: SkillTrainer = trainer or ImaginationTrainer()
        models_url = os.getenv("GIL_MODELS_URL", "").strip()
        self.perception = perception or PerceptionClient(base_url=models_url)
        log.info("AgentDirector initialized (ledger=%s)", type(self.ledger).__name__)

    def list_embodiments(self) -> dict[str, Any]:
        from gil.learn.curriculum import curriculum_spec

        return {
            "ok": True,
            "robots": list_isaac_robots(),
            "execute": "isaac_sim_workstation",
            "viewport": "native_isaac_sim",
            "curriculum": curriculum_spec(),
            "note": "Spawn via scripts/launch_isaac_robot.ps1 (or run_gil_workstation.ps1). set_embodiment does not load factory sensors.",
        }

    def select_embodiment(self, key: str, robot_id: str | None = None, token: str | None = None) -> dict[str, Any]:
        robot = get_isaac_robot(key)
        self.embodiment = robot
        self.robot_id = robot_id or robot.key.replace("unitree_", "")
        attached = self.orch.connect_robot(robot.profile_id, robot_id=self.robot_id, token=token)
        log.info("Embodiment selected: %s (type=%s)", robot.key, robot.robot_type)
        METRICS.inc("embodiment_selected")
        return {
            "ok": True,
            "embodiment": {
                "key": robot.key,
                "usd_rel": robot.usd_rel,
                "profile_id": robot.profile_id,
                "kind": robot.kind,
                "locomotion": robot.locomotion,
                "lab_task": robot.lab_task,
                "pretrained_task": robot.pretrained_task,
                "robot_type": robot.robot_type,
                "curriculum": [s.id for s in curriculum_for(robot.robot_type)],
            },
            "session": attached,
            "isaac_command": {"type": "set_embodiment", "usd_rel": robot.usd_rel, "variant": robot.key, "key": robot.key},
            "launch": launch_spec(robot),
            "note": "Factory sensors/policy come from a process relaunch (launch_isaac_robot.ps1), not set_embodiment on a live stage.",
        }

    def ingest_world(self, source: str, content: str) -> dict[str, Any]:
        spec = ingest_world(source, content)
        self.world = spec
        self.env_key = f"{spec.generator}:{spec.live}"
        robot_type = self._robot_type()
        self.orch.world_model = world_model_for(spec, robot_type=robot_type)
        if self.robot_id in self.orch.maps:
            self.orch.maps[self.robot_id].ingest(spec.observation)
        log.info("World ingested: generator=%s robot_type=%s", spec.generator, robot_type)
        return {
            "ok": True,
            "generator": spec.generator,
            "live": spec.live,
            "note": spec.note,
            "isaac_command": spec.isaac_command,
        }

    def instruct(self, instruction: str, x: float | None = None, y: float | None = None, radius: float = 0.45) -> dict[str, Any]:
        self._ensure_session()
        maze = self._maze()
        goal = {
            "language": instruction,
            "x": maze.goal[0] if x is None else x,
            "y": maze.goal[1] if y is None else y,
            "radius": radius,
        }
        return self.orch.set_goal(self.robot_id, goal)

    def _robot_type(self):
        if self.embodiment:
            return self.embodiment.robot_type
        return "humanoid_biped"

    def _target_skill(self, instruction: str | None = None) -> str:
        session = None
        try:
            session = self.orch.sessions.get(self.robot_id)
        except KeyError:
            pass
        text = instruction or ""
        if session and session.goal:
            text = text or str((session.goal or {}).get("language") or "")
        return instruction_to_skill(text or "escape the maze", self._robot_type())

    def _observation(self, observation: dict[str, Any] | None = None) -> dict[str, Any]:
        session = None
        try:
            session = self.orch.sessions.get(self.robot_id)
        except KeyError:
            pass
        raw = observation or (session.last_observation if session else None) or (
            self.world.observation if self.world else self._maze().to_observation()
        )
        return self.perception.enrich(raw)

    def _preflight_ok(self) -> bool:
        try:
            return bool(self.orch._controls[self.robot_id].preflight(self.robot_id).get("ok"))
        except KeyError:
            return False

    def snapshot(self, observation: dict[str, Any] | None = None, live_facts: dict[str, Any] | None = None) -> StageSnapshot:
        session_goal = False
        try:
            session_goal = bool(self.orch.sessions.get(self.robot_id).goal)
        except KeyError:
            pass
        req_img = self.require_live_image or bool((live_facts or {}).get("connected"))
        a_stage, a_decision, checks, a_reason = probe_readiness(
            has_embodiment=self.embodiment is not None,
            has_world=self.world is not None,
            has_instruction=session_goal,
            preflight_ok=self._preflight_ok(),
            observation=self._observation(observation),
            live_facts=live_facts,
            require_live_image=req_img,
        )
        skill = self._target_skill()
        gap = first_gap(
            robot_type=self._robot_type(),
            target_skill=skill,
            ledger=self.ledger,
            robot_id=self.robot_id,
            env_key=self.env_key,
        )
        snap = decide_after_competence(
            a_decision=a_decision,
            a_stage=a_stage,
            a_reason=a_reason,
            checks=checks,
            target_skill=skill,
            gap=gap,
            gate=self.last_gate if a_decision == "ready" else None,
        )
        snap.competence = self.ledger.dump(self.robot_id, self.env_key)
        self.last_snapshot = snap
        return snap

    def evaluate_skill(self, skill_id: str | None = None, observation: dict[str, Any] | None = None) -> dict[str, Any]:
        skill = skill_id or self._target_skill()
        dreamed = self.dream(observation)
        report = evaluate_rollouts(skill, self.last_dreams, goal=self.orch.sessions.get(self.robot_id).goal)
        status = "passed" if report["ok"] else "failed"
        self.ledger.set(self.robot_id, self.env_key, skill, status)
        METRICS.inc(f"skill_evaluated.{status}")
        return {"ok": report["ok"], "skill": skill, "status": status, "report": report, "dream": dreamed}

    def learn_skill(self, skill_id: str | None = None, observation: dict[str, Any] | None = None, live_probe: bool = False) -> dict[str, Any]:
        skill = skill_id or self._target_skill()
        gap = first_gap(
            robot_type=self._robot_type(),
            target_skill=skill,
            ledger=self.ledger,
            robot_id=self.robot_id,
            env_key=self.env_key,
        )
        focus = gap or skill
        rounds: list[dict[str, Any]] = []
        goal = {}
        try:
            goal = self.orch.sessions.get(self.robot_id).goal or {}
        except KeyError:
            pass
        for i in range(self.learn_budget):
            dreamed = self.dream(observation)
            train_report = self.trainer.train(
                focus, rollouts=self.last_dreams, observation=self._observation(observation), goal=goal
            )
            rounds.append({
                "round": i + 1,
                "skill": focus,
                "gate": dreamed["gate"],
                "train": {"ok": train_report.ok, "method": train_report.method},
            })
            if dreamed["gate"]["ok"] and train_report.ok:
                self.ledger.set(self.robot_id, self.env_key, focus, "passed")
                METRICS.inc("skill_learned")
                log.info("Skill learned: %s (method=%s)", focus, train_report.method)
                return {
                    "ok": True,
                    "runtime_stage": "L5",
                    "learned": focus,
                    "rounds": rounds,
                    "live_probe": live_probe,
                    "note": f"Skill passed via {train_report.method}. Live probe is optional and still gated.",
                }
        self.ledger.set(self.robot_id, self.env_key, focus, "failed")
        METRICS.inc("skill_learn_failed")
        return {"ok": False, "runtime_stage": "L3", "learned": focus, "rounds": rounds, "note": "Learn budget exhausted."}

    def _bootstrap_locomotion_if_policy(self) -> None:
        if not self.embodiment or self.embodiment.locomotion != "h1_policy":
            return
        for sid in _BOOTSTRAP_SKILLS:
            if self.ledger.get(self.robot_id, self.env_key, sid) == "unknown":
                self.ledger.set(self.robot_id, self.env_key, sid, "passed")

    def dream(self, observation: dict[str, Any] | None = None, n: int = 8) -> dict[str, Any]:
        session = self._ensure_session()
        if not session.goal:
            self.instruct(str((self.world.content if self.world else "") or "escape the maze"))
            session = self.orch.sessions.get(self.robot_id)
        obs = self._observation(observation)
        session.last_observation = obs
        scene = self.orch.maps[self.robot_id].ingest(obs)
        goal = Goal.from_dict(session.goal or {})
        from gil.world.dream_engine import dream_rollouts

        dreams = dream_rollouts(world_model=self.orch.world_model, observation=obs, goal=goal, n=n)
        kept = self.orch.critic.filter(dreams, goal)
        pre = self.orch._controls[self.robot_id].preflight(self.robot_id)
        decision = self.orch.gate.evaluate(
            imagination_success=_imagination_success(kept),
            critic_score=max((r.critic_score for r in kept), default=0.0),
            map_coverage=scene.coverage,
            map_calibrated=scene.calibrated,
            preflight_ok=bool(pre.get("ok")),
        )
        self.last_dreams = dreams
        self.last_kept = kept
        self.last_gate = decision
        METRICS.inc("dreams_total")
        if decision.ok:
            METRICS.inc("gate_passed")
        else:
            METRICS.inc("gate_blocked")
        summaries = [
            {
                "success": r.success,
                "physics_ok": r.physics_ok,
                "score": r.critic_score,
                "steps": len(r.commands),
                "note": r.note,
            }
            for r in dreams
        ]
        return {
            "ok": True,
            "viewport": "isaac_sim",
            "imagine": "isaac_scene_copy",
            "dreams": summaries,
            "kept": len(kept),
            "gate": {"ok": decision.ok, "reason": decision.reason, "scores": decision.scores, "gate_id": decision.gate_id},
            "preview_commands": list(kept[0].commands) if kept else [],
            "note": "Replay preview_commands as preview_vel in Isaac to watch the dream, then reset_episode. Commit only if gate.ok.",
        }

    def preview_plan(self) -> dict[str, Any]:
        if not self.last_kept:
            return {"ok": False, "error": "No kept dream. Call dream() first."}
        commands = [
            {
                "type": "preview_vel",
                "vx": c.get("vx", 0.0),
                "vy": c.get("vy", 0.0),
                "wz": c.get("wz", 0.0),
                "dt": c.get("dt", 0.2),
            }
            for c in self.last_kept[0].commands
        ]
        return {
            "ok": True,
            "phase": "E2",
            "commands": commands,
            "reset_after": {"type": "reset_episode"},
            "note": "These are previews. They are not a gated execute.",
        }

    def commit(self, observation: dict[str, Any] | None = None, token: str | None = None, n_dreams: int = 8) -> MissionResult:
        session = self._ensure_session()
        obs = observation or session.last_observation or (self.world.observation if self.world else self._maze().to_observation())
        return self.orch.run_mission(self.robot_id, obs, token=token, n_dreams=n_dreams)

    def steer(
        self,
        observation: dict[str, Any] | None = None,
        *,
        instruction: str | None = None,
        embodiment: str | None = None,
        world_source: str = "text",
        world_content: str = "",
        commit: bool = False,
        token: str | None = None,
        live_facts: dict[str, Any] | None = None,
        learn: bool = True,
    ) -> dict[str, Any]:
        log_steps: list[str] = []
        if embodiment or not self.embodiment:
            chosen = embodiment or "unitree_h1"
            self.select_embodiment(chosen, token=token)
            log_steps.append(f"embodiment={chosen}")
        if world_content or not self.world:
            spec = self.ingest_world(world_source, world_content or instruction or "isaac maze seed 0")
            log_steps.append(f"world={spec['generator']} live={spec['live']}")
        if instruction:
            self.instruct(instruction)
            log_steps.append("instructed")
        elif not self.orch.sessions.get(self.robot_id).goal:
            self.instruct("escape the maze")
            log_steps.append("default_instruction")

        self._bootstrap_locomotion_if_policy()
        snap = self.snapshot(observation, live_facts=live_facts)
        log_steps.append(f"A={snap.runtime_stage}:{snap.decision}:{snap.reason}")
        result: dict[str, Any] = {
            "ok": snap.decision != "unsafe",
            "decided": log_steps,
            "embodiment": None if not self.embodiment else self.embodiment.key,
            "lab_task": None if not self.embodiment else self.embodiment.lab_task,
            "curriculum": [] if not self.embodiment else [s.id for s in curriculum_for(self.embodiment.robot_type)],
            "world": None if not self.world else {"generator": self.world.generator, "live": self.world.live},
            "runtime_stage": snap.runtime_stage,
            "target_skill": snap.target_skill,
            "first_gap": snap.first_gap,
            "competence": snap.competence,
            "decision": snap.decision,
            "dream": None,
            "executed": False,
        }
        if snap.decision in ("blocked", "unsafe"):
            result["note"] = f"Agent refused: {snap.reason}"
            result["stage"] = snap.as_dict()
            METRICS.inc("steer_refused")
            return result

        learned_all: list[dict[str, Any]] = []
        learn_hops = 0
        while snap.decision == "learn" and learn and learn_hops < 8:
            learned = self.learn_skill(snap.first_gap or snap.target_skill, observation)
            learned_all.append(learned)
            log_steps.append(f"L={learned.get('learned')} ok={learned.get('ok')}")
            learn_hops += 1
            if not learned.get("ok"):
                break
            snap = self.snapshot(observation, live_facts=live_facts)
            result["runtime_stage"] = snap.runtime_stage
            result["decision"] = snap.decision
            result["first_gap"] = snap.first_gap
            result["competence"] = snap.competence
        if learned_all:
            result["learn"] = learned_all[-1]
            result["learn_hops"] = learned_all
        if snap.decision == "learn":
            result["note"] = "Still learning; will not execute."
            result["dream"] = {"gate": {"ok": False, "reason": "learn_incomplete"}}
            return result

        dreamed = self.dream(observation)
        log_steps.append(f"gate={dreamed['gate']['reason']}")
        result["dream"] = dreamed
        eval_target = self.evaluate_rollouts_current()
        if eval_target["ok"]:
            self.ledger.set(self.robot_id, self.env_key, snap.target_skill, "passed")
        result["competence"] = self.ledger.dump(self.robot_id, self.env_key)

        if commit:
            if snap.decision != "execute" and not eval_target["ok"]:
                result["note"] = "Agent refused to execute: skill not competent."
                return result
            if not dreamed["gate"]["ok"]:
                result["note"] = "Agent refused to execute: gate blocked."
                return result
            mission = self.commit(observation, token=token)
            result["executed"] = mission.executed
            result["mission_reason"] = mission.reason
            result["runtime_stage"] = "E3" if mission.executed else "A6"
            METRICS.inc("steer_executed" if mission.executed else "steer_commit_failed")
        else:
            result["note"] = "Dream ready. Preview in Isaac, then steer(..., commit=True) to execute."
        result["stage"] = (self.last_snapshot or snap).as_dict()
        log.info("steer complete: decision=%s executed=%s", result["decision"], result.get("executed"))
        return result

    def evaluate_rollouts_current(self) -> dict[str, Any]:
        goal = {}
        try:
            goal = self.orch.sessions.get(self.robot_id).goal or {}
        except KeyError:
            pass
        return evaluate_rollouts(self._target_skill(), self.last_dreams, goal=goal)

    def _ensure_session(self):
        try:
            return self.orch.sessions.get(self.robot_id)
        except KeyError:
            self.select_embodiment(self.embodiment.key if self.embodiment else "unitree_h1")
            return self.orch.sessions.get(self.robot_id)

    def _maze(self):
        if self.world and self.world.maze is not None:
            return self.world.maze
        return generate_maze()
