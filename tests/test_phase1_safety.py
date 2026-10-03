from __future__ import annotations

import pytest

from gil.core.auth import AuthContext
from gil.core.authority import MotionAuthorityError, assert_motion_allowed, vision_may_authorize_motion
from gil.core.config import GilSettings
from gil.core.health import controls_health, models_health
from gil.core.profiles import load_profile
from gil.orchestrator.controls import FakeControls

pytestmark = pytest.mark.phase1


def test_settings_read_env(monkeypatch):
    monkeypatch.setenv("GIL_MAX_VX", "0.15")
    monkeypatch.setenv("GIL_REQUIRE_MOTION_ENABLE", "true")
    settings = GilSettings()
    assert settings.max_vx == 0.15
    assert settings.require_motion_enable is True


def test_health_reports_never_grant_models_motion():
    body = controls_health(backend="sim_ws", ready_for_motion=True)
    brain = models_health(model="gemini", loaded=True)
    assert body.service == "gil_controls"
    assert brain.ready_for_motion is False
    assert brain.details["safe_for_motion_authority"] is False


def test_vision_payload_is_not_motion_authority_by_default():
    assert vision_may_authorize_motion({"x": 1.0, "y": 0.0, "confidence": "estimated"}) is False
    assert vision_may_authorize_motion({"safe_for_motion_authority": False, "x": 1}) is False
    assert vision_may_authorize_motion({"safe_for_motion_authority": True}) is True


def test_untrusted_sources_cannot_issue_cmd_vel():
    with pytest.raises(MotionAuthorityError):
        assert_motion_allowed(command_type="cmd_vel", source="world_model")
    with pytest.raises(MotionAuthorityError):
        assert_motion_allowed(command_type="move_robot", source="gemini")
    assert_motion_allowed(command_type="cmd_vel", source="orchestrator")
    assert_motion_allowed(command_type="cmd_vel", source="controls")


def test_fake_controls_rejects_world_model_and_disabled_motion():
    profile = load_profile("unitree_h1_sim")
    controls = FakeControls(profile)
    denied = controls.send("h1", {"type": "cmd_vel", "vx": 0.2}, source="dream")
    assert denied.success is False
    disabled = controls.send("h1", {"type": "cmd_vel", "vx": 0.2}, source="orchestrator")
    assert disabled.success is False
    controls.motion_enabled = True
    ok = controls.send("h1", {"type": "cmd_vel", "vx": 0.2}, source="orchestrator")
    assert ok.success is True


def test_open_sim_auth_defaults_to_operator():
    ctx = AuthContext(GilSettings(operator_token="", autonomy_token=""))
    assert ctx.resolve(None, hardware=False).value == "operator"
    with pytest.raises(Exception):
        ctx.resolve(None, hardware=True)
