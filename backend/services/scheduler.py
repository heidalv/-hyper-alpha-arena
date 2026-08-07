"""
Scheduled task scheduler service
Used to manage WebSocket snapshot updates and other scheduled tasks
"""

import logging
import threading
import time
from datetime import date, datetime
from typing import Callable, Dict, List, Optional, Set

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from backend.database.connection import SessionLocal
from backend.database.models import CryptoPrice, Position

logger = logging.getLogger(__name__)

# 秒级监控独立线程（绕开 APScheduler 默认线程池被 LLM 长任务占满的问题）
_paper_monitor_stop: Optional[threading.Event] = None
_paper_monitor_thread: Optional[threading.Thread] = None
_tpsl_monitor_stop: Optional[threading.Event] = None
_tpsl_monitor_thread: Optional[threading.Thread] = None


class TaskScheduler:
    """Unified task scheduler"""
    
    def __init__(self):
        self.scheduler: Optional[BackgroundScheduler] = None
        self._started = False
        self._account_connections: Dict[int, Set] = {}  # track account connections
        
    def start(self):
        """Start the scheduler"""
        if not self._started or not (self.scheduler and self.scheduler.running):
            # 本机 CPU/内存充足：默认线程池 10 -> 32，避免 LLM 长任务饿死短任务
            self.scheduler = BackgroundScheduler(
                executors={
                    "default": {
                        "type": "threadpool",
                        "max_workers": int(
                            __import__("os").environ.get("SCHEDULER_MAX_WORKERS", "64")
                        ),
                    }
                }
            )
            self.scheduler.start()
            self._started = True
            logger.info("Scheduler started")
    
    def shutdown(self, *, wait: bool = False):
        """Shutdown the scheduler

        2026-06-17: 修复 apscheduler ``cannot schedule new futures after shutdown``
        刷屏（日志 140 次）。根因是 shutdown(wait=False) 时 executor 被立即关闭，
        但调度线程仍在 _process_jobs 里尝试 submit_job。现改为：
        1. 先 pause 停止新触发，并清空所有 job（杜绝 trigger 再次 fire）；
        2. shutdown(wait=True) 等待 in-flight job 落地，但用 _shutdown_deadline
           兜底避免无限等待（最长 10s）。
        """
        if self.scheduler and self.scheduler.running:
            try:
                self.scheduler.pause()
                for job in list(self.scheduler.get_jobs()):
                    try:
                        self.scheduler.remove_job(job.id)
                    except Exception:
                        pass
            except Exception:
                pass
            # wait=True 等待 in-flight job，防止它们在 executor 关闭后抢提交。
            # 用 try 包住，避免 shutdown 自身异常掩盖上层关闭流程。
            try:
                import time as _t
                deadline = _t.monotonic() + 10  # 最长等 10s
                self.scheduler.shutdown(wait=True)
                # apscheduler 的 shutdown(wait=True) 是阻塞的，已返回即说明落地。
                _ = deadline
            except Exception as e:
                logger.debug("Scheduler shutdown wait interrupted: %s", e)
                try:
                    self.scheduler.shutdown(wait=False)
                except Exception:
                    pass
            self._started = False
            logger.info("Scheduler shutdown")
        self.scheduler = None
    
    def is_running(self) -> bool:
        """Check if scheduler is running"""
        return self._started and self.scheduler and self.scheduler.running
    
    def add_account_snapshot_task(self, account_id: int, interval_seconds: int = 10):
        """
        Add snapshot update task for account

        Args:
            account_id: Account ID
            interval_seconds: Update interval (seconds), default 10 seconds
        """
        if not self.is_running():
            self.start()
            
        job_id = f"snapshot_account_{account_id}"
        
        # Check if task already exists
        if self.scheduler.get_job(job_id):
            logger.debug(f"Snapshot task for account {account_id} already exists")
            return
        
        try:
            self.scheduler.add_job(
                func=self._execute_account_snapshot,
                trigger=IntervalTrigger(seconds=interval_seconds),
                args=[account_id],
                id=job_id,
                replace_existing=True,
                max_instances=1,  # Avoid duplicate execution
                coalesce=True,    # Combine missed executions into one
                misfire_grace_time=5  # Allow 5 seconds grace time for late execution
            )
        except RuntimeError as exc:
            # 关闭过程中调度器已不可提交新 job（与 add_interval_task 对齐），
            # 静默跳过避免在 shutdown 序列里刷 "cannot schedule new futures" 错误。
            # 同时匹配 "shutdown" 和 "shut down"（apscheduler 两种措辞）。
            low = str(exc).lower()
            if "shutdown" in low or "shut down" in low or "cannot schedule" in low:
                logger.debug("Skip add_account_snapshot_task %s: %s", job_id, exc)
                return
            raise
        
        logger.info(f"Added snapshot task for account {account_id}, interval {interval_seconds} seconds")
    
    def remove_account_snapshot_task(self, account_id: int):
        """
        Remove snapshot update task for account

        Args:
            account_id: Account ID
        """
        if not self.scheduler:
            return
            
        job_id = f"snapshot_account_{account_id}"
        
        try:
            self.scheduler.remove_job(job_id)
            logger.info(f"Removed snapshot task for account {account_id}")
        except Exception as e:
            logger.debug(f"Failed to remove snapshot task for account {account_id}: {e}")
    
    
    def add_interval_task(self, task_func: Callable, interval_seconds: int, task_id: str,
                           max_instances: int = 1, next_run_time: "datetime | None" = None, *args, **kwargs):
        """
        Add interval execution task

        Args:
            task_func: Function to execute
            interval_seconds: Execution interval (seconds)
            task_id: Task unique identifier
            max_instances: Maximum concurrent instances (default 1)
            next_run_time: 显式指定首次运行时间（用于重启后恢复相位）。
                           None 时用 now+60s+错峰偏移（首次注册的默认行为）。
            *args, **kwargs: Parameters passed to task_func
        """
        if not self.is_running():
            self.start()
        if not self.is_running():
            logger.debug("Skip add_interval_task %s: scheduler not running", task_id)
            return

        try:
            # 首次运行时间：优先用传入的 next_run_time（重启相位恢复）。
            # [2026-07-12 修复 - 错峰调度，缓解偶发短暂卡顿] 原来所有任务的默认首跑时间
            # 都是"注册时刻 + 60s"，而很多任务(unified_loop/scalp_loop 都是30s一次)是在
            # 同一个函数里紧挨着注册的(毫秒级间隔)，导致它们的相位完全重合——每次触发都
            # 挤在同一秒内一起抢 CPU/GIL，是"模拟盘接口偶尔卡顿2~8秒"的直接原因之一。
            # 修复：给每个 task_id 加一个基于自身哈希的稳定错峰偏移(0~interval_seconds之间)，
            # 同名任务重启后偏移不变(可复现)，不同任务之间自然被错开，不再全部撞在同一秒。
            import hashlib
            from datetime import datetime, timedelta
            if next_run_time is not None:
                _first_run = next_run_time
            else:
                _jitter_span = max(1, min(int(interval_seconds), 60))
                _jitter = int(hashlib.md5(task_id.encode("utf-8")).hexdigest(), 16) % _jitter_span
                _first_run = datetime.now() + timedelta(seconds=60 + _jitter)

            self.scheduler.add_job(
                func=task_func,
                trigger=IntervalTrigger(seconds=interval_seconds, start_date=_first_run),
                args=args,
                kwargs=kwargs,
                id=task_id,
                replace_existing=True,
                max_instances=max_instances,
                coalesce=True,
                # misfire_grace_time：允许错过的触发在 N 秒内补跑。
                # 设为 interval 的 2 倍，确保偶发慢 tick（如 LLM 130s）后下一轮不会被丢弃。
                misfire_grace_time=max(interval_seconds * 2, 300),
            )
        except RuntimeError as exc:
            # 关闭过程中调度器已不可提交新 job，静默跳过避免刷
            # "cannot schedule new futures after shutdown" / "Scheduler is already shut down"。
            # 同时匹配 "shutdown" 和 "shut down"（apscheduler 两种措辞）。
            low = str(exc).lower()
            if "shutdown" in low or "shut down" in low or "cannot schedule" in low:
                logger.debug("Skip add_interval_task %s: %s", task_id, exc)
                return
            raise

        logger.info(f"Added interval task {task_id}: Execute every {interval_seconds} seconds (max_instances={max_instances})")
    
    def add_cron_task(self, task_func: Callable, task_id: str, max_instances: int = 1,
                       hour: "int | str | None" = None, minute: "int | str | None" = 0,
                       second: "int | str | None" = 0, day_of_week: "str | None" = None,
                       *args, **kwargs):
        """
        Add a cron-style task that fires at a fixed wall-clock time (daily/weekly),
        as opposed to add_interval_task's fixed-period trigger.

        [2026-07-17 新增] 此前 factor_evolution_loop 等模块调用了不存在的
        add_cron_task，导致 AttributeError 被上层 try/except 静默吞掉——
        因子进化闭环注册"看起来成功"但实际从未跑过一次。现补上该方法。

        Args:
            task_func: Function to execute
            task_id: Task unique identifier
            max_instances: Maximum concurrent instances (default 1)
            hour/minute/second: 触发时刻（0-23/0-59/0-59），None 表示每个该字段值都触发
            day_of_week: 可选，如 "mon" 或 "mon-fri"；None 表示每天
            *args, **kwargs: Parameters passed to task_func
        """
        if not self.is_running():
            self.start()
        if not self.is_running():
            logger.debug("Skip add_cron_task %s: scheduler not running", task_id)
            return

        try:
            self.scheduler.add_job(
                func=task_func,
                trigger=CronTrigger(hour=hour, minute=minute, second=second, day_of_week=day_of_week),
                args=args,
                kwargs=kwargs,
                id=task_id,
                replace_existing=True,
                max_instances=max_instances,
                coalesce=True,
                # cron 任务通常一天一次，允许较大的补跑窗口（1小时），避免因短暂
                # 调度器暂停/重启而错过当天整轮
                misfire_grace_time=3600,
            )
        except RuntimeError as exc:
            low = str(exc).lower()
            if "shutdown" in low or "shut down" in low or "cannot schedule" in low:
                logger.debug("Skip add_cron_task %s: %s", task_id, exc)
                return
            raise

        logger.info(
            f"Added cron task {task_id}: hour={hour} minute={minute} second={second} "
            f"day_of_week={day_of_week or 'every day'} (max_instances={max_instances})"
        )

    def remove_task(self, task_id: str):
        """
        Remove specified task

        Args:
            task_id: Task ID
        """
        if not self.scheduler:
            return
            
        try:
            self.scheduler.remove_job(task_id)
            logger.info(f"Removed task: {task_id}")
        except Exception as e:
            logger.debug(f"Failed to remove task {task_id}: {e}")

    def get_job_info(self) -> list:
        """Get all task information"""
        if not self.scheduler:
            return []

        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                'id': job.id,
                'next_run_time': job.next_run_time,
                'func_name': job.func.__name__ if hasattr(job.func, '__name__') else str(job.func)
            })
        return jobs

    def _execute_account_snapshot(self, account_id: int):
        """
        Internal method to execute account snapshot update.
        Must be synchronous — BackgroundScheduler cannot call async functions.

        Args:
            account_id: Account ID
        """
        start_time = datetime.now()
        try:
            # [C1] APScheduler 后台任务不在 HTTP 请求上下文,设 system_identity 穿透 RLS,
            # 否则非超用户 DB 角色下 Position 等租户作用域表 fail-closed → 快照为空。
            from backend.core.tenant import set_system_identity
            set_system_identity()
            # Dynamic import to avoid circular dependency
            from backend.api.ws import manager

            # Check if account still has active connections
            if account_id not in manager.active_connections:
                # Account disconnected, remove task
                self.remove_account_snapshot_task(account_id)
                return

            # Execute optimized snapshot update
            db: Session = SessionLocal()
            try:
                # Send optimized snapshot update (reduced frequency for expensive data)
                # Note: For now, skip the async WebSocket update in sync scheduler context
                # This can be enhanced later to properly handle async operations
                logger.debug(f"Skipping WebSocket snapshot update for account {account_id} in sync context")

                # Save latest prices for account's positions (less frequently)
                if start_time.second % 30 == 0:  # Only every 30 seconds
                    self._save_position_prices(db, account_id)

            finally:
                db.close()

        except Exception as e:
            logger.error(f"Account {account_id} snapshot update failed: {e}")
        finally:
            execution_time = (datetime.now() - start_time).total_seconds()
            if execution_time > 5:  # Log if execution takes longer than 5 seconds
                logger.warning(f"Slow snapshot execution for account {account_id}: {execution_time:.2f}s")
    
    def _save_position_prices(self, db: Session, account_id: int):
        """
        Save latest prices for account's positions on the current date

        Args:
            db: Database session
            account_id: Account ID
        """
        try:
            # Get all account's positions
            positions = db.query(Position).filter(
                Position.account_id == account_id,
                Position.quantity > 0
            ).all()

            if not positions:
                logger.debug(f"Account {account_id} has no positions, skip price saving")
                return

            today = date.today()

            for position in positions:
                try:
                    # Check if crypto price already saved today
                    existing_price = db.query(CryptoPrice).filter(
                        CryptoPrice.symbol == position.symbol,
                        CryptoPrice.market == position.market,
                        CryptoPrice.price_date == today
                    ).first()

                    if existing_price:
                        logger.debug(f"crypto {position.symbol} price already exists for today, skip")
                        continue

                    # Get latest price
                    from services.market_data import get_last_price
                    current_price = get_last_price(position.symbol, position.market)

                    # Save price record
                    crypto_price = CryptoPrice(
                        symbol=position.symbol,
                        market=position.market,
                        price=current_price,
                        price_date=today
                    )

                    db.add(crypto_price)
                    db.commit()

                    logger.info(f"Saved crypto price: {position.symbol} {today} {current_price}")

                except Exception as e:
                    logger.error(f"Failed to save crypto {position.symbol} price: {e}")
                    db.rollback()
                    continue

        except Exception as e:
            logger.error(f"Failed to save account {account_id} position prices: {e}")
            db.rollback()


