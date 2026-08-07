"""
多周期趋势共振分析服务

跨时间框架分析趋势一致性，计算共振评分，用于判断市场方向强度。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 时间框架权重配置
TIMEFRAME_WEIGHTS: Dict[str, float] = {
    "5m": 0.10,
    "15m": 0.15,
    "1h": 0.25,
    "4h": 0.25,
    "1d": 0.25,
}

# EMA 配置（用于趋势判断）
TREND_EMA_PERIOD = 20


@dataclass
class TimeframeAnalysis:
    """单个时间框架的分析结果"""
    period: str
    trend: str            # "bullish" | "bearish" | "neutral"
    trend_strength: float # 0-1
    ema_slope: float      # EMA 斜率（正值=上升, 负值=下降）
    rsi_value: float      # RSI 值
    price_vs_ema: float   # 价格/EMA 的偏移百分比
    macd_signal: str      # "bullish" | "bearish" | "neutral"
    candle_count: int     # 可用 K 线数量


@dataclass  
class ResonanceResult:
    """共振分析结果"""
    symbol: str
    resonance_score: float       # -100 到 +100, 正值=看涨一致
    resonance_level: str         # "strong_bullish" | "bullish" | "neutral" | "bearish" | "strong_bearish"
    alignment: float             # 0-1, 多周期排列一致性
    timeframes: List[TimeframeAnalysis]
    summary: str                 # 中文摘要
    signals: List[str]           # 信号列表


def _calculate_ema(values: List[float], period: int) -> List[float]:
    """简单 EMA 计算"""
    if not values or len(values) < period:
        return [values[-1]] * len(values) if values else []
    multiplier = 2.0 / (period + 1)
    ema = [values[0]]
    for v in values[1:]:
        ema.append((v - ema[-1]) * multiplier + ema[-1])
    return ema


def _calculate_rsi(values: List[float], period: int = 14) -> float:
    """计算最后一条的 RSI 值"""
    if len(values) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        gains.append(diff if diff > 0 else 0)
        losses.append(-diff if diff < 0 else 0)
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def analyze_single_timeframe(
    klines: List[Dict[str, Any]],
    period: str,
) -> Optional[TimeframeAnalysis]:
    """分析单个时间框架的趋势"""
    if not klines or len(klines) < 5:
        return None

    closes = [float(b["close"]) for b in klines]
    volumes = [float(b["volume"]) for b in klines]

    # EMA 斜率和价格位置
    ema_line = _calculate_ema(closes, TREND_EMA_PERIOD)
    if not ema_line:
        return None

    current_price = closes[-1]
    current_ema = ema_line[-1]
    price_vs_ema = ((current_price - current_ema) / current_ema) * 100 if current_ema > 0 else 0

    # EMA 斜率（最近 N 根）
    lookback = min(10, len(ema_line))
    ema_slope = (ema_line[-1] - ema_line[-lookback]) / max(abs(ema_line[-lookback]), 1e-10)

    # RSI
    rsi_val = _calculate_rsi(closes)

    # MACD 信号（简化版：用 12/26 EMA 差值和 9 EMA 差值）
    macd_line = _calculate_ema(closes, 12)
    macd_sig = _calculate_ema(closes, 26)
    if macd_line and macd_sig:
        macd = macd_line[-1] - macd_sig[-1]
        macd_signal = "bullish" if macd > 0 else "bearish"
    else:
        macd_signal = "neutral"

    # 趋势方向
    if rsi_val > 60 and price_vs_ema > 1 and ema_slope > 0.01:
        trend = "bullish"
    elif rsi_val < 40 and price_vs_ema < -1 and ema_slope < -0.01:
        trend = "bearish"
    else:
        trend = "neutral"

    # 趋势强度
    if trend == "bullish":
        strength = min(1.0, (price_vs_ema / 5) * 0.4 + ((rsi_val - 50) / 50) * 0.4 + (ema_slope * 10) * 0.2)
    elif trend == "bearish":
        strength = min(1.0, (abs(price_vs_ema) / 5) * 0.4 + ((50 - rsi_val) / 50) * 0.4 + (abs(ema_slope) * 10) * 0.2)
    else:
        strength = 0.3

    return TimeframeAnalysis(
        period=period,
        trend=trend,
        trend_strength=max(0.1, min(1.0, strength)),
        ema_slope=ema_slope,
        rsi_value=rsi_val,
        price_vs_ema=price_vs_ema,
        macd_signal=macd_signal,
        candle_count=len(klines),
    )


def compute_resonance(
    symbol: str,
    klines_by_period: Dict[str, List[Dict[str, Any]]],
) -> ResonanceResult:
    """
    多周期共振分析。

    Args:
        symbol: 交易对
        klines_by_period: { period: [klines] } 各周期的 K 线数据

    Returns:
        ResonanceResult 共振分析结果
    """
    timeframes: List[TimeframeAnalysis] = []
    weighted_score = 0.0
    total_weight = 0.0

    for period in ["5m", "15m", "1h", "4h", "1d"]:
        klines = klines_by_period.get(period, [])
        tf = analyze_single_timeframe(klines, period)
        if tf:
            timeframes.append(tf)
            w = TIMEFRAME_WEIGHTS.get(period, 0.1)

            # 计算该时间框架的得分
            tf_score = 0.0
            if tf.trend == "bullish":
                tf_score = 50 + tf.trend_strength * 50  # 50-100
            elif tf.trend == "bearish":
                tf_score = -(50 + tf.trend_strength * 50)  # -100 to -50
            else:
                tf_score = (tf.rsi_value - 50) * 0.5  # 接近 rsi 偏离

            weighted_score += tf_score * w
            total_weight += w

    if total_weight == 0:
        return ResonanceResult(
            symbol=symbol,
            resonance_score=0,
            resonance_level="neutral",
            alignment=0,
            timeframes=[],
            summary="数据不足，无法分析",
            signals=[],
        )

    resonance_score = weighted_score / total_weight

    # 排列一致性
    trends = [t.trend for t in timeframes]
    bull_count = trends.count("bullish")
    bear_count = trends.count("bearish")
    alignment = max(bull_count, bear_count) / len(trends) if trends else 0

    # 共振等级
    if resonance_score > 60 and alignment >= 0.6:
        level = "strong_bullish"
    elif resonance_score > 20:
        level = "bullish"
    elif resonance_score < -60 and alignment >= 0.6:
        level = "strong_bearish"
    elif resonance_score < -20:
        level = "bearish"
    else:
        level = "neutral"

    # 中文摘要
    summaries = {
        "strong_bullish": f"{symbol} 多周期强烈看涨共振 (得分: {resonance_score:.0f})",
        "bullish": f"{symbol} 多周期偏多 (得分: {resonance_score:.0f})",
        "neutral": f"{symbol} 多周期分歧，方向不明 (得分: {resonance_score:.0f})",
        "bearish": f"{symbol} 多周期偏空 (得分: {resonance_score:.0f})",
        "strong_bearish": f"{symbol} 多周期强烈看跌共振 (得分: {resonance_score:.0f})",
    }

    # 信号
    signals = []
    if alignment >= 0.8 and level in ("strong_bullish", "strong_bearish"):
        signals.append("多周期高度一致，趋势信号强烈")
    elif alignment < 0.6:
        signals.append("周期分歧，建议等待方向确认")

    if timeframes:
        tf_info = [f"{t.period}({t.trend[:3]})" for t in timeframes]
        signals.append(f"各周期趋势: {', '.join(tf_info)}")

    return ResonanceResult(
        symbol=symbol,
        resonance_score=round(resonance_score, 1),
        resonance_level=level,
        alignment=round(alignment, 2),
        timeframes=timeframes,
        summary=summaries.get(level, summaries["neutral"]),
        signals=signals,
    )
