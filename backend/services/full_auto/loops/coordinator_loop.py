"""
协调器统一循环（整改#8 coordinator_loop 拆分）。

从 full_auto_trading_service._run_unified_loop 迁出；
monolith 保留 thin shim 转发。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.services.full_auto_trading_service import FullAutoTradingService

logger = logging.getLogger(__name__)


def run_unified_loop(svc: "FullAutoTradingService", session_id: str) -> None:
    """统一调度入口（协调器心跳，默认 30–45s）。"""
    from backend.services.resource_guard import hot_path_context

    with hot_path_context("unified_tick"):
        _run_unified_loop_inner(svc, session_id)


def _run_unified_loop_inner(svc: "FullAutoTradingService", session_id: str) -> None:
    self = svc
    # [C1] 协调器心跳是 APScheduler 后台循环,不在 HTTP 请求上下文。设 system_identity
    # 覆盖整轮(含 _pul_db 及下方调用的 _run_trading_cycle / _run_maintenance_cycle
    # 等子循环各自开的 SessionLocal),否则非超用户 DB 角色下 RLS fail-closed 破坏交易。
    from backend.core.tenant import set_system_identity
    set_system_identity()
    from backend.config.settings import (
        FULLAUTO_FLOW_MODE,
        FULLAUTO_MAINTENANCE_EVERY_N_TICKS,
        QAA_MODE,
        QAA_V3_ENABLED,
    )

    tick = self._unified_tick_count.get(session_id, 0) + 1
    self._unified_tick_count[session_id] = tick

    try:
        from backend.services.symbol_lock_registry import lock_registry
        lock_registry.cleanup_expired()
    except Exception:
        pass
    session_status = self._get_session_status_fast(session_id)

    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import FullAutoSession
        _pul_db = SessionLocal()
        try:
            _pul_sess = _pul_db.query(FullAutoSession).filter(
                FullAutoSession.session_id == session_id
            ).first()
            if _pul_sess and self._paper_loss_locks_disabled(_pul_sess):
                # reset_loss_protection_state 的 paused→running 门控已下沉到
                # paper_auto_unlock_session 内部(paper_session_helpers.py),
                # 此处保持每 tick 无条件调用,使其它 6 个副作用(策略恢复/解冻/
                # 重入冷却清除/MT-freeze 清除等)行为不变(根因1止血 issue#1)。
                if self._paper_auto_unlock_session(_pul_db, _pul_sess):
                    self._safe_commit(_pul_db, "paper_unlock_tick", session=_pul_sess)
                    session_status = _pul_sess.status
        finally:
            _pul_db.close()
    except Exception as _pul_err:
        logger.debug(f"[FullAuto] paper unlock tick skip: {_pul_err}")

    if session_status == "paused":
        return

    if FULLAUTO_FLOW_MODE == "ai_first":
        from backend.services.tier_tick_scheduler import (
            format_skip_reason,
            get_due_ai_tiers,
            mark_coordinator_run,
            mark_tier_run,
        )
        from backend.config.settings import TIER_TICK_SCHEDULER_ENABLED

        mark_coordinator_run(session_id)
        due_tiers: list = []
        if TIER_TICK_SCHEDULER_ENABLED:
            due_tiers = get_due_ai_tiers(session_id)
        else:
            # 回退路径同样尊重 TIER_MID_ENABLED / TIER_LONG_ENABLED 开关
            from backend.config.settings import TIER_MID_ENABLED, TIER_LONG_ENABLED
            due_tiers = []
            if TIER_MID_ENABLED:
                due_tiers.append("mid")
            if TIER_LONG_ENABLED:
                due_tiers.append("long")

        if session_status in ("running", "defensive") and due_tiers:
            logger.info(
                f"[FullAuto] tick#{tick} 🧠AI周期 {session_id} "
                f"tiers={due_tiers} (status={session_status})"
            )
            try:
                self._run_trading_cycle(session_id, ai_tiers=due_tiers)
                # [中长线合并] 主循环已把 mid/long LLM+开仓委派给独立循环
                # （_skip_agent_llm=True），不再标记 tier_run，否则独立循环
                # 会因 due 被清空而提前空转（"due 为空，提前 return"）。
                # 独立循环按自身 job 周期 + batch 轮换稳定调度。
                mark_tier_run(session_id, [t for t in due_tiers if t == "short"])
            except Exception as _tc_err:
                logger.error(f"[FullAuto] AI周期异常: {_tc_err}", exc_info=True)
        elif session_status in ("running", "defensive"):
            logger.debug(
                f"[FullAuto] tick#{tick} 轻量协调 {session_id} "
                f"({format_skip_reason(session_id)})"
            )
        if tick % max(1, FULLAUTO_MAINTENANCE_EVERY_N_TICKS) == 0:
            if session_status in ("running", "defensive", "paused"):
                logger.info(
                    f"[FullAuto] tick#{tick} 🔧维护循环 {session_id}"
                )
                self._run_maintenance_cycle(session_id)
        self._run_hold_timeout_ai_review_if_needed(session_id)

        try:
            self._run_arbitrage_tick(session_id)
        except Exception as _arb_err:
            logger.debug(f"[FullAuto] 套利 tick 异常（不影响主循环）: {_arb_err}")

        try:
            self._run_rebate_arb_tick(session_id)
        except Exception as _arb_err:
            logger.debug(f"[FullAuto] 积分套利 tick 异常（不影响主循环）: {_arb_err}")

        try:
            from backend.config.settings import (
                FULLAUTO_LEARNING_INTEGRATION_EVERY_N,
                PAPER_FAST_TRIAL,
            )
            _learn_every = max(1, int(FULLAUTO_LEARNING_INTEGRATION_EVERY_N or 5))
        except Exception:
            _learn_every = 5
            PAPER_FAST_TRIAL = False
        if tick % _learn_every == 0:
            self._run_learning_integration(session_id, tick)
            if PAPER_FAST_TRIAL:
                from backend.services.paper_fast_trial_controller import mlto_learning_tick_enabled
                if mlto_learning_tick_enabled():
                    self._run_mlto_learning_tick(session_id)

        return
