from __future__ import annotations

from typing import Iterable


Point2 = tuple[float, float]


def chaikin_smooth(points: Iterable[Point2], *, iterations: int = 2) -> list[Point2]:
    """
    Chaikin corner-cutting -> produces a spline-like smooth polyline.
    Keeps endpoints fixed.
    """
    pts = [(float(x), float(y)) for (x, y) in points]
    if len(pts) < 3:
        return pts
    it = max(0, int(iterations))
    for _ in range(it):
        out: list[Point2] = [pts[0]]
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            q = (0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1)
            r = (0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1)
            out.append(q)
            out.append(r)
        out.append(pts[-1])
        pts = out
    return pts