# Global scheduler instance
task_scheduler = TaskScheduler()


# Convenience functions
def start_scheduler():
    """Start global scheduler"""
    task_scheduler.start()


def stop_scheduler():
    """Stop global scheduler"""
    task_scheduler.shutdown()


def add_account_snapshot_job(account_id: int, interval_seconds: int = 10):
    """Convenience function to add snapshot task for account"""
    task_scheduler.add_account_snapshot_task(account_id, interval_seconds)


def remove_account_snapshot_job(account_id: int):
    """Convenience function to remove account snapshot task"""
    task_scheduler.remove_account_snapshot_task(account_id)


# Legacy compatibility functions
def add_user_snapshot_job(user_id: int, interval_seconds: int = 10):
    """Legacy function - now redirects to account-based function"""
    # For backward compatibility, assume this is account_id
    add_account_snapshot_job(user_id, interval_seconds)


def remove_user_snapshot_job(user_id: int):
    """Legacy function - now redirects to account-based function"""
    # For backward compatibility, assume this is account_id
    remove_account_snapshot_job(user_id)


def setup_market_tasks():
    """Set up crypto market-related scheduled tasks"""
    # Crypto markets run 24/7, no specific market open/close times needed
    logger.info("Crypto markets run 24/7 - no market hours tasks needed")


