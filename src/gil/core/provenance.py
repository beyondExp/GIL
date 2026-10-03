from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class CommandProvenance:
    """Full origin chain for a motor command.

    source is who is *sending* (orchestrator / controls).
    origin is who *proposed* (dream / operator / skill).
    gate_id is required when origin is untrusted (dream).
    """

    source: str = "orchestrator"
    origin: str = "operator"
    gate_id: str = ""
    rollout_id: str = ""
    skill_id: str = ""
    instruction: str = ""
    command_id: str = field(default_factory=lambda: uuid4().hex[:12])

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_command(cls, command: dict[str, Any], *, source: str | None = None) -> CommandProvenance:
        raw = command.get("provenance") if isinstance(command.get("provenance"), dict) else {}
        return cls(
            source=str(source or raw.get("source") or command.get("source") or "unknown"),
            origin=str(raw.get("origin") or command.get("origin") or ""),
            gate_id=str(raw.get("gate_id") or command.get("gate_id") or ""),
            rollout_id=str(raw.get("rollout_id") or command.get("rollout_id") or ""),
            skill_id=str(raw.get("skill_id") or command.get("skill_id") or ""),
            instruction=str(raw.get("instruction") or command.get("instruction") or ""),
            command_id=str(raw.get("command_id") or uuid4().hex[:12]),
        )


def stamp_command(command: dict[str, Any], provenance: CommandProvenance) -> dict[str, Any]:
    out = dict(command)
    out["provenance"] = provenance.as_dict()
    out["origin"] = provenance.origin
    out["gate_id"] = provenance.gate_id
    return out
