from __future__ import annotations

from gil.learn.humanoid_body_tasks import humanoid_body_tasks


def test_humanoid_body_tasks_has_square_tasks() -> None:
    ids = {t.id for t in humanoid_body_tasks()}
    assert "walk_square_cmdvel_safe" in ids
    assert "walk_square_goal_mode" in ids

