# -*- coding: utf-8 -*-
"""[P3.2 2026-09-03] 晋升回测"延迟一根成交"稳健性指标。

审查员曾主张把成交价改成下一根开盘价（"同根收盘成交=前视"）。加密永续 24/7、
open[t+1]≈close[t]，线上也是 closed_only 后立即市价，口径本就一致——真实风险是
**执行延迟**。正确做法不是改成交价，而是并列跑一遍"延迟一根成交"的对照，
看边际是否留存（lag1_retention），并可选作为门禁（FACTOR_SCORER_LAG1_GATE）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import numpy as np
import pytest

from backend.services.factor_engine.factor_backtest_scorer import FactorBacktestScorer


def _closes_from_returns(ret: np.ndarray) -> np.ndarray:
    return np.cumprod(1.0 + ret) * 100.0


class TestWalkForwardEntryLag:
    def test_default_lag0_identical_to_explicit(self):
        rng = np.random.default_rng(1)
        n = 600
        ret = rng.normal(0, 0.002, n)
        closes = _closes_from_returns(ret)
        factor = np.roll(ret, -1)  # 完美预知下一根
        s = FactorBacktestScorer()
        a = FactorBacktestScorer._walk_forward_backtest(s, factor, closes, 1, 0.0005)
        b = FactorBacktestScorer._walk_forward_backtest(s, factor, closes, 1, 0.0005, entry_lag=0)
        assert a == b
        assert a["trades"] > 0 and a["net_return"] > 0

    def test_lag1_kills_one_bar_foresight_edge(self):
        """因子只知道下一根：lag0 大赚；延迟一根成交后边际消失（≤0）。"""
        rng = np.random.default_rng(2)
        n = 900
        ret = rng.normal(0, 0.002, n)
        closes = _closes_from_returns(ret)
        factor = np.roll(ret, -1)
        s = FactorBacktestScorer()
        lag0 = FactorBacktestScorer._walk_forward_backtest(s, factor, closes, 1, 0.0005)
        lag1 = FactorBacktestScorer._walk_forward_backtest(s, factor, closes, 1, 0.0005, entry_lag=1)
        assert lag0["net_return"] > 0.1
        assert lag1["net_return"] < lag0["net_return"] * 0.2
        assert lag1["trades"] > 0

    def test_lag1_preserves_persistent_edge(self):
        """边际来自持续多根的漂移（regime）：延迟一根几乎不损失。"""
        rng = np.random.default_rng(3)
        n = 1200
        regime = np.repeat(np.where(np.arange(n // 60) % 2 == 0, 1.0, -1.0), 60)
        ret = regime * 0.0015 + rng.normal(0, 0.0008, n)
        closes = _closes_from_returns(ret)
        factor = regime + rng.normal(0, 0.1, n)
        s = FactorBacktestScorer()
        lag0 = FactorBacktestScorer._walk_forward_backtest(s, factor, closes, 3, 0.0005)
        lag1 = FactorBacktestScorer._walk_forward_backtest(s, factor, closes, 3, 0.0005, entry_lag=1)
        assert lag0["net_return"] > 0
        assert lag1["net_return"] > 0.6 * lag0["net_return"]

    def test_lag1_return_alignment(self):
        """lag=1、fwd=2：第 t 笔应对应 closes[t+3]/closes[t+1]−1（进 t+1、出 t+3）。"""
        closes = np.array([100, 101, 103, 106, 110, 115, 121, 128, 136, 145], dtype=float)
        n = len(closes)
        fwd, lag = 2, 1
        span = fwd + lag
        expect = np.full(n, np.nan)
        expect[: n - span] = (closes[span:] - closes[lag: n - fwd]) / closes[lag: n - fwd]
        for t in range(n - span):
            assert expect[t] == pytest.approx(closes[t + 3] / closes[t + 1] - 1.0)
        assert np.isnan(expect[n - span:]).all()


class TestScoreFormulaLag1:
    def _stub(self, monkeypatch, scorer, closes, factor):
        monkeypatch.setattr(
            scorer, "_load_klines",
            lambda sym, tf, lb: [{"close": float(c)} for c in closes[-lb:]],
            raising=False,
        )
        monkeypatch.setattr(
            scorer, "_eval_formula",
            lambda formula, arrays: factor[-len(arrays["close"]):],
            raising=False,
        )
        monkeypatch.setattr(
            scorer, "_active_factor_series",
            lambda arrays_by_symbol, pool=None: {},
            raising=False,
        )
        # IC/ICIR 评估器桩：本测试只关心 walk-forward 与 lag1 对照，IC 口径给定为达标值
        import types
        from backend.services.factor_engine import factor_evaluator as _fe

        class _Rep:
            ic_mean = 0.08
            icir = 0.9
            ic_decay_halflife = 5
            monotonicity = 0.5
            data_points = 500

        fake_eval = types.SimpleNamespace(evaluate_factor=lambda *a, **k: _Rep())
        monkeypatch.setattr(_fe, "get_factor_evaluator", lambda *a, **k: fake_eval)

    def _one_bar_foresight(self):
        rng = np.random.default_rng(4)
        n = 800
        ret = rng.normal(0, 0.002, n)
        return _closes_from_returns(ret), np.roll(ret, -1)

    def test_metrics_recorded_and_gate_off_by_default(self, monkeypatch):
        from backend.config import settings as _s
        monkeypatch.setattr(_s, "FACTOR_SCORER_LAG1_ENABLED", True, raising=False)
        monkeypatch.setattr(_s, "FACTOR_SCORER_LAG1_GATE", False, raising=False)
        monkeypatch.setattr(_s, "FACTOR_SCORER_LAG1_MIN_RETENTION", 0.3, raising=False)
        closes, factor = self._one_bar_foresight()
        scorer = FactorBacktestScorer()
        self._stub(monkeypatch, scorer, closes, factor)
        r = FactorBacktestScorer.score_formula(
            scorer, "ai_test_lag1", "fake", symbols=["BTC", "ETH", "SOL"],
            interval="1h", lookback=700, fwd=1, cost=0.0005,
            dsr_required=False, skip_dsr=True,
        )
        assert r.oos_net_return > 0
        assert r.lag1_retention is not None and r.lag1_retention < 0.3
        assert r.lag1_fragile is True
        assert r.grade in ("A", "B") and r.admitted, (r.grade, r.reason)  # 门关着：只记指标
        assert "lag1_net=" in r.reason and "fragile" in r.reason
        for sym in ("BTC", "ETH", "SOL"):
            assert "lag1" in r.per_symbol[sym]

    def test_gate_on_downgrades_fragile_candidate(self, monkeypatch):
        from backend.config import settings as _s
        monkeypatch.setattr(_s, "FACTOR_SCORER_LAG1_ENABLED", True, raising=False)
        monkeypatch.setattr(_s, "FACTOR_SCORER_LAG1_GATE", True, raising=False)
        monkeypatch.setattr(_s, "FACTOR_SCORER_LAG1_MIN_RETENTION", 0.3, raising=False)
        closes, factor = self._one_bar_foresight()
        scorer = FactorBacktestScorer()
        self._stub(monkeypatch, scorer, closes, factor)
        r = FactorBacktestScorer.score_formula(
            scorer, "ai_test_lag1_gate", "fake", symbols=["BTC", "ETH", "SOL"],
            interval="1h", lookback=700, fwd=1, cost=0.0005,
            dsr_required=False, skip_dsr=True,
        )
        assert r.lag1_fragile is True
        assert r.grade == "C" and not r.admitted
        assert "延迟一根成交" in r.reason

    def test_gate_on_keeps_robust_candidate(self, monkeypatch):
        from backend.config import settings as _s
        monkeypatch.setattr(_s, "FACTOR_SCORER_LAG1_ENABLED", True, raising=False)
        monkeypatch.setattr(_s, "FACTOR_SCORER_LAG1_GATE", True, raising=False)
        monkeypatch.setattr(_s, "FACTOR_SCORER_LAG1_MIN_RETENTION", 0.3, raising=False)
        rng = np.random.default_rng(5)
        n = 1200
        regime = np.repeat(np.where(np.arange(n // 60) % 2 == 0, 1.0, -1.0), 60)
        ret = regime * 0.0015 + rng.normal(0, 0.0008, n)
        closes = _closes_from_returns(ret)
        factor = regime + rng.normal(0, 0.1, n)
        scorer = FactorBacktestScorer()
        self._stub(monkeypatch, scorer, closes, factor)
        r = FactorBacktestScorer.score_formula(
            scorer, "ai_test_lag1_robust", "fake", symbols=["BTC", "ETH", "SOL"],
            interval="1h", lookback=1100, fwd=3, cost=0.0005,
            dsr_required=False, skip_dsr=True,
        )
        assert r.lag1_fragile is False, (r.lag1_retention, r.reason)
        assert r.grade in ("A", "B"), (r.grade, r.reason)

    def test_disabled_skips_metric(self, monkeypatch):
        from backend.config import settings as _s
        monkeypatch.setattr(_s, "FACTOR_SCORER_LAG1_ENABLED", False, raising=False)
        closes, factor = self._one_bar_foresight()
        scorer = FactorBacktestScorer()
        self._stub(monkeypatch, scorer, closes, factor)
        r = FactorBacktestScorer.score_formula(
            scorer, "ai_test_lag1_off", "fake", symbols=["BTC", "ETH", "SOL"],
            interval="1h", lookback=700, fwd=1, cost=0.0005,
            dsr_required=False, skip_dsr=True,
        )
        assert r.lag1_retention is None and r.lag1_fragile is False
        assert "lag1_net=" not in r.reason

    def test_settings_defaults(self):
        from backend.config import settings as _s
        assert hasattr(_s, "FACTOR_SCORER_LAG1_ENABLED")
        assert hasattr(_s, "FACTOR_SCORER_LAG1_GATE")
        assert hasattr(_s, "FACTOR_SCORER_LAG1_MIN_RETENTION")

    def test_scores_payload_includes_lag1(self):
        import inspect
        src = inspect.getsource(FactorBacktestScorer)
        assert '"lag1_net_return": result.lag1_net_return' in src
        assert '"lag1_fragile": bool(result.lag1_fragile)' in src
