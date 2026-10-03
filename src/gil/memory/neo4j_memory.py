from __future__ import annotations

import os
import uuid
from dataclasses import asdict
from typing import Any

from gil.memory.spatial import SpatialMemory
from gil.memory.types import BeliefState, Pose, SpatialRelation, TrackedObject
from gil.memory.schema import neo4j_ddl


def _optional_import_neo4j():
    try:
        import neo4j  # type: ignore

        return neo4j
    except Exception:
        return None


class Neo4jSpatialMemory(SpatialMemory):
    """
    Neo4j-backed spatial + semantic memory (scene graph).

    Node model (minimal, evolve over time):
    - (:Robot {robot_id})
    - (:Observation {obs_id, robot_id, observed_at_s, source, x,y,z,yaw, frame})
    - (:Object {robot_id, object_id, label})
    Edges:
    - (Robot)-[:HAS_OBSERVATION]->(Observation)
    - (Observation)-[:SEES {x,y,z,yaw,confidence,observed_at_s}]->(Object)
    - (Object)-[:REL {predicate,score,observed_at_s}]->(Object)
    """

    def __init__(
        self,
        *,
        uri: str,
        user: str,
        password: str,
        database: str = "neo4j",
        app_name: str = "gil",
    ) -> None:
        neo4j = _optional_import_neo4j()
        if neo4j is None:
            raise RuntimeError("neo4j driver not installed. Install with `pip install 'gil[memory]'` or `pip install neo4j`.")
        self._neo4j = neo4j
        self._database = database
        # Neo4j python driver accepts neo4j:// and bolt:// URIs.
        self._driver = neo4j.GraphDatabase.driver(uri, auth=(user, password), user_agent=app_name)

    @classmethod
    def from_env(cls) -> "Neo4jSpatialMemory":
        uri = os.getenv("GIL_NEO4J_URI", "neo4j://127.0.0.1:7687")
        user = os.getenv("GIL_NEO4J_USER", "neo4j")
        password = os.getenv("GIL_NEO4J_PASSWORD", "")
        database = os.getenv("GIL_NEO4J_DATABASE", "neo4j")
        return cls(uri=uri, user=user, password=password, database=database)

    def close(self) -> None:
        try:
            self._driver.close()
        except Exception:
            pass

    def ensure_schema(self) -> None:
        stmts = neo4j_ddl()
        with self._driver.session(database=self._database) as s:
            for q in stmts:
                s.run(q)

    def start_episode(
        self,
        *,
        robot_id: str,
        stage_id: str,
        started_at_s: float,
        meta: dict | None = None,
    ) -> str:
        episode_id = str(uuid.uuid4())
        q = """
        MERGE (r:Robot {robot_id: $robot_id})
        CREATE (e:Episode {
          episode_id: $episode_id,
          robot_id: $robot_id,
          stage_id: $stage_id,
          started_at_s: $started_at_s,
          meta: $meta
        })
        MERGE (r)-[:HAS_EPISODE]->(e)
        RETURN e.episode_id AS episode_id
        """
        with self._driver.session(database=self._database) as s:
            rec = s.run(
                q,
                robot_id=str(robot_id),
                episode_id=episode_id,
                stage_id=str(stage_id),
                started_at_s=float(started_at_s),
                meta=dict(meta or {}),
            ).single()
            return str(rec["episode_id"]) if rec and "episode_id" in rec else episode_id

    def end_episode(self, *, episode_id: str, ended_at_s: float, outcome: dict | None = None) -> None:
        q = """
        MATCH (e:Episode {episode_id: $episode_id})
        SET e.ended_at_s = $ended_at_s
        SET e.outcome = $outcome
        RETURN e.episode_id AS episode_id
        """
        with self._driver.session(database=self._database) as s:
            s.run(q, episode_id=str(episode_id), ended_at_s=float(ended_at_s), outcome=dict(outcome or {}))

    def record_belief(self, belief: BeliefState, *, episode_id: str | None = None) -> str:
        obs_id = str(uuid.uuid4())
        p = belief.pose
        params = {
            "robot_id": belief.robot_id,
            "obs_id": obs_id,
            "episode_id": episode_id,
            "observed_at_s": float(belief.observed_at_s),
            "source": str(belief.source or ""),
            "x": float(p.x),
            "y": float(p.y),
            "z": None if p.z is None else float(p.z),
            "yaw": None if p.yaw is None else float(p.yaw),
            "frame": str(p.frame),
            "health": dict(belief.health or {}),
        }
        q = """
        MERGE (r:Robot {robot_id: $robot_id})
        OPTIONAL MATCH (e:Episode {episode_id: $episode_id})
        CREATE (o:Observation {
          obs_id: $obs_id,
          robot_id: $robot_id,
          episode_id: $episode_id,
          observed_at_s: $observed_at_s,
          source: $source,
          x: $x, y: $y, z: $z, yaw: $yaw,
          frame: $frame,
          health: $health
        })
        MERGE (r)-[:HAS_OBSERVATION]->(o)
        FOREACH (_ IN CASE WHEN e IS NULL THEN [] ELSE [1] END | MERGE (e)-[:HAS_OBSERVATION]->(o))
        RETURN o.obs_id AS obs_id
        """
        with self._driver.session(database=self._database) as s:
            rec = s.run(q, **params).single()
            return str(rec["obs_id"]) if rec and "obs_id" in rec else obs_id

    def record_objects(self, objects: list[TrackedObject], *, episode_id: str | None = None) -> int:
        if not objects:
            return 0
        q = """
        MERGE (r:Robot {robot_id: $robot_id})
        MERGE (obj:Object {robot_id: $robot_id, object_id: $object_id})
        SET obj.label = $label
        SET obj.last_seen_s = $observed_at_s
        SET obj.confidence = $confidence
        SET obj.extra = $extra
        WITH r, obj
        MATCH (o:Observation {robot_id: $robot_id})
        WITH r, obj, o
        ORDER BY o.observed_at_s DESC
        LIMIT 1
        MERGE (o)-[e:SEES]->(obj)
        SET e.x = $x, e.y = $y, e.z = $z, e.yaw = $yaw
        SET e.confidence = $confidence
        SET e.observed_at_s = $observed_at_s
        RETURN obj.object_id AS object_id
        """
        wrote = 0
        with self._driver.session(database=self._database) as s:
            for o in objects:
                pose = o.pose or Pose(x=0.0, y=0.0)
                params = {
                    "robot_id": o.robot_id,
                    "episode_id": episode_id,
                    "object_id": o.object_id,
                    "label": o.label,
                    "observed_at_s": float(o.observed_at_s),
                    "confidence": None if o.confidence is None else float(o.confidence),
                    "extra": dict(o.extra or {}),
                    "x": float(pose.x),
                    "y": float(pose.y),
                    "z": None if pose.z is None else float(pose.z),
                    "yaw": None if pose.yaw is None else float(pose.yaw),
                }
                s.run(q, **params)
                wrote += 1
        return wrote

    def record_relations(self, relations: list[SpatialRelation], *, episode_id: str | None = None) -> int:
        if not relations:
            return 0
        q = """
        MERGE (a:Object {robot_id: $robot_id, object_id: $subject_id})
        MERGE (b:Object {robot_id: $robot_id, object_id: $object_id})
        MERGE (a)-[r:REL {predicate: $predicate}]->(b)
        SET r.score = $score
        SET r.observed_at_s = $observed_at_s
        SET r.extra = $extra
        RETURN r.predicate AS predicate
        """
        wrote = 0
        with self._driver.session(database=self._database) as s:
            for rel in relations:
                params = {
                    "robot_id": rel.robot_id,
                    "episode_id": episode_id,
                    "subject_id": rel.subject_id,
                    "object_id": rel.object_id,
                    "predicate": rel.predicate,
                    "score": None if rel.score is None else float(rel.score),
                    "observed_at_s": float(rel.observed_at_s),
                    "extra": dict(rel.extra or {}),
                }
                s.run(q, **params)
                wrote += 1
        return wrote

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
        q = """
        MERGE (r:Robot {robot_id: $robot_id})
        MERGE (k:Keyframe {robot_id: $robot_id, keyframe_id: $keyframe_id})
        SET k.observed_at_s = $observed_at_s
        SET k.frame = $frame
        SET k.pose = $pose
        SET k.intrinsics = $intrinsics
        SET k.rgb = $rgb
        SET k.depth = $depth
        SET k.extra = $extra
        WITH r, k
        OPTIONAL MATCH (e:Episode {episode_id: $episode_id})
        FOREACH (_ IN CASE WHEN e IS NULL THEN [] ELSE [1] END | MERGE (e)-[:HAS_KEYFRAME]->(k))
        RETURN k.keyframe_id AS keyframe_id
        """
        with self._driver.session(database=self._database) as s:
            s.run(
                q,
                robot_id=str(robot_id),
                episode_id=(str(episode_id) if episode_id else None),
                keyframe_id=str(keyframe_id),
                observed_at_s=float(observed_at_s),
                frame=str(frame),
                pose=dict(pose or {}),
                intrinsics=dict(intrinsics or {}),
                rgb=dict(rgb or {}),
                depth=dict(depth or {}),
                extra=dict(extra or {}),
            )

    def upsert_map_artifact(self, *, robot_id: str, episode_id: str | None, map_artifact: dict) -> None:
        """
        Map artifact pointers live outside Neo4j. This stores only metadata + blob pointer.

        Required keys in map_artifact: map_id, kind
        Optional keys: created_at_s, frame, voxel_size_m, truncation_m, bounds_min_xyz, bounds_max_xyz,
                       feature_model, feature_dim, feature_aggregation, blob (dict with uri/storage/content_id/extra)
        """
        map_id = str(map_artifact.get("map_id") or "")
        kind = str(map_artifact.get("kind") or "")
        if not map_id or not kind:
            raise ValueError("map_artifact must include map_id and kind")
        blob = dict(map_artifact.get("blob") or {})
        q = """
        MERGE (r:Robot {robot_id: $robot_id})
        MERGE (m:MapArtifact {robot_id: $robot_id, map_id: $map_id})
        SET m.kind = $kind
        SET m.created_at_s = $created_at_s
        SET m.frame = $frame
        SET m.voxel_size_m = $voxel_size_m
        SET m.truncation_m = $truncation_m
        SET m.bounds_min_xyz = $bounds_min_xyz
        SET m.bounds_max_xyz = $bounds_max_xyz
        SET m.feature_model = $feature_model
        SET m.feature_dim = $feature_dim
        SET m.feature_aggregation = $feature_aggregation
        SET m.blob = $blob
        SET m.extra = $extra
        WITH r, m
        OPTIONAL MATCH (e:Episode {episode_id: $episode_id})
        FOREACH (_ IN CASE WHEN e IS NULL THEN [] ELSE [1] END | MERGE (e)-[:HAS_MAP]->(m))
        MERGE (r)-[:HAS_MAP]->(m)
        RETURN m.map_id AS map_id
        """
        with self._driver.session(database=self._database) as s:
            s.run(
                q,
                robot_id=str(robot_id),
                episode_id=(str(episode_id) if episode_id else None),
                map_id=map_id,
                kind=kind,
                created_at_s=float(map_artifact.get("created_at_s") or 0.0),
                frame=str(map_artifact.get("frame") or "world"),
                voxel_size_m=map_artifact.get("voxel_size_m", None),
                truncation_m=map_artifact.get("truncation_m", None),
                bounds_min_xyz=map_artifact.get("bounds_min_xyz", None),
                bounds_max_xyz=map_artifact.get("bounds_max_xyz", None),
                feature_model=str(map_artifact.get("feature_model") or ""),
                feature_dim=map_artifact.get("feature_dim", None),
                feature_aggregation=str(map_artifact.get("feature_aggregation") or "overwrite"),
                blob=blob,
                extra=dict(map_artifact.get("extra") or {}),
            )

    def last_belief(self, *, robot_id: str) -> BeliefState | None:
        q = """
        MATCH (o:Observation {robot_id: $robot_id})
        RETURN o
        ORDER BY o.observed_at_s DESC
        LIMIT 1
        """
        with self._driver.session(database=self._database) as s:
            rec = s.run(q, robot_id=robot_id).single()
            if not rec:
                return None
            o = rec["o"]
            pose = Pose(
                x=float(o.get("x", 0.0)),
                y=float(o.get("y", 0.0)),
                z=o.get("z", None),
                yaw=o.get("yaw", None),
                frame=str(o.get("frame", "world")),
            )
            return BeliefState(
                robot_id=str(o.get("robot_id", robot_id)),
                pose=pose,
                observed_at_s=float(o.get("observed_at_s", 0.0)),
                source=str(o.get("source", "")),
                health=dict(o.get("health") or {}),
            )

    def dump_debug_counts(self) -> dict[str, int]:
        q = """
        MATCH (r:Robot) WITH count(r) AS robots
        MATCH (o:Observation) WITH robots, count(o) AS observations
        MATCH (x:Object) WITH robots, observations, count(x) AS objects
        RETURN robots, observations, objects
        """
        with self._driver.session(database=self._database) as s:
            rec = s.run(q).single()
            if not rec:
                return {"robots": 0, "observations": 0, "objects": 0}
            return {
                "robots": int(rec["robots"]),
                "observations": int(rec["observations"]),
                "objects": int(rec["objects"]),
            }

