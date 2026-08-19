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


def weekly_atr_causal(df_1d: pd.DataFrame, period: int = _ATR_PERIOD) -> pd.Series:
    """[A7 因果修复] 严格因果的周线 ATR：bar i 的值 = 截至「上一个完整周」的 Wilder ATR。

    旧 weekly_atr 把第 k 周整周数据算出的 ATR 前向填充到该周 7 根日线——回测里
    周一的 bar 就用到周二~周日的数据（前视 6 天）。本版本当前周数据完全不参与
    （周内只用已完成周的波动尺度），回测/实盘同核且无前视。
    """
    n = len(df_1d)
    if n < 14:
        return pd.Series(np.nan, index=df_1d.index)
    high = df_1d["high"].astype(float).to_numpy()
    low = df_1d["low"].astype(float).to_numpy()
    close = df_1d["close"].astype(float).to_numpy()

    nw = n // 7  # 完整周数
    wh = np.array([high[k * 7:(k + 1) * 7].max() for k in range(nw)])
    wl = np.array([low[k * 7:(k + 1) * 7].min() for k in range(nw)])
    wc = np.array([close[(k + 1) * 7 - 1] for k in range(nw)])
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

    out = np.full(n, np.nan)
    for i in range(n):
        wk = i // 7
        if wk >= 1:
            out[i] = atr[wk - 1]
    return pd.Series(out, index=df_1d.index)


def chandelier_long_stop(
    close: pd.Series,
    atr_w: pd.Series,
    mult: float = _CHANDELIER_MULT,
    entry_idx: int = 0,
    entry_price: Optional[float] = None,
) -> pd.Series:
    """多头 Chandelier 追踪止损序列（从 entry_idx 起，只上移不下移）。

    止损 = max(历史最高收盘 - mult×ATR(1w), 初始止损)；初始止损 = entry_close - mult×ATR(1w)。
    [A2 同核] entry_price：实盘传真实入场价（回测不传=用开仓日收盘价），同一函数两端复用。
    """
    n = len(close)
    stop = pd.Series(np.nan, index=close.index)
    if entry_idx >= n:
        return stop
    _entry_base = float(entry_price) if entry_price is not None else float(close.iloc[entry_idx])
    init = _entry_base - mult * float(atr_w.iloc[entry_idx])
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
    stop: Optional[float],
    new_high: bool,
    r_multiple: float,
    in_position: bool = True,
    cur_sl: Optional[float] = None,
    peak_r: Optional[float] = None,
    hold_days: Optional[float] = None,
    drawdown_pct: Optional[float] = None,
    pyr_batch: int = 0,
    max_batches: int = 3,
    target: Optional[float] = None,
    needs_topup: bool = False,
    topup_ratio: float = 0.5,
) -> Dict[str, Any]:
    """长线持仓单日决策（纯规则，回测/实盘同核的唯一决策函数）。

    l1_state: trend_layer.classify 的 state（up/down/sideways）
    close: 当日收盘
    stop: 当前 Chandelier 止损
    new_high: 是否创 60 日新高
    r_multiple: 当前浮盈 R（相对首仓风险）
    in_position: 是否持仓
    cur_sl: 当前仓位 SL 价（收紧止损判定用）
    peak_r: 持仓峰值 R（no_progress 判定用）
    hold_days: 持有天数（no_progress 判定用）
    drawdown_pct: 相对峰值的持仓回撤（极端回撤保护用，0~1）
    pyr_batch: 已完成的金字塔加仓批次数（capped 3 档 0.5/0.35/0.25）
    max_batches: 金字塔批次上限

    返回 {"action": hold/add/reduce/close/tighten_sl, "reason", "ratio"?, "new_sl"?}
    """
    if not in_position:
        return {"action": "hold", "reason": "无持仓"}
    # 结构破坏：L1 不再 up → 唯一主动退出
    if l1_state != "up":
        return {"action": "close", "reason": f"结构破坏(L1={l1_state})"}
    # Chandelier 打穿 → 被动退出
    if stop is not None and close < stop:
        return {"action": "close", "reason": f"Chandelier止损(close={close:.2f}<stop={stop:.2f})"}
    # [A3] 极端回撤（紧急保护，优先于加仓/持有）：>=80% 全平、>=60% 减半
    if drawdown_pct is not None:
        if drawdown_pct >= 0.8:
            return {"action": "close", "reason": f"极端回撤≥80%({drawdown_pct:.0%})"}
        if drawdown_pct >= 0.6:
            return {"action": "reduce", "ratio": 0.5,
                    "reason": f"极端回撤≥60%({drawdown_pct:.0%})减半"}
    # [A3] no_progress 兜底：hold>=30 天且峰值从未达到 1R → 离场
    if hold_days is not None and peak_r is not None and hold_days >= 30.0 and peak_r < 1.0:
        return {"action": "close",
                "reason": f"no_progress(hold={hold_days:.0f}天, peak_r={peak_r:.2f})"}
    # [A4] 结构目标减仓：收盘达 L1 结构目标（h60+ATR 投影）→ 减 50%，其余交给追踪
    if target is not None and close >= target:
        return {"action": "reduce", "ratio": 0.5,
                "reason": f"结构目标达成减半(close={close:.2f}≥target={target:.2f})"}
    # [A4] 首仓补足：满 24h 且未补足 → 补到 100%（试探仓 50% 的补足腿）
    if needs_topup and hold_days is not None and hold_days >= 1.0:
        return {"action": "add", "ratio": round(float(topup_ratio), 4), "topup": True,
                "reason": f"首仓补足(hold={hold_days:.2f}天, +{float(topup_ratio) * 100:.0f}%)"}
    # 金字塔：新高 + 浮盈 >= 1R，capped 批次序列 0.5/0.35/0.25（Phase C/E 口径）
    if new_high and r_multiple >= _PYRAMID_R and int(pyr_batch) < max_batches:
        _ratios = [0.5, 0.35, 0.25][:max_batches]
        _ratio = _ratios[min(int(pyr_batch), len(_ratios) - 1)]
        return {"action": "add", "ratio": _ratio,
                "reason": f"新高加仓(r={r_multiple:.2f}R, 第{int(pyr_batch) + 1}批)"}
    # Chandelier 上移 → 收紧 SL（只上移）
    if cur_sl is not None and stop is not None and stop > cur_sl:
        return {"action": "tighten_sl", "new_sl": round(float(stop), 6),
                "reason": f"Chandelier上移 SL→{stop:.4f}"}
    return {"action": "hold", "reason": "持有"}
