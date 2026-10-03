"""Tests for the learn/trainer module."""
from __future__ import annotations

import pytest

from gil.learn.trainer import ImaginationTrainer, PolicyTrainer, default_trainer, TrainReport
from gil.world.dream import Rollout

pytestmark = pytest.mark.phase3


def _rollouts(n_success: int, n_fail: int = 0) -> list[Rollout]:
    out = []
    for _ in range(n_success):
        out.append(Rollout(success=True, critic_score=0.9, physics_ok=True, commands=[{"type": "cmd_vel"}]))
    for _ in range(n_fail):
        out.append(Rollout(success=False, critic_score=0.2, physics_ok=False))
    return out


class TestImaginationTrainer:
    def test_passes_with_successes(self):
        trainer = ImaginationTrainer()
        report = trainer.train("locomotion_forward", rollouts=_rollouts(4, 0))
        assert report.ok is True
        assert report.method == "imagination_eval"

    def test_fails_with_no_successes(self):
        trainer = ImaginationTrainer()
        report = trainer.train("locomotion_forward", rollouts=_rollouts(0, 4))
        assert report.ok is False

    def test_empty_rollouts_fail(self):
        trainer = ImaginationTrainer()
        report = trainer.train("stand_idle", rollouts=[])
        assert report.ok is False


class TestPolicyTrainer:
    def test_no_backend_falls_back_to_imagination(self):
        trainer = PolicyTrainer(backend=None)
        report = trainer.train("locomotion_forward", rollouts=_rollouts(3))
        assert report.ok is True
        assert "fallback" in report.method

    def test_with_mock_backend(self):
        class FakeBackend:
            def fit(self, skill_id, **kwargs):
                return TrainReport(ok=True, method="rl", skill_id=skill_id)

        trainer = PolicyTrainer(backend=FakeBackend())
        report = trainer.train("maze_escape", rollouts=_rollouts(2))
        assert report.ok is True
        assert report.method == "rl"


class TestDefaultTrainer:
    def test_returns_imagination(self):
        trainer = default_trainer()
        assert isinstance(trainer, ImaginationTrainer)
