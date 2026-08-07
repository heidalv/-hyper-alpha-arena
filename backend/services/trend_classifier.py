"""
Trend Classifier — 趋势分级引擎

纯函数模块，负责：
1. 单周期趋势分类（方向 + 强度 + 结构 + 持续性）
2. 多周期趋势确认（1d/4h/1h 嵌套验证）
3. 市场环境分级（trending / ranging / volatile）
4. 回调 vs 反转识别
"""

import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

# 2026-07-06 整改：三周期→K线周期的定义不再由本文件自己维护，统一从
# backend/config/tier_timeframe_map.py 读取，避免与 strategy_coordinator/
# multi_timeframe_orchestrator/signal_pre_screener 各自的硬编码定义互相矛盾。
from backend.config.tier_timeframe_map import TIER_TIMEFRAME_MAP

logger = logging.getLogger(__name__)


# ─────────────────── 数据结构 ───────────────────

@dataclass
class TrendState:
    """单一周期的趋势快照"""
    direction: str = "neutral"        # "up" / "down" / "neutral"
    strength: str = "none"            # "strong" / "moderate" / "weak" / "none"
    adx: float = 0.0
    plus_di: float = 0.0
    minus_di: float = 0.0
    duration_bars: int = 0            # EMA9 连续在 EMA21 同侧的 K 线数
    regime: str = "ranging"           # "trending" / "ranging" / "volatile"
    higher_highs: bool = False
    higher_lows: bool = False
    lower_highs: bool = False
    lower_lows: bool = False
    ema_alignment: str = "mixed"      # "bullish_aligned" / "bearish_aligned" / "mixed"
    atr_pct: float = 0.0             # ATR / close（波动率百分比）


@dataclass
class MultiTFResult:
    """多周期趋势确认结果"""
    alignment: str = "no_trend"       # "full_alignment" / "pullback_in_trend" / "conflict" / "no_trend"
    confirmed_direction: str = "neutral"  # "up" / "down" / "neutral"
    entry_timing: str = "wait"        # "enter" / "wait_pullback" / "wait" / "exit"
    confidence: float = 0.0           # 0.0 ~ 1.0
    details: str = ""


# ─────────────────── ADX 强度分级 ───────────────────

_ADX_STRONG = 40
_ADX_MODERATE = 25
_ADX_WEAK = 15


def _adx_strength(adx: float) -> str:
    if adx >= _ADX_STRONG:
        return "strong"
    if adx >= _ADX_MODERATE:
        return "moderate"
    if adx >= _ADX_WEAK:
        return "weak"
    return "none"


# ─────────────────── 核心：单周期分类 ───────────────────

def classify(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    adx: float = 0.0,
    plus_di: float = 0.0,
    minus_di: float = 0.0,
    atr: float = 0.0,
) -> TrendState:
    """
    对一组 K 线数据计算趋势状态。
    ADX/DI/ATR 可由调用方传入（已在 _capture_indicators 中算好），
    避免重复计算。
    """
    ts = TrendState(adx=adx, plus_di=plus_di, minus_di=minus_di)
    n = len(close)
    if n < 20:
        return ts

    # ATR 波动率
    ts.atr_pct = atr / close[-1] if close[-1] > 0 and atr > 0 else 0.0

    # ── EMA 排列 ──
    def _ema(data, period):
        if len(data) < period:
            return data
        alpha = 2.0 / (period + 1)
        out = np.zeros(len(data))
        out[period - 1] = np.mean(data[:period])
        for i in range(period, len(data)):
            out[i] = alpha * data[i] + (1 - alpha) * out[i - 1]
        return out

    ema9 = _ema(close, 9)
    ema21 = _ema(close, 21)
    ema50 = _ema(close, 50) if n >= 50 else ema21

    e9 = ema9[-1]
    e21 = ema21[-1]
    e50 = ema50[-1]

    if e9 > e21 > e50:
        ts.ema_alignment = "bullish_aligned"
    elif e9 < e21 < e50:
        ts.ema_alignment = "bearish_aligned"
    else:
        ts.ema_alignment = "mixed"

    # ── 趋势方向（+DI vs -DI 为主，EMA 辅助） ──
    if plus_di > minus_di and ts.ema_alignment in ("bullish_aligned", "mixed"):
        ts.direction = "up"
    elif minus_di > plus_di and ts.ema_alignment in ("bearish_aligned", "mixed"):
        ts.direction = "down"
    else:
        if ts.ema_alignment == "bullish_aligned":
            ts.direction = "up"
        elif ts.ema_alignment == "bearish_aligned":
            ts.direction = "down"
        else:
            ts.direction = "neutral"

    # ── 强度 ──
    ts.strength = _adx_strength(adx)

    # ── 价格结构：Higher High / Higher Low ──
    lookback = min(20, n)
    highs_seg = high[-lookback:]
    lows_seg = low[-lookback:]
    pivot_len = 5

    hh_count = 0
    hl_count = 0
    lh_count = 0
    ll_count = 0
    for i in range(pivot_len, lookback, pivot_len):
        prev_h = np.max(highs_seg[max(0, i - pivot_len):i])
        curr_h = np.max(highs_seg[i:min(i + pivot_len, lookback)])
        prev_l = np.min(lows_seg[max(0, i - pivot_len):i])
        curr_l = np.min(lows_seg[i:min(i + pivot_len, lookback)])
        if curr_h > prev_h:
            hh_count += 1
        else:
            lh_count += 1
        if curr_l > prev_l:
            hl_count += 1
        else:
            ll_count += 1

    ts.higher_highs = hh_count >= 2
    ts.higher_lows = hl_count >= 2
    ts.lower_highs = lh_count >= 2
    ts.lower_lows = ll_count >= 2

    # ── 趋势持续性：EMA9 连续在 EMA21 同侧的根数 ──
    duration = 0
    is_above = ema9[-1] > ema21[-1]
    for i in range(n - 1, max(n - 100, 20) - 1, -1):
        if is_above and ema9[i] > ema21[i]:
            duration += 1
        elif not is_above and ema9[i] < ema21[i]:
            duration += 1
        else:
            break
    ts.duration_bars = duration

    # ── 市场环境 ──
    if adx >= _ADX_WEAK and ts.direction != "neutral":
        ts.regime = "trending"
    elif ts.atr_pct > 0.04:
        ts.regime = "volatile"
    else:
        ts.regime = "ranging"

    return ts


