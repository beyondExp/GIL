from __future__ import annotations

import json
import time
from typing import Any

from pydantic import BaseModel, Field


class HealthReport(BaseModel):
    ok: bool
    service: str
    role: str
    backend: str = "unknown"
    ready_for_motion: bool = False
    warnings: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    ts_ms: int = Field(default_factory=lambda: int(time.time() * 1000))

    def to_json(self) -> str:
        return json.dumps(self.model_dump(), ensure_ascii=False)


def controls_health(*, backend: str, ready_for_motion: bool, warnings: list[str] | None = None) -> HealthReport:
    warns = list(warnings or [])
    return HealthReport(
        ok=True,
        service="gil_controls",
        role="body",
        backend=backend,
        ready_for_motion=ready_for_motion,
        warnings=warns,
    )


def models_health(*, model: str, loaded: bool) -> HealthReport:
    return HealthReport(
        ok=loaded,
        service="gil_models",
        role="perception",
        backend=model,
        ready_for_motion=False,
        warnings=[] if loaded else ["No vision model loaded"],
        details={"model": model, "loaded": loaded, "safe_for_motion_authority": False},
    )
