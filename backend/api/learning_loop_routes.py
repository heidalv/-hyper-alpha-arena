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


def _valid_jobs() -> set:
    """合法 job 名 —— **从服务层的别名表取**（唯一来源）。

    [2026-10-10 前端升级] 此前这里是硬编码的 4 个名字，而服务层实际支持 7 个
    （`factor_decay` / `live_outcome_backfill` / `hft_brain` 不在其中）⇒
    前端/运维手册里承诺的手动触发一律 400。两处各自维护必然再次漂移，
    故改为派生。
    """
    try:
        from backend.services.learning_loop_service import JOB_TRIGGER_ALIASES
        return set(JOB_TRIGGER_ALIASES)
    except Exception:  # pragma: no cover - 服务层导入失败时保守放行核心 4 个
        return {"outcome_batch", "paper_outcome_backfill", "kelly_portfolio",
                "coordinator"}


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
        job: 见 `LearningLoopService.JOB_TRIGGER_ALIASES`
             （outcome_batch / paper_outcome_backfill / kelly_portfolio /
              coordinator / factor_decay / live_outcome_backfill / hft_brain）

    Returns:
        { "ok": bool, "job": str, "elapsed_ms": int, "extra": {...}, "error"?: str }
        `extra` 是该 tick 的 `_record_tick` 摘要（例如高频入口的 ingested/cursor），
        前端据此告诉操作者"这次到底推进了多少"。
    """
    valid = _valid_jobs()
    if job not in valid:
        raise HTTPException(
            status_code=400,
            detail=f"unknown job: {job}. valid: {sorted(valid)}",
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