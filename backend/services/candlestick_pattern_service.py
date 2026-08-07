"""
K线形态识别服务 — 算法检测经典蜡烛图形态

支持形态:
- 单K线: 十字星、锤子线、射击之星、光头光脚
- 双K线: 吞没、刺透、乌云盖顶、孕线
- 三K线: 晨星、黄昏星、三白兵、三乌鸦
- 多K线: 双顶/双底、头肩顶/底
"""

from __future__ import annotations

import math
import logging
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── 蜡烛实体数据 ──

@dataclass
class CandleInfo:
    """单根 K 线的标准化信息"""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    body: float          # 实体大小 = |close - open|
    upper_shadow: float  # 上影线
    lower_shadow: float  # 下影线
    range: float         # high - low
    is_bullish: bool     # close > open
    body_ratio: float    # body / range (实体占比)


@dataclass
class DetectedPattern:
    """检测到的形态结果"""
    id: str                # 形态 ID (如 "hammer_1")
    name: str              # 中文名 + 英文名 (如 "锤子线 (Hammer)")
    pattern_type: str      # "bullish" | "bearish" | "neutral"
    timestamp: int         # 形态发生的时间戳
    confidence: float      # 0–1 置信度
    description: str       # 形态描述
    trading_hints: List[str] = field(default_factory=list)
    reliability: str = "medium"


# ── 辅助函数 ──

def _to_candle(bar: Dict[str, Any]) -> CandleInfo:
    """将原始 K 线 dict 转为 CandleInfo"""
    o, h, l, c, v = (
        float(bar.get("open", 0)),
        float(bar.get("high", 0)),
        float(bar.get("low", 0)),
        float(bar.get("close", 0)),
        float(bar.get("volume", 0)),
    )
    body = abs(c - o)
    rng = h - l
    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - l
    return CandleInfo(
        timestamp=int(bar.get("timestamp", 0)),
        open=o, high=h, low=l, close=c, volume=v,
        body=body,
        upper_shadow=max(upper_shadow, 0),
        lower_shadow=max(lower_shadow, 0),
        range=max(rng, 1e-10),
        is_bullish=c > o,
        body_ratio=body / max(rng, 1e-10),
    )


# ── 单 K 线形态 ──

def is_doji(c: CandleInfo) -> Optional[float]:
    """
    十字星: 开盘价 ≈ 收盘价, 上下影线近似等长
    返回置信度, 或 None (不匹配)
    """
    if c.body_ratio > 0.1:
        return None
    if c.range < 1e-10:
        return None
    # 影线均衡度
    shadow_balance = 1 - abs(c.upper_shadow - c.lower_shadow) / c.range
    conf = min(1.0, c.body_ratio * 0 + shadow_balance * 0.8 + 0.2)
    return conf


def is_hammer(c: CandleInfo) -> Optional[float]:
    """
    锤子线: 实体在顶部, 下影线 >= 2x 实体, 上影线很短
    """
    if c.range < 1e-10:
        return None
    # 实体在顶部三分之一
    body_mid = (c.open + c.close) / 2
    if body_mid < c.low + c.range * 0.67:
        return None
    if c.body < 1e-10:
        return None
    # 下影线 >= 2x 实体
    if c.lower_shadow < c.body * 2:
        return None
    # 上影线 < 0.15 * 区间
    if c.upper_shadow > c.range * 0.15:
        return None
    return min(1.0, c.lower_shadow / (c.body * 3))


def is_shooting_star(c: CandleInfo) -> Optional[float]:
    """
    射击之星: 实体在底部, 上影线 >= 2x 实体, 下影线很短
    """
    if c.range < 1e-10:
        return None
    body_mid = (c.open + c.close) / 2
    if body_mid > c.low + c.range * 0.33:
        return None
    if c.body < 1e-10:
        return None
    if c.upper_shadow < c.body * 2:
        return None
    if c.lower_shadow > c.range * 0.15:
        return None
    return min(1.0, c.upper_shadow / (c.body * 3))


def is_marubozu(c: CandleInfo) -> Optional[float]:
    """
    光头光脚: 几乎没有影线, 大实体
    """
    if c.range < 1e-10:
        return None
    if c.upper_shadow > c.range * 0.05 or c.lower_shadow > c.range * 0.05:
        return None
    if c.body_ratio < 0.7:
        return None
    return c.body_ratio


def is_spinning_top(c: CandleInfo) -> Optional[float]:
    """
    纺锤线: 小实体, 上下影线都比实体长
    """
    if c.range < 1e-10:
        return None
    if c.body_ratio > 0.3:
        return None
    if c.upper_shadow < c.body or c.lower_shadow < c.body:
        return None
    return min(1.0, (1 - c.body_ratio))


