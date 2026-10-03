"""Tests for logging and metrics infrastructure."""
from __future__ import annotations

import logging

import pytest

from gil.core.logging_setup import configure_logging, get_logger
from gil.core.metrics import METRICS, GilMetrics

pytestmark = pytest.mark.phase0


class TestLogging:
    def test_configure_returns_logger(self):
        logger = configure_logging()
        assert isinstance(logger, logging.Logger)
        assert logger.name == "gil"

    def test_get_logger_prefixes(self):
        lg = get_logger("world.kinematic3d")
        assert lg.name == "gil.world.kinematic3d"

    def test_get_logger_already_prefixed(self):
        lg = get_logger("gil.core")
        assert lg.name == "gil.core"


class TestMetrics:
    def test_inc_and_snapshot(self):
        m = GilMetrics()
        m.inc("test_counter")
        m.inc("test_counter", 2.0)
        snap = m.snapshot()
        assert snap["counters"]["test_counter"] == 3.0

    def test_gauge(self):
        m = GilMetrics()
        m.set_gauge("latency_ms", 42.5)
        snap = m.snapshot()
        assert snap["gauges"]["latency_ms"] == 42.5

    def test_global_metrics(self):
        METRICS.inc("global_test")
        snap = METRICS.snapshot()
        assert "global_test" in snap["counters"]
