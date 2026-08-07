"""
macro_direction_filter — S8 大方向过滤（L0）

两层过滤：
1. V5 RegimeAgent 市场状态判定 — 极端态禁止开仓，震荡态轻度降置信
2. MultiTimeframeOrchestrator long/mid bias — 逆势 skip 或缩仓
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_COUNTER_TREND_CONF = 0.30
_SAME_DIR_BOOST = 0.10
_RANGING_CONF_PENALTY = -0.05


def _classify_symbol_regime(symbol: str) -> Optional[Any]:
    """
    用 V5 决策核心的 RegimeAgent 对该币种做市场状态判定。

    数据来源：unified_data_pool 统一快照指标（与主策略同源）。
    快照不可用或缺该币种数据时返回 None（增强层缺数据不阻断，
    fail-closed 由下方 mt_orchestrator 主过滤层保证）。
    """
    try:
        from backend.services.decision_core.regime_agent import classify_regime
        from backend.services.unified_data_pool import unified_data_pool

        snapshot = unified_data_pool.get_snapshot()
        if snapshot is None:
            return None

        indicators = getattr(snapshot, "indicators", {}) or {}
        base = (symbol or "").split("/")[0].upper()
        ind = None
        for key in (symbol, f"{base}/USDT", base):
            if key in indicators:
                ind = indicators[key]
                break
        if ind is None:
            for k, v in indicators.items():
                if str(k).split("/")[0].upper() == base:
                    ind = v
                    break
        if not isinstance(ind, dict) or not ind:
            return None

        market_data = {
            "price_change_1h_pct": (ind.get("price_change_1h", 0) or 0) * 100,
            "price_change_24h_pct": (ind.get("price_change_24h", 0) or 0) * 100,
            "volatility_pct": ind.get("volatility", 0) or 0,
        }
        return classify_regime(market_data)
    except Exception as exc:
        logger.debug("[MacroFilter] regime 判定不可用: %s", exc)
        return None


def _bias_to_dir(bias: str) -> str:
    b = (bias or "").lower()
    if b in ("bullish", "long", "long_only"):
        return "bullish"
    if b in ("bearish", "short", "short_only"):
        return "bearish"
    return "neutral"


def evaluate_macro_filter(symbol: str, direction: str) -> Dict[str, Any]:
    """
    评估 AI 方向与 macro 是否一致。

    Returns:
        passed, long_bias, mid_bias, action (allow|half|skip), confidence_adjust
    """
    direction = _bias_to_dir(direction)
    result: Dict[str, Any] = {
        "passed": True,
        "long_bias": "neutral",
        "mid_bias": "neutral",
        "allowed_direction": "both",
        "action": "allow",
        "confidence_adjust": 0.0,
        "reason": "",
        "regime": "unknown",
        "regime_detail": "",
    }

    # ── L0a: V5 RegimeAgent 市场状态（与主策略共用判定逻辑）──
    regime = _classify_symbol_regime(symbol)
    if regime is not None:
        result["regime"] = regime.regime
        result["regime_detail"] = regime.detail
        if not regime.allow_open:
            # 极端行情禁止新开仓（与 V5 unified_gate 行为一致）
            result.update(
                passed=False,
                action="skip",
                reason=f"regime_extreme:{regime.detail}",
            )
            return result
        if regime.regime == "ranging":
            # 震荡市轻度降置信（噪声多、方向胜率差），不直接 skip
            result["confidence_adjust"] += _RANGING_CONF_PENALTY

    try:
        from backend.services.multi_timeframe_orchestrator import mt_orchestrator

        # 数据快照/编排器内部均以裸符号为键（如 "ASTER"），
        # 而 S8 的 AI 选币返回 "ASTER/USDT" 格式——必须归一化，
        # 否则查不到指标数据，宏观方向永远是 neutral
        base_symbol = (symbol or "").split("/")[0].split(":")[0].upper()
        decision = mt_orchestrator.evaluate(base_symbol or symbol)
        long_bias = _bias_to_dir(getattr(decision.long_view, "bias", "") or decision.long_view.signal)
        mid_bias = _bias_to_dir(getattr(decision.mid_view, "bias", "") or decision.mid_view.signal)
        allowed = (decision.allowed_direction or "both").lower()
        long_conf = float(getattr(decision.long_view, "confidence", 0) or 0)
        mid_conf = float(getattr(decision.mid_view, "confidence", 0) or 0)

        result["long_bias"] = long_bias
        result["mid_bias"] = mid_bias
        result["allowed_direction"] = allowed

        macro_dir = long_bias if long_conf >= mid_conf else mid_bias
        if macro_dir == "neutral":
            return result

        if direction == "neutral":
            return result

        counter = (
            (direction == "bullish" and macro_dir == "bearish")
            or (direction == "bearish" and macro_dir == "bullish")
        )
        macro_strength = max(long_conf, mid_conf)

        if allowed == "long_only" and direction == "bearish":
            result.update(passed=False, action="skip", reason="macro_long_only_blocks_short")
            return result
        if allowed == "short_only" and direction == "bullish":
            result.update(passed=False, action="skip", reason="macro_short_only_blocks_long")
            return result

        if counter and macro_strength >= _COUNTER_TREND_CONF:
            result.update(
                passed=False,
                action="skip",
                reason=f"counter_trend macro={macro_dir} ai={direction} conf={macro_strength:.2f}",
            )
            return result

        if direction == macro_dir:
            # 累加而非覆盖（保留 ranging 震荡惩罚）
            result["confidence_adjust"] += _SAME_DIR_BOOST
            result["reason"] = "macro_aligned"
        elif counter:
            result["action"] = "half"
            result["confidence_adjust"] += -0.15
            result["reason"] = "weak_counter_trend"
    except Exception as exc:
        # fail-closed：宏观数据不可用时跳过本轮，不再静默放行（旧版 fail-open 已修复）
        logger.warning("[MacroFilter] 数据不可用，本轮 fail-closed skip: %s", exc)
        result.update(
            passed=False,
            action="skip",
            reason=f"filter_unavailable:{exc}",
        )
    return result
