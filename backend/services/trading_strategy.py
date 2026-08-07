"""
AI trading strategy trigger management with simplified logic.

Supports parallel execution of multiple AI traders via ThreadPoolExecutor.
Each trader runs in its own thread — one slow LLM call won't block others.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional, Any, List

from backend.database.connection import SessionLocal
from backend.database.models import Account, AccountStrategyConfig, GlobalSamplingConfig
from sqlalchemy import text
from backend.repositories.strategy_repo import (
    get_strategy_by_account,
    list_strategies,
    upsert_strategy,
)
from backend.services.sampling_pool import sampling_pool
from backend.services.trading_commands import (
    place_ai_driven_crypto_order,
    place_ai_driven_hyperliquid_order,
)
from backend.services.hyperliquid_symbol_service import get_selected_symbols as get_hyperliquid_selected_symbols

logger = logging.getLogger(__name__)

STRATEGY_REFRESH_INTERVAL = 60.0  # seconds


def _as_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure stored timestamps are timezone-aware UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        return dt.replace(tzinfo=local_tz).astimezone(timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class StrategyState:
    account_id: int
    price_threshold: float  # Deprecated, kept for compatibility
    trigger_interval: int   # Trigger interval (seconds) - scheduled trigger fallback
    signal_pool_id: Optional[int]  # Signal pool binding for signal-based triggering
    enabled: bool
    last_trigger_at: Optional[datetime]
    running: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)
    # Minimum interval between any two triggers (signal or scheduled) to prevent rapid re-triggers
    min_trigger_interval: int = 60  # seconds

    def should_trigger_scheduled(self, event_time: datetime) -> bool:
        """Check if strategy should trigger based on scheduled time interval (fallback)"""
        if not self.enabled:
            return False

        # Quick check without lock to avoid unnecessary contention
        if self.running:
            return False

        with self.lock:
            # Double-check after acquiring lock
            if self.running:
                return False

            now_ts = event_time.timestamp()
            last_ts = self.last_trigger_at.timestamp() if self.last_trigger_at else 0
            time_diff = now_ts - last_ts

            # Check minimum interval between any triggers (prevents rapid re-triggers)
            if time_diff < self.min_trigger_interval:
                return False

            # Check time interval trigger (scheduled fallback)
            if time_diff >= self.trigger_interval:
                self.last_trigger_at = event_time
                self.running = True
                logger.info(
                    f"Strategy scheduled trigger for account {self.account_id}: "
                    f"Time interval ({time_diff:.1f}s / {self.trigger_interval}s)"
                )
                return True

            return False

    def mark_triggered_by_signal(self, event_time: datetime) -> bool:
        """Mark strategy as triggered by signal (called from signal callback)

        Signal triggers have priority over scheduled triggers but still respect
        the minimum interval to prevent rapid re-triggers.
        """
        if not self.enabled:
            return False

        with self.lock:
            if self.running:
                return False

            # Respect minimum interval even for signal triggers
            now_ts = event_time.timestamp()
            last_ts = self.last_trigger_at.timestamp() if self.last_trigger_at else 0
            time_diff = now_ts - last_ts

            if time_diff < self.min_trigger_interval:
                logger.debug(
                    f"Signal trigger for account {self.account_id} skipped: "
                    f"min interval not reached ({time_diff:.1f}s / {self.min_trigger_interval}s)"
                )
                return False

            self.last_trigger_at = event_time
            self.running = True
            return True


MAX_PARALLEL_TRADERS = 10


class StrategyManager:
    def __init__(self):
        self.strategies: Dict[int, StrategyState] = {}
        self.lock = threading.Lock()
        self.running = False
        self.refresh_thread: Optional[threading.Thread] = None
        self._executor: Optional[ThreadPoolExecutor] = None

    def start(self):
        """Start the strategy manager"""
        with self.lock:
            if self.running:
                logger.warning("Strategy manager already running")
                return

            self.running = True
            self._executor = ThreadPoolExecutor(
                max_workers=MAX_PARALLEL_TRADERS,
                thread_name_prefix="ai-trader",
            )
            # 后台线程无 HTTP 租户上下文：用系统身份穿透 RLS，否则策略 0 加载
            from backend.core.tenant import system_identity
            with system_identity():
                self._load_strategies()

            self.refresh_thread = threading.Thread(
                target=self._refresh_strategies_loop,
                daemon=True
            )
            self.refresh_thread.start()

            logger.info(
                f"Strategy manager started (parallel execution: up to {MAX_PARALLEL_TRADERS} traders)"
            )

    def stop(self):
        """Stop the strategy manager and wait for running trades to finish"""
        with self.lock:
            if not self.running:
                return
            self.running = False

        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=False)
            self._executor = None
            logger.info("Strategy thread pool shut down")

        if self.refresh_thread:
            self.refresh_thread.join(timeout=5.0)

        logger.info("Strategy manager stopped")

    def _load_strategies(self):
        """Load strategies from database, preserving running state for in-progress executions"""
        try:
            # PostgreSQL handles concurrent access natively
            db = SessionLocal()
            try:
                rows = (
                    db.query(AccountStrategyConfig, Account)
                    .join(Account, AccountStrategyConfig.account_id == Account.id)
                    .all()
                )

                # Preserve running states from existing strategies before clearing
                running_states: Dict[int, bool] = {}
                for aid, old_state in self.strategies.items():
                    if old_state.running:
                        running_states[aid] = True

                self.strategies.clear()
                for strategy, account in rows:
                    state = StrategyState(
                        account_id=strategy.account_id,
                        price_threshold=strategy.price_threshold,
                        trigger_interval=strategy.trigger_interval,
                        signal_pool_id=strategy.signal_pool_id,
                        enabled=strategy.enabled == "true",
                        last_trigger_at=_as_aware(strategy.last_trigger_at),
                    )
                    # Restore running state if strategy was mid-execution
                    if strategy.account_id in running_states:
                        state.running = True
                    self.strategies[strategy.account_id] = state

                    # DEBUG: Print loaded strategy configuration
                    print(
                        f"[DEBUG] Loaded strategy for account {strategy.account_id} ({account.name}): "
                        f"interval={strategy.trigger_interval}s ({strategy.trigger_interval/60:.1f}min), "
                        f"signal_pool_id={strategy.signal_pool_id}, enabled={strategy.enabled}, "
                        f"last_trigger={state.last_trigger_at}"
                    )

                logger.info(f"Loaded {len(self.strategies)} strategies")
            finally:
                db.close()

        except Exception as e:
            logger.error(f"Failed to load strategies: {e}")
            # Don't retry immediately on database lock
            if "database is locked" in str(e):
                logger.warning("Database locked, skipping strategy refresh")

    def _refresh_strategies_loop(self):
        """Periodically refresh strategies from database"""
        while self.running:
            try:
                time.sleep(STRATEGY_REFRESH_INTERVAL)
                if self.running:
                    from backend.core.tenant import system_identity
                    with system_identity():
                        self._load_strategies()
            except Exception as e:
                logger.error(f"Error in strategy refresh loop: {e}")

    def get_strategy_status(self) -> Dict[str, Any]:
        """Get current strategy manager status"""
        strategies_info = []
        for account_id, state in self.strategies.items():
            strategies_info.append({
                "account_id": account_id,
                "enabled": state.enabled,
                "running": state.running,
                "trigger_interval": state.trigger_interval,
                "signal_pool_id": state.signal_pool_id,
                "last_trigger_at": state.last_trigger_at.isoformat() if state.last_trigger_at else None,
            })
        
        return {
            "manager_running": self.running,
            "loaded_strategies_count": len(self.strategies),
            "strategies": strategies_info,
        }

    def handle_price_update(self, symbol: str, price: float, event_time: datetime):
        """Handle price update and dispatch strategy triggers to thread pool.

        This method is called from the market data WebSocket thread and must return
        quickly.  Actual AI decision + trade execution runs in the thread pool so
        multiple traders execute in parallel without blocking each other or the
        market data stream.
        """
        try:
            # Add to sampling pool if needed
            with SessionLocal() as db:
                global_config = db.query(GlobalSamplingConfig).first()
                sampling_interval = global_config.sampling_interval if global_config else 18

            if sampling_pool.should_sample(symbol, sampling_interval):
                sampling_pool.add_sample(symbol, price, event_time.timestamp())

            # Check each strategy and dispatch to thread pool (non-blocking)
            for account_id, state in list(self.strategies.items()):
                if state.should_trigger_scheduled(event_time):
                    scheduled_trigger_context = {
                        "trigger_type": "scheduled",
                        "trigger_interval": state.trigger_interval,
                    }
                    if self._executor:
                        self._executor.submit(
                            self._execute_strategy,
                            account_id, symbol, event_time,
                            "scheduled", scheduled_trigger_context,
                        )
                    else:
                        # Fallback: synchronous if pool not available
                        self._execute_strategy(
                            account_id, symbol, event_time,
                            trigger_type="scheduled",
                            trigger_context=scheduled_trigger_context,
                        )

        except Exception as e:
            logger.error(f"Error handling price update for {symbol}: {e}")

    def _execute_strategy(
        self,
        account_id: int,
        symbol: str,
        event_time: datetime,
        trigger_type: str = "scheduled",
        trigger_context: Optional[Dict[str, Any]] = None
    ):
        """Execute strategy for account with trigger context"""
        state = self.strategies.get(account_id)
        if not state:
            return

        # Note: running state and timestamp already set in should_trigger or mark_triggered_by_signal
        try:
            # Immediately persist timestamp to database (before AI call)
            with SessionLocal() as db:
                strategy = db.query(AccountStrategyConfig).filter_by(account_id=account_id).first()
                if strategy:
                    strategy.last_trigger_at = event_time
                    db.commit()
                    logger.info(
                        f"Strategy execution started for account {account_id} (trigger: {trigger_type}), "
                        f"next scheduled trigger in {strategy.trigger_interval}s ({strategy.trigger_interval/60:.1f}min)"
                    )

            # Check account configuration
            with SessionLocal() as db:
                account = db.query(Account).filter(Account.id == account_id).first()
                if not account or account.auto_trading_enabled != "true":
                    logger.debug(f"Account {account_id} auto trading disabled, skipping strategy execution")
                    return

            # Execute AI trading decision with trigger context
            logger.info(f"Account {account_id} executing Hyperliquid trading (trigger: {trigger_type})")
            from services.trading_commands import place_ai_driven_hyperliquid_order
            place_ai_driven_hyperliquid_order(account_id=account_id, trigger_context=trigger_context)

        except Exception as e:
            logger.error(f"Error executing strategy for account {account_id}: {e}")
        finally:
            # Always reset running state
            state.running = False

    def get_strategy_status(self) -> Dict[str, Any]:
        """Get status of all strategies including per-trader execution state."""
        active_count = sum(1 for s in self.strategies.values() if s.running)
        pool_info = {}
        if self._executor:
            pool_info = {
                "max_parallel": MAX_PARALLEL_TRADERS,
                "active_threads": active_count,
            }

        status: Dict[str, Any] = {
            "running": self.running,
            "strategy_count": len(self.strategies),
            "parallel_execution": pool_info,
            "strategies": {},
        }

        for account_id, state in self.strategies.items():
            status["strategies"][account_id] = {
                "enabled": state.enabled,
                "executing": state.running,
                "trigger_interval": state.trigger_interval,
                "signal_pool_id": state.signal_pool_id,
                "last_trigger_at": state.last_trigger_at.isoformat() if state.last_trigger_at else None,
            }

        return status


# Hyperliquid-only strategy manager with signal pool support
class HyperliquidStrategyManager(StrategyManager):
    def __init__(self):
        super().__init__()
        self._signal_callback_registered = False

    def _load_strategies(self):
        """重写：加载所有已启用且配置了 Hyperliquid 的账户策略，保留运行中状态

        Loading conditions:
        - Account.is_active == "true"
        - Account.auto_trading_enabled == "true"
        - Account.hyperliquid_enabled == "true"
        - AccountStrategyConfig.enabled == "true"
        """
        try:
            db = SessionLocal()
            try:
                # Diagnostic: check why strategies might be 0
                active_accounts = db.query(Account).filter(
                    Account.is_active == "true",
                    Account.auto_trading_enabled == "true",
                ).all()
                for acc in active_accounts:
                    if acc.hyperliquid_enabled != "true":
                        logger.info(
                            f"[Hyperliquid策略] 账户 {acc.id} ({acc.name}) "
                            f"is_active={acc.is_active}, auto_trading={acc.auto_trading_enabled}, "
                            f"hyperliquid_enabled={acc.hyperliquid_enabled} → 跳过（需在UI中启用Hyperliquid）"
                        )

                rows = (
                    db.query(AccountStrategyConfig, Account)
                    .join(Account, AccountStrategyConfig.account_id == Account.id)
                    .filter(
                        Account.is_active == "true",
                        Account.auto_trading_enabled == "true",
                        Account.hyperliquid_enabled == "true",
                        AccountStrategyConfig.enabled == "true",
                    )
                    .all()
                )

                # Preserve running states from existing strategies before clearing
                running_states: Dict[int, bool] = {}
                for aid, old_state in self.strategies.items():
                    if old_state.running:
                        running_states[aid] = True

                self.strategies.clear()
                for strategy, account in rows:
                    state = StrategyState(
                        account_id=strategy.account_id,
                        price_threshold=strategy.price_threshold,
                        trigger_interval=strategy.trigger_interval,
                        signal_pool_id=strategy.signal_pool_id,
                        enabled=True,
                        last_trigger_at=_as_aware(strategy.last_trigger_at),
                    )
                    # Restore running state if strategy was mid-execution
                    if strategy.account_id in running_states:
                        state.running = True
                    self.strategies[strategy.account_id] = state

                    logger.debug(
                        f"[Hyperliquid策略] 已加载账户 {strategy.account_id} ({account.name}): "
                        f"interval={strategy.trigger_interval}s, "
                        f"signal_pool_id={strategy.signal_pool_id}"
                    )

                logger.info(f"[Hyperliquid策略] 已加载 {len(self.strategies)} 个策略")
            finally:
                db.close()

        except Exception as e:
            logger.error(f"[Hyperliquid策略] 加载策略失败: {e}")
            if "database is locked" in str(e):
                logger.warning("[Hyperliquid策略] 数据库被锁定，跳过策略刷新")

    def start(self):
        """Start the strategy manager and register signal callback"""
        super().start()
        self._register_signal_callback()

    def stop(self):
        """Stop the strategy manager and unregister signal callback"""
        self._unregister_signal_callback()
        super().stop()

    def _register_signal_callback(self):
        """注册信号检测服务回调"""
        logger.warning("[Hyperliquid策略] 正在注册信号触发回调")
        if self._signal_callback_registered:
            logger.warning("[Hyperliquid策略] 回调已注册，跳过")
            return
        try:
            from services.signal_detection_service import signal_detection_service
            logger.warning(f"[Hyperliquid策略] 注册前回调数量: {len(signal_detection_service._trigger_callbacks)}")
            signal_detection_service.subscribe_signal_triggers(self._on_signal_triggered)
            self._signal_callback_registered = True
            logger.warning(f"[Hyperliquid策略] 信号触发回调已注册！注册后回调数量: {len(signal_detection_service._trigger_callbacks)}")
        except Exception as e:
            logger.error(f"[Hyperliquid策略] 注册信号回调失败: {e}", exc_info=True)

    def _unregister_signal_callback(self):
        """Unregister callback from signal detection service"""
        if not self._signal_callback_registered:
            return
        try:
            from services.signal_detection_service import signal_detection_service
            signal_detection_service.unsubscribe_signal_triggers(self._on_signal_triggered)
            self._signal_callback_registered = False
            logger.info("[Hyperliquid策略] 信号触发回调已注销")
        except Exception as e:
            logger.error(f"[Hyperliquid策略] 注销信号回调失败: {e}")

    def _on_signal_triggered(self, symbol: str, pool: dict, market_data: dict, triggered_signals: list):
        """Callback when a signal pool triggers — dispatches bound strategies to thread pool."""
        pool_id = pool.get("id")
        pool_name = pool.get("pool_name", "Unknown")
        event_time = datetime.now(timezone.utc)

        logger.info(
            f"[Hyperliquid策略] 信号池触发: {pool_name} (pool_id={pool_id}) symbol={symbol}, "
            f"检查 {len(self.strategies)} 个策略"
        )

        found_match = False
        for account_id, state in self.strategies.items():
            if state.signal_pool_id == pool_id:
                found_match = True
                # Try to mark as triggered (handles running state check)
                if state.mark_triggered_by_signal(event_time):
                    # Build trigger context for AI prompt
                    trigger_context = {
                        "trigger_type": "signal",
                        "signal_pool_id": pool_id,
                        "signal_pool_name": pool_name,
                        "pool_logic": pool.get("logic", "OR"),
                        "triggered_signals": triggered_signals,
                        "trigger_symbol": symbol,
                        "market_data_snapshot": market_data,
                        "signal_trigger_id": pool.get("trigger_log_id"),  # For decision tracking
                    }
                    logger.info(f"[Hyperliquid策略] 信号触发账户 {account_id} (pool: {pool_name})")
                    if self._executor:
                        self._executor.submit(
                            self._execute_strategy,
                            account_id, symbol, event_time,
                            "signal", trigger_context,
                        )
                    else:
                        self._execute_strategy(
                            account_id, symbol, event_time,
                            trigger_type="signal", trigger_context=trigger_context,
                        )
                else:
                    logger.debug(f"[Hyperliquid策略] 账户 {account_id} 正在执行中，跳过信号触发")

        if not found_match:
            logger.debug(f"[Hyperliquid策略] 没有策略绑定到 pool_id={pool_id}")


# Phase 1: Binance removed - stub only (no Binance imports)
class BinanceStrategyManager(StrategyManager):
    """Stub: Binance has been removed. Kept for API compatibility."""

    def _load_strategies(self):
        self.strategies.clear()

    def get_strategy_status(self) -> Dict[str, Any]:
        return {"strategies": [], "message": "Binance removed (Phase 1)"}

    def _on_signal_triggered(self, symbol: str, pool: dict, market_data: dict, triggered_signals: list):
        return

    def _run_strategy_loop(self):
        return

    def _execute_strategy(self, account_id: int, symbol: str, event_time: datetime, trigger_type: str = "scheduled", trigger_context: Optional[Dict] = None):
        return

    def _check_and_trigger_by_price(self, symbol: str, price: float, event_time: datetime):
        return

    def _placeholder_binance_removed(self):
        # Placeholder so any remaining references to BinanceStrategyManager internals don't break
        pass

    def _find_match_placeholder(self):
        pass


# Global strategy manager instances
hyper_strategy_manager = HyperliquidStrategyManager()
binance_strategy_manager = BinanceStrategyManager()


def start_strategy_manager():
    """Start the global strategy managers"""
    hyper_strategy_manager.start()
    binance_strategy_manager.start()
    logger.info("Both Hyperliquid and Binance strategy managers started")


def stop_strategy_manager():
    """Stop the global strategy managers"""
    hyper_strategy_manager.stop()
    binance_strategy_manager.stop()
    logger.info("Both Hyperliquid and Binance strategy managers stopped")


def handle_price_update(symbol: str, price: float, event_time: Optional[datetime] = None):
    """Handle price update from market data"""
    if event_time is None:
        event_time = datetime.now(timezone.utc)

    # Use both strategy managers
    hyper_strategy_manager.handle_price_update(symbol, price, event_time)
    binance_strategy_manager.handle_price_update(symbol, price, event_time)


def _execute_strategy_direct(account_id: int, symbol: str, event_time: datetime, db, is_hyper: bool = False, is_binance: bool = False):
    """Execute strategy directly without going through StrategyManager"""
    try:
        from database.models import AccountStrategyConfig

        # Update last trigger time
        strategy = db.query(AccountStrategyConfig).filter_by(account_id=account_id).first()
        if strategy:
            strategy.last_trigger_at = event_time
            db.commit()

        # Execute the trade
        if is_hyper:
            logger.info(f"[DirectStrategy] Executing Hyperliquid trade for account {account_id}")
            place_ai_driven_hyperliquid_order(account_id=account_id)
        elif is_binance:
            logger.info(f"[DirectStrategy] Binance removed (Phase 1), skip account {account_id}")
        else:
            from services.trading_commands import place_ai_driven_crypto_order
            place_ai_driven_crypto_order(max_ratio=0.2, account_id=account_id)
        logger.info(f"Strategy executed for account {account_id} on {symbol} price update")

    except Exception as e:
        logger.error(f"Failed to execute strategy for account {account_id}: {e}")
        import traceback
        traceback.print_exc()


def get_strategy_status() -> Dict[str, Any]:
    """Get strategy manager status"""
    status = {
        "hyperliquid": hyper_strategy_manager.get_strategy_status(),
        "binance": binance_strategy_manager.get_strategy_status(),
    }
    return status
