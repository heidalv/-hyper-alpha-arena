"""
Learning Loop API Routes — P2-2

暴露 LearningLoopService 的状态 / 指标 / 暂停 / 恢复 / 手动触发能力。
前端 AILearningCenter / 运维脚本使用，方便在不进入后端容器的情况下观察和
干预 AI 自动学习闭环。

所有端点均为纯查询或运行时覆盖，不会修改磁盘配置。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learning/loop", tags=["Learning Loop"])

# 合法的 job 名与 LearningLoopService.trigger_job 中的 mapping 保持一致
_VALID_JOBS = {"outcome_batch", "paper_outcome_backfill", "kelly_portfolio", "coordinator"}


def _get_service():
    """延迟导入，避免循环依赖 & 启动期未就绪时抛 500。"""
    from backend.services.learning_loop_service import learning_loop
    return learning_loop


@router.get("/status")
async def loop_status() -> Dict[str, Any]:
    """返回学习闭环整体状态（是否启用 / 暂停 / 三个 tick 的 last/next 时间等）。"""
    try:
        return _get_service().status()
    except Exception as exc:  # pragma: no cover
        logger.warning("[learning/loop] status failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/metrics")
async def loop_metrics() -> Dict[str, Any]:
    """返回三个 tick 的运行指标（count / success_rate / p50/p95 耗时 / 最后一次 extra）。"""
    try:
        return _get_service().metrics()
    except Exception as exc:  # pragma: no cover
        logger.warning("[learning/loop] metrics failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/pause")
async def loop_pause() -> Dict[str, Any]:
    """暂停所有 tick（运行时生效，重启后回到 enabled 状态）。"""
    svc = _get_service()
    svc.pause()
    return {"ok": True, "paused": svc.is_paused}


@router.post("/resume")
async def loop_resume() -> Dict[str, Any]:
    """从暂停状态恢复。"""
    svc = _get_service()
    svc.resume()
    return {"ok": True, "paused": svc.is_paused}


@router.post("/trigger/{job}")
async def loop_trigger(job: str) -> Dict[str, Any]:
    """手动同步触发某个 tick。

    Args:
        job: outcome_batch / paper_outcome_backfill / kelly_portfolio / coordinator

    Returns:
        { "ok": bool, "elapsed_ms": int, "error"?: str }
    """
    if job not in _VALID_JOBS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown job: {job}. valid: {sorted(_VALID_JOBS)}",
        )
    try:
        return _get_service().trigger_job(job)
    except Exception as exc:  # pragma: no cover
        logger.error("[learning/loop] trigger %s failed: %s", job, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ══════════════════════════════════════════════════════════════════
#  学习闭环健康检测 — 已迁移至 P3 仪表盘 /api/learning/dashboard/health
#  原 /api/learning/health 路由及 health_router 已移除
# ══════════════════════════════════════════════════════════════════