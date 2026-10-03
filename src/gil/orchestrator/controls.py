from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gil.core.authority import MotionAuthorityError, assert_motion_allowed
from gil.core.config import RobotProfile, Role
from gil.core.health import HealthReport, controls_health
from gil.core.provenance import CommandProvenance


@dataclass
class CommandResult:
    success: bool
    command_type: str
    source: str
    error: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    origin: str = ""
    gate_id: str = ""


class ControlsPort:
    """The only path from GIL product code to actuators."""

    def preflight(self, robot_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def health(self, robot_id: str) -> HealthReport:
        raise NotImplementedError

    def send(self, robot_id: str, command: dict[str, Any], *, source: str) -> CommandResult:
        raise NotImplementedError

    def enable_motion(self, robot_id: str, *, role: Role, reason: str = "") -> dict[str, Any]:
        raise NotImplementedError

    def stop(self, robot_id: str, *, reason: str) -> CommandResult:
        raise NotImplementedError


class FakeControls(ControlsPort):
    def __init__(self, profile: RobotProfile):
        self.profile = profile
        self.motion_enabled = False
        self.estop = False
        self.sent: list[dict[str, Any]] = []
        self.connected = True
        self.topics_ok = True
        self.heartbeat_ok = True

    def preflight(self, robot_id: str) -> dict[str, Any]:
        ok = self.connected and self.topics_ok and not self.estop
        return {"ok": ok, "robot_id": robot_id, "backend": self.profile.backend}

    def health(self, robot_id: str) -> HealthReport:
        return controls_health(
            backend=self.profile.backend,
            ready_for_motion=self.motion_enabled and not self.estop and self.heartbeat_ok,
            warnings=[] if self.heartbeat_ok else ["heartbeat_timeout"],
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
        if self.estop:
            return CommandResult(False, command_type, source, error="Emergency stop is active.")
        if command_type != "preview_vel" and self.profile.safety.require_motion_enable and not self.motion_enabled:
            return CommandResult(False, command_type, source, error="Motion is disabled.")
        if not self.heartbeat_ok:
            return CommandResult(False, command_type, source, error="Heartbeat timed out.")
        self.sent.append({"robot_id": robot_id, "command": dict(command), "source": source, "provenance": prov.as_dict()})
        return CommandResult(True, command_type, source, payload=dict(command), origin=prov.origin, gate_id=prov.gate_id)

    def enable_motion(self, robot_id: str, *, role: Role, reason: str = "") -> dict[str, Any]:
        if role == Role.observer:
            return {"ok": False, "error": "Observer cannot enable motion."}
        pre = self.preflight(robot_id)
        if not pre.get("ok"):
            return {"ok": False, "error": "Preflight failed.", "preflight": pre}
        self.motion_enabled = True
        return {"ok": True, "motion_enabled": True, "reason": reason, "robot_id": robot_id}

    def stop(self, robot_id: str, *, reason: str) -> CommandResult:
        self.motion_enabled = False
        cmd = {"type": "cmd_vel", "vx": 0.0, "vy": 0.0, "wz": 0.0, "reason": reason}
        self.sent.append({"robot_id": robot_id, "command": cmd, "source": "safety"})
        return CommandResult(True, "cmd_vel", "safety", payload=cmd)
