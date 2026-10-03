from __future__ import annotations

import math

import pytest

from gil.world.twin import UnicycleTwin, wrap_pi

pytestmark = [pytest.mark.phase8, pytest.mark.integration]


def test_unicycle_moves_in_body_frame():
    twin = UnicycleTwin()
    assert twin.step(0.5, 0.0, 0.0, dt=1.0)
    assert twin.pose.x == pytest.approx(0.5)
    assert twin.pose.y == pytest.approx(0.0)

    turning = UnicycleTwin()
    turning.step(0.0, 0.0, math.pi / 2, dt=1.0)
    assert turning.pose.yaw == pytest.approx(math.pi / 2)
    turning.step(1.0, 0.0, 0.0, dt=1.0)
    assert turning.pose.x == pytest.approx(0.0, abs=1e-9)
    assert turning.pose.y == pytest.approx(1.0)


def test_occupancy_stops_the_twin_without_tunneling_into_the_cell():
    blocked = UnicycleTwin()
    moved = blocked.step(1.0, occupancy=lambda x, y: x > 0.3, dt=1.0)
    assert moved is False
    assert blocked.collided is True
    assert blocked.pose.x == pytest.approx(0.0)


def test_wrap_pi_stays_in_range():
    assert wrap_pi(3 * math.pi) == pytest.approx(math.pi) or abs(wrap_pi(3 * math.pi)) <= math.pi
    assert abs(wrap_pi(4.0)) <= math.pi
