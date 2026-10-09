"""
QAA Architecture Health Monitoring API
提供 QAA v3.0 架构运行状态查询端点
"""

import asyncio
from fastapi import APIRouter, HTTPException
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/qaa", tags=["QAA"])

#: [2026-09-19 退役] QAA v3 卡片编排层**已正式退役**（决策见 docs/ADR_QAA退役_20260919.md）。
#: 事实：`QAA_MODE=ai_first` + `QAA_V3_ENABLED=false` + `QAA_FULLAUTO_SCHEDULE_ENABLED=false`；
#: `register_qaa_agents` 无调用点（`full_auto_trading_service.py:5618` 自注"路由断裂"）；
#: 20 个日志文件 `[EventBus][QAA]`/`[QAAScheduler]` 0 命中；`agent_predictions` 无 9 张卡片名。
#: 替代者：`unified_loop_ai_first`（90s tick）+ 主脑 MLTO（权威 `llm_thesis`）+ 观察型 Agent 群。
#: **为什么不再返回 503**：503 让调用方以为"服务故障、等等就好"，
#: 实际是"能力已下线、永远不会回来"——两者混在一起，每次排查都要重新判定（本轮就为此多花了三份扫描）。
_RETIRED: dict = {
    "status": "retired",
    "since": "2026-09-17",
    "retired_at": "2026-09-19",
    "reason": "QAA v3 多智能体卡片编排层已被 ai_first 统一循环 + 主脑 MLTO + 观察型 Agent 群替代",
    "replacement": {
        "flow": "unified_loop_ai_first（FULLAUTO_FLOW_MODE=ai_first，90s tick）",
        "brain": "MLTO mid/long 主脑（mid_open_authority=llm_thesis）",
        "observers": "/api/agents/status · /api/agents/latest/{id}（6 个观察型 Agent）",
        "task_health": "/api/ops/jobs（job_registry 的 stale 判定）",
        "canvas": "/api/agent-wall/state · /tail · /audit（Agent Wall 画布）",
    },
    "rollback": "见 docs/ADR_QAA退役_20260919.md「回滚步骤」（需同时置 QAA_MODE=qaa 与 QAA_V3_ENABLED=true，并重接 register_qaa_agents）",
    "note": "本响应为**明确退役**，不是故障；/api/qaa/* 的其余端点同样语义。",
}


def _get_qaa_context():
    """获取全局 QAAContext 单例（延迟导入，带超时保护）"""
    try:
        from backend.services.full_auto_trading_service import full_auto_service
        ctx = getattr(full_auto_service, "_qaa_ctx", None)
        return ctx
    except Exception as e:
        logger.debug("[QAA API] Context resolution failed: %s", e)
        return None


@router.get("/health")
async def qaa_health():
    """QAA 架构全局健康状态"""
    ctx = _get_qaa_context()
    if ctx is None:
        # [2026-09-19] 退役语义（不是故障）：见本文件顶部 _RETIRED 与 ADR
        return {**_RETIRED, "message": "QAA v3.0 编排层已退役（QAA_MODE=ai_first, QAA_V3_ENABLED=false）"}

    try:
        # Guard: ctx.summary() may block (e.g. waiting on locks); timeout after 2s
        summary = await asyncio.wait_for(
            asyncio.to_thread(ctx.summary), timeout=2.0
        )
        return {
            "status": "healthy",
            "version": "3.0.0",
            "domains": summary.get("domains", []),
            "registered_agents": summary.get("registry", {}).get("total_cards", 0),
        }
    except asyncio.TimeoutError:
        logger.warning("[QAA API] /health timed out after 2s")
        return {"status": "degraded", "message": "Context summary timed out"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/agents")
async def qaa_agents():
    """已注册 Agent 列表和熔断器状态"""
    ctx = _get_qaa_context()
    if ctx is None:
        return {**_RETIRED, "agents": [], "total": 0}

    try:
        registry = ctx.registry
        cards = registry.get_all_cards()
        agents_info = []
        for agent_id, card in cards.items():
            domain = registry.get_card_domain(agent_id)
            agents_info.append({
                "agent_id": agent_id,
                "domain": domain,
                "capabilities": [c.name for c in card.capabilities],
                "llm_level": card.llm_level.value if hasattr(card.llm_level, "value") else str(card.llm_level),
                "timeout_s": card.timeout_policy.total_timeout_s if card.timeout_policy else 0,
            })
        return {"agents": agents_info, "total": len(agents_info)}
    except Exception as e:
        logger.error(f"[QAA API] /agents error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/context")
async def qaa_context_summary():
    """QAAContext 完整子系统状态"""
    ctx = _get_qaa_context()
    if ctx is None:
        return {**_RETIRED, "domains": [], "registry": {"total_cards": 0}}

    try:
        return ctx.summary()
    except Exception as e:
        logger.error(f"[QAA API] /context error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/latency")
async def qaa_latency():
    """延迟监控数据"""
    ctx = _get_qaa_context()
    if ctx is None:
        return {**_RETIRED, "percentiles": {}}

    try:
        monitor = ctx.latency_monitor
        if monitor is None:
            return {"status": "no_monitor", "percentiles": {}}
        return {
            "status": "active",
            "percentiles": getattr(monitor, "get_percentiles", lambda: {})(),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
