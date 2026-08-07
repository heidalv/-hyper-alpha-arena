"""
QAA Architecture Health Monitoring API
提供 QAA v3.0 架构运行状态查询端点
"""

import asyncio
from fastapi import APIRouter, HTTPException
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/qaa", tags=["QAA"])


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
        return {
            "status": "unavailable",
            "message": "QAA v3.0 not initialized (QAA_V3_ENABLED=false or QAA_MODE!=qaa)",
        }

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
        raise HTTPException(status_code=503, detail="QAA v3.0 not initialized")

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
        raise HTTPException(status_code=503, detail="QAA v3.0 not initialized")

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
        raise HTTPException(status_code=503, detail="QAA v3.0 not initialized")

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
