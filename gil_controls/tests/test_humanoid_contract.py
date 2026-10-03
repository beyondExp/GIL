import os
import sys
import unittest

import pytest

pytestmark = pytest.mark.phase1


SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from humanoid_contract import default_robot_state, merge_state_update  # noqa: E402


class HumanoidContractTests(unittest.TestCase):
    def test_default_state_has_health_metadata(self):
        state = default_robot_state("humanoid")
        self.assertEqual(state["robot_kind"], "humanoid")
        self.assertIn("health", state)
        self.assertIn("meta", state)
        self.assertFalse(state["health"]["ready_for_motion"])

    def test_merge_state_update_tracks_source_and_readiness(self):
        state = default_robot_state("humanoid")
        merge_state_update(
            state,
            {"base": {"x": 1.0, "y": 0.0, "z": 0.0}},
            source="test",
            backend="sim_ws",
            stale_after_s=5.0,
            motion_enabled=True,
            estop=False,
        )
        self.assertEqual(state["base"]["x"], 1.0)
        self.assertEqual(state["meta"]["last_source"], "test")
        self.assertTrue(state["health"]["ready_for_motion"])


if __name__ == "__main__":
    unittest.main()
