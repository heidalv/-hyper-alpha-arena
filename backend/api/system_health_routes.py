"""
系统健康观测 API（深挖第 3 轮 2026-05-08）

提供三组只读端点，让 UI/运维快速看清"系统真实状态"：

1. /api/system-health/llm-cost-ranking
   按 caller 分组的 LLM token / 估算成本排行（依托新增的
   call_type=sync:<module>:<func> / async:<module>:<func>）。

2. /api/system-health/risk-events
   risk_control_events 表中各类 guard 拦截事件汇总，可按 event_type
   或 guard_name 过滤。

3. /api/system-health/session-summary
   FullAutoSession 健康摘要：当前活跃数 / legacy 占位数 / 24h 决策快照
   / 24h AI 决策日志 / 24h 拦截事件总数。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from backend.database.connection import get_analytics_db as get_db
from backend.database.connection import get_db as get_core_db
from backend.database.models import (
    LLMUsageLog, RiskControlEvent, FullAutoSession,
    DecisionSnapshot, AIDecisionLog,
)

router = APIRouter(prefix="/api/system-health", tags=["system-health"])


# ──────────────────────────────────────────────────
@router.get("/llm-cost-ranking")
def llm_cost_ranking(
    hours: int = Query(24, ge=1, le=168, description="时间窗口（小时）"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """按 caller (call_type) 分组统计 LLM 调用次数 / token / 估算成本。

    依赖 llm_config_service 的 caller 自动追踪（深挖第 2 轮）。
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (
        db.query(
            LLMUsageLog.call_type,
            LLMUsageLog.model,
            func.count(LLMUsageLog.id).label("calls"),
            func.coalesce(func.sum(LLMUsageLog.total_tokens), 0).label("tokens"),
            func.coalesce(func.sum(LLMUsageLog.estimated_cost_usd), 0).label("cost"),
            func.coalesce(func.avg(LLMUsageLog.duration_ms), 0).label("avg_ms"),
        )
        .filter(LLMUsageLog.created_at >= cutoff)
        .group_by(LLMUsageLog.call_type, LLMUsageLog.model)
        .order_by(func.count(LLMUsageLog.id).desc())
        .limit(limit)
        .all()
    )
    items = [
        {
            "call_type": r.call_type or "unknown",
            "model": r.model or "unknown",
            "calls": int(r.calls),
            "tokens": int(r.tokens or 0),
            "cost_usd": round(float(r.cost or 0), 4),
            "avg_duration_ms": round(float(r.avg_ms or 0), 1),
        }
        for r in rows
    ]
    total_calls = sum(it["calls"] for it in items)
    total_cost = round(sum(it["cost_usd"] for it in items), 4)
    return {
        "window_hours": hours,
        "total_calls": total_calls,
        "total_cost_usd": total_cost,
        "items": items,
    }


