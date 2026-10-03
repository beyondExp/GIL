from __future__ import annotations

import json
from pathlib import Path

from gil.orchestrator.stage_machine import CompetenceLedger, CompetenceStatus


class FileCompetenceLedger(CompetenceLedger):
    """JSON-backed competence ledger that survives process restarts."""

    def __init__(self, path: str | Path | None = None) -> None:
        super().__init__()
        self.path = Path(path or Path.home() / ".gil" / "competence_ledger.json")
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        rows = raw.get("rows") if isinstance(raw, dict) else raw
        if not isinstance(rows, dict):
            return
        for key, status in rows.items():
            parts = str(key).split("|", 2)
            if len(parts) != 3:
                continue
            st = status if status in {"passed", "failed", "unknown"} else "unknown"
            self._rows[(parts[0], parts[1], parts[2])] = st  # type: ignore[assignment]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rows = {"|".join(k): v for k, v in self._rows.items()}
        self.path.write_text(json.dumps({"rows": rows}, indent=2), encoding="utf-8")

    def set(self, robot_id: str, env_key: str, stage_id: str, status: CompetenceStatus) -> None:
        super().set(robot_id, env_key, stage_id, status)
        try:
            self.save()
        except OSError:
            pass
