from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the Isaac-identical H1 maze JSON for the Three.js viewer.")
    parser.add_argument(
        "--out",
        default=str(ROOT / "gil_frontend" / "src" / "scenes" / "h1_maze.json"),
    )
    args = parser.parse_args()

    import sys

    sys.path.insert(0, str(ROOT / "src"))
    from gil.world.maze3d import generate_maze

    maze = generate_maze()
    path = maze.write_json(args.out)
    public = ROOT / "gil_frontend" / "public" / "scenes" / "h1_maze.json"
    maze.write_json(public)
    print(f"wrote {path} ({len(maze.walls)} walls)")
    print(f"wrote {public}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
