from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Goal:
    language: str = ""
    x: float | None = None
    y: float | None = None
    radius: float = 0.6

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Goal":
        return cls(
            language=str(data.get("language") or data.get("task") or ""),
            x=data.get("x"),
            y=data.get("y"),
            radius=float(data.get("radius") or 0.6),
        )
