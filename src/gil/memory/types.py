from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Frame = Literal["world", "map", "odom", "base"]


@dataclass(frozen=True)
class Pose:
    x: float
    y: float
    z: float | None = None
    yaw: float | None = None
    frame: Frame = "world"
    # Optional covariance (flattened row-major); leave None for toy state.
    cov: list[float] | None = None


@dataclass(frozen=True)
class BeliefState:
    """
    Minimal (toy) belief state the rest of the GI layer can standardize on.

    Over time, this should expand to include:
    - timestamp alignment / time sync status
    - uncertainty (covariances)
    - contact/stability estimates for legged robots
    - semantic context summaries
    """

    robot_id: str
    pose: Pose
    observed_at_s: float
    source: str = "unknown"
    health: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrackedObject:
    robot_id: str
    object_id: str
    label: str = ""
    pose: Pose | None = None
    confidence: float | None = None
    observed_at_s: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SpatialRelation:
    robot_id: str
    subject_id: str
    predicate: str
    object_id: str
    score: float | None = None
    observed_at_s: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