# ─────────────────── 从 indicators dict 快速构建 TrendState ───────────────────

def classify_from_indicators(
    indicators: Dict[str, float],
    klines: Optional[Dict] = None,
    timeframe: str = "1h",
    symbol: str = "",
) -> TrendState:
    """
    从 UnifiedSnapshot.indicators[symbol] 字典直接构建 TrendState。
    如果提供了 klines[(symbol, timeframe)]，用于计算价格结构和 EMA 持续性。
    """
    suffix = ""
    if timeframe == "4h":
        suffix = "_4h"
    elif timeframe == "1d":
        suffix = "_1d"
    elif timeframe == "1w":
        suffix = "_1w"

    adx = indicators.get(f"adx{suffix}", 0.0)
    plus_di = indicators.get(f"plus_di{suffix}", 50.0)
    minus_di = indicators.get(f"minus_di{suffix}", 50.0)
    atr = indicators.get(f"atr{suffix}", 0.0)

    # 如果有 K 线数据，用完整算法
    if klines is not None:
        key = (symbol, timeframe)
        if key in klines:
            df = klines[key]
            h = df['high'].values.astype(float) if 'high' in df.columns else None
            l = df['low'].values.astype(float) if 'low' in df.columns else None
            c = df['close'].values.astype(float) if 'close' in df.columns else None
            if h is not None and l is not None and c is not None and len(c) >= 20:
                return classify(h, l, c, adx, plus_di, minus_di, atr)

    # 无 K 线时用简化判断
    ts = TrendState(adx=adx, plus_di=plus_di, minus_di=minus_di)
    ts.strength = _adx_strength(adx)
    if plus_di > minus_di + 5:
        ts.direction = "up"
    elif minus_di > plus_di + 5:
        ts.direction = "down"
    else:
        ts.direction = "neutral"

    price = indicators.get("close", 0)
    ts.atr_pct = atr / price if price > 0 and atr > 0 else 0.0

    if adx >= _ADX_WEAK and ts.direction != "neutral":
        ts.regime = "trending"
    elif ts.atr_pct > 0.04:
        ts.regime = "volatile"
    else:
        ts.regime = "ranging"

    # EMA 排列（简化版）
    if suffix == "":
        e9 = indicators.get("ema_9", 0)
        e21 = indicators.get("ema_21", 0)
        e50 = indicators.get("ema_50", 0)
    else:
        e9 = indicators.get(f"ema_9{suffix}", 0)
        e21 = indicators.get(f"ema_21{suffix}", 0)
        e50 = indicators.get(f"ema_50{suffix}", 0)

    if e9 and e21 and e50:
        if e9 > e21 > e50:
            ts.ema_alignment = "bullish_aligned"
        elif e9 < e21 < e50:
            ts.ema_alignment = "bearish_aligned"

    return ts


