from __future__ import annotations

from copy import deepcopy
import time
import uuid
from typing import Any


ROBOT_KINDS = ("arm", "humanoid")

CRITICAL_STREAMS_BY_KIND: dict[str, tuple[str, ...]] = {
    "arm": ("joints", "end_effector"),
    "humanoid": ("base",),
}


def now_ms() -> int:
    return int(time.time() * 1000)


def monotonic_s() -> float:
    return float(time.monotonic())


def default_robot_state(robot_kind: str) -> dict[str, Any]:
    base = {
        "robot_kind": robot_kind,
        "joints": {},
        "end_effector": {},
        "gripper_open": True,
        "base": {},
        "sensors": {},
        "imu": {},
        "battery": {},
        "faults": [],
        "last_image": None,
        "last_image_left": None,
        "last_image_right": None,
        "last_image_wide": None,
        "camera_info": None,
        "camera_info_wide": None,
        "images": {},
        # Visualization/inspection anchor (local XY -> global lat/lon). Owned by gil_controls.
        "world_anchor": None,
        # Optional frame graph / calibration (backend-provided or derived).
        "frame_graph": None,
        "status": "idle",
        "backend": "unknown",
        "mode": "external" if robot_kind == "humanoid" else "arm",
        "meta": {
            "created_at_ms": now_ms(),
            "updated_at_ms": now_ms(),
            "updated_at_monotonic_s": monotonic_s(),
            "last_source": "bootstrap",
            "sources": {},
        },
        "health": {
            "ok": robot_kind == "arm",
            "ready_for_motion": False,
            "warnings": [],
            "stale_fields": [],
            "critical_fields": list(CRITICAL_STREAMS_BY_KIND.get(robot_kind, ())),
            "backend": "unknown",
            "estop": False,
            "motion_enabled": False,
        },
        "last_command": {},
    }
    return base


def command_envelope(
    command_type: str,
    *,
    robot_kind: str,
    reason: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    env = {
        "type": command_type,
        "robot_kind": robot_kind,
        "command_id": str(uuid.uuid4()),
        "issued_at_ms": now_ms(),
        "issued_at_monotonic_s": monotonic_s(),
        "reason": reason or "",
    }
    if payload:
        env.update(payload)
    return env


def merge_state_update(
    state: dict[str, Any],
    partial: dict[str, Any],
    *,
    source: str,
    backend: str,
    stale_after_s: float,
    motion_enabled: bool,
    estop: bool,
) -> dict[str, Any]:
    if not partial:
        return recompute_health(
            state,
            stale_after_s=stale_after_s,
            motion_enabled=motion_enabled,
            estop=estop,
            backend=backend,
        )

    updated_at_ms = int(partial.get("_observed_at_ms") or now_ms())
    updated_at_monotonic_s = float(partial.get("_observed_at_monotonic_s") or monotonic_s())
    sources = state.setdefault("meta", {}).setdefault("sources", {})

    for key, value in partial.items():
        if key.startswith("_"):
            continue
        if key in {"images", "world_anchor", "frame_graph"}:
            # IMPORTANT: Replace images dict entirely (do NOT merge).
            # Otherwise stale camera keys linger forever across sim restarts / sensor reconfigs.
            if key == "images":
                state[key] = deepcopy(value) if isinstance(value, dict) else {}
            else:
                state[key] = deepcopy(value)
        elif isinstance(value, dict) and isinstance(state.get(key), dict) and key in {
            "sensors",
            "base",
            "end_effector",
            "camera_info",
            "camera_info_wide",
            "battery",
            "safety",
        }:
            merged = deepcopy(state.get(key) or {})
            merged.update(deepcopy(value))
            state[key] = merged
        else:
            state[key] = deepcopy(value)
        sources[key] = {
            "source": source,
            "updated_at_ms": updated_at_ms,
            "updated_at_monotonic_s": updated_at_monotonic_s,
        }

    state["backend"] = backend
    meta = state.setdefault("meta", {})
    meta["updated_at_ms"] = updated_at_ms
    meta["updated_at_monotonic_s"] = updated_at_monotonic_s
    meta["last_source"] = source
    return recompute_health(
        state,
        stale_after_s=stale_after_s,
        motion_enabled=motion_enabled,
        estop=estop,
        backend=backend,
    )


def recompute_health(
    state: dict[str, Any],
    *,
    stale_after_s: float,
    motion_enabled: bool,
    estop: bool,
    backend: str,
) -> dict[str, Any]:
    now_mono = monotonic_s()
    stale_fields: list[str] = []
    warnings: list[str] = []
    critical_fields = list(CRITICAL_STREAMS_BY_KIND.get(state.get("robot_kind") or "", ()))
    source_meta = (state.get("meta") or {}).get("sources") or {}

    for field in critical_fields:
        info = source_meta.get(field)
        if not info:
            stale_fields.append(field)
            continue
        age_s = now_mono - float(info.get("updated_at_monotonic_s") or 0.0)
        if age_s > stale_after_s:
            stale_fields.append(field)

    if estop:
        warnings.append("Emergency stop is active.")
    if not motion_enabled:
        warnings.append("Motion is disabled.")
    if stale_fields:
        warnings.append(f"Stale or missing critical fields: {', '.join(sorted(stale_fields))}")

    ready = (not estop) and motion_enabled and (not stale_fields)
    state["health"] = {
        "ok": not stale_fields,
        "ready_for_motion": ready,
        "warnings": warnings,
        "stale_fields": stale_fields,
        "critical_fields": critical_fields,
        "backend": backend,
        "estop": estop,
        "motion_enabled": motion_enabled,
    }
    return state


def sanitized_state(state: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(state)

