"""Tests for the generic RobotInterface, adapters, and provenance."""
from __future__ import annotations

import pytest

from gil.core.authority import MotionAuthorityError, assert_motion_allowed
from gil.core.provenance import CommandProvenance, stamp_command
from gil.core.robot_interface import (
    Morphology,
    RobotAction,
    RobotObservation,
    action_from_command,
    morphology_from_kind,
)
from gil.hardware.adapters import InProcessAdapter, adapter_for_kind, gated_action

pytestmark = pytest.mark.phase1


class TestMorphologyMapping:
    def test_humanoid_variants(self):
        for k in ("humanoid", "h1", "g1", "humanoid_biped"):
            assert morphology_from_kind(k) == "humanoid"

    def test_arm_variants(self):
        for k in ("arm", "franka", "panda", "manipulator_arm"):
            assert morphology_from_kind(k) == "arm"

    def test_quadruped(self):
        assert morphology_from_kind("go2") == "quadruped"
        assert morphology_from_kind("anymal") == "quadruped"

    def test_wheeled(self):
        assert morphology_from_kind("jetbot") == "wheeled"
        assert morphology_from_kind("nova_carter") == "wheeled"

    def test_drone(self):
        assert morphology_from_kind("drone") == "drone"

    def test_unknown(self):
        assert morphology_from_kind("custom_robot_xyz") == "other"


class TestRobotAction:
    def test_as_command_includes_provenance(self):
        prov = CommandProvenance(source="orchestrator", origin="dream", gate_id="abc123")
        action = RobotAction(kind="cmd_vel", values={"vx": 0.2}, provenance=prov)
        cmd = action.as_command()
        assert cmd["type"] == "cmd_vel"
        assert cmd["vx"] == 0.2
        assert cmd["provenance"]["origin"] == "dream"
        assert cmd["gate_id"] == "abc123"

    def test_action_from_command(self):
        cmd = {"type": "move_robot", "x": 0.5, "y": 0.3, "z": 0.1}
        action = action_from_command(cmd, source="controls")
        assert action.kind == "move_robot"
        assert action.values["x"] == 0.5
        assert action.provenance.source == "controls"


class TestInProcessAdapter:
    def test_execute_requires_motion_enabled(self):
        adapter = InProcessAdapter("arm")
        action = gated_action("move_robot", {"x": 0.5}, source="orchestrator", origin="operator")
        result = adapter.execute(action)
        assert result["ok"] is False
        assert "motion_disabled" in result["error"]

    def test_execute_succeeds_when_enabled(self):
        adapter = InProcessAdapter("arm")
        adapter.enable(reason="test")
        action = gated_action("move_robot", {"x": 0.5}, source="orchestrator", origin="operator")
        result = adapter.execute(action)
        assert result["ok"] is True
        assert len(adapter.sent) == 1

    def test_estop_blocks_execution(self):
        adapter = InProcessAdapter("humanoid")
        adapter.enable(reason="test")
        adapter.estop(True, reason="test")
        action = gated_action("cmd_vel", {"vx": 0.2}, source="orchestrator")
        result = adapter.execute(action)
        assert result["ok"] is False

    def test_preview_allowed_without_motion_enable(self):
        adapter = InProcessAdapter("humanoid")
        action = gated_action("preview_vel", {"vx": 0.1}, source="orchestrator")
        result = adapter.execute(action)
        assert result["ok"] is True

    def test_adapter_for_kind(self):
        a = adapter_for_kind("franka")
        assert a.morphology == "arm"
        a2 = adapter_for_kind("h1")
        assert a2.morphology == "humanoid"


class TestProvenanceAuthority:
    def test_orchestrator_relaying_dream_with_gate_id_passes(self):
        assert_motion_allowed(
            command_type="cmd_vel",
            source="orchestrator",
            origin="dream",
            gate_id="abc123",
        )

    def test_orchestrator_relaying_dream_without_gate_id_fails(self):
        with pytest.raises(MotionAuthorityError, match="gate_id"):
            assert_motion_allowed(
                command_type="cmd_vel",
                source="orchestrator",
                origin="dream",
                gate_id="",
            )

    def test_world_model_source_still_denied(self):
        with pytest.raises(MotionAuthorityError):
            assert_motion_allowed(command_type="cmd_vel", source="world_model")

    def test_operator_origin_no_gate_needed(self):
        assert_motion_allowed(
            command_type="cmd_vel",
            source="orchestrator",
            origin="operator",
        )

    def test_provenance_dataclass(self):
        prov = CommandProvenance(source="orchestrator", origin="dream", gate_id="g1", rollout_id="r1")
        assert_motion_allowed(
            command_type="cmd_vel",
            source="orchestrator",
            provenance=prov,
        )

    def test_stamp_command(self):
        cmd = {"type": "cmd_vel", "vx": 0.2}
        prov = CommandProvenance(source="orchestrator", origin="dream", gate_id="g1")
        stamped = stamp_command(cmd, prov)
        assert stamped["provenance"]["origin"] == "dream"
        assert stamped["gate_id"] == "g1"
        assert stamped["type"] == "cmd_vel"
