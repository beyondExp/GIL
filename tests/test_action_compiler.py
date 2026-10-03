from __future__ import annotations

from gil.core.robot_adapter import CmdVelCalibration, SafeEnvelope
from gil.orchestrator.action_compiler import ActionCompiler


def test_action_compiler_clamps_and_scales_cmd_vel() -> None:
    env = SafeEnvelope(max_vx=0.2, max_vy=0.0, max_wz=0.35, max_drive_duration_s=2.0, min_upright_base_z_m=0.7)
    calib = CmdVelCalibration(vx_scale=2.0, vy_scale=1.0, wz_scale=0.5)
    c = ActionCompiler(envelope=env, calib=calib)

    a = c.cmd_vel(vx=0.9, vy=1.0, wz=2.0, duration_s=9.0, reason="x")
    assert a.kind == "cmd_vel"
    # clamped then scaled
    assert abs(a.payload["vx"] - 0.4) < 1e-6  # 0.2 * 2.0
    assert abs(a.payload["vy"] - 0.0) < 1e-6
    assert abs(a.payload["wz"] - 0.175) < 1e-6  # 0.35 * 0.5
    assert abs(a.payload["duration_s"] - 2.0) < 1e-6

