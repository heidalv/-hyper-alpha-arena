"""entry_timing — L2 中短线因子归一择时（长线趋势的入场/加仓切入窗口）。

设计见《长线趋势策略重构设计_V2.md》§4.2：
- 趋势方向已由 L1(up/down) 决定，L2 只负责「什么时候动手」——找回调结束/动能恢复的
  切入窗口，避免追高/接刀。
- 所有特征滚动 z-score 归一，IC 加权融合 → timing_score。
- 本模块提供日线级特征（8 年可验证）与 4h/1h 特征（执行层细化，Phase C/E 接入）。

无前视：所有滚动/z-score 只用截至当前 bar 的数据。
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

_EMA_FAST = 12
_EMA_SLOW = 26
_EMA_SIGNAL = 9
_ATR_PERIOD = 14
_Z_WINDOW = 120  # 滚动 z-score 窗口


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = _ATR_PERIOD) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def _zscore(s: pd.Series, window: int = _Z_WINDOW) -> pd.Series:
    """滚动 z-score（只用历史窗口，无未来）。"""
    mu = s.rolling(window).mean()
    sd = s.rolling(window).std()
    return (s - mu) / sd.replace(0, np.nan)


def _macd_hist(close: pd.Series) -> pd.Series:
    """MACD 柱状图 = DIF - DEA（12/26/9）。"""
    dif = _ema(close, _EMA_FAST) - _ema(close, _EMA_SLOW)
    dea = _ema(dif, _EMA_SIGNAL)
    return dif - dea


def timing_features(df: pd.DataFrame) -> pd.DataFrame:
    """日线级时机特征（8 年可验证）。

    返回列：
    - pullback_z   价格相对 EMA20 的偏离（z）——负=回调中/下方
    - macd_hist    MACD 柱状图
    - macd_rising  柱状图近 3 日回升（回调结束的动能恢复信号）
    - vol_contract ATR(14)/ATR(14).rolling(20).mean —— <1 = 波动收缩（蓄势）
    """
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    e20 = _ema(close, 20)
    atr = _atr(high, low, close)
    dev = (close - e20) / atr.replace(0, np.nan)
    pullback_z = _zscore(dev)

    hist = _macd_hist(close)
    macd_rising = hist > hist.shift(3)

    vol_contract = atr / atr.rolling(20).mean().replace(0, np.nan)

    return pd.DataFrame({
        "pullback_z": pullback_z,
        "macd_hist": hist,
        "macd_rising": macd_rising,
        "vol_contract": vol_contract,
    }, index=df.index)


def timing_score(feat: pd.DataFrame,
                 w_pullback: float = 0.4,
                 w_macd: float = 0.4,
                 w_vol: float = 0.2) -> pd.Series:
    """加权融合 timing_score（权重默认等权，待 Phase B 用 OOS 校准）。

    语义：回调到位（pullback_z 负但不深）、动能恢复（macd_rising）、波动收缩
    （vol_contract < 1）三者共振 → 高分 = 好的切入窗口。
    """
    pull = -feat["pullback_z"]  # 回调越深（负 z），时机分越高（但有界，防接刀）
    pull = pull.clip(-2, 2)
    macd = feat["macd_rising"].astype(float)  # 1 = 恢复
    macd = pd.Series(np.where(feat["macd_hist"].values > 0, 1.0, -1.0), index=feat.index) * 0.5 + macd * 0.5
    vol = (1.0 - feat["vol_contract"]).clip(-1, 1)  # 收缩为正
    # 各自 z 化后加权
    pull_z = _zscore(pull)
    macd_z = _zscore(macd)
    vol_z = _zscore(vol)
    score = w_pullback * pull_z + w_macd * macd_z + w_vol * vol_z
    return score


def entry_signal(df: pd.DataFrame, l1_state: pd.Series,
                 threshold: float = 0.5) -> pd.Series:
    """长线入场信号：L1=up 且 timing_score ≥ threshold 且 MACD 柱状图回升。

    返回 bool Series（True = 可开多/可加仓的切入窗口）。
    """
    feat = timing_features(df)
    ts = timing_score(feat)
    return (l1_state.values == "up") & (ts >= threshold) & (feat["macd_hist"] > feat["macd_hist"].shift(3))
