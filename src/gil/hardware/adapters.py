from __future__ import annotations

from typing import Any, Callable

from gil.core.authority import assert_motion_allowed
from gil.core.provenance import CommandProvenance
from gil.core.robot_interface import Morphology, RobotAction, RobotInterface, RobotObservation, morphology_from_kind


SendFn = Callable[[dict[str, Any], str], dict[str, Any]]
StateFn = Callable[[], dict[str, Any]]


class InProcessAdapter:
    """Generic adapter used by tests and the orchestrator FakeControls path."""

    def __init__(
        self,
        morphology: Morphology,
        *,
        get_state: StateFn | None = None,
        send: SendFn | None = None,
    ) -> None:
        self.morphology = morphology
        self._state: dict[str, Any] = {}
        self._get_state = get_state
        self._send = send
        self.motion_enabled = False
        self.estop_active = False
        self.sent: list[dict[str, Any]] = []

    def get_state(self) -> dict[str, Any]:
        if self._get_state:
            return self._get_state()
        return dict(self._state)

    def get_observation(self) -> RobotObservation:
        st = self.get_state()
        return RobotObservation(
            morphology=self.morphology,
            state=st,
            image=st.get("last_image"),
        )

    def execute(self, action: RobotAction) -> dict[str, Any]:
        command = action.as_command()
        assert_motion_allowed(
            command_type=action.kind,
            source=action.provenance.source,
            origin=action.provenance.origin,
            gate_id=action.provenance.gate_id,
        )
        if self.estop_active:
            return {"ok": False, "error": "estop"}
        if action.kind not in {"preview_vel", "estop", "stop"} and not self.motion_enabled:
            return {"ok": False, "error": "motion_disabled"}
        self.sent.append(command)
        if self._send:
            return self._send(command, self.morphology)
        return {"ok": True, "command": command}

    def preflight(self) -> dict[str, Any]:
        return {"ok": not self.estop_active, "morphology": self.morphology}

    def enable(self, *, reason: str = "") -> dict[str, Any]:
        if self.estop_active:
            return {"ok": False, "error": "estop"}
        self.motion_enabled = True
        return {"ok": True, "reason": reason}

    def disable(self, *, reason: str = "") -> dict[str, Any]:
        self.motion_enabled = False
        return {"ok": True, "reason": reason}

    def estop(self, active: bool, *, reason: str = "") -> dict[str, Any]:
        self.estop_active = bool(active)
        if active:
            self.motion_enabled = False
        return {"ok": True, "estop": self.estop_active, "reason": reason}


def adapter_for_kind(kind: str) -> RobotInterface:
    return InProcessAdapter(morphology_from_kind(kind))


def gated_action(
    kind: str,
    values: dict[str, Any],
    *,
    source: str = "orchestrator",
    origin: str = "operator",
    gate_id: str = "",
    rollout_id: str = "",
    skill_id: str = "",
) -> RobotAction:
    return RobotAction(
        kind=kind,  # type: ignore[arg-type]
        values=values,
        provenance=CommandProvenance(
            source=source,
            origin=origin,
            gate_id=gate_id,
            rollout_id=rollout_id,
            skill_id=skill_id,
        ),
    )
