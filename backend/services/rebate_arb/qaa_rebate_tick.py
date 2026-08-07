"""
qaa_rebate_tick — Rebate 域 QAA Tick 入口。

QAA_V3_ENABLED 时走 TickOrchestrator；否则 fallback ExecutionAuthority.run_rebate_tick。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_last_workflow_run: Dict[str, Any] = {}

# M5: rebate tick 心跳（学习闭环健康检查用）
_last_tick_at: float = 0.0


def get_last_qaa_workflow_run() -> Dict[str, Any]:
    return dict(_last_workflow_run)


def get_last_rebate_tick_at() -> float:
    """最近一次 rebate tick 的 unix 时间戳（0 = 从未跑过）。"""
    return _last_tick_at


def _collect_s8_feedback() -> Dict[str, Any]:
    """收集 S8 最近表现，给 QAA/前端解释“下一轮是否值得刷”。"""
    feedback: Dict[str, Any] = {
        "active_positions": [],
        "last_closed": [],
        "recommendation": "safe",
        "reason": "",
    }
    try:
        from backend.services.rebate_arb.position_monitor import rebate_position_monitor

        for pos in rebate_position_monitor.get_active_positions():
            if pos.strategy_type.value != "S8":
                continue
            meta = pos.metadata if isinstance(pos.metadata, dict) else {}
            metrics = meta.get("rh_metrics") if isinstance(meta.get("rh_metrics"), dict) else {}
            feedback["active_positions"].append({
                "position_id": pos.position_id,
                "symbol": pos.symbol,
                "hold_hours": round(float(pos.hold_duration_hours or 0), 3),
                "pnl": round(float(pos.current_pnl or 0), 4),
                "rh_optimization_mode": meta.get("rh_optimization_mode"),
                "estimated_round_rh": meta.get("estimated_round_rh"),
                "rh_per_fee_usd": metrics.get("rh_per_fee_usd"),
                "rh_per_margin_hour": metrics.get("rh_per_margin_hour"),
                "round_quality_score": metrics.get("round_quality_score"),
                "safety_score": metrics.get("safety_score"),
                "paper_ab_test_matrix": meta.get("paper_ab_test_matrix") or [],
            })
    except Exception as exc:
        logger.debug("[QAARebateTick] collect active S8 feedback skipped: %s", exc)

    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import RebatePerformanceLogDB

        db = SessionLocal()
        try:
            rows = (
                db.query(RebatePerformanceLogDB)
                .filter(RebatePerformanceLogDB.strategy_type == "S8")
                .order_by(RebatePerformanceLogDB.id.desc())
                .limit(5)
                .all()
            )
            for row in rows:
                hold_hours = float(row.hold_hours or 0)
                points = float(row.total_points or 0)
                feedback["last_closed"].append({
                    "position_id": row.position_id,
                    "points": round(points, 2),
                    "pnl": round(float(row.total_pnl or 0), 4),
                    "rebate": round(float(row.total_rebate or 0), 4),
                    "hold_hours": round(hold_hours, 3),
                    "rh_per_hold_hour": round(points / max(hold_hours, 0.01), 3),
                    "close_reason": row.close_reason,
                })
        finally:
            db.close()
    except Exception as exc:
        logger.debug("[QAARebateTick] collect closed S8 feedback skipped: %s", exc)

    try:
        from backend.services.rebate_arb.s8_param_learner import get_learning_gate

        feedback["learning_gate"] = get_learning_gate(paper_mode=True)
    except Exception:
        feedback["learning_gate"] = {}

    active = feedback["active_positions"]
    gate = feedback.get("learning_gate") or {}
    if active:
        feedback["recommendation"] = "wait_active_position"
        feedback["reason"] = "已有 S8 仓位，等待持仓阶段完成"
    elif gate.get("paper_advisory"):
        feedback["recommendation"] = "continue_paper_learning"
        feedback["reason"] = (
            f"Paper 模拟继续开仓；cash/pt={float(gate.get('cash_per_point') or 0):.4f} "
            f"为负仅作学习告警（{gate.get('samples')} 轮样本）"
        )
    elif gate.get("recovery_mode"):
        feedback["recommendation"] = "paper_experiment"
        feedback["reason"] = (
            f"学习门禁 recovery：cash/pt={float(gate.get('cash_per_point') or 0):.4f}，"
            f"仅悲观 EV>0 且置信度≥{gate.get('recovery_min_ai_confidence') or 70}% 时开仓"
        )
    elif feedback["last_closed"]:
        recent = feedback["last_closed"][0]
        if float(recent.get("pnl") or 0) < -1.0:
            feedback["recommendation"] = "paper_experiment"
            feedback["reason"] = "最近一轮亏损偏高，强制短持低杠杆模式"
        else:
            feedback["recommendation"] = "safe_or_quick_if_signal_strong"
            feedback["reason"] = "最近一轮可作为下一轮质量参考"
    return feedback


def _ensure_qaa_context():
    """获取或 bootstrap QAAContext（含 RebateArbPlugin）。"""
    try:
        from backend.services.full_auto_trading_service import full_auto_trading_service

        if full_auto_trading_service.bootstrap_qaa_v3_context(blocking=True, timeout=20.0):
            ctx = getattr(full_auto_trading_service, "_qaa_ctx", None)
            if ctx is not None:
                return ctx
    except Exception as exc:
        logger.debug("[QAARebateTick] full_auto bootstrap skip: %s", exc)

    try:
        from qaa.core.context import QAAContext
        from qaa.domains.rebate_arb.plugin import RebateArbPlugin
        from qaa.domains.arbitrage.plugin import ArbitragePlugin

        ctx = QAAContext.default()
        ctx.bootstrap(plugins=[RebateArbPlugin(), ArbitragePlugin()])
        return ctx
    except Exception as exc:
        logger.warning("[QAARebateTick] standalone bootstrap failed: %s", exc)
        return None


def run_qaa_rebate_tick(
    *,
    symbols: Optional[List[str]] = None,
    snapshot: Any = None,
    account_equity: Optional[float] = None,
    auto_execute: Optional[bool] = None,
    enabled_strategies: Optional[List[str]] = None,
    trader_profile_id: Optional[int] = None,
    trader_account_id: Optional[int] = None,
    arbitrage_paper_account_id: Optional[int] = None,
    profile_snapshot: Optional[Dict[str, Any]] = None,
    source: str = "fullauto",
) -> Dict[str, Any]:
    global _last_workflow_run, _last_tick_at
    _last_tick_at = time.time()

    try:
        from backend.config.settings import QAA_V3_ENABLED
    except Exception:
        QAA_V3_ENABLED = False

    if not QAA_V3_ENABLED:
        from backend.services.arbitrage.execution_authority import (
            ExecutionAuthority,
            ExecutionSource,
        )

        src = ExecutionSource.FULLAUTO if source == "fullauto" else ExecutionSource.API
        return ExecutionAuthority.run_rebate_tick(
            symbols=symbols,
            snapshot=snapshot,
            account_equity=account_equity,
            auto_execute=auto_execute,
            source=src,
        )

    from backend.services.rebate_arb.tick_context import (
        build_rebate_arb_context,
        build_rebate_tick_context,
    )
    from backend.services.rebate_arb.trader_llm_resolver import resolve_rebate_tick_params

    profile_params = resolve_rebate_tick_params(
        trader_account_id=trader_account_id,
        trader_profile_id=trader_profile_id,
        arbitrage_paper_account_id=arbitrage_paper_account_id,
        profile_snapshot=profile_snapshot,
        enabled_strategies=enabled_strategies,
    )
    resolved_strategies = profile_params.get("enabled_strategies") or []
    resolved_profile_id = profile_params.get("trader_profile_id")

    ctx_data = build_rebate_tick_context(
        symbols=symbols,
        snapshot=snapshot,
        account_equity=account_equity,
    )
    equity = float(ctx_data.get("account_equity") or 0)

    opportunities: List[Dict[str, Any]] = []
    try:
        from backend.services.rebate_arb.engine import rebate_arb_engine

        evals = rebate_arb_engine.scan_all_strategies(
            incentive_data=ctx_data.get("incentive_data") or {},
            funding_rates=ctx_data.get("funding_rates") or {},
            account_equity=equity,
            enabled_strategies=resolved_strategies or None,
        )
        opportunities = [
            {
                "strategy_type": e.strategy_type.value,
                "is_viable": e.is_viable,
                "expected_monthly_value": e.expected_monthly_value,
                "required_volume_usd": e.required_volume_usd,
                "risk_score": e.risk_score,
                "confidence": e.confidence,
                "details": e.details,
            }
            for e in evals
        ]
    except Exception as exc:
        logger.debug("[QAARebateTick] opportunity pre-scan: %s", exc)

    rebate_arb_context = build_rebate_arb_context(ctx_data, profile_params, opportunities)
    viable_count = sum(1 for o in opportunities if o.get("is_viable"))
    top_viable = next((o for o in opportunities if o.get("is_viable")), None)
    s8_feedback = _collect_s8_feedback()

    # auto_execute 未显式指定时读取全局配置（修复旧版硬编码 True 绕过配置的问题）
    if auto_execute is None:
        try:
            from backend.config.rebate_config_loader import rebate_config

            auto_execute = bool(rebate_config.engine.auto_execute)
        except Exception:
            auto_execute = False

    input_data = {
        "symbols": symbols or [],
        "account_equity": equity,
        "auto_execute": bool(auto_execute),
        "enabled_strategies": resolved_strategies,
        "trader_profile_id": resolved_profile_id,
        "profile_params": profile_params,
        "rebate_arb_context": rebate_arb_context,
        "opportunities": [o for o in opportunities if o.get("is_viable")],
        "incentive_data": ctx_data.get("incentive_data"),
        "funding_rates": ctx_data.get("funding_rates"),
        "s8_feedback": s8_feedback,
        "source": source,
    }

    qaa_ctx = _ensure_qaa_context()
    if qaa_ctx is None or not hasattr(qaa_ctx, "tick_orchestrator"):
        from backend.services.arbitrage.execution_authority import (
            ExecutionAuthority,
            ExecutionSource,
        )

        src = ExecutionSource.FULLAUTO if source == "fullauto" else ExecutionSource.API
        return ExecutionAuthority.run_rebate_tick(
            symbols=symbols,
            snapshot=snapshot,
            account_equity=account_equity,
            auto_execute=auto_execute,
            source=src,
        )

    try:
        run = qaa_ctx.tick_orchestrator.run_tick(
            domain="rebate_arb",
            input_data=input_data,
        )
        decision = run.decision if run and getattr(run, "decision", None) else {}
        steps_summary = []
        if run and getattr(run, "steps", None):
            for step in run.steps:
                steps_summary.append({
                    "agent_id": getattr(step, "agent_id", ""),
                    "action": getattr(step, "action", ""),
                    "status": getattr(step, "status", ""),
                    "duration_ms": getattr(step, "duration_ms", 0),
                })

        auto_executed = False
        exec_payload = None
        if input_data.get("auto_execute") and decision.get("action") == "execute":
            strat_id = decision.get("strategy_id") or decision.get("strategy")
            exec_action = decision.get("executor_action")
            size = float(decision.get("size_usd") or equity * 0.15 or 30)
            if (strat_id or "").upper() == "S8":
                try:
                    from backend.services.rebate_arb.capital_coordinator import capital_coordinator
                    from backend.services.rebate_arb.strategies.s8_asterdex_rh import S8AsterdexRhStrategy

                    resolved = S8AsterdexRhStrategy.resolve_target_margin(
                        account_equity=equity,
                        paper_account_id=capital_coordinator.get_arbitrage_paper_account_id(),
                        exchange="asterdex",
                    )
                    size = float(resolved.get("margin_usd") or size)
                except Exception as exc:
                    logger.debug("[QAARebateTick] S8 margin resolve: %s", exc)
            if strat_id and size >= 30:
                try:
                    from backend.services.arbitrage.execution_authority import (
                        ExecutionAuthority,
                        ExecutionSource,
                    )

                    src = ExecutionSource.FULLAUTO if source == "fullauto" else ExecutionSource.QAA
                    if exec_action:
                        exec_payload = ExecutionAuthority.route_qaa_rebate_executor(
                            exec_action,
                            {
                                "size_usd": size,
                                "strategy": strat_id,
                                "opportunity": {
                                    **(decision.get("details") or {}),
                                    "s8_feedback": s8_feedback,
                                },
                                "mode": "paper",
                            },
                        )
                    else:
                        exec_payload = ExecutionAuthority.execute_rebate_strategy(
                            strategy_type=strat_id,
                            size_usd=size,
                            source=src,
                        )
                    auto_executed = bool(exec_payload.get("success") or exec_payload.get("ok"))
                except Exception as exc:
                    logger.warning("[QAARebateTick] post-decision execute: %s", exc)

        # QAA 管线未产出 execute（如卡在 collecting）时，Paper/API 回退直连自动执行
        if (
            input_data.get("auto_execute")
            and not auto_executed
            and viable_count > 0
            and (decision.get("action") or "hold") == "hold"
        ):
            try:
                from backend.services.arbitrage.execution_authority import (
                    ExecutionAuthority,
                    ExecutionSource,
                )

                src = ExecutionSource.FULLAUTO if source == "fullauto" else ExecutionSource.API
                direct = ExecutionAuthority.run_rebate_tick(
                    symbols=symbols,
                    snapshot=snapshot,
                    account_equity=equity,
                    auto_execute=True,
                    source=src,
                )
                if direct.get("auto_executed"):
                    auto_executed = True
                    exec_payload = direct.get("auto_exec_result")
                    logger.info(
                        "[QAARebateTick] direct fallback executed strategy=%s",
                        direct.get("top_auto_strategy") or direct.get("top_strategy"),
                    )
            except Exception as exc:
                logger.warning("[QAARebateTick] direct auto_execute fallback: %s", exc)

        result = {
            "qaa_tick": True,
            "run_id": getattr(run, "run_id", "") if run else "",
            "decision": decision,
            "steps": steps_summary,
            "account_equity": equity,
            "total_evaluated": len(opportunities),
            "viable_count": viable_count,
            "top_strategy": (
                decision.get("strategy_id")
                or decision.get("strategy")
                or (top_viable or {}).get("strategy_type", "")
            ),
            "top_monthly_value": float((top_viable or {}).get("expected_monthly_value") or 0),
            "auto_executed": auto_executed,
            "execution_source": "qaa",
            "auto_exec_result": exec_payload,
            "rebate_arb_context": rebate_arb_context,
            "s8_feedback": s8_feedback,
        }
        _last_workflow_run = {
            "timestamp": time.time(),
            "run_id": result["run_id"],
            "decision": decision,
            "steps": steps_summary,
            "s8_feedback": s8_feedback,
            "source": source,
        }

        # 监控退出 + S8/S3 hold_phase（与直连 tick 一致）
        try:
            from backend.services.arbitrage.execution_authority import ExecutionAuthority, ExecutionSource

            src = ExecutionSource.FULLAUTO if source == "fullauto" else ExecutionSource.API
            from backend.services.rebate_arb.position_monitor import rebate_position_monitor

            # check_exits 内部会 MTM 刷新；此处再保一次活跃仓标记价最新
            try:
                from backend.services.rebate_arb.rebate_position_mtm import refresh_all_paper_positions_mtm

                refresh_all_paper_positions_mtm()
            except Exception:
                pass

            for exit_info in rebate_position_monitor.check_exits():
                ExecutionAuthority.close_rebate_position(
                    exit_info["position_id"],
                    reason=exit_info.get("reason", "auto_exit"),
                    source=src,
                )
            from backend.services.rebate_arb.engine import rebate_arb_engine

            completed = rebate_arb_engine.check_and_advance_hold_phases()
            result["hold_phases_completed"] = completed or []
        except Exception as exc:
            logger.debug("[QAARebateTick] post-tick monitor: %s", exc)

        return result
    except Exception as exc:
        logger.error("[QAARebateTick] run_tick failed: %s", exc, exc_info=True)
        from backend.services.arbitrage.execution_authority import (
            ExecutionAuthority,
            ExecutionSource,
        )

        src = ExecutionSource.FULLAUTO if source == "fullauto" else ExecutionSource.API
        fallback = ExecutionAuthority.run_rebate_tick(
            symbols=symbols,
            snapshot=snapshot,
            account_equity=account_equity,
            auto_execute=auto_execute,
            source=src,
        )
        fallback["qaa_tick"] = False
        fallback["qaa_error"] = str(exc)
        return fallback
