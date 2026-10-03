from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class EpisodeResult:
    task: str
    success: bool
    time_s: float
    collisions: int = 0
    safety_stops: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalReport:
    task: str
    n: int
    successes: int
    success_rate: float
    mean_time_s: float
    collisions: int
    safety_stops: int

    @property
    def ok(self) -> bool:
        return self.n > 0


def maze_success(pose: dict[str, float], goal: dict[str, float], radius: float = 0.6) -> bool:
    dx = float(pose.get("x", 0.0)) - float(goal.get("x", 0.0))
    dy = float(pose.get("y", 0.0)) - float(goal.get("y", 0.0))
    return (dx * dx + dy * dy) ** 0.5 <= radius


def pick_place_success(*, held: bool, cube_in_bin: bool, dropped: bool) -> bool:
    return cube_in_bin and dropped and not held


def evaluate(task: str, episodes: list[EpisodeResult]) -> EvalReport:
    if not episodes:
        return EvalReport(task=task, n=0, successes=0, success_rate=0.0, mean_time_s=0.0, collisions=0, safety_stops=0)
    successes = sum(1 for e in episodes if e.success)
    return EvalReport(
        task=task,
        n=len(episodes),
        successes=successes,
        success_rate=successes / float(len(episodes)),
        mean_time_s=sum(e.time_s for e in episodes) / float(len(episodes)),
        collisions=sum(e.collisions for e in episodes),
        safety_stops=sum(e.safety_stops for e in episodes),
    )


def beats_baseline(candidate: EvalReport, baseline: EvalReport) -> bool:
    if candidate.n == 0 or baseline.n == 0:
        return False
    return candidate.success_rate > baseline.success_rate
