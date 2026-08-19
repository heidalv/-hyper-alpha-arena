# -*- coding: utf-8 -*-
"""A2/A3: 回测/实盘同核 decide_long + 兜底分支 + Chandelier entry_price。"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import numpy as np
import pandas as pd

from backend.services.long_tier_manager import decide_long, chandelier_long_stop
from backend.services import long_trend_v2 as lv2


def test_decide_long_structure_break_closes():
    d = decide_long(l1_state="sideways", close=100, stop=90, new_high=False, r_multiple=0.5)
    assert d["action"] == "close" and "结构破坏" in d["reason"]


def test_decide_long_chandelier_hit_closes():
    d = decide_long(l1_state="up", close=89, stop=90, new_high=False, r_multiple=0.5)
    assert d["action"] == "close" and "Chandelier" in d["reason"]


def test_decide_long_tighten_sl():
    d = decide_long(l1_state="up", close=100, stop=95, new_high=False, r_multiple=0.5,
                    cur_sl=90)
    assert d["action"] == "tighten_sl" and d["new_sl"] == 95


def test_decide_long_extreme_drawdown():
    d80 = decide_long(l1_state="up", close=100, stop=80, new_high=False, r_multiple=0.5,
                      drawdown_pct=0.85)
    assert d80["action"] == "close" and "80%" in d80["reason"]
    d60 = decide_long(l1_state="up", close=100, stop=80, new_high=False, r_multiple=0.5,
                      drawdown_pct=0.65)
    assert d60["action"] == "reduce" and d60["ratio"] == 0.5


def test_decide_long_no_progress():
    d = decide_long(l1_state="up", close=100, stop=80, new_high=False, r_multiple=0.2,
                    hold_days=35, peak_r=0.5)
    assert d["action"] == "close" and "no_progress" in d["reason"]
    # 峰值达标（>=1R）不触发
    d2 = decide_long(l1_state="up", close=100, stop=80, new_high=False, r_multiple=0.2,
                     hold_days=35, peak_r=1.2)
    assert d2["action"] == "hold"


def test_decide_long_pyramid_three_batches():
    d0 = decide_long(l1_state="up", close=100, stop=80, new_high=True, r_multiple=1.2, pyr_batch=0)
    assert d0["action"] == "add" and d0["ratio"] == 0.5
    d1 = decide_long(l1_state="up", close=100, stop=80, new_high=True, r_multiple=1.2, pyr_batch=1)
    assert d1["action"] == "add" and d1["ratio"] == 0.35
    d2 = decide_long(l1_state="up", close=100, stop=80, new_high=True, r_multiple=1.2, pyr_batch=2)
    assert d2["action"] == "add" and d2["ratio"] == 0.25
    d3 = decide_long(l1_state="up", close=100, stop=80, new_high=True, r_multiple=1.2, pyr_batch=3)
    assert d3["action"] != "add", "批次打满不再加仓"


def test_chandelier_entry_price():
    close = pd.Series(np.arange(100.0, 120.0, 1.0))
    atr = pd.Series(np.full(len(close), 2.0))
    stops = chandelier_long_stop(close, atr, mult=2.0, entry_idx=0, entry_price=101.0)
    # 初始止损 = entry_price - 2*ATR = 101-4 = 97
    assert abs(float(stops.iloc[0]) - 97.0) < 1e-9


def test_manage_long_position_same_core():
    """同输入下 manage_long_position 与 decide_long 输出一致（同核断言）。"""
    n = 300
    rng = np.random.default_rng(7)
    close = pd.Series(100 * np.cumprod(1 + rng.normal(0.001, 0.02, n)))
    _ts_base = float(pd.Timestamp.utcnow().timestamp()) - n * 86400
    df = pd.DataFrame({
        "timestamp": np.arange(n, dtype=float) * 86400 + _ts_base,
        "open": close * 0.99, "high": close * 1.02,
        "low": close * 0.98, "close": close,
    })
    fake_c = {"state": "up", "score": 4, "close": float(close.iloc[-1])}
    atr_series = pd.Series(np.full(n, 5.0))
    position = {
        "id": 1, "symbol": "BTC", "side": "long",
        "entry_price": 100.0, "sl_price": 90.0,
        "opened_at": pd.Timestamp.utcnow() - pd.Timedelta(days=5),
        "margin": 100.0, "unrealized_pnl": 8.0, "peak_pnl_pct": 0.12,
        "exit_state_json": '{"pyramid_batch": 0}',
    }
    with patch.object(lv2, "long_v2_enabled", return_value=True), \
         patch.object(lv2, "_get_l1_classification", return_value=(df, fake_c)), \
         patch.object(lv2, "weekly_atr", return_value=atr_series), \
         patch.object(lv2, "is_new_high", return_value=pd.Series([False] * n)):
        out = lv2.manage_long_position(None, account_id=1, position=position)

    # 手动按同核逻辑算期望：entry_idx=5 天前的 bar（开仓日在 df 中），close_now=最后一根
    from backend.services.long_tier_manager import chandelier_long_stop as _chs
    entry = 100.0
    mult = 2.0
    atr_w = 5.0
    close_now = float(close.iloc[-1])
    stops = _chs(close, atr_series, mult=mult, entry_idx=n - 6, entry_price=entry)
    stop = float(stops.iloc[-1])
    expect = decide_long(
        l1_state="up", close=close_now, stop=stop, new_high=False,
        r_multiple=(close_now - entry) / (mult * atr_w),
        in_position=True, cur_sl=90.0,
        peak_r=0.12 / ((mult * atr_w) / entry),
        hold_days=5.0, drawdown_pct=max(0.0, 1.0 - (1.0 + 0.08) / (1.0 + 0.12)),
        pyr_batch=0,
    )
    assert out["action"] == expect["action"], f"同核失败: {out} vs {expect}"
