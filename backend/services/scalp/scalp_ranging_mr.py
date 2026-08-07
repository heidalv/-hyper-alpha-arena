"""ScalpRangingMR — 震荡均值回归（高抛低吸）打法（2026-07-09）。

独立于现有"趋势跟随"短线链路：仅当 regime==ranging 且振幅落在
[SCALP_MR_MIN_RANGE_PCT, SCALP_MR_MAX_RANGE_PCT] 区间时，由 full_auto 循环
调用本模块接管选点。趋势市完全不进入这里。

核心思想（与趋势打法相反）：
- 趋势打法追突破、亏小赚大、大止盈；震荡里必然频繁被止损、目标够不到。
- 本模式在区间【低位+超卖】做多、【高位+超买】做空，贴着区间边缘设小止盈小止损，
  吃"回归到区间中枢"的那一段薄利，靠胜率而非盈亏比赚钱。

设计约束：
- 不拆任何下游安全网（手续费闸/EV闸/置信度/冷却/风险闸照旧生效）。本模式只保证
  "振幅足够宽 + 止盈盖过手续费"，把 min_tp/min_rr 交给 unified_gate 用 MR 专用阈值判。
- 完全自包含：只依赖 StructureStopCalculator.swing_levels（复用现成 48×5m 区间）与
  factor_engine.compute_rsi，不引入新的重型计算。
- 返回 ScalpSignal，与 scalp_factor_router.evaluate 输出同构，便于 full_auto 无缝替换。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import pandas as pd

from backend.config.settings import (
    SCALP_MR_HIGH_BAND,
    SCALP_MR_LOW_BAND,
    SCALP_MR_MAX_RANGE_PCT,
    SCALP_MR_MIN_RANGE_PCT,
    SCALP_MR_MIN_RR,
    SCALP_MR_MIN_TP,
    SCALP_MR_RSI_OB,
    SCALP_MR_RSI_OS,
    SCALP_MR_TP_RANGE_FRAC,
)
from backend.services.scalp_factor_router import ScalpSignal
from backend.services.scalp.structure_stop_calculator import StructureStopCalculator

logger = logging.getLogger(__name__)

# 止损贴在区间边沿【外侧】的缓冲（价格%），防止刚好插针到边沿就被扫。
# [2026-07-31 crypto-native] 0.2%→0.5%：加密 5m 噪音带 0.5-1%，0.2% 缓冲等于没缓冲。
# [2026-07-31 research] 0.5%→0.8%：永续常见「结构位外侧再加缓冲」；HYPE 等 alt 插针更狠。
_EDGE_BUFFER_PCT = 0.008
# 止损硬下限（价格%）。
# [2026-07-31 crypto-native] 0.4%→0.8%：5m crypto ATR 0.5-1%，SL 0.3-0.4% 100%被扫。
# [2026-07-31 research] 0.8%→1.2%：近7天26% scalp SL贴≤0.85%；行业 RR≥1.5 且禁固定过紧%；
#   典型币合约 taker 往返≈0.08–0.12%，0.8%/1.0% 盈亏比仅1.25，盈亏平衡胜率≈50%偏脆。
_MR_SL_FLOOR = 0.012
# 止损硬上限：MR 单不该扛过大止损，否则退化成趋势单的大亏。
# [2026-07-31] 1.2%→1.5%→2.0%：给 SL 空间同时仍可控（单笔风险靠仓位，不靠把 SL 压进噪音）。
_MR_SL_CAP = 0.020

_calc = StructureStopCalculator()


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def range_amplitude_pct(market_data: Dict[str, Any]) -> Optional[float]:
    """估算当前 48×5m 区间振幅（(high-low)/price）。取不到返回 None。"""
    df = market_data.get("klines")
    if df is None:
        return None
    try:
        df = df if isinstance(df, pd.DataFrame) else pd.DataFrame(df)
    except Exception:
        return None
    if df.empty or "low" not in df.columns or "high" not in df.columns:
        return None
    swing_low, swing_high, _ = _calc.swing_levels(df)
    price = float(market_data.get("price") or market_data.get("mark_price") or 0.0)
    if price <= 0:
        price = swing_high  # 兜底
    if price <= 0 or swing_high <= swing_low:
        return None
    return (swing_high - swing_low) / price


def amplitude_in_band(amp: Optional[float]) -> bool:
    """振幅是否落在允许做 MR 的区间。"""
    if amp is None:
        return False
    return SCALP_MR_MIN_RANGE_PCT <= amp <= SCALP_MR_MAX_RANGE_PCT


def evaluate_ranging_mr(symbol: str, market_data: Dict[str, Any]) -> ScalpSignal:
    """震荡均值回归选点。

    返回 ScalpSignal：
    - action: buy(低位超卖) / sell(高位超买) / hold(区间中部或未双确认)
    - confidence/factor_score: 位置越靠边+RSI越极端 → 分越高（50-100）
    - sl_pct/tp_pct: 贴区间边缘的小止盈小止损（价格波动%）
    """
    df = market_data.get("klines")
    try:
        df = df if isinstance(df, pd.DataFrame) else pd.DataFrame(df)
    except Exception:
        return ScalpSignal(action="hold", source="ranging_mr", reasoning="无K线")
    if df is None or df.empty or "close" not in df.columns:
        return ScalpSignal(action="hold", source="ranging_mr", reasoning="K线不足")

    price = float(market_data.get("price") or market_data.get("mark_price") or 0.0)
    if price <= 0:
        try:
            price = float(df["close"].iloc[-1])
        except Exception:
            return ScalpSignal(action="hold", source="ranging_mr", reasoning="无价格")

    swing_low, swing_high, range_pos = _calc.swing_levels(df)
    if swing_high <= swing_low or price <= 0:
        return ScalpSignal(action="hold", source="ranging_mr", reasoning="区间无效")

    amp = (swing_high - swing_low) / price
    if not amplitude_in_band(amp):
        return ScalpSignal(
            action="hold",
            source="ranging_mr",
            reasoning=f"振幅{amp:.3%}不在[{SCALP_MR_MIN_RANGE_PCT:.1%},{SCALP_MR_MAX_RANGE_PCT:.1%}]",
        )

    # RSI（复用因子引擎，保持与全局一致）
    try:
        from backend.services.factor_engine.base_factors import factor_engine
        rsi = float(factor_engine.compute_rsi(df))
    except Exception:
        rsi = 50.0

    # ── 选点：低位+超卖→买；高位+超买→卖；否则观望 ──
    direction = "neutral"
    action = "hold"
    if range_pos <= SCALP_MR_LOW_BAND and rsi <= SCALP_MR_RSI_OS:
        direction, action = "long", "buy"
    elif range_pos >= SCALP_MR_HIGH_BAND and rsi >= SCALP_MR_RSI_OB:
        direction, action = "short", "sell"
    else:
        return ScalpSignal(
            action="hold",
            source="ranging_mr",
            reasoning=f"未达边缘: pos={range_pos:.2f} rsi={rsi:.1f}",
        )

    # ── 打分：位置极端度 + RSI 极端度，映射到 50-100 ──
    if direction == "long":
        pos_extreme = _clip((SCALP_MR_LOW_BAND - range_pos) / max(SCALP_MR_LOW_BAND, 1e-6), 0.0, 1.0)
        rsi_extreme = _clip((SCALP_MR_RSI_OS - rsi) / max(SCALP_MR_RSI_OS, 1e-6), 0.0, 1.0)
    else:
        pos_extreme = _clip((range_pos - SCALP_MR_HIGH_BAND) / max(1.0 - SCALP_MR_HIGH_BAND, 1e-6), 0.0, 1.0)
        rsi_extreme = _clip((rsi - SCALP_MR_RSI_OB) / max(100.0 - SCALP_MR_RSI_OB, 1e-6), 0.0, 1.0)
    combined = 0.5 * pos_extreme + 0.5 * rsi_extreme
    score = int(round(_clip(50.0 + 50.0 * combined, 0.0, 100.0)))

    # ── MR 止盈止损：贴区间边缘 ──
    if direction == "long":
        # 止盈：朝对沿(swing_high)方向吃 TP_RANGE_FRAC 段
        dist_to_far = max(swing_high - price, 0.0)
        tp_pct = (dist_to_far * SCALP_MR_TP_RANGE_FRAC) / price
        # 止损：贴当前低沿(swing_low)外一点
        sl_pct = (price - swing_low) / price + _EDGE_BUFFER_PCT
    else:
        dist_to_far = max(price - swing_low, 0.0)
        tp_pct = (dist_to_far * SCALP_MR_TP_RANGE_FRAC) / price
        sl_pct = (swing_high - price) / price + _EDGE_BUFFER_PCT

    # 止盈强制盖过手续费；止损夹在合理区间（防过紧被噪声扫、防过松变趋势大亏）
    tp_pct = max(tp_pct, SCALP_MR_MIN_TP)
    sl_pct = _clip(sl_pct, _MR_SL_FLOOR, _MR_SL_CAP)

    # 自洽盈亏比：贴边缘算出的止损有时略大于止盈(rr<1)，会被下游 min_rr 冤杀。
    # 优先收紧止损（不低于硬下限）；若仍不足则【抬止盈】到 sl×MIN_RR，
    # 不再把结构性 RR 倒挂丢给 V5（2026-08-02：ONDO/HYPE 大量 TP0.9/SL1.2 冤杀）。
    if SCALP_MR_MIN_RR > 0:
        _sl_cap_for_rr = tp_pct / SCALP_MR_MIN_RR
        if _sl_cap_for_rr >= _MR_SL_FLOOR:
            sl_pct = min(sl_pct, _sl_cap_for_rr)
        elif sl_pct > 0 and (tp_pct / sl_pct) < SCALP_MR_MIN_RR:
            tp_pct = min(0.04, max(tp_pct, sl_pct * SCALP_MR_MIN_RR))

    if direction == "long":
        sl_price = price * (1.0 - sl_pct)
        tp_price = price * (1.0 + tp_pct)
    else:
        sl_price = price * (1.0 + sl_pct)
        tp_price = price * (1.0 - tp_pct)

    rr = tp_pct / sl_pct if sl_pct > 0 else 0.0
    reasoning = (
        f"[ScalpMR] {direction} pos={range_pos:.2f} rsi={rsi:.1f} amp={amp:.2%} "
        f"tp={tp_pct:.3%} sl={sl_pct:.3%} rr={rr:.2f} score={score}"
    )
    logger.info(f"[ScalpMR] {symbol} {reasoning}")

    return ScalpSignal(
        action=action,
        confidence=score,
        factor_score=score,
        direction=direction,
        entry_price=price,
        sl_price=sl_price,
        tp_price=tp_price,
        sl_pct=sl_pct,
        tp_pct=tp_pct,
        factor_breakdown={
            "range_position": round(range_pos, 4),
            "rsi": round(rsi, 2),
            "amplitude_pct": round(amp, 4),
            "pos_extreme": round(pos_extreme, 3),
            "rsi_extreme": round(rsi_extreme, 3),
        },
        source="ranging_mr",
        reasoning=reasoning,
    )
