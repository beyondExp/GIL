from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

CONTRACTS_VERSION = "2026-08-30"


class ContractsMeta(BaseModel):
    version: str = CONTRACTS_VERSION
    product: str = "gil"


class DetectedObject(BaseModel):
    label: str
    x: float
    y: float
    z: float = 0.0
    occupied: bool = True


class BasePose(BaseModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    yaw: float = 0.0


class WorldAnchor(BaseModel):
    """Anchor mapping local robot XY into a global (lat/lon/height) frame.

    This is for inspection/visualization. Robotics planning should use its own
    estimator/map; do not treat this as metric ground truth.
    """

    lon: float
    lat: float
    height: float = 0.0
    source: str = "unknown"
    at_ms: int = 0


class CameraIntrinsics(BaseModel):
    """Camera intrinsics in pixel units (best-effort across backends)."""

    width: int | None = None
    height: int | None = None
    fx: float | None = None
    fy: float | None = None
    cx: float | None = None
    cy: float | None = None
    model: str | None = None
    dist: list[float] | None = None


class FrameTransform(BaseModel):
    """Rigid transform (parent -> child).

    Translation is meters, quaternion is (x,y,z,w).
    """

    parent: str
    child: str
    t: tuple[float, float, float] = (0.0, 0.0, 0.0)
    q: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    at_ms: int = 0


class FrameGraph(BaseModel):
    """Minimal frame graph for robot + sensors."""

    world: str = "world"
    base: str = "base"
    frames: list[FrameTransform] = Field(default_factory=list)
    cameras: dict[str, CameraIntrinsics] = Field(default_factory=dict)


class ObservationEnvelope(BaseModel):
    """Canonical observation envelope (sim or hardware).

    This intentionally mirrors `gil_controls.get_observation()` so every consumer
    (agent core + inspector UI) can share one stable schema.
    """

    meta: ContractsMeta = Field(default_factory=ContractsMeta)
    morphology: Literal["humanoid", "arm", "unknown"] = "unknown"
    state: dict[str, Any] = Field(default_factory=dict)
    image: str | None = None  # base64 JPEG/PNG (backend-dependent)
    images: dict[str, Any] = Field(default_factory=dict)
    objects: list[Any] = Field(default_factory=list)
    anchor: WorldAnchor | None = None
    frame_graph: FrameGraph | None = None

    def base_pose(self) -> BasePose:
        base = (self.state.get("base") or {}) if isinstance(self.state, dict) else {}
        return BasePose(
            x=float(base.get("x", 0.0) or 0.0),
            y=float(base.get("y", 0.0) or 0.0),
            z=float(base.get("z", 0.0) or 0.0),
            yaw=float(base.get("yaw", 0.0) or 0.0),
        )


class SceneBeliefSnapshot(BaseModel):
    """Belief snapshot as used by the dream/gate loop (occupancy + objects)."""

    calibrated: bool = False
    coverage: float = 0.0
    objects: list[DetectedObject] = Field(default_factory=list)
    occupied_cells: int = 0
    occupied: list[tuple[int, int]] | None = None  # optional heavy payload


class GateSnapshot(BaseModel):
    ok: bool = False
    reason: str = ""
    gate_id: str = ""
    scores: dict[str, Any] = Field(default_factory=dict)


class DreamPlanSnapshot(BaseModel):
    kept: int = 0
    preview_commands: list[dict[str, Any]] = Field(default_factory=list)
    predicted_path_local: list[dict[str, float]] = Field(default_factory=list)  # [{x,y,z}, ...]


class InspectorSnapshot(BaseModel):
    meta: ContractsMeta = Field(default_factory=ContractsMeta)
    observation: ObservationEnvelope
    belief: SceneBeliefSnapshot = Field(default_factory=SceneBeliefSnapshot)
    gate: GateSnapshot = Field(default_factory=GateSnapshot)
    dream: DreamPlanSnapshot = Field(default_factory=DreamPlanSnapshot)
    director: dict[str, Any] = Field(default_factory=dict)
    controls: dict[str, Any] = Field(default_factory=dict)

