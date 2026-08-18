# -*- coding: utf-8 -*-
"""升级 v3.0 S2/M3 单测：held-out 判决集两段晋升门。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.factor_engine.factor_backtest_scorer import (
    FactorBacktestScorer, FactorScoreResult,
)


class _FakeStore:
    """内存 store 替身（避免污染 data/discovered_factors.json）。"""

    def __init__(self, record):
        self.rec = dict(record)
        self.scores_written = {}
        self.status_written = None
        self.extra_written = None

    def get(self, factor_id, tenant_id=None):
        return dict(self.rec) if self.rec.get("factor_id") == factor_id else None

    def list_active(self, tenant_id=None):
        return []

    def update_scores(self, factor_id, grade, scores, status=None, tenant_id=None, extra_update=None):
        self.scores_written = scores
        self.status_written = status
        self.extra_written = extra_update
        self.rec["status"] = status
        self.rec["grade"] = grade
        return True


def _make_scorer(train_result, verdict_result):
    scorer = FactorBacktestScorer()
    calls = {"n": 0}

    def _fake_score(factor_id, formula, *args, **kwargs):
        calls["n"] += 1
        # 第 1 次 = 训练段；第 2 次 = 判决段
        return train_result if calls["n"] == 1 else verdict_result

    scorer.score_formula = _fake_score
    return scorer, calls


def test_heldout_pass_then_verdict_reject(monkeypatch):
    train = FactorScoreResult(factor_id="t", grade="B", admitted=True, ic_mean=0.06, icir=0.5)
    verdict = FactorScoreResult(
        factor_id="t", grade="B", admitted=False, ic_mean=0.01, icir=0.1,
        oos_sharpe=0.1, oos_trades=3,
    )
    scorer, calls = _make_scorer(train, verdict)
    store = _FakeStore({
        "factor_id": "ai_test_f", "formula": "ts_mean(close,20)/close-1",
        "extra": {"horizon": "midlong", "timeframe": "4h"}, "status": "candidate",
    })
    monkeypatch.setattr("backend.services.factor_engine.custom_factor_store.custom_factor_store", store)
    r = scorer.validate_and_promote("ai_test_f")
    assert calls["n"] == 2, "应跑训练段+判决段两次打分"
    assert r.admitted is False
    assert store.status_written == "candidate", "held-out 拒绝应留候选池（而非 rejected）"
    assert store.extra_written and store.extra_written["heldout"]["verdict"] == "reject"


def test_heldout_verdict_pass(monkeypatch):
    train = FactorScoreResult(factor_id="t", grade="A", admitted=True, ic_mean=0.08, icir=0.7)
    verdict = FactorScoreResult(
        factor_id="t", grade="A", admitted=True, ic_mean=0.05, icir=0.5,
        oos_sharpe=0.6, oos_trades=12,
    )
    scorer, calls = _make_scorer(train, verdict)
    store = _FakeStore({
        "factor_id": "ai_test_g", "formula": "ts_mean(close,20)/close-1",
        "extra": {"horizon": "midlong", "timeframe": "4h"}, "status": "candidate",
    })
    monkeypatch.setattr("backend.services.factor_engine.custom_factor_store.custom_factor_store", store)
    r = scorer.validate_and_promote("ai_test_g")
    assert calls["n"] == 2
    assert r.admitted is True
    assert store.extra_written and store.extra_written["heldout"]["verdict"] == "pass"


def test_heldout_disabled_single_pass(monkeypatch):
    from backend.config import settings as _s
    _s.FACTOR_HELDOUT_ENABLED = False
    try:
        train = FactorScoreResult(factor_id="t", grade="B", admitted=True, ic_mean=0.06, icir=0.5)
        verdict = FactorScoreResult(factor_id="t", grade="B", admitted=False)
        scorer, calls = _make_scorer(train, verdict)
        store = _FakeStore({
            "factor_id": "ai_test_h", "formula": "ts_mean(close,20)/close-1",
            "extra": {"horizon": "scalp"}, "status": "candidate",
        })
        monkeypatch.setattr("backend.services.factor_engine.custom_factor_store.custom_factor_store", store)
        r = scorer.validate_and_promote("ai_test_h")
        assert calls["n"] == 1, "held-out 关闭时只跑一次"
        assert r.admitted is True
    finally:
        _s.FACTOR_HELDOUT_ENABLED = True
