"""
全局学习仪表盘 API — P3.1
提供学习进化系统的全方位监控数据，供前端仪表盘展示。

端点：
- GET /api/learning/dashboard/overview     → 全局概览
- GET /api/learning/dashboard/factors       → 因子状态
- GET /api/learning/dashboard/strategies    → 策略状态
- GET /api/learning/dashboard/evolution     → 进化进度
- GET /api/learning/dashboard/memory        → 策略记忆
- GET /api/learning/dashboard/experiments   → A/B 实验状态
- GET /api/learning/dashboard/transfer      → 跨市场迁移
- GET /api/learning/dashboard/feature-flags → 特性开关
- POST /api/learning/dashboard/feature-flags → 更新开关
"""

from __future__ import annotations

import json
import logging
import os
import time as _time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learning/dashboard", tags=["Learning Dashboard"])


# ── 响应模型 ──

class FeatureFlagUpdate(BaseModel):
    key: str
    value: bool


class LearningOverview(BaseModel):
    factors_loaded: int
    strategies_active: int
    strategies_evolving: int
    strategies_promoted: int
    total_lessons: int
    daily_trades: int
    daily_pnl: float
    evolution_generation: int
    last_evolution_at: Optional[str]
    opencode_sessions_active: int
    system_uptime_hours: float


# ── 共用辅助 ──

def _get_db():
    try:
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        return db
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════
#  GET /overview — 全局概览
# ═══════════════════════════════════════════════════════════

@router.get("/overview", response_model=Dict[str, Any])
async def get_learning_overview():
    """获取学习进化系统全局概览数据。"""
    db = _get_db()
    result: Dict[str, Any] = {
        "factors": {"loaded": 0, "categories": {}},
        "strategies": {"active": 0, "evolving": 0, "promoted": 0},
        "memory": {"total_lessons": 0, "key_themes": []},
        "evolution": {"generation": 0, "last_run": None},
        "opencode": {"sessions_active": 0},
        "uptime_hours": 0,
    }

    try:
        # 因子统计
        try:
            from backend.services.factor_engine.base_factors import factor_engine
            factors = getattr(factor_engine, "factors", [])
            result["factors"]["loaded"] = len(factors)
            cats = {}
            for f in factors:
                cat = getattr(f, "category", "unknown")
                cats[cat] = cats.get(cat, 0) + 1
            result["factors"]["categories"] = cats
        except Exception:
            pass

        # 策略统计
        if db:
            try:
                from backend.database.models import StrategyTemplate
                templates = db.query(StrategyTemplate).all()
                result["strategies"]["active"] = len(templates)
                # 统计不同状态
                evolving = sum(1 for t in templates if getattr(t, "status", "") == "evolving")
                promoted = sum(1 for t in templates if getattr(t, "status", "") == "promoted")
                result["strategies"]["evolving"] = evolving
                result["strategies"]["promoted"] = promoted
            except Exception:
                pass

            # 策略记忆统计
            try:
                from backend.database.models import StrategyMemory
                memories = db.query(StrategyMemory).all()
                total_lessons = sum(len(m.key_lessons or []) for m in memories)
                result["memory"]["total_lessons"] = total_lessons
                # 提取最近的主题
                themes = {}
                for m in memories:
                    for lesson in (m.key_lessons or [])[-5:]:
                        if isinstance(lesson, dict):
                            t = lesson.get("type", "unknown")
                            themes[t] = themes.get(t, 0) + 1
                result["memory"]["key_themes"] = sorted(
                    themes.items(), key=lambda x: x[1], reverse=True
                )[:5]
            except Exception:
                pass

            # 进化进度
            try:
                from backend.services.evolution_scheduler import evolution_scheduler
                if hasattr(evolution_scheduler, "_generation"):
                    result["evolution"]["generation"] = evolution_scheduler._generation
            except Exception:
                pass

            # 最近交易统计
            try:
                from backend.database.models import PaperTradeRecord
                today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                today_trades = (
                    db.query(PaperTradeRecord)
                    .filter(PaperTradeRecord.exit_time >= today)
                    .all()
                )
                result["daily_trades"] = len(today_trades)
                result["daily_pnl"] = round(
                    sum(float(t.pnl or 0) for t in today_trades), 4
                )
            except Exception:
                result["daily_trades"] = 0
                result["daily_pnl"] = 0

        # OpenCode sessions
        try:
            from backend.services.opencode_bridge import _session_count
            if callable(_session_count):
                result["opencode"]["sessions_active"] = _session_count()
        except Exception:
            pass

        # 启动时间
        try:
            from backend.main import _startup_ts
            result["uptime_hours"] = round(
                (_time.time() - _startup_ts) / 3600, 1
            )
        except Exception:
            pass

        # MLTO 学习指标
        try:
            from backend.database.connection import AnalyticsSessionLocal
            from backend.services.mlto.learning_bridge import get_learning_metrics
            _adb = AnalyticsSessionLocal()
            try:
                result["mlto"] = get_learning_metrics("", _adb)
            finally:
                _adb.close()
        except Exception:
            result["mlto"] = {}

    finally:
        if db:
            db.close()

    return result


