from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from gil.core.robot_adapter import CmdVelCalibration, SafeEnvelope


CompiledActionKind = Literal["cmd_vel", "goal_xy", "stop"]


@dataclass(frozen=True)
class CompiledAction:
    kind: CompiledActionKind
    payload: dict


def _clamp(x: float, lo: float, hi: float) -> float:
    return float(max(float(lo), min(float(hi), float(x))))


class ActionCompiler:
    """
    Convert agent intents / skills into safe, normalized low-level actions.

    - clamps to a SafeEnvelope
    - applies cmd_vel calibration scaling (units/semantics)
    """

    def __init__(self, *, envelope: SafeEnvelope, calib: CmdVelCalibration | None = None):
        self.env = envelope
        self.calib = calib or CmdVelCalibration()

    def stop(self, *, reason: str) -> CompiledAction:
        return CompiledAction(kind="stop", payload={"reason": str(reason or "stop")})

    def cmd_vel(self, *, vx: float, vy: float, wz: float, duration_s: float, reason: str, preview: bool = False) -> CompiledAction:
        # Clamp in "agent space" first.
        vx_c = _clamp(vx, -self.env.max_vx, self.env.max_vx)
        vy_c = _clamp(vy, -self.env.max_vy, self.env.max_vy)
        wz_c = _clamp(wz, -self.env.max_wz, self.env.max_wz)
        d_c = _clamp(duration_s, 0.02, self.env.max_drive_duration_s)

        # Apply normalization scaling for the underlying controller.
        vx_out = float(vx_c) * float(self.calib.vx_scale)
        vy_out = float(vy_c) * float(self.calib.vy_scale)
        wz_out = float(wz_c) * float(self.calib.wz_scale)

        if not all(math.isfinite(v) for v in (vx_out, vy_out, wz_out, d_c)):
            return self.stop(reason="non_finite_cmd_vel")

        return CompiledAction(
            kind="cmd_vel",
            payload={
                "vx": vx_out,
                "vy": vy_out,
                "wz": wz_out,
                "duration_s": d_c,
                "reason": str(reason or ""),
                "preview": bool(preview),
            },
        )

    def goal_xy(self, *, x: float, y: float, reason: str) -> CompiledAction:
        if not (math.isfinite(float(x)) and math.isfinite(float(y))):
            return self.stop(reason="non_finite_goal")
        return CompiledAction(kind="goal_xy", payload={"x": float(x), "y": float(y), "reason": str(reason or "")})

