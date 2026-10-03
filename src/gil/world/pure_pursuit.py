from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable


Point2 = tuple[float, float]


def _wrap_pi(a: float) -> float:
    a = (a + math.pi) % (2 * math.pi) - math.pi
    return float(a)


def _dist(a: Point2, b: Point2) -> float:
    dx = float(b[0] - a[0])
    dy = float(b[1] - a[1])
    return float(math.sqrt(dx * dx + dy * dy))


@dataclass(frozen=True)
class PursuitCmd:
    vx: float
    wz: float
    target: Point2
    heading_err: float


def pick_lookahead_point(path: Iterable[Point2], *, x: float, y: float, lookahead_m: float) -> Point2 | None:
    pts = [(float(px), float(py)) for (px, py) in path]
    if not pts:
        return None
    here = (float(x), float(y))
    # find closest index
    best_i = 0
    best_d = 1e9
    for i, p in enumerate(pts):
        d = _dist(here, p)
        if d < best_d:
            best_d = d
            best_i = i
    # walk forward until lookahead distance is met
    acc = 0.0
    for j in range(best_i, len(pts) - 1):
        seg = _dist(pts[j], pts[j + 1])
        acc += seg
        if acc >= float(lookahead_m):
            return pts[j + 1]
    return pts[-1]


def pure_pursuit_cmd(
    *,
    x: float,
    y: float,
    yaw: float,
    path: Iterable[Point2],
    lookahead_m: float = 0.65,
    vx: float = 0.10,
    wz_cap: float = 0.25,
    k_heading: float = 2.0,
    turn_in_place_err: float = 0.95,
    turn_min_vx_scale: float = 0.12,
) -> PursuitCmd | None:
    tgt = pick_lookahead_point(path, x=float(x), y=float(y), lookahead_m=float(lookahead_m))
    if tgt is None:
        return None
    desired = float(math.atan2(float(tgt[1]) - float(y), float(tgt[0]) - float(x)))
    err = _wrap_pi(desired - float(yaw))
    wz = float(max(-float(wz_cap), min(float(wz_cap), float(k_heading) * err)))
    if abs(err) > float(turn_in_place_err):
        vx_out = float(vx) * float(max(0.0, min(1.0, float(turn_min_vx_scale))))
    else:
        vx_out = float(vx)
    return PursuitCmd(vx=float(vx_out), wz=float(wz), target=tgt, heading_err=float(err))

