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
