from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


# What kind of dense memory artifact this is (stored outside Neo4j).
MapArtifactKind = Literal[
    "tsdf",
    "feature_voxel",
    "mesh_vertices",
    "gaussian_splats",
    "occupancy_grid",
]


StorageKind = Literal["local_path", "http", "s3", "hf_cache", "other"]


@dataclass(frozen=True)
class BlobRef:
    """
    Pointer to a large artifact stored outside Neo4j.

    Neo4j stores only the pointer + metadata; the actual dense arrays live in a blob store
    (local disk, object storage, HF cache, etc.).
    """

    uri: str
    storage: StorageKind = "local_path"
    # Optional: content identity for caching (keep short; no huge hashes).
    content_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MapArtifactSpec:
    """
    Specification for a metric-semantic map artifact (mindmap-style).
    """

    robot_id: str
    map_id: str
    kind: MapArtifactKind
    frame: str = "world"
    created_at_s: float = 0.0

    # Geometry and representation metadata
    voxel_size_m: float | None = None
    truncation_m: float | None = None
    bounds_min_xyz: tuple[float, float, float] | None = None
    bounds_max_xyz: tuple[float, float, float] | None = None

    # Feature metadata (for semantic fusion)
    feature_model: str = ""
    feature_dim: int | None = None
    feature_aggregation: str = "overwrite"  # overwrite | ema | other

    # External storage pointer
    blob: BlobRef | None = None

    extra: dict[str, Any] = field(default_factory=dict)


def neo4j_ddl() -> list[str]:
    """
    Cypher DDL statements (idempotent) for the GI-layer memory schema.

    Design goal:
    - Neo4j holds symbolic + sparse geometry and pointers to dense artifacts.
    - Dense TSDF/feature grids are stored outside Neo4j (blob store).
    """
    return [
        # Core identities
        "CREATE CONSTRAINT robot_id IF NOT EXISTS FOR (r:Robot) REQUIRE r.robot_id IS UNIQUE",
        "CREATE CONSTRAINT episode_id IF NOT EXISTS FOR (e:Episode) REQUIRE e.episode_id IS UNIQUE",
        "CREATE CONSTRAINT obs_id IF NOT EXISTS FOR (o:Observation) REQUIRE o.obs_id IS UNIQUE",
        "CREATE CONSTRAINT object_key IF NOT EXISTS FOR (o:Object) REQUIRE (o.robot_id, o.object_id) IS NODE KEY",
        "CREATE CONSTRAINT map_id IF NOT EXISTS FOR (m:MapArtifact) REQUIRE (m.robot_id, m.map_id) IS NODE KEY",
        "CREATE CONSTRAINT keyframe_id IF NOT EXISTS FOR (k:Keyframe) REQUIRE (k.robot_id, k.keyframe_id) IS NODE KEY",
        # Useful indexes
        "CREATE INDEX obs_robot IF NOT EXISTS FOR (o:Observation) ON (o.robot_id)",
        "CREATE INDEX obs_time IF NOT EXISTS FOR (o:Observation) ON (o.observed_at_s)",
        "CREATE INDEX obj_label IF NOT EXISTS FOR (o:Object) ON (o.label)",
        "CREATE INDEX map_kind IF NOT EXISTS FOR (m:MapArtifact) ON (m.kind)",
        "CREATE INDEX map_time IF NOT EXISTS FOR (m:MapArtifact) ON (m.created_at_s)",
        "CREATE INDEX keyframe_time IF NOT EXISTS FOR (k:Keyframe) ON (k.observed_at_s)",
    ]