# ──────────────────────────────────────────────────
@router.get("/risk-events")
def risk_events(
    hours: int = Query(24, ge=1, le=168),
    event_type: Optional[str] = Query(None, description="如 unified_blocked / guard_blocked"),
    guard_name: Optional[str] = Query(None, description="按 guard 名称过滤（来自 details JSON）"),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """风控/守卫拦截事件汇总。

    1. 顶部按 event_type 分组的 24h 计数；
    2. 按 details.guard_name 拆分；
    3. 最近若干条事件原始记录（用于 UI 列表展示）。
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    q_base = db.query(RiskControlEvent).filter(
        RiskControlEvent.created_at >= cutoff
    )
    if event_type:
        q_base = q_base.filter(RiskControlEvent.event_type == event_type)
    if guard_name:
        # details 是 Text 存的 JSON；用 LIKE 兜底
        q_base = q_base.filter(
            RiskControlEvent.details.contains(f'"guard_name": "{guard_name}"')
        )

    by_event_type = (
        db.query(
            RiskControlEvent.event_type,
            func.count(RiskControlEvent.id).label("n"),
        )
        .filter(RiskControlEvent.created_at >= cutoff)
        .group_by(RiskControlEvent.event_type)
        .order_by(func.count(RiskControlEvent.id).desc())
        .all()
    )
    type_counts = [{"event_type": r.event_type, "count": int(r.n)} for r in by_event_type]

    # 按 details JSON 中的 guard_name 拆分（用 SQL 字符串提取，简易实现）
    guard_counts: List[Dict[str, Any]] = []
    try:
        sql = text(
            """
            SELECT
              CASE
                WHEN instr(details, '"guard_name"') > 0
                  THEN substr(
                    details,
                    instr(details, '"guard_name"') + length('"guard_name"') + 3,
                    50
                  )
                ELSE 'n/a'
              END AS gn_raw,
              COUNT(*) AS n
            FROM risk_control_events
            WHERE created_at >= :cutoff
            GROUP BY gn_raw
            ORDER BY n DESC
            LIMIT 30
            """
        )
        rs = db.execute(sql, {"cutoff": cutoff.replace(tzinfo=None)}).fetchall()
        for row in rs:
            raw = (row[0] or "").strip()
            # raw 形如 fee_guard"} ……，取前面引号之间内容
            if raw.startswith('"'):
                raw = raw[1:]
            quote_end = raw.find('"')
            gn = raw[:quote_end] if quote_end > 0 else raw
            guard_counts.append({"guard_name": gn or "n/a", "count": int(row[1])})
    except Exception:
        guard_counts = []

    recent = (
        q_base.order_by(RiskControlEvent.created_at.desc())
        .limit(limit)
        .all()
    )
    items = [
        {
            "id": e.id,
            "account_id": e.account_id,
            "event_type": e.event_type,
            "details": e.details,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in recent
    ]
    return {
        "window_hours": hours,
        "type_counts": type_counts,
        "guard_counts": guard_counts,
        "recent_events": items,
    }


# ──────────────────────────────────────────────────
@router.get("/session-summary")
def session_summary(
    db: Session = Depends(get_db),
    core_db: Session = Depends(get_core_db),
) -> Dict[str, Any]:
    """FullAutoSession 健康摘要。

    FullAutoSession 属于 core DB (Base)，其余指标属于 analytics DB。
    """
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(hours=24)

    # FullAutoSession 在 core DB (alpha_arena.db)
    by_status = (
        core_db.query(FullAutoSession.status, func.count(FullAutoSession.id))
        .group_by(FullAutoSession.status).all()
    )
    status_counts = {st: int(n) for st, n in by_status}

    # DecisionSnapshot / AIDecisionLog / RiskControlEvent 在 analytics DB
    snap_24h = (
        db.query(func.count(DecisionSnapshot.id))
        .filter(DecisionSnapshot.timestamp >= yesterday).scalar() or 0
    )
    decision_log_24h = (
        db.query(func.count(AIDecisionLog.id))
        .filter(AIDecisionLog.created_at >= yesterday).scalar() or 0
    )
    risk_events_24h = (
        db.query(func.count(RiskControlEvent.id))
        .filter(RiskControlEvent.created_at >= yesterday).scalar() or 0
    )

    active_total = sum(
        n for st, n in by_status
        if st in ("running", "defensive", "paused")
    )
    legacy_total = status_counts.get("legacy", 0)

    return {
        "as_of": now.isoformat(),
        "active_sessions": int(active_total),
        "legacy_sessions": int(legacy_total),
        "by_status": status_counts,
        "decision_snapshots_24h": int(snap_24h),
        "ai_decision_logs_24h": int(decision_log_24h),
        "risk_control_events_24h": int(risk_events_24h),
        "ai_running_hint": (
            "AI 正在自动交易" if active_total > 0
            else "⚠️ 当前没有任何活跃全自动会话，AI 不会自动开仓"
        ),
    }
