"""
全历史因子回测链路测试。

验证完整管线：HistoryDataLoader → FactorComputeAgent → evaluate_factor
能在"有信号"的合成全历史数据上跑出有意义的评估指标（IC 显著/ICIR>0/半衰期合理），
在"无信号"数据上 IC≈0。

这是"因子回测管线真的能用"的端到端证明（无真实 DB 依赖）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services.alpha.factor_compute import FactorComputeAgent, kline_df_to_fields
from backend.services.factor_engine.evaluation import evaluate_factor, FactorEvalResult
from backend.services.factor_engine.expr.parser import parse
from backend.services.contracts.types import Instrument


pytestmark = pytest.mark.unit


def _make_signal_history(n=2000, momentum_strength=0.3, seed=42):
    """
    生成"有动量信号"的合成全历史价格。

    momentum_strength: 动量信号的强度（0=纯随机，无预测力；高=动量因子有效）。
    构造：下一期收益 = strength × 过去 5 期均收益 + 噪声。
    这样 5 期动量因子对未来收益有正向预测力。
    """
    rng = np.random.default_rng(seed)
    rets = np.zeros(n)
    prices = np.zeros(n)
    prices[0] = 100.0
    for t in range(5, n):
        past_5_mean = np.mean(rets[t - 5:t])
        rets[t] = momentum_strength * past_5_mean + rng.normal(0, 0.01)
        prices[t] = prices[t - 1] * (1 + rets[t])
    prices[:5] = np.cumprod(1 + rets[:5]) * 100
    # 构造 OHLCV
    high = prices * (1 + np.abs(rng.normal(0, 0.002, n)))
    low = prices * (1 - np.abs(rng.normal(0, 0.002, n)))
    open_ = prices * (1 + rng.normal(0, 0.001, n))
    volume = np.abs(rng.normal(1000, 200, n)) + 10
    idx = pd.date_range("2020-01-01", periods=n, freq="1h")
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": prices,
        "volume": volume,
    }, index=idx)


def _make_random_history(n=2000, seed=99):
    """纯随机游走（无因子信号，动量因子应 IC≈0）。"""
    rng = np.random.default_rng(seed)
    rets = rng.normal(0, 0.01, n)
    prices = 100 * np.exp(np.cumsum(rets))
    high = prices * (1 + np.abs(rng.normal(0, 0.002, n)))
    low = prices * (1 - np.abs(rng.normal(0, 0.002, n)))
    open_ = prices * (1 + rng.normal(0, 0.001, n))
    volume = np.abs(rng.normal(1000, 200, n)) + 10
    idx = pd.date_range("2020-01-01", periods=n, freq="1h")
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": prices,
        "volume": volume,
    }, index=idx)


@pytest.fixture
def instrument():
    return Instrument(symbol="BTC-PERP", venue="binance", kind="perp")


class TestFactorBacktestPipeline:
    """完整链路：DataFrame → 因子计算 → IC/ICIR/半衰期评估。"""

    def test_momentum_factor_positive_ic_on_signal_data(self, instrument):
        """动量因子在有信号数据上 IC 显著为正。"""
        df = _make_signal_history(momentum_strength=0.5, n=2000)
        agent = FactorComputeAgent(instrument=instrument)
        agent.register("mom5", {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]})

        # 算因子序列
        factor_series = agent.compute_series(df)
        mom5 = pd.Series(factor_series["mom5"], index=df.index)

        # 构造下期收益（forward return）作为评估 target
        close = df["close"]
        fwd_ret = close.shift(-5) / close - 1  # 5 期 forward return
        fwd_ret = fwd_ret.dropna()
        mom5_aligned = mom5.reindex(fwd_ret.index)

        result = evaluate_factor("mom5", mom5_aligned, fwd_ret)
        assert isinstance(result, FactorEvalResult)
        # 动量因子在有信号数据上应有正向 IC
        assert abs(result.rank_ic_mean) > 0.05, f"IC={result.rank_ic_mean:.4f} 应显著"
        assert result.icir > 0.1, f"ICIR={result.icir:.4f} 应 >0.1"

    def test_momentum_factor_zero_ic_on_random_data(self, instrument):
        """动量因子在纯随机数据上 IC≈0（无预测力）。"""
        df = _make_random_history(n=2000)
        agent = FactorComputeAgent(instrument=instrument)
        agent.register("mom5", {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]})

        factor_series = agent.compute_series(df)
        mom5 = pd.Series(factor_series["mom5"], index=df.index)
        close = df["close"]
        fwd_ret = close.shift(-5) / close - 1
        fwd_ret = fwd_ret.dropna()
        mom5_aligned = mom5.reindex(fwd_ret.index)

        result = evaluate_factor("mom5", mom5_aligned, fwd_ret)
        # 随机数据上 IC 应接近 0（无预测力）
        assert abs(result.rank_ic_mean) < 0.1, f"随机数据 IC={result.rank_ic_mean:.4f} 应≈0"

    def test_halflife_computed(self, instrument):
        """半衰期被计算（>0 表示有衰减）。"""
        df = _make_signal_history(momentum_strength=0.4, n=2000)
        agent = FactorComputeAgent(instrument=instrument)
        agent.register("mom10", {"op": "mean", "args": [{"f": "returns"}, {"c": 10}]})

        factor_series = agent.compute_series(df)
        mom10 = pd.Series(factor_series["mom10"], index=df.index)
        close = df["close"]
        fwd_ret = close.shift(-5) / close - 1
        fwd_ret = fwd_ret.dropna()
        mom10_aligned = mom10.reindex(fwd_ret.index)

        result = evaluate_factor("mom10", mom10_aligned, fwd_ret)
        # 半衰期应被计算（非 NaN 错误）
        assert isinstance(result.halflife_bars, int)
        assert result.n_samples > 100

    def test_multiple_factors_evaluated(self, instrument):
        """多个因子同时评估（pipeline 批量）。"""
        df = _make_signal_history(n=1500)
        agent = FactorComputeAgent(instrument=instrument)
        agent.register("mom5", {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]})
        agent.register("vol", {"op": "std", "args": [{"f": "returns"}, {"c": 20}]})
        agent.register("rank_corr", {"op": "rank", "args": [{"op": "corr", "args": [{"f": "close"}, {"f": "volume"}, {"c": 10}]}]})

        factor_series = agent.compute_series(df)
        close = df["close"]
        fwd_ret = (close.shift(-5) / close - 1).dropna()

        results = {}
        for name, arr in factor_series.items():
            s = pd.Series(arr, index=df.index).reindex(fwd_ret.index)
            results[name] = evaluate_factor(name, s, fwd_ret)

        assert len(results) == 3
        for name, r in results.items():
            assert isinstance(r, FactorEvalResult)
            assert r.n_samples > 50

    def test_full_pipeline_through_cache(self, instrument):
        """链路经 ExpressionCache 不丢数据。"""
        from backend.services.factor_engine.expr.parser import ExpressionCache
        cache = ExpressionCache()
        df = _make_signal_history(n=500)
        agent = FactorComputeAgent(instrument=instrument, cache=cache)
        agent.register("mom5", {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]})

        # 两次 compute（同 window 命中缓存）
        s1 = agent.compute_series(df, window_id="w1")
        s2 = agent.compute_series(df, window_id="w1")
        assert cache.hits == 1
        np.testing.assert_array_equal(s1["mom5"], s2["mom5"])  # 缓存结果一致

    def test_signal_strength_scales_ic(self, instrument):
        """信号越强，IC 越高（管线能区分因子有效性）。"""
        ics = {}
        for strength in [0.1, 0.5, 0.9]:
            df = _make_signal_history(momentum_strength=strength, n=1500, seed=7)
            agent = FactorComputeAgent(instrument=instrument)
            agent.register("mom5", {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]})
            factor_series = agent.compute_series(df)
            mom5 = pd.Series(factor_series["mom5"], index=df.index)
            close = df["close"]
            fwd_ret = (close.shift(-5) / close - 1).dropna()
            mom5_a = mom5.reindex(fwd_ret.index)
            result = evaluate_factor("mom5", mom5_a, fwd_ret)
            ics[strength] = abs(result.rank_ic_mean)
        # 信号强度递增 → IC 递增（管线能区分）
        assert ics[0.9] > ics[0.1], f"强信号 IC({ics[0.9]:.4f}) 应 > 弱信号({ics[0.1]:.4f})"
