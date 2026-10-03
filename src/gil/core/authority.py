from __future__ import annotations

from gil import MOTOR_COMMAND_TYPES, UNTRUSTED_MOTION_SOURCES
from gil.core.metrics import METRICS
from gil.core.provenance import CommandProvenance

TRUSTED_RELAY_SOURCES = frozenset({"orchestrator", "controls", "operator", "autonomy"})


class MotionAuthorityError(PermissionError):
    """Raised when an untrusted source tries to command actuators."""


def assert_motion_allowed(
    *,
    command_type: str,
    source: str,
    origin: str = "",
    gate_id: str = "",
    provenance: CommandProvenance | dict | None = None,
) -> None:
    src = (source or "unknown").strip().lower()
    cmd = (command_type or "").strip().lower()
    origin_s = (origin or "").strip().lower()
    gate = gate_id
    if provenance is not None:
        if isinstance(provenance, CommandProvenance):
            origin_s = origin_s or provenance.origin.strip().lower()
            gate = gate or provenance.gate_id
            src = src or provenance.source.strip().lower()
        elif isinstance(provenance, dict):
            origin_s = origin_s or str(provenance.get("origin") or "").strip().lower()
            gate = gate or str(provenance.get("gate_id") or "")
    if cmd not in MOTOR_COMMAND_TYPES:
        return
    if src in UNTRUSTED_MOTION_SOURCES:
        METRICS.inc("authority_denied_source")
        raise MotionAuthorityError(
            f"Source '{src}' cannot issue motor command '{cmd}'. "
            "World models propose; gil_controls permits."
        )
    if origin_s in UNTRUSTED_MOTION_SOURCES:
        if src not in TRUSTED_RELAY_SOURCES:
            METRICS.inc("authority_denied_origin")
            raise MotionAuthorityError(
                f"Origin '{origin_s}' cannot reach motors via source '{src}'."
            )
        if not str(gate).strip():
            METRICS.inc("authority_denied_ungated_dream")
            raise MotionAuthorityError(
                "Dream-originated motor commands require a gate_id after ConfidenceGate pass."
            )
    METRICS.inc("authority_allowed")


def vision_may_authorize_motion(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("safe_for_motion_authority") is True:
        return True
    return False
