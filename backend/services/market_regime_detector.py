"""
市况检测器 — 回测和实盘共用

通过 ATR + 趋势斜率 + 波动率 判断当前市场状态：
  trending  — 单边趋势（向上或向下）
  ranging   — 区间震荡
  volatile  — 高波动无方向
"""

import logging
import numpy as np
from typing import List, Optional

logger = logging.getLogger(__name__)


def detect_regime(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    lookback: int = 20,
    idx: Optional[int] = None,
) -> str:
    """
    检测指定位置的市场状态。

    Args:
        closes: 收盘价数组
        highs: 最高价数组
        lows: 最低价数组
        lookback: 回看周期
        idx: 检测位置（默认最后一根）

    Returns:
        "trending" | "ranging" | "volatile"
    """
    if idx is None:
        idx = len(closes) - 1
    if idx < lookback + 5:
        return "ranging"

    window = closes[idx - lookback: idx + 1]
    h_window = highs[idx - lookback: idx + 1]
    l_window = lows[idx - lookback: idx + 1]

    # 趋势斜率：线性回归斜率归一化
    x = np.arange(len(window), dtype=np.float64)
    if np.std(window) < 1e-10:
        return "ranging"
    slope = np.polyfit(x, window, 1)[0]
    norm_slope = abs(slope) / np.mean(window) * lookback

    # 波动率：ATR / 平均价
    tr = np.maximum(h_window[1:] - l_window[1:],
                    np.maximum(np.abs(h_window[1:] - window[:-1]),
                               np.abs(l_window[1:] - window[:-1])))
    avg_atr = np.mean(tr) if len(tr) > 0 else 0
    avg_price = np.mean(window)
    volatility = avg_atr / avg_price if avg_price > 0 else 0

    # 方向一致性：价格高于/低于EMA的比例
    ema = _simple_ema(window, min(lookback, 10))
    above = np.sum(window > ema) / len(window)
    direction_consistency = max(above, 1 - above)

    # 判定逻辑
    if norm_slope > 0.03 and direction_consistency > 0.65:
        return "trending"
    elif volatility > 0.03 and direction_consistency < 0.55:
        return "volatile"
    else:
        return "ranging"


def detect_regime_series(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    lookback: int = 20,
    step: int = 10,
) -> List[dict]:
    """
    对整段数据逐段检测市况，返回 [{start_idx, end_idx, regime}, ...]
    """
    regimes = []
    start = lookback + 5
    current_regime = None
    seg_start = start

    for i in range(start, len(closes), step):
        regime = detect_regime(closes, highs, lows, lookback, i)
        if regime != current_regime:
            if current_regime is not None:
                regimes.append({
                    "start_idx": seg_start,
                    "end_idx": i - 1,
                    "regime": current_regime,
                })
            current_regime = regime
            seg_start = i

    if current_regime is not None:
        regimes.append({
            "start_idx": seg_start,
            "end_idx": len(closes) - 1,
            "regime": current_regime,
        })

    return regimes


def _simple_ema(data: np.ndarray, period: int) -> np.ndarray:
    """快速 EMA 计算"""
    result = np.zeros_like(data, dtype=np.float64)
    result[0] = data[0]
    k = 2 / (period + 1)
    for i in range(1, len(data)):
        result[i] = data[i] * k + result[i - 1] * (1 - k)
    return result