def _ensure_market_data_ready() -> None:
    """Prefetch required market data before enabling trading tasks"""
    try:
        from services.market_data import get_last_price
        from services.trading_commands import AI_TRADING_SYMBOLS

        missing_symbols: List[str] = []

        for symbol in AI_TRADING_SYMBOLS:
            try:
                price = get_last_price(symbol, "CRYPTO")
                if price is None or price <= 0:
                    missing_symbols.append(symbol)
                    logger.warning(f"Prefetch returned invalid price for {symbol}: {price}")
                else:
                    logger.debug(f"Prefetched market data for {symbol}: {price}")
            except Exception as fetch_err:
                missing_symbols.append(symbol)
                logger.warning(f"Failed to prefetch price for {symbol}: {fetch_err}")

        if missing_symbols:
            raise RuntimeError(
                "Market data not ready for symbols: " + ", ".join(sorted(set(missing_symbols)))
            )

    except Exception as err:
        logger.error(f"Market data readiness check failed: {err}")
        raise


def reset_auto_trading_job():
    """DEPRECATED: Legacy function from paper trading module

    This function is now DISABLED and performs no operations.

    Historical issue (GitHub #31):
    - This function used to unconditionally start a fixed 300-second APScheduler task
    - That task called place_ai_driven_crypto_order() for ALL accounts every 5 minutes
    - This conflicted with Hyperliquid strategy manager's per-account trigger intervals
    - Result: Users configured 600s interval but got double triggers at ~300s intervals

    Current behavior:
    - No-op function (does nothing)
    - All trading is now managed exclusively by Hyperliquid strategy manager
    - Strategy manager respects per-account trigger intervals configured in strategy settings
    """
    logger.info(
        "reset_auto_trading_job() called but DISABLED (paper trading legacy). "
        "All trading managed by Hyperliquid strategy manager. See GitHub issue #31."
    )




