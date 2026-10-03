from __future__ import annotations

import pytest

from gil.hardware.isaac_robots import get_isaac_robot, list_isaac_robots
from gil.orchestrator.director import AgentDirector
from gil.world.ingest import ingest_world

pytestmark = pytest.mark.phase7


def test_isaac_catalog_includes_h1_and_g1():
    keys = {row["key"] for row in list_isaac_robots()}
    assert {"unitree_h1", "unitree_g1", "franka"} <= keys
    h1 = get_isaac_robot("h1")
    assert h1.usd_rel.endswith("h1.usd")
    assert h1.profile_id == "unitree_h1_sim"
    assert h1.lab_task == "Isaac-Velocity-Flat-H1-Maze-v0"
    assert h1.pretrained_task == "Isaac-Velocity-Flat-H1-v0"
    assert "-ForcePolicy" in h1.kit_args
    h12 = get_isaac_robot("unitree_h1_2")
    assert "-UseLabRobot" in h12.kit_args
    g1 = get_isaac_robot("g1")
    assert "isaaclab_play_h1.py" in g1.kit_script
    rows = {row["key"]: row for row in list_isaac_robots()}
    assert "locomotion_forward" in rows["unitree_h1"]["curriculum"]
    assert rows["franka"]["lab_task"] == "Isaac-Reach-Franka-v0"
    director = AgentDirector()
    picked = director.select_embodiment("unitree_h1_2")
    assert "-UseLabRobot" in picked["launch"]["args"]


def test_text_world_is_live_isaac_maze_and_image_is_held_for_nurec():
    maze = ingest_world("text", "escape the maze")
    assert maze.live is True
    assert maze.generator == "maze_dfs"
    held = ingest_world("image", "C:/captures/room.mp4")
    assert held.generator == "nurec"
    assert held.live is False


def test_director_refuses_when_live_camera_missing():
    director = AgentDirector()
    result = director.steer(
        instruction="walk to the exit",
        embodiment="unitree_h1",
        commit=False,
        live_facts={"connected": True, "has_image": False, "base": {"x": 0.0, "y": 0.0, "z": 0.9}, "heartbeat_ok": True},
    )
    assert result["executed"] is False
    assert result["decision"] == "blocked"
    assert result["runtime_stage"] == "A3"


def test_director_steers_dream_without_executing():
    director = AgentDirector()
    result = director.steer(instruction="escape the maze", embodiment="unitree_h1", commit=False)
    assert result["executed"] is False
    assert result["embodiment"] == "unitree_h1"
    assert result["lab_task"] == "Isaac-Velocity-Flat-H1-Maze-v0"
    assert "maze_escape" in result["curriculum"]
    assert result["dream"]["kept"] >= 1
    assert result["dream"]["gate"]["ok"] is True
    assert result["target_skill"] == "maze_escape"
    preview = director.preview_plan()
    assert preview["ok"] is True
    assert preview["commands"]
    assert all(cmd["type"] == "preview_vel" for cmd in preview["commands"])
    assert director.orch._controls[director.robot_id].sent == []


def test_director_commit_goes_through_orchestrator_not_world_model():
    director = AgentDirector()
    result = director.steer(instruction="escape the maze", commit=True)
    assert result["executed"] is True
    sent = director.orch._controls[director.robot_id].sent
    assert any(row["source"] == "orchestrator" for row in sent)
    assert all(row["source"] != "world_model" for row in sent)


def test_world_model_source_cannot_preview_either():
    from gil.orchestrator.controls import FakeControls
    from gil.core.profiles import load_profile

    controls = FakeControls(load_profile("unitree_h1_sim"))
    sneaky = controls.send("h1", {"type": "preview_vel", "vx": 0.2}, source="cosmos")
    assert sneaky.success is False
