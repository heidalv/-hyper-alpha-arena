"""
支撑阻力位自动计算服务

支持多种计算方式:
1. Standard Pivot Points (标准枢轴点)
2. Fibonacci Pivot & Retracement (斐波那契)
3. Volume Profile Nodes (成交量分布节点)
4. Swing Highs/Lows (摆荡高低点)
5. Round Numbers (整数关口)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MIN_SWING_INTERVAL = 3


@dataclass
class SRLevel:
    """支撑/阻力位"""
    price: float
    label: str
    level_type: str   # "support" | "resistance"
    method: str        # 计算方法
    strength: float    # 0-1 强度评估


@dataclass
class SRResult:
    """支撑阻力分析结果"""
    symbol: str
    period: str
    current_price: float
    supports: List[SRLevel]    # 支撑位（按价格降序）
    resistances: List[SRLevel] # 阻力位（按价格升序）
    pivot: float               # 枢轴点
    all_levels: List[SRLevel]  # 所有识别出的水平位


def calculate_pivot_points(high: float, low: float, close: float) -> Dict[str, float]:
    """标准枢轴点计算"""
    pp = (high + low + close) / 3
    return {
        "P": round(pp, 2),
        "R1": round(2 * pp - low, 2),
        "R2": round(pp + (high - low), 2),
        "R3": round(high + 2 * (pp - low), 2),
        "S1": round(2 * pp - high, 2),
        "S2": round(pp - (high - low), 2),
        "S3": round(low - 2 * (high - pp), 2),
    }


def calculate_fibonacci_pivots(high: float, low: float, close: float) -> Dict[str, float]:
    """斐波那契枢轴点"""
    pp = (high + low + close) / 3
    range_val = high - low
    return {
        "P": round(pp, 2),
        "R1": round(pp + 0.382 * range_val, 2),
        "R2": round(pp + 0.618 * range_val, 2),
        "R3": round(pp + 1.000 * range_val, 2),
        "S1": round(pp - 0.382 * range_val, 2),
        "S2": round(pp - 0.618 * range_val, 2),
        "S3": round(pp - 1.000 * range_val, 2),
    }


def calculate_fibonacci_retracement(swing_high: float, swing_low: float) -> Dict[str, float]:
    """斐波那契回撤位"""
    diff = swing_high - swing_low
    return {
        "0": round(swing_low, 2),
        "0.236": round(swing_low + 0.236 * diff, 2),
        "0.382": round(swing_low + 0.382 * diff, 2),
        "0.5": round(swing_low + 0.5 * diff, 2),
        "0.618": round(swing_low + 0.618 * diff, 2),
        "0.786": round(swing_low + 0.786 * diff, 2),
        "1": round(swing_high, 2),
    }


def find_swing_highs_lows(
    highs: List[float],
    lows: List[float],
    min_distance: int = 5,
) -> Tuple[List[float], List[float]]:
    """找出摆动高点和低点"""
    n = len(highs)
    if n < 2 * min_distance + 1:
        return ([], [])

    swing_highs = []
    swing_lows = []

    for i in range(min_distance, n - min_distance):
        is_high = all(highs[i] >= highs[j] for j in range(i - min_distance, i + min_distance + 1))
        is_low = all(lows[i] <= lows[j] for j in range(i - min_distance, i + min_distance + 1))
        if is_high:
            swing_highs.append(highs[i])
        if is_low:
            swing_lows.append(lows[i])

    return (swing_highs, swing_lows)


def find_volume_nodes(
    klines: List[Dict[str, Any]],
    bins: int = 20,
) -> List[Tuple[float, float]]:
    """
    成交量分布: 按价格分桶，找出成交量最大的价格区间
    返回 [(价格, 权重), ...] 按权重降序
    """
    if not klines:
        return []

    all_highs = [float(b["high"]) for b in klines]
    all_lows = [float(b["low"]) for b in klines]

    price_min = min(all_lows)
    price_max = max(all_highs)

    if price_max <= price_min:
        return []

    bucket_size = (price_max - price_min) / bins
    buckets: Dict[int, float] = {}
    bucket_count: Dict[int, int] = {}

    for bar in klines:
        high = float(bar["high"])
        low = float(bar["low"])
        vol = float(bar["volume"])
        price_range = high - low
        if price_range < 1e-10:
            continue

        # 估计每根 K 线内的成交量分布（等权）
        for b in range(bins):
            bucket_price = price_min + b * bucket_size + bucket_size / 2
            if low <= bucket_price <= high:
                idx = b
                weight = vol / (price_range / bucket_size) if price_range > bucket_size else vol
                buckets[idx] = buckets.get(idx, 0) + weight
                bucket_count[idx] = bucket_count.get(idx, 0) + 1

    if not buckets:
        return []

    max_vol = max(buckets.values())
    nodes = []
    for idx, vol_val in buckets.items():
        if vol_val > max_vol * 0.3:  # 只保留前 30% 的节点
            price = price_min + idx * bucket_size + bucket_size / 2
            nodes.append((price, vol_val / max_vol))

    return sorted(nodes, key=lambda x: x[1], reverse=True)[:3]


def find_round_numbers(current_price: float, count: int = 5) -> List[float]:
    """找出附近的整数关口"""
    if current_price <= 0:
        return []

    magnitude = 10 ** (len(str(int(abs(current_price)))) - 2)
    round_base = round(current_price / magnitude) * magnitude

    levels = []
    for i in range(-count, count + 1):
        levels.append(round_base + i * magnitude / 2)

    return levels


def calculate_support_resistance(
    symbol: str,
    period: str,
    klines: List[Dict[str, Any]],
    swing_high: float = None,
    swing_low: float = None,
) -> SRResult:
    """
    综合计算支撑阻力位。

    Args:
        symbol: 交易对
        period: 周期
        klines: K 线数据 (按时间升序)
        swing_high: 区间最高价 (可选, 默认从 klines 取)
        swing_low: 区间最低价 (可选, 默认从 klines 取)

    Returns:
        SRResult 支撑阻力的综合结果
    """
    if not klines:
        return SRResult(
            symbol=symbol, period=period, current_price=0,
            supports=[], resistances=[], pivot=0, all_levels=[]
        )

    close = float(klines[-1]["close"])

    if swing_high is None:
        swing_high = max(float(b["high"]) for b in klines)
    if swing_low is None:
        swing_low = min(float(b["low"]) for b in klines)

    all_levels: List[SRLevel] = []

    # 1. 标准枢轴点
    pivots = calculate_pivot_points(swing_high, swing_low, close)
    for key, price in pivots.items():
        if price <= 0:
            continue
        if key == "P":
            all_levels.append(SRLevel(price=price, label="Pivot", level_type="neutral", method="pivot", strength=0.8))
        elif key.startswith("R"):
            all_levels.append(SRLevel(price=price, label=key, level_type="resistance", method="pivot", strength=0.7))
        else:
            all_levels.append(SRLevel(price=price, label=key, level_type="support", method="pivot", strength=0.7))

    # 2. 斐波那契回撤
    fib_levels = calculate_fibonacci_retracement(swing_high, swing_low)
    for key, price in fib_levels.items():
        ltype = "resistance" if price > close else "support"
        all_levels.append(SRLevel(
            price=price, label=f"Fib {key}", level_type=ltype,
            method="fibonacci", strength=0.4 if key in ("0.5",) else 0.5
        ))

    # 3. 摆动高低点
    highs_list = [float(b["high"]) for b in klines]
    lows_list = [float(b["low"]) for b in klines]
    swing_highs, swing_lows = find_swing_highs_lows(highs_list, lows_list, MIN_SWING_INTERVAL)

    for sh in swing_highs[:10]:
        all_levels.append(SRLevel(price=sh, label="Swing High", level_type="resistance", method="swing", strength=0.5))

    for sl in swing_lows[:10]:
        all_levels.append(SRLevel(price=sl, label="Swing Low", level_type="support", method="swing", strength=0.5))

    # 4. 成交量节点
    vol_nodes = find_volume_nodes(klines)
    for price, weight in vol_nodes:
        ltype = "resistance" if price > close else "support"
        all_levels.append(SRLevel(price=round(price, 2), label=f"Vol Node", level_type=ltype, method="volume", strength=weight * 0.6))

    # 5. 整数关口
    round_nums = find_round_numbers(close)
    for rn in round_nums:
        ltype = "resistance" if rn > close else "support"
        all_levels.append(SRLevel(price=rn, label="Round", level_type=ltype, method="round", strength=0.35))

    # 合并相近水平位（<0.5% 视为同一水平位）
    merged = _merge_nearby_levels(all_levels, close)

    # 分类
    supports = sorted(
        [l for l in merged if l.price < close],
        key=lambda l: -l.price
    )
    resistances = sorted(
        [l for l in merged if l.price > close],
        key=lambda l: l.price
    )

    return SRResult(
        symbol=symbol,
        period=period,
        current_price=close,
        supports=supports,
        resistances=resistances,
        pivot=pivots.get("P", close),
        all_levels=merged,
    )


def _merge_nearby_levels(levels: List[SRLevel], current_price: float, threshold: float = 0.005) -> List[SRLevel]:
    """合并相近的价格水平位"""
    if not levels:
        return []

    sorted_levels = sorted(levels, key=lambda l: l.price)
    merged: List[SRLevel] = []
    cluster: List[SRLevel] = [sorted_levels[0]]

    for level in sorted_levels[1:]:
        if abs(level.price - cluster[0].price) / max(current_price, 1e-10) < threshold:
            cluster.append(level)
        else:
            # 选集群中强度最高的
            best = max(cluster, key=lambda l: l.strength)
            merged.append(best)
            cluster = [level]

    merged.append(max(cluster, key=lambda l: l.strength))
    return merged
