from __future__ import annotations

import json
from typing import Any, Callable


class ControlBackend:
    backend_name = "unknown"
    backend_role = "unknown"

    async def send_command(self, command_data: dict[str, Any], robot_kind: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def preflight_check(self) -> dict[str, Any]:
        return {"ok": True, "backend": self.backend_name}


class WebSocketSimBackend(ControlBackend):
    backend_name = "sim_ws"
    backend_role = "simulation"

    def __init__(
        self,
        *,
        connected_clients: Callable[[], list[Any]],
        client_kind: Callable[[Any], str | None],
    ) -> None:
        self._connected_clients = connected_clients
        self._client_kind = client_kind

    async def send_command(self, command_data: dict[str, Any], robot_kind: str | None = None) -> dict[str, Any]:
        clients = list(self._connected_clients())
        if not clients:
            return {"success": False, "error": "No simulation client connected", "backend": self.backend_name}

        target_kind = (robot_kind or "").lower().strip() or None
        message = json.dumps(command_data)
        sent = 0

        if not target_kind:
            for ws in clients:
                await ws.send(message)
                sent += 1
            return {"success": True, "sent": sent, "backend": self.backend_name}

        for ws in clients:
            if self._client_kind(ws) == target_kind:
                await ws.send(message)
                sent += 1

        if sent == 0:
            for ws in clients:
                await ws.send(message)
                sent += 1
            return {
                "success": True,
                "sent": sent,
                "backend": self.backend_name,
                "warning": f"No websocket client tagged as '{target_kind}'. Broadcasted instead.",
            }
        return {"success": True, "sent": sent, "backend": self.backend_name}

    def preflight_check(self) -> dict[str, Any]:
        count = len(list(self._connected_clients()))
        return {
            "ok": count > 0,
            "backend": self.backend_name,
            "role": self.backend_role,
            "connected_clients": count,
        }


class Ros2ControlBackend(ControlBackend):
    def __init__(self, bridge: Any, *, hardware_mode: bool) -> None:
        self._bridge = bridge
        self.backend_name = "h1_hardware" if hardware_mode else "sim_ros2"
        self.backend_role = "hardware" if hardware_mode else "simulation"

    async def send_command(self, command_data: dict[str, Any], robot_kind: str | None = None) -> dict[str, Any]:
        result = self._bridge.send_command(command_data)
        if result.get("success"):
            result.setdefault("backend", self.backend_name)
        else:
            result["backend"] = self.backend_name
        return result

    def preflight_check(self) -> dict[str, Any]:
        return self._bridge.preflight_check(mode=self.backend_name)

