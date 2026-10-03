from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock
from time import time


@dataclass
class Counter:
    name: str
    value: float = 0.0
    labels: dict[str, str] = field(default_factory=dict)


class GilMetrics:
    """In-process counters for safety events, gate outcomes, and latency."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._last_event_s: float = 0.0

    def inc(self, name: str, amount: float = 1.0) -> None:
        with self._lock:
            self._counters[name] += amount
            self._last_event_s = time()

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def snapshot(self) -> dict[str, dict[str, float]]:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "last_event_s": self._last_event_s,
            }


METRICS = GilMetrics()
