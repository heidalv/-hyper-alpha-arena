"""
学习进化系统健康与 Wisdom 生命周期统计 API。

对齐 v6 8.3 阶段1-1（静默→告警）与 9.2 前端设计：
- GET /api/learning/health       → 真实闭环健康（learning_health_service.build_learning_health）
- GET /api/learning/wisdom/stats → Wisdom 生命周期五步真实计数聚合（提取/闸门/注入/验证/淘汰）

设计原则：只读、只聚合真实数据源，无任何伪造；指标为 0 就如实返回 0。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learning", tags=["Learning Health"])


# ═══════════════════════════════════════════════════════════
#  GET /api/learning/health — 真实闭环健康
# ═══════════════════════════════════════════════════════════

@router.get("/health")
async def learning_health() -> Dict[str, Any]:
    """学习进化各闭环健康状态（6 类数据 + 5 条 LearningLoop 闭环）。

    语义：ok ≤ 阈值；warn ≤ 2×阈值；超过即 dead（超时标红）。
    取代已废弃的假健康接口（原 /api/learning/dashboard/health 仅做 import 检查）。
    """
    try:
        from backend.services.learning_health_service import build_learning_health
        return build_learning_health()
    except Exception as exc:  # pragma: no cover
        logger.warning("[learning/health] build failed: %s", exc)
        return {
            "overall": "dead",
            "checked_at": None,
            "items": [{"name": "learning_health", "label": "健康检查", "status": "dead", "detail": str(exc)}],
        }


# ═══════════════════════════════════════════════════════════
#  GET /api/learning/wisdom/stats — Wisdom 生命周期五步计数
# ═══════════════════════════════════════════════════════════

def _extract_stats() -> Dict[str, Any]:
    """步骤1 提取：agent_decision_wisdom（hermes_evolution.db）。"""
    try:
        from backend.services.hermes_db import get_hermes_conn

        # [2026-08-19 修复] get_hermes_conn 返回的是进程内共享连接——
        # 这里 close 会毒化整个进程的 hermes 通道（后续所有调用报
        # "Cannot operate on a closed database"，提取/闸门计数归零、
        # 平仓沉淀写入全部失败）。共享连接由进程生命周期管理，绝不 close。
        conn = get_hermes_conn()
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM agent_decision_wisdom")
        total = int(cur.fetchone()[0] or 0)
        cur.execute(
            "SELECT outcome, count(*) FROM agent_decision_wisdom GROUP BY outcome"
        )
        by_outcome = {str(r[0]): int(r[1]) for r in cur.fetchall()}
        cur.execute("SELECT max(created_at) FROM agent_decision_wisdom")
        latest = cur.fetchone()[0]
        return {"total": total, "by_outcome": by_outcome, "latest": latest}
    except Exception as exc:
        logger.warning("[wisdom/stats] extract failed: %s", exc)
        return {"total": 0, "by_outcome": {}, "latest": None, "error": str(exc)}


def _gate_stats() -> Dict[str, Any]:
    """步骤2 质量闸门：proposal_wisdom_records（沉淀即过门）。"""
    try:
        from backend.services.hermes_db import get_hermes_conn

        # [2026-08-19 修复] 同 _extract_stats：共享连接，禁止 close。
        conn = get_hermes_conn()
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM proposal_wisdom_records")
        total = int(cur.fetchone()[0] or 0)
        cur.execute("SELECT max(created_at) FROM proposal_wisdom_records")
        latest = cur.fetchone()[0]
        return {"total": total, "latest": latest}
    except Exception as exc:
        logger.warning("[wisdom/stats] gate failed: %s", exc)
        return {"total": 0, "latest": None, "error": str(exc)}


def _lifecycle_stats() -> Dict[str, Any]:
    """步骤3/4/5 注入/验证/淘汰：trading_wisdom（主库 alpha_arena）。"""
    try:
        from sqlalchemy import text

        from backend.database.connection import SessionLocal

        db = SessionLocal()
        try:
            row = db.execute(
                text(
                    "SELECT count(*), coalesce(sum(applied_count), 0),"
                    " coalesce(sum(evaluation_count), 0),"
                    " coalesce(sum(quality_hit_count), 0)"
                    " FROM trading_wisdom"
                )
            ).fetchone()
            total, applied, evaluated, quality_hit = (
                (row[0], row[1], row[2], row[3]) if row else (0, 0, 0, 0)
            )
            retired = db.execute(
                text("SELECT count(*) FROM trading_wisdom WHERE is_active = false")
            ).scalar() or 0
            latest = db.execute(
                text("SELECT max(last_updated) FROM trading_wisdom")
            ).scalar()
        finally:
            db.close()
        return {
            "total": int(total),
            "injected": int(applied),
            "validated": int(evaluated),
            "quality_hit": int(quality_hit),
            "retired": int(retired),
            "latest_update": latest,
        }
    except Exception as exc:
        logger.warning("[wisdom/stats] lifecycle failed: %s", exc)
        return {
            "total": 0, "injected": 0, "validated": 0,
            "quality_hit": 0, "retired": 0, "latest_update": None,
            "error": str(exc),
        }


def _retrieval_stats() -> Dict[str, Any]:
    """检索注入载体：RAG trading_wisdom collection 状态（透传，只读）。"""
    try:
        from backend.services.rag_knowledge_service import rag_knowledge_service

        stats = rag_knowledge_service.get_stats()
        collections = stats.get("collections", {})
        tw = collections.get("trading_wisdom", {})
        return {
            "ready": bool(stats.get("ready", False)),
            "embedding_model": stats.get("embedding_model"),
            "total_documents": int(
                sum(c.get("doc_count", 0) for c in collections.values())
            ),
            "trading_wisdom_docs": int(tw.get("doc_count", 0) or 0),
            "trading_wisdom_last_indexed": tw.get("last_indexed"),
        }
    except Exception as exc:
        logger.warning("[wisdom/stats] retrieval failed: %s", exc)
        return {"ready": False, "error": str(exc)}


@router.get("/wisdom/stats")
async def wisdom_stats() -> Dict[str, Any]:
    """Wisdom 生命周期五步真实计数聚合（v6 8.2 / 9.2）。

    返回:
        steps: 五步计数（extract/gate/inject/validate/retire）
        rates: 三率（usage_rate/effect_rate/retire_rate，分母为 0 时为 0）
        slot_budget: 注入 slot 预算（当前未启用，如实返回 0）
        retrieval: RAG 检索注入载体状态（透传 /api/rag/stats 核心字段）
    """
    extract = _extract_stats()
    gate = _gate_stats()
    lifecycle = _lifecycle_stats()
    retrieval = _retrieval_stats()

    extract_total = extract.get("total", 0)
    gate_total = gate.get("total", 0)
    injected = lifecycle.get("injected", 0)
    validated = lifecycle.get("validated", 0)
    quality_hit = lifecycle.get("quality_hit", 0)
    retired = lifecycle.get("retired", 0)
    wisdom_total = lifecycle.get("total", 0)

    usage_rate = round(injected / extract_total, 4) if extract_total else 0.0
    effect_rate = round(quality_hit / validated, 4) if validated else 0.0
    retire_rate = round(retired / wisdom_total, 4) if wisdom_total else 0.0

    return {
        "steps": {
            "extract": extract,
            "gate": gate,
            "inject": {"total": injected, "cumulative_count": injected},
            "validate": {"total": validated, "quality_hit_count": quality_hit},
            "retire": {"total": retired},
        },
        "rates": {
            "usage_rate": usage_rate,
            "effect_rate": effect_rate,
            "retire_rate": retire_rate,
        },
        "slot_budget": {
            "enabled": False,
            "max_slots": 0,
            "used": 0,
            "note": "注入 slot 预算未启用（v6 8.2 阶段2 内容）",
        },
        "retrieval": retrieval,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
