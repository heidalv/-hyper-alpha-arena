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
    def test_flags_are_env_driven_bools(self):
        """[2026-09-02] 骨架阶段"全部默认关闭"已过时：M2 因子标签已产品化（代码默认 true），
        M3 暴露层由 .env 开启（FEATURE_FACTOR_EXPOSURE_ENABLED=true）。
        现只守：三者都是 bool；M3/M8 的**代码默认**仍为关闭（未配置环境 fail-safe）。
        """
        import re
        from backend.services.factor_engine import exposure_service
        from backend.services.portfolio import resonance_layer
        for flag in (FEATURE_FACTOR_LABELS_ENABLED, FEATURE_FACTOR_EXPOSURE_ENABLED, PRL_ENABLED):
            assert isinstance(flag, bool)
        src_m3 = open(exposure_service.__file__, encoding="utf-8").read()
        assert re.search(r'"FEATURE_FACTOR_EXPOSURE_ENABLED",\s*"false"', src_m3), "M3 代码默认应关闭"
        src_m8 = open(resonance_layer.__file__, encoding="utf-8").read()
        assert re.search(r'os\.getenv\("PRL_ENABLED",\s*"false"\)', src_m8), "M8 代码默认应关闭"


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
        # [2026-09-02] 注释一直写着"不应全为 0"，却从未断言内容——包装层按旧契约迭代
        # DataFrame 解包失败被吞、恒返回全 0 的 bug 因此漏网半个月。单边上涨必须出现 +1。
        assert (labels == 1).any(), f"单边上涨却无 +1 标签: {labels.value_counts().to_dict()}"
        assert not (labels == -1).any(), "单边上涨不应出现 -1"

    def test_triple_barrier_labels_match_raw_labeler(self):
        """[2026-09-02 回归] 包装层必须逐点等于底层 apply_triple_barrier 的 label 列，
        且在随机游走上三类标签都出现（否则 factor_evolution_loop 会静默回退前瞻收益）。"""
        import numpy as np
        from backend.services.labeling.triple_barrier import (
            TripleBarrierConfig, apply_triple_barrier,
        )
        rng = np.random.default_rng(0)
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 600)))
        idx = pd.date_range("2026-01-01", periods=600, freq="5min")
        df = pd.DataFrame({"close": close}, index=idx)
        labels = build_triple_barrier_labels(df, horizon_bars=12)
        raw = apply_triple_barrier(
            df["close"], events_index=df.index,
            config=TripleBarrierConfig(num_days=12, upper_mult=1.5, lower_mult=1.5,
                                       min_vol=0.0001, vol_lookback=20),
        )
        assert set(labels.unique()) == {-1, 0, 1}, labels.value_counts().to_dict()
        aligned = raw["label"].astype(int).reindex(df.index).fillna(0).astype(int)
        assert labels.equals(aligned), "包装层标签与底层打标器不一致"

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

    def test_disabled_returns_empty(self, monkeypatch):
        """开关关闭时 exposure() 返回空、status.enabled=False（fail-safe）。
        [2026-09-02] .env 已开启该功能，用例改为显式关闭 env 后验证（_exposure_enabled 每次读 env）。"""
        monkeypatch.setenv("FEATURE_FACTOR_EXPOSURE_ENABLED", "false")
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
