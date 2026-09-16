# -*- coding: utf-8 -*-
"""[调研轮15] 第三层止损封顶契约测试（PositionMemoryManager._calc_tp_sl）。

为什么需要这一层：轮7 在**入场声明**（midlong_helpers）与**价格计算**（tp_sl_prices）
两处按 MIDLONG_MAX_SL_PCT_* 封顶后，线上新仓**仍挂着 4.5~4.7% 止损** —— 实测开仓日志：

    16:02:59 [MidLong] stage=open_ready symbol=XRP sl_source=llm sl=2.00%   ← 声明 2%
    16:03:30 [PosMgr]  XRP buy: ... TP=$1.37 SL=$1.23                        ← 实际 3.8%+

根因：`_calc_tp_sl` 的 swing 档 `min_sl_pct=0.955` 被当作**硬下限**使用 ——
「AI 给的止损比 4.5% 更紧 → 拉到 4.5%」，于是把 2% 的声明拉宽成 4.5%。
本函数才是**实际挂到仓位的 sl_price 的最后一公里**，故必须在此再封一次。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.position_memory_manager import PositionMemoryManager as P  # noqa: E402


class _Fake(P):
    """只借配置与映射表，不触发 DB/初始化。"""

    def __init__(self):  # noqa: D107
        pass


def _call(*, side="buy", price=100.0, leverage=4, vol=0.02, raw_sl=98.0, tier="swing", wr=0.6):
    fake = _Fake()
    mem = SimpleNamespace(symbol_win_rate=wr)
    return P._calc_tp_sl(
        fake, side=side, price=price, leverage=leverage, volatility_pct=vol,
        raw_tp=0.0, raw_sl=raw_sl, memory=mem, tier=tier,
    )


def test_swing_stop_capped_at_2pct():
    """复现线上现场：AI 声明 2%（raw_sl=98），此前被拉到 4.5% → 现须 ≤2%。"""
    tp, sl = _call(tier="swing", raw_sl=98.0)
    dist = abs(sl - 100.0) / 100.0
    assert dist <= 0.02 + 1e-9, f"swing 止损距离 {dist:.4%} 未封顶（sl={sl}）"


def test_swing_stop_capped_when_ai_gives_no_sl():
    """AI 未给 SL（raw_sl=0）时走系统基准 4.5% 分支 —— 同样要被封顶。"""
    tp, sl = _call(tier="swing", raw_sl=0.0)
    dist = abs(sl - 100.0) / 100.0
    assert dist <= 0.02 + 1e-9, f"无 AI SL 时系统基准 {dist:.4%} 未封顶"


def test_long_tier_capped_at_3pct():
    tp, sl = _call(tier="trend_follow", raw_sl=88.0)   # 声明 12%（配置档）
    dist = abs(sl - 100.0) / 100.0
    assert dist <= 0.03 + 1e-9, f"long 止损距离 {dist:.4%} 未封顶"


def test_short_lane_untouched():
    """ scalp/short 车道不改（其口径独立，且当前停开）。"""
    tp, sl = _call(tier="scalp", raw_sl=97.0, price=100.0)
    dist = abs(sl - 100.0) / 100.0
    assert dist >= 0.019, "scalp 车道不应被本封顶改变"


def test_sell_side_symmetric():
    tp, sl = _call(side="sell", tier="swing", raw_sl=102.0)
    assert sl > 100.0 and abs(sl - 100.0) / 100.0 <= 0.02 + 1e-9


def test_rollback_zero_disables(monkeypatch):
    from backend.config import settings as S
    monkeypatch.setattr(S, "MIDLONG_MAX_SL_PCT_MID", 0.0, raising=False)
    monkeypatch.setattr(S, "MIDLONG_MAX_SL_PCT_LONG", 0.0, raising=False)
    tp, sl = _call(tier="swing", raw_sl=98.0)
    dist = abs(sl - 100.0) / 100.0
    assert dist > 0.02, "cap=0 必须回到旧的硬下限行为（4.5%）"


def test_tp_still_present_and_consistent():
    """封顶只动 SL；TP 仍存在，且 RR = TP/SL 因 SL 收窄而改善（不应倒挂）。"""
    tp, sl = _call(tier="swing", raw_sl=98.0)
    assert tp > 100.0
    sl_dist = abs(sl - 100.0)
    tp_dist = abs(tp - 100.0)
    assert tp_dist / sl_dist >= 1.0, "封顶后 RR 不得倒挂"
