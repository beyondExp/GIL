from __future__ import annotations

from pathlib import Path

from gil.orchestrator.director import AgentDirector
from gil.world.ingest import ingest_world
from gil.world.occupancy import occupancy_grid_to_maze

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "hf_occupancy.json"


def test_occupancy_grid_has_open_path():
    maze = occupancy_grid_to_maze(
        [
            [0, 0, 1, 0],
            [1, 0, 1, 0],
            [0, 0, 0, 0],
            [0, 1, 1, 0],
        ],
        cell_size=0.9,
        origin_x=-2.0,
        origin_y=-2.0,
    )
    path = maze.shortest_cell_path((maze.start[0], maze.start[1]), (maze.goal[0], maze.goal[1]))
    assert path is not None
    assert len(path) >= 2


def test_huggingface_source_loads_local_fixture():
    spec = ingest_world("huggingface", str(FIXTURE))
    assert spec.live is True
    assert spec.generator == "hf_occupancy"
    assert spec.maze is not None
    assert spec.observation["scene_kind"] == "hf_occupancy"


def test_director_dreams_inside_ingested_occupancy_world():
    director = AgentDirector()
    result = director.steer(
        instruction="reach the free cell",
        world_source="huggingface",
        world_content=str(FIXTURE),
        commit=False,
    )
    assert result["executed"] is False
    assert result["world"]["generator"] == "hf_occupancy"
    assert director.orch.world_model.maze.spec.width == 4
    assert result["dream"]["kept"] >= 1
