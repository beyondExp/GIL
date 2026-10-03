from __future__ import annotations

import json

import pytest

from gil.learn.eval_harness import EpisodeResult, beats_baseline, evaluate, maze_success, pick_place_success
from gil.learn.lerobot_writer import EpisodeFrame, LeRobotWriter

pytestmark = pytest.mark.phase3


def test_lerobot_writer_emits_required_meta(tmp_path):
    writer = LeRobotWriter(tmp_path / "demo", repo_id="gil/test")
    writer.add_episode(
        [
            EpisodeFrame(0.0, [0, 0, 0], [0.1, 0, 0], "pick the red cube", robot_id="arm-1"),
            EpisodeFrame(0.1, [0.1, 0, 0], [0.1, 0, 0], "pick the red cube", done=True, success=True, robot_id="arm-1"),
        ]
    )
    root = writer.close()
    info = json.loads((root / "meta" / "info.json").read_text(encoding="utf-8"))
    assert info["total_episodes"] == 1
    assert info["total_frames"] == 2
    assert (root / "meta" / "tasks.jsonl").is_file()
    assert (root / "meta" / "episodes.jsonl").is_file()
    assert (root / "meta" / "modality.json").is_file()
    episode = root / "data" / "chunk-000" / "episode_000000.jsonl"
    assert episode.is_file()
    rows = [json.loads(line) for line in episode.read_text(encoding="utf-8").splitlines() if line]
    assert rows[0]["episode_index"] == 0
    assert "observation.state" in rows[0]
    assert "action" in rows[0]


def test_eval_harness_maze_and_pick_place():
    assert maze_success({"x": 3.4, "y": 3.5}, {"x": 3.5, "y": 3.5}, radius=0.6)
    assert not maze_success({"x": 0.0, "y": 0.0}, {"x": 3.5, "y": 3.5}, radius=0.6)
    assert pick_place_success(held=False, cube_in_bin=True, dropped=True)
    assert not pick_place_success(held=True, cube_in_bin=True, dropped=True)

    scripted = evaluate(
        "maze",
        [EpisodeResult("maze", True, 12.0), EpisodeResult("maze", False, 30.0)],
    )
    gated = evaluate(
        "maze",
        [EpisodeResult("maze", True, 10.0), EpisodeResult("maze", True, 11.0)],
    )
    assert scripted.success_rate == 0.5
    assert beats_baseline(gated, scripted)
    empty = evaluate("maze", [])
    assert empty.ok is False
