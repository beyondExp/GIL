from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SafetyConfig:
    stale_state_after_s: float = 1.5
    heartbeat_timeout_s: float = 2.0
    max_linear_velocity_mps: float = 0.4
    max_lateral_velocity_mps: float = 0.3
    max_angular_velocity_rps: float = 0.6
    max_drive_duration_s: float = 10.0
    max_arm_height_m: float = 2.1
    min_arm_height_m: float = 0.05
    require_motion_enable: bool = True
    allow_sim_preview: bool = True
    # If base.z drops below this threshold, treat as fallen and refuse motion.
    min_upright_base_z_m: float = 0.55
    disable_motion_on_fall: bool = True


class SafetySupervisor:
    def __init__(self, cfg: SafetyConfig):
        self.cfg = cfg
        self.motion_enabled = False
        self.estop_active = False
        self.last_heartbeat_monotonic_s = time.monotonic()
        self.last_motion_command_monotonic_s = 0.0
        self.last_stop_reason = "not_stopped"
        self.last_validation_warning = ""

    def heartbeat(self, source: str = "") -> dict[str, Any]:
        self.last_heartbeat_monotonic_s = time.monotonic()
        return {
            "ok": True,
            "source": source or "unknown",
            "heartbeat_age_s": 0.0,
        }

    def set_motion_enabled(self, enabled: bool, reason: str = "") -> dict[str, Any]:
        self.motion_enabled = bool(enabled)
        if not enabled:
            self.last_stop_reason = reason or "motion_disabled"
        return {
            "ok": True,
            "motion_enabled": self.motion_enabled,
            "reason": reason or "",
        }

    def set_estop(self, active: bool, reason: str = "") -> dict[str, Any]:
        self.estop_active = bool(active)
        if active:
            self.motion_enabled = False
            self.last_stop_reason = reason or "estop"
        return {
            "ok": True,
            "estop_active": self.estop_active,
            "reason": reason or "",
        }

    def watchdog_status(self) -> dict[str, Any]:
        age_s = max(0.0, time.monotonic() - self.last_heartbeat_monotonic_s)
        return {
            "heartbeat_age_s": age_s,
            "heartbeat_timeout_s": self.cfg.heartbeat_timeout_s,
            "timed_out": age_s > self.cfg.heartbeat_timeout_s,
        }

    def build_status(self) -> dict[str, Any]:
        watchdog = self.watchdog_status()
        return {
            "motion_enabled": self.motion_enabled,
            "estop_active": self.estop_active,
            "last_stop_reason": self.last_stop_reason,
            "last_validation_warning": self.last_validation_warning,
            "watchdog": watchdog,
        }

    def validate_motion(
        self,
        *,
        command_type: str,
        state: dict[str, Any],
        payload: dict[str, Any],
        source: str = "controls",
    ) -> dict[str, Any]:
        try:
            from gil.core.authority import MotionAuthorityError, assert_motion_allowed
        except ImportError:
            MotionAuthorityError = None  # type: ignore[misc, assignment]
            assert_motion_allowed = None  # type: ignore[assignment]
        if assert_motion_allowed is not None:
            try:
                assert_motion_allowed(command_type=command_type, source=source)
            except MotionAuthorityError as exc:  # type: ignore[misc]
                return {"ok": False, "error": str(exc)}
        health = (state.get("health") or {}) if isinstance(state, dict) else {}
        sanitized = dict(payload)
        if command_type in {"cmd_vel", "preview_vel"}:
            if self.estop_active:
                return {"ok": False, "error": "Emergency stop is active."}
            is_preview = command_type == "preview_vel"
            if is_preview and not self.cfg.allow_sim_preview:
                return {"ok": False, "error": "Dream preview is disabled on hardware."}
            if (not is_preview) and self.cfg.require_motion_enable and not self.motion_enabled:
                return {"ok": False, "error": "Motion is disabled. Call enable_humanoid_motion first."}
            if (not is_preview) and not bool(health.get("ok")):
                return {
                    "ok": False,
                    "error": "Robot state is stale or incomplete.",
                    "health": health,
                }
            if (not is_preview) and bool(self.cfg.disable_motion_on_fall):
                try:
                    base = (state.get("base") or {}) if isinstance(state, dict) else {}
                    z = float(base.get("z", 0.0) or 0.0)
                except Exception:
                    z = 0.0
                # Only trigger if we have a plausible z reading.
                if 0.0 < z < float(self.cfg.min_upright_base_z_m):
                    self.motion_enabled = False
                    self.last_stop_reason = "fall_detected"
                    return {
                        "ok": False,
                        "error": f"Robot appears fallen (base.z={z:.2f}m). Motion disabled.",
                        "base_z_m": z,
                        "min_upright_base_z_m": float(self.cfg.min_upright_base_z_m),
                    }
            watchdog = self.watchdog_status()
            if (not is_preview) and watchdog["timed_out"]:
                self.motion_enabled = False
                self.last_stop_reason = "heartbeat_timeout"
                return {"ok": False, "error": "Heartbeat timed out. Motion disabled.", "watchdog": watchdog}
            sanitized["vx"] = _clamp(float(payload.get("vx", 0.0)), -self.cfg.max_linear_velocity_mps, self.cfg.max_linear_velocity_mps)
            sanitized["vy"] = _clamp(float(payload.get("vy", 0.0)), -self.cfg.max_lateral_velocity_mps, self.cfg.max_lateral_velocity_mps)
            sanitized["wz"] = _clamp(float(payload.get("wz", 0.0)), -self.cfg.max_angular_velocity_rps, self.cfg.max_angular_velocity_rps)
            sanitized["duration_s"] = _clamp(
                float(payload.get("duration_s", 0.25)),
                0.0,
                self.cfg.max_drive_duration_s,
            )
            if sanitized != payload:
                self.last_validation_warning = "Motion command was clamped to configured limits."
            self.last_motion_command_monotonic_s = time.monotonic()
            return {"ok": True, "payload": sanitized, "warning": self.last_validation_warning or None}

        if command_type == "move_robot":
            sanitized["y"] = _clamp(
                float(payload.get("y", self.cfg.min_arm_height_m)),
                self.cfg.min_arm_height_m,
                self.cfg.max_arm_height_m,
            )
            if float(payload.get("y", sanitized["y"])) != sanitized["y"]:
                self.last_validation_warning = "Arm height was clamped to configured limits."
            self.last_motion_command_monotonic_s = time.monotonic()
            return {"ok": True, "payload": sanitized, "warning": self.last_validation_warning or None}

        return {"ok": True, "payload": sanitized}

    def should_force_stop(self) -> tuple[bool, str]:
        watchdog = self.watchdog_status()
        if watchdog["timed_out"] and self.motion_enabled:
            self.motion_enabled = False
            self.last_stop_reason = "heartbeat_timeout"
            return True, "Heartbeat timed out."
        return False, ""


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))

