"""应用程序启动初始化服务"""

import logging
import threading
import asyncio
import os
import time

from backend.services.trading_commands import (
    place_ai_driven_crypto_order,
    place_random_crypto_order,
    AUTO_TRADE_JOB_ID,
    AI_TRADE_JOB_ID,
)
from backend.services.scheduler import start_scheduler, setup_market_tasks, task_scheduler
from backend.services.market_price_service import (
    stop_market_price_services,
    sync_market_symbols,
)
from backend.services.market_events import subscribe_price_updates, unsubscribe_price_updates
from backend.services.asset_snapshot_service import handle_price_update
from backend.services.trading_strategy import start_strategy_manager, stop_strategy_manager
from backend.services.hyperliquid_symbol_service import (
    refresh_hyperliquid_symbols,
    schedule_symbol_refresh_task,
)

logger = logging.getLogger(__name__)

# [fix] 模块级变量：保持文件锁 fd 存活，防止被 GC 回收导致锁释放
_scheduler_lock_fd = None
_scheduler_initialized = False


def _reap_orphan_workers():
    """[2026-09-05] 收割孤儿 worker：父进程已死的后台子进程。

    事故（2026-09-05 03:16）：历次后端死亡后遗留的孤儿 worker（evo_subprocess×2、
    user_stream×2、market_data_center×2 双份残留）继续持有继承来的 backend.log
    句柄，新后端 `>> backend.log` 重定向直接失败、秒退且无任何日志 —— watchdog
    反复拉起反复死。句柄继承已在各 spawn 点改 DEVNULL（治本），本函数是启动时
    的清道夫（治存量）：按「命令行匹配 worker + 父进程不在」判定孤儿并结束。

    父进程存活的实例不动 —— data-center-watchdog / user_stream_guard 托管的
    正常 worker 有活着的父进程，不受影响。fail-open：psutil 缺失/异常只记日志。
    """
    import os as _os

    patterns = (
        "binance_user_stream",
        "workers.market_data_center",
        "evo_subprocess",
    )
    try:
        import psutil
    except Exception as exc:
        logger.warning("[Startup] 孤儿 worker 收割跳过（无 psutil）: %s", exc)
        return
    me = _os.getpid()
    reaped = []
    try:
        for proc in psutil.process_iter(["pid", "ppid", "cmdline"]):
            try:
                cmd = " ".join(proc.info.get("cmdline") or [])
                if not cmd or proc.pid == me:
                    continue
                if not any(p in cmd for p in patterns):
                    continue
                if "python" not in (proc.info.get("cmdline") or [""])[0].lower():
                    continue
                ppid = proc.info.get("ppid") or 0
                if ppid and psutil.pid_exists(ppid):
                    continue  # 有活着的父进程 → 托管实例，不动
                proc.terminate()
                reaped.append(f"{proc.pid}:{cmd[:60]}")
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception as exc:
        logger.warning("[Startup] 孤儿 worker 收割异常（忽略）: %s", exc)
        return
    if reaped:
        logger.warning("[Startup] 已收割孤儿 worker %d 个: %s", len(reaped), "; ".join(reaped))
    else:
        logger.info("[Startup] 无孤儿 worker（父进程存活检查通过）")


