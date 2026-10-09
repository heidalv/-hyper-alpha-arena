# -*- coding: utf-8 -*-
"""[h454 2026-09-28] 自适应趋势门槛（trend_only_q）契约测试。

契约：
  1. trend_only_q=0 且 trend_only_bp=0 ⇒ 不产生任何拦截（旧行为）；
  2. 分位形式：门槛 = max(绝对下限, 近 1h |r300| 分布的第 q 分位)；
     通过率与市场绝对波动无关（死盘也放行最强的一段）；
  3. 历史不足（<60 样本）⇒ 退回绝对下限；
  4. ar300_hist 序列化 round-trip 保留。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from backend.services.market_maker.core import LaneRiskLimits  # noqa: E402
from backend.services.market_maker.runner import SymbolState  # noqa: E402


def _threshold(hist, abs_floor, q):
    """复刻 runner 内的门槛计算（便于单测直接验证口径）。"""
    thr = float(abs_floor or 0.0)
    if q and q > 0 and len(hist) >= 60:
        srt = sorted(hist)
        qq = min(max(float(q), 0.0), 0.99)
        thr = max(thr, srt[min(len(srt) - 1, int(len(srt) * qq))])
    return thr


def test_defaults_off():
    lim = LaneRiskLimits()
    assert float(getattr(lim, "trend_only_q", 0.0) or 0.0) == 0.0
    # 两者都 0 ⇒ 门槛 0 ⇒ 任何 |r300| 都通过
    assert _threshold([], 0.0, 0.0) == 0.0


def test_quantile_threshold_scales_with_market():
    hi_vol = [10.0 + i * 0.5 for i in range(240)]      # 活跃市：|r300| 10~130
    lo_vol = [0.5 + i * 0.005 for i in range(240)]     # 死盘：0.5~1.7
    t_hi = _threshold(hi_vol, 0.0, 0.75)
    t_lo = _threshold(lo_vol, 0.0, 0.75)
    assert t_hi > 50.0        # 活跃市门槛高
    assert t_lo < 2.0         # 死盘门槛自动降低 ⇒ 仍会放行最强的一段（不熔断频率）
    # 通过率近似恒定：各自分布中 ≥q 分位的比例都 ≈ 25%
    pass_hi = sum(1 for v in hi_vol if v >= t_hi) / len(hi_vol)
    pass_lo = sum(1 for v in lo_vol if v >= t_lo) / len(lo_vol)
    assert abs(pass_hi - pass_lo) < 0.05


def test_absolute_floor_wins_in_dead_market():
    lo_vol = [0.5] * 240
    # 死盘 + 绝对下限 30 ⇒ 门槛取 30（不放行）—— 这正是 h452 的失败模式，需显式兜底为 0
    assert _threshold(lo_vol, 30.0, 0.75) == 30.0
    # 若绝对下限为 0（推荐配置）⇒ 门槛 = 分位 ≈ 0.5 ⇒ 仍可交易
    assert _threshold(lo_vol, 0.0, 0.75) <= 0.5


def test_insufficient_history_falls_back_to_floor():
    assert _threshold([5.0] * 10, 3.0, 0.9) == 3.0


def test_ar300_hist_roundtrip():
    s = SymbolState(symbol="BTC", ar300_hist=[1.0, 2.5, 3.75])
    d = s.to_dict()
    s2 = SymbolState.from_dict(d)
    assert s2.ar300_hist == [1.0, 2.5, 3.75]
