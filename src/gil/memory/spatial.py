from __future__ import annotations

from typing import Protocol

from gil.memory.types import BeliefState, SpatialRelation, TrackedObject


class SpatialMemory(Protocol):
    """
    Industry-standard GI layers need a persistent, queryable spatial/semantic memory.

    This protocol defines the minimum ingestion/query surface for the rest of the system.
    """

    def ensure_schema(self) -> None:
        """Create constraints/indexes if needed (idempotent)."""

    def start_episode(
        self,
        *,
        robot_id: str,
        stage_id: str,
        started_at_s: float,
        meta: dict | None = None,
    ) -> str:
        """Create an episode and return episode_id."""

    def end_episode(self, *, episode_id: str, ended_at_s: float, outcome: dict | None = None) -> None:
        """Mark an episode complete."""

    def record_belief(self, belief: BeliefState, *, episode_id: str | None = None) -> str:
        """Persist a belief snapshot. Returns observation ID."""

    def record_objects(self, objects: list[TrackedObject], *, episode_id: str | None = None) -> int:
        """Persist object observations. Returns count written."""

    def record_relations(self, relations: list[SpatialRelation], *, episode_id: str | None = None) -> int:
        """Persist semantic/spatial relations. Returns count written."""

    def record_keyframe(
        self,
        *,
        robot_id: str,
        episode_id: str | None,
        keyframe_id: str,
        observed_at_s: float,
        frame: str,
        pose: dict,
        intrinsics: dict | None = None,
        rgb: dict | None = None,
        depth: dict | None = None,
        extra: dict | None = None,
    ) -> None:
        """Store a keyframe pointer (RGB/Depth URIs live outside Neo4j)."""

    def upsert_map_artifact(self, *, robot_id: str, episode_id: str | None, map_artifact: dict) -> None:
        """Store/update a map artifact pointer (TSDF/features live outside Neo4j)."""

    def last_belief(self, *, robot_id: str) -> BeliefState | None:
        """Return the last belief snapshot if available."""

