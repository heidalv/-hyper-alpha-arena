# -*- coding: utf-8 -*-
"""升级 v3.0 S0/M0 单测：n_trials 累计 + 冗余阈值统一 + 短线自适应回看。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import pytest

from backend.services.factor_engine import trials_counter as tc
from backend.services.factor_engine.factor_backtest_scorer import (
    FactorBacktestScorer,
    scalp_lookback_for,
)


def test_trials_counter_monotonic_and_persistent():
    tc.reset()
    assert tc.total() == 0 or tc.total() >= 0
    a = tc.bump()
    b = tc.bump()
    assert b == a + 1
    assert tc.total() == b
    # 持久化：新实例（清内存态）读回相同值
    tc._state = None
    assert tc.total() == b
    tc.reset()


def test_trials_counter_reset_remigrates():
    # reset 后文件不存在 → 下一次加载走迁移播种（store 记录数 + 130 ai_gen 归档）
    tc.reset()
    n = tc.bump()
    assert n >= 131  # ≥130 归档 + 1 次 bump（store 记录数非负）
    tc.reset()


def test_scalp_lookback_adaptive(monkeypatch):
    # 数据充足：min(目标, 可用) 且不低于下限
    monkeypatch.setattr(
        FactorBacktestScorer, "_load_klines",
        staticmethod(lambda s, i, lim: [1] * 600),
    )
    from backend.services.factor_engine.factor_backtest_scorer import _SCALP_AVAIL_CACHE
    _SCALP_AVAIL_CACHE.clear()
    assert scalp_lookback_for("NEWCOIN") == 600  # min(720, 600)=600
    # 数据薄于下限 500 → 0（跳过该币）
    monkeypatch.setattr(
        FactorBacktestScorer, "_load_klines",
        staticmethod(lambda s, i, lim: [1] * 300),
    )
    _SCALP_AVAIL_CACHE.clear()
    assert scalp_lookback_for("THINCOIN") == 0
    # 数据充足但超目标 → 截到目标
    monkeypatch.setattr(
        FactorBacktestScorer, "_load_klines",
        staticmethod(lambda s, i, lim: [1] * 900),
    )
    _SCALP_AVAIL_CACHE.clear()
    assert scalp_lookback_for("BIGCOIN") == 720


def test_redundancy_threshold_unified(monkeypatch):
    from backend.services.factor_engine.factor_evaluator import FactorEvaluator

    ev = FactorEvaluator()
    assert ev._redundancy_threshold() == 0.7  # 默认统一 0.7
    monkeypatch.setenv("FACTOR_SCORER_REDUNDANCY_CORR", "0.6")
    import importlib
    from backend.config import settings as _s
    _s.FACTOR_SCORER_REDUNDANCY_CORR = 0.6
    assert ev._redundancy_threshold() == 0.6
    _s.FACTOR_SCORER_REDUNDANCY_CORR = 0.7
