from __future__ import annotations

from gil.world.path_spline import chaikin_smooth
from gil.world.pure_pursuit import pure_pursuit_cmd


def test_chaikin_smooth_keeps_endpoints() -> None:
    pts = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]
    out = chaikin_smooth(pts, iterations=2)
    assert out[0] == pts[0]
    assert out[-1] == pts[-1]
    assert len(out) > len(pts)


def test_pure_pursuit_returns_cmd() -> None:
    path = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]
    cmd = pure_pursuit_cmd(x=0.0, y=0.0, yaw=0.0, path=path, vx=0.1, wz_cap=0.2)
    assert cmd is not None
    assert cmd.vx >= 0.0
    assert abs(cmd.wz) <= 0.2 + 1e-6