# ── 双 K 线形态 ──

def is_bullish_engulfing(c1: CandleInfo, c2: CandleInfo) -> Optional[float]:
    """
    看涨吞没: c1 阴线 → c2 阳线完全吞没 c1 实体
    """
    if c1.is_bullish or not c2.is_bullish:
        return None
    if c2.open > c1.close or c2.close < c1.open:
        return None
    conf = min(1.0, c2.body / max(c1.body, 1e-10) * 0.7)
    return conf


def is_bearish_engulfing(c1: CandleInfo, c2: CandleInfo) -> Optional[float]:
    """
    看跌吞没: c1 阳线 → c2 阴线完全吞没 c1 实体
    """
    if not c1.is_bullish or c2.is_bullish:
        return None
    if c2.open < c1.close or c2.close > c1.open:
        return None
    conf = min(1.0, c2.body / max(c1.body, 1e-10) * 0.7)
    return conf


def is_piercing_line(c1: CandleInfo, c2: CandleInfo) -> Optional[float]:
    """
    刺透形态: c1 阴线 → c2 阳线, c2 收盘 > c1 实体中点
    """
    if c1.is_bullish or not c2.is_bullish:
        return None
    mid1 = (c1.open + c1.close) / 2
    if c2.close <= mid1:
        return None
    if c2.open > c1.close:
        return None
    return min(1.0, (c2.close - mid1) / max(c1.range * 0.5, 1e-10))


def is_dark_cloud_cover(c1: CandleInfo, c2: CandleInfo) -> Optional[float]:
    """
    乌云盖顶: c1 阳线 → c2 阴线, c2 收盘 < c1 实体中点
    """
    if not c1.is_bullish or c2.is_bullish:
        return None
    mid1 = (c1.open + c1.close) / 2
    if c2.close >= mid1:
        return None
    if c2.open < c1.close:
        return None
    return min(1.0, (mid1 - c2.close) / max(c1.range * 0.5, 1e-10))


def is_harami(c1: CandleInfo, c2: CandleInfo) -> Optional[Tuple[float, str]]:
    """
    孕线: c2 实体完全被 c1 实体包含
    返回 (置信度, 类型 "bullish"/"bearish")
    """
    if c2.body >= c1.body * 0.95:
        return None
    if c2.open < min(c1.open, c1.close) or c2.close > max(c1.open, c1.close):
        return None
    conf = min(1.0, 1 - c2.body_ratio + 0.3)
    typ = "bullish" if c1.is_bullish else "bearish"
    return (conf, typ)


# ── 三 K 线形态 ──

def is_morning_star(c1: CandleInfo, c2: CandleInfo, c3: CandleInfo) -> Optional[float]:
    """
    晨星: 阴线 → 小实体/十字星 (跳空低) → 阳线 (跳空高, 收盘 > c1 实体中点)
    """
    if c1.is_bullish or not c3.is_bullish:
        return None
    # c2 小实体或十字星
    if c2.body_ratio > 0.3:
        return None
    # c2 在 c1 下方
    if max(c2.open, c2.close) > c1.close:
        return None
    # c3 收盘超过 c1 实体中点
    mid1 = (c1.open + c1.close) / 2
    if c3.close <= mid1:
        return None
    return min(1.0, is_doji(c2) or 0.4 + 0.3)


def is_evening_star(c1: CandleInfo, c2: CandleInfo, c3: CandleInfo) -> Optional[float]:
    """
    黄昏星: 阳线 → 小实体/十字星 (跳空高) → 阴线 (跳空低, 收盘 < c1 实体中点)
    """
    if not c1.is_bullish or c3.is_bullish:
        return None
    if c2.body_ratio > 0.3:
        return None
    if min(c2.open, c2.close) < c1.close:
        return None
    mid1 = (c1.open + c1.close) / 2
    if c3.close >= mid1:
        return None
    return min(1.0, is_doji(c2) or 0.4 + 0.3)


def is_three_white_soldiers(c1: CandleInfo, c2: CandleInfo, c3: CandleInfo) -> Optional[float]:
    """
    三白兵: 连续三根阳线, 每次收在新高, 实体依次增大
    """
    if not all((c.is_bullish for c in (c1, c2, c3))):
        return None
    if not (c1.close < c2.close < c3.close):
        return None
    if not (c2.open > c1.open and c3.open > c2.open):
        return None
    conf = min(1.0, (c3.close - c1.open) / max(c1.range, 1e-10) * 0.5 + 0.3)
    return conf


