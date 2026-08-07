"""
全自动分析上下文 — 统一策略记忆注入（v3 / unified 共用）
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def enrich_positions_with_strategy_meta(
    positions_list: List[Dict[str, Any]],
    strat_meta_cache: Dict[str, Dict[str, Any]],
) -> None:
    """DB 持仓字段优先；仅缺失时用策略 genome 补全。"""
    for p in positions_list:
        sid = p.get("strategy_id")
        meta = strat_meta_cache.get(sid, {}) if sid else {}
        if not (p.get("trade_nature") or "").strip():
            p["trade_nature"] = meta.get("trade_nature") or "swing"
        if not (p.get("timeframe_tier") or "").strip():
            p["timeframe_tier"] = meta.get("timeframe_tier") or "mid"


def build_strategy_meta_cache(db, strategy_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    cache: Dict[str, Dict[str, Any]] = {}
    if not strategy_ids:
        return cache
    try:
        from backend.database.models import AIStrategy

        rows = db.query(AIStrategy).filter(AIStrategy.strategy_id.in_(strategy_ids)).all()
        for s in rows:
            genome = s.genome or {}
            cache[s.strategy_id] = {
                "timeframe_tier": getattr(s, "timeframe_tier", None) or "mid",
                "trade_nature": genome.get("trade_nature") or "swing",
            }
    except Exception as exc:
        logger.debug("[StrategyAnalysisContext] meta cache skip: %s", exc)
    return cache


def build_strategies_for_analysis(
    db,
    active_strategy_ids: List[str],
) -> List[Dict[str, Any]]:
    """从 StrategyMemory 构建完整分析 payload（unified / v3 共用）。"""
    if not active_strategy_ids:
        return []
    try:
        from backend.database.models import AIStrategy, StrategyMemory as SM

        strats = db.query(AIStrategy).filter(
            AIStrategy.strategy_id.in_(list(active_strategy_ids))
        ).all()
        mem_rows = db.query(SM).filter(SM.strategy_id.in_(list(active_strategy_ids))).all()
        mem_map = {m.strategy_id: m for m in mem_rows}
    except Exception as exc:
        logger.warning("[StrategyAnalysisContext] 策略记忆查询失败: %s", exc)
        return []

    out: List[Dict[str, Any]] = []
    for strat in strats:
        mem = mem_map.get(strat.strategy_id)
        genome = strat.genome or {}
        total = (mem.total_trades or 0) if mem else 0
        wr = round((mem.win_rate or 0) * 100, 1) if mem else 0.0
        avg_profit = (mem.avg_profit or 0) if mem else 0
        avg_loss = (mem.avg_loss or 0) if mem else 0
        est_pnl = round(
            avg_profit * total * ((mem.win_rate or 0))
            + avg_loss * total * (1 - (mem.win_rate or 0)),
            2,
        ) if mem and total > 0 else 0
        out.append({
            "strategy_id": strat.strategy_id,
            "name": strat.name or "",
            "primary_symbol": strat.primary_symbol or "",
            "status": strat.status or "",
            "tier": getattr(strat, "timeframe_tier", "mid") or "mid",
            "trade_nature": genome.get("trade_nature", "swing"),
            "total_trades": total,
            "win_rate": wr,
            "total_pnl": est_pnl,
            "avg_profit": avg_profit,
            "avg_loss": avg_loss,
            "max_drawdown": (mem.max_drawdown or 0) if mem else 0,
            "sharpe_ratio": (mem.sharpe_ratio or 0) if mem else 0,
            "performance_by_regime": (mem.performance_by_regime or {}) if mem else {},
            "successful_patterns": (mem.successful_patterns or []) if mem else [],
            "failed_patterns": (mem.failed_patterns or []) if mem else [],
            "key_lessons": (mem.key_lessons or []) if mem else [],
        })
    return out