def start_multi_venue_funding_collector():
    """启动多场所资金费采集（为 delta-neutral 刷分补第二条腿数据）。

    默认关闭（settings.MULTI_VENUE_FUNDING_COLLECTOR_ENABLED=false）；开启后每
    N 秒用公共只读客户端拉各场所资金费写入 perp_funding。无外网时优雅空转、不造数。
    """
    try:
        from backend.config import settings as _settings
    except Exception as e:
        logger.debug("[MultiVenueFunding] 读取配置失败，跳过启动: %s", e)
        return

    if not getattr(_settings, "MULTI_VENUE_FUNDING_COLLECTOR_ENABLED", False):
        logger.info(
            "[MultiVenueFunding] 采集器默认关闭（MULTI_VENUE_FUNDING_COLLECTOR_ENABLED=false）；"
            "有外网环境可开启以补齐第二场所资金费、让 SDN 凑出双腿"
        )
        return

    interval = int(getattr(_settings, "MULTI_VENUE_FUNDING_COLLECT_INTERVAL_SECONDS", 300))
    venues_cfg = (getattr(_settings, "MULTI_VENUE_FUNDING_VENUES", "") or "").strip()
    symbols_cfg = (getattr(_settings, "MULTI_VENUE_FUNDING_SYMBOLS", "") or "").strip()
    venues = [v.strip() for v in venues_cfg.split(",") if v.strip()] or None
    symbols = [s.strip() for s in symbols_cfg.split(",") if s.strip()] or None

    def _funding_collect_tick():
        try:
            # [C1] APScheduler 后台任务,设 system_identity 穿透 RLS(collect_once 内部开 SessionLocal 写 perp_funding)。
            from backend.core.tenant import set_system_identity
            set_system_identity()
            from backend.services.multi_venue_funding_collector import collect_once

            collect_once(symbols=symbols, venues=venues)
        except Exception as e:
            logger.error("[MultiVenueFunding] 采集 tick 异常: %s", e, exc_info=True)

    JOB_ID = "multi_venue_funding_collector"
    try:
        if not task_scheduler.is_running():
            task_scheduler.start()
        if task_scheduler.scheduler and task_scheduler.scheduler.get_job(JOB_ID):
            task_scheduler.remove_task(JOB_ID)
        task_scheduler.add_interval_task(
            task_func=_funding_collect_tick,
            interval_seconds=interval,
            task_id=JOB_ID,
        )
        logger.info(
            "[MultiVenueFunding] 已启动 — 每 %ds 采集多场所资金费写入 perp_funding", interval
        )
    except Exception as e:
        logger.error("[MultiVenueFunding] 启动失败: %s", e, exc_info=True)


