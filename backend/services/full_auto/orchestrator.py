"""
FullAutoOrchestrator — 整改#8 Phase2。

仅负责循环注册/注销与调度分发，不含交易业务逻辑。
monolith 保留执行层辅助方法，通过本模块统一接线各 loop。
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from backend.services.full_auto_trading_service import FullAutoTradingService

logger = logging.getLogger(__name__)


class FullAutoOrchestrator:
    """全自动驾驶循环编排器（依赖注入 + 调度，无业务逻辑）。"""

    def __init__(self, svc: "FullAutoTradingService") -> None:
        self.svc = svc

    # ── 会话循环注册 ─────────────────────────────────────────────

    def register_session(self, session_id: str, interval_seconds: int) -> None:
        """注册统一循环 + scalp + midlong + OrchBG（从 monolith 迁出）。"""
        svc = self.svc
        try:
            from backend.services.scheduler import task_scheduler
            from backend.services.paper_pace_controller import paper_pace_controller

            if not hasattr(svc, "_session_intervals"):
                svc._session_intervals = {}
            svc._session_intervals[session_id] = interval_seconds

            if not getattr(svc, "_pace_cb_registered", False):
                paper_pace_controller.register_gear_change_callback(svc._on_pace_gear_change)
                svc._pace_cb_registered = True

            unified_id = f"fullauto_unified_{session_id}"
            self.unregister_session(session_id)
            if session_id not in svc._unified_tick_count:
                svc._unified_tick_count[session_id] = 0

            task_scheduler.start()
            effective_interval = svc._resolve_unified_tick_interval(interval_seconds)
            tick_sec = max(15, int(effective_interval or paper_pace_controller.get_tick_seconds()))
            task_scheduler.add_interval_task(
                task_func=svc._run_unified_loop_safe,
                interval_seconds=tick_sec,
                task_id=unified_id,
                max_instances=1,
                session_id=session_id,
            )
            logger.info(
                "[FullAutoOrchestrator] 注册统一循环: %s (%ss, tick=%s)",
                unified_id, tick_sec, svc._unified_tick_count[session_id],
            )
            self.register_scalp_loop(session_id, tick_sec)
            self.register_midlong_loop(session_id)

            _sess_info = svc._running_sessions.get(session_id, {})
            try:
                from backend.database.connection import SessionLocal
                from backend.database.models import FullAutoSession

                _db = SessionLocal()
                try:
                    _sess = _db.query(FullAutoSession).filter(
                        FullAutoSession.session_id == session_id
                    ).first()
                    _syms = (
                        svc._resolve_session_trade_symbols(_sess, _db)
                        if _sess else list(_sess_info.get("symbols") or [])
                    )
                finally:
                    _db.close()
            except Exception:
                _syms = list(_sess_info.get("symbols") or [])

            if _syms:
                try:
                    svc._ensure_orchestrator_bg_running(session_id, _syms)
                except Exception as _obg_err:
                    logger.warning("[FullAutoOrchestrator] OrchBG 接线失败 %s: %s", session_id, _obg_err)
                # 后台预热分析师报告缓存，消除首次 synthesize 36s 冷启动
                try:
                    svc._warmup_analyst_reports(_syms)
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("[FullAutoOrchestrator] 注册统一循环失败: %s", exc)

    def register_scalp_loop(self, session_id: str, unified_tick_sec: int) -> None:
        svc = self.svc
        try:
            from dotenv import load_dotenv as _ld

            _ld(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"), override=True)
            _scalp_enabled = os.getenv("SCALP_FACTOR_INDEPENDENT_SCHEDULER", "true").lower() in (
                "1", "true", "yes", "on",
            )
            if not _scalp_enabled:
                logger.info("[FullAutoOrchestrator] scalp 独立调度已禁用")
                return

            svc._warmup_scalp_factor_engine()
            from backend.config.settings import SCALP_FACTOR_SCAN_INTERVAL_SEC
            from backend.services.scheduler import task_scheduler

            scalp_id = f"fullauto_scalp_{session_id}"
            self._unregister_scalp(session_id)
            if session_id not in svc._scalp_tick_count:
                svc._scalp_tick_count[session_id] = 0
            task_scheduler.start()
            scalp_sec = (
                int(SCALP_FACTOR_SCAN_INTERVAL_SEC)
                if int(SCALP_FACTOR_SCAN_INTERVAL_SEC or 0) > 0
                else int(unified_tick_sec)
            )
            scalp_sec = max(10, scalp_sec)
            task_scheduler.add_interval_task(
                task_func=svc._run_scalp_loop_safe,
                interval_seconds=scalp_sec,
                task_id=scalp_id,
                max_instances=1,
                session_id=session_id,
            )
            logger.info(
                "[FullAutoOrchestrator] 注册 scalp 循环: %s (%ss)",
                scalp_id, scalp_sec,
            )
        except Exception as exc:
            logger.warning("[FullAutoOrchestrator] 注册 scalp 循环失败: %s", exc)

    def register_midlong_loop(self, session_id: str) -> None:
        svc = self.svc
        try:
            from dotenv import load_dotenv as _ld

            _ld(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"), override=True)
            _enabled = os.getenv("MIDLONG_AGENT_INDEPENDENT_SCHEDULER", "true").lower() in (
                "1", "true", "yes", "on",
            )
            if not _enabled:
                logger.info("[FullAutoOrchestrator] midlong 独立调度已禁用")
                return

            from backend.config.settings import TIER_MID_AI_TICK_SEC
            from backend.services.scheduler import task_scheduler

            midlong_id = f"fullauto_midlong_{session_id}"
            self._unregister_midlong(session_id)
            if session_id not in svc._midlong_tick_count:
                svc._midlong_tick_count[session_id] = 0
            task_scheduler.start()
            interval = max(45, int(TIER_MID_AI_TICK_SEC or 45))
            # MidLong v2 Phase3：相对 Master 主循环错峰，降低 LLM/DB 同秒争抢
            _next_run = None
            _stagger = 0
            try:
                from datetime import datetime, timedelta
                from backend.config.settings import MIDLONG_MASTER_STAGGER_SEC
                _stagger = max(0, int(MIDLONG_MASTER_STAGGER_SEC or 0))
                if _stagger > 0:
                    _next_run = datetime.now() + timedelta(seconds=60 + _stagger)
            except Exception:
                _next_run = None
                _stagger = 0
            task_scheduler.add_interval_task(
                task_func=svc._run_midlong_loop_safe,
                interval_seconds=interval,
                task_id=midlong_id,
                max_instances=1,
                next_run_time=_next_run,
                session_id=session_id,
            )
            logger.info(
                "[FullAutoOrchestrator] 注册 midlong 循环: %s (%ss) stagger=%ss",
                midlong_id, interval, _stagger,
            )
        except Exception as exc:
            logger.warning("[FullAutoOrchestrator] 注册 midlong 循环失败: %s", exc)

    def unregister_session(self, session_id: str) -> None:
        svc = self.svc
        try:
            from backend.services.scheduler import task_scheduler

            for prefix in ("fullauto_unified_", "fullauto_hc_", "fullauto_monitor_"):
                try:
                    task_scheduler.remove_task(f"{prefix}{session_id}")
                except Exception:
                    pass
            self._unregister_scalp(session_id)
            self._unregister_midlong(session_id)
            svc._unified_tick_count.pop(session_id, None)
        except Exception as exc:
            logger.warning("[FullAutoOrchestrator] 注销循环失败: %s", exc)

    def _unregister_scalp(self, session_id: str) -> None:
        svc = self.svc
        try:
            from backend.services.scheduler import task_scheduler

            task_scheduler.remove_task(f"fullauto_scalp_{session_id}")
            svc._scalp_tick_count.pop(session_id, None)
            svc._scalp_loop_running.pop(session_id, None)
            svc._scalp_loop_started.pop(session_id, None)
        except Exception as exc:
            logger.warning("[FullAutoOrchestrator] 注销 scalp 失败: %s", exc)

    def _unregister_midlong(self, session_id: str) -> None:
        svc = self.svc
        try:
            from backend.services.scheduler import task_scheduler

            task_scheduler.remove_task(f"fullauto_midlong_{session_id}")
            svc._midlong_tick_count.pop(session_id, None)
            svc._midlong_loop_running.pop(session_id, None)
            svc._midlong_loop_started.pop(session_id, None)
        except Exception as exc:
            logger.warning("[FullAutoOrchestrator] 注销 midlong 失败: %s", exc)

    # ── Loop 分发（thin shim 目标）────────────────────────────────

    def dispatch_unified(self, session_id: str) -> None:
        from backend.services.full_auto.loops.coordinator_loop import run_unified_loop
        run_unified_loop(self.svc, session_id)

    def dispatch_scalp(self, session_id: str, tick: int) -> None:
        from backend.services.full_auto.loops.scalp_loop import run_scalp_independent
        run_scalp_independent(self.svc, session_id, tick)

    def dispatch_midlong(self, session_id: str, tick: int) -> None:
        from backend.services.full_auto.loops.midlong_loop import run_midlong_independent
        run_midlong_independent(self.svc, session_id, tick)

    def dispatch_maintenance(self, session_id: str) -> None:
        from backend.services.full_auto.loops.maintenance_loop import run_maintenance_cycle
        run_maintenance_cycle(self.svc, session_id)

    def get_loop_stats(self) -> Dict[str, Any]:
        svc = self.svc
        return {
            "unified_sessions": len(svc._unified_tick_count),
            "scalp_sessions": len(getattr(svc, "_scalp_tick_count", {})),
            "midlong_sessions": len(getattr(svc, "_midlong_tick_count", {})),
            "orch_bg_running": bool(getattr(svc, "_orch_bg_running", False)),
        }


def get_orchestrator(svc: "FullAutoTradingService") -> FullAutoOrchestrator:
    orch = getattr(svc, "_full_auto_orchestrator", None)
    if orch is None:
        orch = FullAutoOrchestrator(svc)
        svc._full_auto_orchestrator = orch
    return orch
