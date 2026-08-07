"""Hermes 自进化系统 API — 暴露成熟度、智慧、模式、Prompt、架构、策略创生数据。"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/hermes", tags=["Hermes"])


# ──── 前端数据 DTO ────

@router.get("/maturity")
def hermes_maturity() -> Dict[str, Any]:
    """Hermes 成熟度评分 (0-100) + 四层明细。"""
    from backend.services.hermes_orchestrator import hermes as orch
    try:
        orch.ensure_initialized()
        return orch.compute_maturity_score()
    except Exception as e:
        return {"error": str(e), "maturity_score": 0}


@router.get("/health")
def hermes_health() -> Dict[str, Any]:
    """全线健康巡检。"""
    from backend.services.hermes_orchestrator import hermes as orch
    try:
        orch.ensure_initialized()
        return orch.full_health_check()
    except Exception as e:
        return {"error": str(e), "db_ok": False}


@router.get("/wisdom")
def hermes_wisdom(limit: int = 20) -> Dict[str, Any]:
    """L1: 提案智慧列表。"""
    from backend.services.hermes_db import hermes_fetchall
    try:
        records = hermes_fetchall(
            """SELECT id, proposal_id, outcome, focus, market_condition,
                      param_key, param_direction, param_delta_pct,
                      pnl_impact, win_rate_delta, confidence, created_at
               FROM proposal_wisdom_records
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        )
        total = hermes_fetchall(
            "SELECT COUNT(*) as cnt FROM proposal_wisdom_records", ()
        )
        return {"records": records, "total": total[0]["cnt"] if total else 0}
    except Exception as e:
        return {"error": str(e), "records": [], "total": 0}


@router.get("/patterns")
def hermes_patterns(min_samples: int = 2) -> Dict[str, Any]:
    """L1: 参数效果模式库 (EMA平滑 + 时间衰减)。"""
    from backend.services.hermes_proposal_wisdom_engine import proposal_wisdom
    try:
        patterns = proposal_wisdom.get_top_patterns(min_samples=min_samples)
        return {"patterns": patterns}
    except Exception as e:
        return {"error": str(e), "patterns": []}


@router.get("/prompts")
def hermes_prompts() -> Dict[str, Any]:
    """L2: Prompt 版本历史 + A/B 测试状态。"""
    from backend.services.hermes_db import hermes_fetchall
    try:
        versions = hermes_fetchall(
            """SELECT id, task_id, version, change_type, change_summary,
                      proposals_generated, avg_improved_rate, avg_degraded_rate,
                      avg_quality_score, status, created_at, activated_at
               FROM prompt_versions
               ORDER BY id DESC LIMIT 30"""
        )
        ab_tests = hermes_fetchall(
            """SELECT id, task_id, version_a, version_b,
                      proposals_a, proposals_b, improved_rate_a, improved_rate_b,
                      winner, status, started_at, concluded_at
               FROM prompt_ab_tests
               ORDER BY id DESC LIMIT 10"""
        )
        return {"versions": versions, "ab_tests": ab_tests}
    except Exception as e:
        return {"error": str(e), "versions": [], "ab_tests": []}


@router.get("/architecture")
def hermes_architecture(status: str = "all") -> Dict[str, Any]:
    """L3: 架构进化提案列表。"""
    from backend.services.hermes_db import hermes_fetchall
    try:
        if status == "all":
            proposals = hermes_fetchall(
                """SELECT * FROM architecture_evolution_proposals
                   ORDER BY id DESC LIMIT 30"""
            )
        else:
            proposals = hermes_fetchall(
                """SELECT * FROM architecture_evolution_proposals
                   WHERE status=? ORDER BY id DESC LIMIT 30""",
                (status,),
            )
        from backend.services.hermes_architecture_evolution_engine import architecture_evolution
        stats = architecture_evolution.get_stats()
        return {"proposals": proposals, "stats": stats}
    except Exception as e:
        return {"error": str(e), "proposals": [], "stats": {}}


