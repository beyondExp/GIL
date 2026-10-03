from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from gil.core.config import Role
from gil.core.metrics import METRICS
from gil.core.provenance import CommandProvenance, stamp_command
from gil.core.types import Goal
from gil.orchestrator.controls import CommandResult, ControlsPort
from gil.orchestrator.gate import ConfidenceGate, GateDecision
from gil.world.critic import PhysicsCritic
from gil.world.dream import Rollout, WorldModel
from gil.world.map import SceneMap


@dataclass
class MissionResult:
    executed: bool
    reason: str
    robot_id: str
    gate: GateDecision | None = None
    dreams: int = 0
    kept: int = 0
    commands: list[CommandResult] = field(default_factory=list)
    map_summary: dict[str, Any] = field(default_factory=dict)


class ObservationSource(Protocol):
    def perceive(self, robot_id: str) -> dict[str, Any]:
        ...


class MissionRunner:
    def __init__(
        self,
        *,
        controls: ControlsPort,
        world_model: WorldModel,
        critic: PhysicsCritic,
        scene_map: SceneMap,
        gate: ConfidenceGate | None = None,
        n_dreams: int = 8,
    ):
        self.controls = controls
        self.world_model = world_model
        self.critic = critic
        self.scene_map = scene_map
        self.gate = gate or ConfidenceGate()
        self.n_dreams = n_dreams

    def run(
        self,
        *,
        robot_id: str,
        goal: Goal,
        observation: dict[str, Any],
        role: Role,
        n_dreams: int | None = None,
    ) -> MissionResult:
        scene = self.scene_map.ingest(observation)
        preflight = self.controls.preflight(robot_id)
        from gil.world.dream_engine import dream_rollouts

        dreams = dream_rollouts(world_model=self.world_model, observation=observation, goal=goal, n=int(n_dreams or self.n_dreams))
        kept = self.critic.filter(dreams, goal)
        imag = _imagination_success(kept)
        critic_score = max((r.critic_score for r in kept), default=0.0)
        decision = self.gate.evaluate(
            imagination_success=imag,
            critic_score=critic_score,
            map_coverage=scene.coverage,
            map_calibrated=scene.calibrated,
            preflight_ok=bool(preflight.get("ok")),
        )
        summary = scene.summary()
        if not decision.ok:
            return MissionResult(
                executed=False,
                reason=decision.reason,
                robot_id=robot_id,
                gate=decision,
                dreams=len(dreams),
                kept=len(kept),
                map_summary=summary,
            )

        enable = self.controls.enable_motion(robot_id, role=role, reason="gate_pass")
        if not enable.get("ok"):
            return MissionResult(
                executed=False,
                reason="enable_rejected",
                robot_id=robot_id,
                gate=decision,
                dreams=len(dreams),
                kept=len(kept),
                map_summary=summary,
            )

        plan = kept[0]
        results: list[CommandResult] = []
        gate_id = decision.gate_id or ""
        rollout_id = getattr(plan, "rollout_id", "") or ""
        METRICS.inc("missions_executed")
        for command in plan.commands:
            stamped = stamp_command(
                command,
                CommandProvenance(
                    source="orchestrator",
                    origin="dream",
                    gate_id=gate_id,
                    rollout_id=rollout_id,
                ),
            )
            results.append(self.controls.send(robot_id, stamped, source="orchestrator"))
        if results and not all(r.success for r in results):
            self.controls.stop(robot_id, reason="command_failed")
            return MissionResult(
                executed=False,
                reason="command_failed",
                robot_id=robot_id,
                gate=decision,
                dreams=len(dreams),
                kept=len(kept),
                commands=results,
                map_summary=summary,
            )
        return MissionResult(
            executed=True,
            reason="executed",
            robot_id=robot_id,
            gate=decision,
            dreams=len(dreams),
            kept=len(kept),
            commands=results,
            map_summary=summary,
        )


def _imagination_success(rollouts: list[Rollout]) -> float:
    if not rollouts:
        return 0.0
    return sum(1.0 for r in rollouts if r.success) / float(len(rollouts))
