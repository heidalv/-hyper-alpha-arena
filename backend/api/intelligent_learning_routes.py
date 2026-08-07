"""
Intelligent Learning Center — 统一智能学习中心 API

Phase 5 整合: 合并 AILearningCenter + OpenCodeCenter 的后端数据源。

  GET  /api/intelligent-learning/overview   — 系统运行状态概览
  GET  /api/intelligent-learning/knowledge  — 统一知识池查询
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/intelligent-learning", tags=["IntelligentLearning"])


@router.get("/overview")
async def get_overview():
    """统一智能学习中心概览 — 合并 Evolution + OpenCode + LearningLoop + 因子 + 知识池状态"""
    overview = {
        "evolution": {},
        "factors": {},
        "strategies": {},
        "opencode": {},
        "learning_loop": {},
        "knowledge_pool": {},
        "alerts": [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # 1) Evolution 状态
    try:
        from backend.services.evolution_scheduler import evolution_scheduler
        from backend.database.connection import SessionLocal as _SL
        from backend.database.models import EvolutionEvent
        _db = _SL()
        try:
            evo_event = _db.query(EvolutionEvent).order_by(
                EvolutionEvent.created_at.desc()
            ).first()
            overview["evolution"] = {
                "last_evolution_at": evo_event.created_at.isoformat() if evo_event and evo_event.created_at else None,
                "last_evolution_type": evo_event.evolution_type if evo_event else None,
                "last_promoted_count": evo_event.promoted_count if evo_event else 0,
                "last_best_fitness": evo_event.best_fitness if evo_event else 0,
                "scheduler_active": True,
            }
        finally:
            _db.close()
    except Exception as e:
        overview["evolution"] = {"error": str(e)}

    # 2) 因子状态（L4 并轨：改用新 FactorRegistry，不再读旧 FactorEngine.FACTORS）
    try:
        from backend.services.factor_engine.factor_registry import registry as _factor_registry
        factor_count = _factor_registry.count()
        overview["factors"] = {
            "total": factor_count,
            "active": factor_count,
            "status": "healthy" if factor_count >= 100 else "degraded" if factor_count > 21 else "critical",
        }
    except Exception as e:
        overview["factors"] = {"error": str(e)}

    # 3) 策略状态
    try:
        from backend.database.connection import SessionLocal as _SL
        from backend.database.models import AIStrategy
        _db = _SL()
        try:
            strategies = _db.query(AIStrategy).filter(AIStrategy.status != "archived").all()
            by_tier = {}
            for s in strategies:
                tier = getattr(s, "timeframe_tier", "unknown") or "unknown"
                by_tier[tier] = by_tier.get(tier, 0) + 1
            overview["strategies"] = {
                "total": len(strategies),
                "active": sum(1 for s in strategies if s.status == "active"),
                "by_tier": by_tier,
            }
        finally:
            _db.close()
    except Exception as e:
        overview["strategies"] = {"error": str(e)}

    # 4) OpenCode 状态
    try:
        from backend.services.opencode_bridge import get_bridge_status
        from backend.database.connection import SessionLocal as _SL
        from backend.database.models import OpenCodeInsightDB, OpenCodeEvolutionProposalDB
        _db = _SL()
        try:
            bridge_status = get_bridge_status()
            open_insights = _db.query(OpenCodeInsightDB).filter(
                OpenCodeInsightDB.status == "open"
            ).count()
            pending_proposals = _db.query(OpenCodeEvolutionProposalDB).filter(
                OpenCodeEvolutionProposalDB.status == "pending"
            ).count()
            overview["opencode"] = {
                "sidecar_healthy": bridge_status.get("sidecar_healthy", False),
                "sidecar_port": bridge_status.get("port"),
                "open_insights": open_insights,
                "pending_proposals": pending_proposals,
            }
        finally:
            _db.close()
    except Exception as e:
        overview["opencode"] = {"error": str(e)}

    # 5) LearningLoop 状态
    try:
        from backend.services.learning_loop_service import learning_loop
        loop_status = learning_loop.status()
        overview["learning_loop"] = {
            "enabled": loop_status.get("enabled", False),
            "paused": loop_status.get("paused", False),
            "registered": loop_status.get("registered", False),
            "last_tick_at": loop_status.get("last_tick_at"),
        }
    except Exception as e:
        overview["learning_loop"] = {"error": str(e)}

    # 6) 知识池状态
    try:
        from backend.database.connection import SessionLocal as _SL
        from backend.database.models import StrategyMemory
        _db = _SL()
        try:
            mem = _db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == "_global_"
            ).first()
            lessons = mem.key_lessons if mem else []
            if isinstance(lessons, str):
                import json
                try:
                    lessons = json.loads(lessons)
                except Exception:
                    lessons = []
            by_category = {}
            for l in (lessons or []):
                if isinstance(l, dict):
                    cat = l.get("category", l.get("type", "other"))
                    by_category[cat] = by_category.get(cat, 0) + 1
            overview["knowledge_pool"] = {
                "total_lessons": len(lessons or []),
                "by_category": by_category,
            }
        finally:
            _db.close()
    except Exception as e:
        overview["knowledge_pool"] = {"error": str(e)}

    # 6b) Hermes 自进化
    try:
        from backend.services.hermes_orchestrator import hermes as _hermes
        _hermes.ensure_initialized()
        maturity = _hermes.compute_maturity_score()
        from backend.services.hermes_architecture_evolution_engine import architecture_evolution
        l3_stats = architecture_evolution.get_stats()
        overview["hermes"] = {
            "maturity_score": maturity.get("maturity_score", 0),
            "layers": maturity.get("layers", {}),
            "l3_architecture": l3_stats,
        }
    except Exception as e:
        overview["hermes"] = {"error": str(e)}

    # 6c) RuntimeGovernor 待审批
    try:
        from backend.services.runtime_governor import runtime_governor
        pending = runtime_governor.list_pending()
        overview["runtime_governor"] = {
            "pending_count": len(pending),
            "pending": pending[:5],
        }
    except Exception as e:
        overview["runtime_governor"] = {"error": str(e)}

    # 7) 告警：OpenCode major/critical insights（带去重）
    try:
        from backend.database.connection import SessionLocal as _SL
        from backend.database.models import OpenCodeInsightDB
        _db = _SL()
        try:
            major_insights = _db.query(OpenCodeInsightDB).filter(
                OpenCodeInsightDB.status == "open",
                OpenCodeInsightDB.severity.in_(["major", "critical"]),
            ).order_by(OpenCodeInsightDB.created_at.desc()).limit(20).all()
            # 去重：相同 title 只保留最新的一条
            seen_titles = set()
            deduped = []
            for i in major_insights:
                key = (i.title or "").strip()
                if key and key not in seen_titles:
                    seen_titles.add(key)
                    deduped.append(i)
            overview["alerts"] = [
                {
                    "severity": i.severity,
                    "title": i.title,
                    "source": i.source or "opencode",
                    "category": i.category,
                    "created_at": i.created_at.isoformat() if i.created_at else None,
                }
                for i in deduped[:10]
            ]
        finally:
            _db.close()
    except Exception as e:
        overview["alerts"] = []

    # 统一进化学习内核：附带 core 块（血缘账本 + 特性开关）。
    # 规范统一入口为 /api/learning/overview，本处 core 块用于兼容期平滑过渡。
    try:
        from backend.services.learning_core import orchestrator as _orc
        overview["core"] = _orc.overview().get("core", {})
        overview["_canonical_overview"] = "/api/learning/overview"
    except Exception:
        pass

    return overview


@router.get("/knowledge")
async def query_knowledge(
    categories: Optional[str] = Query(None, description="逗号分隔的类别: insight,lesson,narrative,pattern"),
    sources: Optional[str] = Query(None, description="逗号分隔的来源: opencode,evolution,learning_loop"),
    limit: int = Query(20, ge=1, le=100),
):
    """统一知识池查询 — 按 category/source 筛选"""
    try:
        from backend.database.connection import SessionLocal as _SL
        from backend.database.models import StrategyMemory
        _db = _SL()
        try:
            mem = _db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == "_global_"
            ).first()
            lessons = mem.key_lessons if mem else []
            if isinstance(lessons, str):
                import json
                try:
                    lessons = json.loads(lessons)
                except Exception:
                    lessons = []
            
            # 过滤
            cat_filter = set(c.strip() for c in (categories or "").split(",") if c.strip())
            src_filter = set(s.strip() for s in (sources or "").split(",") if s.strip())
            
            result = []
            for l in (lessons or []):
                if not isinstance(l, dict):
                    continue
                if cat_filter:
                    item_cat = l.get("category", l.get("type", ""))
                    if item_cat not in cat_filter:
                        continue
                if src_filter:
                    item_src = l.get("source", "")
                    if item_src not in src_filter:
                        continue
                result.append(l)
            
            # 按 ingested_at 降序
            result.sort(key=lambda x: str(x.get("ingested_at", "")), reverse=True)
            result = result[:limit]
            
            return {
                "total": len(lessons or []),
                "filtered": len(result),
                "items": result,
            }
        finally:
            _db.close()
    except Exception as e:
        logger.error(f"[IntelligentLearning] knowledge query error: {e}")
        return {"total": 0, "filtered": 0, "items": [], "error": str(e)}


# ════════════════════════════════════════════════════════════════
#  阶段2(S2-11) 学习三通道看板 API
#  通道一：wisdom 闭环（净扣费+质量闸门+验证强度排序）
#  通道二：参数域扩展（Hermes 高置信模式 → GA 搜索域）
#  通道三：QAA 调度统一（域注册表 + 心跳）
#  配套：决策链路视图 + 选币反馈面板
# ════════════════════════════════════════════════════════════════


@router.get("/wisdom-loop")
def get_wisdom_loop() -> dict:
    """通道一：wisdom 闭环看板。

    返回：
    - ranked: 验证强度排序 Top 20（eff × 质量命中权重 × log1p(applied)）；
    - report: 总览（active/deactivated/by_type/top_wisdom）；
    - cfg: 质量闸门 / 金额标度 / 最小质量样本配置。
    """
    try:
        from backend.database.connection import SessionLocal as _SL
        from backend.services.wisdom_tracker import wisdom_tracker

        db = _SL()
        try:
            ranked = wisdom_tracker.get_ranked_wisdom(db, limit=20)
            report = wisdom_tracker.get_wisdom_effectiveness_report(db)
        finally:
            db.close()
        return {"ranked": ranked, "report": report}
    except Exception as e:
        logger.error(f"[IntelligentLearning] wisdom-loop error: {e}")
        return {"ranked": [], "report": {}, "error": str(e)}


@router.get("/param-domain")
def get_param_domain_status() -> dict:
    """通道二：参数域扩展状态。

    返回 Hermes L1 高置信模式（param_effect_patterns outcome=improved）的
    统计 + 基于当前基础域实时重放的扩展 changes（哪个参数被向哪侧扩了）。
    """
    try:
        from backend.services.evolution_scheduler import _get_full_param_ranges
        from backend.services.param_domain_expander import (
            _settings_cfg as _pde_cfg,
            apply_domain_expansion,
            load_improved_patterns,
        )

        patterns = load_improved_patterns()
        by_dir: dict = {"increase": 0, "decrease": 0}
        by_key: dict = {}
        for p in patterns:
            d = str(p.get("direction") or "").lower()
            key = str(p.get("param_key") or "").strip()
            if d in by_dir:
                by_dir[d] += 1
            if key:
                k = by_key.setdefault(
                    key, {"increase": 0, "decrease": 0, "avg_pnl_impact": 0.0, "n": 0}
                )
                if d in k:
                    k[d] += 1
                k["avg_pnl_impact"] += float(p.get("avg_pnl_impact") or 0)
                k["n"] += 1
        for k in by_key.values():
            if k["n"]:
                k["avg_pnl_impact"] = round(k["avg_pnl_impact"] / k["n"], 4)

        base = _get_full_param_ranges()
        expanded, changes = apply_domain_expansion(base)
        return {
            "cfg": _pde_cfg(),
            "patterns": {
                "total": len(patterns),
                "by_direction": by_dir,
                "by_key": by_key,
            },
            "base_ranges": {k: list(v) for k, v in base.items()},
            "expanded_ranges": {k: list(v) for k, v in expanded.items()},
            "changes": changes,
            "expanded_count": len(changes),
        }
    except Exception as e:
        logger.error(f"[IntelligentLearning] param-domain error: {e}")
        return {"patterns": {"total": 0}, "changes": [], "error": str(e)}


@router.get("/qaa-scheduler")
def get_qaa_scheduler_status() -> dict:
    """通道三：QAA 调度统一心跳（域注册表 + 最近运行状态 + 总开关）。"""
    try:
        from backend.services.qaa_scheduler import get_scheduler_status
        return get_scheduler_status()
    except Exception as e:
        logger.error(f"[IntelligentLearning] qaa-scheduler error: {e}")
        return {"enabled": False, "domains": {}, "error": str(e)}


@router.get("/decision-chain")
def get_decision_chain(limit: int = Query(20, ge=1, le=100)) -> dict:
    """决策链路视图：AI 决策 → wisdom 应用 → 交易结果评估。

    跨库查询：AIDecisionLog（analytics）→ wisdom_applied →
    TradingWisdom 详情（core）。返回最近 limit 条带智慧应用的决策。
    """
    try:
        from backend.database.connection import (
            AnalyticsSessionLocal as _ASL,
            SessionLocal as _SL,
        )
        from backend.database.models import AIDecisionLog, TradingWisdom

        adb = _ASL()
        cdb = _SL()
        try:
            rows = (
                adb.query(AIDecisionLog)
                .filter(AIDecisionLog.wisdom_applied.isnot(None))
                .order_by(AIDecisionLog.decision_time.desc())
                .limit(limit)
                .all()
            )

            # 智慧详情（一次批量查询）
            wisdom_ids: set = set()
            for r in rows:
                applied = r.wisdom_applied or {}
                if isinstance(applied, dict):
                    wisdom_ids.update(applied.get("wisdom_ids", []) or [])
            wisdom_map: dict = {}
            if wisdom_ids:
                for w in cdb.query(TradingWisdom).filter(
                    TradingWisdom.id.in_(list(wisdom_ids))
                ).all():
                    wisdom_map[w.id] = {
                        "type": w.wisdom_type,
                        "tier": w.tier,
                        "template_id": w.template_id,
                        "effectiveness": w.effectiveness_score,
                        "evaluation_count": w.evaluation_count or 0,
                        "quality_hit_count": w.quality_hit_count or 0,
                        "is_active": w.is_active,
                    }

            chain = []
            for r in rows:
                applied = r.wisdom_applied or {}
                ids = applied.get("wisdom_ids", []) if isinstance(applied, dict) else []
                chain.append({
                    "id": r.id,
                    "decision_time": r.decision_time.isoformat() if r.decision_time else None,
                    "symbol": r.symbol,
                    "operation": r.operation,
                    "decision_source": r.decision_source,
                    "realized_pnl": float(r.realized_pnl) if r.realized_pnl is not None else None,
                    "wisdom_ids": ids,
                    "wisdoms": [wisdom_map.get(i) for i in ids if wisdom_map.get(i)],
                })

            total_any = adb.query(AIDecisionLog).count()
            return {
                "chain": chain,
                "total_decisions": total_any,
                "sampled": len(chain),
                "wisdom_covered": sum(1 for c in chain if c["wisdom_ids"]),
            }
        finally:
            adb.close()
            cdb.close()
    except Exception as e:
        logger.error(f"[IntelligentLearning] decision-chain error: {e}")
        return {"chain": [], "error": str(e)}


@router.get("/coin-feedback")
def get_coin_feedback() -> dict:
    """选币反馈面板：IC 加权权重 + 注入样本命中率（24h/72h）。"""
    try:
        from backend.database.connection import SessionLocal as _SL
        from backend.database.models import AutoCoinSelection
        from backend.services.coin_rank.ic_weights import get_ic_weights

        db = _SL()
        try:
            ic = get_ic_weights(db, force=False)
            injected = (
                db.query(AutoCoinSelection)
                .filter(AutoCoinSelection.action == "injected")
                .all()
            )
            total = len(injected)
            with_snapshot = sum(1 for r in injected if r.factor_snapshot_json)
            hit24 = sum(1 for r in injected if r.hit_24h)
            hit72 = sum(1 for r in injected if r.hit_72h)

            by_symbol: dict = {}
            for r in injected:
                s = by_symbol.setdefault(
                    r.symbol or "?", {"n": 0, "hit24": 0, "hit72": 0}
                )
                s["n"] += 1
                if r.hit_24h:
                    s["hit24"] += 1
                if r.hit_72h:
                    s["hit72"] += 1

            return {
                "ic_weights": {
                    "weights": ic.weights,
                    "ics": ic.ics,
                    "n_samples": ic.n_samples,
                    "enabled": ic.enabled,
                    "note": ic.note,
                    "computed_at": ic.computed_at,
                },
                "injected": {
                    "total": total,
                    "with_snapshot": with_snapshot,
                    "hit_24h": hit24,
                    "hit_72h": hit72,
                    "hit_rate_24h": round(hit24 / total, 4) if total else 0.0,
                    "hit_rate_72h": round(hit72 / total, 4) if total else 0.0,
                    "by_symbol": {
                        k: {
                            **v,
                            "hit_rate_24h": round(v["hit24"] / v["n"], 4) if v["n"] else 0.0,
                        }
                        for k, v in by_symbol.items()
                    },
                },
            }
        finally:
            db.close()
    except Exception as e:
        logger.error(f"[IntelligentLearning] coin-feedback error: {e}")
        return {"ic_weights": {}, "injected": {}, "error": str(e)}