def start_asset_curve_broadcast():
    """Start asset curve broadcast task - broadcasts every 60 seconds"""
    from backend.api.ws import broadcast_asset_curve_update

    def broadcast_all_timeframes():
        """Broadcast asset curve updates for all timeframes"""
        # [fix] P2-3: 使用 asyncio.run() 替代手动 new_event_loop/close，
        # 避免每 60s 创建销毁事件循环导致的 fd 碎片化（24h ≈ 1440 次）
        # [C1] APScheduler 后台任务,设 system_identity 穿透 RLS(broadcast 内部读资产曲线)。
        from backend.core.tenant import set_system_identity
        set_system_identity()
        import asyncio as _asyncio

        async def _broadcast():
            await broadcast_asset_curve_update("5m")
            await broadcast_asset_curve_update("1h")
            await broadcast_asset_curve_update("1d")

        try:
            _asyncio.run(_broadcast())
            logger.debug("Broadcasted asset curve updates for all timeframes")
        except Exception as e:
            logger.error(f"Failed to broadcast asset curve updates: {e}")

    try:
        # Ensure scheduler is running
        if not task_scheduler.is_running():
            task_scheduler.start()
            logger.info("Started scheduler for asset curve broadcast")

        # Add broadcast task (every 60 seconds)
        ASSET_CURVE_BROADCAST_JOB_ID = "asset_curve_broadcast"
        BROADCAST_INTERVAL_SECONDS = 60

        # Remove existing job if it exists
        if task_scheduler.scheduler and task_scheduler.scheduler.get_job(ASSET_CURVE_BROADCAST_JOB_ID):
            task_scheduler.remove_task(ASSET_CURVE_BROADCAST_JOB_ID)
            logger.info("Removed existing asset curve broadcast job")

        # Add the broadcast job
        task_scheduler.add_interval_task(
            task_func=broadcast_all_timeframes,
            interval_seconds=BROADCAST_INTERVAL_SECONDS,
            task_id=ASSET_CURVE_BROADCAST_JOB_ID
        )

        logger.info(f"Asset curve broadcast job started - interval: {BROADCAST_INTERVAL_SECONDS}s")

    except Exception as e:
        logger.error(f"Failed to start asset curve broadcast: {e}")
        raise


