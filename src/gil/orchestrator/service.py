from __future__ import annotations

from typing import Any

from gil.core.auth import AuthContext
from gil.core.config import GilSettings, Role
from gil.core.health import HealthReport
from gil.core.profiles import list_profiles
from gil.core.schemas import ORCHESTRATOR_TOOLS
from gil.orchestrator.controls import ControlsPort, FakeControls
from gil.orchestrator.gate import ConfidenceGate
from gil.core.types import Goal
from gil.orchestrator.mission import MissionResult, MissionRunner
from gil.orchestrator.session import SessionStore
from gil.world.critic import PhysicsCritic
from gil.world.dream import MockWorldModel
from gil.world.map import SceneMap


class Orchestrator:
    """Agent-facing facade. World models never talk to actuators through this object."""

    def __init__(
        self,
        *,
        settings: GilSettings | None = None,
        controls_factory=None,
        world_model=None,
        critic=None,
        gate: ConfidenceGate | None = None,
    ):
        self.settings = settings or GilSettings()
        self.auth = AuthContext(self.settings)
        self.sessions = SessionStore()
        self._controls_factory = controls_factory or (lambda profile: FakeControls(profile))
        self._controls: dict[str, ControlsPort] = {}
        self.world_model = world_model or MockWorldModel()
        self.critic = critic or PhysicsCritic()
        self.maps: dict[str, SceneMap] = {}
        self.gate = gate or ConfidenceGate()

    def connect_robot(self, profile_id: str, robot_id: str | None = None, token: str | None = None) -> dict[str, Any]:
        session = self.sessions.attach(robot_id or profile_id, profile_id)
        role = self.auth.resolve(token, hardware=session.profile.is_hardware)
        self._controls[session.robot_id] = self._controls_factory(session.profile)
        self.maps[session.robot_id] = SceneMap(profile=session.profile)
        return {
            "ok": True,
            "robot_id": session.robot_id,
            "profile_id": session.profile.profile_id,
            "backend": session.profile.backend,
            "role": role.value,
            "available_profiles": list_profiles(),
        }

    def set_goal(self, robot_id: str, goal: dict[str, Any]) -> dict[str, Any]:
        session = self.sessions.get(robot_id)
        session.goal = dict(goal)
        return {"ok": True, "robot_id": robot_id, "goal": session.goal}

    def get_situation(self, robot_id: str) -> dict[str, Any]:
        session = self.sessions.get(robot_id)
        controls = self._controls[robot_id]
        scene = self.maps[robot_id]
        health: HealthReport = controls.health(robot_id)
        return {
            "robot_id": robot_id,
            "profile_id": session.profile.profile_id,
            "goal": session.goal,
            "health": health.model_dump(),
            "map": scene.summary(),
            "preflight": controls.preflight(robot_id),
        }

    def abort(self, robot_id: str) -> dict[str, Any]:
        result = self._controls[robot_id].stop(robot_id, reason="abort")
        return {"ok": result.success, "robot_id": robot_id, "result": result.__dict__}

    def run_mission(
        self,
        robot_id: str,
        observation: dict[str, Any],
        token: str | None = None,
        n_dreams: int = 8,
    ) -> MissionResult:
        session = self.sessions.get(robot_id)
        if not session.goal:
            return MissionResult(executed=False, reason="no_goal", robot_id=robot_id)
        role = self.auth.resolve(token, hardware=session.profile.is_hardware)
        if role == Role.observer:
            return MissionResult(executed=False, reason="observer_forbidden", robot_id=robot_id)
        runner = MissionRunner(
            controls=self._controls[robot_id],
            world_model=self.world_model,
            critic=self.critic,
            scene_map=self.maps[robot_id],
            gate=self.gate,
            n_dreams=n_dreams,
        )
        session.last_observation = observation
        return runner.run(
            robot_id=robot_id,
            goal=Goal.from_dict(session.goal),
            observation=observation,
            role=role,
            n_dreams=n_dreams,
        )

    def tool_manifest(self) -> list[dict[str, Any]]:
        return [t.model_dump() for t in ORCHESTRATOR_TOOLS]
