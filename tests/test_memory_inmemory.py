from __future__ import annotations

from gil.memory.in_memory import InMemorySpatialMemory
from gil.memory.types import BeliefState, Pose, SpatialRelation, TrackedObject


def test_in_memory_spatial_memory_roundtrip() -> None:
    mem = InMemorySpatialMemory()
    mem.ensure_schema()

    ep = mem.start_episode(robot_id="r1", stage_id="stand_idle", started_at_s=1.0, meta={"a": 1})
    assert isinstance(ep, str) and ep

    belief = BeliefState(robot_id="r1", pose=Pose(x=1.0, y=2.0, yaw=0.3), observed_at_s=123.0, source="test")
    obs_id = mem.record_belief(belief, episode_id="e1")
    assert isinstance(obs_id, str) and obs_id
    last = mem.last_belief(robot_id="r1")
    assert last is not None
    assert last.pose.x == 1.0
    assert last.pose.y == 2.0

    n_obj = mem.record_objects(
        [
            TrackedObject(robot_id="r1", object_id="o1", label="cube", pose=Pose(x=0.0, y=0.0), observed_at_s=123.0),
            TrackedObject(robot_id="r1", object_id="o2", label="bin", pose=Pose(x=1.0, y=1.0), observed_at_s=123.0),
        ],
        episode_id="e1",
    )
    assert n_obj == 2

    n_rel = mem.record_relations(
        [SpatialRelation(robot_id="r1", subject_id="o1", predicate="inside", object_id="o2", score=0.9, observed_at_s=123.0)],
        episode_id="e1",
    )
    assert n_rel == 1

    mem.record_keyframe(
        robot_id="r1",
        episode_id=ep,
        keyframe_id="kf0",
        observed_at_s=2.0,
        frame="world",
        pose={"x": 0.0, "y": 0.0},
        rgb={"uri": "E:/x.jpg", "storage": "local_path"},
    )
    mem.upsert_map_artifact(robot_id="r1", episode_id=ep, map_artifact={"map_id": "m0", "kind": "occupancy_grid", "blob": {"uri": "E:/m.json"}})
    mem.record_competence(robot_id="r1", env_key="maze0", stage_id="stand_idle", status="passed")
    mem.end_episode(episode_id=ep, ended_at_s=3.0, outcome={"ok": True})
    assert any(r.get("type") == "competence" and r.get("status") == "passed" for r in mem._belief_rows)

