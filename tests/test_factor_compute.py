"""
FactorComputeAgent 测试 —— 真实 K线数据接到表达式因子引擎。

验证：DataFrame OHLCV → fields → DSL 计算 → 契约 FactorVector。
这是把"抽象 DSL"接到"真实市场数据"的接入层测试。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services.alpha.factor_compute import (
    FactorComputeAgent,
    kline_df_to_fields,
)
from backend.services.contracts.types import FactorVector, Instrument

pytestmark = pytest.mark.unit


@pytest.fixture
def kline_df():
    """合成 K线 DataFrame（OHLCV，200 根）。"""
    rng = np.random.default_rng(42)
    n = 200
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    open_ = close * (1 + rng.normal(0, 0.002, n))
    volume = np.abs(rng.normal(1000, 200, n)) + 10
    idx = pd.date_range("2024-01-01", periods=n, freq="1h")
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close,
        "volume": volume, "funding_rate": rng.normal(0.0001, 0.0005, n),
        "open_interest": np.abs(rng.normal(1e8, 1e7, n)),
    }, index=idx)


@pytest.fixture
def instrument():
    return Instrument(symbol="BTC-PERP", venue="binance", kind="perp")


class TestKlineToFields:
    def test_extracts_ohlcv(self, kline_df):
        fields = kline_df_to_fields(kline_df)
        assert "open" in fields
        assert "high" in fields
        assert "low" in fields
        assert "close" in fields
        assert "volume" in fields

    def test_derives_returns(self, kline_df):
        fields = kline_df_to_fields(kline_df)
        assert "returns" in fields
        assert len(fields["returns"]) == len(kline_df)
        # returns[0] = 0（首点无前值）
        assert fields["returns"][0] == 0.0

    def test_derives_vwap(self, kline_df):
        fields = kline_df_to_fields(kline_df)
        assert "vwap" in fields

    def test_maps_funding_oi(self, kline_df):
        fields = kline_df_to_fields(kline_df)
        assert "funding" in fields   # funding_rate → funding
        assert "oi" in fields        # open_interest → oi


class TestFactorComputeAgent:
    def test_compute_returns_factor_vector(self, kline_df, instrument):
        agent = FactorComputeAgent(instrument=instrument)
        agent.register("simple_mom", {"op": "mean", "args": [{"f": "returns"}, {"c": 10}]})
        fv = agent.compute(kline_df)
        assert isinstance(fv, FactorVector)
        assert "simple_mom" in fv.values
        assert "simple_mom" in fv.expr_ids  # 可追溯
        assert fv.instrument.symbol == "BTC-PERP"

    def test_perp_defaults(self, kline_df, instrument):
        """注册永续因子集后全部可计算。"""
        agent = FactorComputeAgent(instrument=instrument)
        agent.register_perp_defaults()
        fv = agent.compute(kline_df)
        # 永续因子至少有几个计算出非零值
        assert len(fv.values) >= 5
        # funding_skew 应存在
        assert "funding_skew" in fv.values

    def test_compute_series_full_array(self, kline_df, instrument):
        """compute_series 返回完整数组（回测用）。"""
        agent = FactorComputeAgent(instrument=instrument)
        agent.register("mom", {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]})
        series = agent.compute_series(kline_df)
        assert "mom" in series
        assert len(series["mom"]) == len(kline_df)  # 完整长度

    def test_cache_hit_on_repeat(self, kline_df, instrument):
        """同 window_id 重复算命中缓存。"""
        from backend.services.factor_engine.expr.parser import ExpressionCache
        cache = ExpressionCache()
        agent = FactorComputeAgent(instrument=instrument, cache=cache)
        agent.register("mom", {"op": "rank", "args": [{"op": "corr", "args": [{"f": "close"}, {"f": "volume"}, {"c": 5}]}]})
        agent.compute(kline_df, window_id="w1")
        assert cache.misses == 1
        agent.compute(kline_df, window_id="w1")
        assert cache.hits == 1

    def test_invalid_factor_logged_not_crash(self, kline_df, instrument):
        """因子计算失败不崩（记日志，填 0）。"""
        agent = FactorComputeAgent(instrument=instrument)
        # 用一个合法但字段缺失的表达式（liquidation 字段不在 K线里）
        agent.register("liq_rank", {"op": "ts_rank", "args": [{"f": "liquidation"}, {"c": 10}]})
        fv = agent.compute(kline_df)
        # 不崩，值填 0
        assert "liq_rank" in fv.values

    def test_multiple_factors(self, kline_df, instrument):
        agent = FactorComputeAgent(instrument=instrument)
        agent.register("f1", {"op": "mean", "args": [{"f": "close"}, {"c": 10}]})
        agent.register("f2", {"op": "std", "args": [{"f": "returns"}, {"c": 20}]})
        agent.register("f3", {"op": "rank", "args": [{"op": "corr", "args": [{"f": "close"}, {"f": "volume"}, {"c": 15}]}]})
        fv = agent.compute(kline_df)
        assert len(fv.values) == 3
        assert all(name in fv.expr_ids for name in ["f1", "f2", "f3"])

    def test_end_to_end_with_alpha_ensemble(self, kline_df, instrument):
        """FactorCompute 产出喂给 AlphaEnsemble（真实数据→因子→信号）。"""
        from backend.services.alpha.ensemble import AlphaEnsemble
        from backend.services.contracts.types import Direction

        agent = FactorComputeAgent(instrument=instrument)
        agent.register("momentum", {"op": "mean", "args": [{"f": "returns"}, {"c": 10}]})
        agent.register("vol_adj_mom", {"op": "div", "args": [
            {"op": "mean", "args": [{"f": "returns"}, {"c": 10}]},
            {"op": "std", "args": [{"f": "returns"}, {"c": 20}]},
        ]})
        fv = agent.compute(kline_df)

        # AlphaEnsemble 消费 FactorVector
        class MomentumModel:
            name = "lightgbm"
            def predict_direction(self, fv):
                v = fv.values.get("momentum", 0)
                return (Direction.LONG if v > 0 else Direction.SHORT), abs(min(0.9, v * 100))

        ens = AlphaEnsemble()
        ens.register(MomentumModel())
        pred = ens.predict(fv, regime="trend_low_vol")
        assert pred.direction in (Direction.LONG, Direction.SHORT, Direction.FLAT)
