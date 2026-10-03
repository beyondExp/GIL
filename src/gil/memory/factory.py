from __future__ import annotations

from gil.core.config import GilSettings
from gil.memory.in_memory import InMemorySpatialMemory
from gil.memory.spatial import SpatialMemory
from gil.memory.neo4j_memory import Neo4jSpatialMemory


def make_spatial_memory(settings: GilSettings | None = None) -> SpatialMemory:
    """
    Pick a memory backend.

    Default is in-memory so the stack works without external services.
    """
    s = settings or GilSettings()
    if bool(getattr(s, "neo4j_enabled", False)):
        mem = Neo4jSpatialMemory(
            uri=str(getattr(s, "neo4j_uri", "neo4j://127.0.0.1:7687")),
            user=str(getattr(s, "neo4j_user", "neo4j")),
            password=str(getattr(s, "neo4j_password", "")),
            database=str(getattr(s, "neo4j_database", "neo4j")),
        )
        return mem
    return InMemorySpatialMemory()

