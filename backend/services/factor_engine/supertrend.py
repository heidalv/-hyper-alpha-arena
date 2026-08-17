"""标准 SuperTrend 方向序列（period=14, mult=3, Wilder ATR）。

[2026-08-16 修复] 旧实现（base_factors.compute_supertrend 与
LegacySupertrendFactor 同款）用「当前 bar 中点 ± 3×ATR」判 close 突破，
而 ATR 包含当前 bar 自身区间，数学上 close 几乎永远落在带内 → 因子恒 0
（BTC 4h 400 天实测 0/386 次触发，注册因子评分恒 F「有效样本不足」）。
此处实现标准跟踪带版本（前一根带 + 方向记忆，close 穿越上/下带翻转），
供 FactorEngine 与 legacy_compat 注册因子两处共用。
"""
from __future__ import annotations

import numpy as np


def supertrend_direction(high, low, close, period: int = 14, mult: float = 3.0):
    """返回与输入等长的方向序列：+1 多头 / -1 空头 / 0 预热期无效。

    纯向量化 + 单次 O(n) 循环（跟踪带必须顺序递推），无未来信息。
    """
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    n = len(close)
    out = np.zeros(n)
    if n < period + 2:
        return out

    prev_close = np.empty(n)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]
    tr = np.maximum(
        high - low,
        np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)),
    )
    # Wilder ATR
    atr = np.full(n, np.nan)
    atr[period] = float(np.mean(tr[1:period + 1]))
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    mid = (high + low) / 2.0
    basic_ub = mid + mult * atr
    basic_lb = mid - mult * atr
    final_ub = np.full(n, np.nan)
    final_lb = np.full(n, np.nan)
    st = np.full(n, np.nan)

    i0 = period + 1
    final_ub[i0] = basic_ub[i0]
    final_lb[i0] = basic_lb[i0]
    # 初始方向：close 在中点之上 → 多头
    st[i0] = final_ub[i0] if close[i0] > (final_ub[i0] + final_lb[i0]) / 2.0 else final_lb[i0]

    for i in range(i0 + 1, n):
        # 跟踪带：新带更紧 或 价格已突破旧带 → 收带
        if basic_ub[i] < final_ub[i - 1] or close[i - 1] > final_ub[i - 1]:
            final_ub[i] = basic_ub[i]
        else:
            final_ub[i] = final_ub[i - 1]
        if basic_lb[i] > final_lb[i - 1] or close[i - 1] < final_lb[i - 1]:
            final_lb[i] = basic_lb[i]
        else:
            final_lb[i] = final_lb[i - 1]
        # 翻转逻辑：多头状态下 close 跌破上带 → 转空；空头状态下突破下带 → 转多
        if st[i - 1] == final_ub[i - 1]:
            st[i] = final_ub[i] if close[i] > final_ub[i] else final_lb[i]
        else:
            st[i] = final_lb[i] if close[i] < final_lb[i] else final_ub[i]

    out = np.where(close > st, 1.0, -1.0)
    out[~np.isfinite(st)] = 0.0
    return out
