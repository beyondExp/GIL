from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


LocomotionMode = Literal["external", "goal"]


@dataclass(frozen=True)
class BasePose:
    x: float
    y: float
    z: float
    yaw: float
    vx: float = 0.0
    vy: float = 0.0
    wz: float = 0.0
    frame: str = "world"


@dataclass(frozen=True)
class RobotState:
    robot_id: str
    kind: str  # "humanoid", "arm", ...
    base: BasePose | None = None
    backend: str = ""
    mode: str = ""
    motion_enabled: bool = False
    observed_at_s: float = 0.0
    health: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LocomotionCaps:
    """Capabilities of the underlying locomotion stack (manufacturer, Isaac, etc.)."""

    supports_cmd_vel: bool = True
    supports_goal_xy: bool = False
    supports_mode_switch: bool = False
    supports_reset_episode: bool = False


@dataclass(frozen=True)
class CmdVelCalibration:
    """
    Normalization for cmd_vel semantics.

    - If a robot/controller interprets `wz` differently (deg/s, scaled, etc.), `wz_scale` corrects it.
    - If linear commands are scaled by the controller, `vx_scale` corrects it.
    """

    vx_scale: float = 1.0
    vy_scale: float = 1.0
    wz_scale: float = 1.0


@dataclass(frozen=True)
class SafeEnvelope:
    """Hard safety clamps applied by the action compiler (agent-independent)."""

    max_vx: float = 0.2
    max_vy: float = 0.0
    max_wz: float = 0.35
    max_drive_duration_s: float = 8.0
    min_upright_base_z_m: float = 0.70


class RobotAdapter(Protocol):
    """
    Robot-agnostic adapter.

    The whole point is: *agents do not need to know the robot SDK quirks*.
    Adapters + compilers handle normalization, clamping, gating, and fallbacks.
    """

    robot_id: str

    def capabilities(self) -> LocomotionCaps: ...

    async def preflight(self) -> dict[str, Any]: ...

    async def heartbeat(self, *, source: str) -> dict[str, Any]: ...

    async def get_state(self) -> RobotState: ...

    async def set_mode(self, mode: LocomotionMode) -> dict[str, Any]: ...

    async def enable_motion(self, *, reason: str) -> dict[str, Any]: ...

    async def disable_motion(self, *, reason: str) -> dict[str, Any]: ...

    async def stop(self, *, reason: str) -> dict[str, Any]: ...

    async def reset_episode(self, *, x: float | None = None, y: float | None = None, yaw: float | None = None) -> dict[str, Any]: ...

    async def drive_cmd_vel(
        self,
        *,
        vx: float,
        vy: float,
        wz: float,
        duration_s: float,
        reason: str,
        preview: bool = False,
    ) -> dict[str, Any]: ...

    async def set_goal_xy(self, *, x: float, y: float, reason: str) -> dict[str, Any]: ...

