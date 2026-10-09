# -*- coding: utf-8 -*-
"""[h901 2026-10-07] 统一进化总督单测:归因 / 提案护栏 / 单变量纪律。"""
from __future__ import annotations

from backend.services.evolution.governor import (
    GOVERNOR_PARAMS, Finding, Proposal, attribute, propose,
)


def _perf(pl_ratio=0.4, n=100, pnl=-2.0, avg_win=4.0, avg_loss=-12.0):
    return {"hours": 6, "n": n, "pnl": pnl, "avg_win": avg_win,
            "avg_loss": avg_loss, "n_win": int(n * 0.57), "n_loss": int(n * 0.43),
            "win_rate": 0.57, "pl_ratio": pl_ratio, "by_symbol": {}, "by_path": {}}


def test_attribute_finds_pl_ratio_problem():
    perf = _perf(pl_ratio=0.4)
    findings = attribute(perf)
    assert any(f.subject == "盈亏比" for f in findings)


def test_attribute_no_problem_when_healthy():
    perf = _perf(pl_ratio=1.5, pnl=10.0)
    findings = attribute(perf)
    assert not any(f.subject == "盈亏比" for f in findings)


def test_propose_tightens_taker_cap_on_bad_pl():
    perf = _perf(pl_ratio=0.4)
    findings = attribute(perf)
    cur = {"stop_taker_bp": 100.0, "take_profit_bp": 60.0, "stop_loss_bp": 40.0}
    p = propose(findings, cur, perf)
    assert p is not None and p.param == "stop_taker_bp"
    assert p.new < p.old           # 收紧
    lo, hi = GOVERNOR_PARAMS["stop_taker_bp"]
    assert lo <= p.new <= hi       # 在边界内


def test_propose_respects_min_evidence():
    perf = _perf(pl_ratio=0.4, n=10)   # 样本太少
    findings = attribute(perf)
    # 样本 <30 ⇒ 盈亏比维度不产生提案
    p = propose(findings, {"stop_taker_bp": 100.0}, perf)
    assert p is None


def test_propose_none_when_no_findings():
    assert propose([], {"stop_taker_bp": 100.0}, _perf()) is None