def initialize_sync_services():
    """初始化同步服务（在后台线程中运行，不包含需要事件循环的异步服务）"""
    global _scheduler_lock_fd, _scheduler_initialized
    if _scheduler_initialized:
        logger.info("[Startup] 同步服务已初始化，跳过重复调用")
        return
    _scheduler_initialized = True

    # 先清孤儿 worker，再初始化/拉起新 worker，避免双份进程与句柄残留
    _reap_orphan_workers()

    try:
        from backend.services.trading_pairs_config import ensure_trading_pairs_seeded
        seeded = ensure_trading_pairs_seeded()
        logger.info("[Startup] 全局交易对已就绪: %s", seeded)
    except Exception as _tp_err:
        logger.warning("[Startup] 全局交易对初始化失败（非致命）: %s", _tp_err)

    try:
        from backend.services.lock_strength_service import get_lock_strength_service
        _ls = get_lock_strength_service()
        _ls_state = _ls.get_state()
        _ls._sync_runtime_flags("paper", _ls_state["paper"]["strength"])
        _ls._sync_runtime_flags("live", _ls_state["live"]["strength"])
        logger.info(
            "[Startup] 锁仓强度已加载: paper=%s live=%s",
            _ls_state["paper"]["strength"],
            _ls_state["live"]["strength"],
        )
    except Exception as _ls_err:
        logger.warning("[Startup] 锁仓强度加载失败（非致命）: %s", _ls_err)

    # [2026-07-30] OpenCode Sidecar 已禁用（资源占用过高，功能无用）
    # try:
    #     from backend.services.opencode_sidecar import boot_sidecar_with_retries
    #     threading.Thread(
    #         target=boot_sidecar_with_retries,
    #         name="opencode-sidecar-boot",
    #         daemon=True,
    #     ).start()
    #     logger.info("[Startup] OpenCode sidecar 自启已触发")
    # except Exception as _sc_boot_err:
    #     logger.warning("[Startup] OpenCode sidecar 自启触发失败: %s", _sc_boot_err)

    # [fix] 多 worker 模式下，通过文件锁确保只有一个 worker 运行调度器
    _scheduler_disabled = os.getenv("SCHEDULER_DISABLED", "").lower() in ("true", "1", "yes")
    if not _scheduler_disabled:
        try:
            _lock_path = os.path.join(os.path.dirname(__file__), "..", "data", ".scheduler.lock")
            os.makedirs(os.path.dirname(_lock_path), exist_ok=True)
            _scheduler_lock_fd = open(_lock_path, "w")
            try:
                import fcntl
                fcntl.flock(_scheduler_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except ImportError:
                # Windows 无 fcntl，用 msvcrt 等价实现（2026-06-11 跨平台修复）
                import msvcrt
                msvcrt.locking(_scheduler_lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
            logger.info("[Startup] 调度器文件锁获取成功（本 worker 运行调度器）")
        except (IOError, OSError):
            logger.info("[Startup] 调度器文件锁获取失败 — 另一个 worker 已持有，跳过调度器启动")
            _scheduler_disabled = True
            _scheduler_lock_fd = None
        except Exception as _lock_err:
            logger.warning("[Startup] 调度器文件锁不可用（继续启动调度器）: %s", _lock_err)

    try:
        # Start scheduler
        if _scheduler_disabled:
            logger.info("[Startup] 调度器已禁用（多 worker 模式从 worker 或 SCHEDULER_DISABLED=true）")
            logger.info("[Startup] 本 worker 不启动任何后台循环，避免重复 AI 策略/市场监控任务")
            return
        else:
            logger.info("正在启动调度器...")
            start_scheduler()
            logger.info("调度器已启动")
            logger.info("调度器服务已启动")

        # ── 恢复全自动交易会话 ──
        # 只有持有调度器文件锁的 worker 可以恢复后台策略循环。
        # 否则多 worker / reload 风暴下会出现多个进程同时注册 fullauto_unified_*，
        # 互相抢 DB/交易状态，表现为 AI 决策日志归零、循环卡死或重复 hold。
        try:
            from backend.services.full_auto_trading_service import full_auto_service
            # 后台启动线程没有 HTTP 租户上下文，必须用系统身份穿透 RLS，
            # 否则 running 会话/active 策略被行级安全隐藏，重启后策略不会恢复。
            from backend.core.tenant import system_identity
            with system_identity():
                full_auto_service.restore_running_sessions()
            logger.info("全自动交易服务已启动")
            # QAA v3 尽早 bootstrap（后台非阻塞，不等待 auto_discover）
            try:
                full_auto_service.bootstrap_qaa_v3_context(blocking=False)
                logger.info("[Startup] QAA v3.0 后台 bootstrap 已触发")
            except Exception as _early_qaa_err:
                logger.debug(f"[Startup] QAA v3 提前初始化跳过: {_early_qaa_err}")
        except Exception as e:
            logger.error(f"全自动交易服务启动失败: {e}")

        # [2026-08-17 删除] OpenCode 调度器整体删除（opencode_scheduler.py 已移除，
        # 路由与 sidecar 同步退役；opencode_bridge 引擎仍由学习循环按需调用）。

        # Set up market-related scheduled tasks
        setup_market_tasks()
        logger.info("市场定时任务已设置完成")

        # Rule Sync: 六所规则源定时监控（默认6小时，可用 RULE_SYNC_ENABLED=false 关闭）
        try:
            from backend.services.rebate_arb.rule_sync_scheduler import schedule_rule_sync_task
            schedule_rule_sync_task(task_scheduler)
        except Exception as _rule_sync_err:
            logger.warning(f"[Startup] Rule Sync 定时任务注册失败（非致命）: {_rule_sync_err}")

        # 激励数据聚合器：configure + 定时拉取（S1-S8 策略评估与 S4 campaigns 数据源）
        try:
            from backend.services.rebate_arb.incentive_aggregator import (
                schedule_incentive_fetch_task,
            )
            schedule_incentive_fetch_task(task_scheduler)
        except Exception as _incentive_err:
            logger.warning(f"[Startup] 激励聚合定时任务注册失败（非致命）: {_incentive_err}")

        # 恢复套利专用 Paper 验证后台 tick（status=running 的账户）
        try:
            from backend.services.rebate_arb.arbitrage_paper_session_runner import (
                arbitrage_paper_session_runner,
            )
            if arbitrage_paper_session_runner.restore_from_db():
                logger.info("[Startup] 套利 Paper 验证会话已恢复")
        except Exception as _arb_paper_err:
            logger.warning(f"[Startup] 套利 Paper 会话恢复失败（非致命）: {_arb_paper_err}")

        # Add price cache cleanup task (every 2 minutes)
        from services.price_cache import clear_expired_prices
        task_scheduler.add_interval_task(
            task_func=clear_expired_prices,
            interval_seconds=120,
            task_id="price_cache_cleanup"
        )
        logger.info("价格缓存清理任务已启动（2分钟间隔）")

        # Refresh Hyperliquid exchange symbols (non-blocking) & register periodic task
        def _deferred_symbol_refresh():
            try:
                refresh_hyperliquid_symbols(environment="mainnet")
                logger.info("Hyperliquid 交易所符号刷新完成（后台）")
            except Exception as _err:
                logger.warning(f"Hyperliquid 符号刷新失败（非致命）: {_err}")
        threading.Thread(target=_deferred_symbol_refresh, daemon=True, name="symbol-refresh").start()
        try:
            schedule_symbol_refresh_task(interval_seconds=7200)
            logger.info("Hyperliquid 符号定时刷新已注册（2小时间隔）")
        except Exception as _sym_err:
            logger.warning(f"Hyperliquid 符号刷新任务注册失败: {_sym_err}")

        # Start market data stream — 使用全局 user_trading_pairs
        from backend.services.trading_pairs_config import ensure_trading_pairs_seeded, get_user_trading_pairs
        try:
            ensure_trading_pairs_seeded()
            combined_symbols = get_user_trading_pairs(force_refresh=True)
        except Exception as _e:
            logger.warning(f"读取全局交易对失败: {_e}")
            combined_symbols = []

        # MarketDataHub 优先；Legacy REST 轮询仅在 disable_rest_market_stream=false 时启用
        try:
            from backend.services.market_data_hub import start_market_data_hub
            if start_market_data_hub(symbols=combined_symbols):
                logger.info(
                    "[Startup] MarketDataHub + 跨所 WS feed 已启动 (%d symbols)",
                    len(combined_symbols),
                )
        except Exception as _hub_err:
            logger.warning(f"MarketDataHub 启动失败（非致命）: {_hub_err}")
            try:
                from backend.services.arbitrage.cross_exchange_ws_feed import start_ws_feed
                if start_ws_feed(symbols=combined_symbols):
                    logger.info("[Startup] 跨所 mid WS feed 已启动（Hub 降级）")
            except Exception as _ws_err:
                logger.warning(f"跨所 mid WS feed 启动失败（非致命）: {_ws_err}")

        logger.info(f"正在同步市场 symbol 列表（{len(combined_symbols)} 个币种）...")
        try:
            sync_market_symbols(combined_symbols, interval_seconds=5)
        except Exception as _e:
            logger.error(f"市场 symbol 同步失败（非致命）: {_e}")
        logger.info(f"市场数据已初始化: {combined_symbols}")

        # Subscribe strategy manager to price updates
        try:
            from services.trading_strategy import handle_price_update as strategy_price_update

            def strategy_price_wrapper(event):
                symbol = event.get("symbol")
                price = event.get("price")
                event_time = event.get("event_time")
                if symbol and price:
                    strategy_price_update(symbol, float(price), event_time)

            subscribe_price_updates(strategy_price_wrapper)
            logger.info("策略管理器已订阅价格更新")
        except Exception as _e:
            logger.error(f"策略管理器订阅价格更新失败（非致命）: {_e}")

        # Start AI trading strategy manager
        logger.info("正在启动策略管理器...")
        try:
            start_strategy_manager()
            logger.info("策略管理器已启动")
        except Exception as _e:
            logger.error(f"策略管理器启动失败（非致命）: {_e}")

        # Start asset curve broadcast task (every 60 seconds)
        try:
            from services.scheduler import start_asset_curve_broadcast
            start_asset_curve_broadcast()
            logger.info("资产曲线广播任务已启动（60秒间隔）")
        except Exception as _e:
            logger.error(f"资产曲线广播启动失败（非致命）: {_e}")

        # Start paper trading position monitor (every 30 seconds)
        try:
            from services.scheduler import start_paper_trading_monitor
            start_paper_trading_monitor()
            logger.info("模拟交易监控任务已启动（10秒间隔）")
        except Exception as _e:
            logger.error(f"模拟交易监控启动失败（非致命）: {_e}")

        # Start TP/SL protection monitor (every 60 seconds)
        try:
            from services.scheduler import start_tpsl_monitor
            start_tpsl_monitor()
            logger.info("TP/SL保护监控任务已启动（60秒间隔）")
        except Exception as _e:
            logger.error(f"TP/SL监控启动失败（非致命）: {_e}")

        # Start market flow data collector (trades, orderbook, OI/funding)
        # 独立数据中心进程模式下由 worker 负责，主 API 跳过以免双开抢资源。
        _dc_mode = (os.environ.get("DATA_CENTER_MODE") or "embedded").strip().lower()
        _dc_external = _dc_mode in ("standalone", "external", "worker", "separate")
        if _dc_external:
            logger.info(
                "[Startup] DATA_CENTER_MODE=%s → 跳过主进程内 market_flow / 多所资金费采集",
                _dc_mode,
            )
        else:
            try:
                from services.scheduler import start_multi_venue_funding_collector
                start_multi_venue_funding_collector()
            except Exception as _e:
                logger.error(f"多场所资金费采集启动失败（非致命）: {_e}")

            try:
                from services.market_flow import market_flow_registry, register_defaults
                from config import settings
                register_defaults()
                active_exchanges = list(
                    getattr(settings, "ACTIVE_MARKET_FLOW_EXCHANGES", None)
                    or ["asterdex"]
                )
                # 仅当显式要求时才关掉 asterdex；默认所永远是 asterdex
                if os.getenv("MARKET_FLOW_DISABLE_ASTERDEX", "").lower() in (
                    "1", "true", "yes", "on",
                ):
                    active_exchanges = [e for e in active_exchanges if e != "asterdex"]
                if not active_exchanges:
                    active_exchanges = ["asterdex"]
                logger.info("[Startup] market_flow exchanges=%s", active_exchanges)
                cvd_window = getattr(settings, "CVD_AGGREGATION_WINDOW_SECONDS", 15)

                symbols_map = {}
                for ex in active_exchanges:
                    if ex == "asterdex":
                        try:
                            from services.trading_pairs_config import get_user_trading_pairs
                            trading_pairs = get_user_trading_pairs()
                            symbols_map[ex] = (
                                trading_pairs[:10] if trading_pairs else ["BTC", "ETH", "SOL"]
                            )
                        except Exception as e:
                            logger.warning(f"[Startup] asterdex symbols 加载失败: {e}")
                            symbols_map[ex] = ["BTC", "ETH", "SOL"]
                    else:
                        symbols_map[ex] = None

                def _start_mf():
                    try:
                        results = market_flow_registry.start_all(
                            symbols_map=symbols_map,
                            exchanges=active_exchanges,
                            aggregation_window_seconds=cvd_window,
                        )
                        logger.info(
                            "[Startup] 多交易所市场流采集器已启动: %s (window=%ss)",
                            results, cvd_window,
                        )
                    except Exception as e:
                        logger.error(
                            "[Startup] market_flow 后台启动失败: %s", e, exc_info=True
                        )
                        try:
                            from services.market_flow_collector import market_flow_collector
                            market_flow_collector.start()
                            logger.warning("[Startup] 回退到旧版单所 market_flow_collector")
                        except Exception as _e2:
                            logger.error(f"市场流采集器启动失败（非致命）: {_e2}")

                import threading as _threading
                _threading.Thread(
                    target=_start_mf, name="market-flow-boot", daemon=True
                ).start()
            except Exception as e:
                logger.error(f"[Startup] market_flow 注册失败（非致命）: {e}")

        # D7: 因子体系初始化 — 衰减监控 + 外部因子加载（始终在主 API）
        try:
            from services.factor_engine.factor_decay_monitor import decay_monitor
            from services.factor_engine.factor_loader import FactorLoader
            _fl = FactorLoader()
            _loaded = _fl.discover_and_load_all()
            # [§65 修复 2026-09-10] 这里走的是**顶格身份** `services.*`，而因子文件
            # 写的是 `from backend.services... import BaseFactor` ⇒ 两套模块对象、
            # BaseFactor 不一致 ⇒ 实测加载 **0 个因子**却打印"就绪"（假绿）。
            # 0 不可能是正常结果（因子树有 155 个文件），必须显式告警。
            if _loaded == 0:
                logger.warning(
                    "[Startup] 因子体系就绪但**加载 0 个因子**：本步走的 `services.*` 身份与"
                    "因子文件的 `backend.services.*` 身份不一致（模块身份分裂），"
                    "真实因子装载发生在 base_factors 合并路径。见报告 §65 / 决策 P16。",
                )
            logger.info(f"[Startup] 因子体系就绪: {_loaded}因子 + 衰减监控")
        except Exception as _fe:
            logger.warning(f"[Startup] 因子体系初始化跳过: {_fe}")

        # L1: 学习后端注册表加载
        try:
            from services.learning import backend_loader
            _bl_loaded = backend_loader.load_all()
            logger.info(f"[Startup] 学习后端注册表就绪: {_bl_loaded}个后端")
        except Exception as _lb_err:
            logger.warning(f"[Startup] 学习后端加载跳过: {_lb_err}")

        # D7: LLM自动因子发现 — 每10分钟检查
        try:
            from services.ai_factor_discovery_service import ai_factor_discovery
            def _run_factor_discovery():
                try:
                    from backend.database.connection import AnalyticsSessionLocal
                    db = AnalyticsSessionLocal()
                    try:
                        result = ai_factor_discovery.run_discovery_cycle(db)
                        if result.get("status") == "completed":
                            logger.info(f"[Startup] AI因子发现完成: {result.get('injected')}")
                    finally:
                        db.close()
                except Exception as _afd_err:
                    logger.debug(f"[Startup] AI因子发现跳过: {_afd_err}")
            task_scheduler.add_interval_task(
                task_func=_run_factor_discovery,
                interval_seconds=600,
                task_id="ai_factor_discovery",
            )
            logger.info("[Startup] AI因子发现定时任务已注册 (每10分钟)")
        except Exception as _afd_reg_err:
            logger.debug(f"[Startup] AI因子发现注册跳过: {_afd_reg_err}")

        # 市场流数据清理（主 API 仍可挂；独立 DC 模式也需要定期清）
        try:
            from services.market_flow_collector import cleanup_old_market_flow_data
            task_scheduler.add_interval_task(
                task_func=cleanup_old_market_flow_data,
                interval_seconds=6 * 3600,
                task_id="market_flow_data_cleanup"
            )
            logger.info("市场流数据清理任务已启动（6小时间隔，30天保留）")
        except Exception as _cleanup_err:
            logger.debug(f"[Startup] 市场流数据清理任务注册跳过: {_cleanup_err}")

        # Add trigger frequency monitoring task (every hour)
        try:
            from services.trigger_frequency_monitor import run_trigger_frequency_monitoring
            task_scheduler.add_interval_task(
                task_func=run_trigger_frequency_monitoring,
                interval_seconds=3600,
                task_id="trigger_frequency_monitoring"
            )
            logger.info("触发频率监控任务已启动（1小时间隔）")

            initial_report = run_trigger_frequency_monitoring()
            if initial_report.get("alert_count", 0) > 0:
                logger.warning(f"初始监控检测到 {initial_report['alert_count']} 个问题")
        except Exception as e:
            logger.warning(f"触发频率监控启动失败（非致命）: {e}")

        # 自主策略服务 — Phase 2 已废弃（stub，不再恢复活跃策略）
        try:
            from backend.services.autonomous_strategy_service import autonomous_service
            logger.debug("AutonomousStrategyService stub — no-op restore")
        except Exception as e:
            logger.debug(f"autonomous_strategy_service skip: {e}")

        # Phase 4: 启动爆仓预警监控器 + 注册紧急平仓回调
        try:
            from backend.services.liquidation_monitor import liquidation_monitor
            from backend.services.hyperliquid_trading_client import HyperliquidTradingClient
            from backend.database.connection import SessionLocal as _SL
            from backend.database.models import Account as _Account

            def _emergency_close_all(account_id: int, symbol: str, risk_obj):
                """爆仓预警触发的紧急平仓回调"""
                _risk_level = getattr(risk_obj, 'risk_level', str(risk_obj))
                logger.critical(
                    f"[EmergencyClose] 触发紧急平仓 account_id={account_id} "
                    f"symbol={symbol} risk_level={_risk_level}"
                )
                _db = _SL()
                try:
                    acc = _db.query(_Account).filter(_Account.id == account_id).first()
                    if not acc:
                        logger.error(f"[EmergencyClose] 账户 {account_id} 不存在")
                        return
                    client = HyperliquidTradingClient(
                        wallet_address=acc.wallet_address or "",
                        private_key=acc.api_secret or "",
                        is_mainnet=(acc.environment == "mainnet"),
                    )
                    client.close_all_positions(symbol=symbol)
                    logger.critical(
                        f"[EmergencyClose] 已对账户 {account_id} 执行紧急平仓 symbol={symbol}"
                    )
                except Exception as e:
                    logger.error(f"[EmergencyClose] 紧急平仓失败: {e}", exc_info=True)
                finally:
                    _db.close()

            liquidation_monitor.register_emergency_close_callback(_emergency_close_all)
            liquidation_monitor.start()
            logger.info("[Startup] 爆仓预警监控器已启动（30秒扫描），紧急平仓回调已注册")
        except Exception as e:
            logger.warning(f"[Startup] 爆仓预警监控器启动失败（不影响主服务）: {e}")

        # 数据库自动清理任务（每6小时清理过期K线、日志等）
        try:
            from backend.services.db_maintenance import run_db_maintenance
            if os.getenv("RUN_DB_MAINTENANCE_ON_STARTUP", "").lower() in ("1", "true", "yes"):
                run_db_maintenance()
            else:
                logger.info("跳过启动即刻数据库维护，仅注册定时任务")
            task_scheduler.add_interval_task(
                task_func=run_db_maintenance,
                interval_seconds=6 * 3600,
                task_id="db_maintenance"
            )
            logger.info("数据库维护任务已启动（6小时间隔）")
        except Exception as e:
            logger.warning(f"数据库维护任务启动失败（非致命）: {e}")

        # ── 全自动交易会话恢复已提前到调度器启动后立即执行 ──

        # 注册每日自学习复盘任务（每24小时）
        try:
            _daily_learning_marker = os.path.join("data", "daily_learning_last_run.json")

            def _daily_sweep_recently_ran() -> bool:
                # [2026-08-25 成本治理] 后端每次重启都会立即触发全量复盘（每策略一次
                # DeepSeek 调用、无用量记录），一晚三次重启 = 三遍全量烧钱。持久化
                # 最近一次运行时间：20 小时内已跑过则跳过（重启不再重复烧）。
                try:
                    import json as _json_dl
                    if os.path.exists(_daily_learning_marker):
                        _ts = _json_dl.load(open(_daily_learning_marker, encoding="utf-8")).get("ts", 0)
                        return (time.time() - float(_ts)) < 20 * 3600
                except Exception:
                    pass
                return False

            def _run_daily_learning():
                from backend.services.strategy_learning_service import strategy_learning
                try:
                    if _daily_sweep_recently_ran():
                        logger.info("每日学习复盘跳过（20h 内已运行过，避免重启重复烧钱）")
                        return
                    results = strategy_learning.run_all_reviews(days=7)
                    try:
                        import json as _json_dl
                        with open(_daily_learning_marker, "w", encoding="utf-8") as _fh_dl:
                            _json_dl.dump({"ts": time.time()}, _fh_dl)
                    except Exception:
                        pass
                    logger.info(f"每日学习复盘完成: {len(results)} 个策略")
                except Exception as e:
                    logger.error(f"每日学习复盘失败: {e}")

            task_scheduler.add_interval_task(
                task_func=_run_daily_learning,
                interval_seconds=24 * 3600,
                task_id="daily_strategy_learning"
            )
            logger.info("每日策略学习复盘任务已注册（24小时间隔）")
        except Exception as e:
            logger.error(f"学习任务注册失败: {e}")

        # 注册绩效矩阵数据衰减任务（每12小时）
        try:
            def _run_score_decay():
                from backend.database.connection import SessionLocal
                from backend.services.unified_learning_service import unified_learning
                _db = SessionLocal()
                try:
                    unified_learning.decay_old_scores(_db, decay_rate=0.98)
                except Exception as e:
                    logger.error(f"绩效矩阵衰减失败: {e}")
                finally:
                    _db.close()

            task_scheduler.add_interval_task(
                task_func=_run_score_decay,
                interval_seconds=12 * 3600,
                task_id="regime_score_decay",
            )
            logger.info("绩效矩阵衰减任务已注册（12小时间隔）")
        except Exception as e:
            logger.error(f"衰减任务注册失败: {e}")

        # [2026-08-22 M0-L1] 多频率训练复评调度：register_training_jobs() 定义了
        # 12 个学习任务（15m/1h/4h 周期复评、概念漂移检测、组合再平衡、晋升扫描、
        # 冠军恢复等），此前全仓唯一调用点是单测文件 → 生产环境多频率复盘与漂移
        # 检测整体从未运行（学习进化"半通"根因之一）。此处挂进启动流程，
        # 任何任务异常都在其内部被吞并记日志，不影响主循环。
        try:
            from backend.services.training_orchestrator import register_training_jobs
            register_training_jobs()
            logger.info("[Startup] 多频率训练复评任务已注册（training_orchestrator）")
        except Exception as e:
            logger.error(f"多频率训练复评任务注册失败（非致命）: {e}")

        # [2026-07-30] 学习进化调度曾禁用（无用功能 + 资源浪费）
        # [2026-08-06] 恢复：v6 验证要求学习进化 + OpenCode 调度真实运行
        # [2026-08-17] OpenCode 调度器已删除，只保留进化调度。
        try:
            from backend.services.evolution_scheduler import register_evolution_tasks
            register_evolution_tasks()
            logger.info("[Startup] 学习系统整合 (V2) 已恢复")
        except Exception as e:
            logger.error(f"进化调度任务注册失败: {e}")

        # 短线信号结算（每5分钟）。短线已停则不再注册，避免和进化调度重复空转。
        try:
            from backend.config.settings import SCALP_OPEN_DISABLED as _scalp_off
            if _scalp_off:
                logger.info("SCALP_OPEN_DISABLED，跳过短线信号结算注册")
            else:
                def _settle_scalp_signals():
                    from backend.services.scalp_signal_logger import settle_pending
                    settle_pending(limit=800)

                task_scheduler.add_interval_task(
                    task_func=_settle_scalp_signals,
                    interval_seconds=300,
                    task_id="scalp_signal_settle",
                )
                logger.info("短线信号结算任务已注册（5分钟间隔）")
        except Exception as e:
            logger.error(f"短线信号结算任务注册失败: {e}")

        # [2026-08-07 5.2] 观察池批量打标（v6 10.2.3 本地 LLM）：ollama 配置存在时
        # 每 6 小时对选币观察池（auto_coin_selections 注入样本）批量打标，
        # min_samples=3 门槛 + selection_id 唯一幂等；ollama 未启用时静默跳过。
        try:
            from backend.services.local_llm.batch_labeler import register_batch_labeler_job
            register_batch_labeler_job(task_scheduler, interval_hours=6)
        except Exception as _bl_err:
            logger.warning("[Startup] 批量打标任务注册失败（非致命）: %s", _bl_err)

        # ═══ 智能多周期系统定时任务 ═══

        # 新闻情报: 每5分钟拉取
        try:
            def _run_news_fetch():
                import asyncio
                # [2026-08-15 P0-1 修复] news_events 表在 Market DB（alpha_market），
                # 必须用 MarketSessionLocal；此前用核心库 SessionLocal 导致落库
                # 静默失败、news_events 长期 0 行。
                from backend.database.connection import MarketSessionLocal
                from backend.services.news_intelligence_service import news_intelligence
                db = MarketSessionLocal()
                try:
                    loop = asyncio.new_event_loop()
                    loop.run_until_complete(news_intelligence.fetch_and_analyze(db))
                    loop.close()
                except Exception as e:
                    logger.error(f"新闻拉取任务异常: {e}")
                finally:
                    db.close()

            task_scheduler.add_interval_task(
                task_func=_run_news_fetch,
                interval_seconds=300,
                task_id="news_intelligence_fetch"
            )
            logger.info("新闻情报定时任务已注册（5分钟间隔）")
            # [2026-08-15 D7] 突发新闻快通道（CryptoPanic important，90s，≤3条/轮）
            try:
                from backend.services.news_intelligence_service import news_intelligence as _ni
                _ni.start_fast_loop(interval_sec=90)
            except Exception as _fast_err:
                logger.warning(f"新闻快通道启动失败（非致命）: {_fast_err}")
        except Exception as e:
            logger.error(f"新闻定时任务注册失败: {e}")

        # 鲸鱼追踪: 每2分钟拉取
        try:
            def _run_whale_fetch():
                import asyncio
                # [2026-08-15 P0-1 修复] whale_activities 表在 Market DB（alpha_market），
                # 必须用 MarketSessionLocal；此前用核心库 SessionLocal 导致落库
                # 静默失败、whale_activities 长期 0 行。
                from backend.database.connection import MarketSessionLocal
                from backend.services.whale_tracker_service import whale_tracker
                db = MarketSessionLocal()
                try:
                    loop = asyncio.new_event_loop()
                    loop.run_until_complete(whale_tracker.fetch_and_record(db))
                    loop.close()
                except Exception as e:
                    logger.error(f"鲸鱼追踪任务异常: {e}")
                finally:
                    db.close()

            task_scheduler.add_interval_task(
                task_func=_run_whale_fetch,
                interval_seconds=120,
                task_id="whale_tracker_fetch"
            )
            logger.info("鲸鱼追踪定时任务已注册（2分钟间隔）")
        except Exception as e:
            logger.error(f"鲸鱼定时任务注册失败: {e}")

        # [2026-08-15 D6] 宏观数据采集：FRED 日频序列 + 官方日历（Fed/BLS）+
        # 发布后 LLM 影响标注。后台线程自循环（每日日历/FRED、每小时标注检查）。
        try:
            from backend.services.macro_data_collector import start_macro_collector
            start_macro_collector()
        except Exception as e:
            logger.warning(f"宏观采集器启动失败（非致命）: {e}")

        # AI日复盘: 每24小时（UTC 21:00）
        try:
            def _run_daily_journal():
                import asyncio
                from backend.services.ai_trade_journal_service import trade_journal
                try:
                    loop = asyncio.new_event_loop()
                    loop.run_until_complete(trade_journal.daily_review())
                    loop.close()
                except Exception as e:
                    logger.error(f"AI日复盘异常: {e}")

            task_scheduler.add_interval_task(
                task_func=_run_daily_journal,
                interval_seconds=24 * 3600,
                task_id="ai_daily_journal"
            )
            logger.info("AI日复盘定时任务已注册（24小时间隔）")
        except Exception as e:
            logger.error(f"AI复盘定时任务注册失败: {e}")

        # ═══ 战略分析师定时任务 ═══
        # 完整战略分析: 每4小时
        try:
            def _run_strategic_analysis():
                from backend.services.strategic_analyst.engine import get_strategic_engine
                try:
                    engine = get_strategic_engine()
                    report = engine.run_full_analysis()
                    try:
                        from backend.services.macro_regime_service import macro_regime_service
                        from backend.services.unified_data_pool import unified_data_pool
                        snap = unified_data_pool.get_snapshot(max_age=600)
                        macro_regime_service.update_from_sources(
                            strategic_report=report, snapshot=snap,
                        )
                        macro_regime_service.invalidate_cache()
                    except Exception as _mre:
                        logger.warning(f"宏观周期心智更新失败: {_mre}")
                except Exception as e:
                    logger.error(f"战略分析任务异常: {e}")

            task_scheduler.add_interval_task(
                task_func=_run_strategic_analysis,
                interval_seconds=4 * 3600,
                task_id="strategic_full_analysis"
            )
            logger.info("战略分析师定时任务已注册（4小时间隔）")
        except Exception as e:
            logger.error(f"战略分析师定时任务注册失败: {e}")

        # 新币扫描: 每1小时（不含LLM，仅发现+评估）
        try:
            def _run_new_coin_scan():
                from backend.services.strategic_analyst.engine import get_strategic_engine
                try:
                    engine = get_strategic_engine()
                    engine.run_new_coin_scan()
                except Exception as e:
                    logger.error(f"新币扫描任务异常: {e}")

            task_scheduler.add_interval_task(
                task_func=_run_new_coin_scan,
                interval_seconds=3600,
                task_id="strategic_new_coin_scan"
            )
            logger.info("新币扫描定时任务已注册（1小时间隔）")
        except Exception as e:
            logger.error(f"新币扫描定时任务注册失败: {e}")

        # 记忆验证与规则提取: 每24小时
        try:
            def _run_memory_validation():
                from backend.services.strategic_analyst.engine import get_strategic_engine
                try:
                    engine = get_strategic_engine()
                    engine.run_memory_validation()
                except Exception as e:
                    logger.error(f"记忆验证任务异常: {e}")

            task_scheduler.add_interval_task(
                task_func=_run_memory_validation,
                interval_seconds=24 * 3600,
                task_id="strategic_memory_validate"
            )
            logger.info("战略记忆验证定时任务已注册（24小时间隔）")
        except Exception as e:
            logger.error(f"记忆验证定时任务注册失败: {e}")

        # 初始化策略模板库种子数据
        try:
            _seed_strategy_templates()
        except Exception as e:
            logger.warning(f"策略模板种子数据初始化失败（非致命）: {e}")

        # 后台预热 RAG 向量库（embedding 模型 ~1.3GB 加载需要数十秒，
        # 启动时后台加载避免首次使用时阻塞交易循环）
        try:
            import threading as _rag_thread
            def _warmup_rag():
                try:
                    from backend.services.rag_knowledge_service import rag_knowledge_service
                    ready = rag_knowledge_service._ensure_ready()
                    if ready and not rag_knowledge_service._degraded:
                        logger.info("[Startup] RAG 向量库预热完成")
                    else:
                        logger.warning("[Startup] RAG 向量库预热降级（embedding 模型不可用）")
                except Exception as e:
                    logger.warning(f"[Startup] RAG 预热跳过: {e}")
            _rag_thread.Thread(target=_warmup_rag, daemon=True, name="rag-warmup").start()
        except Exception:
            pass

        # 初始化 LLM 预设（仅全新空库时执行一次；用户删除的配置不会复活）
        try:
            _seed_llm_presets_once()
        except Exception as e:
            logger.warning(f"LLM 预设初始化失败（非致命）: {e}")

        # ── QAA v3.0 初始化 (TickOrchestrator + TradingPlugin + QAABridge) ──
        try:
            from backend.config.settings import QAA_MODE, QAA_V3_ENABLED

            # QAABridge 进化系统 (始终初始化，独立于调度模式)
            from backend.services.qaa_evolution_bridge import qaa_bridge
            qaa_bridge.initialize(persist_dir="./data/qaa_evolution")

            # auto_discover 扫描全库策略极慢（~2min），放到后台避免阻塞 QAAContext
            def _qaa_evolution_deferred():
                from backend.database.connection import SessionLocal as _qaa_SL
                _qaa_db = _qaa_SL()
                try:
                    qaa_bridge.strategy_tuner.auto_discover(_qaa_db)
                    qaa_bridge.restore_grayscale_plans(_qaa_db)
                    logger.info("[Startup] QAA 进化 auto_discover 后台完成")
                except Exception as _disc_err:
                    logger.warning(f"[Startup] QAA auto_discover 后台失败: {_disc_err}")
                finally:
                    _qaa_db.close()

            threading.Thread(
                target=_qaa_evolution_deferred,
                daemon=True,
                name="qaa-evolution-init",
            ).start()
            task_scheduler.add_interval_task(
                task_func=qaa_bridge.run_optimization_cycle,
                interval_seconds=300,
                task_id="qaa_optimization_cycle",
            )
            logger.info("[Startup] QAA 进化系统已初始化（auto_discover 后台运行）")

            # QAAContext + TradingPlugin（若 restore 阶段未提前完成则在此补全）
            if QAA_MODE == "qaa" and QAA_V3_ENABLED:
                from backend.services.full_auto_trading_service import full_auto_service
                full_auto_service.bootstrap_qaa_v3_context(blocking=False)
                if getattr(full_auto_service, "_qaa_ctx", None):
                    _qaa_ctx = full_auto_service._qaa_ctx
                    logger.info(
                        f"[Startup] QAA v3.0 已就绪: domains={_qaa_ctx.registry.get_domains()}, "
                        f"agents={_qaa_ctx.registry.stats.get('total_cards', 0)}"
                    )
                else:
                    logger.info("[Startup] QAA v3.0 后台 bootstrap 进行中")
            else:
                logger.info(
                    f"[Startup] QAA v3.0 已退役，不进主循环 "
                    f"(QAA_MODE={QAA_MODE}, QAA_V3_ENABLED={QAA_V3_ENABLED})"
                )

        except Exception as _qaa_err:
            logger.warning(f"[Startup] QAA 初始化跳过（非致命）: {_qaa_err}")

        # ══════════════════════════════════════════════════════════
        #  启动完整性自检（2026-06-22）
        #  验证因子引擎、学习循环、进化任务等核心组件是否正常启动。
        # ══════════════════════════════════════════════════════════
        _health_report = []
        _health_issues = []

        # 1) 因子引擎 Registry 合并验证
        try:
            from backend.services.factor_engine.base_factors import FactorEngine
            _factor_count = len(FactorEngine.FACTORS)
            if _factor_count >= 100:
                _health_report.append(f"✅ 因子引擎: {_factor_count} 因子（≥100，正常）")
            elif _factor_count > 21:
                _health_report.append(f"⚠️ 因子引擎: {_factor_count} 因子（<100，可能 Registry 合并不完整）")
                _health_issues.append(f"FactorEngine 仅 {_factor_count} 因子，建议检查 _merge_registry")
            else:
                _health_report.append(f"🔴 因子引擎: {_factor_count} 因子（≤21，严重退化！只加载了 base 因子）")
                _health_issues.append(f"CRITICAL: FactorEngine 仅 {_factor_count} 因子，Registry 合并可能失败")
        except Exception as _fe_health:
            _health_report.append(f"🔴 因子引擎: 检查失败 ({_fe_health})")
            _health_issues.append(f"FactorEngine 健康检查异常: {_fe_health}")

        # 2) EvolutionEvent 表可用性
        try:
            from backend.database.connection import SessionLocal as _HSL
            from backend.database.models import EvolutionEvent
            _hdb = _HSL()
            try:
                _evt_count = _hdb.query(EvolutionEvent).count()
                _health_report.append(f"✅ EvolutionEvent 表: {_evt_count} 条历史记录")
            finally:
                _hdb.close()
        except Exception as _evt_health:
            _health_report.append(f"⚠️ EvolutionEvent 表: 不可用 ({_evt_health})")
            _health_issues.append(f"EvolutionEvent 表查询失败: {_evt_health}")

        # 3) 学习循环注册状态
        try:
            from backend.services.learning_loop_service import learning_loop
            _ll_registered = getattr(learning_loop, '_registered', False)
            if _ll_registered:
                _health_report.append("✅ LearningLoop: 已注册")
            else:
                _health_report.append("⚠️ LearningLoop: 未注册（可能在 register_evolution_tasks 后完成）")
        except Exception as _ll_health:
            _health_report.append(f"⚠️ LearningLoop: 检查失败 ({_ll_health})")

        # 4) 进化任务注册状态
        try:
            from backend.services.evolution_scheduler import evolution_scheduler
            _es_exists = evolution_scheduler is not None
            if _es_exists:
                _health_report.append("✅ EvolutionScheduler: 实例已就绪")
            else:
                _health_report.append("🔴 EvolutionScheduler: 实例不存在")
                _health_issues.append("EvolutionScheduler 实例不存在")
        except Exception as _es_health:
            _health_report.append(f"⚠️ EvolutionScheduler: 检查失败 ({_es_health})")

        # 输出健康报告
        logger.info("[Startup] ═══ 启动健康报告 ═══")
        for _line in _health_report:
            logger.info(f"[Startup] {_line}")
        if _health_issues:
            logger.warning(f"[Startup] 发现 {len(_health_issues)} 个问题: {_health_issues}")
        else:
            logger.info("[Startup] ✅ 所有核心组件通过健康检查")
        logger.info("[Startup] ═══════════════════════")

        logger.info("同步服务初始化成功")

    except Exception as e:
        logger.error(f"Service initialization failed: {e}")
        raise


def _seed_strategy_templates():
    """向 strategy_templates 表插入/更新内置策略模板（使用 upsert 逻辑）"""
    from backend.database.connection import SessionLocal
    from backend.database.models import StrategyTemplate

    REMOVED_TEMPLATE_IDS = [
        "tpl_multi_tf_momentum",
        "tpl_fib_swing",
        "tpl_grid_range",
        "tpl_extreme_snowball",
        "tpl_ema_trend",
        "tpl_bb_reversion",
        "tpl_breakout",
        "tpl_rsi_oversold",
        "tpl_macd_divergence",
        "tpl_volatility_breakout",
        "tpl_pullback_swing",
    ]

    BUILTIN_TEMPLATES = [
        # ═══════════════════════════════════════════
        #  短线 (short) — 日内交易，5m/15m K线
        # ═══════════════════════════════════════════
        {
            "template_id": "tpl_short_trend",
            "name": "日内趋势追踪",
            "description": "5分钟级别 EMA 交叉捕捉日内趋势，快进快出。适合高频波动的加密货币市场。",
            "category": "trend",
            "market_regime": "bull",
            "risk_level": "moderate",
            "timeframe": "5m",
            "tier": "short",
            "source": "builtin",
            "author": "system",
            "version": "2.0",
            "rating": 4.0,
            "tags": ["短线", "日内", "趋势", "EMA"],
            "strategy_config": {
                "category": "trend",
                "strategy_logic": "5m EMA快/中线交叉 + MACD确认 + 放量入场。短线快进快出，每日多笔交易。",
                "risk_params": {
                    "max_position_size": 0.15,
                    "stop_loss_pct": 0.02,
                    "take_profit_pct": 0.04,
                    "trailing_activation_pct": 0.02,
                    "trailing_distance_pct": 0.01,
                    "max_daily_loss": 0.06,
                    "max_leverage": 20,
                    "default_leverage": 10,
                    "signal_params": {
                        "ema_fast": 5, "ema_mid": 12, "ema_slow": 30,
                        "rsi_period": 10, "rsi_long_lo": 25, "rsi_long_hi": 88,
                        "rsi_short_lo": 12, "rsi_short_hi": 75,
                        "min_bars_between": 1,
                    }
                },
                "applicable_symbols": ["BTC", "ETH", "SOL"],
            },
        },
        {
            "template_id": "tpl_short_momentum",
            "name": "动量剥头皮",
            "description": "15分钟级别 MACD 动量加速 + 放量确认，捕捉短期强势动量延续。",
            "category": "momentum",
            "market_regime": "bull",
            "risk_level": "aggressive",
            "timeframe": "15m",
            "tier": "short",
            "source": "builtin",
            "author": "system",
            "version": "2.0",
            "rating": 3.8,
            "tags": ["短线", "动量", "MACD", "剥头皮"],
            "strategy_config": {
                "category": "momentum",
                "strategy_logic": "15m MACD柱加速 + EMA多头 + 放量 → 追涨。RSI范围放宽适配加密高波动。",
                "risk_params": {
                    "max_position_size": 0.15,
                    "stop_loss_pct": 0.025,
                    "take_profit_pct": 0.05,
                    "trailing_activation_pct": 0.025,
                    "trailing_distance_pct": 0.012,
                    "max_daily_loss": 0.08,
                    "max_leverage": 20,
                    "default_leverage": 10,
                    "signal_params": {
                        "ema_fast": 6, "ema_mid": 14, "ema_slow": 35,
                        "rsi_long_hi": 90, "rsi_short_lo": 10,
                        "momentum_vol_mult": 1.1, "min_bars_between": 1,
                    }
                },
                "applicable_symbols": ["BTC", "ETH", "SOL"],
            },
        },
        {
            "template_id": "tpl_short_range",
            "name": "日内区间震荡",
            "description": "5分钟级别布林带边缘 + RSI 超买超卖，在区间内高抛低吸。适合震荡行情。",
            "category": "range",
            "market_regime": "sideways",
            "risk_level": "conservative",
            "timeframe": "5m",
            "tier": "short",
            "source": "builtin",
            "author": "system",
            "version": "2.0",
            "rating": 4.0,
            "tags": ["短线", "区间", "布林带", "震荡"],
            "strategy_config": {
                "category": "range",
                "strategy_logic": "5m 布林带边缘 + RSI极值 → 反向操作。量能枯竭确认反转。",
                "risk_params": {
                    "max_position_size": 0.12,
                    "stop_loss_pct": 0.015,
                    "take_profit_pct": 0.03,
                    "trailing_activation_pct": 0.015,
                    "trailing_distance_pct": 0.008,
                    "max_daily_loss": 0.05,
                    "max_leverage": 20,
                    "default_leverage": 10,
                    "signal_params": {
                        "bb_period": 15, "bb_std": 1.8, "bb_edge_pct": 0.25,
                        "rsi_ob": 75, "rsi_os": 25, "min_bars_between": 1,
                    }
                },
                "applicable_symbols": ["BTC", "ETH"],
            },
        },

        # ═══════════════════════════════════════════
        #  中线 (mid) — 波段交易，1h/4h K线
        # ═══════════════════════════════════════════
        {
            "template_id": "tpl_mid_swing",
            "name": "波段趋势回调",
            "description": "1小时级别趋势确认后等待回调入场，经典趋势交易手法。持仓1-7天。",
            "category": "swing",
            "market_regime": "all",
            "risk_level": "moderate",
            "timeframe": "1h",
            "tier": "mid",
            "source": "builtin",
            "author": "system",
            "version": "2.0",
            "rating": 4.2,
            "tags": ["中线", "波段", "回调", "经典"],
            "strategy_config": {
                "category": "swing",
                "strategy_logic": "1h EMA中/慢线确定趋势 → 价格回调到EMA中线 → RSI/MACD确认反弹 → 入场。",
                "risk_params": {
                    "max_position_size": 0.18,
                    "stop_loss_pct": 0.04,
                    "take_profit_pct": 0.10,
                    "trailing_activation_pct": 0.04,
                    "trailing_distance_pct": 0.025,
                    "max_daily_loss": 0.10,
                    "max_leverage": 20,
                    "default_leverage": 10,
                    "signal_params": {
                        "ema_fast": 9, "ema_mid": 21, "ema_slow": 55,
                        "swing_pullback_lo": -0.06, "swing_pullback_hi": 0.01,
                        "rsi_long_lo": 25, "rsi_short_hi": 75,
                        "min_bars_between": 2,
                    }
                },
                "applicable_symbols": ["BTC", "ETH", "SOL", "BNB"],
            },
        },
        {
            "template_id": "tpl_mid_reversion",
            "name": "均值回归波段",
            "description": "4小时级别布林带 + RSI 极值捕捉超买超卖反转，波段级别的均值回归。持仓1-5天。",
            "category": "mean_reversion",
            "market_regime": "sideways",
            "risk_level": "moderate",
            "timeframe": "4h",
            "tier": "mid",
            "source": "builtin",
            "author": "system",
            "version": "2.0",
            "rating": 4.0,
            "tags": ["中线", "均值回归", "布林带"],
            "strategy_config": {
                "category": "mean_reversion",
                "strategy_logic": "4h 布林带上/下轨 + RSI极值 + 量能枯竭 → 反向波段操作。",
                "risk_params": {
                    "max_position_size": 0.15,
                    "stop_loss_pct": 0.05,
                    "take_profit_pct": 0.08,
                    "trailing_activation_pct": 0.04,
                    "trailing_distance_pct": 0.025,
                    "max_daily_loss": 0.10,
                    "max_leverage": 20,
                    "default_leverage": 10,
                    "signal_params": {
                        "bb_period": 20, "bb_std": 2.0,
                        "rsi_ob": 72, "rsi_os": 28,
                        "vol_quiet_mult": 0.8, "min_bars_between": 2,
                    }
                },
                "applicable_symbols": ["BTC", "ETH"],
            },
        },

        # ═══════════════════════════════════════════
        #  长线 (long) — 趋势跟随，4h/1d K线
        # ═══════════════════════════════════════════
        {
            "template_id": "tpl_long_trend",
            "name": "长线趋势跟随",
            "description": "4小时级别 EMA 多头排列 + MACD 确认大趋势。持仓1-4周，捕捉大波段。",
            "category": "trend",
            "market_regime": "bull",
            "risk_level": "moderate",
            "timeframe": "4h",
            "tier": "long",
            "source": "builtin",
            "author": "system",
            "version": "2.0",
            "rating": 4.0,
            "tags": ["长线", "趋势跟随", "大波段"],
            "strategy_config": {
                "category": "trend",
                "strategy_logic": "4h EMA多头排列 + MACD方向确认 → 做多持有。宽止损配大止盈，追求大趋势利润。",
                "risk_params": {
                    "max_position_size": 0.12,
                    "stop_loss_pct": 0.07,
                    "take_profit_pct": 0.18,
                    "trailing_activation_pct": 0.06,
                    "trailing_distance_pct": 0.035,
                    "max_daily_loss": 0.12,
                    "max_leverage": 20,
                    "default_leverage": 10,
                    "signal_params": {
                        "ema_fast": 12, "ema_mid": 26, "ema_slow": 100,
                        "rsi_long_lo": 30, "rsi_long_hi": 80,
                        "rsi_short_lo": 20, "rsi_short_hi": 70,
                        "min_bars_between": 4,
                    }
                },
                "applicable_symbols": ["BTC", "ETH"],
            },
        },
        {
            "template_id": "tpl_long_breakout",
            "name": "突破持仓",
            "description": "4小时级别突破近期高/低点 + 放量确认，建仓后长期持有。适合趋势启动初期。",
            "category": "breakout",
            "market_regime": "bull",
            "risk_level": "aggressive",
            "timeframe": "4h",
            "tier": "long",
            "source": "builtin",
            "author": "system",
            "version": "2.0",
            "rating": 3.8,
            "tags": ["长线", "突破", "持仓"],
            "strategy_config": {
                "category": "breakout",
                "strategy_logic": "4h 突破N周期高/低点 + 放量 + EMA趋势确认 → 建仓长持。大止盈配宽止损。",
                "risk_params": {
                    "max_position_size": 0.15,
                    "stop_loss_pct": 0.06,
                    "take_profit_pct": 0.15,
                    "trailing_activation_pct": 0.06,
                    "trailing_distance_pct": 0.035,
                    "max_daily_loss": 0.12,
                    "max_leverage": 20,
                    "default_leverage": 10,
                    "signal_params": {
                        "breakout_lookback": 30, "vol_surge_mult": 1.3,
                        "ema_fast": 10, "ema_mid": 25,
                        "min_bars_between": 5,
                    }
                },
                "applicable_symbols": ["BTC", "ETH", "SOL"],
            },
        },
    ]

    db = SessionLocal()
    try:
        # 清理已移除的虚假模板
        removed = db.query(StrategyTemplate).filter(
            StrategyTemplate.template_id.in_(REMOVED_TEMPLATE_IDS)
        ).delete(synchronize_session=False)
        if removed:
            logger.info(f"[Seed] 已删除 {removed} 个不可回测的旧模板")

        # Upsert: 存在则更新，不存在则插入
        for tpl_data in BUILTIN_TEMPLATES:
            existing = db.query(StrategyTemplate).filter(
                StrategyTemplate.template_id == tpl_data["template_id"]
            ).first()
            if existing:
                for key, val in tpl_data.items():
                    if key != "template_id":
                        setattr(existing, key, val)
            else:
                db.add(StrategyTemplate(**tpl_data))

        db.commit()
        logger.info(f"[Seed] 策略模板库已同步: {len(BUILTIN_TEMPLATES)} 个内置模板")
    except Exception as e:
        db.rollback()
        logger.warning(f"[Seed] 策略模板种子数据失败: {e}")
    finally:
        db.close()


def _seed_llm_presets_once():
    """多账户模式下**不再**用 env 种子写入「全站默认 DeepSeek/本地 Key」。

    每个登录用户必须在设置页自行添加 LLM（BYOK）。
    若关闭 FORBID_SHARED_PLATFORM_LLM，才允许旧的空库种子行为。
    """
    try:
        from backend.config.settings import FORBID_SHARED_PLATFORM_LLM
        if FORBID_SHARED_PLATFORM_LLM:
            logger.info(
                "[Seed] FORBID_SHARED_PLATFORM_LLM=true："
                "跳过平台级 LLM 种子（各账户请自备 API Key）"
            )
            return
    except Exception:
        logger.info("[Seed] 跳过平台级 LLM 种子（默认禁止公用）")
        return

    from backend.database.connection import SessionLocal
    from backend.database.models import LLMConfiguration

    db = SessionLocal()
    try:
        if db.query(LLMConfiguration).count() > 0:
            logger.info("[Seed] LLM 配置库非空，跳过预设初始化")
            return
    finally:
        db.close()
    _seed_deepseek_config()
    _seed_local_llm_config()


def _seed_local_llm_config():
    """
    初始化内网本地 LLM 预设配置。
    
    当以下条件同时满足时自动写入一条 "内网本地模型" 记录：
    - 数据库中不存在 provider='local' 的任何记录
    - 用户在环境变量中提供了 LOCAL_LLM_API_KEY（或使用默认示例 key）
    
    目标：用户首次启动后，LLM 配置界面即可看到内网模型选项，无需手动创建。
    """
    import os
    from backend.database.connection import SessionLocal
    from backend.database.models import LLMConfiguration

    base_url = os.getenv("LOCAL_LLM_BASE_URL", "http://10.29.193.24:8888/v1")
    api_key = os.getenv("LOCAL_LLM_API_KEY", "sk-your-local-api-key")
    model = os.getenv("LOCAL_LLM_MODEL", "local-model")
    preset_name = os.getenv("LOCAL_LLM_NAME", "内网本地模型 (Unsloth)")

    db = SessionLocal()
    try:
        existed = db.query(LLMConfiguration).filter(
            LLMConfiguration.provider == "local"
        ).first()
        if existed:
            logger.info(f"[Seed] 内网 LLM 配置已存在（id={existed.id}），跳过初始化")
            return

        config = LLMConfiguration(
            name=preset_name,
            provider="local",
            description="内网部署的 OpenAI 兼容大模型服务（Unsloth / vLLM / Ollama / LM Studio）",
            model=model,
            base_url=base_url,
            api_key=api_key,
            is_default="false",
            is_active="true",
            test_status="pending",
        )
        db.add(config)
        db.commit()
        db.refresh(config)
        logger.info(
            f"[Seed] 已写入内网 LLM 预设: id={config.id}, base_url={base_url}, "
            f"model={model}（默认未启用为全局默认，可在 LLM 配置页面切换）"
        )
    except Exception as e:
        db.rollback()
        logger.warning(f"[Seed] 内网 LLM 预设写入失败: {e}")
    finally:
        db.close()


def _seed_deepseek_config():
    """[2026-09-05] deepseek 全面退役（GLM 全面替代）后的空占位，保留调用点兼容。

    原逻辑：只要 DEEPSEEK_API_KEY 存在且 llm_configurations 无 deepseek 记录，
    就自动写入一条 is_default 的 DeepSeek V4 Flash 预设 —— coin_select 等按
    「管理员默认配置」取模的服务会因此复活 deepseek 直连流量，故整体停用。
    """
    logger.info("[Seed] deepseek 已退役（GLM 全面替代），跳过 DeepSeek 预设")
    return


async def shutdown_services():
    """Shut down all services"""
    global _scheduler_lock_fd, _scheduler_initialized
    try:
        from services.scheduler import stop_scheduler
        from services.hyperliquid_snapshot_service import hyperliquid_snapshot_service
        from services.kline_realtime_collector import realtime_collector

        # ── 优先注销 FullAuto 定时任务（2026-06-17 修复）──
        # 原顺序是先 stop_strategy_manager / 各 collector，最后才注销 fullauto job，
        # 期间 fullauto 的 reload tick 仍可能抢 add_job，命中 apscheduler
        # ``cannot schedule new futures after shutdown``（日志 140 次）。
        # 现提到最前：scheduler 还活着时先把易 reload 的 fullauto job 干净摘掉。
        try:
            from backend.services.full_auto_trading_service import full_auto_service
            for sid in list(getattr(full_auto_service, "_session_intervals", {}).keys()):
                full_auto_service._unregister_health_check(sid)
        except Exception as _fa_stop_err:
            logger.debug("[Shutdown] fullauto job cleanup: %s", _fa_stop_err)

        # 停止自主策略服务 — Phase 2 存根（no-op）
        try:
            from backend.services.autonomous_strategy_service import autonomous_service
            autonomous_service.stop_all()
        except Exception:
            pass

        stop_strategy_manager()
        stop_market_price_services()
        unsubscribe_price_updates(handle_price_update)
        hyperliquid_snapshot_service.stop()

        # Stop K-line realtime collector (must await — create_task 会导致 Task exception was never retrieved)
        try:
            await realtime_collector.stop()
        except Exception as _kline_stop_err:
            logger.debug("[Shutdown] kline collector stop: %s", _kline_stop_err)

        # Stop market flow collectors (新版注册表 + 旧单例兜底)
        try:
            from services.market_flow import market_flow_registry
            market_flow_registry.stop_all()
        except Exception as _mf_stop_err:
            logger.debug("[Shutdown] market_flow_registry stop: %s", _mf_stop_err)
        try:
            from services.market_flow_collector import market_flow_collector
            if market_flow_collector.running:
                market_flow_collector.stop()
        except Exception as _old_mf_err:
            logger.debug("[Shutdown] legacy market_flow_collector stop: %s", _old_mf_err)

        # Binance user stream removed (Phase 1: Binance removed)

        stop_scheduler()

        # [2026-08-17 删除] opencode_sidecar 已退役，无 sidecar 需要回收。

        logger.info("All services have been shut down")

    except Exception as e:
        logger.error(f"Failed to shut down services: {e}")
    finally:
        if _scheduler_lock_fd is not None:
            try:
                _scheduler_lock_fd.close()
            except Exception:
                pass
            _scheduler_lock_fd = None
        _scheduler_initialized = False


async def startup_event():
    """FastAPI application startup event"""
    initialize_services()


async def shutdown_event():
    """FastAPI application shutdown event"""
    await shutdown_services()


def schedule_auto_trading(interval_seconds: int = 300, max_ratio: float = 0.2, use_ai: bool = True) -> None:
    """Schedule automatic trading tasks
    
    Args:
        interval_seconds: Interval between trading attempts
        max_ratio: Maximum portion of portfolio to use per trade
        use_ai: If True, use AI-driven trading; if False, use random trading
    """
    from services.trading_commands import (
        place_ai_driven_crypto_order,
        place_random_crypto_order,
        AUTO_TRADE_JOB_ID,
        AI_TRADE_JOB_ID,
    )

    def execute_trade():
        try:
            if use_ai:
                place_ai_driven_crypto_order(max_ratio)
            else:
                place_random_crypto_order(max_ratio)
            logger.info("Initial auto-trading execution completed")
        except Exception as e:
            logger.error(f"Error during initial auto-trading execution: {e}")

    if use_ai:
        task_func = place_ai_driven_crypto_order
        job_id = AI_TRADE_JOB_ID
        logger.info("Scheduling AI-driven crypto trading")
    else:
        task_func = place_random_crypto_order
        job_id = AUTO_TRADE_JOB_ID
        logger.info("Scheduling random crypto trading")

    # Schedule the recurring task
    task_scheduler.add_interval_task(
        task_func=task_func,
        interval_seconds=interval_seconds,
        task_id=job_id,
        max_ratio=max_ratio,
    )
    
    # Execute the first trade immediately in a separate thread to avoid blocking
    initial_trade = threading.Thread(target=execute_trade, daemon=True)
    initial_trade.start()


# ─────────────────────────────────────────────────────────────
# [2026-08-22 M3-3] 每日对账/退出审计（凌晨 01:05 本地时间）
# 三类不变量任一失败 → 记录 CRITICAL（供告警/人工复核）：
#   ① orders_pnl==realized  ② orders_fee==fee  ③ equity==initial+realized-fee+unrealized
# 同时刷新 exit_audit_report.json（分批 PnL 双计已修复后的口径）。
# ─────────────────────────────────────────────────────────────
try:
    def _daily_reconcile_tick() -> None:
        try:
            from backend.database.connection import SessionLocal
            from sqlalchemy import text as _t
            db = SessionLocal()
            try:
                from backend.core.tenant import set_system_identity
                set_system_identity()
                rows = db.execute(_t("""
                    SELECT pb.account_id,
                           pb.initial_balance, pb.total_equity, pb.realized_pnl,
                           pb.total_fee_paid, pb.unrealized_pnl,
                           (SELECT coalesce(sum(o.pnl),0) FROM paper_orders o
                             WHERE o.account_id=pb.account_id AND o.pnl IS NOT NULL) AS order_pnl,
                           (SELECT coalesce(sum(o.fee),0) FROM paper_orders o
                             WHERE o.account_id=pb.account_id AND o.fee IS NOT NULL) AS order_fee
                    FROM paper_balances pb
                """)).fetchall()
                for r in rows:
                    ok = (
                        abs(float(r.realized_pnl or 0) - float(r.order_pnl or 0)) < 0.05
                        and abs(float(r.total_fee_paid or 0) - float(r.order_fee or 0)) < 0.05
                        and abs(float(r.total_equity) - (
                            float(r.initial_balance) + float(r.realized_pnl or 0)
                            - float(r.total_fee_paid or 0) + float(r.unrealized_pnl or 0)
                        )) < 0.05
                    )
                    if not ok:
                        logger.error(
                            "[Reconcile] 对账失败 account=%s equity=%.2f realized=%.2f "
                            "orders_pnl=%.2f fee=%.2f orders_fee=%.2f",
                            r.account_id, float(r.total_equity), float(r.realized_pnl or 0),
                            float(r.order_pnl or 0), float(r.total_fee_paid or 0),
                            float(r.order_fee or 0),
                        )
                logger.info("[Reconcile] 每日对账完成（账户数=%d）", len(rows))
                # [2026-08-22 PROFIT-3] EV 反馈审计：真实结果 → 自动放大正期望簇 /
                # 收缩负期望簇（不做开单门禁，做资金分配）
                try:
                    from backend.core.tenant import set_system_identity
                    set_system_identity()
                    from backend.services.ev_governor import audit_and_write
                    audit_and_write(db)
                except Exception as _ev_err:
                    logger.warning("[EvGovernor] 每日审计失败: %s", _ev_err)
            finally:
                db.close()
        except Exception as _re_e:
            logger.error("[Reconcile] 每日对账任务异常: %s", _re_e, exc_info=True)
        try:
            from backend.services.close_guard_calibrator import run_exit_audit
            report = run_exit_audit(lookback_days=30)
            logger.info("[Reconcile] exit_audit 刷新: channels=%d", len(report or {}))
        except Exception as _ea_e:
            logger.warning("[Reconcile] exit_audit 刷新失败: %s", _ea_e)

    task_scheduler.add_cron_task(
        task_func=_daily_reconcile_tick,
        task_id="daily_pnl_reconcile",
        hour=1, minute=5, second=0,
    )
    logger.info("[Startup] 每日对账任务已注册 (00:05 UTC / 08:05 北京)")
except Exception as _re_reg_err:
    logger.warning("[Startup] 每日对账任务注册失败: %s", _re_reg_err)