@router.get("/genesis")
def hermes_genesis() -> Dict[str, Any]:
    """L4: 策略创生候选 + 孵化状态。"""
    from backend.services.hermes_db import hermes_fetchall
    try:
        candidates = hermes_fetchall(
            """SELECT * FROM strategy_genesis_candidates
               ORDER BY id DESC LIMIT 20"""
        )
        from backend.services.hermes_strategy_genesis_engine import strategy_genesis
        stats = strategy_genesis.get_stats()
        return {"candidates": candidates, "stats": stats}
    except Exception as e:
        return {"error": str(e), "candidates": [], "stats": {}}


@router.get("/schedule")
def hermes_schedule() -> Dict[str, Any]:
    """四层(L1-L4)定时任务时间轴。"""
    from backend.services.opencode_scheduler import get_hermes_schedule_status
    try:
        return {"tasks": get_hermes_schedule_status()}
    except Exception as e:
        return {"error": str(e), "tasks": []}


_HERMES_RUN_HANDLERS = {
    "wisdom_accumulate": lambda h: h.accumulate_wisdom(),
    "meta_analysis": lambda h: h.run_meta_analysis(),
    "prompt_optimize": lambda h: h.run_prompt_optimization(),
    "ab_test_eval": lambda h: h.evaluate_ab_tests(),
    "architecture_evolution": lambda h: h.run_architecture_evolution(),
    "strategy_genesis": lambda h: h.run_strategy_genesis(),
    "genesis_check": lambda h: h.check_genesis_candidates(),
    "bootstrap": lambda h: (
        h.run_prompt_optimization(),
        h.run_architecture_evolution(),
        h.run_strategy_genesis(),
    ),
}


@router.post("/run/{task_name}")
def hermes_run_task(task_name: str) -> Dict[str, Any]:
    """手动触发 Hermes 任务（调试/补跑）。task_name 见 _HERMES_RUN_HANDLERS。"""
    from backend.services.hermes_orchestrator import hermes as orch

    handler = _HERMES_RUN_HANDLERS.get(task_name)
    if not handler:
        return {
            "ok": False,
            "error": f"未知任务: {task_name}",
            "available": sorted(_HERMES_RUN_HANDLERS.keys()),
        }
    try:
        orch.ensure_initialized()
        result = handler(orch)
        return {"ok": True, "task": task_name, "result": result}
    except Exception as e:
        return {"ok": False, "task": task_name, "error": str(e)}


@router.post("/architecture/{proposal_id}/accept")
def hermes_accept_architecture(proposal_id: int) -> Dict[str, Any]:
    from backend.services.hermes_architecture_evolution_engine import architecture_evolution
    return architecture_evolution.accept_proposal(proposal_id)


@router.post("/architecture/auto-accept-pending")
def hermes_auto_accept_architecture_pending(
    limit: int = Query(20, ge=1, le=100),
) -> Dict[str, Any]:
    """Paper 模式批量 accept L3 pending 提案（可多次调用直到 remaining=0）。"""
    from backend.services.hermes_architecture_evolution_engine import architecture_evolution
    return architecture_evolution.auto_accept_pending_paper(limit=limit)


@router.post("/architecture/reconcile-implemented")
def hermes_reconcile_architecture_implemented(
    limit: int = Query(100, ge=1, le=500),
) -> Dict[str, Any]:
    """Paper：将 status=accepted 的 L3 提案批量标记为 implemented（Governor 已落地）。"""
    from backend.services.hermes_architecture_evolution_engine import architecture_evolution
    return architecture_evolution.reconcile_implemented_paper(limit=limit)


@router.post("/architecture/{proposal_id}/reject")
def hermes_reject_architecture(proposal_id: int, reason: str = "") -> Dict[str, Any]:
    from backend.services.hermes_architecture_evolution_engine import architecture_evolution
    return architecture_evolution.reject_proposal(proposal_id, reason)


@router.post("/genesis/{candidate_id}/promote")
def hermes_promote_genesis(candidate_id: int) -> Dict[str, Any]:
    from backend.services.hermes_strategy_genesis_engine import strategy_genesis
    return strategy_genesis.propose_promote_validated(candidate_id)


