from __future__ import annotations

import asyncio
import os
import sys

import pytest

pytestmark = [pytest.mark.phase8, pytest.mark.integration]

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gil_controls", "src"))
if SRC not in sys.path:
    sys.path.insert(0, SRC)


def test_websocket_backend_moves_real_mock_sim():
    websockets = pytest.importorskip("websockets")
    from control_backends import WebSocketSimBackend
    from mock_sim_client import MockSim

    async def _run() -> None:
        clients: list = []
        kinds: dict = {}

        async def handler(ws):
            clients.append(ws)
            kinds[ws] = "humanoid"
            try:
                await ws.wait_closed()
            finally:
                if ws in clients:
                    clients.remove(ws)

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            sim = MockSim()
            task = asyncio.create_task(sim.run(f"ws://127.0.0.1:{port}"))
            try:
                for _ in range(80):
                    if clients:
                        break
                    await asyncio.sleep(0.025)
                assert clients, "MockSim did not connect to the test websocket server"
                backend = WebSocketSimBackend(
                    connected_clients=lambda: list(clients),
                    client_kind=lambda ws: kinds.get(ws),
                )
                pre = backend.preflight_check()
                assert pre["ok"] is True
                x0 = sim.base["x"]
                result = await backend.send_command(
                    {"type": "cmd_vel", "vx": 0.5, "vy": 0.0, "wz": 0.0, "dt": 0.4},
                    robot_kind="humanoid",
                )
                assert result["success"] is True
                for _ in range(40):
                    if sim.base["x"] > x0:
                        break
                    await asyncio.sleep(0.025)
                assert sim.base["x"] > x0
            finally:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    asyncio.run(_run())