# ═══════════════════════════════════════════════════════════
#  GET /factors — 因子状态详情
# ═══════════════════════════════════════════════════════════

@router.get("/factors")
async def get_factor_status():
    """获取因子引擎状态详情。"""
    result: Dict[str, Any] = {
        "factors": [],
        "factor_discovery": {},
        "factor_fusion": {},
    }

    try:
        from backend.services.factor_engine.base_factors import factor_engine
        for f in getattr(factor_engine, "factors", []):
            result["factors"].append({
                "name": getattr(f, "name", "?"),
                "category": getattr(f, "category", "?"),
                "value": getattr(f, "value", None),
                "signal": getattr(f, "signal", "neutral"),
            })

        # 因子发现状态
        try:
            from backend.services.factor_discovery import factor_discovery_engine
            result["factor_discovery"] = {
                "discovered_count": factor_discovery_engine._state.get("discovered_count", 0),
                "validated_count": factor_discovery_engine._state.get("validated_count", 0),
                "last_discovery": factor_discovery_engine._state.get("last_discovery_ts", 0),
            }
        except Exception:
            pass

        # 因子融合状态
        try:
            from backend.services.factor_strategy_fusion import factor_strategy_fusion
            result["factor_fusion"] = factor_strategy_fusion.get_status()
        except Exception:
            pass

    except Exception as exc:
        result["error"] = str(exc)

    return result


# ═══════════════════════════════════════════════════════════
#  GET /strategies — 策略状态
# ═══════════════════════════════════════════════════════════

@router.get("/strategies")
async def get_strategy_status():
    """获取策略模板与进化状态。"""
    db = _get_db()
    result: Dict[str, Any] = {"templates": [], "evolution_progress": {}, "walk_forward": {}}

    try:
        if db:
            from backend.database.models import StrategyTemplate, StrategyMemory

            templates = db.query(StrategyTemplate).limit(30).all()
            for t in templates:
                memory = db.query(StrategyMemory).filter(
                    StrategyMemory.strategy_id == t.template_id
                ).first()
                result["templates"].append({
                    "template_id": t.template_id,
                    "name": t.name,
                    "symbol": getattr(t, "symbol", "?"),
                    "tier": getattr(t, "tier", "mid"),
                    "status": getattr(t, "status", "active"),
                    "sharpe": getattr(t, "sharpe_ratio", None),
                    "win_rate": getattr(t, "win_rate", None),
                    "total_trades": getattr(t, "total_trades", None),
                    "lessons_count": len(memory.key_lessons) if memory and memory.key_lessons else 0,
                })

        # 进化进度
        try:
            from backend.services.evolution_scheduler import evolution_scheduler
            if hasattr(evolution_scheduler, "progress"):
                result["evolution_progress"] = {
                    "is_running": evolution_scheduler.is_running,
                    "generation": getattr(evolution_scheduler, "_generation", 0),
                    "active_populations": len(
                        getattr(evolution_scheduler, "_active_populations", {})
                    ),
                }
        except Exception:
            pass

        # Walk-Forward 验证
        try:
            from backend.services.walk_forward_validator import walk_forward_validator
            result["walk_forward"] = walk_forward_validator.get_status()
        except Exception:
            pass

    finally:
        if db:
            db.close()

    return result


# ═══════════════════════════════════════════════════════════
#  GET /evolution — 进化进度
# ═══════════════════════════════════════════════════════════

