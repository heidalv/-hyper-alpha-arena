"""
volume_program_executor — S2/S4 刷量程序执行（Maker 循环模拟）。
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def build_volume_side_a(
    exchange: str,
    symbol: str,
    size_usd: float,
    *,
    prefer_limit: float = 0.7,
) -> Dict[str, Any]:
    """将 volume program 计划转为标准 side_a 腿。"""
    use_limit = prefer_limit >= 0.5
    return {
        "exchange": exchange,
        "symbol": symbol,
        "side": "buy",
        "type": "limit" if use_limit else "market",
        "size_usd": size_usd,
    }


def normalize_volume_plan(plan: Dict[str, Any], size_usd: float) -> Dict[str, Any]:
    """S2/S4 规划型 plan → 引擎可执行的 side_a 格式。"""
    strategy = (plan.get("strategy") or "").upper()
    exchange = plan.get("exchange") or "okx"
    symbol = plan.get("symbol") or "ETH/USDT:USDT"
    mix = plan.get("order_mix") or {}
    prefer_limit = float(mix.get("limit", 0.7))

    side_a = build_volume_side_a(exchange, symbol, size_usd, prefer_limit=prefer_limit)
    normalized = dict(plan)
    normalized["side_a"] = side_a
    normalized["side_b"] = None
    normalized["volume_program"] = True
    normalized["strategy"] = strategy

    if strategy == "S2":
        normalized["hold_phase"] = {
            "total_seconds": 3600,
            "reason": "vip_sprint_round",
        }
        normalized["close_plan"] = {
            "exchange": exchange,
            "symbol": symbol,
            "side": "sell",
            "type": "limit" if prefer_limit >= 0.5 else "market",
            "size_usd": size_usd,
        }
    elif strategy == "S4":
        normalized["hold_phase"] = {
            "total_seconds": 7200,
            "reason": "campaign_round",
        }
        normalized["close_plan"] = {
            "exchange": exchange,
            "symbol": symbol,
            "side": "sell",
            "type": "market",
            "size_usd": size_usd,
        }
    return normalized


def execute_volume_program_tick(
    engine: Any,
    strategy_type: str,
    size_usd: float,
    plan: Dict[str, Any],
    *,
    paper_mode: bool = True,
) -> Dict[str, Any]:
    """单次刷量 round：normalize → 走引擎 paper execute。"""
    normalized = normalize_volume_plan(plan, size_usd)
    position_id = f"rebate_{strategy_type}_{uuid.uuid4().hex[:8]}"
    try:
        from backend.services.rebate_arb.models import (
            RebatePosition,
            RebatePositionStatus,
            RebateStrategyType,
        )

        side_a = normalized.get("side_a") or {}
        position = RebatePosition(
            position_id=position_id,
            strategy_type=RebateStrategyType(strategy_type),
            source_exchange=side_a.get("exchange", "okx"),
            symbol=side_a.get("symbol", ""),
            side_a_size=size_usd,
            entry_time=time.time(),
            status=RebatePositionStatus.ACTIVE,
            paper_mode=paper_mode,
            metadata=normalized,
        )
        order_results = engine._execute_orders(position, normalized, paper_mode=paper_mode)
        hold = normalized.get("hold_phase")
        if hold and isinstance(position.metadata, dict):
            position.metadata["hold_target_time"] = time.time() + hold.get("total_seconds", 3600)
            position.metadata["close_plan"] = normalized.get("close_plan")
        engine._persist_position(position, order_results or {})
        return {
            "ok": True,
            "position_id": position_id,
            "strategy": strategy_type,
            "orders": order_results,
        }
    except Exception as exc:
        logger.warning("[VolumeProgram] execute failed: %s", exc)
        return {"ok": False, "error": str(exc)}
