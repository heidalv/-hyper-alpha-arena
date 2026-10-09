# -*- coding: utf-8 -*-
"""[h896 2026-10-07] 逐币成绩单规则(decide)单测。

防回归:规则是「统计显著性驱动 + 自愈式(不永久拉黑)」——
每个分支都要有断言,且边界值(恰好压线)行为正确。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "symbol_scorecard", _ROOT / "scripts" / "tools" / "symbol_scorecard.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
decide = _mod.decide


def _s(n=0, avg=0.0, t=0.0):
    return {"n": n, "avg_bp": avg, "t": t, "pnl_usd": 0.0}


def test_disaster_probe():
    # n≥5 且 avg < −30 ⇒ 0.15(探针)
    assert decide(_s(5, -35.0), _s()) == (0.15, "disaster_probe")
    assert decide(_s(28, -35.8), _s()) == (0.15, "disaster_probe")
    # 样本不足 5 ⇒ 不算灾难
    assert decide(_s(4, -50.0), _s()) == (1.0, "default")


def test_persistent_loser():
    # n72≥12 且 avg72<−5 且 24h<0 ⇒ 0.25
    assert decide(_s(36, -14.3), _s(14, -15.6)) == (0.25, "persistent_loser")
    # 24h 已转正 ⇒ 不算持续亏损(恢复中,给 1.0 观察)
    assert decide(_s(36, -14.3), _s(14, +5.0))[1] != "persistent_loser"


def test_mild_loser():
    # −5 ≤ avg72 < −2 ⇒ 0.5
    assert decide(_s(48, -2.9), _s()) == (0.5, "mild_loser")
    assert decide(_s(48, -4.9), _s(10, +1.0)) == (0.5, "mild_loser")
    # 超过 −5(如 −5.5)且 24h<0 ⇒ 归 persistent(更严)
    assert decide(_s(48, -5.5), _s(10, -1.0)) == (0.25, "persistent_loser")
    # 恰好 −5.0 是 mild 的边界(规则用严格 < −5)
    assert decide(_s(48, -5.0), _s(10, -1.0)) == (0.5, "mild_loser")


def test_winner_boost():
    # n≥12 且 avg>+5 且 t>1.5 ⇒ 1.3
    assert decide(_s(40, 18.6, 3.2), _s()) == (1.3, "winner_boost")
    # t 不显著 ⇒ 不加码(防运气)
    assert decide(_s(40, 18.6, 1.0), _s()) == (1.0, "default")
    # 样本不足 ⇒ 不加码
    assert decide(_s(8, 30.0, 3.0), _s()) == (1.0, "default")


def test_default_no_data():
    assert decide(_s(0, 0.0), _s(0, 0.0)) == (1.0, "default")
    # 轻度正但不显著 ⇒ 默认
    assert decide(_s(20, 2.0, 0.5), _s()) == (1.0, "default")