# ─────────────────── 多周期趋势确认 ───────────────────

def multi_timeframe_confirm(
    trend_long: TrendState,
    trend_mid: TrendState,
    trend_short: TrendState,
) -> MultiTFResult:
    """
    三周期嵌套确认：long 定方向 → mid 定节奏 → short 定入场。

    2026-07-06 整改：参数由原来硬编码的 (trend_1d, trend_4h, trend_1h) 改名为
    (trend_long, trend_mid, trend_short)，语义上对齐 TIER_TIMEFRAME_MAP 的
    long/mid/short 三档（分别以 4h/1h/15m 为 primary 周期）——调用方应传入
    "该 tier 的 primary 周期"算出的 TrendState，而不是固定传 1d/4h/1h。
    该函数此前在全项目内没有调用点，属于死代码，本次借统一周期映射的机会
    一并修正参数命名，避免未来接入调用时又引入第四套周期定义。
    """
    result = MultiTFResult()
    _long_tf = TIER_TIMEFRAME_MAP["long"]["primary"]
    _mid_tf = TIER_TIMEFRAME_MAP["mid"]["primary"]
    _short_tf = TIER_TIMEFRAME_MAP["short"]["primary"]

    dirs = [trend_long.direction, trend_mid.direction, trend_short.direction]
    up_count = dirs.count("up")
    down_count = dirs.count("down")

    # 全部无趋势
    all_weak = (
        trend_long.strength in ("none", "weak")
        and trend_mid.strength in ("none", "weak")
        and trend_short.strength in ("none", "weak")
    )
    if all_weak:
        result.alignment = "no_trend"
        result.confirmed_direction = "neutral"
        result.entry_timing = "wait"
        result.confidence = 0.15
        result.details = "三周期均无有效趋势（ADX 偏低）"
        return result

    # 三周期同向
    if up_count == 3:
        result.alignment = "full_alignment"
        result.confirmed_direction = "up"
        base_conf = 0.5
        if trend_long.strength in ("moderate", "strong"):
            base_conf += 0.2
        if trend_mid.strength in ("moderate", "strong"):
            base_conf += 0.15
        result.confidence = min(1.0, base_conf)
        result.entry_timing = "enter"
        result.details = "三周期多头一致"
        return result

    if down_count == 3:
        result.alignment = "full_alignment"
        result.confirmed_direction = "down"
        base_conf = 0.5
        if trend_long.strength in ("moderate", "strong"):
            base_conf += 0.2
        if trend_mid.strength in ("moderate", "strong"):
            base_conf += 0.15
        result.confidence = min(1.0, base_conf)
        result.entry_timing = "enter"
        result.details = "三周期空头一致"
        return result

    # 大中同向，小周期回调
    if trend_long.direction == trend_mid.direction and trend_long.direction != "neutral":
        main_dir = trend_long.direction
        if trend_short.direction != main_dir:
            result.alignment = "pullback_in_trend"
            result.confirmed_direction = main_dir
            result.entry_timing = "wait_pullback"
            result.confidence = 0.45
            result.details = f"{_long_tf}+{_mid_tf}={main_dir}，{_short_tf} 回调中 → 等待回调结束"
            return result
        # 大中小方向混合但大中一致
        result.alignment = "full_alignment"
        result.confirmed_direction = main_dir
        result.entry_timing = "enter"
        result.confidence = 0.55
        result.details = f"{_long_tf}+{_mid_tf}+{_short_tf}={main_dir}"
        return result

    # 大中矛盾
    if trend_long.direction != "neutral" and trend_mid.direction != "neutral" and trend_long.direction != trend_mid.direction:
        result.alignment = "conflict"
        result.confirmed_direction = "neutral"
        result.entry_timing = "wait"
        result.confidence = 0.15
        result.details = f"{_long_tf}={trend_long.direction} vs {_mid_tf}={trend_mid.direction} 矛盾"
        return result

    # 其它混合情况
    dominant = "neutral"
    if trend_long.direction != "neutral" and trend_long.strength not in ("none",):
        dominant = trend_long.direction
    elif trend_mid.direction != "neutral" and trend_mid.strength not in ("none",):
        dominant = trend_mid.direction

    result.alignment = "pullback_in_trend" if dominant != "neutral" else "no_trend"
    result.confirmed_direction = dominant
    result.entry_timing = "wait_pullback" if dominant != "neutral" else "wait"
    result.confidence = 0.30
    result.details = (
        f"混合: {_long_tf}={trend_long.direction}/{trend_long.strength}, "
        f"{_mid_tf}={trend_mid.direction}/{trend_mid.strength}, "
        f"{_short_tf}={trend_short.direction}/{trend_short.strength}"
    )
    return result


