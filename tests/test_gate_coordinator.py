"""
统一开仓门禁协调器测试。
"""
from __future__ import annotations

import pytest

from backend.services.gate_coordinator import OpenGateCoordinator

pytestmark = pytest.mark.unit


class TestGateCoordinator:
    def test_record_pass(self):
        gc = OpenGateCoordinator()
        gc.record("unified_gate", "BTC", "long", passed=True)
        assert gc.consecutive_blocks("BTC", "long") == 0

    def test_record_block(self):
        gc = OpenGateCoordinator()
        gc.record("mtf_constraint", "BTC", "long", passed=False, reason="veto")
        assert gc.consecutive_blocks("BTC", "long") == 1

    def test_consecutive_blocks_reset_on_pass(self):
        gc = OpenGateCoordinator()
        gc.record("mtf", "BTC", "long", False, "veto")
        gc.record("mtf", "BTC", "long", False, "veto")
        gc.record("mtf", "BTC", "long", False, "veto")
        assert gc.consecutive_blocks("BTC", "long") == 3
        gc.record("unified_gate", "BTC", "long", True)
        assert gc.consecutive_blocks("BTC", "long") == 0

    def test_stats(self):
        gc = OpenGateCoordinator()
        gc.record("unified_gate", "BTC", "short", True)
        gc.record("unified_gate", "BTC", "short", True)
        gc.record("unified_gate", "BTC", "short", False, "confidence")
        stats = gc.stats(window_sec=60)
        assert "unified_gate" in stats
        assert stats["unified_gate"]["pass"] == 2
        assert stats["unified_gate"]["block"] == 1
        assert stats["unified_gate"]["pass_rate"] == pytest.approx(0.67, abs=0.01)

    def test_should_degrade(self):
        gc = OpenGateCoordinator()
        for _ in range(10):
            gc.record("gate", "ETH", "long", False, "blocked")
        assert gc.should_degrade_threshold("ETH", "long", threshold=10)

    def test_blocked_symbols(self):
        gc = OpenGateCoordinator()
        gc.record("gate", "BTC", "long", False, "veto")
        gc.record("gate", "ETH", "mid", False, "confidence")
        blocked = gc.blocked_symbols()
        assert len(blocked) == 2
