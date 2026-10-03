from __future__ import annotations

import pytest

from gil.a2a.cards import list_cards, load_card
from gil.orchestrator import Orchestrator
from gil.orchestrator.session import SessionStore

pytestmark = pytest.mark.phase7


def test_a2a_cards_are_valid_and_split_authority():
    names = set(list_cards())
    assert {"orchestrator", "critic", "robot"} <= names
    critic = load_card("critic")
    robot = load_card("robot")
    orch = load_card("orchestrator")
    assert "drive" not in critic["skills"]
    assert "cmd_vel" not in " ".join(critic["skills"])
    assert "stop" in robot["skills"]
    assert "run_mission" in orch["skills"]
    assert "steer" in orch["skills"]
    assert "list_embodiments" in orch["skills"]
    assert critic["protocol"] == "a2a"


def test_multi_robot_session_requires_robot_id():
    store = SessionStore()
    store.attach("arm-1", "arm_sim")
    store.attach("h1-1", "unitree_h1_sim")
    assert store.ids() == ["arm-1", "h1-1"]
    with pytest.raises(KeyError):
        store.require(None)
    assert store.require("h1-1").profile.kind == "humanoid"


def test_orchestrator_holds_independent_maps_per_robot():
    orch = Orchestrator()
    orch.connect_robot("arm_sim", robot_id="arm-1")
    orch.connect_robot("unitree_h1_sim", robot_id="h1-1")
    orch.maps["arm-1"].ingest({"objects": [{"label": "blue cube", "x": 1.1, "y": 0.0}]})
    orch.maps["h1-1"].ingest({"objects": [{"label": "maze exit", "x": 3.5, "y": 3.5}]})
    assert orch.maps["arm-1"].locate("blue cube") is not None
    assert orch.maps["arm-1"].locate("maze exit") is None
    sit = orch.get_situation("h1-1")
    assert sit["robot_id"] == "h1-1"
    assert sit["profile_id"] == "unitree_h1_sim"
