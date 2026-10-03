from __future__ import annotations

import pytest

from gil.core.auth import AuthError
from gil.core.config import GilSettings
from gil.core.profiles import list_profiles, load_profile
from gil.core.schemas import ORCHESTRATOR_TOOLS
from gil.orchestrator import Orchestrator

pytestmark = pytest.mark.phase2

REPO_LEGACY = ("hardware", "h1", "default_profile.json")


def test_bundled_profiles_load():
    names = set(list_profiles())
    assert {"arm_sim", "unitree_h1_sim", "unitree_h1_hardware"} <= names
    arm = load_profile("arm_sim")
    h1 = load_profile("unitree_h1_sim")
    hw = load_profile("unitree_h1_hardware")
    assert arm.kind == "arm"
    assert h1.backend == "sim_ws"
    assert hw.is_hardware is True
    assert hw.safety.require_estop_topic is True


def test_legacy_h1_profile_normalizes(tmp_path):
    from pathlib import Path

    legacy = Path(__file__).resolve().parents[1].joinpath(*REPO_LEGACY)
    profile = load_profile(legacy)
    assert profile.backend == "h1_hardware"
    assert profile.topics.cmd_vel == "/cmd_vel"
    assert profile.action_space.type == "cmd_vel"


def test_orchestrator_connects_and_exposes_versioned_tools():
    orch = Orchestrator()
    result = orch.connect_robot("arm_sim", robot_id="arm-1")
    assert result["ok"] is True
    assert result["robot_id"] == "arm-1"
    names = {t["name"] for t in orch.tool_manifest()}
    assert names == {t.name for t in ORCHESTRATOR_TOOLS}
    for tool in ORCHESTRATOR_TOOLS:
        assert tool.version == "1"


def test_hardware_connect_without_token_fails():
    orch = Orchestrator(settings=GilSettings(operator_token="op", autonomy_token="auto"))
    with pytest.raises(AuthError):
        orch.connect_robot("unitree_h1_hardware", robot_id="h1", token="")


def test_observer_cannot_run_mission():
    orch = Orchestrator(settings=GilSettings(operator_token="op", autonomy_token="auto"))
    orch.connect_robot("arm_sim", robot_id="arm-1", token="op")
    # Re-bind with observer by using a junk token on sim when tokens are configured.
    orch.auth.settings = GilSettings(operator_token="op", autonomy_token="auto")
    orch.set_goal("arm-1", {"language": "wave"})
    result = orch.run_mission("arm-1", observation={"base": {"x": 0, "y": 0}}, token="nope")
    assert result.executed is False
    assert result.reason == "observer_forbidden"
