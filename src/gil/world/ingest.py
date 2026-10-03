from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import re

from gil.world.maze3d import Maze3D, MazeSpec, generate_maze

Source = Literal["text", "image", "video", "huggingface", "hf", "occupancy"]


@dataclass
class WorldSpec:
    generator: str
    live: bool
    source: str
    content: str
    observation: dict[str, Any] = field(default_factory=dict)
    isaac_command: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    maze: Maze3D | None = None


def ingest_world(source: str, content: str) -> WorldSpec:
    """Turn agent input into a world the director can dream and Isaac can load.

    Live today: Isaac-identical maze, or an occupancy grid from Hugging Face / JSON.
    Stored, not live: image/video captures waiting for NuRec, text waiting for Marble.
    """
    kind = (source or "text").strip().lower()
    payload = (content or "").strip()
    if kind in {"huggingface", "hf", "occupancy"}:
        return _ingest_huggingface(payload)
    maze = _maze_from_text(payload) if kind == "text" else generate_maze()
    maze_obs = maze.to_observation()
    isaac_maze = {
        "type": "load_world",
        "generator": "maze_dfs",
        "seed": maze.spec.seed,
        "width": maze.spec.width,
        "height": maze.spec.height,
        "cell_size": maze.spec.cell_size,
        "rebuild": True,
    }

    if kind == "text":
        wants_gen = any(token in payload.lower() for token in ("warehouse", "kitchen", "apartment", "office", "street"))
        return WorldSpec(
            generator="maze_dfs" if not wants_gen else "marble",
            live=not wants_gen,
            source=kind,
            content=payload,
            observation=maze_obs,
            isaac_command=isaac_maze,
            maze=maze,
            note=(
                "Live Isaac maze (gil.maze_env seed 0). Marble text-to-USD is not wired; "
                "the prompt is kept so the agent can retry when Marble/Replicator is attached."
                if wants_gen
                else "Live Isaac maze copied for dreams. Isaac PhysX is the execute/preview body."
            ),
        )
    if kind in {"image", "video"}:
        return WorldSpec(
            generator="nurec",
            live=False,
            source=kind,
            content=payload,
            observation=maze_obs,
            isaac_command=isaac_maze,
            maze=maze,
            note=(
                "NuRec/3DGS reconstruction is not wired. Capture path stored. "
                "Dreams currently use the Isaac maze copy until a USD+collider exists."
            ),
        )
    raise ValueError("source must be text, image, video, or huggingface")


def _maze_from_text(payload: str) -> Maze3D:
    text = (payload or "").lower()
    seed_m = re.search(r"seed\s+(-?\d+)", text)
    seed = int(seed_m.group(1)) if seed_m else 0
    easy = "easy" in text or "small" in text or "3x3" in text or "tiny" in text
    width = 3 if easy else 9
    height = 3 if easy else 9
    cell = 1.6 if easy else 1.4
    return generate_maze(MazeSpec(width=width, height=height, seed=seed, cell_size=cell))


def _ingest_huggingface(payload: str) -> WorldSpec:
    from gil.world.hf_scenes import fetch_hf_occupancy

    maze, meta = fetch_hf_occupancy(payload)
    obs = maze.to_observation()
    obs["scene_kind"] = "hf_occupancy"
    obs["hf"] = {k: v for k, v in meta.items() if k != "maze"}
    return WorldSpec(
        generator="hf_occupancy",
        live=True,
        source="huggingface",
        content=payload,
        observation=obs,
        isaac_command={
            "type": "load_world",
            "generator": "hf_occupancy",
            "seed": -1,
            "repo": meta.get("repo"),
            "path": meta.get("path"),
        },
        maze=maze,
        note=(
            "Occupancy grid from Hugging Face (or a local JSON fixture) copied for dreams. "
            "Isaac still executes on PhysX; USD/NuRec reconstruction is not wired yet."
        ),
    )


def local_occupancy_fixture() -> Path:
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "hf_occupancy.json"
