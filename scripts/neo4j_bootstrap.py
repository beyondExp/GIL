from __future__ import annotations

import argparse

from gil.memory.neo4j_memory import Neo4jSpatialMemory


def main() -> None:
    ap = argparse.ArgumentParser(description="Initialize Neo4j constraints/indexes for GIL spatial memory.")
    ap.add_argument("--uri", default="", help="Neo4j URI (default from env GIL_NEO4J_URI)")
    ap.add_argument("--user", default="", help="Neo4j username (default from env GIL_NEO4J_USER)")
    ap.add_argument("--password", default="", help="Neo4j password (default from env GIL_NEO4J_PASSWORD)")
    ap.add_argument("--database", default="", help="Neo4j database (default from env GIL_NEO4J_DATABASE)")
    args = ap.parse_args()

    if args.uri or args.user or args.password or args.database:
        mem = Neo4jSpatialMemory(
            uri=args.uri or "neo4j://127.0.0.1:7687",
            user=args.user or "neo4j",
            password=args.password or "",
            database=args.database or "neo4j",
        )
    else:
        mem = Neo4jSpatialMemory.from_env()

    try:
        mem.ensure_schema()
        print("OK: Neo4j schema ensured.")
        try:
            print("Counts:", mem.dump_debug_counts())
        except Exception:
            pass
    finally:
        mem.close()


if __name__ == "__main__":
    main()

