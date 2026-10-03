from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gil.core.config import RobotProfile


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class ChecklistReport:
    ok: bool
    backend: str
    checks: list[CheckResult] = field(default_factory=list)
    limp_vx: float = 0.05

    def failed(self) -> list[str]:
        return [c.name for c in self.checks if not c.ok]


def run_hardware_checklist(
    profile: RobotProfile,
    *,
    topics_seen: dict[str, bool],
    camera_info_present: bool,
    estop_clear: bool,
    battery_ok: bool | None = None,
    imu_ok: bool | None = None,
) -> ChecklistReport:
    checks: list[CheckResult] = []

    def need(name: str, ok: bool, detail: str) -> None:
        checks.append(CheckResult(name=name, ok=ok, detail=detail))

    need("odom", bool(topics_seen.get("odom")), "odometry must be streaming")
    need("joint_states", bool(topics_seen.get("joint_states")), "joint states must be streaming")
    if profile.safety.require_estop_topic or profile.is_hardware:
        need("estop_topic", bool(topics_seen.get("estop")), "estop topic required for hardware")
        need("estop_clear", estop_clear, "estop must be clear before limp test")
    if profile.safety.require_camera_info or profile.is_hardware:
        need("camera_info", camera_info_present, "calibrated camera_info required before autonomy")
    if profile.topics.battery:
        need("battery", battery_ok is not False and bool(topics_seen.get("battery") or battery_ok), "battery stream")
    if profile.topics.imu:
        need("imu", imu_ok is not False and bool(topics_seen.get("imu") or imu_ok), "imu stream")

    ok = all(c.ok for c in checks)
    return ChecklistReport(ok=ok, backend=profile.backend, checks=checks, limp_vx=profile.safety.limp_vx)


def limp_command(profile: RobotProfile, vx: float) -> dict[str, Any]:
    max_vx = min(abs(profile.safety.limp_vx), abs(profile.safety.max_vx))
    clamped = max(-max_vx, min(max_vx, float(vx)))
    return {"type": "cmd_vel", "vx": clamped, "vy": 0.0, "wz": 0.0, "mode": "limp"}
