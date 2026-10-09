# -*- coding: utf-8 -*-
"""[2026-10-08] 趋势概率 8 特征的**实时计算**(纯标准库,worker/选币器都能用)。

为什么要有这个文件:trend_prob 模型的 8 个特征此前只在**训练侧**(numpy)算,
线上推理时喂不齐 ⇒ predict_side_edge 永远算不出方向 ⇒ 趋势概率那 30 分是死的。
本文件用**纯标准库**从原始表(trades + book_ticker)现算当前快照的 8 个特征,
口径与训练侧 hft_trend_prob_train.py **逐字一致**(见各注释),防训练/推理漂移。

特征口径(与训练逐字一致):
  mp_skew_bp = 顶档微价偏离 (microprice − mid)/mid×1e4,microprice=(bid·aq+ask·bq)/(bq+aq)
  ofi        = 60s 主动买/卖量失衡 (bv−sv)/(bv+sv)
  obi_top    = 顶档盘口失衡 (bid_qty0−ask_qty0)/(bid_qty0+ask_qty0)
  trend_Ns   = (mid_now / mid_{N秒前} − 1)×1e4
  accel      = trend_20s − trend_60s
  vol_20s    = 近 20 步中价对数收益 std×1e4
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Tuple


def _trend_move(mid_now: float, mid_then: float) -> float:
    if mid_then <= 0 or mid_now <= 0:
        return 0.0
    return (mid_now / mid_then - 1.0) * 1e4


def _realized_vol(mids: List[float]) -> float:
    """近 20 步中价对数收益 std×1e4(与训练 vol20 同口径)。"""
    if len(mids) < 2:
        return 0.0
    logm = [math.log(max(m, 1e-12)) for m in mids]
    r = [logm[i] - logm[i - 1] for i in range(1, len(logm))]
    tail = r[-20:]
    if len(tail) < 2:
        return 0.0
    mean = sum(tail) / len(tail)
    var = sum((x - mean) ** 2 for x in tail) / len(tail)
    return math.sqrt(var) * 1e4


def compute_features(
    book_rows: List[Tuple[float, float, float, float, float]],
    ofi_60s: Optional[float],
    ofi_20s: Optional[float] = None,
    vol_60s_qty: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    """从 book_ticker 行 + 订单流算 10 个特征。

    book_rows: [(ts_s, bid, ask, bid_qty, ask_qty), ...] 按时间升序。
    ofi_60s:  60s 主动买卖失衡(调用方从 trades 表算好传进来)。
    ofi_20s:  20s 主动买卖失衡(算 ofi_accel 用;None ⇒ ofi_accel=None)。
    vol_60s_qty: 60s 总成交量(算 ofi_volw 用;None ⇒ ofi_volw=None)。
    返回 dict,键 = trend_prob.FEATURES。数据不足时对应键为 None。
    """
    if not book_rows:
        return {k: None for k in (
            "mp_skew_bp", "ofi", "obi_top", "trend_20s", "trend_60s",
            "trend_120s", "accel", "vol_20s", "ofi_accel", "ofi_volw")}
    ts = [float(r[0]) for r in book_rows]
    bid = [float(r[1]) for r in book_rows]
    ask = [float(r[2]) for r in book_rows]
    bq = [float(r[3]) for r in book_rows]
    aq = [float(r[4]) for r in book_rows]
    mid = [(b + a) / 2.0 for b, a in zip(bid, ask)]

    # 最新一档
    i = len(mid) - 1
    mid_now = mid[i]
    # 顶档微价偏离
    denom = max(bq[i] + aq[i], 1e-12)
    mp = (bid[i] * aq[i] + ask[i] * bq[i]) / denom
    mp_skew = (mp - mid_now) / max(mid_now, 1e-12) * 1e4 if mid_now > 0 else None
    # 顶档盘口失衡
    obi_top = (bq[i] - aq[i]) / denom

    # 趋势:找 N 秒前最近的一档
    def _mid_at(seconds_ago: float) -> Optional[float]:
        target = ts[i] - seconds_ago
        # 从后往前找第一个 ≤ target 的
        lo = None
        for j in range(i, -1, -1):
            if ts[j] <= target:
                lo = j
                break
        if lo is None:
            return None
        return mid[lo]

    m20 = _mid_at(20.0)
    m60 = _mid_at(60.0)
    m120 = _mid_at(120.0)
    t20 = _trend_move(mid_now, m20) if m20 else None
    t60 = _trend_move(mid_now, m60) if m60 else None
    t120 = _trend_move(mid_now, m120) if m120 else None
    accel = (t20 - t60) if (t20 is not None and t60 is not None) else None
    # vol_20s:用最近 ~20 步的 mid 序列
    vol_mids = mid[max(0, i - 25): i + 1]
    vol20 = _realized_vol(vol_mids) if len(vol_mids) >= 3 else None

    # [2026-10-08 提准] 订单流加强(与训练侧同口径):
    #   ofi_accel = ofi_20s − ofi_60s(资金加速度)
    #   ofi_volw  = ofi_60s × log1p(60s 成交量)(量大的 OFI 才可信)
    ofi_accel = None
    if ofi_20s is not None and ofi_60s is not None:
        ofi_accel = float(ofi_20s) - float(ofi_60s)
    ofi_volw = None
    if ofi_60s is not None and vol_60s_qty is not None:
        ofi_volw = float(ofi_60s) * math.log1p(max(0.0, float(vol_60s_qty)))

    return {
        "mp_skew_bp": mp_skew,
        "ofi": ofi_60s,
        "obi_top": obi_top,
        "trend_20s": t20,
        "trend_60s": t60,
        "trend_120s": t120,
        "accel": accel,
        "vol_20s": vol20,
        "ofi_accel": ofi_accel,
        "ofi_volw": ofi_volw,
    }
