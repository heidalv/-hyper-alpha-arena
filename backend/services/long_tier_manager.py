"""long_tier_manager — L3 长线专用管理（设计 V2 §4.3）。

长线趋势单的独特处置（与 short/mid 分开）：
- 止损：周线 Chandelier（最高收盘 - mult × ATR(1w)），只随 1d 收盘上移，不追盘中噪声。
- 退出：结构破坏（L1 从 up 翻转）为唯一主动退出；Chandelier 打穿为唯一被动退出。
- 金字塔：创 60 日新高（动量延续）且浮盈达标 → 加仓。
- 频率：每日一次复盘（调用方节流），不参与 45s/15min tick。

纯规则、无 DB、无 LLM、无前视（所有计算只用截至当前 bar 的数据）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_ATR_PERIOD = 14
_CHANDELIER_MULT = 2.0
_NEW_HIGH_WINDOW = 60
_PYRAMID_R = 1.0  # 每 1R 允许一次加仓


def weekly_atr(df_1d: pd.DataFrame, period: int = _ATR_PERIOD) -> pd.Series:
    """1d 数据按每 7 根聚合为「周」，算 ATR(period)，前向填充回日频。

    不依赖 DatetimeIndex（RangeIndex/时间戳索引均可），避免 resample 对索引类型
    的硬要求。无前视：第 k 周的 ATR 只用第 k 周及之前的 bar（Wilder 递推），
    并前向填充到该周的 7 根日线。
    """
    n = len(df_1d)
    if n < 7:
        return pd.Series(np.nan, index=df_1d.index)
    high = df_1d["high"].astype(float).to_numpy()
    low = df_1d["low"].astype(float).to_numpy()
    close = df_1d["close"].astype(float).to_numpy()

    nw = (n + 6) // 7
    wh = np.full(nw, np.nan); wl = np.full(nw, np.nan); wc = np.full(nw, np.nan)
    for k in range(nw):
        s = k * 7; e = min(n, s + 7)
        wh[k] = high[s:e].max(); wl[k] = low[s:e].min(); wc[k] = close[e - 1]

    tr = np.zeros(nw)
    for k in range(nw):
        if k == 0:
            tr[k] = wh[k] - wl[k]
        else:
            tr[k] = max(wh[k] - wl[k], abs(wh[k] - wc[k - 1]), abs(wl[k] - wc[k - 1]))

    atr = np.zeros(nw)
    atr[0] = tr[0]
    for k in range(1, nw):
        atr[k] = (atr[k - 1] * (period - 1) + tr[k]) / period

    atr_daily = np.zeros(n)
    for k in range(nw):
        s = k * 7; e = min(n, s + 7)
        atr_daily[s:e] = atr[k]
    return pd.Series(atr_daily, index=df_1d.index)


def chandelier_long_stop(
    close: pd.Series,
    atr_w: pd.Series,
    mult: float = _CHANDELIER_MULT,
    entry_idx: int = 0,
) -> pd.Series:
    """多头 Chandelier 追踪止损序列（从 entry_idx 起，只上移不下移）。

    止损 = max(历史最高收盘 - mult×ATR(1w), 初始止损)；初始止损 = entry_close - mult×ATR(1w)。
    """
    n = len(close)
    stop = pd.Series(np.nan, index=close.index)
    if entry_idx >= n:
        return stop
    init = close.iloc[entry_idx] - mult * atr_w.iloc[entry_idx]
    highest = -np.inf
    cur = init
    for i in range(entry_idx, n):
        c = float(close.iloc[i])
        highest = max(highest, c)
        cand = highest - mult * float(atr_w.iloc[i] if pd.notna(atr_w.iloc[i]) else 0.0)
        cur = max(cur, cand)
        stop.iloc[i] = cur
    return stop


def is_new_high(high: pd.Series, window: int = _NEW_HIGH_WINDOW) -> pd.Series:
    """当前收盘是否创 window 日新高（严格新高，不含当前 bar 之前的最高）。"""
    prev_high = high.rolling(window).max().shift(1)
    return high > prev_high


def decide_long(
    *,
    l1_state: str,
    close: float,
    stop: float,
    new_high: bool,
    r_multiple: float,
    in_position: bool,
) -> Dict[str, Any]:
    """长线持仓单日决策（纯规则）。

    l1_state: trend_layer.classify 的 state（up/down/sideways）
    close: 当日收盘
    stop: 当前 Chandelier 止损
    new_high: 是否创 60 日新高
    r_multiple: 当前浮盈 R（相对首仓风险）
    in_position: 是否持仓

    返回 {"action": hold/add/close, "reason": ...}
    """
    if not in_position:
        return {"action": "hold", "reason": "无持仓"}
    # 结构破坏：L1 不再 up → 唯一主动退出
    if l1_state != "up":
        return {"action": "close", "reason": f"结构破坏(L1={l1_state})"}
    # Chandelier 打穿 → 被动退出
    if stop is not None and close < stop:
        return {"action": "close", "reason": f"Chandelier止损(close={close:.2f}<stop={stop:.2f})"}
    # 金字塔：新高 + 浮盈 ≥ 1R
    if new_high and r_multiple >= _PYRAMID_R:
        return {"action": "add", "reason": f"新高加仓(r={r_multiple:.2f}R)"}
    return {"action": "hold", "reason": "持有"}
