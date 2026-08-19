# -*- coding: utf-8 -*-
"""A4: 结构目标减仓 + 首仓 50% 补足 + 尖峰过滤。"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import numpy as np
import pandas as pd

from backend.services.long_tier_manager import decide_long
from backend.services import long_trend_v2 as lv2


def test_decide_long_target_reduce():
    d = decide_long(l1_state="up", close=110, stop=80, new_high=False, r_multiple=0.5,
                    target=105)
    assert d["action"] == "reduce" and d["ratio"] == 0.5


def test_decide_long_topup():
    d = decide_long(l1_state="up", close=100, stop=80, new_high=False, r_multiple=0.3,
                    hold_days=1.5, needs_topup=True, topup_ratio=0.5)
    assert d["action"] == "add" and d["topup"] is True and d["ratio"] == 0.5
    # 未满 24h 不补
    d2 = decide_long(l1_state="up", close=100, stop=80, new_high=False, r_multiple=0.3,
                     hold_days=0.5, needs_topup=True, topup_ratio=0.5)
    assert d2["action"] != "add"


def test_entry_signal_spike_filter():
    """pullback_z |z|>3 的单日暴涨 bar → hold 不追。"""
    n = 300
    rng = np.random.default_rng(2)
    close = pd.Series(100 * np.cumprod(1 + rng.normal(0.001, 0.02, n)))
    df = pd.DataFrame({"open": close * 0.99, "high": close * 1.02,
                       "low": close * 0.98, "close": close})
    fake_c = {"state": "up", "score": 4, "close": float(close.iloc[-1]), "target": 999.0}
    # 尖峰：mock timing_features 返回 pullback_z = 4.0
    fake_feat = pd.DataFrame({"pullback_z": [4.0] * n,
                              "macd_hist": [0.0] * n,
                              "macd_rising": [False] * n,
                              "vol_contract": [1.0] * n})
    with patch.object(lv2, "long_v2_enabled", return_value=True), \
         patch.object(lv2, "_get_l1_classification", return_value=(df, fake_c)), \
         patch("backend.services.entry_timing.timing_features", return_value=fake_feat):
        out = lv2.entry_signal("BTC")
    assert out["should_open"] is False and "尖峰" in out["hold_reason"]


def test_entry_signal_initial_fill_half():
    """首仓 size_hint_mult = 0.5（LONG_V2_INITIAL_FILL 默认 50%）。"""
    n = 300
    rng = np.random.default_rng(3)
    close = pd.Series(100 * np.cumprod(1 + rng.normal(0.001, 0.02, n)))
    df = pd.DataFrame({"open": close * 0.99, "high": close * 1.02,
                       "low": close * 0.98, "close": close})
    fake_c = {"state": "up", "score": 4, "close": float(close.iloc[-1]), "target": 999.0}
    fake_feat = pd.DataFrame({"pullback_z": [0.2] * n,
                              "macd_hist": [0.1] * n,
                              "macd_rising": [True] * n,
                              "vol_contract": [1.0] * n})
    with patch.object(lv2, "long_v2_enabled", return_value=True), \
         patch.object(lv2, "_get_l1_classification", return_value=(df, fake_c)), \
         patch.object(lv2, "weekly_atr", return_value=pd.Series([5.0] * n)), \
         patch("backend.services.entry_timing.timing_features", return_value=fake_feat):
        out = lv2.entry_signal("BTC")
    assert out["should_open"] is True
    assert out["size_hint_mult"] == 0.5, f"首仓应为 50%，got {out['size_hint_mult']}"
