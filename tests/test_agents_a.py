# -*- coding: utf-8 -*-
"""p1-agents-a：SignalReview / Anomaly / Timing 三个 Agent 的纯逻辑单测（不连库）。

覆盖：
  统计核心      zscore / cusum / _spearman / _half_life_hours / max_drawdown_pct / _z_to_unit
  可信度门      样本不足 / 得分不足 / Brier 超标 → 强制降 observe
  流水线        observe 模式不执行 advice；analyze 抛错时 ok=False 且不落预测
  regime 分类   一致方向门槛（8 币 ≥4）、高波动优先、流动性只在无方向时生效
  到期评分      三个 kind 的评估器：判对 1.0、判错 0.0、样本不足 None（不造分）
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from backend.services.agents import anomaly_agent, signal_review, timing_agent
from backend.services.agents.base import (
    MODE_ADVISE,
    MODE_OBSERVE,
    Advice,
    CredibilityGate,
    ObservationAgent,
    Prediction,
    cusum,
    gate_verdict,
    zscore,
)


# ─────────────────────────── 统计核心 ───────────────────────────
def test_zscore_needs_samples_and_variance():
    assert zscore([1, 2, 3]) is None                  # 样本 < 8
    assert zscore([5] * 12) is None                   # 无波动
    # 基线零方差也返回 None：宁可不出分，也不把 0 标准差外推成无穷大 z
    assert zscore([1] * 10 + [9]) is None
    z = zscore([1, 2] * 5 + [20])
    assert z is not None and z > 3


def test_cusum_detects_upward_drift_only_when_sustained():
    flat = cusum([1.0] * 20)
    assert flat["detected"] is False
    drift = cusum([1.0] * 20 + [9.0] * 20)
    assert drift["detected"] is True and drift["direction"] != 0
    assert cusum([1, 2, 3])["detected"] is False       # 样本 < 12


def test_spearman_rank_correlation():
    xs = list(range(20))
    assert signal_review._spearman(xs, xs) == pytest.approx(1.0, abs=1e-9)
    assert signal_review._spearman(xs, xs[::-1]) == pytest.approx(-1.0, abs=1e-9)
    assert signal_review._spearman([1, 2, 3], [1, 2, 3]) is None   # n < 10
    assert signal_review._spearman([1] * 12, list(range(12))) is None  # 一侧无变化


def test_half_life_uses_peak_and_min_samples():
    curve = [
        {"bucket_h": 1, "n": 20, "avg_excess_bp": 40.0},
        {"bucket_h": 4, "n": 20, "avg_excess_bp": 20.0},
        {"bucket_h": 24, "n": 20, "avg_excess_bp": 5.0},
    ]
    assert signal_review._half_life_hours(curve, 10) == pytest.approx(4.0)
    # 样本不足的桶被剔除后不足两点 → None
    assert signal_review._half_life_hours(curve, 50) is None
    # 峰值为负 → 无半衰期可言
    neg = [{"bucket_h": 1, "n": 20, "avg_excess_bp": -3.0}, {"bucket_h": 4, "n": 20, "avg_excess_bp": -9.0}]
    assert signal_review._half_life_hours(neg, 10) is None


def test_max_drawdown_and_z_to_unit():
    assert anomaly_agent.max_drawdown_pct([100, 110, 88]) == pytest.approx(20.0)
    assert anomaly_agent.max_drawdown_pct([100, 101, 102]) == pytest.approx(0.0)
    assert anomaly_agent._z_to_unit(None) == 0.0
    assert anomaly_agent._z_to_unit(1.0) == 0.0            # 低于 soft
    assert anomaly_agent._z_to_unit(9.0) == 1.0            # 高于 hard
    assert anomaly_agent._z_to_unit(3.0) == pytest.approx(0.5)


# ─────────────────────────── 可信度门 ───────────────────────────
@pytest.mark.parametrize(
    "cred, expect_pass",
    [
        ({"n_scored": 10, "avg_score": 0.9, "avg_brier": 0.1}, False),   # 样本不足
        ({"n_scored": 50, "avg_score": 0.4, "avg_brier": 0.1}, False),   # 得分不足
        ({"n_scored": 50, "avg_score": 0.9, "avg_brier": 0.9}, False),   # Brier 超标
        ({"n_scored": 50, "avg_score": 0.9, "avg_brier": 0.1}, True),
    ],
)
def test_gate_verdict(cred, expect_pass):
    assert gate_verdict(cred, CredibilityGate())["passed"] is expect_pass


class _Dummy(ObservationAgent):
    agent_id = "_dummy"
    kinds = ("direction",)
    default_mode = MODE_ADVISE
    max_mode = MODE_ADVISE

    def __init__(self, boom: bool = False, **kw):
        super().__init__(**kw)
        self.boom = boom
        self.applied = 0

    def analyze(self, errors):
        if self.boom:
            raise RuntimeError("boom")
        return {"ok": 1}

    def predict(self, findings):
        return [Prediction(kind="direction", subject="BTC", prediction={"direction": 1}, horizon_ms=3600000)]

    def advise(self, findings):
        return [Advice(action="do_something", target="x", reason="test")]

    def apply_advice(self, adv, findings):
        self.applied += 1
        return "applied"


def _no_credibility(monkeypatch):
    monkeypatch.setattr("backend.services.agents.base.credibility_of",
                        lambda *a, **k: {"n": 0, "n_scored": 0, "avg_score": None, "avg_brier": None})


def test_low_credibility_forces_observe_and_skips_apply(monkeypatch):
    _no_credibility(monkeypatch)
    agent = _Dummy()
    res = agent.run(dry_run=True)
    assert res.configured_mode == MODE_ADVISE
    assert res.mode == MODE_OBSERVE and res.downgraded is True
    assert agent.applied == 0
    assert res.advice and res.advice[0]["applied"] is False


def test_dry_run_does_not_record_predictions(monkeypatch):
    _no_credibility(monkeypatch)
    called = []
    monkeypatch.setattr("backend.services.analysis.ledgers.record_prediction",
                        lambda **kw: called.append(kw) or "id1")
    res = _Dummy().run(dry_run=True)
    assert res.predictions == [] and called == []


def test_analyze_failure_marks_not_ok(monkeypatch):
    _no_credibility(monkeypatch)
    res = _Dummy(boom=True).run(dry_run=True)
    assert res.ok is False and res.predictions == []
    assert any("analyze" in e for e in res.errors)


# ─────────────────────────── regime 分类 ───────────────────────────
def _series(n: int, start: float, step: float) -> pd.DataFrame:
    close = [start + step * i for i in range(n)]
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"open": close, "high": [c * 1.01 for c in close],
                         "low": [c * 0.99 for c in close], "close": close}, index=idx)


def test_regime_trend_up_needs_majority_and_btc_above_ema200():
    data = {s: _series(300, 100.0, 1.0) for s in ("BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "LINK", "AVAX")}
    out = timing_agent.classify_regime(data)
    assert out["regime"] == "trend_up"
    assert out["consensus_needed"] == 4 and out["up_count"] >= 4
    assert out["btc_above_ema200"] is True


def test_regime_unknown_when_no_data():
    out = timing_agent.classify_regime({})
    assert out["regime"] == "unknown" and out["confidence"] < 0.5
    assert timing_agent.REGIME_BUCKETS[out["regime"]]["trend"] == 0.30


def test_regime_low_liquidity_only_without_direction():
    """8 币全部上行时，即使成交量枯竭也应判 trend_up（缩量不该盖过一致方向）。"""
    syms = ("BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "LINK", "AVAX")
    data = {s: _series(300, 100.0, 1.0) for s in syms}
    dry = {s: [1000.0] * 93 + [1.0] * 7 for s in syms}   # 7d 均量远低于 90d
    out = timing_agent.classify_regime(data, volumes=dry)
    assert out["regime"] == "trend_up"
    assert out["liquidity_ratio_7d_90d"] is not None and out["liquidity_ratio_7d_90d"] < 0.35


def test_bucket_weights_sum_to_one():
    for regime, w in timing_agent.REGIME_BUCKETS.items():
        assert sum(w.values()) == pytest.approx(1.0), regime
    engines = timing_agent._engine_capital("trend_up", timing_agent.REGIME_BUCKETS["trend_up"])
    assert {e["engine"] for e in engines} == {"E1", "E2", "E5", "E3"}
    assert sum(e["weight"] for e in engines) == pytest.approx(1.0)


# ─────────────────────────── 到期评分器 ───────────────────────────
def test_score_trading_state_rewards_correct_call(monkeypatch):
    monkeypatch.setattr(anomaly_agent, "btc_stress_window",
                        lambda lo, hi, **kw: {"n_bars": 24, "max_drawdown_pct": 9.0, "ret_pct": -8.0})
    row = {"created_ms": 0, "expires_ms": 1, "prediction": {"state": "reducing", "stress_dd_pct": 4.0}}
    assert anomaly_agent.score_trading_state(row)["score"] == 1.0
    row["prediction"] = {"state": "active", "stress_dd_pct": 4.0}
    assert anomaly_agent.score_trading_state(row)["score"] == 0.0


def test_score_trading_state_none_without_prices(monkeypatch):
    monkeypatch.setattr(anomaly_agent, "btc_stress_window", lambda lo, hi, **kw: None)
    row = {"created_ms": 0, "expires_ms": 1, "prediction": {"state": "reducing"}}
    assert anomaly_agent.score_trading_state(row) is None


def test_score_source_edge_direction_and_min_n(monkeypatch):
    monkeypatch.setattr(signal_review, "source_edge_since",
                        lambda src, lo, hi: {"n": 20, "avg_excess": 30.0, "hit_rate": 0.6})
    row = {"created_ms": 0, "expires_ms": 1, "subject": "s",
           "prediction": {"source": "s", "direction": 1, "min_n": 5, "expected_excess_bp": 10.0}}
    assert signal_review.score_source_edge(row)["score"] == 1.0
    row["prediction"]["direction"] = -1
    assert signal_review.score_source_edge(row)["score"] == 0.0

    monkeypatch.setattr(signal_review, "source_edge_since", lambda src, lo, hi: {"n": 2, "avg_excess": 30.0})
    row["prediction"]["direction"] = 1
    assert signal_review.score_source_edge(row) is None      # 窗口内样本不足 → 不造分


def test_score_regime_exact_family_and_miss(monkeypatch):
    monkeypatch.setattr(timing_agent, "load_daily", lambda syms, **kw: {"BTC": _series(300, 100.0, 1.0)})
    monkeypatch.setattr(timing_agent, "load_daily_volume", lambda syms, **kw: {})
    monkeypatch.setattr(timing_agent, "classify_regime",
                        lambda data, **kw: {"regime": "high_vol", "reason": "r", "up_count": 0, "down_count": 0})
    base = {"created_ms": 0, "expires_ms": 1}
    assert timing_agent.score_regime({**base, "prediction": {"regime": "high_vol"}})["score"] == 1.0
    assert timing_agent.score_regime({**base, "prediction": {"regime": "trend_down"}})["score"] == 0.5  # 同属 risk_off
    assert timing_agent.score_regime({**base, "prediction": {"regime": "trend_up"}})["score"] == 0.0


def test_score_regime_none_without_history(monkeypatch):
    monkeypatch.setattr(timing_agent, "load_daily", lambda syms, **kw: {"BTC": _series(10, 100.0, 1.0)})
    monkeypatch.setattr(timing_agent, "load_daily_volume", lambda syms, **kw: {})
    assert timing_agent.score_regime({"created_ms": 0, "expires_ms": 1,
                                      "prediction": {"regime": "trend_up"}}) is None


# ─────────────────────────── 注册与安全边界 ───────────────────────────
def test_all_agents_registered_and_capped_at_advise():
    from backend.services.agents.base import get_agent, registered_agents
    from backend.services.agents.jobs import ensure_registered

    ensure_registered()
    # 用包含而非相等：后续阶段会继续加 Agent（p2-agents-b 已加三个），
    # 但"任何 Agent 都不得拿到 act"这条边界对所有阶段都必须成立。
    assert {"signal_review", "anomaly", "timing"}.issubset(set(registered_agents()))
    for aid in registered_agents():
        agent = get_agent(aid)
        assert agent.max_mode == MODE_ADVISE, f"{aid} 不得拿到 act 模式"
        assert agent.configured_mode in (MODE_OBSERVE, MODE_ADVISE)


def test_evaluators_registered_for_every_kind():
    from backend.services.agents.jobs import ensure_evaluators
    from backend.services.analysis.ledgers import _OUTCOME_EVALUATORS

    ensure_evaluators()
    for kind in ("source_edge", "trading_state", "regime"):
        assert kind in _OUTCOME_EVALUATORS, f"{kind} 缺评估器，预测会一直挂 open 直到 void"


def test_anomaly_never_writes_trading_state():
    """观察模式铁律：apply_advice 也只返回说明，不触碰 TradingStateStore。"""
    agent = anomaly_agent.AnomalyAgent()
    note = agent.apply_advice(Advice(action="set_trading_state", target="halted"), {})
    assert "不直接写" in note
