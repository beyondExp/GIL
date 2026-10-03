from __future__ import annotations

from gil.memory.schema import neo4j_ddl


def test_neo4j_ddl_contains_core_constraints() -> None:
    ddl = neo4j_ddl()
    joined = "\n".join(ddl).lower()
    assert "constraint robot_id" in joined
    assert "constraint obs_id" in joined
    assert "constraint object_key" in joined
    assert "constraint map_id" in joined
    assert "constraint keyframe_id" in joined