def is_three_black_crows(c1: CandleInfo, c2: CandleInfo, c3: CandleInfo) -> Optional[float]:
    """
    三乌鸦: 连续三根阴线, 每次收在新低, 实体依次增大
    """
    if all((c.is_bullish for c in (c1, c2, c3))):
        return None
    if not (c1.close > c2.close > c3.close):
        return None
    if not (c2.open < c1.open and c3.open < c2.open):
        return None
    conf = min(1.0, (c1.open - c3.close) / max(c1.range, 1e-10) * 0.5 + 0.3)
    return conf


# ── 多 K 线形态(简化版) ──

def find_swing_points(candles: List[CandleInfo], lookback: int = 3) -> Tuple[List[int], List[int]]:
    """寻找摆荡高点和低点索引"""
    highs, lows = [], []
    n = len(candles)
    for i in range(lookback, n - lookback):
        is_high = all(candles[i].high >= candles[j].high for j in range(i - lookback, i + lookback + 1) if j != i)
        is_low = all(candles[i].low <= candles[j].low for j in range(i - lookback, i + lookback + 1) if j != i)
        if is_high:
            highs.append(i)
        if is_low:
            lows.append(i)
    return highs, lows


def is_double_top(candles: List[CandleInfo], price_tolerance: float = 0.03) -> List[Dict[str, Any]]:
    """
    双顶: 两个近似相等的高点, 中间有低点
    """
    results = []
    highs, _ = find_swing_points(candles, lookback=3)
    for i in range(len(highs) - 1):
        i1, i2 = highs[i], highs[i + 1]
        if i2 - i1 < 3 or i2 - i1 > 40:
            continue
        h1, h2 = candles[i1].high, candles[i2].high
        if abs(h1 - h2) / max(h1, 1e-10) > price_tolerance:
            continue
        # 中间需要有一个明显的低点
        between = candles[i1:i2 + 1]
        min_low = min(c.low for c in between)
        if min_low == h1:
            continue
        conf = 1 - abs(h1 - h2) / max(h1, 1e-10)
        results.append({
            "name": "双顶 (Double Top)",
            "pattern_type": "bearish",
            "timestamp": candles[i2].timestamp,
            "confidence": conf,
            "description": "两次冲高形成相同高点，顶部形态",
            "trading_hints": ["颈线破位后入场做空", "止损设在前高上方"],
            "reliability": "high",
        })
    return results


def is_double_bottom(candles: List[CandleInfo], price_tolerance: float = 0.03) -> List[Dict[str, Any]]:
    """双底: 两个近似相等的低点, 中间有高点"""
    results = []
    _, lows = find_swing_points(candles, lookback=3)
    for i in range(len(lows) - 1):
        i1, i2 = lows[i], lows[i + 1]
        if i2 - i1 < 3 or i2 - i1 > 40:
            continue
        l1, l2 = candles[i1].low, candles[i2].low
        if abs(l1 - l2) / max(l1, 1e-10) > price_tolerance:
            continue
        conf = 1 - abs(l1 - l2) / max(l1, 1e-10)
        results.append({
            "name": "双底 (Double Bottom)",
            "pattern_type": "bullish",
            "timestamp": candles[i2].timestamp,
            "confidence": conf,
            "description": "两次探底形成相同低点，底部形态",
            "trading_hints": ["颈线突破后入场做多", "止损设在前低下方"],
            "reliability": "high",
        })
    return results


def is_head_and_shoulders_top(candles: List[CandleInfo]) -> List[Dict[str, Any]]:
    """
    头肩顶: 左肩 → 头(更高) → 右肩(≈左肩高度)
    """
    results = []
    highs, lows = find_swing_points(candles, lookback=3)
    if len(highs) < 3:
        return results

    for i in range(len(highs) - 2):
        ls, hd, rs = highs[i], highs[i + 1], highs[i + 2]
        # 头最高
        if candles[hd].high <= candles[ls].high or candles[hd].high <= candles[rs].high:
            continue
        # 左右肩高度接近
        if abs(candles[ls].high - candles[rs].high) / max(candles[ls].high, 1e-10) > 0.15:
            continue
        # 间距合理
        if (hd - ls) < 2 or (rs - hd) < 2:
            continue
        conf = min(1.0, (candles[hd].high - max(candles[ls].high, candles[rs].high)) / max(candles[hd].high * 0.05, 1e-10) + 0.5)
        results.append({
            "name": "头肩顶 (Head and Shoulders Top)",
            "pattern_type": "bearish",
            "timestamp": candles[rs].timestamp,
            "confidence": conf,
            "description": "左肩、头部、右肩三个峰值，经典反转形态",
            "trading_hints": ["颈线破位后入场", "目标为头部到颈线的距离"],
            "reliability": "high",
        })
    return results


