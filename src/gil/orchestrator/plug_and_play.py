from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from gil.core.robot_adapter import CmdVelCalibration, RobotAdapter, SafeEnvelope
from gil.orchestrator.action_compiler import ActionCompiler


@dataclass(frozen=True)
class CalibrationResult:
    """
    Minimal boot calibration result.

    This is meant to be persisted (sidecar JSON) and used by the ActionCompiler.
    """

    observed_at_s: float
    cmd_vel: CmdVelCalibration
    envelope: SafeEnvelope
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "observed_at_s": float(self.observed_at_s),
            "cmd_vel": {"vx_scale": self.cmd_vel.vx_scale, "vy_scale": self.cmd_vel.vy_scale, "wz_scale": self.cmd_vel.wz_scale},
            "envelope": {
                "max_vx": self.envelope.max_vx,
                "max_vy": self.envelope.max_vy,
                "max_wz": self.envelope.max_wz,
                "max_drive_duration_s": self.envelope.max_drive_duration_s,
                "min_upright_base_z_m": self.envelope.min_upright_base_z_m,
            },
            "notes": list(self.notes),
        }


class PlugAndPlayBootstrap:
    """
    Plug-and-play boot sequence:
    - preflight
    - stand/reset
    - quick cmd_vel response checks (unit sanity + effective scaling)
    - return an ActionCompiler configured for safe steering
    """

    def __init__(self, adapter: RobotAdapter):
        self.adapter = adapter

    async def calibrate(
        self,
        *,
        min_z: float = 0.70,
        test_vx: float = 0.20,
        test_vx_s: float = 6.0,
        test_wz: float = 0.25,
        test_wz_s: float = 1.0,
    ) -> CalibrationResult:
        notes: list[str] = []
        pre = await self.adapter.preflight()
        if not bool(pre.get("ok")):
            notes.append("preflight_failed")
        # Bring it to a known safe state.
        await self.adapter.stop(reason="bootstrap")
        await self.adapter.disable_motion(reason="bootstrap")
        if self.adapter.capabilities().supports_reset_episode:
            try:
                await self.adapter.reset_episode()
            except Exception:
                notes.append("reset_episode_failed")

        # Ensure goal/external doesn't matter for cmd_vel tests: we use external mode.
        if self.adapter.capabilities().supports_mode_switch:
            try:
                await self.adapter.set_mode("external")
            except Exception:
                notes.append("set_mode_external_failed")

        await self.adapter.enable_motion(reason="bootstrap")

        st0 = await self.adapter.get_state()
        z0 = float(st0.base.z) if st0.base else 0.0
        if 0.0 < z0 < float(min_z):
            notes.append(f"not_upright_at_start:z={z0:.3f}")

        # Forward response: measure distance moved for a given commanded vx.
        await self.adapter.drive_cmd_vel(vx=float(test_vx), vy=0.0, wz=0.0, duration_s=float(test_vx_s), reason="bootstrap_vx")
        st1 = await self.adapter.get_state()

        vx_scale = 1.0
        if st0.base and st1.base:
            dx = float(st1.base.x - st0.base.x)
            dy = float(st1.base.y - st0.base.y)
            dist = float(math.sqrt(dx * dx + dy * dy))
            # expected distance in ideal unicycle world is vx * t; use it only as a sanity ratio
            exp = abs(float(test_vx) * float(test_vx_s))
            if exp > 1e-6:
                ratio = dist / exp
                # Reject obvious pose jumps (reset not converged / stale pose cache / teleport).
                if dist > 3.0:
                    notes.append(f"vx_response_dist_too_large={dist:.3f}")
                # If ratio is extremely small/large, don't attempt to "correct" by huge scaling (unsafe).
                elif 0.15 <= ratio <= 2.5:
                    vx_scale = 1.0 / max(0.2, min(5.0, ratio))
                    notes.append(f"vx_response_ratio={ratio:.3f}")
                else:
                    notes.append(f"vx_response_ratio_out_of_range={ratio:.3f}")
        else:
            notes.append("no_pose_for_vx_response")

        # Yaw response: measure dyaw for a given commanded wz.
        st2 = await self.adapter.get_state()
        await self.adapter.drive_cmd_vel(vx=0.0, vy=0.0, wz=float(test_wz), duration_s=float(test_wz_s), reason="bootstrap_wz")
        st3 = await self.adapter.get_state()

        wz_scale = 1.0
        if st2.base and st3.base:
            dyaw = float(st3.base.yaw - st2.base.yaw)
            # wrap to [-pi, pi]
            dyaw = (dyaw + math.pi) % (2 * math.pi) - math.pi
            exp = float(test_wz) * float(test_wz_s)
            if abs(exp) > 1e-6:
                ratio = abs(dyaw) / abs(exp)
                if 0.15 <= ratio <= 3.0:
                    wz_scale = 1.0 / max(0.2, min(5.0, ratio))
                    notes.append(f"wz_response_ratio={ratio:.3f}")
                else:
                    notes.append(f"wz_response_ratio_out_of_range={ratio:.3f}")
        else:
            notes.append("no_pose_for_wz_response")

        # Conservative envelope defaults: clamp to low values; calibration scales adjust semantics.
        env = SafeEnvelope(max_vx=0.20, max_vy=0.0, max_wz=0.35, max_drive_duration_s=8.0, min_upright_base_z_m=float(min_z))
        calib = CmdVelCalibration(vx_scale=float(vx_scale), vy_scale=1.0, wz_scale=float(wz_scale))

        # Leave the robot safe after calibration.
        await self.adapter.disable_motion(reason="bootstrap_done")

        return CalibrationResult(observed_at_s=float(time.time()), cmd_vel=calib, envelope=env, notes=notes)

    async def make_compiler(self, *, calibration: CalibrationResult | None = None) -> ActionCompiler:
        if calibration is None:
            calibration = await self.calibrate()
        return ActionCompiler(envelope=calibration.envelope, calib=calibration.cmd_vel)

