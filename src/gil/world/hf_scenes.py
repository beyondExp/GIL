from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from gil.world.maze3d import Maze3D
from gil.world.occupancy import maze_text_to_grid, occupancy_grid_to_maze, payload_to_grid

# Default to an ungated dataset so installs work without an HF token.
# (Some richer scene datasets like InteriorGS are gated and require authentication.)
DEFAULT_HF_REPO = "achinta3/2d_maze_dataset_with_grid_size"
DEFAULT_HF_FILE = "data/train-00000-of-00001.parquet"
CACHE_ENV = "GIL_HF_WORLD_CACHE"


def cache_dir() -> Path:
    raw = os.environ.get(CACHE_ENV) or "E:/GIL/hf-worlds"
    path = Path(raw)
    path.mkdir(parents=True, exist_ok=True)
    return path


def parse_hf_ref(content: str) -> tuple[str, str]:
    """`repo` or `repo:path/to/file.(json|png|parquet)` or a local filesystem path."""
    text = (content or "").strip()
    if not text:
        return DEFAULT_HF_REPO, DEFAULT_HF_FILE
    as_path = Path(text)
    if as_path.exists():
        return "", str(as_path.resolve())
    if ":" in text and not text.startswith("http"):
        repo, _, filename = text.partition(":")
        return repo.strip(), filename.strip() or DEFAULT_HF_FILE
    if text.endswith(".json") or text.endswith(".png") or text.endswith(".parquet"):
        return DEFAULT_HF_REPO, text
    return text, DEFAULT_HF_FILE


def load_occupancy_json(path: str | Path) -> Maze3D:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("occupancy JSON must be an object")
    grid, meta = payload_to_grid(payload)
    return occupancy_grid_to_maze(grid, **meta)


def load_occupancy_png(path: str | Path, *, size: int = 16) -> Maze3D:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is required to read occupancy.png") from exc
    image = Image.open(path).convert("L").resize((size, size))
    pixels = list(image.getdata())
    width, height = image.size
    grid = [[0 if pixels[y * width + x] > 127 else 1 for x in range(width)] for y in range(height)]
    return occupancy_grid_to_maze(grid, cell_size=0.9)

def load_maze_parquet(path: str | Path, *, row: int = 0) -> Maze3D:
    """Load one row from a HF Parquet maze dataset and turn it into a maze.

    Currently expects a `maze` string column that contains newline-separated rows with S/E markers.
    """
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("pyarrow is required to read Parquet HF datasets (pip install pyarrow)") from exc
    table = pq.read_table(path, columns=["maze"])
    if table.num_rows <= row:
        raise ValueError(f"parquet has only {table.num_rows} rows; requested row={row}")
    maze_txt = table.column("maze")[row].as_py()
    grid, start, goal = maze_text_to_grid(str(maze_txt))
    return occupancy_grid_to_maze(grid, cell_size=0.9, start_cell=start, goal_cell=goal)


def _load_any(path: str | Path) -> Maze3D:
    suffix = Path(path).suffix.lower()
    if suffix == ".png":
        return load_occupancy_png(path)
    if suffix == ".parquet":
        return load_maze_parquet(path)
    try:
        return load_occupancy_json(path)
    except ValueError:
        png = Path(path).with_name("occupancy.png")
        if png.exists():
            return load_occupancy_png(png)
        raise


def fetch_hf_occupancy(content: str = "", *, repo_id: str | None = None, filename: str | None = None) -> tuple[Maze3D, dict[str, Any]]:
    """Download one occupancy JSON/PNG/Parquet from Hugging Face, or load a local path."""
    local_repo, local_file = parse_hf_ref(content)
    repo = repo_id or local_repo
    file_name = filename or local_file
    if not repo:
        maze = _load_any(file_name)
        return maze, {"source": "local", "path": file_name}

    dest = cache_dir() / repo.replace("/", "__") / Path(file_name).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        maze = _load_any(dest)
        return maze, {"source": "cache", "repo": repo, "path": str(dest), "file": file_name}

    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise RuntimeError("huggingface_hub is required to fetch scene data. pip install huggingface_hub") from exc

    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN")
    downloaded = hf_hub_download(
        repo_id=repo,
        filename=file_name,
        repo_type="dataset",
        cache_dir=str(cache_dir() / "hub"),
        token=token,
    )
    maze = _load_any(downloaded)
    dest.write_bytes(Path(downloaded).read_bytes())
    return maze, {"source": "huggingface", "repo": repo, "path": str(downloaded), "file": file_name}