@router.get("/evolution")
async def get_evolution_progress():
    """获取进化调度器进度详情。"""
    result: Dict[str, Any] = {}

    try:
        from backend.services.evolution_scheduler import evolution_scheduler

        result["is_running"] = getattr(evolution_scheduler, "_running_evolution", False)
        result["generation"] = getattr(evolution_scheduler, "_generation", 0)
        result["active_populations"] = len(
            getattr(evolution_scheduler, "_active_populations", {})
        )
        result["progress"] = getattr(evolution_scheduler, "progress", {})
        result["last_evolution_at"] = getattr(
            evolution_scheduler, "last_evolution_at", None
        )
        # NSGA-II 开关状态
        try:
            from backend.config.settings import NSGA2_ENABLED
            result["nsga2_enabled"] = NSGA2_ENABLED
        except Exception:
            result["nsga2_enabled"] = True
        # 进化历史记录数
        try:
            from backend.database.connection import SessionLocal as _SL
            _edb = _SL()
            try:
                from sqlalchemy import text as _t
                _cnt = _edb.execute(_t("SELECT count(*) FROM backtest_runs WHERE created_at >= NOW() - INTERVAL '7 days'")).scalar()
                result["history_count"] = int(_cnt or 0)
                # promoted 信息可能存在 status 字段
                _promoted = _edb.execute(_t("SELECT count(*) FROM backtest_runs WHERE status LIKE '%promot%'")).scalar()
                result["promoted_count"] = int(_promoted or 0)
            finally:
                _edb.close()
        except Exception:
            result["history_count"] = 0
            result["promoted_count"] = 0

        # 记忆衰减状态
        try:
            from backend.services.memory_decay_service import memory_decay_service
            result["memory_decay"] = memory_decay_service.get_status()
        except Exception:
            pass

        # 交易叙事状态（[2026-08-17] trading_narrative_engine 已删除）
        result["narrative"] = {"removed": True, "note": "trading_narrative_engine removed 2026-08-17"}

    except Exception as exc:
        result["error"] = str(exc)

    return result


# ═══════════════════════════════════════════════════════════
#  GET /memory — 策略记忆分析
# ═══════════════════════════════════════════════════════════

@router.get("/memory")
async def get_memory_insights():
    """获取策略记忆汇总与分析。"""
    db = _get_db()
    result: Dict[str, Any] = {"memories": [], "cross_cycle_patterns": [], "counterfactual": []}

    try:
        if db:
            from backend.database.models import StrategyMemory

            memories = db.query(StrategyMemory).limit(20).all()
            for m in memories:
                lessons = m.key_lessons or []
                result["memories"].append({
                    "strategy_id": m.strategy_id,
                    "lessons_count": len(lessons),
                    "recent_types": list(set(
                        l.get("type", "?") for l in lessons[-10:]
                        if isinstance(l, dict)
                    )),
                    "last_updated": (
                        lessons[-1].get("ts", "")
                        if lessons and isinstance(lessons[-1], dict)
                        else ""
                    ),
                })

                # 提取跨周期模式
                for l in lessons:
                    if isinstance(l, dict) and l.get("type") == "cross_cycle_pattern":
                        result["cross_cycle_patterns"].append({
                            "strategy_id": m.strategy_id,
                            "symbol": l.get("symbol", ""),
                            "pattern_count": l.get("pattern_count", 0),
                            "ts": l.get("ts", ""),
                        })

                # 提取反事实推理
                for l in lessons:
                    if isinstance(l, dict) and l.get("type") == "counterfactual":
                        result["counterfactual"].append({
                            "strategy_id": m.strategy_id,
                            "scenario": l.get("scenario", ""),
                            "lesson": l.get("lesson", "")[:200],
                            "ts": l.get("ts", ""),
                        })

    finally:
        if db:
            db.close()

    return result


# ═══════════════════════════════════════════════════════════
#  GET /experiments — A/B 实验状态
# ═══════════════════════════════════════════════════════════

@router.get("/experiments")
async def get_experiment_status():
    """获取 A/B 学习对照实验状态。"""
    try:
        from backend.services.learning_ab_framework import learning_ab_framework
        # 先检查超时实验
        learning_ab_framework.check_timeout_experiments()
        return learning_ab_framework.get_status()
    except Exception as exc:
        return {"error": str(exc)}


# ═══════════════════════════════════════════════════════════
#  GET /transfer — 跨市场迁移状态
# ═══════════════════════════════════════════════════════════

@router.get("/transfer")
async def get_transfer_status():
    """获取跨市场知识迁移状态。"""
    try:
        from backend.services.cross_market_transfer import cross_market_transfer
        return cross_market_transfer.get_status()
    except Exception as exc:
        return {"error": str(exc)}


# ═══════════════════════════════════════════════════════════
#  GET /feature-flags — 获取特性开关
# ═══════════════════════════════════════════════════════════

