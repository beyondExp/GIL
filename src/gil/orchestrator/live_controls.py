from __future__ import annotations

from typing import Any

from gil.core.authority import MotionAuthorityError, assert_motion_allowed
from gil.core.provenance import CommandProvenance
from gil.core.config import RobotProfile, Role
from gil.core.health import HealthReport, controls_health
from gil.orchestrator.controls import CommandResult, ControlsPort
from gil.world.twin import UnicycleTwin

try:
    from humanoid_contract import default_robot_state, merge_state_update
    from safety_supervisor import SafetyConfig, SafetySupervisor
except ImportError as exc:  # pragma: no cover
    raise ImportError("SupervisorControls requires gil_controls/src on PYTHONPATH") from exc


class SupervisorControls(ControlsPort):
    """Real SafetySupervisor + unicycle twin. Motor commands only after validation."""

    def __init__(self, profile: RobotProfile, twin: UnicycleTwin | None = None):
        self.profile = profile
        self.twin = twin or UnicycleTwin()
        self.sent: list[dict[str, Any]] = []
        self.safety = SafetySupervisor(
            SafetyConfig(
                stale_state_after_s=profile.safety.stale_state_after_s,
                heartbeat_timeout_s=profile.safety.heartbeat_timeout_s,
                max_linear_velocity_mps=profile.safety.max_vx,
                max_lateral_velocity_mps=profile.safety.max_vy,
                max_angular_velocity_rps=profile.safety.max_wz,
                max_drive_duration_s=profile.safety.max_drive_duration_s,
                require_motion_enable=profile.safety.require_motion_enable,
            )
        )
        self.state = default_robot_state(profile.kind)
        self._sync_state("bootstrap")

    def _sync_state(self, source: str) -> None:
        merge_state_update(
            self.state,
            {"base": {"x": self.twin.pose.x, "y": self.twin.pose.y, "yaw": self.twin.pose.yaw, "z": 0.0}},
            source=source,
            backend=self.profile.backend,
            stale_after_s=self.profile.safety.stale_state_after_s,
            motion_enabled=self.safety.motion_enabled,
            estop=self.safety.estop_active,
        )

    def preflight(self, robot_id: str) -> dict[str, Any]:
        self._sync_state("preflight")
        ok = (not self.safety.estop_active) and bool(self.state.get("health", {}).get("ok"))
        return {"ok": ok, "robot_id": robot_id, "backend": self.profile.backend}

    def health(self, robot_id: str) -> HealthReport:
        self._sync_state("health")
        h = self.state.get("health") or {}
        return controls_health(
            backend=self.profile.backend,
            ready_for_motion=bool(h.get("ready_for_motion")),
            warnings=list(h.get("warnings") or []),
        )

    def send(self, robot_id: str, command: dict[str, Any], *, source: str) -> CommandResult:
        command_type = str(command.get("type") or "")
        prov = CommandProvenance.from_command(command, source=source)
        try:
            assert_motion_allowed(
                command_type=command_type,
                source=source,
                origin=prov.origin,
                gate_id=prov.gate_id,
                provenance=prov,
            )
        except MotionAuthorityError as exc:
            return CommandResult(False, command_type, source, error=str(exc), origin=prov.origin, gate_id=prov.gate_id)
        self.safety.heartbeat(source=source)
        self._sync_state("command")
        validation = self.safety.validate_motion(
            command_type=command_type or "cmd_vel",
            state=self.state,
            payload=command,
            source=source,
        )
        if not validation.get("ok"):
            return CommandResult(False, command_type, source, error=str(validation.get("error") or "rejected"))
        payload = dict(validation.get("payload") or command)
        if command_type == "cmd_vel" or not command_type:
            self.twin.step(
                float(payload.get("vx", 0.0)),
                float(payload.get("vy", 0.0)),
                float(payload.get("wz", 0.0)),
                float(payload.get("dt", 0.2)),
            )
        self._sync_state("twin")
        self.sent.append({"robot_id": robot_id, "command": payload, "source": source})
        return CommandResult(True, command_type or "cmd_vel", source, payload=payload)

    def enable_motion(self, robot_id: str, *, role: Role, reason: str = "") -> dict[str, Any]:
        if role.value == "observer":
            return {"ok": False, "error": "Observer cannot enable motion."}
        pre = self.preflight(robot_id)
        if not pre.get("ok"):
            return {"ok": False, "error": "Preflight failed.", "preflight": pre}
        self.safety.heartbeat(source="enable")
        result = self.safety.set_motion_enabled(True, reason=reason)
        self._sync_state("enable")
        return {"ok": True, "motion_enabled": result["motion_enabled"], "reason": reason, "robot_id": robot_id}

    def stop(self, robot_id: str, *, reason: str) -> CommandResult:
        self.safety.set_motion_enabled(False, reason=reason)
        self.twin.step(0.0, 0.0, 0.0, 0.05)
        self._sync_state("stop")
        cmd = {"type": "cmd_vel", "vx": 0.0, "vy": 0.0, "wz": 0.0, "reason": reason}
        self.sent.append({"robot_id": robot_id, "command": cmd, "source": "safety"})
        return CommandResult(True, "cmd_vel", "safety", payload=cmd)
