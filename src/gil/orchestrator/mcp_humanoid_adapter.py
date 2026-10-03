from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from gil.core.robot_adapter import BasePose, LocomotionCaps, LocomotionMode, RobotAdapter, RobotState


def _optional_import_mcp():
    try:
        from mcp.client.session import ClientSession  # type: ignore
        from mcp.client.streamable_http import streamablehttp_client  # type: ignore

        return ClientSession, streamablehttp_client
    except Exception:
        return None


@dataclass
class McpHumanoidAdapter(RobotAdapter):
    """
    Adapter for the `gil_controls` MCP server humanoid tools.

    This is *not* Unitree-H1-specific. It depends only on the humanoid tool contract:
    - get_robot_state_for(robot_kind="humanoid")
    - drive_humanoid(...)
    - set_humanoid_goal(x,y)
    - set_humanoid_mode(mode)
    - enable/disable/stop/reset/heartbeat/preflight
    """

    url: str = "http://127.0.0.1:6769/mcp/"
    robot_id: str = "humanoid"
    robot_kind: str = "humanoid"

    # Session objects (created by `connect()`).
    _session: Any | None = None
    _cm_read: Any | None = None
    _cm_write: Any | None = None
    _cm_exit: Any | None = None

    def capabilities(self) -> LocomotionCaps:
        # Current `gil_controls` contract supports both cmd_vel driving and goal setting + mode switching.
        return LocomotionCaps(supports_cmd_vel=True, supports_goal_xy=True, supports_mode_switch=True, supports_reset_episode=True)

    async def connect(self) -> "McpHumanoidAdapter":
        m = _optional_import_mcp()
        if m is None:
            raise RuntimeError('MCP client not installed. Install with `pip install -e ".[servers]"`.')
        ClientSession, streamablehttp_client = m
        # MCP streamable HTTP can transiently stall during heavy sim startup; be generous.
        cm = streamablehttp_client(self.url, timeout=60, sse_read_timeout=60)
        read, write, _ = await cm.__aenter__()
        self._cm_exit = cm
        self._cm_read = read
        self._cm_write = write
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()
        return self

    async def aclose(self) -> None:
        # Close session + transport.
        try:
            if self._session is not None:
                try:
                    await self._session.__aexit__(None, None, None)
                except BaseException as e:
                    # Best-effort: connections can drop while closing.
                    if isinstance(e, asyncio.CancelledError):
                        pass
                    pass
        finally:
            self._session = None
            try:
                if self._cm_exit is not None:
                    try:
                        await self._cm_exit.__aexit__(None, None, None)
                    except BaseException as e:
                        if isinstance(e, asyncio.CancelledError):
                            pass
                        pass
            finally:
                self._cm_exit = None

    async def __aenter__(self) -> "McpHumanoidAdapter":
        return await self.connect()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def _call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if self._session is None:
            raise RuntimeError("MCP session not connected. Use `async with McpHumanoidAdapter(...): ...`")
        try:
            res = await self._session.call_tool(name, args)
        except BaseException as e:
            # Transient cancellation/transport issues: reconnect once and retry.
            if isinstance(e, asyncio.CancelledError):
                try:
                    await self.aclose()
                except BaseException:
                    pass
                await asyncio.sleep(0.15)
                await self.connect()
                res = await self._session.call_tool(name, args)
            else:
                raise
        txt = res.content[0].text if res.content else "{}"
        try:
            out = json.loads(txt)
            return out if isinstance(out, dict) else {"value": out}
        except Exception:
            return {"_raw": txt}

    async def preflight(self) -> dict[str, Any]:
        return await self._call("run_humanoid_preflight", {})

    async def heartbeat(self, *, source: str) -> dict[str, Any]:
        return await self._call("send_humanoid_heartbeat", {"source": str(source or "adapter")})

    async def get_state(self) -> RobotState:
        st = await self._call("get_robot_state_for", {"robot_kind": self.robot_kind})
        base = st.get("base") or {}
        base_pose: BasePose | None = None
        try:
            base_pose = BasePose(
                x=float(base.get("x", 0.0) or 0.0),
                y=float(base.get("y", 0.0) or 0.0),
                z=float(base.get("z", 0.0) or 0.0),
                yaw=float(base.get("yaw", 0.0) or 0.0),
                vx=float(base.get("vx", 0.0) or 0.0),
                vy=float(base.get("vy", 0.0) or 0.0),
                wz=float(base.get("wz", 0.0) or 0.0),
            )
        except Exception:
            base_pose = None
        health = {}
        try:
            health = dict(st.get("health") or {})
        except Exception:
            health = {}
        return RobotState(
            robot_id=self.robot_id,
            kind=str(st.get("kind") or self.robot_kind),
            base=base_pose,
            backend=str(st.get("backend") or ""),
            mode=str(st.get("mode") or ""),
            motion_enabled=bool(st.get("motion_enabled", False)),
            observed_at_s=float(time.time()),
            health=health,
        )

    async def set_mode(self, mode: LocomotionMode) -> dict[str, Any]:
        return await self._call("set_humanoid_mode", {"mode": str(mode)})

    async def enable_motion(self, *, reason: str) -> dict[str, Any]:
        return await self._call("enable_humanoid_motion", {"reason": str(reason or "")})

    async def disable_motion(self, *, reason: str) -> dict[str, Any]:
        return await self._call("disable_humanoid_motion", {"reason": str(reason or "")})

    async def stop(self, *, reason: str) -> dict[str, Any]:
        return await self._call("stop_humanoid_now", {"reason": str(reason or "")})

    async def reset_episode(self, *, x: float | None = None, y: float | None = None, yaw: float | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if x is not None:
            payload["x"] = float(x)
        if y is not None:
            payload["y"] = float(y)
        if yaw is not None:
            payload["yaw"] = float(yaw)
        return await self._call("reset_humanoid_episode", payload)

    async def drive_cmd_vel(
        self,
        *,
        vx: float,
        vy: float,
        wz: float,
        duration_s: float,
        reason: str,
        preview: bool = False,
    ) -> dict[str, Any]:
        tool = "preview_humanoid_cmd_vel" if bool(preview) else "drive_humanoid"
        return await self._call(
            tool,
            {
                "vx": float(vx),
                "vy": float(vy),
                "wz": float(wz),
                "duration_s": float(duration_s),
                "reason": str(reason or ""),
            },
        )

    async def set_goal_xy(self, *, x: float, y: float, reason: str) -> dict[str, Any]:
        # `gil_controls` currently ignores `reason`, but we keep it in the adapter contract for consistency.
        _ = reason
        return await self._call("set_humanoid_goal", {"x": float(x), "y": float(y)})

    # Escape hatch for non-core tools (e.g. ingest_world) without baking them into the adapter contract.
    async def call_tool(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._call(str(name), dict(args or {}))

