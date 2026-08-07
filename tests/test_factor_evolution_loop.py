"""
因子进化闭环测试。
"""
from __future__ import annotations

import pytest
import numpy as np
import pandas as pd

from backend.services.evolution.factor_evolution_loop import (
    _forward_returns, _kline_to_fields, _mine_candidates,
    _evaluate_candidates, _purge_and_select, _promote_factors,
    run_factor_evolution_loop,
)


pytestmark = pytest.mark.unit


def _make_df(n=500, seed=42):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    volume = np.abs(rng.normal(1000, 200, n)) + 10
    idx = pd.date_range("2024-01-01", periods=n, freq="4h")
    return pd.DataFrame({"open": close, "high": high, "low": low,
                         "close": close, "volume": volume}, index=idx)


@pytest.fixture
def dfs():
    return {"BTC": _make_df(500, 42), "ETH": _make_df(500, 99)}


class TestForwardReturns:
    def test_shape(self):
        df = _make_df(100)
        fwd = _forward_returns(df, horizon=5)
        assert len(fwd) == 100

    def test_last_n_zero(self):
        df = _make_df(100)
        fwd = _forward_returns(df, horizon=5)
        assert fwd[-5:].sum() == 0


class TestMineCandidates:
    def test_produces_candidates(self):
        cands = _mine_candidates({})
        assert len(cands) > 5  # 至少反转+动量+波动率+量价

    def test_all_parseable(self):
        from backend.services.factor_engine.expr.parser import FactorExpr
        cands = _mine_candidates({})
        for expr, source in cands:
            assert isinstance(expr, FactorExpr), f"{source} 不是 FactorExpr"


class TestEvaluate:
    def test_evaluate_returns_results(self, dfs):
        cands = _mine_candidates(dfs)
        results = _evaluate_candidates(cands, dfs)
        assert len(results) > 0
        for fid, info in results.items():
            assert "avg_icir" in info
            assert "expr" in info
            assert info["n_symbols"] > 0


class TestPurge:
    def test_purge_runs(self, dfs):
        cands = _mine_candidates(dfs)
        results = _evaluate_candidates(cands, dfs)
        survivors = _purge_and_select(results, dfs)
        assert isinstance(survivors, list)


class TestPromote:
    def test_promote_runs(self, dfs):
        cands = _mine_candidates(dfs)
        results = _evaluate_candidates(cands, dfs)
        survivors = _purge_and_select(results, dfs)
        all_icir = [info.get("avg_icir", 0) for info in results.values()]
        promoted = _promote_factors(survivors, results, all_icir, len(cands))
        assert isinstance(promoted, list)


class TestFullLoop:
    def test_run_returns_report(self):
        """完整循环跑通（用 mock 数据避免依赖 DB）。"""
        import backend.services.evolution.factor_evolution_loop as mod
        original_load = mod._load_data
        mod._load_data = lambda *a, **kw: {"BTC": _make_df(300, 42), "ETH": _make_df(300, 99)}
        try:
            report = run_factor_evolution_loop()
            assert "candidates" in report
            assert report["candidates"] > 0
        finally:
            mod._load_data = original_load
