from __future__ import annotations

import json

from gil.memory.schema import neo4j_ddl


def main() -> None:
    """
    Print the Neo4j DDL statements as JSON for review/auditing.
    """
    print(json.dumps({"ddl": neo4j_ddl()}, indent=2))


if __name__ == "__main__":
    main()

