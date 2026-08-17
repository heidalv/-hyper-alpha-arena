"""trend_layer — L1 长线趋势判定器（规则化，替代 trend_agent 方向判定）。

设计见《长线趋势策略重构设计_V2.md》§4.1：
- 5 个确定性结构信号 ±1 投票 → score ∈ [-5, +5]
- score ≥ +3 → up；≤ -3 → down；其余 sideways
- 只吃 1d K 线（薄数据币降级 1d 视图；核心币由调用方聚合 1w）
- 评估频率：1d 收盘后（调用方节流，非 tick 级）
- 交易路径 100% 确定性、可复现、无 LLM、无前视（全部信号只用截至当前 bar 的数据）

无前视保证：EMA/rolling 均为因果算子；结构信号用 rolling(60) 含当前 bar（bar 收盘即已知）；
ADX 用 Wilder 平滑（因果）；fwd 收益由调用方用 shift 计算，本模块不涉及未来数据。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

_ADX_PERIOD = 14
_ATR_PERIOD = 14
_STRUCT_WINDOW = 60
_PULLBACK_WINDOW = 20
_UP_SCORE = 3
_DOWN_SCORE = -3
_MIN_BARS = 260  # 至少一年日线才出判定（EMA200 需足够历史）


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = _ADX_PERIOD):
    """Wilder 平滑 ADX/DMI。返回 (pdi, mdi, adx) 三个 Series。"""
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    pdi = 100.0 * pd.Series(plus_dm, index=high.index).ewm(alpha=1.0 / period, adjust=False).mean() / atr
    mdi = 100.0 * pd.Series(minus_dm, index=high.index).ewm(alpha=1.0 / period, adjust=False).mean() / atr
    dx = 100.0 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adx = dx.ewm(alpha=1.0 / period, adjust=False).mean()
    return pdi, mdi, adx


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = _ATR_PERIOD) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def _signals(df: pd.DataFrame) -> Dict[str, pd.Series]:
    """向量化计算 5 个结构信号的 ±1 贡献序列（因果，无前视）。"""
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    e20 = _ema(close, 20)
    e50 = _ema(close, 50)
    e100 = _ema(close, 100)
    e200 = _ema(close, 200)

    # 1 EMA 排列
    s_ema = pd.Series(0.0, index=close.index)
    s_ema[(e20 > e50) & (e50 > e100)] = 1.0
    s_ema[(e20 < e50) & (e50 < e100)] = -1.0

    # 2 结构（60 日高低点）
    h60 = high.rolling(_STRUCT_WINDOW).max()
    l60 = low.rolling(_STRUCT_WINDOW).min()
    s_struct = pd.Series(0.0, index=close.index)
    s_struct[close > h60 * 0.98] = 1.0
    s_struct[close < l60 * 1.02] = -1.0

    # 3 动能 ADX
    pdi, mdi, adx = _adx(high, low, close)
    s_adx = pd.Series(0.0, index=close.index)
    s_adx[(adx >= 20) & (pdi > mdi)] = 1.0
    s_adx[(adx >= 20) & (mdi > pdi)] = -1.0

    # 4 长周期 EMA200
    s_ma200 = pd.Series(0.0, index=close.index)
    s_ma200[close > e200] = 1.0
    s_ma200[close < e200] = -1.0

    # 5 回撤不破（最近 20 日 swing 低/高 vs EMA100）
    sw_low = low.rolling(_PULLBACK_WINDOW).min()
    sw_high = high.rolling(_PULLBACK_WINDOW).max()
    s_pull = pd.Series(0.0, index=close.index)
    s_pull[sw_low > e100] = 1.0
    s_pull[sw_high < e100] = -1.0

    return {
        "ema": s_ema, "structure": s_struct, "adx": s_adx,
        "ma200": s_ma200, "pullback": s_pull,
        "e20": e20, "e50": e50, "e100": e100, "e200": e200,
        "adx_v": adx, "atr": _atr(high, low, close),
        "h60": h60, "l60": l60, "close": close,
    }


def classify_series(df: pd.DataFrame) -> pd.DataFrame:
    """向量化历史判定：返回逐 bar 的 state/score/strength（供回测）。"""
    sig = _signals(df)
    score = sig["ema"] + sig["structure"] + sig["adx"] + sig["ma200"] + sig["pullback"]
    state = pd.Series("sideways", index=df.index, dtype=object)
    state[score >= _UP_SCORE] = "up"
    state[score <= _DOWN_SCORE] = "down"
    strength = (score / 5.0 * 100.0).clip(-100, 100)
    out = pd.DataFrame({
        "score": score, "state": state, "strength": strength,
        "atr": sig["atr"], "close": sig["close"],
        "h60": sig["h60"], "l60": sig["l60"],
    }, index=df.index)
    return out


def classify(df: pd.DataFrame) -> Dict[str, Any]:
    """判定最新一根 bar 的趋势状态（供实盘/每日节流调用）。

    df：按时间升序、含 open/high/low/close 列的 1d K 线 DataFrame。
    """
    if df is None or len(df) < _MIN_BARS:
        return {"state": "sideways", "score": 0, "strength": 0.0,
                "reason": f"数据不足({len(df) if df is not None else 0}<{_MIN_BARS})",
                "signals": {}, "target": None, "atr": None, "close": None}
    close = df["close"].astype(float)
    sig = _signals(df)
    score = float(sum(sig[k].iloc[-1] for k in ("ema", "structure", "adx", "ma200", "pullback")))
    state = "up" if score >= _UP_SCORE else ("down" if score <= _DOWN_SCORE else "sideways")
    atr = float(sig["atr"].iloc[-1])
    h60 = float(sig["h60"].iloc[-1])
    l60 = float(sig["l60"].iloc[-1])
    target = None
    if state == "up":
        target = h60 + atr
    elif state == "down":
        target = l60 - atr
    return {
        "state": state,
        "score": score,
        "strength": round(score / 5.0 * 100.0, 1),
        "signals": {
            "ema": _vote_label(sig["ema"].iloc[-1]),
            "structure": _vote_label(sig["structure"].iloc[-1]),
            "adx": _vote_label(sig["adx"].iloc[-1]),
            "ma200": _vote_label(sig["ma200"].iloc[-1]),
            "pullback": _vote_label(sig["pullback"].iloc[-1]),
        },
        "target": round(target, 6) if target is not None else None,
        "atr": round(atr, 6),
        "close": round(float(close.iloc[-1]), 6),
        "reason": f"score={score} ({state})",
    }


def _vote_label(v: float) -> str:
    if v > 0:
        return "up"
    if v < 0:
        return "down"
    return "mixed"