def is_head_and_shoulders_bottom(candles: List[CandleInfo]) -> List[Dict[str, Any]]:
    """头肩底: 左肩 → 头(更低) → 右肩(≈左肩低点)"""
    results = []
    _, lows = find_swing_points(candles, lookback=3)
    if len(lows) < 3:
        return results

    for i in range(len(lows) - 2):
        ls, hd, rs = lows[i], lows[i + 1], lows[i + 2]
        if candles[hd].low >= candles[ls].low or candles[hd].low >= candles[rs].low:
            continue
        if abs(candles[ls].low - candles[rs].low) / max(candles[ls].low, 1e-10) > 0.15:
            continue
        if (hd - ls) < 2 or (rs - hd) < 2:
            continue
        conf = min(1.0, (min(candles[ls].low, candles[rs].low) - candles[hd].low) / max(candles[hd].low * 0.05, 1e-10) + 0.5)
        results.append({
            "name": "头肩底 (Inverse Head and Shoulders)",
            "pattern_type": "bullish",
            "timestamp": candles[rs].timestamp,
            "confidence": conf,
            "description": "左肩、头部、右肩三个谷底，经典底部反转形态",
            "trading_hints": ["颈线突破后入场", "目标为头部到颈线的距离"],
            "reliability": "high",
        })
    return results


# ── 主检测函数 ──

