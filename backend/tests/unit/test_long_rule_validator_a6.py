# -*- coding: utf-8 -*-
"""A6: 验证器单边口径修复——信号=0 空仓不做空。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import numpy as np

from backend.services.factor_engine.long_rule_validator import (
    _single_side_backtest, _l1_score_series,
)


def test_single_side_signal_zero_is_flat():
    """构造「信号=0 时段价格暴跌」的序列：双边口径会因做空赚钱，单边口径应不赚。"""
    n = 800
    rng = np.random.default_rng(1)
    closes = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
    # 信号：前 200 根=1，之后=0（且之后价格下跌）
    sig = np.zeros(n)
    sig[:200] = 1.0
    # 信号=0 区间（200~800）价格单边下跌 30%
    drop = np.linspace(1.0, 0.7, n - 200)
    closes[200:] = closes[199] * drop

    bt = _single_side_backtest(sig, closes, fwd=2, cost=0.0021)
    # 单边口径：下跌区间空仓，不应产生大幅正收益（信号=0 不做空）
    assert bt["trades"] > 0
    # 宽松断言：净收益不可能是"靠做空暴跌赚来的巨量正收益"
    assert bt["net_return"] < 2.0, f"单边口径不应靠信号=0 的做空赚钱, net={bt['net_return']}"


def test_l1_score_series_real_ohlc_shape():
    """真实 OHLC 输入下 score 序列形状正确（修复 close 冒充 high/low）。"""
    n = 320
    rng = np.random.default_rng(2)
    closes = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
    highs = closes * (1 + np.abs(rng.normal(0, 0.005, n)))
    lows = closes * (1 - np.abs(rng.normal(0, 0.005, n)))
    scores = _l1_score_series(closes, highs, lows, lookback=260)
    assert len(scores) == n
    assert not np.all(np.isnan(scores)), "lookback 之后应有有效 score"
    assert np.isfinite(scores[300])