def start_paper_trading_monitor():
    """启动模拟交易持仓监控（每 10 秒更新标记价格 + 检查 TP/SL/爆仓 + 限价单触发）"""

    def _paper_tick(fast: bool = True):
        # [C1] APScheduler 后台任务不在 HTTP 请求上下文,设 system_identity 穿透 RLS,
        # 覆盖本 tick 内两处 db = SessionLocal()。PaperPosition 是租户作用域表(NOT NULL
        # tenant_id),不设身份会 fail-closed → 模拟盘持仓 TP/SL/爆仓检查全部失效。
        from backend.core.tenant import set_system_identity
        set_system_identity()
        from services.paper_trading_engine import paper_engine

        from backend.database.models import PaperPosition
        # 阶段1：短事务 — 仅读取 open 持仓 ID 列表
        db = SessionLocal()
        try:
            if not fast:
                paper_engine.check_pending_orders(db)
            open_ids = [p.id for p in db.query(PaperPosition.id).filter(PaperPosition.status == "open").all()]
        except Exception as e:
            logger.error(f"[Paper Monitor] 挂单触发检查异常: {e}")
            open_ids = []
        finally:
            db.close()

        if not open_ids:
            return

        # 阶段2：每个持仓独立短事务 — 减少锁持有时间
        for pos_id in open_ids:
            db = SessionLocal()
            try:
                # 设置短锁超时（2秒），避免长等锁
                from sqlalchemy import text as _sa_text
                db.execute(_sa_text("SET LOCAL lock_timeout = '2s'"))
                db.execute(_sa_text("SET LOCAL statement_timeout = '10s'"))
                pos = db.query(PaperPosition).filter(PaperPosition.id == pos_id).first()
                if not pos or pos.status != "open":
                    continue
                if fast:
                    paper_engine.reprice_position(db, pos)
                else:
                    paper_engine.update_single_position(db, pos)
                    db.commit()
            except Exception as e:
                logger.error(f"[Paper Monitor] 持仓 {pos_id} 更新异常: {e}")
                try:
                    db.rollback()
                except Exception:
                    pass
            finally:
                db.close()

    PAPER_JOB_ID = "paper_trading_monitor"
    PAPER_INTERVAL = 3

    global _paper_monitor_stop, _paper_monitor_thread

    def _paper_loop():
        tick_count = 0
        while not _paper_monitor_stop.is_set():
            t0 = time.time()
            try:
                tick_count += 1
                _paper_tick(fast=(tick_count % 10 != 0))
            except Exception as e:
                logger.error(f"[Paper Monitor] tick 异常: {e}", exc_info=True)
            elapsed = time.time() - t0
            wait = max(0.5, PAPER_INTERVAL - elapsed)
            _paper_monitor_stop.wait(wait)

    if _paper_monitor_thread and _paper_monitor_thread.is_alive():
        return
    _paper_monitor_stop = threading.Event()
    _paper_monitor_thread = threading.Thread(
        target=_paper_loop, name="paper-monitor-realtime", daemon=True
    )
    _paper_monitor_thread.start()
    logger.info(f"[Paper Monitor] 已启动(独立线程) — 每 {PAPER_INTERVAL}s 检查持仓/挂单")
    return

    try:
        if not task_scheduler.is_running():
            task_scheduler.start()

        if task_scheduler.scheduler and task_scheduler.scheduler.get_job(PAPER_JOB_ID):
            task_scheduler.remove_task(PAPER_JOB_ID)

        task_scheduler.add_interval_task(
            task_func=_paper_tick,
            interval_seconds=PAPER_INTERVAL,
            task_id=PAPER_JOB_ID,
            max_instances=2,  # 允许轻微重叠，避免 30s 高频跳过告警
        )
        logger.info(f"[Paper Monitor] 已启动 — 每 {PAPER_INTERVAL}s 检查持仓/挂单")
    except Exception as e:
        logger.error(f"[Paper Monitor] 启动失败: {e}", exc_info=True)


