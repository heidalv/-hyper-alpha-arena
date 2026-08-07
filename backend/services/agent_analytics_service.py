"""Agent 维度绩效统计 — Swing / Trend 专属看板数据。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

AGENT_NATURE_KEYS = {
    "swing": ("swing",),
    "trend_follow": ("trend_follow", "position"),
}


def build_by_agent_report(db, *, days: int = 30, nature: str = None) -> Dict[str, Any]:
    """构建 by-agent 绩效报告（净扣费口径 + 持仓时长 + scenario 命中率）。"""
    from backend.services.decision_feedback_service import decision_feedback_service

    attribution = decision_feedback_service.build_net_attribution(db, days=days)
    by_nature = attribution.get("by_nature") or {}

    result: Dict[str, Any] = {"days": days, "agents": {}}

    targets = ["swing", "trend_follow"]
    if nature in targets:
        targets = [nature]

    for agent_key in targets:
        keys = AGENT_NATURE_KEYS.get(agent_key, (agent_key,))
        merged = _merge_nature_buckets(by_nature, keys)
        merged["avg_hold_hours"] = _avg_hold_hours(db, keys, days)
        if agent_key == "trend_follow":
            merged["scenario_hit_rate"] = _scenario_hit_rate(days)
        result["agents"][agent_key] = merged

    return result


def _merge_nature_buckets(by_nature: dict, keys: tuple) -> dict:
    out = {
        "trades": 0, "net_pnl": 0.0, "gross_pnl": 0.0, "fees": 0.0,
        "wins": 0, "win_rate": 0.0, "profit_factor": None,
        "avg_win": 0.0, "avg_loss": 0.0,
    }
    win_amount = 0.0
    loss_amount = 0.0
    for k in keys:
        b = by_nature.get(k) or {}
        out["trades"] += int(b.get("trades") or 0)
        out["net_pnl"] += float(b.get("net_pnl") or 0)
        out["gross_pnl"] += float(b.get("gross_pnl") or 0)
        out["fees"] += float(b.get("fees") or 0)
        wins = int(b.get("wins") or 0)
        out["wins"] += wins
        win_amount += float(b.get("win_amount") or 0)
        loss_amount += float(b.get("loss_amount") or 0)
    if out["trades"]:
        out["win_rate"] = round(out["wins"] / out["trades"], 3)
        losses = out["trades"] - out["wins"]
        out["avg_win"] = round(win_amount / out["wins"], 2) if out["wins"] else 0
        out["avg_loss"] = round(loss_amount / losses, 2) if losses else 0
        out["profit_factor"] = (
            round(win_amount / loss_amount, 3) if loss_amount > 0 else None
        )
    for k in ("net_pnl", "gross_pnl", "fees"):
        out[k] = round(out[k], 2)
    return out


def _avg_hold_hours(db, nature_keys: tuple, days: int) -> Optional[float]:
    try:
        from backend.database.models import StrategyTrade
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        rows = (
            db.query(StrategyTrade.holding_period, StrategyTrade.decision_context)
            .filter(
                StrategyTrade.status == "closed",
                StrategyTrade.closed_at >= cutoff,
            )
            .all()
        )
        secs = []
        for hp, ctx in rows:
            nature = ""
            if isinstance(ctx, dict):
                nature = (ctx.get("nature") or "").lower()
            if nature not in nature_keys:
                continue
            if hp:
                secs.append(int(hp))
        if not secs:
            return None
        return round(sum(secs) / len(secs) / 3600, 1)
    except Exception as exc:
        logger.debug("[AgentAnalytics] avg_hold_hours 跳过: %s", exc)
        return None


def _scenario_hit_rate(days: int) -> Optional[float]:
    try:
        from backend.database.connection import AnalyticsSessionLocal
        from backend.services.strategic_analyst.db_models import TrendPredictionRecord

        db = AnalyticsSessionLocal()
        try:
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
            rows = (
                db.query(TrendPredictionRecord.outcome)
                .filter(
                    TrendPredictionRecord.outcome.in_(("hit", "partial", "miss")),
                    TrendPredictionRecord.closed_at >= cutoff,
                )
                .all()
            )
        finally:
            db.close()

        if not rows:
            return None
        hits = sum(1 for (o,) in rows if o == "hit")
        partial = sum(1 for (o,) in rows if o == "partial")
        return round((hits + partial * 0.5) / len(rows), 3)
    except Exception as exc:
        logger.debug("[AgentAnalytics] scenario_hit_rate 跳过: %s", exc)
        return None
