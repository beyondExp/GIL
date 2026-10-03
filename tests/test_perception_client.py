"""Tests for the PerceptionClient."""
from __future__ import annotations

import pytest

from gil.orchestrator.perception import PerceptionClient

pytestmark = pytest.mark.phase5


class TestPerceptionClient:
    def test_offline_with_objects(self):
        client = PerceptionClient(base_url="")
        obs = {"last_image": "data:image/png;base64,abc", "objects": [{"name": "cube_red"}]}
        result = client.analyze(obs)
        assert result["ok"] is True
        assert result["backend"] == "offline"
        assert result["safe_for_motion_authority"] is False

    def test_no_image(self):
        client = PerceptionClient(base_url="")
        result = client.analyze({})
        assert result["ok"] is False

    def test_enrich_adds_perception(self):
        client = PerceptionClient(base_url="")
        obs = {"last_image": "data:image/png;base64,abc", "base": {"x": 0, "y": 0}}
        enriched = client.enrich(obs)
        assert "perception" in enriched
        assert enriched["base"]["x"] == 0

    def test_unreachable_url_is_graceful(self):
        client = PerceptionClient(base_url="http://127.0.0.1:1", timeout_s=0.1)
        result = client.analyze({"last_image": "data:image/png;base64,abc"})
        assert result["ok"] is False
        assert result["safe_for_motion_authority"] is False
