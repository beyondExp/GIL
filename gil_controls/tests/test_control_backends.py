import asyncio
import os
import sys
import unittest

import pytest

pytestmark = pytest.mark.phase1


SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from control_backends import WebSocketSimBackend  # noqa: E402


class _FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, message: str) -> None:
        self.messages.append(message)


class ControlBackendTests(unittest.TestCase):
    def test_sim_backend_preflight_requires_clients(self):
        backend = WebSocketSimBackend(connected_clients=lambda: [], client_kind=lambda ws: None)
        report = backend.preflight_check()
        self.assertFalse(report["ok"])

    def test_sim_backend_targets_matching_client_kind(self):
        arm_client = _FakeWebSocket()
        humanoid_client = _FakeWebSocket()
        backend = WebSocketSimBackend(
            connected_clients=lambda: [arm_client, humanoid_client],
            client_kind=lambda ws: "arm" if ws is arm_client else "humanoid",
        )
        result = asyncio.run(backend.send_command({"type": "cmd_vel"}, robot_kind="humanoid"))
        self.assertTrue(result["success"])
        self.assertEqual(len(arm_client.messages), 0)
        self.assertEqual(len(humanoid_client.messages), 1)


if __name__ == "__main__":
    unittest.main()
