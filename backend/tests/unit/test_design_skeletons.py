"""
设计骨架验证测试（M2/M3/M8）

验证目标：
1. 新模块可导入、特征开关默认关闭；
2. 纯函数行为与《详细技术设计文档》公式一致；
3. 未启用时全部 fail-safe（空结果/直通），不改变现有行为。
"""

import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.services.evolution.factor_labels import (  # noqa: E402
    FEATURE_FACTOR_LABELS_ENABLED,
    build_triple_barrier_labels,
    capacity_usd,
    compute_quality_metrics,
    net_ic,
    turnover,
)
from backend.services.factor_engine.exposure_service import (  # noqa: E402
    FEATURE_FACTOR_EXPOSURE_ENABLED,
    FactorExposure,
    factor_exposure_service,
)
from backend.services.portfolio.resonance_layer import (  # noqa: E402
    PRL_ENABLED,
    PeriodSignal,
    ResonanceLayer,
    resonance_layer,
    resonance_score,
    resolve_verdict,
    score_per_signal,
)


class TestFeatureFlags:
    def test_flags_default_off(self):
        # 骨架阶段所有开关必须默认关闭
        assert FEATURE_FACTOR_LABELS_ENABLED is False
        assert FEATURE_FACTOR_EXPOSURE_ENABLED is False
        assert PRL_ENABLED is False


class TestM2FactorLabels:
    def test_net_ic_formula(self):
        # net_ic = ic_mean − turnover × cost_per_turn
        assert net_ic(0.05, 0.1, 0.001) == 0.05 - 0.1 * 0.001
        assert net_ic(0.03, 0.8, 0.05) < 0  # 高换手+高成本吃掉 alpha
        assert net_ic(0.03, 0.8) == 0.03 - 0.8 * 0.001

    def test_turnover(self):
        s = pd.Series([0.0, 1.0, 1.0, -1.0, -1.0])
        # diff: 1,0,-2,0 → mean=0.75 → /2 = 0.375
        assert turnover(s) == 0.375
        assert turnover(pd.Series([1.0])) == 0.0

    def test_capacity_usd(self):
        assert capacity_usd(1_000_000, 0.5) == 1_000_000 * min(0.02, 0.0005 / 0.5)
        assert capacity_usd(0, 0.5) == 0.0

    def test_triple_barrier_labels_aligned(self):
        # 单边上涨序列 → 标签不应全为 0（有 +1 或至少非空）
        idx = pd.date_range("2026-01-01", periods=40, freq="5min")
        df = pd.DataFrame({
            "open": [100 + i * 0.1 for i in range(40)],
            "high": [100 + i * 0.1 + 0.05 for i in range(40)],
            "low": [100 + i * 0.1 - 0.05 for i in range(40)],
            "close": [100 + i * 0.1 for i in range(40)],
            "volume": [1.0] * 40,
        }, index=idx)
        labels = build_triple_barrier_labels(df, horizon_bars=5)
        assert isinstance(labels, pd.Series)
        assert len(labels) == len(df)
        assert labels.index.equals(df.index)

    def test_quality_metrics(self):
        s = pd.Series([0.0, 1.0, 1.0, -1.0, -1.0])
        m = compute_quality_metrics(
            ic_mean=0.05, icir=1.2,
            factor_series=s, volume_24h_usd=1e6,
        )
        assert m.net_ic < m.ic_mean
        assert m.capacity_usd >= 0


class TestM3Exposure:
    def test_expected_alpha(self):
        e = FactorExposure(factor_id="f1", z_score=1.5, net_ic=0.02, weight=0.1)
        assert e.expected_alpha == 1.5 * 0.02 * 0.1
        d = e.to_dict()
        assert d["expected_alpha"] == round(1.5 * 0.02 * 0.1, 8)

    def test_disabled_returns_empty(self):
        assert factor_exposure_service.exposure("BTC", "5m") == []
        assert factor_exposure_service.status()["enabled"] is False


class TestM8Resonance:
    def _sig(self, tier, direction, conf, symbol="BTC"):
        return PeriodSignal(symbol=symbol, tier=tier, direction=direction, confidence=conf)

    def test_score_per_signal(self):
        s = self._sig("mid", "long", 80)
        # 1.0 × 0.8 × 0.40 = 0.32
        assert abs(score_per_signal(s) - 0.32) < 1e-9

    def test_resonance_verdict(self):
        aligned = [
            self._sig("short", "long", 80),
            self._sig("mid", "long", 70),
            self._sig("long", "long", 60),
        ]
        score = resonance_score(aligned)
        assert resolve_verdict(score, 3) == "aligned"
        conflict = [
            self._sig("short", "short", 90),
            self._sig("mid", "short", 90),
        ]
        assert resolve_verdict(resonance_score(conflict), 2) == "conflict"
        assert resolve_verdict(0.0, 0) == "no_data"
        assert resolve_verdict(0.1, 1) == "neutral"

    def test_disabled_direct_pass(self):
        assert resonance_layer.status()["enabled"] is False
        proposal = {"symbol": "BTC", "action": "buy", "position_pct": 1.0}
        assert resonance_layer.evaluate(proposal) is proposal  # 直通
        allocated, reason = resonance_layer.allocate("BTC", "short", 100.0, 1000.0, [])
        assert allocated == 100.0 and reason == ""

    def test_publish_ignored_when_disabled(self):
        resonance_layer.publish(self._sig("mid", "long", 80))
        assert len(resonance_layer._ring) == 0  # 关闭时不写入


class TestSkeletonImportable:
    def test_modules_importable(self):
        assert callable(build_triple_barrier_labels)
        assert callable(resonance_score)
        assert hasattr(ResonanceLayer, "get_instance")
