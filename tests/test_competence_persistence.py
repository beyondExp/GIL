"""Tests for FileCompetenceLedger persistence."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from gil.memory.competence import FileCompetenceLedger

pytestmark = pytest.mark.phase3


class TestFileCompetenceLedger:
    def test_set_and_get(self, tmp_path: Path):
        path = tmp_path / "ledger.json"
        ledger = FileCompetenceLedger(path)
        ledger.set("h1", "maze0", "stand_idle", "passed")
        assert ledger.get("h1", "maze0", "stand_idle") == "passed"
        assert ledger.get("h1", "maze0", "locomotion_forward") == "unknown"

    def test_persistence_across_instances(self, tmp_path: Path):
        path = tmp_path / "ledger.json"
        l1 = FileCompetenceLedger(path)
        l1.set("h1", "maze0", "stand_idle", "passed")
        l1.set("h1", "maze0", "locomotion_forward", "failed")

        l2 = FileCompetenceLedger(path)
        assert l2.get("h1", "maze0", "stand_idle") == "passed"
        assert l2.get("h1", "maze0", "locomotion_forward") == "failed"
        assert l2.get("h1", "maze0", "maze_escape") == "unknown"

    def test_dump(self, tmp_path: Path):
        path = tmp_path / "ledger.json"
        ledger = FileCompetenceLedger(path)
        ledger.set("h1", "maze0", "stand_idle", "passed")
        ledger.set("h1", "maze0", "stop_and_hold", "passed")
        dump = ledger.dump("h1", "maze0")
        assert dump == {"stand_idle": "passed", "stop_and_hold": "passed"}

    def test_file_format(self, tmp_path: Path):
        path = tmp_path / "ledger.json"
        ledger = FileCompetenceLedger(path)
        ledger.set("h1", "env", "skill", "passed")
        raw = json.loads(path.read_text())
        assert "rows" in raw
        assert "h1|env|skill" in raw["rows"]

    def test_corrupted_file_survives(self, tmp_path: Path):
        path = tmp_path / "ledger.json"
        path.write_text("not json!!!")
        ledger = FileCompetenceLedger(path)
        assert ledger.get("h1", "x", "y") == "unknown"
        ledger.set("h1", "x", "y", "passed")
        assert ledger.get("h1", "x", "y") == "passed"
