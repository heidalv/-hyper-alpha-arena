# -*- coding: utf-8 -*-
"""E5 / 挑战者研究桶放量骨架（p3-promotion）。

过 `promotion_ready` → 可写入 capital_allocator 阶梯 small(5%)；
**不实现真下单**（live() 仍拒绝，除非显式 E5_RESEARCH_LIVE=true 且非 freeze）。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _env_true(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def scan_and_promote(*, auto_promote: bool = False) -> Dict[str, Any]:
    from backend.services.allocation.capital_allocator import (
        promote_strategy, promotion_frozen, research_notional_for,
    )
    from backend.services.strategies.event.base import get_strategy, registered_strategies

    freeze = promotion_frozen()
    out: Dict[str, Any] = {
        "freeze": freeze, "auto_promote": auto_promote,
        "strategies": [], "promoted": [],
    }
    if freeze.get("frozen"):
        out["ok"] = False
        out["reason"] = "promotion_freeze"
        return out

    for sid in registered_strategies():
        strat = get_strategy(sid)
        if strat is None:
            continue
        try:
            kpi = strat.kpi()
        except Exception as exc:
            out["strategies"].append({"strategy": sid, "error": str(exc)[:160]})
            continue
        row = {
            "strategy": sid,
            "promotion_ready": bool(kpi.get("promotion_ready")),
            "promotion_reason": kpi.get("promotion_reason"),
            "n_scored": kpi.get("n_scored"),
            "net_lower_bp": kpi.get("net_lower_bp"),
            "notional": research_notional_for(sid),
        }
        out["strategies"].append(row)
        if row["promotion_ready"] and (auto_promote or _env_true("E5_AUTO_PROMOTE", False)):
            res = promote_strategy(sid)
            row["promote_result"] = res
            if res.get("ok"):
                out["promoted"].append(sid)

    out["ok"] = True
    out["live_enabled"] = _env_true("E5_RESEARCH_LIVE", False)
    out["note"] = "过门只进研究桶阶梯；E5_RESEARCH_LIVE 默认 false，live() 仍拒真下单"
    return out


def try_research_live(strategy_id: str) -> Dict[str, Any]:
    """受控入口：检查门与开关后调用 strategy.live()（多数仍 NotImplemented）。"""
    from backend.services.allocation.capital_allocator import promotion_frozen, research_notional_for
    from backend.services.strategies.event.base import get_strategy

    if not _env_true("E5_RESEARCH_LIVE", False):
        return {"ok": False, "reason": "E5_RESEARCH_LIVE=false"}
    fr = promotion_frozen()
    if fr.get("frozen"):
        return {"ok": False, "reason": "promotion_freeze", "freeze": fr}
    notion = research_notional_for(strategy_id)
    if notion.get("notional_usd", 0) <= 0:
        return {"ok": False, "reason": "研究桶名义为 0", "notional": notion}
    strat = get_strategy(strategy_id)
    if strat is None:
        return {"ok": False, "reason": f"未知策略 {strategy_id}"}
    kpi = strat.kpi()
    if not kpi.get("promotion_ready"):
        return {"ok": False, "reason": kpi.get("promotion_reason"), "kpi": kpi}
    try:
        return {"ok": True, "result": strat.live(notional_usd=notion["notional_usd"])}
    except NotImplementedError as exc:
        return {"ok": False, "reason": str(exc), "notional": notion, "kpi": kpi}
