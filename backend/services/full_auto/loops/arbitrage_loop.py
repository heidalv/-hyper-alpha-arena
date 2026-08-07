"""
套利域循环（整改#8 arbitrage_loop 拆分）。
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from backend.services.full_auto_trading_service import FullAutoTradingService

logger = logging.getLogger(__name__)


def record_arb_tick_error(svc: "FullAutoTradingService", domain: str, exc: Exception) -> None:
    """套利 tick 异常计数 + warning 可见化（供健康检查读取）。"""
    self = svc
    if not hasattr(self, "_arb_tick_errors"):
        self._arb_tick_errors: Dict[str, Dict[str, Any]] = {}
    slot = self._arb_tick_errors.setdefault(
        domain, {"count": 0, "last_error": "", "last_at": 0.0}
    )
    slot["count"] += 1
    slot["last_error"] = str(exc)
    slot["last_at"] = time.time()
    logger.warning(
        "[ArbTick:%s] tick 异常 #%s: %s", domain, slot["count"], exc
    )



def run_arbitrage_tick(svc: "FullAutoTradingService", session_id: str) -> None:
    """套利域并行 Tick。"""
    self = svc
    # 检查套利开关
    _sinfo = self._running_sessions.get(session_id, {})
    if not _sinfo.get("arb_enabled", False):
        return

    from backend.config.settings import FUNDING_ARB_ENABLED
    if not FUNDING_ARB_ENABLED:
        return

    # 延迟初始化 Orchestrator（单例）
    if not hasattr(self, '_arb_orchestrator') or self._arb_orchestrator is None:
        try:
            from backend.services.arbitrage.orchestrator import arbitrage_orchestrator
            self._arb_orchestrator = arbitrage_orchestrator
        except ImportError:
            return  # 套利模块不可用时静默跳过

    # 获取缓存的 snapshot（已在交易循环中捕获）
    snapshot = getattr(self, '_last_unified_snapshot', None)
    if snapshot is None:
        return

    symbols = []
    session = self._get_session_fast(session_id)
    if session:
        symbols = list(getattr(session, 'symbols', None) or [])

    if not symbols:
        return

    try:
        # 获取 exchange_manager（用于 Live 模式和跨交易所扫描）
        exchange_manager = None
        try:
            from backend.services.exchange.exchange_manager import get_exchange_manager
            exchange_manager = get_exchange_manager()
        except Exception:
            pass

        # 执行完整 tick（经 ExecutionAuthority 统一路由 + mid 缓存刷新）
        from backend.services.arbitrage.execution_authority import (
            ExecutionAuthority,
            ExecutionSource,
        )
        result = ExecutionAuthority.run_v3_arbitrage_tick(
            symbols=symbols,
            snapshot=snapshot,
            exchange_manager=exchange_manager,
            source=ExecutionSource.FULLAUTO,
        )

        if result.get("scanned", 0) > 0 or result.get("error"):
            logger.info(
                f"[ArbTick] tick={result.get('tick', 0)} "
                f"scanned={result.get('scanned', 0)} "
                f"risk_passed={result.get('risk_passed', 0)} "
                f"executed={result.get('executed', 0)} "
                f"monitored={result.get('monitored', 0)} "
                f"mode={result.get('mode', 'paper')} "
                f"elapsed={result.get('elapsed_ms', 0)}ms"
            )

    except Exception as e:
        record_arb_tick_error(self, "v3", e)



def run_rebate_arb_tick(svc: "FullAutoTradingService", session_id: str) -> None:
    """积分/返利套利域 Tick。"""
    self = svc
    _sinfo = self._running_sessions.get(session_id, {})
    if not _sinfo.get("arb_enabled", False):
        return

    try:
        from backend.services.arbitrage.execution_authority import (
            ExecutionAuthority,
            ExecutionSource,
        )
    except ImportError:
        return

    snapshot = getattr(self, "_last_unified_snapshot", None)
    symbols = list(_sinfo.get("symbols") or [])
    if not symbols:
        session = self._get_session_fast(session_id)
        if session:
            symbols = list(getattr(session, "symbols", None) or [])

    try:
        from backend.services.rebate_arb.qaa_rebate_tick import run_qaa_rebate_tick

        arb_profile = _sinfo.get("arbitrage_profile") or {}
        result = run_qaa_rebate_tick(
            symbols=symbols,
            snapshot=snapshot,
            source="fullauto",
            enabled_strategies=arb_profile.get("enabled_strategies"),
            trader_profile_id=arb_profile.get("profile_id"),
            trader_account_id=_sinfo.get("account_id"),
            profile_snapshot=arb_profile if arb_profile else None,
        )
        self._last_rebate_arb_context = result.get("rebate_arb_context") or {}
        if result.get("viable_count", 0) > 0:
            logger.info(
                f"[RebateArbTick] viable={result['viable_count']} "
                f"top={result.get('top_strategy')} "
                f"${result.get('top_monthly_value', 0):.0f}/月 "
                f"auto={result.get('auto_executed', False)}"
            )
    except Exception as e:
        record_arb_tick_error(self, "rebate", e)

