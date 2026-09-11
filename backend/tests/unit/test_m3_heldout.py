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
    # [2026-08-22 M0-F1] 语义升级：训练段 A/B 级被 held-out 拒绝时不再永久滞留
    # 候选池，而是晋升为 role=paper 纸面影子（权重受 PAPER_FACTOR_WEIGHT_CAP 上限），
    # 由衰减复检与实盘 IC 反馈兜底——"让因子能进、能被消费、用实盘数据学习"。
    assert r.admitted is True
    assert store.status_written == "active", "A/B 级 held-out 拒绝 → 纸面影子晋升 active(role=paper)"
    assert store.extra_written and store.extra_written["heldout"]["verdict"] == "reject"
    assert store.extra_written.get("role") == "paper"


def test_heldout_verdict_pass(monkeypatch):
    """[2026-09-02 E14 契约同步] 判决段必须给出正的费后净收益才算 pass。

    08-29 的 P3.3 给 held-out 判决加了硬条件 ``oos_net_return > 0``
    （开关 FACTOR_HELDOUT_REQUIRE_NET，默认 True），因为诊断实证发现 active
    因子的 oos_net_return 普遍为负却仍停在 PAPER 态 —— 评估口径与费后盈利脱节。
    本用例原先没设 oos_net_return（默认 0.0），在新契约下就代表「不赚钱」，
    判 reject 是正确的；补上正净收益后才真正测到「判决通过」这条路径。
    """
    train = FactorScoreResult(factor_id="t", grade="A", admitted=True, ic_mean=0.08, icir=0.7)
    verdict = FactorScoreResult(
        factor_id="t", grade="A", admitted=True, ic_mean=0.05, icir=0.5,
        oos_sharpe=0.6, oos_trades=12, oos_net_return=0.012,
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


def test_heldout_rejects_negative_net_return(monkeypatch):
    """[2026-09-02 E14] IC/Sharpe 全达标但费后净收益为负 → 判决必须拒绝。

    正向锁定 08-29 P3.3 的硬条件：这正是「因子指标好看、实盘不赚钱」的病灶，
    若有人把该条件回滚，本用例会立刻变红。
    """
    train = FactorScoreResult(factor_id="t", grade="A", admitted=True, ic_mean=0.08, icir=0.7)
    verdict = FactorScoreResult(
        factor_id="t", grade="A", admitted=True, ic_mean=0.05, icir=0.5,
        oos_sharpe=0.6, oos_trades=12, oos_net_return=-0.008,
    )
    scorer, calls = _make_scorer(train, verdict)
    store = _FakeStore({
        "factor_id": "ai_test_h", "formula": "ts_mean(close,20)/close-1",
        "extra": {"horizon": "midlong", "timeframe": "4h"}, "status": "candidate",
    })
    monkeypatch.setattr(
        "backend.services.factor_engine.custom_factor_store.custom_factor_store", store)

    scorer.validate_and_promote("ai_test_h")

    assert calls["n"] == 2
    assert store.extra_written["heldout"]["verdict"] == "reject", (
        "费后亏钱的因子不得通过 held-out 判决"
    )
    assert store.extra_written["heldout"]["net_return"] == -0.008, (
        "判决记录必须落 net_return，否则运维无法看出拒绝理由"
    )


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
