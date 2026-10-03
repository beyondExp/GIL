from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from gil.core.contracts import BasePose, ObservationEnvelope
from gil.world.twin import UnicycleTwin

OccupancyFn = Callable[[float, float], bool]


class SimBackend(Protocol):
    """A simulation/surrogate backend usable by dream rollouts.

    The critical product invariant: *dream* and *execute* consume the same
    `ObservationEnvelope` shape, regardless of whether the source is Isaac,
    replay logs, or a fast surrogate.
    """

    backend_name: str

    def reset(self, *, seed: int | None = None, scenario: str | None = None) -> None: ...

    def sense(self) -> ObservationEnvelope: ...

    def step(self, *, vx: float, vy: float = 0.0, wz: float = 0.0, dt: float = 0.25) -> ObservationEnvelope: ...

    def close(self) -> None: ...


@dataclass
class KinematicTwinBackend:
    """Fast surrogate backend: unicycle twin against an occupancy function."""

    occupancy: OccupancyFn | None = None
    pose: BasePose = BasePose()
    morphology: str = "humanoid"
    backend_name: str = "kinematic_twin"

    def __post_init__(self) -> None:
        self._twin = UnicycleTwin()
        self._twin.pose.x = float(self.pose.x)
        self._twin.pose.y = float(self.pose.y)
        self._twin.pose.yaw = float(self.pose.yaw)

    def reset(self, *, seed: int | None = None, scenario: str | None = None) -> None:
        _ = seed, scenario
        self._twin = UnicycleTwin()
        self._twin.pose.x = float(self.pose.x)
        self._twin.pose.y = float(self.pose.y)
        self._twin.pose.yaw = float(self.pose.yaw)

    def _obs(self) -> ObservationEnvelope:
        return ObservationEnvelope(
            morphology="humanoid" if self.morphology == "humanoid" else "unknown",
            state={"base": {"x": self._twin.pose.x, "y": self._twin.pose.y, "z": 0.0, "yaw": self._twin.pose.yaw}},
            image=None,
            images={},
            objects=[],
        )

    def sense(self) -> ObservationEnvelope:
        return self._obs()

    def step(self, *, vx: float, vy: float = 0.0, wz: float = 0.0, dt: float = 0.25) -> ObservationEnvelope:
        self._twin.step(float(vx), float(vy), float(wz), float(dt), occupancy=self.occupancy)
        return self._obs()

    def close(self) -> None:
        return


@dataclass
class ReplayBackend:
    """Replay backend: deterministic observation stream from a JSONL file.

    File format: one JSON object per line, each object is an ObservationEnvelope-compatible dict.
    """

    path: str
    backend_name: str = "replay"

    def __post_init__(self) -> None:
        self._items: list[dict[str, Any]] = []
        p = Path(self.path)
        if p.exists():
            for ln in p.read_text(encoding="utf-8").splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    self._items.append(json.loads(ln))
                except Exception:
                    continue
        self._idx = 0

    def reset(self, *, seed: int | None = None, scenario: str | None = None) -> None:
        _ = seed, scenario
        self._idx = 0

    def sense(self) -> ObservationEnvelope:
        if not self._items:
            return ObservationEnvelope(morphology="unknown", state={}, image=None, images={}, objects=[])
        item = self._items[min(self._idx, len(self._items) - 1)]
        return ObservationEnvelope.model_validate(item)

    def step(self, *, vx: float, vy: float = 0.0, wz: float = 0.0, dt: float = 0.25) -> ObservationEnvelope:
        _ = vx, vy, wz, dt
        self._idx = min(self._idx + 1, max(0, len(self._items) - 1))
        return self.sense()

    def close(self) -> None:
        return

