"""period_reports_routes — 三周期报告可观测 API（2026-08-19）。

GET /api/period/reports/daily   近 N 天日报列表 + 单日明细（含亏损归因）
GET /api/period/reports/weekly  最新周报（内存缓存）
GET /api/period/cycles          TrendCycle 趋势周期列表 + R 分布统计
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/period", tags=["period-reports"])


def _resolve_account(db, session_id, account_id):
    """session_id 或 account_id 二选一解析账户；都缺省取第一个活跃会话。"""
    from backend.database.models import FullAutoSession
    if account_id:
        return int(account_id)
    q = db.query(FullAutoSession)
    if session_id:
        q = q.filter(FullAutoSession.session_id == session_id)
    else:
        q = q.filter(FullAutoSession.status.in_(["running", "defensive"]))
    s = q.first()
    if s is None:
        return None
    return int(getattr(s, "paper_account_id", None) or getattr(s, "account_id", None) or 0)


@router.get("/reports/daily")
def get_daily_reports(
    session_id: Optional[str] = Query(None),
    account_id: Optional[int] = Query(None),
    days: int = Query(7, ge=1, le=90),
    horizon: Optional[str] = Query(None),
):
    from backend.database.connection import SessionLocal
    from backend.database.models import PeriodDailyReport

    db = SessionLocal()
    try:
        acct = _resolve_account(db, session_id, account_id)
        if acct is None:
            return {"error": "无活跃会话"}
        q = db.query(PeriodDailyReport).filter(PeriodDailyReport.account_id == acct)
        if horizon:
            q = q.filter(PeriodDailyReport.horizon == horizon)
        rows = q.order_by(PeriodDailyReport.report_date.desc(), PeriodDailyReport.horizon).limit(days * 3).all()
        items = []
        for r in rows:
            try:
                payload = json.loads(r.payload_json) if r.payload_json else {}
            except Exception:
                payload = {}
            items.append({
                "date": r.report_date, "horizon": r.horizon,
                "payload": payload, "llm_summary": r.llm_summary,
            })
        return {"account_id": acct, "reports": items}
    finally:
        db.close()


@router.get("/reports/weekly")
def get_weekly_reports(
    session_id: Optional[str] = Query(None),
    account_id: Optional[int] = Query(None),
    refresh: bool = Query(False),
):
    from backend.database.connection import SessionLocal
    db = SessionLocal()
    try:
        acct = _resolve_account(db, session_id, account_id)
        if acct is None:
            return {"error": "无活跃会话"}
        if refresh:
            from backend.services.period_weekly_report import build_weekly_report
            return build_weekly_report(db, acct)
        from backend.services.period_weekly_report import get_latest_weekly
        latest = get_latest_weekly(acct)
        if latest is None:
            from backend.services.period_weekly_report import build_weekly_report
            latest = build_weekly_report(db, acct)
        return latest
    finally:
        db.close()


@router.get("/cycles")
def get_trend_cycles(
    session_id: Optional[str] = Query(None),
    account_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    import statistics
    from backend.database.connection import SessionLocal
    from backend.database.models import TrendCycle

    db = SessionLocal()
    try:
        acct = _resolve_account(db, session_id, account_id)
        if acct is None:
            return {"error": "无活跃会话"}
        rows = db.query(TrendCycle).filter(TrendCycle.account_id == acct) \
            .order_by(TrendCycle.start_ts.desc()).limit(limit).all()
        items = [{
            "id": r.id, "symbol": r.symbol, "direction": r.direction,
            "start_ts": str(r.start_ts), "end_ts": str(r.end_ts) if r.end_ts else None,
            "l1_score_at_entry": r.l1_score_at_entry,
            "total_r": r.total_r, "peak_r": r.peak_r,
            "exit_reason": r.exit_reason, "hold_days": r.hold_days,
        } for r in rows]
        rs = [float(r.total_r) for r in rows if r.total_r is not None]
        stats_out = {
            "n": len(rows),
            "total_r": round(sum(rs), 2) if rs else 0.0,
            "mean_r": round(statistics.fmean(rs), 3) if rs else 0.0,
            "win_rate": round(sum(1 for x in rs if x > 0) / len(rs), 3) if rs else 0.0,
        }
        return {"account_id": acct, "stats": stats_out, "cycles": items}
    finally:
        db.close()