def detect_patterns(
    kline_data: List[Dict[str, Any]],
    min_confidence: float = 0.3,
) -> List[DetectedPattern]:
    """
    从 K 线数据中检测所有形态。

    Args:
        kline_data: K 线列表 (包含 timestamp, open, high, low, close, volume)
        min_confidence: 最低置信度阈值

    Returns:
        检测到的形态列表 (按时间戳排序)
    """
    if len(kline_data) < 2:
        return []

    candles = [_to_candle(b) for b in kline_data]
    patterns: List[DetectedPattern] = []

    # ── 单 K 线 ──
    for c in candles:
        ts = c.timestamp

        if (conf := is_doji(c)) and conf >= min_confidence:
            patterns.append(DetectedPattern(
                id="doji_1", name="十字星 (Doji)", pattern_type="neutral",
                timestamp=ts, confidence=conf,
                description="开盘收盘接近，市场犹豫",
                trading_hints=["结合前后K线判断", "需配合其他指标"],
                reliability="low"))

        if (conf := is_hammer(c)) and conf >= min_confidence:
            patterns.append(DetectedPattern(
                id="hammer_1", name="锤子线 (Hammer)", pattern_type="bullish",
                timestamp=ts, confidence=conf,
                description="下影线较长，实体在顶部，暗示下跌趋势可能反转",
                trading_hints=["等待次日确认", "配合成交量放大"],
                reliability="medium"))

        if (conf := is_shooting_star(c)) and conf >= min_confidence:
            patterns.append(DetectedPattern(
                id="shooting_star_1", name="射击之星 (Shooting Star)", pattern_type="bearish",
                timestamp=ts, confidence=conf,
                description="上影线较长，实体在底部，暗示上涨趋势可能反转",
                trading_hints=["等待次日确认", "配合成交量放大"],
                reliability="medium"))

        if (conf := is_marubozu(c)) and conf >= min_confidence:
            ptype = "bullish" if c.is_bullish else "bearish"
            pname = "光头光脚阳线 (Marubozu)" if c.is_bullish else "光头光脚阴线 (Marubozu)"
            patterns.append(DetectedPattern(
                id=f"marubozu_{ptype}", name=pname, pattern_type=ptype,
                timestamp=ts, confidence=conf,
                description="几乎没有影线的大实体，趋势强劲",
                trading_hints=["顺势操作"],
                reliability="medium"))

    # ── 双 K 线 ──
    for i in range(len(candles) - 1):
        c1, c2 = candles[i], candles[i + 1]
        ts = c2.timestamp

        if (conf := is_bullish_engulfing(c1, c2)) and conf >= min_confidence:
            patterns.append(DetectedPattern(
                id="engulf_bullish_1", name="看涨吞没 (Bullish Engulfing)", pattern_type="bullish",
                timestamp=ts, confidence=conf,
                description="阳线完全吞没前一根阴线，强烈反转信号",
                trading_hints=["前一日趋势越强信号越可靠"],
                reliability="high"))

        if (conf := is_bearish_engulfing(c1, c2)) and conf >= min_confidence:
            patterns.append(DetectedPattern(
                id="engulf_bearish_1", name="看跌吞没 (Bearish Engulfing)", pattern_type="bearish",
                timestamp=ts, confidence=conf,
                description="阴线完全吞没前一根阳线，强烈反转信号",
                trading_hints=["前一日趋势越强信号越可靠"],
                reliability="high"))

        if (conf := is_piercing_line(c1, c2)) and conf >= min_confidence:
            patterns.append(DetectedPattern(
                id="piercing_1", name="刺透形态 (Piercing Line)", pattern_type="bullish",
                timestamp=ts, confidence=conf,
                description="阴线后阳线收于阴线实体中点上方，看涨反转",
                trading_hints=["等待次日确认"],
                reliability="medium"))

        if (conf := is_dark_cloud_cover(c1, c2)) and conf >= min_confidence:
            patterns.append(DetectedPattern(
                id="dark_cloud_1", name="乌云盖顶 (Dark Cloud Cover)", pattern_type="bearish",
                timestamp=ts, confidence=conf,
                description="阳线后阴线收于阳线实体中点下方，看跌反转",
                trading_hints=["等待次日确认"],
                reliability="medium"))

        harami = is_harami(c1, c2)
        if harami and harami[0] >= min_confidence:
            conf, typ = harami
            pname = "看涨孕线 (Bullish Harami)" if typ == "bullish" else "看跌孕线 (Bearish Harami)"
            patterns.append(DetectedPattern(
                id=f"harami_{typ}_1", name=pname, pattern_type=typ,
                timestamp=ts, confidence=conf,
                description="小K线完全被前一根大K线实体包含，趋势可能反转",
                trading_hints=["等待突破确认"],
                reliability="medium"))

    # ── 三 K 线 ──
    for i in range(len(candles) - 2):
        c1, c2, c3 = candles[i], candles[i + 1], candles[i + 2]
        ts = c3.timestamp

        if (conf := is_morning_star(c1, c2, c3)) and conf >= min_confidence:
            patterns.append(DetectedPattern(
                id="morning_star_1", name="晨星 (Morning Star)", pattern_type="bullish",
                timestamp=ts, confidence=conf,
                description="阴线-小K线-阳线三根组合，预示底部反转",
                trading_hints=["十字星实体越小越好", "阳线需突破阴线中点"],
                reliability="high"))

        if (conf := is_evening_star(c1, c2, c3)) and conf >= min_confidence:
            patterns.append(DetectedPattern(
                id="evening_star_1", name="黄昏星 (Evening Star)", pattern_type="bearish",
                timestamp=ts, confidence=conf,
                description="阳线-小K线-阴线三根组合，预示顶部反转",
                trading_hints=["十字星实体越小越好", "阴线需跌破阳线中点"],
                reliability="high"))

        if (conf := is_three_white_soldiers(c1, c2, c3)) and conf >= min_confidence:
            patterns.append(DetectedPattern(
                id="three_white_soldiers_1", name="三白兵 (Three White Soldiers)", pattern_type="bullish",
                timestamp=ts, confidence=conf,
                description="连续三根阳线稳步上涨，强势上涨信号",
                trading_hints=["成交量递增确认"],
                reliability="high"))

        if (conf := is_three_black_crows(c1, c2, c3)) and conf >= min_confidence:
            patterns.append(DetectedPattern(
                id="three_black_crows_1", name="三乌鸦 (Three Black Crows)", pattern_type="bearish",
                timestamp=ts, confidence=conf,
                description="连续三根阴线稳步下跌，弱势下跌信号",
                trading_hints=["成交量递增确认"],
                reliability="high"))

    # ── 多 K 线 (最少 10 根) ──
    if len(candles) >= 10:
        for res in is_double_top(candles):
            patterns.append(DetectedPattern(id="double_top_1", **res))
        for res in is_double_bottom(candles):
            patterns.append(DetectedPattern(id="double_bottom_1", **res))
        for res in is_head_and_shoulders_top(candles):
            patterns.append(DetectedPattern(id="head_shoulders_1", **res))
        for res in is_head_and_shoulders_bottom(candles):
            patterns.append(DetectedPattern(id="head_shoulders_bottom_1", **res))

    # 去重: 同一时间戳 + 同类型只保留最高置信度
    seen: Dict[Tuple[int, str], DetectedPattern] = {}
    for p in patterns:
        key = (p.timestamp, p.name)
        if key not in seen or p.confidence > seen[key].confidence:
            seen[key] = p

    return sorted(seen.values(), key=lambda p: p.timestamp)
