from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


@dataclass
class EpisodeFrame:
    timestamp: float
    observation_state: list[float]
    action: list[float]
    task: str
    done: bool = False
    success: bool = False
    robot_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class LeRobotWriter:
    """LeRobot-layout dataset without requiring the full LeRobot stack at runtime.

    Writes meta/*.json(l) plus per-episode JSONL. If pyarrow is installed, also
    writes data/chunk-000/episode_XXXXXX.parquet.
    """

    def __init__(self, root: str | Path, repo_id: str = "gil/local"):
        self.root = Path(root)
        self.repo_id = repo_id
        self.episodes: list[list[EpisodeFrame]] = []
        self.tasks: list[str] = []

    def add_episode(self, frames: list[EpisodeFrame]) -> int:
        if not frames:
            raise ValueError("episode must contain at least one frame")
        task = frames[0].task
        if task not in self.tasks:
            self.tasks.append(task)
        self.episodes.append(list(frames))
        return len(self.episodes) - 1

    def close(self) -> Path:
        meta = self.root / "meta"
        data = self.root / "data" / "chunk-000"
        meta.mkdir(parents=True, exist_ok=True)
        data.mkdir(parents=True, exist_ok=True)

        info = {
            "codebase_version": "v2.0",
            "robot_type": "gil",
            "fps": 10,
            "total_episodes": len(self.episodes),
            "total_frames": sum(len(ep) for ep in self.episodes),
            "data_path": "data/chunk-000/episode_{episode_index:06d}.parquet",
            "video_path": "videos/chunk-000/observation.images.ego_view/episode_{episode_index:06d}.mp4",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "repo_id": self.repo_id,
        }
        (meta / "info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
        _write_jsonl(
            meta / "tasks.jsonl",
            [{"task_index": i, "task": t} for i, t in enumerate(self.tasks)],
        )
        _write_jsonl(
            meta / "episodes.jsonl",
            [
                {
                    "episode_index": i,
                    "tasks": [ep[0].task],
                    "length": len(ep),
                    "robot_id": ep[0].robot_id,
                }
                for i, ep in enumerate(self.episodes)
            ],
        )
        modality = {
            "state": {"base_xyz": {"start": 0, "end": 3}},
            "action": {"delta_xyz": {"start": 0, "end": 3}},
        }
        (meta / "modality.json").write_text(json.dumps(modality, indent=2), encoding="utf-8")

        global_index = 0
        for i, ep in enumerate(self.episodes):
            task_index = self.tasks.index(ep[0].task)
            rows = []
            for t, frame in enumerate(ep):
                rows.append(
                    {
                        "observation.state": frame.observation_state,
                        "action": frame.action,
                        "timestamp": frame.timestamp,
                        "episode_index": i,
                        "index": global_index,
                        "task_index": task_index,
                        "next.done": frame.done,
                        "next.reward": 1.0 if frame.success else 0.0,
                        "annotation.human.action.task_description": task_index,
                    }
                )
                global_index += 1
            jsonl_path = data / f"episode_{i:06d}.jsonl"
            _write_jsonl(jsonl_path, rows)
            parquet_path = data / f"episode_{i:06d}.parquet"
            _maybe_write_parquet(parquet_path, rows)
        return self.root


def _maybe_write_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except Exception:
        return
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path)
