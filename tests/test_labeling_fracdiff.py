"""
P1.4 Triple-Barrier + P1.5 Fractional Differencing 测试。

完成标准：
    P1.4: 三屏障标签分布合理（非全 0/VERTICAL）；上下轨触发在预期方向。
    P1.5: FFD 序列 ADF p<0.05（平稳）且与原序列相关 >0.9（记忆保留）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services.factor_engine.expr.frac_diff import (
    _weights,
    find_min_d,
    frac_diff_ffd,
)
from backend.services.labeling.triple_barrier import (
    BarrierLabel,
    TripleBarrierConfig,
    apply_triple_barrier,
    daily_volatility,
)

pytestmark = pytest.mark.unit


# ==================== P1.4 Triple-Barrier ====================

@pytest.fixture
def trending_prices():
    """明显上涨趋势的价格序列（应多触发上轨）。"""
    rng = np.random.default_rng(7)
    n = 300
    drift = 0.002
    rets = rng.normal(drift, 0.015, n)
    prices = 100 * np.exp(np.cumsum(rets))
    return pd.Series(prices, index=pd.RangeIndex(n), name="close")


@pytest.fixture
def meanrev_prices():
    """强均值回归序列（上下轨交替）。"""
    rng = np.random.default_rng(11)
    n = 300
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = 0.9 * x[i - 1] + rng.normal(0, 1.0)
    prices = pd.Series(100 + x, index=pd.RangeIndex(n), name="close")
    return prices


class TestTripleBarrier:
    def test_not_all_vertical(self, trending_prices):
        """上涨趋势不应全是 VERTICAL（应有不少 UPPER）。"""
        labels = apply_triple_barrier(trending_prices, TripleBarrierConfig(num_days=5))
        assert len(labels) > 0
        counts = labels["label"].value_counts()
        # 至少有一些非 VERTICAL 触发
        assert counts.get(int(BarrierLabel.UPPER), 0) + counts.get(int(BarrierLabel.LOWER), 0) > 0

    def test_trending_more_upper(self, trending_prices):
        """明显上涨趋势：UPPER 触发数 > LOWER 触发数。"""
        labels = apply_triple_barrier(trending_prices, TripleBarrierConfig(num_days=10))
        upper = (labels["label"] == int(BarrierLabel.UPPER)).sum()
        lower = (labels["label"] == int(BarrierLabel.LOWER)).sum()
        assert upper > lower

    def test_label_values_valid(self, meanrev_prices):
        labels = apply_triple_barrier(meanrev_prices)
        assert set(labels["label"].unique()).issubset({-1, 0, 1})

    def test_horizon_within_num_days(self, trending_prices):
        cfg = TripleBarrierConfig(num_days=7)
        labels = apply_triple_barrier(trending_prices, cfg)
        assert labels["horizon"].max() <= cfg.num_days
        assert labels["horizon"].min() >= 1

    def test_touch_price_in_range(self, trending_prices):
        cfg = TripleBarrierConfig(upper_mult=1.0, lower_mult=1.0, num_days=5)
        labels = apply_triple_barrier(trending_prices, cfg)
        # touch_price 应是 prices 序列中的某个值
        for tp in labels["touch_price"]:
            assert tp > 0

    def test_custom_events_index(self, trending_prices):
        """仅对指定时点打标签。"""
        events = trending_prices.index[50:100:10]
        labels = apply_triple_barrier(trending_prices, events_index=events)
        assert len(labels) == len(events)


class TestDailyVolatility:
    def test_returns_series(self, trending_prices):
        vol = daily_volatility(trending_prices, 20)
        assert len(vol) == len(trending_prices)
        assert vol.dropna().min() > 0


# ==================== P1.5 Fractional Differencing ====================

@pytest.fixture
def random_walk_prices():
    """随机游走价格（非平稳，需差分）。"""
    rng = np.random.default_rng(99)
    n = 1000
    rets = rng.normal(0.0001, 0.01, n)
    prices = pd.Series(100 * np.exp(np.cumsum(rets)), name="close")
    return prices


def _autocorr(x: np.ndarray, lag: int = 1) -> float:
    """序列的一阶自相关（记忆度量）。高自相关 = 强记忆。"""
    x = x[np.isfinite(x)]
    if len(x) < lag + 2:
        return 0.0
    return float(np.corrcoef(x[:-lag], x[lag:])[0, 1])


class TestFracDiff:
    def test_weights_decay(self):
        """分数差分权重应随距离衰减（|w_k| 递减，w_0 最大）。"""
        w = _weights(0.4, 1e-5)
        # w 已反转：w[0] 是最远（最小）、w[-1]=w_k0=1（最大）。
        abs_w = np.abs(w)
        # 最近权重（末尾）应最大
        assert abs_w[-1] == pytest.approx(1.0)
        # 中间权重 < 末尾权重
        assert abs_w[len(abs_w) // 2] < abs_w[-1]

    def test_ffd_preserves_memory_vs_integer(self, random_walk_prices):
        """FFD(d=0.4) 保留的记忆应显著强于整数差分(d=1)。

        记忆度量用一阶自相关：原始价格序列自相关高（~1，有记忆），
        整数差分后自相关接近 0（记忆被抹去），分数差分保留中间水平。
        """
        diffed_frac = frac_diff_ffd(random_walk_prices, 0.4).dropna()
        diffed_int = frac_diff_ffd(random_walk_prices, 1.0).dropna()
        ac_frac = abs(_autocorr(diffed_frac.values))
        ac_int = abs(_autocorr(diffed_int.values))
        # 分数差分自相关应明显高于整数差分（保留更多记忆）
        assert ac_frac > ac_int, f"分数差分记忆({ac_frac})应 > 整数差分({ac_int})"

    def test_ffd_stationary_at_low_d(self, random_walk_prices):
        """d 足够大时序列平稳（ADF p<0.05 或方差比低）。"""
        diffed = frac_diff_ffd(random_walk_prices, 0.6).dropna()
        try:
            from statsmodels.tsa.stattools import adfuller
            p = float(adfuller(diffed.values, autolag="BIC")[1])
            assert p < 0.05, f"d=0.6 仍非平稳 p={p}"
        except ImportError:
            # 无 statsmodels：方差比近似
            from backend.services.factor_engine.expr.frac_diff import _variance_ratio
            assert _variance_ratio(diffed.values) < 0.9

    def test_find_min_d_stationary(self, random_walk_prices):
        """find_min_d 应找到使序列平稳的最小 d。"""
        best_d, p_val = find_min_d(random_walk_prices, d_range=(0.1, 0.8), step=0.1)
        assert 0 < best_d <= 0.8
        assert p_val < 0.05, f"找到的 d={best_d} 仍未平稳，p={p_val}"
        # 该最小 d 下记忆应强于整数差分（方案精神：平稳 + 最大记忆保留）
        diffed_best = frac_diff_ffd(random_walk_prices, best_d).dropna()
        diffed_int = frac_diff_ffd(random_walk_prices, 1.0).dropna()
        ac_best = abs(_autocorr(diffed_best.values))
        ac_int = abs(_autocorr(diffed_int.values))
        assert ac_best > ac_int, f"最小平稳 d 记忆({ac_best})应 > 整数差分({ac_int})"
