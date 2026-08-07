"""
P2.6c DerivativesData + P2.7 Regime 细分 + P2.9 Universe 测试。
"""
from __future__ import annotations

import pytest

from backend.services.alpha.regime_refined import (
    Regime,
    RegimeFeatures,
    classify_regime,
)
from backend.services.alpha.universe import (
    UniverseAgent,
    UniverseConfig,
    UniverseTier,
)
from backend.services.data.derivatives_collector import (
    DerivativesCollector,
    DerivativesSnapshot,
)

pytestmark = pytest.mark.unit


# ==================== P2.6c DerivativesData ====================

class TestDerivativesCollector:
    def test_collect_degrades_gracefully(self):
        """多源 down 降级不阻断（占位 API 返回 None）。"""
        coll = DerivativesCollector()
        snap = coll.collect("BTC-PERP")
        assert isinstance(snap, DerivativesSnapshot)
        assert snap.symbol == "BTC-PERP"

    def test_liquidation_alert(self):
        coll = DerivativesCollector(liquidation_alert_threshold_usd=1e7)
        snap = DerivativesSnapshot(symbol="X", liquidation_cluster_usd=5e7)
        assert coll.is_liquidation_alert(snap)
        snap2 = DerivativesSnapshot(symbol="X", liquidation_cluster_usd=1e6)
        assert not coll.is_liquidation_alert(snap2)


# ==================== P2.7 Regime 细分 ====================

class TestRegimeClassification:
    def test_trend_low_vol(self):
        f = RegimeFeatures(volatility_pct=0.3, trend_strength=0.6)
        assert classify_regime(f) == Regime.TREND_LOW_VOL

    def test_trend_high_vol(self):
        f = RegimeFeatures(volatility_pct=0.8, trend_strength=0.6)
        assert classify_regime(f) == Regime.TREND_HIGH_VOL

    def test_range(self):
        f = RegimeFeatures(volatility_pct=0.3, trend_strength=0.1)
        assert classify_regime(f) == Regime.RANGE

    def test_squeeze(self):
        f = RegimeFeatures(funding_extreme=True, oi_surge=True)
        assert classify_regime(f) == Regime.SQUEEZE

    def test_liquidation_cascade(self):
        f = RegimeFeatures(liquidation_burst=True, price_gap=0.06)
        assert classify_regime(f) == Regime.LIQUIDATION_CASCADE

    def test_extreme_price_gap(self):
        f = RegimeFeatures(price_gap=0.15)
        assert classify_regime(f) == Regime.EXTREME

    def test_extreme_high_vol(self):
        f = RegimeFeatures(volatility_pct=2.5)
        assert classify_regime(f) == Regime.EXTREME

    def test_at_least_6_regimes(self):
        """≥6 子 regime（诊断：3 态过粗）。"""
        assert len(list(Regime)) >= 6


# ==================== P2.9 Universe ====================

class TestUniverseAgent:
    def test_core_selection(self):
        agent = UniverseAgent()
        candidates = [
            {"symbol": "BTC-PERP", "venue": "binance", "adv_usd": 5e9},
            {"symbol": "ETH-PERP", "venue": "binance", "adv_usd": 2e9},
        ]
        sel = agent.select(candidates)
        assert len(sel) == 2
        assert sel[0].tier == UniverseTier.CORE

    def test_low_adv_excluded(self):
        agent = UniverseAgent()
        candidates = [
            {"symbol": "MICRO-PERP", "venue": "binance", "adv_usd": 1e6},
        ]
        sel = agent.select(candidates)
        assert len(sel) == 0  # 流动性不足

    def test_new_symbol_shadow(self):
        """新品种标 shadow（不直接 ACTIVE）。"""
        agent = UniverseAgent()
        sel = agent.select([{"symbol": "SOL-PERP", "venue": "binance", "adv_usd": 5e8}])
        assert sel[0].shadow is True

    def test_promote_from_shadow(self):
        agent = UniverseAgent()
        agent.select([{"symbol": "SOL-PERP", "venue": "binance", "adv_usd": 5e8}])
        assert agent.promote_from_shadow("SOL-PERP") is True
        assert agent.promote_from_shadow("NONEXIST") is False

    def test_max_size_cap(self):
        agent = UniverseAgent(UniverseConfig(max_universe_size=3))
        candidates = [
            {"symbol": f"S{i}-PERP", "venue": "binance", "adv_usd": 1e10 - i * 1e8}
            for i in range(10)
        ]
        sel = agent.select(candidates)
        assert len(sel) <= 3

    def test_tier_ordering(self):
        """CORE 排在 SECONDARY 前。"""
        agent = UniverseAgent()
        candidates = [
            {"symbol": "SOL-PERP", "venue": "binance", "adv_usd": 5e8},  # secondary
            {"symbol": "BTC-PERP", "venue": "binance", "adv_usd": 5e9},  # core
        ]
        sel = agent.select(candidates)
        assert sel[0].tier == UniverseTier.CORE
