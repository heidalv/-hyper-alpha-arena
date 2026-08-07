"""
市场指纹引擎 — 统一的环境识别

回测和实盘用同一套代码识别市场环境，确保一致性。
输出一个5维数值向量 + 1个离散状态标签。
"""

import math
import logging
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any

import numpy as np

logger = logging.getLogger(__name__)

REGIME_LABELS = ("trending_up", "trending_down", "ranging", "volatile", "breakout")

RICH_TO_SIMPLE_MAP = {
    "breakout": "breakout",
    "continuation": "trending_up",
    "absorption": "ranging",
    "exhaustion": "volatile",
    "trap": "volatile",
    "stop_hunt": "volatile",
    "noise": "ranging",
    "trending_up": "trending_up",
    "trending_down": "trending_down",
    "ranging": "ranging",
    "volatile": "volatile",
    "bull": "trending_up",
    "bear": "trending_down",
    "sideways": "ranging",
}


@dataclass
class MarketFingerprint:
    """5维数值指纹 + 离散状态标签"""
    trend_score: float = 0.0       # [-1, 1] EMA排列 + 价格位置
    volatility_rank: float = 0.0   # [0, 1] ATR相对历史的百分位
    momentum_score: float = 0.0    # [-1, 1] MACD + RSI 综合
    volume_profile: float = 0.0    # [-1, 1] 量能趋势
    mean_reversion: float = 0.0    # [0, 1] 布林带位置
    regime: str = "ranging"        # 离散标签

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_vector(self) -> List[float]:
        return [self.trend_score, self.volatility_rank, self.momentum_score,
                self.volume_profile, self.mean_reversion]


def _safe(val: float, default: float = 0.0) -> float:
    if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
        return default
    return float(val)


def _ema(data: np.ndarray, period: int) -> np.ndarray:
    """快速EMA计算"""
    alpha = 2.0 / (period + 1)
    result = np.empty_like(data, dtype=float)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result


def compute_fingerprint(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    volumes: np.ndarray,
    index: int = -1,
    ema_fast_period: int = 9,
    ema_mid_period: int = 21,
    ema_slow_period: int = 55,
    rsi_period: int = 14,
    bb_period: int = 20,
    atr_period: int = 14,
) -> MarketFingerprint:
    """
    从K线数据计算市场指纹（回测和实盘通用）。
    index 参数指定计算到哪根bar（-1表示最新）。
    """
    fp = MarketFingerprint()

    n = len(closes)
    if n < max(ema_slow_period, bb_period, atr_period, rsi_period) + 5:
        return fp

    idx = index if index >= 0 else n - 1
    if idx < ema_slow_period + 5:
        return fp

    c = closes[:idx + 1].astype(float)
    h = highs[:idx + 1].astype(float)
    lo = lows[:idx + 1].astype(float)
    v = volumes[:idx + 1].astype(float)

    # --- 1. Trend Score ---
    ema_f = _ema(c, ema_fast_period)
    ema_m = _ema(c, ema_mid_period)
    ema_s = _ema(c, ema_slow_period)

    price = c[-1]
    ef, em, es = ema_f[-1], ema_m[-1], ema_s[-1]

    if es > 0:
        pos_score = (price - es) / es
        align_score = 0.0
        if ef > em > es:
            align_score = 0.5
        elif ef < em < es:
            align_score = -0.5
        fp.trend_score = _safe(max(-1, min(1, pos_score * 10 + align_score)))

    # --- 2. Volatility Rank ---
    tr = np.maximum(h[1:] - lo[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(lo[1:] - c[:-1])))
    if len(tr) >= atr_period:
        atr_vals = np.convolve(tr, np.ones(atr_period) / atr_period, mode="valid")
        current_atr = atr_vals[-1]
        if len(atr_vals) > 1:
            fp.volatility_rank = _safe(float(np.searchsorted(np.sort(atr_vals), current_atr) / len(atr_vals)))

    # --- 3. Momentum Score ---
    if len(c) > rsi_period + 1:
        deltas = np.diff(c[-(rsi_period + 1):])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains) if len(gains) > 0 else 0
        avg_loss = np.mean(losses) if len(losses) > 0 else 1e-10
        rs = avg_gain / max(avg_loss, 1e-10)
        rsi = 100 - 100 / (1 + rs)
        rsi_norm = (rsi - 50) / 50  # [-1, 1]

        ema12 = _ema(c, 12)
        ema26 = _ema(c, 26)
        macd_val = ema12[-1] - ema26[-1]
        macd_norm = _safe(macd_val / max(abs(price), 1e-10) * 100)

        fp.momentum_score = _safe(max(-1, min(1, rsi_norm * 0.6 + macd_norm * 0.4)))

    # --- 4. Volume Profile ---
    if len(v) >= 20:
        recent_vol = np.mean(v[-5:])
        older_vol = np.mean(v[-20:-5])
        if older_vol > 0:
            ratio = recent_vol / older_vol
            fp.volume_profile = _safe(max(-1, min(1, (ratio - 1) * 2)))

    # --- 5. Mean Reversion (Bollinger position) ---
    if len(c) >= bb_period:
        bb_slice = c[-bb_period:]
        bb_mean = np.mean(bb_slice)
        bb_std = np.std(bb_slice)
        if bb_std > 0:
            z = (price - bb_mean) / (2 * bb_std)
            fp.mean_reversion = _safe(max(0, min(1, (z + 1) / 2)))

    # --- 6. Regime Label ---
    fp.regime = _classify_regime(fp)

    return fp


def _classify_regime(fp: MarketFingerprint) -> str:
    """根据5维指纹确定离散状态标签"""
    if fp.volatility_rank > 0.85:
        return "volatile"
    if abs(fp.trend_score) > 0.5 and fp.momentum_score > 0.3:
        return "trending_up" if fp.trend_score > 0 else "trending_down"
    if abs(fp.trend_score) > 0.4 and fp.momentum_score < -0.3:
        return "trending_down" if fp.trend_score < 0 else "trending_up"
    if fp.volatility_rank > 0.7 and abs(fp.trend_score) > 0.3:
        return "breakout"
    return "ranging"


def compute_fingerprint_from_live(
    market_data: Dict[str, Any],
    ema_fast_period: int = 9,
    ema_mid_period: int = 21,
    ema_slow_period: int = 55,
) -> MarketFingerprint:
    """从实盘市场数据字典计算指纹（便利函数）"""
    try:
        closes = np.array(market_data.get("closes", []), dtype=float)
        highs = np.array(market_data.get("highs", []), dtype=float)
        lows_arr = np.array(market_data.get("lows", []), dtype=float)
        volumes = np.array(market_data.get("volumes", []), dtype=float)

        if len(closes) < 60:
            return MarketFingerprint()

        return compute_fingerprint(
            closes, highs, lows_arr, volumes,
            ema_fast_period=ema_fast_period,
            ema_mid_period=ema_mid_period,
            ema_slow_period=ema_slow_period,
        )
    except Exception as e:
        logger.warning(f"[Fingerprint] 计算指纹失败: {e}")
        return MarketFingerprint()


def simplify_regime(rich_regime: str) -> str:
    """将 market_regime_service 的7种丰富分类映射到5种简化标签"""
    return RICH_TO_SIMPLE_MAP.get(rich_regime, "ranging")
