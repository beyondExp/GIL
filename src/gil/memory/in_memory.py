from __future__ import annotations

import uuid
from dataclasses import asdict

from gil.memory.spatial import SpatialMemory
from gil.memory.types import BeliefState, SpatialRelation, TrackedObject


class InMemorySpatialMemory(SpatialMemory):
    """
    A toy in-process backend for unit tests and early integration.
    """

    def __init__(self) -> None:
        self._belief_by_robot: dict[str, BeliefState] = {}
        self._belief_rows: list[dict] = []
        self._object_rows: list[dict] = []
        self._relation_rows: list[dict] = []
        self._schema_ensured = False

    def ensure_schema(self) -> None:
        self._schema_ensured = True

    def start_episode(
        self,
        *,
        robot_id: str,
        stage_id: str,
        started_at_s: float,
        meta: dict | None = None,
    ) -> str:
        episode_id = str(uuid.uuid4())
        self._belief_rows.append(
            {"type": "episode_start", "episode_id": episode_id, "robot_id": robot_id, "stage_id": stage_id, "started_at_s": started_at_s, "meta": meta or {}}
        )
        return episode_id

    def end_episode(self, *, episode_id: str, ended_at_s: float, outcome: dict | None = None) -> None:
        self._belief_rows.append(
            {"type": "episode_end", "episode_id": episode_id, "ended_at_s": ended_at_s, "outcome": outcome or {}}
        )

    def record_belief(self, belief: BeliefState, *, episode_id: str | None = None) -> str:
        obs_id = str(uuid.uuid4())
        self._belief_by_robot[belief.robot_id] = belief
        row = {"obs_id": obs_id, "episode_id": episode_id, **asdict(belief)}
        self._belief_rows.append(row)
        return obs_id

    def record_objects(self, objects: list[TrackedObject], *, episode_id: str | None = None) -> int:
        for o in objects:
            row = {"episode_id": episode_id, **asdict(o)}
            self._object_rows.append(row)
        return len(objects)

    def record_relations(self, relations: list[SpatialRelation], *, episode_id: str | None = None) -> int:
        for r in relations:
            row = {"episode_id": episode_id, **asdict(r)}
            self._relation_rows.append(row)
        return len(relations)

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
        self._belief_rows.append(
            {
                "type": "keyframe",
                "robot_id": robot_id,
                "episode_id": episode_id,
                "keyframe_id": keyframe_id,
                "observed_at_s": observed_at_s,
                "frame": frame,
                "pose": pose,
                "intrinsics": intrinsics or {},
                "rgb": rgb or {},
                "depth": depth or {},
                "extra": extra or {},
            }
        )

    def upsert_map_artifact(self, *, robot_id: str, episode_id: str | None, map_artifact: dict) -> None:
        self._belief_rows.append(
            {"type": "map_artifact", "robot_id": robot_id, "episode_id": episode_id, "map_artifact": dict(map_artifact)}
        )

    def last_belief(self, *, robot_id: str) -> BeliefState | None:
        return self._belief_by_robot.get(robot_id)

    def record_competence(
        self,
        *,
        robot_id: str,
        env_key: str,
        stage_id: str,
        status: str,
    ) -> None:
        self._belief_rows.append(
            {
                "type": "competence",
                "robot_id": robot_id,
                "env_key": env_key,
                "stage_id": stage_id,
                "status": status,
            }
        )

