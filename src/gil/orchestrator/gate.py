from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class GateConfig:
    min_imagination_success: float = 0.8
    min_critic_score: float = 0.7
    min_map_coverage: float = 0.5
    require_preflight: bool = True
    require_calibrated_map: bool = True


@dataclass
class GateDecision:
    ok: bool
    reason: str
    scores: dict[str, Any] = field(default_factory=dict)
    gate_id: str = ""


class ConfidenceGate:
    def __init__(self, cfg: GateConfig | None = None):
        self.cfg = cfg or GateConfig()

    def evaluate(
        self,
        *,
        imagination_success: float,
        critic_score: float,
        map_coverage: float,
        map_calibrated: bool,
        preflight_ok: bool,
    ) -> GateDecision:
        scores = {
            "imagination_success": imagination_success,
            "critic_score": critic_score,
            "map_coverage": map_coverage,
            "map_calibrated": map_calibrated,
            "preflight_ok": preflight_ok,
        }
        if self.cfg.require_preflight and not preflight_ok:
            return GateDecision(False, "preflight_failed", scores)
        if self.cfg.require_calibrated_map and not map_calibrated:
            return GateDecision(False, "map_not_calibrated", scores)
        if map_coverage < self.cfg.min_map_coverage:
            return GateDecision(False, "map_coverage_low", scores)
        if critic_score < self.cfg.min_critic_score:
            return GateDecision(False, "critic_rejected", scores)
        if imagination_success < self.cfg.min_imagination_success:
            return GateDecision(False, "imagination_unreliable", scores)
        return GateDecision(True, "pass", scores, gate_id=uuid4().hex[:12])