@router.get("/feature-flags")
async def get_feature_flags():
    """获取所有学习系统特性开关状态。

    L5 起额外暴露 LearningConfig（统一配置）和 BackendRegistry 状态。
    """
    flags: Dict[str, bool] = {}

    flag_keys = [
        # P0 学习基建
        "AI_CAUSAL_DISCOVERY_ENABLED",
        "AI_FACTOR_STRATEGY_JOINT_ENABLED",
        "AI_CONCEPT_DRIFT_DETECTION_ENABLED",
        "AI_MEMORY_DECAY_ENABLED",
        # P1 深度推理
        "AI_MULTI_ROUND_ANALYSIS_ENABLED",
        "AI_COUNTERFACTUAL_SANDBOX_ENABLED",
        "AI_TRADING_NARRATIVE_ENABLED",
        "AI_STRATEGY_DEEP_DIVE_ENHANCED_ENABLED",
        # P2 自主进化
        "AI_FACTOR_DISCOVERY_ENABLED",
        "AI_STRUCTURAL_MUTATION_ENABLED",
        "AI_FACTOR_STRATEGY_FUSION_ENABLED",
        "AI_VPVR_V3_ENABLED",
        "AI_FREQUENCY_CONSTRAINT_CHAIN_ENABLED",
        "AI_WALK_FORWARD_VALIDATION_ENABLED",
        # P3 全局调度
        "AI_CROSS_MARKET_TRANSFER_ENABLED",
    ]

    for key in flag_keys:
        try:
            from backend.config import settings
            flags[key] = bool(getattr(settings, key, False))
        except Exception:
            flags[key] = False

    # L5: 附带统一配置快照 + 后端注册表状态
    try:
        from backend.config.learning_config import get_learning_config
        flags["_learning_config"] = get_learning_config().to_dict()
    except Exception:
        pass
    try:
        from backend.services.learning_registry_bridge import get_registry
        flags["_backends"] = get_registry().status()
    except Exception:
        pass

    flags["_unwired_flags"] = {
        "AI_AB_FRAMEWORK_ENABLED": "Paper 已永久关闭：无 record_trade 分流，开启无效",
        "HERMES_L2_AB": "Paper 默认 direct active，L2 优化后直接生效",
        "PROMPT_TRAINING_AB": "Paper 默认 B 版直接绑定策略",
    }

    return flags


# ═══════════════════════════════════════════════════════════
#  POST /feature-flags — 更新特性开关（运行时）
# ═══════════════════════════════════════════════════════════

@router.post("/feature-flags")
async def update_feature_flag(body: FeatureFlagUpdate):
    """
    运行时更新特性开关。
    ⚠️ 仅内存生效，重启后恢复 .env 配置值。
    """
    valid_keys = (
        # P0
        "AI_CAUSAL_DISCOVERY_ENABLED",
        "AI_FACTOR_STRATEGY_JOINT_ENABLED",
        "AI_CONCEPT_DRIFT_DETECTION_ENABLED",
        "AI_MEMORY_DECAY_ENABLED",
        # P1
        "AI_MULTI_ROUND_ANALYSIS_ENABLED",
        "AI_COUNTERFACTUAL_SANDBOX_ENABLED",
        "AI_TRADING_NARRATIVE_ENABLED",
        "AI_STRATEGY_DEEP_DIVE_ENHANCED_ENABLED",
        # P2
        "AI_FACTOR_DISCOVERY_ENABLED",
        "AI_STRUCTURAL_MUTATION_ENABLED",
        "AI_FACTOR_STRATEGY_FUSION_ENABLED",
        "AI_VPVR_V3_ENABLED",
        "AI_FREQUENCY_CONSTRAINT_CHAIN_ENABLED",
        "AI_WALK_FORWARD_VALIDATION_ENABLED",
        # P3
        "AI_CROSS_MARKET_TRANSFER_ENABLED",
    )

    if body.key not in valid_keys:
        raise HTTPException(
            status_code=400,
            detail=f"无效开关: {body.key}. 有效: {valid_keys}",
        )

    try:
        import backend.config.settings as settings
        setattr(settings, body.key, body.value)
        logger.info(f"[Dashboard] 特性开关更新: {body.key}={body.value}")
        return {"key": body.key, "value": body.value, "status": "updated"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ═══════════════════════════════════════════════════════════
#  GET /health — 系统健康检查
# ═══════════════════════════════════════════════════════════

@router.get("/health")
async def get_learning_health():
    """
    学习系统健康检查（2026-08-06 修正：弃用 import 检查，改为真实闭环健康）。

    原实现仅验证模块可 import 即返回 ok，属假健康；现转调
    learning_health_service.build_learning_health()（6 类数据 + 5 条闭环）。
    """
    try:
        from backend.services.learning_health_service import build_learning_health

        return build_learning_health()
    except Exception as exc:
        return {
            "overall": "dead",
            "checked_at": None,
            "items": [{"name": "learning_health", "label": "健康检查", "status": "dead", "detail": str(exc)}],
        }


# 路由已在模块级定义，直接由 main.py 挂载
