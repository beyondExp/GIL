from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from gil.core.provenance import CommandProvenance, stamp_command

ActionKind = Literal["cmd_vel", "preview_vel", "ee_delta", "ee_pose", "gripper", "joint_traj", "estop", "stop"]
Morphology = Literal["humanoid", "arm", "quadruped", "wheeled", "drone", "mobile_manipulator", "other"]


@dataclass
class RobotAction:
    kind: ActionKind
    values: dict[str, Any] = field(default_factory=dict)
    provenance: CommandProvenance = field(default_factory=CommandProvenance)

    def as_command(self) -> dict[str, Any]:
        payload = {"type": self.kind, **self.values}
        return stamp_command(payload, self.provenance)


@dataclass
class RobotObservation:
    morphology: Morphology
    state: dict[str, Any] = field(default_factory=dict)
    image: str | None = None
    objects: list[dict[str, Any]] = field(default_factory=list)
    perception: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "morphology": self.morphology,
            "state": dict(self.state),
            "image": self.image,
            "objects": list(self.objects),
            "perception": dict(self.perception),
        }


class RobotInterface(Protocol):
    """Every body GIL can operate implements this protocol.

    The orchestrator never names humanoid vs arm — it calls these methods.
    """

    morphology: Morphology

    def get_state(self) -> dict[str, Any]:
        ...

    def get_observation(self) -> RobotObservation:
        ...

    def execute(self, action: RobotAction) -> dict[str, Any]:
        ...

    def preflight(self) -> dict[str, Any]:
        ...

    def enable(self, *, reason: str = "") -> dict[str, Any]:
        ...

    def disable(self, *, reason: str = "") -> dict[str, Any]:
        ...

    def estop(self, active: bool, *, reason: str = "") -> dict[str, Any]:
        ...


def morphology_from_kind(kind: str) -> Morphology:
    k = (kind or "").strip().lower()
    if k in {"humanoid", "h1", "g1", "humanoid_biped"}:
        return "humanoid"
    if k in {"arm", "franka", "panda", "manipulator_arm"}:
        return "arm"
    if k in {"quadruped", "anymal", "go2"}:
        return "quadruped"
    if k in {"wheeled", "jetbot", "nova_carter", "wheeled_base"}:
        return "wheeled"
    if k in {"drone", "aerial_drone"}:
        return "drone"
    if k in {"mobile_manipulator"}:
        return "mobile_manipulator"
    return "other"


def action_from_command(command: dict[str, Any], *, source: str = "orchestrator") -> RobotAction:
    kind = str(command.get("type") or "cmd_vel")
    values = {k: v for k, v in command.items() if k not in {"type", "provenance", "origin", "gate_id", "source"}}
    return RobotAction(
        kind=kind,  # type: ignore[arg-type]
        values=values,
        provenance=CommandProvenance.from_command(command, source=source),
    )