# ─────────────────── 回调 vs 反转（多维度评分系统）───────────────────

# 评分阈值：总分 >= PULLBACK_THRESHOLD → 正常回调；< → 可能反转
_PULLBACK_THRESHOLD = 50

def is_pullback_not_reversal(
    trend_4h: TrendState,
    trend_1h: TrendState,
    position_side: str,
    trend_1d: Optional[TrendState] = None,
) -> bool:
    """
    多维度评分判断：当前回调是趋势中的正常回调（True）还是可能的反转（False）。

    评分维度（满分 100）：
      D1: 4h 方向一致性  0-25
      D2: ADX 趋势强度   0-20
      D3: 价格结构完整性  0-20
      D4: EMA 排列      0-15
      D5: 1d 大周期支撑  0-10
      D6: DI 差值        0-10

    position_side: "long" / "short"
    """
    expected_dir = "up" if position_side in ("long", "buy") else "down"
    opposite_dir = "down" if expected_dir == "up" else "up"
    score = 0

    # ── D1: 4h 方向一致性（0-25）──
    if trend_4h.direction == expected_dir:
        score += 25
    elif trend_4h.direction == "neutral":
        score += 10
    # 4h 已翻转到反方向 → 0 分

    # ── D2: ADX 趋势强度（0-20）──
    if trend_4h.adx >= _ADX_STRONG:
        score += 20
    elif trend_4h.adx >= _ADX_MODERATE:
        score += 15
    elif trend_4h.adx >= _ADX_WEAK:
        score += 8
    # ADX < 15 → 0 分

    # ── D3: 价格结构完整性（0-20）──
    if expected_dir == "up":
        if trend_4h.higher_highs and trend_4h.higher_lows:
            score += 20
        elif trend_4h.higher_lows:
            score += 12
        elif not trend_4h.lower_lows:
            score += 5
    else:
        if trend_4h.lower_highs and trend_4h.lower_lows:
            score += 20
        elif trend_4h.lower_highs:
            score += 12
        elif not trend_4h.higher_highs:
            score += 5

    # ── D4: EMA 排列（0-15）──
    expected_ema = "bullish_aligned" if expected_dir == "up" else "bearish_aligned"
    if trend_4h.ema_alignment == expected_ema:
        score += 15
    elif trend_4h.ema_alignment == "mixed":
        score += 5
    # 反向排列 → 0 分

    # ── D5: 1d 大周期支撑（0-10）──
    if trend_1d is not None:
        if trend_1d.direction == expected_dir:
            if trend_1d.strength in ("strong", "moderate"):
                score += 10
            else:
                score += 6
        elif trend_1d.direction == "neutral":
            score += 3
    else:
        score += 5  # 无 1d 数据时给中间分

    # ── D6: DI 差值方向（0-10）──
    di_diff = trend_4h.plus_di - trend_4h.minus_di
    if expected_dir == "up" and di_diff > 5:
        score += 10
    elif expected_dir == "up" and di_diff > 0:
        score += 5
    elif expected_dir == "down" and di_diff < -5:
        score += 10
    elif expected_dir == "down" and di_diff < 0:
        score += 5

    # ── 1h 反转预警扣分 ──
    if trend_1h.direction == opposite_dir and trend_1h.adx >= _ADX_MODERATE:
        score -= 10
    elif trend_1h.direction == opposite_dir:
        score -= 5

    is_pullback = score >= _PULLBACK_THRESHOLD
    logger.debug(
        "pullback_score=%d threshold=%d is_pullback=%s side=%s "
        "4h_dir=%s 4h_adx=%.1f 1h_dir=%s",
        score, _PULLBACK_THRESHOLD, is_pullback, position_side,
        trend_4h.direction, trend_4h.adx, trend_1h.direction,
    )
    return is_pullback


# ─────────────────── 市场环境分级（基于 1d） ───────────────────

def classify_market_environment(trend_1d: TrendState) -> str:
    """
    基于日线级别判断市场环境：
    - strong_trend: 强趋势市（趋势跟随为主）
    - weak_trend: 弱趋势市（波段策略，小仓位）
    - ranging: 震荡市（区间策略或观望）
    - volatile: 高波动市（减仓、加宽 SL）
    """
    if trend_1d.atr_pct > 0.05:
        return "volatile"
    if trend_1d.strength in ("strong", "moderate") and trend_1d.direction != "neutral":
        return "strong_trend"
    if trend_1d.strength == "weak" and trend_1d.direction != "neutral":
        return "weak_trend"
    return "ranging"
