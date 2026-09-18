"""period_reports_routes — 双车道报告可观测 API（2026-08-19 建立，轮63 重构）。

    GET /api/period/reports/daily   近 N 天日报列表 + 单日明细（含亏损归因 + 周期身份）
    GET /api/period/reports/weekly  最新周报（内存缓存）
    GET /api/period/cycles          TrendCycle 趋势周期列表 + R 分布统计
    GET /api/period/lanes           车道词表（前端渲染标签/周期身份的唯一来源）

## 轮63 改了什么

- `lane` 成为主查询参数；`horizon` 保留为**兼容别名**，旧枚举值（scalp/midlong/long）
  会被解析成规范车道（scalp→intraday、midlong→intraday、long→trend）。
  旧前端传 `horizon=long` 仍然能查到长线趋势，不会因为改名而变空白。
- 响应里回传 `lane_identity`（tier/nature/主看周期/期望持仓/实测持仓），
  前端不必再靠外层 tab 猜「这一行是哪个周期」。
- 无 `lane` 过滤时**两条车道都返回**（旧版默认只返回全部 horizon 的混合列表，
  前端再各自渲染，行与行之间没有身份标识）。
- 单日 limit 从 `days*3` 改为 `days*len(REPORT_LANES)` —— 车道数变化时不再截断。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from backend.config import lane_semantics as lane_sem

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


def _resolve_lane_or_none(raw: Optional[str]) -> Optional[str]:
    """任意 lane/horizon/中文写法 → 规范车道；无法识别或未传返回 None（= 不过滤）。"""
    if raw is None or str(raw).strip() == "":
        return None
    return lane_sem.lane_for_label(raw)


@router.get("/lanes")
def get_lanes():
    """车道词表 —— 前端标签与周期身份的唯一来源（避免前端再维护一份映射）。"""
    return {
        "lanes": [
            {
                **lane_sem.LANE_SPECS[lane].identity(),
                "hold_bracket_label": lane_sem.hold_bracket_label(lane),
            }
            for lane in lane_sem.REPORT_LANES
        ],
        # 旧枚举 → 新车道，前端可据此把历史缓存/深链参数翻译过来
        "legacy_horizon_map": dict(lane_sem.LEGACY_HORIZON_TO_LANE),
    }


@router.get("/reports/daily")
def get_daily_reports(
    session_id: Optional[str] = Query(None),
    account_id: Optional[int] = Query(None),
    days: int = Query(7, ge=1, le=90),
    lane: Optional[str] = Query(None, description="intraday / trend / research（也接受旧 horizon 值）"),
    horizon: Optional[str] = Query(None, description="[兼容别名] 等同 lane"),
):
    from backend.database.connection import SessionLocal
    from backend.database.models import PeriodDailyReport

    wanted = _resolve_lane_or_none(lane) or _resolve_lane_or_none(horizon)

    db = SessionLocal()
    try:
        acct = _resolve_account(db, session_id, account_id)
        if acct is None:
            return {"error": "无活跃会话"}
        q = db.query(PeriodDailyReport).filter(PeriodDailyReport.account_id == acct)
        if wanted:
            # `lane` 列在迁移落地前为 NULL → 同时比 horizon（旧行的车道信息在那一列）
            from sqlalchemy import or_
            q = q.filter(or_(PeriodDailyReport.lane == wanted,
                             PeriodDailyReport.horizon == wanted,
                             # 旧枚举行：lane 已回填则命中上面，未回填则用旧值等价映射
                             PeriodDailyReport.horizon.in_(
                                 [k for k, v in lane_sem.LEGACY_HORIZON_TO_LANE.items()
                                  if v == wanted] or [wanted]
                             )))
        rows = (q.order_by(PeriodDailyReport.report_date.desc(), PeriodDailyReport.lane)
                .limit(days * max(len(lane_sem.REPORT_LANES), 3)).all())
        items: List[Dict[str, Any]] = []
        for r in rows:
            try:
                payload = json.loads(r.payload_json) if r.payload_json else {}
            except Exception:
                payload = {}
            row_lane = _resolve_lane_or_none(getattr(r, "lane", None)) \
                or _resolve_lane_or_none(getattr(r, "horizon", None)) \
                or lane_sem.DEFAULT_LANE
            if wanted and row_lane != wanted:
                continue
            items.append({
                "date": r.report_date,
                "lane": row_lane,
                "horizon": row_lane,  # 兼容别名
                "lane_identity": lane_sem.lane_identity(row_lane),
                "payload": payload,
                "llm_summary": r.llm_summary,
            })
        return {
            "account_id": acct,
            "lanes": list(lane_sem.REPORT_LANES),
            "lane_filter": wanted,
            "reports": items,
        }
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
        return {
            "account_id": acct,
            "lane": lane_sem.LANE_TREND,
            "lane_identity": lane_sem.lane_identity(lane_sem.LANE_TREND),
            "stats": stats_out,
            "cycles": items,
        }
    finally:
        db.close()