@router.post("/prompts/recover-stuck")
def hermes_recover_stuck_prompts() -> Dict[str, Any]:
    """立即恢复卡在 ab_testing 的 prompt → active。"""
    from backend.services.hermes_prompt_optimizer_engine import PromptOptimizerEngine
    return PromptOptimizerEngine().recover_stuck_versions()


@router.post("/prompts/sync")
def hermes_sync_prompts() -> Dict[str, Any]:
    """磁盘 .md → 数据库 prompt_versions 热同步。

    当提示词文件手动升级后（如 1.0.0→2.0.0），调用此端点将最新磁盘版本
    同步到数据库 active 快照，确保 L2 优化在新版本基础上进行。
    """
    from backend.services.hermes_prompt_optimizer_engine import PromptOptimizerEngine
    engine = PromptOptimizerEngine()
    return engine.sync_baseline_from_disk()


@router.get("/prompts/diff")
def hermes_prompts_diff() -> Dict[str, Any]:
    """比较磁盘 .md 版本 vs 数据库 active 版本（不执行同步，仅诊断）。"""
    from backend.services.hermes_prompt_optimizer_engine import PromptOptimizerEngine, OPTIMIZABLE_TASKS
    from backend.services.hermes_db import hermes_fetchone

    engine = PromptOptimizerEngine()
    items = []
    for task_id in OPTIMIZABLE_TASKS:
        disk_ver = engine._parse_disk_version(task_id) or "unknown"
        row = hermes_fetchone(
            "SELECT version FROM prompt_versions WHERE task_id=? AND status='active' ORDER BY id DESC LIMIT 1",
            (task_id,),
        )
        db_ver = row["version"] if row else "none"
        needs_sync = engine._version_tuple(disk_ver) > engine._version_tuple(db_ver)
        items.append({
            "task": task_id,
            "disk_version": disk_ver,
            "db_version": db_ver,
            "needs_sync": needs_sync,
        })
    return {"items": items, "needs_sync_count": sum(1 for i in items if i["needs_sync"])}


@router.get("/block-patterns")
def hermes_block_patterns() -> Dict[str, Any]:
    from backend.services.learning.backends.block_pattern_learning_backend import BlockPatternLearningBackend
    return {"stats": BlockPatternLearningBackend.get_stats()}


@router.get("/dashboard")
def hermes_dashboard() -> Dict[str, Any]:
    """一站式仪表盘：maturity + 各层关键指标打包。"""
    from backend.services.hermes_db import hermes_fetchall
    from backend.services.hermes_orchestrator import hermes as orch

    try:
        orch.ensure_initialized()
        maturity = orch.compute_maturity_score()

        # 时间轴：四层「上次/下次运行时间、是否运行中」
        from backend.services.opencode_scheduler import get_hermes_schedule_status
        try:
            schedule = get_hermes_schedule_status()
        except Exception as se:
            schedule = []

        # L1 指标
        wisdom_total = hermes_fetchall(
            "SELECT COUNT(*) as cnt FROM proposal_wisdom_records", ()
        )[0]["cnt"]
        pattern_count = hermes_fetchall(
            "SELECT COUNT(*) as cnt FROM param_effect_patterns WHERE sample_count >= 2", ()
        )[0]["cnt"]

        # L2 指标
        active_prompts = hermes_fetchall(
            "SELECT COUNT(*) as cnt FROM prompt_versions WHERE status='active'", ()
        )[0]["cnt"]
        running_ab = hermes_fetchall(
            "SELECT COUNT(*) as cnt FROM prompt_ab_tests WHERE status='running'", ()
        )[0]["cnt"]

        # L3 指标
        arch_stats = orch.arch_evo.get_stats()

        # L4 指标
        gen_stats = orch.strategy_gen.get_stats()

        return {
            "maturity": maturity,
            "schedule": schedule,
            "l1_wisdom": {
                "total_records": wisdom_total,
                "patterns": pattern_count,
            },
            "l2_prompt": {
                "active_versions": active_prompts,
                "running_ab_tests": running_ab,
            },
            "l3_architecture": arch_stats,
            "l4_genesis": gen_stats,
        }
    except Exception as e:
        return {"error": str(e)}
