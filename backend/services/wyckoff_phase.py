"""wyckoff_phase — 简化 Wyckoff 四相位结构检测（设计总方案 B2，2026-08-19）。

四相位（纯规则，无前视）：
- markup（上涨）：价格突破区间上沿且 OBV 上行
- markdown（下跌）：价格跌破区间下沿且 OBV 下行
- distribution（派发）：价格在区间上 1/3 横盘 + 量增 + OBV 走平/下行
- accumulation（吸筹）：价格在区间下 1/3 + 量缩 + OBV 走平/上行
- transition：以上皆非
相位标签进报告观测 + 作为长线仓位乘数输入（派发相位禁加仓——预留，接线由调用方决定）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_BASE_LO = 80   # 区间下沿窗口（不含最近 20 根）
_BASE_HI = 80
_RECENT = 20
_OBV_SHORT = 20
_VOL_SHORT = 20
_VOL_LONG = 60


def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0.0)
    return (direction * volume).cumsum()


def _slope(s: pd.Series, window: int) -> float:
    """最近 window 根的线性回归斜率（min-max 归一化到 -1..1）。"""
    y = s.iloc[-window:].astype(float).values
    if len(y) < 5 or np.std(y) < 1e-12:
        return 0.0
    x = np.arange(len(y), dtype=float)
    b = float(np.polyfit(x, y, 1)[0])
    return float(np.tanh(b / (np.std(y) + 1e-12)))


def classify_phase(df: pd.DataFrame, base_window: int = 60,
                   recent: int = 20) -> Dict[str, Any]:
    """返回 {phase, base_high, base_low, obv_slope, vol_ratio, price_pos}。"""
    out: Dict[str, Any] = {"phase": "transition", "base_high": None, "base_low": None,
                           "obv_slope": 0.0, "vol_ratio": 0.0, "price_pos": 0.0}
    try:
        if df is None or len(df) < 140:
            return out
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)
        volume = df["volume"].astype(float) if "volume" in df.columns else pd.Series(
            np.ones(len(df)), index=df.index)

        # 基础区间 = 近 base_window 根（不含最近 recent 根）的高低点
        base_high = float(high.iloc[-(base_window + recent):-recent].max())
        base_low = float(low.iloc[-(base_window + recent):-recent].min())
        out["base_high"] = round(base_high, 4)
        out["base_low"] = round(base_low, 4)
        rng = max(base_high - base_low, 1e-9)

        c_now = float(close.iloc[-1])
        pos = (c_now - base_low) / rng
        out["price_pos"] = round(pos, 3)

        obv = _obv(close, volume)
        obv_slope = _slope(obv, _OBV_SHORT)
        out["obv_slope"] = round(obv_slope, 3)
        vol_ratio = float(volume.iloc[-_VOL_SHORT:].mean() / max(
            volume.iloc[-_VOL_LONG:].mean(), 1e-9))
        out["vol_ratio"] = round(vol_ratio, 3)

        if c_now > base_high and obv_slope > 0.15:
            phase = "markup"
        elif c_now < base_low and obv_slope < -0.15:
            phase = "markdown"
        elif pos >= 0.66 and vol_ratio >= 1.2 and obv_slope <= 0.15:
            phase = "distribution"
        elif pos <= 0.34 and vol_ratio <= 0.9:
            phase = "accumulation"
        else:
            phase = "transition"
        out["phase"] = phase
    except Exception as e:
        logger.debug("[WyckoffPhase] 相位检测失败: %s", e)
    return out
