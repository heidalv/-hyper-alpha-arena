"""ReversalSignalPack — 趋势反转信号包（L1 规则感知层）。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

from backend.services.trend_health_score import TrendHealthResult


@dataclass
class ReversalSignalResult:
    level: str
    short_tf_flip: bool
    mid_tf_weaken: bool
    long_tf_intact: bool
    urgency: int
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _dir(value: str) -> int:
    v = str(value or "").lower()
    if v in ("bullish", "long", "up", "buy"):
        return 1
    if v in ("bearish", "short", "down", "sell"):
        return -1
    return 0


def _side(side: str) -> int:
    return 1 if str(side).lower() in ("long", "buy") else -1


class ReversalSignalPackBuilder:
    """把多周期冲突、动能衰减、结构破位整理成 AI 可读的反转等级。"""

    def evaluate(
        self,
        *,
        symbol: str,
        side: str,
        trade_nature: str,
        market_env: Optional[Dict[str, Any]] = None,
        health: Optional[TrendHealthResult] = None,
    ) -> ReversalSignalResult:
        env = market_env or {}
        orchestrator = env.get("orchestrator") or {}
        indicators = env.get("indicators") or env.get("technical_indicators") or env
        side_dir = _side(side)
        evidence: list[str] = []

        short_dir = _dir(orchestrator.get("short_bias") or orchestrator.get("short_side"))
        mid_dir = _dir(orchestrator.get("mid_bias") or orchestrator.get("mid_side"))
        long_dir = _dir(orchestrator.get("long_bias") or orchestrator.get("long_side") or orchestrator.get("final_side"))

        short_tf_flip = short_dir not in (0, side_dir)
        if short_tf_flip:
            evidence.append("短周期方向已反向")

        mid_tf_weaken = mid_dir not in (0, side_dir)
        if mid_tf_weaken:
            evidence.append("中周期趋势减弱或反向")

        long_tf_intact = long_dir in (0, side_dir)
        if not long_tf_intact:
            evidence.append("长周期方向与持仓相反")

        macd_hist = _f(indicators.get("macd_hist") or indicators.get("macd_histogram"))
        macd_prev = _f(indicators.get("macd_hist_prev") or indicators.get("macd_histogram_prev"))
        if macd_prev and abs(macd_hist) < abs(macd_prev) and macd_prev * side_dir > 0:
            evidence.append("MACD动能柱衰减")
        if macd_hist and macd_hist * side_dir < 0:
            evidence.append("MACD动能已反向")

        price = _f(env.get("price") or env.get("last_price") or indicators.get("last_price") or indicators.get("close"))
        swing_low = _f(indicators.get("swing_low") or indicators.get("recent_low"))
        swing_high = _f(indicators.get("swing_high") or indicators.get("recent_high"))
        structure_broken = False
        if price > 0:
            if side_dir > 0 and swing_low > 0 and price < swing_low:
                structure_broken = True
                evidence.append("价格跌破最近结构低点")
            elif side_dir < 0 and swing_high > 0 and price > swing_high:
                structure_broken = True
                evidence.append("价格突破最近结构高点")

        health_score = float(health.score) if health else 50.0
        if health and health.regime in ("reversal_risk", "broken"):
            evidence.append(f"趋势健康状态={health.regime}")

        urgency = 0
        urgency += 20 if short_tf_flip else 0
        urgency += 25 if mid_tf_weaken else 0
        urgency += 25 if not long_tf_intact else 0
        urgency += 20 if structure_broken else 0
        urgency += max(0, int((50 - health_score) * 0.6))
        urgency = max(0, min(100, urgency))

        if (not long_tf_intact and mid_tf_weaken) or (structure_broken and mid_tf_weaken):
            level = "confirmed_reversal"
        elif mid_tf_weaken or structure_broken or (health and health.regime == "reversal_risk"):
            level = "structure_warning"
        elif short_tf_flip and long_tf_intact:
            level = "pullback"
        else:
            level = "none"

        return ReversalSignalResult(
            level=level,
            short_tf_flip=short_tf_flip,
            mid_tf_weaken=mid_tf_weaken,
            long_tf_intact=long_tf_intact,
            urgency=urgency,
            evidence=evidence[:8],
        )


_BUILDER = ReversalSignalPackBuilder()


def get_reversal_signal_builder() -> ReversalSignalPackBuilder:
    return _BUILDER
