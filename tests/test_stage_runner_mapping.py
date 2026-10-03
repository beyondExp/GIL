from __future__ import annotations

from gil.learn.curriculum import curriculum_for


def test_curriculum_has_stages_runner_can_skip_or_run() -> None:
    stages = curriculum_for("humanoid_biped")
    ids = [s.id for s in stages]
    assert "safety_do_no_harm" in ids
    assert "stand_idle" in ids
    assert "instruction_following_natural_env" in ids

