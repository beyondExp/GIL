import base64
import json
import os
from dataclasses import dataclass
from typing import Optional


def _strip_data_url(data_url: str) -> str:
    # Accept either raw base64 or a data URL like "data:image/jpeg;base64,..."
    if not isinstance(data_url, str):
        return ""
    s = data_url.strip()
    if "," in s and s.startswith("data:"):
        return s.split(",", 1)[1]
    return s


def write_data_url_jpeg(data_url: str, path: str) -> None:
    b64 = _strip_data_url(data_url)
    if not b64:
        raise ValueError("empty image data")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    raw = base64.b64decode(b64)
    with open(path, "wb") as f:
        f.write(raw)


@dataclass
class DatasetRun:
    root_dir: str
    run_name: str
    task: str

    def __post_init__(self) -> None:
        self.run_dir = os.path.join(self.root_dir, self.run_name)
        self.frames_dir = os.path.join(self.run_dir, "frames")
        os.makedirs(self.frames_dir, exist_ok=True)
        self.transitions_path = os.path.join(self.run_dir, "transitions.jsonl")
        self.meta_path = os.path.join(self.run_dir, "meta.json")

    def write_meta(self, meta: dict) -> None:
        os.makedirs(self.run_dir, exist_ok=True)
        out = {"task": self.task, **meta}
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)

    def frame_paths(self, episode: int, step: int) -> dict:
        prefix = f"ep{episode:04d}_t{step:04d}"
        return {
            "image": os.path.join(self.frames_dir, f"{prefix}_main.jpg"),
            "image_left": os.path.join(self.frames_dir, f"{prefix}_left.jpg"),
            "image_right": os.path.join(self.frames_dir, f"{prefix}_right.jpg"),
            "image_wide": os.path.join(self.frames_dir, f"{prefix}_wide.jpg"),
        }

    def append_transition(self, row: dict) -> None:
        with open(self.transitions_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")


def default_run_name(prefix: str = "pick_place") -> str:
    import time

    ts = time.strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{ts}"


