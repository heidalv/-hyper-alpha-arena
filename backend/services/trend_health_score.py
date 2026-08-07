"""TrendHealthScore — 持仓趋势健康分（L1 规则感知层）。

该模块只做低成本、可解释的风险感知，不直接下单。
健康分低代表“必须复审”，不能单独触发强制减仓。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass
class TrendHealthResult:
    score: float
    regime: str
    components: Dict[str, float]
    aligned_with_position: bool
    nature_adjusted_threshold: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _side_direction(side: str) -> int:
    return 1 if str(side).lower() in ("long", "buy") else -1


def _bias_direction(bias: str) -> int:
    b = str(bias or "").lower()
    if b in ("bullish", "long", "up", "buy"):
        return 1
    if b in ("bearish", "short", "down", "sell"):
        return -1
    return 0


class TrendHealthScorer:
    """根据多周期方向、趋势强度、动能衰减和结构破位计算 0-100 健康分。"""

    def evaluate(
        self,
        *,
        symbol: str,
        side: str,
        trade_nature: str,
        market_env: Optional[Dict[str, Any]] = None,
    ) -> TrendHealthResult:
        from backend.config.settings import NATURE_HEALTH_PROFILES

        env = market_env or {}
        profile = NATURE_HEALTH_PROFILES.get(trade_nature or "swing", NATURE_HEALTH_PROFILES["swing"])
        threshold = float(profile.get("review_threshold", 40.0))

        orchestrator = env.get("orchestrator") or {}
        indicators = env.get("indicators") or env.get("technical_indicators") or env
        side_dir = _side_direction(side)

        long_bias = orchestrator.get("long_bias") or orchestrator.get("final_side") or env.get("trend")
        mid_bias = orchestrator.get("mid_bias")
        short_bias = orchestrator.get("short_bias")
        long_dir = _bias_direction(long_bias)
        mid_dir = _bias_direction(mid_bias)
        short_dir = _bias_direction(short_bias)

        long_conf = _f(orchestrator.get("long_conf") or orchestrator.get("long_confidence"), 50.0)
        mid_conf = _f(orchestrator.get("mid_conf") or orchestrator.get("mid_confidence"), 50.0)
        short_conf = _f(orchestrator.get("short_conf") or orchestrator.get("short_confidence"), 50.0)

        alignment_votes = [
            1.0 if long_dir == side_dir else 0.0 if long_dir == 0 else -1.0,
            1.0 if mid_dir == side_dir else 0.0 if mid_dir == 0 else -1.0,
            0.5 if short_dir == side_dir else 0.0 if short_dir == 0 else -0.5,
        ]
        weighted_alignment = (
            alignment_votes[0] * long_conf * 0.50
            + alignment_votes[1] * mid_conf * 0.30
            + alignment_votes[2] * short_conf * 0.20
        ) / 50.0
        trend_alignment = max(0.0, min(1.0, (weighted_alignment + 1.0) / 2.0))

        adx = max(
            _f(indicators.get("adx_1d")),
            _f(indicators.get("adx_4h")),
            _f(indicators.get("adx")),
        )
        adx_score = max(0.0, min(1.0, adx / 35.0)) if adx else 0.50
        ema_slope = (
            _f(indicators.get("ema_slope_1d"))
            or _f(indicators.get("ema_slope_4h"))
            or _f(indicators.get("ema_slope"))
        )
        ema_score = 0.50
        if ema_slope:
            ema_score = 1.0 if ema_slope * side_dir > 0 else 0.0
        trend_strength = (trend_alignment * 0.55 + adx_score * 0.30 + ema_score * 0.15) * 100.0

        macd_hist = _f(indicators.get("macd_hist") or indicators.get("macd_histogram"))
        macd_hist_prev = _f(indicators.get("macd_hist_prev") or indicators.get("macd_histogram_prev"))
        momentum = 55.0
        if macd_hist or macd_hist_prev:
            same_side = macd_hist * side_dir > 0
            decaying = abs(macd_hist) < abs(macd_hist_prev) and macd_hist_prev * side_dir > 0
            if same_side and not decaying:
                momentum = 80.0
            elif same_side and decaying:
                momentum = 55.0
            elif decaying or macd_hist * side_dir < 0:
                momentum = 25.0

        structure_break = 50.0
        price = _f(env.get("price") or env.get("last_price") or indicators.get("last_price") or indicators.get("close"))
        swing_low = _f(indicators.get("swing_low") or indicators.get("recent_low"))
        swing_high = _f(indicators.get("swing_high") or indicators.get("recent_high"))
        if price > 0:
            if side_dir > 0 and swing_low > 0:
                structure_break = 20.0 if price < swing_low else 80.0
            elif side_dir < 0 and swing_high > 0:
                structure_break = 20.0 if price > swing_high else 80.0

        score = trend_strength * 0.40 + momentum * 0.30 + structure_break * 0.30
        aligned = long_dir in (0, side_dir)
        if not aligned:
            score = min(score, 60.0)

        if score >= 70:
            regime = "strong_trend"
        elif score >= threshold:
            regime = "weakening"
        elif score >= max(20.0, threshold - 15.0):
            regime = "reversal_risk"
        else:
            regime = "broken"

        return TrendHealthResult(
            score=round(max(0.0, min(100.0, score)), 2),
            regime=regime,
            components={
                "trend_strength": round(trend_strength, 2),
                "momentum_decay": round(momentum, 2),
                "structure_break": round(structure_break, 2),
            },
            aligned_with_position=aligned,
            nature_adjusted_threshold=threshold,
        )


_SCORER = TrendHealthScorer()


def get_trend_health_scorer() -> TrendHealthScorer:
    return _SCORER
