"""Rebate 平仓 outcome → QAA 进化桥接。"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def record_rebate_close_outcome(
    position: Any,
    reason: str = "",
    *,
    pnl: Optional[float] = None,
) -> Dict[str, Any]:
    """
    平仓后写入 QAA PerformanceTracker（best-effort，不阻塞主流程）。
    """
    sid = ""
    try:
        sid = getattr(position.strategy_type, "value", str(position.strategy_type))
    except Exception:
        sid = str(getattr(position, "strategy_type", ""))

    pnl_val = pnl
    if pnl_val is None:
        pnl_val = float(getattr(position, "current_pnl", 0) or 0) + float(
            getattr(position, "accumulated_rebate", 0) or 0
        )

    outcome = {
        "action": "close",
        "strategy": sid,
        "strategy_id": sid,
        "status": "completed" if pnl_val >= 0 else "failure",
        "details": f"reason={reason} pnl={pnl_val:.2f}",
        "pnl": pnl_val,
    }

    try:
        from backend.services.qaa_evolution_bridge import qaa_bridge

        if hasattr(qaa_bridge, "record_trade_outcome"):
            qaa_bridge.record_trade_outcome(
                {
                    "symbol": getattr(position, "symbol", ""),
                    "strategy_id": sid,
                    "domain": "rebate_arb",
                    "pnl": pnl_val,
                    "reason": reason,
                }
            )
    except Exception as exc:
        logger.debug("[RebateOutcomeBridge] qaa_bridge skip: %s", exc)

    try:
        from qaa.domains.rebate_arb.reflection import RebateArbReflectionStrategy

        lesson = RebateArbReflectionStrategy().extract_lesson(outcome)
        if lesson:
            logger.info("[RebateOutcomeBridge] %s lesson: %s", sid, lesson[:120])
    except Exception:
        pass

    return outcome
