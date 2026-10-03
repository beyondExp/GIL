import os
import sys
import time
import unittest

import pytest

pytestmark = pytest.mark.phase1


SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from humanoid_contract import default_robot_state, merge_state_update  # noqa: E402
from safety_supervisor import SafetyConfig, SafetySupervisor  # noqa: E402


class SafetySupervisorTests(unittest.TestCase):
    def setUp(self):
        self.supervisor = SafetySupervisor(SafetyConfig(stale_state_after_s=5.0, heartbeat_timeout_s=5.0))
        self.state = default_robot_state("humanoid")
        merge_state_update(
            self.state,
            {"base": {"x": 0.0, "y": 0.0, "z": 0.0}},
            source="test",
            backend="sim_ws",
            stale_after_s=5.0,
            motion_enabled=True,
            estop=False,
        )

    def test_drive_rejected_when_motion_disabled(self):
        result = self.supervisor.validate_motion(
            command_type="cmd_vel",
            state=self.state,
            payload={"vx": 1.0, "vy": 0.0, "wz": 0.0, "duration_s": 0.5},
        )
        self.assertFalse(result["ok"])
        self.assertIn("Motion is disabled", result["error"])

    def test_drive_clamped_when_enabled(self):
        self.supervisor.set_motion_enabled(True, reason="test")
        self.supervisor.heartbeat("unit-test")
        result = self.supervisor.validate_motion(
            command_type="cmd_vel",
            state=self.state,
            payload={"vx": 99.0, "vy": 99.0, "wz": 99.0, "duration_s": 99.0},
        )
        self.assertTrue(result["ok"])
        self.assertLessEqual(result["payload"]["vx"], self.supervisor.cfg.max_linear_velocity_mps)
        self.assertLessEqual(result["payload"]["vy"], self.supervisor.cfg.max_lateral_velocity_mps)
        self.assertLessEqual(result["payload"]["wz"], self.supervisor.cfg.max_angular_velocity_rps)
        self.assertLessEqual(result["payload"]["duration_s"], self.supervisor.cfg.max_drive_duration_s)

    def test_estop_rejects_drive(self):
        self.supervisor.set_motion_enabled(True, reason="test")
        self.supervisor.heartbeat("unit-test")
        self.supervisor.set_estop(True, reason="unit-test")
        result = self.supervisor.validate_motion(
            command_type="cmd_vel",
            state=self.state,
            payload={"vx": 0.1, "vy": 0.0, "wz": 0.0},
        )
        self.assertFalse(result["ok"])

    def test_heartbeat_timeout_force_stop(self):
        self.supervisor.cfg = SafetyConfig(heartbeat_timeout_s=0.01)
        self.supervisor.set_motion_enabled(True, reason="test")
        self.supervisor.last_heartbeat_monotonic_s = time.monotonic() - 1.0
        should_stop, reason = self.supervisor.should_force_stop()
        self.assertTrue(should_stop)
        self.assertIn("Heartbeat", reason)
        self.assertFalse(self.supervisor.motion_enabled)

    def test_preview_allowed_without_motion_enable_in_sim(self):
        self.supervisor.heartbeat("unit-test")
        result = self.supervisor.validate_motion(
            command_type="preview_vel",
            state=self.state,
            payload={"vx": 0.2, "vy": 0.0, "wz": 0.0},
            source="orchestrator",
        )
        self.assertTrue(result["ok"])

    def test_preview_disabled_on_hardware_config(self):
        self.supervisor.cfg = SafetyConfig(allow_sim_preview=False)
        result = self.supervisor.validate_motion(
            command_type="preview_vel",
            state=self.state,
            payload={"vx": 0.2, "vy": 0.0, "wz": 0.0},
            source="orchestrator",
        )
        self.assertFalse(result["ok"])

    def test_world_model_cannot_command_motors(self):
        self.supervisor.set_motion_enabled(True, reason="test")
        self.supervisor.heartbeat("unit-test")
        result = self.supervisor.validate_motion(
            command_type="cmd_vel",
            state=self.state,
            payload={"vx": 0.1, "vy": 0.0, "wz": 0.0},
            source="world_model",
        )
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
