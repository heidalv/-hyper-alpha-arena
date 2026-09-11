"""long_tier_manager — L3 长线专用管理（设计 V2 §4.3）。

长线趋势单的独特处置（与 short/mid 分开）：
- 止损：周线 Chandelier（最高收盘 - mult × ATR(1w)），只随 1d 收盘上移，不追盘中噪声。
- 退出：结构破坏（L1 从 up 翻转）为唯一主动退出；Chandelier 打穿为唯一被动退出。
- 金字塔：创 60 日新高（动量延续）且浮盈达标 → 加仓。
- 频率：每日一次复盘（调用方节流），不参与 45s/15min tick。

纯规则、无 DB、无 LLM、无前视（所有计算只用截至当前 bar 的数据）。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_ATR_PERIOD = 14
_CHANDELIER_MULT = 2.0
_NEW_HIGH_WINDOW = 60
_PYRAMID_R = 1.0  # 每 1R 允许一次加仓

# ── [2026-09-07 P0] 长线早期复盘 + 浮盈保本（小资金盘诊断驱动） ──
# 背景：BTC 长线 5x 扛 29.4h 峰值仅 +0.2% 最终 -$44.87；旧 no_progress 要等 30 天，
# 对 max_hold=7d 的盘子等于没有。XRP/ETH 峰值 +2.4%/+3.2% 但 SL 仍在 -12%/-6.5% 深水区。
_EARLY_NP_REDUCE_DAYS = 2.0   # 持仓 ≥2 天且峰值从未达 0.3R → 减半（只执行一次）
_EARLY_NP_CLOSE_DAYS = 3.0    # 持仓 ≥3 天且峰值从未达 0.3R → 退出（与 72h min_hold 对齐）
_EARLY_NP_PEAK_R = 0.3        # 峰值 R 门槛（0.3R ≈ 0.6×周ATR，"动起来过"的最低标准）
_BREAKEVEN_PEAK_PCT = 0.02    # 峰值浮盈(无杠杆价格%) ≥2% → SL 推保本
_LOCK_PROFIT_PEAK_PCT = 0.03  # 峰值浮盈 ≥3% → 锁定峰值利润的 1/3
_LOCK_PROFIT_FRAC = 1.0 / 3.0


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
    entry_price: Optional[float] = None,
    peak_pnl_pct: Optional[float] = None,
    dd_halve_done: bool = False,
    target_halve_done: bool = False,
    early_np_done: bool = False,
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
    drawdown_pct: 相对峰值的利润回撤（极端回撤保护用，0~1；
                  口径 = (峰值浮盈% − 当前浮盈%) / 峰值浮盈%，峰值/当前同单位）
    pyr_batch: 已完成的金字塔加仓批次数（capped 3 档 0.5/0.35/0.25）
    max_batches: 金字塔批次上限
    entry_price: [P0] 入场价（浮盈保本/锁利用；不传则该规则静默跳过）
    peak_pnl_pct: [P0] 峰值浮盈（无杠杆价格%，与 paper_positions.peak_pnl_pct 同口径）
    dd_halve_done: [幂等] 极端回撤减半是否已执行过（防调用方高频重评导致连续减半）
    target_halve_done: [幂等] 结构目标减半是否已执行过
    early_np_done: [幂等] 早期 no_progress 减半是否已执行过

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
    # [A3] 极端回撤（紧急保护，优先于加仓/持有）：>=80% 全平、>=60% 减半（减半幂等）
    # [2026-09-08 放宽浮盈保护] 数据分析：盈利单冲到峰值后回撤60%就被减半，回吐过多。
    # 阈值放宽为 env 可调：极端回撤减半 LONG_DD_HALVE(默认0.75)、全平 LONG_DD_CLOSE(默认0.90)，
    # 给盈利单更大回旋空间，让 winners 多跑一段。env 设回 0.6/0.8 即回退。
    _dd_close = float(os.getenv("LONG_DD_CLOSE", "0.90"))
    _dd_halve = float(os.getenv("LONG_DD_HALVE", "0.75"))
    if drawdown_pct is not None:
        if drawdown_pct >= _dd_close:
            return {"action": "close", "reason": f"极端回撤≥{_dd_close:.0%}({drawdown_pct:.0%})"}
        if drawdown_pct >= _dd_halve and not dd_halve_done:
            return {"action": "reduce", "ratio": 0.5,
                    "reason": f"极端回撤≥{_dd_halve:.0%}({drawdown_pct:.0%})减半"}
    # [P0 2026-09-07] 早期 no_progress：趋势仍 up 但价格迟迟不动（死钱占用）。
    # 旧规则要等 30 天，对 max_hold=7d 的盘子形同虚设（BTC 长线扛 29h -$44.87 实证）。
    # ≥2 天减半（幂等）、≥3 天退出（与 TIER_PROTECTION long min_hold 72h 对齐）。
    if hold_days is not None and peak_r is not None and peak_r < _EARLY_NP_PEAK_R:
        if hold_days >= _EARLY_NP_CLOSE_DAYS:
            return {"action": "close",
                    "reason": f"early_no_progress(hold={hold_days:.1f}天≥{_EARLY_NP_CLOSE_DAYS:.0f}天, "
                              f"peak_r={peak_r:.2f}<{_EARLY_NP_PEAK_R})"}
        if hold_days >= _EARLY_NP_REDUCE_DAYS and not early_np_done:
            return {"action": "reduce", "ratio": 0.5,
                    "reason": f"early_no_progress减半(hold={hold_days:.1f}天≥{_EARLY_NP_REDUCE_DAYS:.0f}天, "
                              f"peak_r={peak_r:.2f}<{_EARLY_NP_PEAK_R})"}
    # [A3] no_progress 兜底：hold>=30 天且峰值从未达到 1R → 离场
    if hold_days is not None and peak_r is not None and hold_days >= 30.0 and peak_r < 1.0:
        return {"action": "close",
                "reason": f"no_progress(hold={hold_days:.0f}天, peak_r={peak_r:.2f})"}
    # [A4] 结构目标减仓：收盘达 L1 结构目标（h60+ATR 投影）→ 减 50%（幂等），其余交给追踪
    if target is not None and close >= target and not target_halve_done:
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
    # [P0 2026-09-07] 浮盈保本/锁利（只上移）：峰值 ≥2% 推保本，≥3% 锁峰值利润 1/3。
    # 与 Chandelier 候选取较高者；XRP/ETH 峰值 +2.4%/+3.2% 而 SL 滞留深水区的实证修复。
    _tighten_cand: Optional[float] = None
    _tighten_why = ""
    if entry_price is not None and peak_pnl_pct is not None and entry_price > 0:
        if peak_pnl_pct >= _LOCK_PROFIT_PEAK_PCT:
            _tighten_cand = entry_price * (1.0 + peak_pnl_pct * _LOCK_PROFIT_FRAC)
            _tighten_why = f"峰值{peak_pnl_pct:.1%}锁利1/3"
        elif peak_pnl_pct >= _BREAKEVEN_PEAK_PCT:
            _tighten_cand = entry_price
            _tighten_why = f"峰值{peak_pnl_pct:.1%}推保本"
    if cur_sl is not None and stop is not None and stop > cur_sl:
        if _tighten_cand is None or stop > _tighten_cand:
            _tighten_cand, _tighten_why = stop, "Chandelier上移"
    if _tighten_cand is not None and (cur_sl is None or _tighten_cand > cur_sl):
        return {"action": "tighten_sl", "new_sl": round(float(_tighten_cand), 6),
                "reason": f"{_tighten_why} SL→{_tighten_cand:.4f}"}
    return {"action": "hold", "reason": "持有"}