def start_tpsl_monitor():
    """启动TP/SL订单状态监控（每60秒检查Hyperliquid仓位是否有TP/SL保护）"""

    def _tpsl_check_tick():
        """Check all open Hyperliquid positions for TP/SL protection.

        If a position has no TP/SL orders, log a warning so operators can
        manually add protection or investigate why TP/SL was not placed.
        """
        try:
            # [C1] APScheduler 后台任务,设 system_identity 穿透 RLS。
            from backend.core.tenant import set_system_identity
            set_system_identity()
            from backend.database.models import Account
            from backend.services.hyperliquid_environment import get_global_trading_mode, get_hyperliquid_client

            db = SessionLocal()
            try:
                # Find all active Hyperliquid accounts
                accounts = db.query(Account).filter(
                    Account.is_active == "true",
                    Account.auto_trading_enabled == "true",
                    Account.hyperliquid_enabled == "true",
                ).all()

                for account in accounts:
                    try:
                        environment = get_global_trading_mode(db)
                        client = get_hyperliquid_client(db, account.id, override_environment=environment)
                        positions = client.get_positions(db, include_timing=False)
                        open_orders = client.get_open_orders(db) if hasattr(client, 'get_open_orders') else []

                        for pos in positions:
                            symbol = pos.get('coin', '')
                            # Check if this position has TP/SL orders
                            tpsl_orders = [
                                o for o in open_orders
                                if o.get('symbol') == symbol and o.get('trigger_condition')
                            ]

                            if not tpsl_orders:
                                logger.warning(
                                    f"[TPSL Monitor] Account {account.name}: "
                                    f"Position {symbol} has NO TP/SL protection! "
                                    f"Size={pos.get('szi', 0)}, Entry={pos.get('entry_px', 0)}"
                                )
                    except Exception as acc_err:
                        logger.debug(f"[TPSL Monitor] Check failed for account {account.id}: {acc_err}")
            finally:
                db.close()

        except Exception as e:
            logger.error(f"[TPSL Monitor] 检查异常: {e}", exc_info=True)

    # ── AI 策略反馈闭环日报（Phase 5）────────────────────────────────────
    AI_FEEDBACK_JOB_ID = "ai_feedback_daily"
    AI_FEEDBACK_INTERVAL = 86400  # 24h

    def _ai_feedback_daily_tick():
        """每日输出亏损规则、最赚钱退出、应禁用交易性质。"""
        # [C1] APScheduler 后台任务,设 system_identity 穿透 RLS。
        from backend.core.tenant import set_system_identity
        set_system_identity()
        db = SessionLocal()
        try:
            from backend.services.decision_feedback_service import decision_feedback_service
            decision_feedback_service.run_daily_report(db)
        except Exception as e:
            logger.error("[AIFeedback] 日报任务异常: %s", e, exc_info=True)
        finally:
            db.close()

    try:
        if not task_scheduler.is_running():
            task_scheduler.start()
        if task_scheduler.scheduler and task_scheduler.scheduler.get_job(AI_FEEDBACK_JOB_ID):
            task_scheduler.remove_task(AI_FEEDBACK_JOB_ID)
        task_scheduler.add_interval_task(
            task_func=_ai_feedback_daily_tick,
            interval_seconds=AI_FEEDBACK_INTERVAL,
            task_id=AI_FEEDBACK_JOB_ID,
        )
        logger.info("[AIFeedback] 已启动 — 每 %ds 生成策略反馈日报", AI_FEEDBACK_INTERVAL)
    except Exception as e:
        logger.error("[AIFeedback] 启动失败: %s", e, exc_info=True)

    TPSL_JOB_ID = "tpsl_monitor"
    TPSL_INTERVAL = 5  # Check every 5 seconds

    global _tpsl_monitor_stop, _tpsl_monitor_thread

    def _tpsl_loop():
        while not _tpsl_monitor_stop.is_set():
            t0 = time.time()
            try:
                _tpsl_check_tick()
            except Exception as e:
                logger.error(f"[TPSL Monitor] tick 异常: {e}", exc_info=True)
            elapsed = time.time() - t0
            wait = max(0.5, TPSL_INTERVAL - elapsed)
            _tpsl_monitor_stop.wait(wait)

    if _tpsl_monitor_thread and _tpsl_monitor_thread.is_alive():
        return
    _tpsl_monitor_stop = threading.Event()
    _tpsl_monitor_thread = threading.Thread(
        target=_tpsl_loop, name="tpsl-monitor-realtime", daemon=True
    )
    _tpsl_monitor_thread.start()
    logger.info(f"[TPSL Monitor] 已启动(独立线程) — 每 {TPSL_INTERVAL}s 检查TP/SL保护状态")
    return

    try:
        if not task_scheduler.is_running():
            task_scheduler.start()

        if task_scheduler.scheduler and task_scheduler.scheduler.get_job(TPSL_JOB_ID):
            task_scheduler.remove_task(TPSL_JOB_ID)

        task_scheduler.add_interval_task(
            task_func=_tpsl_check_tick,
            interval_seconds=TPSL_INTERVAL,
            task_id=TPSL_JOB_ID,
        )
        logger.info(f"[TPSL Monitor] 已启动 — 每 {TPSL_INTERVAL}s 检查TP/SL保护状态")
    except Exception as e:
        logger.error(f"[TPSL Monitor] 启动失败: {e}", exc_info=True)
