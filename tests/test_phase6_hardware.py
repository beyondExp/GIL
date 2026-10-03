from __future__ import annotations

import pytest

from gil.core.profiles import load_profile
from gil.hardware.checklist import limp_command, run_hardware_checklist
from gil.orchestrator.controls import FakeControls
from safety_supervisor import SafetyConfig, SafetySupervisor

pytestmark = pytest.mark.phase6


def test_hardware_checklist_requires_estop_and_camera_info():
    profile = load_profile("unitree_h1_hardware")
    failed = run_hardware_checklist(
        profile,
        topics_seen={"odom": True, "joint_states": True},
        camera_info_present=False,
        estop_clear=True,
    )
    assert failed.ok is False
    assert "estop_topic" in failed.failed()
    assert "camera_info" in failed.failed()

    passed = run_hardware_checklist(
        profile,
        topics_seen={"odom": True, "joint_states": True, "estop": True, "battery": True, "imu": True},
        camera_info_present=True,
        estop_clear=True,
        battery_ok=True,
        imu_ok=True,
    )
    assert passed.ok is True
    assert passed.limp_vx == 0.05


def test_limp_speed_is_clamped():
    profile = load_profile("unitree_h1_hardware")
    cmd = limp_command(profile, vx=1.0)
    assert cmd["type"] == "cmd_vel"
    assert abs(cmd["vx"]) <= profile.safety.limp_vx
    assert cmd["mode"] == "limp"


def test_dead_heartbeat_cannot_drive():
    controls = FakeControls(load_profile("unitree_h1_sim"))
    controls.motion_enabled = True
    controls.heartbeat_ok = False
    result = controls.send("h1", {"type": "cmd_vel", "vx": 0.2}, source="orchestrator")
    assert result.success is False
    assert "Heartbeat" in result.error


def test_safety_watchdog_zeros_motion_flag():
    supervisor = SafetySupervisor(SafetyConfig(heartbeat_timeout_s=0.01))
    supervisor.set_motion_enabled(True, reason="test")
    supervisor.last_heartbeat_monotonic_s -= 5.0
    should_stop, _ = supervisor.should_force_stop()
    assert should_stop is True
    assert supervisor.motion_enabled is False
