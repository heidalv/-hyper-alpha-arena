# -*- coding: utf-8 -*-
"""定时任务登记 / 心跳 / 告警 运维接口（/api/ops/jobs*、/api/ops/alerts*，v3 方向 6）。

  GET  /api/ops/jobs                    全部任务（job_registry ∪ APScheduler）：cadence、上次/下次、时长、失败次数、心跳、负责人、陈旧
  GET  /api/ops/jobs/{name}             单任务详情 + 最近运行记录
  POST /api/ops/jobs/{name}/run         立即运行一次（仅 v3 登记且带 runner 的任务）
  POST /api/ops/jobs/{name}/enable      启用 / POST .../disable 禁用（禁用后调度器仍触发但被包装器跳过）
  GET  /api/ops/alerts/status           通道配置 / 统计 / 最近 20 条
  POST /api/ops/alerts/test             向所有通道发一条测试告警 {level?, text?}
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ops", tags=["ops-jobs"])


@router.get("/jobs")
def ops_jobs() -> Dict[str, Any]:
    from backend.services.ops.job_registry import list_jobs
    jobs = list_jobs()
    summary = {
        "total": len(jobs),
        "registered": sum(1 for j in jobs if not j.get("scheduler_only")),
        "scheduler_only": sum(1 for j in jobs if j.get("scheduler_only")),
        "stale_warn": sum(1 for j in jobs if j.get("stale") == "warn"),
        "stale_critical": sum(1 for j in jobs if j.get("stale") == "critical"),
        "failing": sum(1 for j in jobs if (j.get("consecutive_failures") or 0) > 0),
        "disabled": sum(1 for j in jobs if j.get("enabled") is False),
    }
    return {"summary": summary, "jobs": jobs}


@router.get("/jobs/{name}")
def ops_job_detail(name: str, limit: int = 50) -> Dict[str, Any]:
    from backend.services.ops.job_registry import list_jobs, job_runs
    job = next((j for j in list_jobs() if j.get("name") == name or j.get("scheduler_id") == name), None)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {name} not found")
    return {"job": job, "runs": job_runs(job["name"], limit=limit)}


@router.post("/jobs/{name}/run")
def ops_job_run(name: str) -> Dict[str, Any]:
    from backend.services.ops.job_registry import trigger
    try:
        out = trigger(name)
    except Exception as exc:
        logger.exception("[ops/jobs] 手动运行 %s 失败", name)
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("error", "cannot trigger"))
    return out


@router.post("/jobs/{name}/enable")
def ops_job_enable(name: str) -> Dict[str, Any]:
    from backend.services.ops.job_registry import set_enabled
    return {"name": name, "enabled": True, "ok": set_enabled(name, True)}


@router.post("/jobs/{name}/disable")
def ops_job_disable(name: str) -> Dict[str, Any]:
    from backend.services.ops.job_registry import set_enabled
    return {"name": name, "enabled": False, "ok": set_enabled(name, False)}


@router.get("/cost-monitor")
def ops_cost_monitor(days: int = 7) -> Dict[str, Any]:
    """[2026-09-23 D7] 车道成本三列监控（只读）：已实现 / 手续费 / 资金费 → 净，附 top5 持仓与告警。

    口径：窗口内已平仓仓（closed_at ≥ now()-days）：已实现 = unrealized_pnl（P0-6 权威口径）+ partial_realized_pnl；
    手续费 = partial_fee_paid + coalesce(final_fee_paid,0)（历史仓 final_fee_paid 为 NULL ⇒ 低估，见报告 §1.4）；
    资金费 = position_funding_events 该仓 funding_paid − funding_received。未平仓仓的资金费按事件表实时累计单列。
    告警：窗口内某 lane 每日资金费中位 > 0.03%/名义 → WARN，> 0.05% → CRITICAL（只回报，不发交易信号）。
    """
    from sqlalchemy import text as _t

    from backend.database.connection import SessionLocal

    db = SessionLocal()
    try:
        # [2026-09-23] RLS 穿透：position_funding_events / paper_positions 的租户隔离需要系统身份，
        # 否则资金费 JOIN 恒为 NULL（实测 funding 全 0）。
        db.execute(_t("SET app.is_admin='on'"))
        rows = db.execute(_t(
            """SELECT COALESCE(p.timeframe_tier,'?') lane,
                      count(*) n,
                      round(SUM(COALESCE(p.unrealized_pnl,0)+COALESCE(p.partial_realized_pnl,0))::numeric,2) realized,
                      round(SUM(COALESCE(p.partial_fee_paid,0)+COALESCE(p.final_fee_paid,0))::numeric,2) fees,
                      round(COALESCE(SUM(f.funding),0)::numeric,2) funding
               FROM paper_positions p
               LEFT JOIN (SELECT position_id,
                                 SUM(amount_usd) funding
                          FROM position_funding_events GROUP BY position_id) f
                 ON f.position_id = p.id
               WHERE p.account_id = 14 AND p.status IN ('closed','liquidated')
                 AND p.closed_at >= now() - (:d || ' days')::interval
               GROUP BY 1 ORDER BY realized DESC"""),
            {"d": int(days)}).mappings().all()
        lanes = {}
        for r in rows:
            lanes[str(r["lane"])] = {"n": int(r["n"]), "realized": float(r["realized"]),
                                     "fees": float(r["fees"]), "funding": float(r["funding"]),
                                     "net": round(float(r["realized"]) - float(r["fees"]) - float(r["funding"]), 2)}
        top = db.execute(_t(
            """SELECT p.id, p.symbol, p.timeframe_tier, round((COALESCE(p.funding_paid,0)-COALESCE(p.funding_received,0)
                      + COALESCE(p.partial_fee_paid,0)+COALESCE(p.final_fee_paid,0))::numeric,2) cost
               FROM paper_positions p WHERE p.account_id = 14 AND p.status = 'closed'
                 AND p.closed_at >= now() - (:d || ' days')::interval
               ORDER BY cost DESC LIMIT 5"""),
            {"d": int(days)}).mappings().all()
        open_funding = float(db.execute(_t(
            """SELECT COALESCE(SUM(e.amount_usd),0) FROM position_funding_events e
               JOIN paper_positions p ON p.id = e.position_id
               WHERE p.account_id = 14 AND p.status = 'open'""")).scalar() or 0)
        alerts = []
        for lane, v in lanes.items():
            if v["funding"] > 0 and v["n"] >= 3:
                per_day = v["funding"] / int(days)
                if per_day > 0.05 * 456:
                    alerts.append({"lane": lane, "level": "CRITICAL",
                                   "msg": "每日资金费估算 $%.2f 超 CRITICAL 阈值（0.05%%×$456）" % per_day})
                elif per_day > 0.03 * 456:
                    alerts.append({"lane": lane, "level": "WARN",
                                   "msg": "每日资金费估算 $%.2f 超 WARN 阈值（0.03%%×$456）" % per_day})
        return {"days": int(days), "lanes": lanes, "top5_cost_positions":
                [{"id": r["id"], "symbol": r["symbol"], "tier": r["timeframe_tier"],
                  "cost_usd": float(r["cost"])} for r in top],
                "open_funding_usd": round(open_funding, 2),
                "alerts": alerts,
                "note": "资金费口径=8h 网格回填（position_funding_events）；历史仓 final_fee_paid 为 NULL ⇒ 手续费低估"}
    finally:
        db.close()


@router.get("/alerts/status")
def ops_alerts_status() -> Dict[str, Any]:
    from backend.services.ops.alerts import alerts_status
    return alerts_status()


@router.post("/alerts/test")
def ops_alerts_test(body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    from backend.services.ops.alerts import send_alert, channels_configured
    level = str(body.get("level") or "P2").upper()
    res = send_alert(level, "告警通道测试", str(body.get("text") or "这是一条来自 /api/ops/alerts/test 的测试告警"),
                     dedupe_key=None, source="ops_test", async_send=False)
    return {"result": res, "channels": channels_configured()}
