"""
strategy_coordinator — 跨 S1–S8 多目标选策略与互斥组。

供 QAA rebate_strategy_coordinator handler 与 ExecutionAuthority 共用。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .qaa_strategy_constants import COORDINATION_GROUPS, MUTEX_GROUPS

logger = logging.getLogger(__name__)


def _score_opportunity(opp: Dict[str, Any], wash_headroom: float = 1.0) -> float:
    monthly = float(opp.get("expected_monthly_value", 0) or 0)
    risk = float(opp.get("risk_score", 0.5) or 0.5)
    conf = float(opp.get("confidence", 0.5) or 0.5)
    monthly_norm = min(monthly / 200.0, 1.0)
    direction_risk = risk if opp.get("strategy_type") in ("S5", "S8") else risk * 0.5
    return (
        0.5 * monthly_norm
        + 0.2 * conf
        + 0.2 * wash_headroom
        - 0.1 * direction_risk
    )


def _apply_mutex(selected: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同互斥组只保留得分最高的一条。"""
    by_group: Dict[str, Dict[str, Any]] = {}
    non_mutex: List[Dict[str, Any]] = []

    for opp in selected:
        sid = (opp.get("strategy_type") or opp.get("strategy_id") or "").upper()
        group = COORDINATION_GROUPS.get(sid, "")
        if group in MUTEX_GROUPS:
            prev = by_group.get(group)
            if prev is None or opp.get("_coord_score", 0) > prev.get("_coord_score", 0):
                by_group[group] = opp
        else:
            non_mutex.append(opp)

    out = non_mutex + list(by_group.values())
    out.sort(key=lambda x: x.get("_coord_score", 0), reverse=True)
    return out


def rank_and_filter(
    opportunities: List[Dict[str, Any]],
    *,
    enabled_strategies: Optional[List[str]] = None,
    active_strategy_ids: Optional[List[str]] = None,
    wash_headroom: float = 1.0,
    account_equity: float = 0.0,
) -> Dict[str, Any]:
    """
    多目标评分 + 互斥过滤，返回首选策略与执行队列。
    """
    enabled = {s.upper() for s in (enabled_strategies or [])} if enabled_strategies else None
    active = {s.upper() for s in (active_strategy_ids or [])}

    viable = [
        o for o in opportunities
        if isinstance(o, dict) and o.get("is_viable", False)
    ]
    if enabled:
        viable = [
            o for o in viable
            if (o.get("strategy_type") or "").upper() in enabled
        ]

    # Asterdex hedge vs directional 冲突：有 S8 活跃则跳过 S1/S6
    if "S8" in active:
        viable = [o for o in viable if (o.get("strategy_type") or "").upper() not in ("S1", "S6")]
    if active & {"S1", "S6"}:
        viable = [o for o in viable if (o.get("strategy_type") or "").upper() != "S8"]

    for opp in viable:
        opp["_coord_score"] = _score_opportunity(opp, wash_headroom)

    ranked = _apply_mutex(viable)
    top = ranked[0] if ranked else None

    size_usd = 0.0
    if top and account_equity > 0:
        sid = (top.get("strategy_type") or top.get("strategy_id") or "").upper()
        if sid == "S8":
            try:
                from backend.services.rebate_arb.strategies.s8_asterdex_rh import S8AsterdexRhStrategy
                from backend.services.rebate_arb.capital_coordinator import capital_coordinator

                paper_id = capital_coordinator.get_arbitrage_paper_account_id()
                resolved = S8AsterdexRhStrategy.resolve_target_margin(
                    account_equity=account_equity,
                    paper_account_id=paper_id,
                    exchange="asterdex",
                )
                size_usd = float(resolved.get("margin_usd") or 0)
            except Exception as exc:
                logger.debug("[StrategyCoordinator] S8 margin resolve skip: %s", exc)
                size_usd = max(account_equity * 0.30, 30.0)
        else:
            size_usd = min(account_equity * 0.15, float(top.get("required_volume_usd", 0) or 0) * 0.1)
            size_usd = max(size_usd, 30.0 if account_equity >= 100 else 0.0)
        try:
            from backend.services.rebate_arb.capital_coordinator import capital_coordinator

            pool_avail = capital_coordinator.get_rebate_available()
            sub_avail = capital_coordinator.get_strategy_sub_available(sid)
            cap = min(pool_avail, sub_avail) if sub_avail > 0 else pool_avail
            if cap > 0:
                if cap < 30:
                    size_usd = 0.0
                elif size_usd > cap:
                    size_usd = cap
        except Exception as exc:
            logger.debug("[StrategyCoordinator] size cap skip: %s", exc)

    return {
        "ok": bool(top),
        "ranked": ranked,
        "top": top,
        "strategy_id": (top or {}).get("strategy_type", ""),
        "size_usd": round(size_usd, 2),
        "coordination_groups": COORDINATION_GROUPS,
        "reasoning": f"ranked {len(ranked)} viable after mutex",
    }
