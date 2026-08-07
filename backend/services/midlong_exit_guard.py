"""MidLongExitGuard — 中长线主动退出（阶段二 B2）。

问题
====
中长线仓位大量靠 `max_hold_timeout`（mid 48h / long 7d）被动了结：论点早已破坏
（趋势反转），却还要扛到超时才平，白白让浮盈回吐或浮亏放大。

方案
====
复用多周期编排器**已经算好**的 bias（写在 `market_summary[sym]["orchestrator"]`，
含 long_bias/mid_bias + 对应 confidence），当持仓方向与对应周期 bias 强烈反向、且
持仓已过最短保护时间时，主动平仓（reason=trend_invalidation / swing_invalidation）。
不引入任何额外重计算，纯旁路、flag 门控、默认仅模拟盘。

- 长线(trend_follow/position) 看 long_bias；中线(swing) 看 mid_bias。
- 仅"强反向"（confidence ≥ MIDLONG_EXIT_INVALIDATE_CONF）触发平仓；neutral/同向不动。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_BIAS_TO_DIR = {"bullish": "long", "bearish": "short"}


@dataclass
class ExitDecision:
    action: str = "hold"        # hold / close
    reason: str = ""
    bias: str = ""
    confidence: float = 0.0


def _pos_direction(side: Any) -> str:
    s = str(side or "").lower()
    if s in ("long", "buy", "b"):
        return "long"
    if s in ("short", "sell", "s"):
        return "short"
    return ""


def _tier_of(position: Dict[str, Any]) -> str:
    tier = str(position.get("timeframe_tier") or "").lower()
    if tier in ("mid", "long"):
        return tier
    nature = str(position.get("trade_nature") or "").lower()
    if nature in ("trend_follow", "position"):
        return "long"
    if nature == "swing":
        return "mid"
    return ""


def _held_seconds(position: Dict[str, Any]) -> float:
    """尽力估算持仓时长（秒）；拿不到开仓时间时返回一个大值（不因未知而拦截退出）。"""
    for key in ("opened_at", "created_at", "entry_time", "open_time"):
        val = position.get(key)
        if not val:
            continue
        try:
            if isinstance(val, (int, float)):
                return max(0.0, time.time() - float(val))
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
        except Exception:
            continue
    return 1e9


def evaluate_midlong_exit(
    position: Dict[str, Any],
    market_data: Optional[Dict[str, Any]],
) -> ExitDecision:
    """判断某个中长线持仓是否因多周期 bias 强烈反向而应主动平仓。"""
    try:
        from backend.config.settings import (
            MIDLONG_ACTIVE_EXIT_ENABLED,
            MIDLONG_EXIT_INVALIDATE_CONF,
            MIDLONG_EXIT_MIN_HOLD_SEC,
        )
    except Exception:
        return ExitDecision()

    if not MIDLONG_ACTIVE_EXIT_ENABLED:
        return ExitDecision()

    tier = _tier_of(position)
    if tier not in ("mid", "long"):
        return ExitDecision()

    pos_dir = _pos_direction(position.get("side"))
    if not pos_dir:
        return ExitDecision()

    orch = None
    if isinstance(market_data, dict):
        orch = market_data.get("orchestrator")
        if not isinstance(orch, dict):
            orch = None
    if not orch:
        return ExitDecision()

    if tier == "long":
        bias = str(orch.get("long_bias") or "neutral").lower()
        conf = float(orch.get("long_confidence") or 0.0)
        reason_tag = "trend_invalidation"
    else:
        bias = str(orch.get("mid_bias") or "neutral").lower()
        conf = float(orch.get("mid_confidence") or 0.0)
        reason_tag = "swing_invalidation"

    bias_dir = _BIAS_TO_DIR.get(bias)
    # 只有出现"强反向"信号才考虑主动平仓
    if not bias_dir or bias_dir == pos_dir:
        return ExitDecision(bias=bias, confidence=conf)

    if conf < float(MIDLONG_EXIT_INVALIDATE_CONF):
        return ExitDecision(bias=bias, confidence=conf)

    # 刚开的仓给一段保护期，避免被反向噪声立刻打出。
    # [Phase F] 对 long 仓位必须与 TIER_LONG_MIN_HOLD_SEC (默认 72h) 取大值,
    # 否则会在长线最短持仓保护期内提前触发 bias-reversal 平仓, 与
    # TIER_PROTECTION_PARAMS["long"]["min_hold_sec"]=72h 的承诺冲突。
    # mid 仓位仍用 MIDLONG_EXIT_MIN_HOLD_SEC (默认 1h) 作为下限。
    _effective_min_hold = float(MIDLONG_EXIT_MIN_HOLD_SEC)
    if tier == "long":
        try:
            from backend.config.settings import TIER_PROTECTION_PARAMS as _TPP
            _long_min_hold = int(_TPP.get("long", {}).get("min_hold_sec", 0) or 0)
            if _long_min_hold > 0:
                _effective_min_hold = max(_effective_min_hold, float(_long_min_hold))
        except Exception:
            pass  # settings 读不到时回退到 MIDLONG_EXIT_MIN_HOLD_SEC

    if _held_seconds(position) < _effective_min_hold:
        return ExitDecision(bias=bias, confidence=conf)

    return ExitDecision(
        action="close",
        reason=f"[{reason_tag}] {tier} 持仓={pos_dir} 与 {tier}_bias={bias}(conf={conf:.0%}) 强反向",
        bias=bias,
        confidence=conf,
    )
