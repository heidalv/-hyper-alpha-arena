"""
全自动交易服务 — 策略生命周期管理器

用户只需：选交易对 → 点开启
AI 自主完成：市场分析 → 策略生成 → 交易执行 → 绩效监控 → 策略轮换 → 自学习

核心循环（每5分钟执行一次健康检查）：
1. 市场扫描 — 分析每个交易对的多周期环境
2. 策略评估 — 检查现有策略表现，淘汰差的
3. 策略补充 — 为空缺的交易对自动创建新策略
4. 风控巡检 — 检查全局回撤/日亏限制
5. 事件记录 — 所有决策写入日志
"""
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import func as sa_func
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class FullAutoTradingService:
    """全自动交易服务（单例）"""

    _instance = None
    _lock = threading.Lock()
    _current_trace_id: str = ""  # 类级变量，贯穿单次健康检查全链路

    # ── 共享状态线程安全锁 ──
    _state_lock = threading.Lock()  # 保护 _position_last_decision_ts / _symbol_daily_pnl / _symbol_frozen_set

    # ── 整改项6: 仓位最小决策间隔 ────────────────────
    _POSITION_MIN_DECISION_INTERVAL = {
        "short": 300,    # 5分钟
        "mid": 600,      # 10分钟
        "long": 1800,    # 30分钟
    }
    _position_last_decision_ts: Dict[int, float] = {}  # key: position_id -> last_decision_timestamp

    # ── per-symbol 日亏损追踪 ──
    _symbol_daily_pnl: Dict[str, Dict[str, float]] = {}  # {session_id: {symbol: realized_pnl_today}}
    _symbol_frozen_set: Dict[str, set] = {}              # {session_id: set(frozen_symbols)}
    # P0-E: tier 归因日盈亏 + 各 symbol 被冻结的周期（tier 隔离，绝不跨周期连坐）
    _symbol_tier_daily_pnl: Dict[str, Dict[str, float]] = {}   # {session_id: {"SYM|tier": pnl}}
    _symbol_frozen_tiers: Dict[str, Dict[str, set]] = {}       # {session_id: {symbol: set(tier)}}

    # ── per-symbol 风控参数 ──
    _SYMBOL_DAILY_LOSS_PCT = 0.03          # 单 symbol 日亏损 3% 触发冻结
    _GLOBAL_EXTREME_DAILY_LOSS_PCT = 0.08 # 全局极端安全网 8%（原15%过高）
    _GLOBAL_EXTREME_DRAWDOWN = 0.25       # 全局极端安全网 25%（原80%远超行业标准15-30%）
    _SYMBOL_FREEZE_COOLDOWN_MINUTES = 60  # 冻结冷却 60 分钟

    @staticmethod
    def _utc_iso(dt) -> Optional[str]:
        from backend.utils.db_datetime import db_naive_to_utc_iso
        return db_naive_to_utc_iso(dt)

    @staticmethod
    def _compute_initial_tp_sl_prices(
        tier: str,
        action: str,
        ref_price: float,
        atr_pct: float = 0.0,
        sym: str = "",
        atr_1d_pct: float = 0.0,
        dec: Optional[dict] = None,
    ) -> tuple[float, float, str]:
        from backend.services.full_auto.tp_sl_prices import compute_initial_tp_sl_prices
        return compute_initial_tp_sl_prices(
            tier, action, ref_price, atr_pct=atr_pct, sym=sym,
            atr_1d_pct=atr_1d_pct, dec=dec,
        )

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    @classmethod
    def get_instance(cls):
        return cls()

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._running_sessions: Dict[str, dict] = {}
        self._creation_lock = threading.Lock()
        self._unified_tick_count: Dict[str, int] = {}
        self._scalp_tick_count: Dict[str, int] = {}
        # 短线因子计算缓存（2026-07-08 提速）：{sym: {"ts": float, "fv": dict}}
        self._scalp_factor_cache: Dict[str, Dict[str, Any]] = {}
        # 因子引擎冷启动预热标记（2026-07-09 提速）：首次 compute_all_factors 会触发
        # 一大批 numba JIT 编译 / lazy import，冷启动实测首个币耗时可达 ~96s。调度注册
        # 时后台线程先跑一遍，把这笔进程级一次性开销挪到后台消化，避免卡住第一次实盘扫描。
        self._scalp_factor_warmup_started: bool = False
        self._tick_symbol_subset: Dict[str, Set[str]] = {}
        self._market_scan_cache: Dict[str, Any] = {}
        self._market_scan_cache_ts: float = 0
        self._last_orch_bias_by_symbol: Dict[str, str] = {}
        self._training_allowed_symbols: Set[str] = set()
        self._MARKET_SCAN_CACHE_TTL = 300
        self._bg_scan_running: bool = False
        # 编排器独立后台线程：不依赖 tick，周期性评估所有币种写入 _market_scan_cache
        self._orch_bg_thread: Optional[threading.Thread] = None
        self._orch_bg_running: bool = False
        self._orch_bg_symbols: List[str] = []  # 当前 session 的 symbols
        self._orch_bg_session_id: Optional[str] = None
        self._last_reduce_time = {}
        # 累计部分平仓追踪：防止"千刀万剐"式重复部分平仓
        # key = "session_id:symbol:strategy_id", value = {"total_pct": float, "count": int, "reset_at": float}
        self._partial_close_tracker: Dict[str, Dict] = {}
        # 中线/长线 Agent 同向信号需连续 N tick 才允许开仓（MIDLONG_PERSISTENCE_TICKS）
        self._midlong_persistence_state: Dict[str, Dict] = {}
        self._midlong_symbol_cursor: Dict[str, int] = {}
        # 平仓冷却期：统一使用 reentry_cooldown.py (F1-5)
        # 记录每个 symbol 最后一次平仓时间，防止平仓后立即重开
        self._last_close_time: Dict[str, float] = {}
        # ScalpRouter 开仓冷却（2026-06-22 修复短线频繁开单）：
        # 平仓冷却 reentry_cooldown 只挡"刚平就重开"，挡不住"刚开又开"。
        # 这里补"开仓后冷却"：记录每个 symbol 最近开仓时间戳。
        # key = "symbol"，value = ts（同向不区分，通用冷却）
        self._scalp_open_ts: Dict[str, float] = {}
        # key = "symbol:side"，value = ts（同向更严冷却）
        self._scalp_open_ts_side: Dict[str, float] = {}
        # 策略创建冷却：防止同一 symbol:tier 短时间内被反复创建
        self._strategy_creation_ts: Dict[str, float] = {}  # key="symbol:tier", value=timestamp
        self._STRATEGY_CREATION_COOLDOWN = 600  # 同一 symbol:tier 至少 10 分钟间隔
        # F1-5: 冷却时间统一由 reentry_cooldown.py 管理
        # 系统健康状态追踪
        self._health_status = {
            "data_flow_ok": True,
            "ai_connection_ok": True,
            "last_ai_success": None,
            "consecutive_ai_failures": 0,
            "data_issues": [],        # 最近的数据问题
            "ai_issues": [],          # 最近的 AI 问题
            "rejected_decisions": [],  # 被审核拒绝的决策
        }
        # D7修复: 死锁熔断计数器 — 同一品种3次死锁恢复后直接熔断
        self._deadlock_rescue_count: Dict[str, int] = {}  # symbol → count
        self._DEADLOCK_RESCUE_MAX = 3  # 最大死锁恢复次数
        # 策略暂停元数据（避免主循环与快评互相打架导致「冻结/恢复」刷屏）
        self._strat_pause_meta: Dict[int, Dict[str, Any]] = {}  # strategy_id → {reason, since, by}
        self._RANGING_MIN_PAUSE_SEC = 900   # 震荡市暂停至少 15 分钟才允许快评恢复
        self._PAUSE_EVENT_COOLDOWN_SEC = 600  # 同类暂停/恢复事件 10 分钟内不重复写日志
        self._pause_event_last_ts: Dict[str, float] = {}  # event_key → ts
        self._last_cache_purge: float = 0
        self._active_positions_cache: list = []
        # V3 因子结果缓存（避免健康检查内重复算因子 + 减少 SQLite 写锁）
        self._v3_factor_cache: Dict[str, dict] = {}
        self._V3_FACTOR_CACHE_TTL = 90
        self._last_unified_snapshot = None
        self._qaa_ctx_lock = threading.Lock()
        # 防守模式冷却恢复：记录进入防守模式的时间
        self._defensive_entered_at: Dict[str, float] = {}  # session_id → timestamp
        # 恢复模式：退出防守后的过渡期（仓位缩减）
        self._recovery_until: Dict[str, float] = {}  # session_id → timestamp
        self._PEAK_DECAY_GRACE_HOURS = 2    # 进入防守2小时后开始衰减峰值（从4h加快到2h）
        self._PEAK_DECAY_RATE_PER_HOUR = 0.10  # 每小时衰减10%的峰值-权益差距（大幅加快）
        self._PEAK_DECAY_ACCEL_HOURS = 6    # 防守超过6小时后进入加速衰减（×3）
        self._RECOVERY_DURATION_HOURS = 2   # 退出防守后2小时恢复期
        self._RECOVERY_POSITION_SCALE = 0.5  # 恢复期仓位缩减到50%
        # 整改项2: 模式切换缓冲机制
        self._mode_switched_at: Dict[str, float] = {}   # session_id → 上次模式切换时间戳
        self._MODE_MIN_HOLD_SEC = 300      # 模式最小保持时间5分钟
        self._MODE_RETURN_DELAY_SEC = 180  # defensive返回running额外延迟3分钟
        # 子仓位管理器 (audit_only=False: 真实拦截，冷却/利润限制全部生效)
        try:
            from backend.services.sub_position_manager import SubPositionManager
            self._sub_mgr = SubPositionManager(audit_only=False)
        except Exception as _sub_err:
            logger.warning(f"[FullAuto] SubPositionManager 初始化失败: {_sub_err}")
            self._sub_mgr = None
        # 冷却期延迟排队：被 reopen_blocked 拦截的信号暂存于此，冷却期满后自动重注入
        # key: "{account_id}:{SYMBOL}:{action}:{tier}"
        self._deferred_signals: Dict[str, dict] = {}
        self._DEFERRED_MAX_RETRIES = 5
        # P2 D14: long tier 分批战略 TP 状态（内存版，重启丢失可接受：
        #   long 仓有 SL 兜底，最坏情况就是某个 stage 重复触发一次，reduce 比例 30% 不会爆）
        # key: f"pos_{position_id}", value: StagedTpState
        try:
            from backend.services.long_tier_staged_tp import StagedTpState as _StagedTpState  # noqa: F401
            self._long_tier_staged_tp_state: Dict[str, Any] = {}
        except Exception:
            self._long_tier_staged_tp_state = {}
        # Phase 0: DB session 泄漏追踪 — 在 join 超时时关闭泄漏的 session
        self._active_db_sessions: Dict[str, Any] = {}  # key="session_id:purpose", value=db_session
        logger.info("[FullAuto] 全自动交易服务初始化完成")
        # 2026-07-06：灰度开关生效值一次性打印，便于运维确认"到底开没开"（历史痛点：改 .env 未重启 → 开关从未生效）
        try:
            _flag = lambda k, d="false": os.getenv(k, d).strip().lower() in ("1", "true", "yes", "on")
            logger.info(
                "[FullAuto][灰度开关] CONSUME_SNAPSHOT_KLINES=%s (max_age=%ss) | DECISION_PRICE_GATE=%s (live=%s paper=%s)",
                _flag("COORDINATOR_CONSUME_SNAPSHOT_KLINES"),
                os.getenv("COORDINATOR_SNAPSHOT_MAX_AGE_SEC", "180"),
                _flag("DECISION_PRICE_GATE_ENABLED"),
                os.getenv("DECISION_PRICE_MAX_DEVIATION_PCT_LIVE", "0.005"),
                os.getenv("DECISION_PRICE_MAX_DEVIATION_PCT_PAPER", "0.010"),
            )
        except Exception:
            pass

    def _get_today_realized_pnl(self, db, account_id: int) -> float:
        from backend.services.full_auto.paper_risk_helpers import get_today_realized_pnl
        return get_today_realized_pnl(db, account_id)

    def _get_account_risk_score(self, account_id: int) -> float:
        from backend.services.full_auto.paper_risk_helpers import get_account_risk_score
        return get_account_risk_score(account_id)

    def _tiny_close_allowed_by_hardfact(
        self, account_id: int, pos: dict, reasoning: str = ""
    ) -> tuple[bool, str]:
        from backend.services.full_auto.paper_risk_helpers import tiny_close_allowed_by_hardfact
        return tiny_close_allowed_by_hardfact(
            account_id, pos, reasoning, risk_score_fn=self._get_account_risk_score,
        )

    def _get_cooldown_status(self, session_id: str) -> dict:
        """获取冷却恢复机制的当前状态"""
        now = time.time()
        result = {"peak_decay_active": False, "recovery_mode": False}

        entered = self._defensive_entered_at.get(session_id)
        if entered:
            hours = (now - entered) / 3600
            result["defensive_hours"] = round(hours, 1)
            result["peak_decay_active"] = hours > self._PEAK_DECAY_GRACE_HOURS
            result["peak_decay_starts_in_hours"] = max(0, round(self._PEAK_DECAY_GRACE_HOURS - hours, 1))

        recovery_ts = self._recovery_until.get(session_id, 0)
        if recovery_ts > now:
            result["recovery_mode"] = True
            result["recovery_remaining_min"] = round((recovery_ts - now) / 60, 0)
            result["recovery_position_scale"] = self._RECOVERY_POSITION_SCALE
        return result

    def _get_lock_profile(self, session):
        from backend.services.lock_strength_service import get_lock_strength_service
        mode = (getattr(session, "trading_mode", "") or "paper").strip().lower()
        return get_lock_strength_service().get_profile(mode)

    def _paper_loss_locks_disabled(self, session) -> bool:
        """模拟盘/实盘：是否关闭因亏损触发的锁仓（由锁仓强度配置决定）。"""
        return bool(self._get_lock_profile(session).disable_loss_locks)

    def _paper_auto_unlock_session(self, db: Session, session) -> bool:
        from backend.services.full_auto.paper_session_helpers import (
            build_paper_session_host,
            paper_auto_unlock_session,
        )
        host = build_paper_session_host(self)
        changed = paper_auto_unlock_session(db, session, host)
        self._defensive_entered_at = host.defensive_entered_at
        self._recovery_until = host.recovery_until
        self._symbol_frozen_set = host.symbol_frozen_set
        self._symbol_frozen_tiers = host.symbol_frozen_tiers
        self._strat_pause_meta = host.strat_pause_meta
        return changed

    def _cap_paper_active_strategies(
        self, db: Session, session, active_ids: list, *, max_per_symbol: Optional[int] = None
    ) -> bool:
        from backend.services.full_auto.paper_session_helpers import (
            build_paper_session_host,
            cap_paper_active_strategies,
        )
        return cap_paper_active_strategies(
            db, session, active_ids, build_paper_session_host(self),
            max_per_symbol=max_per_symbol,
        )

    def _get_trade_history(self, db, session) -> list:
        from backend.services.full_auto.paper_session_helpers import (
            build_paper_session_host,
            get_trade_history,
        )
        return get_trade_history(db, session, build_paper_session_host(self))

    def _should_switch_mode(self, session_id: str, current_mode: str, target_mode: str) -> bool:
        """检查是否允许模式切换（防止频繁切换导致操作混乱）

        Returns:
            True = 允许切换, False = 应延迟切换
        """
        import time as _t
        now = _t.time()
        last_switch = self._mode_switched_at.get(session_id, 0)
        elapsed = now - last_switch

        # 最小保持时间：任何方向的切换都要满足
        if elapsed < self._MODE_MIN_HOLD_SEC:
            logger.info(
                f"[FullAuto] 模式切换缓冲 {session_id}: "
                f"{current_mode}→{target_mode} 仅过{elapsed:.0f}s"
                f"(<{self._MODE_MIN_HOLD_SEC}s)，延迟切换")
            return False

        # defensive → running 额外延迟
        if current_mode == "defensive" and target_mode == "running":
            required = self._MODE_MIN_HOLD_SEC + self._MODE_RETURN_DELAY_SEC
            if elapsed < required:
                logger.info(
                    f"[FullAuto] defensive→running缓冲 {session_id}: "
                    f"仅过{elapsed:.0f}s(<{required}s)，延迟切换")
                return False

        # 允许切换，记录时间戳
        self._mode_switched_at[session_id] = now
        return True

    @staticmethod
    def _safe_commit(db, label: str = "", session=None, retries: int = 4):
        from backend.services.full_auto.db_session_helpers import safe_commit
        return safe_commit(db, label=label, session=session, retries=retries)

    # 事件日志展示：明确的周期标识（短/中/长），替代模糊的「敏捷/波段/趋势」
    _NATURE_LOG_CN = {
        "scalp": "短线",
        "intraday": "短线",
        "swing": "中线",
        "position": "长线",
        "trend_follow": "长线",
    }
    _TIER_LOG_CN = {
        "short": "短线", "mid": "中线", "long": "长线", "active": "中线",
    }
    # trade_nature → 引擎/仓位 timeframe_tier（统一引用 sub_position_manager.NATURE_TO_TIER）
    from backend.services.sub_position_manager import NATURE_TO_TIER as _NATURE_TO_TIER_MAP  # noqa: E402

    @classmethod
    def _event_scope_label(cls, trade_nature: Optional[str], tier_compat: str) -> str:
        n = (trade_nature or "").strip().lower()
        if n in cls._NATURE_LOG_CN:
            return cls._NATURE_LOG_CN[n]
        t = (tier_compat or "mid").strip().lower()
        return cls._TIER_LOG_CN.get(t, "波段")

    def _clear_master_strat_cache(self) -> None:
        """清除策略 ORM 缓存。rollback / db.close 后缓存的 AIStrategy 会 detach。"""
        if hasattr(self, "_master_strat_cache"):
            self._master_strat_cache.clear()

    def _load_strategy_by_id(
        self,
        db: Session,
        strategy_id: str,
        *,
        active_ids: Optional[list] = None,
        symbol: Optional[str] = None,
        status: Optional[tuple] = None,
    ):
        from backend.services.full_auto.strategy_binding import load_strategy_by_id
        return load_strategy_by_id(
            db, strategy_id, active_ids=active_ids, symbol=symbol, status=status,
        )

    def _ensure_bound_strategy(
        self,
        db: Session,
        strat,
        *,
        active_ids: Optional[list] = None,
        symbol: Optional[str] = None,
        status: Optional[tuple] = None,
    ):
        from backend.services.full_auto.strategy_binding import ensure_bound_strategy
        return ensure_bound_strategy(
            db, strat, active_ids=active_ids, symbol=symbol, status=status,
        )

    @staticmethod
    def _active_exchange() -> str:
        from backend.services.full_auto.strategy_binding import active_exchange
        return active_exchange()

    def bootstrap_qaa_v3_context(self, blocking: bool = False, timeout: float = 120.0) -> bool:
        """初始化 QAAContext + TradingPlugin（幂等；默认非阻塞后台启动）。"""
        if getattr(self, "_qaa_ctx", None) is not None:
            self._wire_qaa_module_singletons(self._qaa_ctx)
            return True

        def _do_bootstrap():
            try:
                from backend.config.settings import QAA_MODE, QAA_V3_ENABLED
                if QAA_MODE != "qaa" or not QAA_V3_ENABLED:
                    return
                import asyncio
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    asyncio.set_event_loop(asyncio.new_event_loop())
                from qaa.core.context import QAAContext
                from qaa.domains.trading.plugin import TradingPlugin
                _qaa_ctx = QAAContext.default()
                trading_plugin = TradingPlugin(service_provider=lambda: self)
                plugins = [trading_plugin]

                # Phase 4: 套利域 read_only bootstrap（扫描/监控可用，执行走 ExecutionAuthority）
                try:
                    from qaa.domains.arbitrage.plugin import ArbitragePlugin
                    from qaa.domains.rebate_arb.plugin import RebateArbPlugin
                    plugins.extend([ArbitragePlugin(), RebateArbPlugin()])
                    from backend.services.arbitrage.execution_authority import (
                        ExecutionAuthority,
                    )
                    ExecutionAuthority.mark_qaa_plugins_bootstrapped(True)
                except Exception as _arb_plug_err:
                    logger.debug(f"[FullAuto] QAA arb plugins skip: {_arb_plug_err}")

                _qaa_ctx.bootstrap(plugins=plugins)
                self._qaa_ctx = _qaa_ctx
                self._wire_qaa_module_singletons(_qaa_ctx)
                logger.info(
                    f"[FullAuto] QAA v3 bootstrap 完成: "
                    f"domains={_qaa_ctx.registry.get_domains()}, "
                    f"agents={_qaa_ctx.registry.stats.get('total_cards', 0)}"
                )
            except Exception as e:
                logger.warning(f"[FullAuto] QAA v3 bootstrap 失败: {e}")

        with self._qaa_ctx_lock:
            if getattr(self, "_qaa_ctx", None) is not None:
                return True
            th = getattr(self, "_qaa_bootstrap_thread", None)
            if th is None or not th.is_alive():
                self._qaa_bootstrap_started_at = time.time()
                self._qaa_bootstrap_thread = threading.Thread(
                    target=_do_bootstrap,
                    daemon=True,
                    name="qaa-bootstrap",
                )
                self._qaa_bootstrap_thread.start()
            th = self._qaa_bootstrap_thread

        if blocking and th.is_alive():
            th.join(timeout=timeout)
        if th.is_alive():
            started_at = float(getattr(self, "_qaa_bootstrap_started_at", 0) or 0)
            elapsed = time.time() - started_at if started_at else 0
            if elapsed >= timeout:
                logger.warning(
                    f"[FullAuto] QAA v3 bootstrap 仍未完成({elapsed:.0f}s)，"
                    "本轮将回退到标准 AI 交易循环"
                )
        return getattr(self, "_qaa_ctx", None) is not None

    @staticmethod
    def _wire_qaa_module_singletons(qaa_ctx) -> None:
        """让模块级 QAA 单例与 QAAContext 共用同一 workflow store，避免 run 跨实例丢失。"""
        try:
            store = qaa_ctx.workflow_store
            tenant = qaa_ctx.tenant
            from qaa.workflow.orchestrator import tick_orchestrator as mod_orch
            from qaa.workflow.outcome import outcome_tracker as mod_outcome

            mod_orch._store = store
            mod_orch._tenant = tenant
            if getattr(mod_orch, "_outcome_tracker", None) is not None:
                mod_orch._outcome_tracker._store = store
            mod_outcome._store = store
            if getattr(qaa_ctx, "_tick_orchestrator", None) is not None:
                qaa_ctx._tick_orchestrator._store = store
            if getattr(qaa_ctx, "_outcome_tracker", None) is not None:
                qaa_ctx._outcome_tracker._store = store
            logger.debug("[FullAuto] QAA workflow store 单例已对齐")
        except Exception as err:
            logger.debug("[FullAuto] QAA 单例对齐跳过: %s", err)

    def _get_or_capture_unified_snapshot(
        self,
        symbols: list,
        account_id=None,
        include_klines: bool = False,
        light_mode: bool = True,
        max_age: float = 90.0,
    ):
        """优先复用新鲜快照，避免 QAA tick 内重复全量 capture。"""
        from backend.services.unified_data_pool import unified_data_pool

        want = set(symbols or [])
        now = time.time()

        def _covers(snap) -> bool:
            if not snap or not want:
                return False
            markets = getattr(snap, "markets", None) or {}
            if not want.issubset(set(markets.keys())):
                return False
            return (now - float(getattr(snap, "timestamp", 0) or 0)) <= max_age

        cached = getattr(self, "_last_unified_snapshot", None)
        if _covers(cached):
            return cached

        pool_snap = unified_data_pool.get_snapshot(max_age=max_age)
        if _covers(pool_snap):
            self._last_unified_snapshot = pool_snap
            return pool_snap

        if os.getenv("QAA_CAPTURE_UNIFIED_SNAPSHOT", "false").lower() not in ("1", "true", "yes", "on"):
            logger.info("[FullAuto][QAA v3] 跳过同步统一快照捕获，使用缓存行情")
            return {}

        def _capture_snapshot():
            return unified_data_pool.capture_snapshot(
                symbols=list(symbols),
                account_id=account_id,
                environment=self._active_exchange(),
                include_klines=include_klines,
                include_strategy=False,
                light_mode=light_mode,
            )

        snap = self._run_with_timeout(
            _capture_snapshot,
            timeout_s=float(os.getenv("QAA_SNAPSHOT_TIMEOUT_S", "8")),
            fallback={},
            label="qaa_unified_snapshot",
        ) or {}
        self._last_unified_snapshot = snap
        return snap

    @staticmethod
    def _recover_db_session(db, label: str = "") -> None:
        from backend.services.full_auto.db_session_helpers import recover_db_session
        recover_db_session(db, label=label)

    @staticmethod
    def _deferred_signal_key(account_id: int, sym: str, action: str, tier: str = "mid") -> str:
        from backend.services.full_auto.db_session_helpers import deferred_signal_key
        return deferred_signal_key(account_id, sym, action, tier=tier)

    def _clear_deferred_signal(self, account_id: int, sym: str, action: str, tier: str = "mid") -> None:
        self._deferred_signals.pop(
            self._deferred_signal_key(account_id, sym, action, tier), None)
        # 兼容旧版无 tier 的 key
        self._deferred_signals.pop(f"{account_id}:{sym}:{action}", None)

    def _orchestrator_blocks_open(
        self,
        sym: str,
        action: str,
        market_summary: dict,
        tier: str = "",
        confidence: float = 0,
        trading_mode: str = "paper",
    ) -> tuple:
        from backend.services.full_auto.execution_gates import orchestrator_blocks_open
        return orchestrator_blocks_open(
            sym, action, market_summary, tier=tier, confidence=confidence, trading_mode=trading_mode,
        )

    # ══════════════════════════════════════════════════
    #  执行层辅助：持仓刷新与敞口重算
    # ══════════════════════════════════════════════════

    def _refresh_positions_local(self, db, account_id: int,
                                  positions_list: list,
                                  position_map: dict,
                                  symbol_positions: dict,
                                  affected_symbol: str = None):
        from backend.services.full_auto.refresh_positions import (
            build_refresh_positions_host,
            refresh_positions_local,
        )
        return refresh_positions_local(
            db, account_id, positions_list, position_map, symbol_positions,
            build_refresh_positions_host(self),
            affected_symbol=affected_symbol,
        )

    def start_session(self, db: Session, account_id: int, symbols: List[str],
                      risk_level: str = "moderate", trading_mode: str = "paper",
                      risk_mode: str = "ai_dynamic", paper_account_id: int = None,
                      auto_coin_enabled: bool = False, arb_enabled: bool = False,
                      arbitrage_profile_id: Optional[int] = None,
                      paper_account_mode: str = "legacy_ai_paper",
                      arbitrage_paper_account_id: Optional[int] = None,
                      profile_override: Optional[Dict[str, Any]] = None,
                      active_exchange: Optional[str] = None) -> dict:
        """启动全自动交易会话

        active_exchange: 会话级交易所覆盖。None 时回退到 account.selected_exchange → DEFAULT_EXCHANGE。
        启用后，该会话的下单与市场数据读取都绑定到这个交易所（支持同账户不同交易所并行）。
        """
        from backend.database.models import Account, ArbitrageProfileDB, FullAutoSession

        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            return {"success": False, "error": "账户不存在"}

        profile_snapshot: Dict[str, Any] = {}
        if arbitrage_profile_id or profile_override:
            profile = None
            if arbitrage_profile_id:
                profile = db.query(ArbitrageProfileDB).filter(
                    ArbitrageProfileDB.id == arbitrage_profile_id,
                    ArbitrageProfileDB.account_id == account_id,
                ).first()
                if not profile:
                    return {"success": False, "error": f"套利档案 #{arbitrage_profile_id} 不存在或不属于该交易员"}
                try:
                    enabled_strategies = json.loads(profile.enabled_strategies_json or "[]")
                except Exception:
                    enabled_strategies = []
                try:
                    strategy_overrides = json.loads(profile.strategy_overrides_json or "{}")
                except Exception:
                    strategy_overrides = {}
                profile_snapshot = {
                    "profile_id": profile.id,
                    "enabled": bool(profile.enabled),
                    "mode": profile.mode or "paper",
                    "paper_account_id": profile.paper_account_id,
                    "paper_account_mode": getattr(profile, "paper_account_mode", None) or "legacy_ai_paper",
                    "arbitrage_paper_account_id": getattr(profile, "arbitrage_paper_account_id", None),
                    "enabled_strategies": enabled_strategies,
                    "strategy_overrides": strategy_overrides,
                    "wash_trade_profile": profile.wash_trade_profile,
                    "linked_llm_config_id": profile.linked_llm_config_id,
                    "ai_config_source": profile.ai_config_source,
                }
                if profile.enabled:
                    arb_enabled = True
                    paper_account_mode = profile_snapshot.get("paper_account_mode") or paper_account_mode
                    if paper_account_mode == "dedicated_arbitrage_paper" and profile_snapshot.get("arbitrage_paper_account_id"):
                        trading_mode = "paper"
                        arbitrage_paper_account_id = int(profile_snapshot["arbitrage_paper_account_id"])
                    elif (profile.mode or "paper") == "paper" and profile.paper_account_id:
                        trading_mode = "paper"
                        paper_account_id = profile.paper_account_id

            if profile_override:
                profile_snapshot.update(profile_override)
                arb_enabled = bool(profile_snapshot.get("enabled", arb_enabled))
                paper_account_mode = profile_snapshot.get("paper_account_mode", paper_account_mode)
                if paper_account_mode == "dedicated_arbitrage_paper" and profile_snapshot.get("arbitrage_paper_account_id"):
                    trading_mode = "paper"
                    arbitrage_paper_account_id = int(profile_snapshot["arbitrage_paper_account_id"])
                elif profile_snapshot.get("paper_account_id") and (profile_snapshot.get("mode") or trading_mode) == "paper":
                    trading_mode = "paper"
                    paper_account_id = int(profile_snapshot["paper_account_id"])

        _paper_name = None
        # P5-fix: paper 模式必须显式选择模拟账户，禁止静默改绑（否则前端下拉框等于摆设）
        uses_dedicated_arb_paper = (
            bool(arb_enabled)
            and paper_account_mode == "dedicated_arbitrage_paper"
            and bool(arbitrage_paper_account_id)
        )
        if (trading_mode or "paper").lower() == "paper" and not uses_dedicated_arb_paper:
            if not paper_account_id:
                return {
                    "success": False,
                    "error": "模拟盘模式下必须在「模拟账户(资金池)」中明确选择一个账户，不能留空。",
                }
            _paper = db.query(Account).filter(Account.id == paper_account_id).first()
            if not _paper:
                return {"success": False, "error": f"模拟账户 id={paper_account_id} 不存在"}
            if (_paper.account_type or "").upper() != "PAPER":
                return {
                    "success": False,
                    "error": f"账户 id={paper_account_id} ({_paper.name}) 不是 PAPER 类型，无法用作模拟账户",
                }
            if (_paper.is_active or "").lower() not in ("true", "1", "yes"):
                return {
                    "success": False,
                    "error": f"模拟账户「{_paper.name}」(#{_paper.id}) 已停用，请换一个启用中的模拟账户。",
                }
            if _paper.user_id != account.user_id:
                return {"success": False, "error": "模拟账户与主账户不属于同一用户"}
            _paper_name = _paper.name

        existing = db.query(FullAutoSession).filter(
            FullAutoSession.account_id == account_id,
            FullAutoSession.status == "running",
        ).first()
        if existing:
            _bound_paper = getattr(existing, "paper_account_id", None)
            if (trading_mode or "paper").lower() == "paper" and paper_account_id:
                if _bound_paper and int(_bound_paper) != int(paper_account_id):
                    _bound = db.query(Account).filter(Account.id == _bound_paper).first()
                    _bound_label = f"{_bound.name}(#{_bound_paper})" if _bound else f"#{_bound_paper}"
                    _want_label = f"{_paper_name}(#{paper_account_id})" if _paper_name else f"#{paper_account_id}"
                    return {
                        "success": False,
                        "error": (
                            f"该交易员已有运行中的会话，且已锁定模拟资金池 {_bound_label}。"
                            f" 你本次选择的是 {_want_label}，请先停止旧会话再新建。"
                        ),
                        "session_id": existing.session_id,
                        "paper_account_id": _bound_paper,
                    }
            return {
                "success": False,
                "error": "该账户已有运行中的全自动会话",
                "session_id": existing.session_id,
                "paper_account_id": _bound_paper,
            }

        # 规范化会话级交易所标识（aster → asterdex），留空表示跟随账户配置
        _active_exchange_norm = (active_exchange or "").strip().lower()
        if _active_exchange_norm == "aster":
            _active_exchange_norm = "asterdex"
        if not _active_exchange_norm:
            _active_exchange_norm = None

        session_id = f"fa_{uuid.uuid4().hex[:10]}"
        session = FullAutoSession(
            session_id=session_id,
            account_id=account_id,
            paper_account_id=paper_account_id if trading_mode == "paper" else None,
            paper_account_mode=paper_account_mode if trading_mode == "paper" else None,
            arbitrage_paper_account_id=arbitrage_paper_account_id if uses_dedicated_arb_paper else None,
            symbols=symbols,
            # 分周期固定币不得启动时焊死成同一份。
            # 启动勾选的 symbols 只种子「长线」；短/中默认空，由定币面板各自勾选。
            fixed_symbols_by_tier={
                "short": [],
                "mid": [],
                "long": list(symbols or []),
            },
            risk_level=risk_level,
            risk_mode=risk_mode,
            trading_mode=trading_mode,
            auto_coin_enabled=auto_coin_enabled,
            arb_enabled=arb_enabled,
            auto_coin_max_slots=5,
            auto_coin_mid_enabled=False,
            auto_coin_mid_max_slots=3,
            active_exchange=_active_exchange_norm,
            status="running",
            event_log=[{
                "time": datetime.now(timezone.utc).isoformat(),
                "event": "session_started",
                "detail": (
                    f"全自动交易启动: {', '.join(symbols)}, 风险模式={risk_mode}, 模式={trading_mode}"
                    + (
                        f", 模拟资金池={_paper_name}(#{paper_account_id})"
                        if (trading_mode or "paper").lower() == "paper" and paper_account_id
                        else (
                            f", 套利专用Paper账户=#{arbitrage_paper_account_id}"
                            if uses_dedicated_arb_paper
                            else ""
                        )
                    )
                ),
            }, {
                "time": datetime.now(timezone.utc).isoformat(),
                "event": "arbitrage_profile_loaded",
                "detail": "专用套利档案已加载" if profile_snapshot else "未使用专用套利档案",
                "profile_snapshot": profile_snapshot,
            }],
        )

        # 自动激活策略配置
        from backend.database.models import AccountStrategyConfig
        _cfg = db.query(AccountStrategyConfig).filter(
            AccountStrategyConfig.account_id == account_id
        ).first()
        if _cfg and _cfg.enabled != "true":
            _cfg.enabled = "true"
            logger.info(f"[FullAuto] 自动激活策略配置 account={account_id}")

        db.add(session)
        self._safe_commit(db, "create_session")
        db.refresh(session)

        _trading_acct = paper_account_id if (trading_mode == "paper" and paper_account_id) else account_id

        # D7: 恢复已有活跃策略（必须用资金池账户，不能用 session.account_id 实盘ID）
        from backend.database.models import AIStrategy
        _existing = db.query(AIStrategy).filter(
            AIStrategy.account_id == _trading_acct,
            AIStrategy.status == "active",
        ).all()
        if _existing:
            session.active_strategy_ids = [s.strategy_id for s in _existing]
            self._safe_commit(db, "restore_strategies")
            logger.info(
                f"[FullAuto] 恢复{len(_existing)}个活跃策略到session {session_id} "
                f"(trading_account={_trading_acct})"
            )
        self._running_sessions[session_id] = {
            "account_id": account_id,
            "trading_account_id": _trading_acct,
            "symbols": symbols,
            "risk_level": risk_level,
            "risk_mode": risk_mode,
            "trading_mode": trading_mode,
            "auto_coin_enabled": auto_coin_enabled,
            "arb_enabled": arb_enabled,
            "arbitrage_profile": profile_snapshot,
            "paper_account_mode": paper_account_mode,
            "arbitrage_paper_account_id": arbitrage_paper_account_id if uses_dedicated_arb_paper else None,
            "active_exchange": _active_exchange_norm,
        }

        if arb_enabled and (trading_mode or "paper").lower() == "paper" and paper_account_id:
            try:
                from backend.services.rebate_arb.engine import rebate_arb_engine
                rebate_arb_engine.set_paper_account(paper_account_id)
            except Exception as e:
                logger.warning("[FullAuto] 同步 Rebate Paper 账户失败: %s", e)
        if uses_dedicated_arb_paper:
            try:
                from backend.services.rebate_arb.capital_coordinator import capital_coordinator
                capital_coordinator.set_arbitrage_paper_account(arbitrage_paper_account_id)
            except Exception as e:
                logger.warning("[FullAuto] 同步套利专用 Paper 账户失败: %s", e)

        if auto_coin_enabled:
            from backend.services.auto_coin_selector import auto_coin_scheduler
            auto_coin_scheduler.register_session(session_id, account_id)
            logger.info(f"[FullAuto] 自动选币已启用 session={session_id} account={account_id}")

        self._apply_paper_fast_trial_pace(trading_mode)
        self._register_health_check(
            session_id,
            self._resolve_unified_tick_interval(session.health_check_interval or 300),
        )
        # [2026-07-10 修复] 显式注册中长线独立循环（同恢复路径，确保 midlong 一定被调度）
        try:
            self._register_midlong_agent_loop(session_id)
        except Exception as _ml_err:
            logger.warning(f"[FullAuto] 启动时 midlong 注册失败 {session_id}: {_ml_err}")

        logger.info(f"[FullAuto] 会话 {session_id} 已启动: symbols={symbols}")

        # Step 2: 根据账户 selected_exchange 确保市场流订阅
        try:
            from config import settings as _settings
            from services.market_flow import market_flow_registry

            # 确定交易所：会话级 active_exchange(已规范化) > account.selected_exchange > DEFAULT_EXCHANGE
            exchange_id = (
                _active_exchange_norm
                or getattr(account, "selected_exchange", None)
                or getattr(_settings, "DEFAULT_EXCHANGE", "asterdex")
            )
            exchange_id = exchange_id.lower()
            if exchange_id == "aster":
                exchange_id = "asterdex"

            # 调用 registry 的 ensure_subscribed（带引用计数）
            success = market_flow_registry.ensure_subscribed(exchange_id, symbols)
            logger.info(
                f"[Session] 账户 {account_id} 交易所 {exchange_id} 市场流订阅: "
                f"{'成功' if success else '失败'}, symbols={symbols}"
            )
        except Exception as e:
            logger.error(f"[Session] 市场流订阅失败: {e}", exc_info=True)

        # 立即执行第一次完整健康检查（异步，不阻塞）
        threading.Thread(
            target=self._run_health_check_safe, args=(session_id,), daemon=True
        ).start()

        # [2026-08-17 删除] drift_monitor：审计实锤 start() 已接线但 24h 零巡检日志
        #（进程重启后线程失效），且其概念漂移检测已有替代路径。

        # ── ScalpExecutionLane Phase 0: 启动编排器后台参谋线程 ──
        try:
            _trade_syms = self._resolve_session_trade_symbols(session, db)
            self._ensure_orchestrator_bg_running(session_id, _trade_syms)
            logger.info(
                f"[FullAuto] OrchBG 已接线 session={session_id} "
                f"symbols={len(_trade_syms)} {_trade_syms}"
            )
        except Exception as _obg_err:
            logger.warning(f"[FullAuto] OrchBG 启动失败(非致命): {_obg_err}")

        return {
            "success": True,
            "session_id": session_id,
            "symbols": symbols,
            "risk_level": risk_level,
            "risk_mode": risk_mode,
            "trading_mode": trading_mode,
            "auto_coin_enabled": auto_coin_enabled,
            "arb_enabled": arb_enabled,
            "arbitrage_profile": profile_snapshot,
            "paper_account_mode": paper_account_mode,
            "arbitrage_paper_account_id": arbitrage_paper_account_id if uses_dedicated_arb_paper else None,
            "paper_account_id": paper_account_id if (trading_mode or "paper").lower() == "paper" else None,
            "paper_account_name": _paper_name,
            "trading_account_id": _trading_acct,
        }

    def stop_session(self, db: Session, session_id: str) -> dict:
        """停止全自动交易会话，并清理关联策略"""
        from backend.database.models import AIStrategy, FullAutoSession

        session = db.query(FullAutoSession).filter(
            FullAutoSession.session_id == session_id
        ).first()
        if not session:
            return {"success": False, "error": "会话不存在"}

        # 先暂停所有活跃策略的自主循环
        paused_count = self._pause_all_strategies(db, session)

        # 清理关联策略：删除本会话创建的所有策略
        all_strategy_ids = list(set(
            (session.active_strategy_ids or []) +
            (session.terminated_strategy_ids or [])
        ))
        deleted_count = 0
        for sid in all_strategy_ids:
            strat = db.query(AIStrategy).filter(AIStrategy.strategy_id == sid).first()
            if strat:
                db.delete(strat)
                deleted_count += 1
                logger.info(f"[FullAuto] 清理策略 {sid} ({strat.name})")

        session.active_strategy_ids = []
        session.terminated_strategy_ids = []
        session.status = "stopped"
        session.stopped_at = datetime.now(timezone.utc)
        self._append_event(session, "session_stopped",
                           f"全自动交易停止，清理了 {deleted_count} 个策略")
        if not self._safe_commit(db, "stop_session"):
            try:
                db.rollback()
                session.status = "stopped"
                session.active_strategy_ids = []
                session.terminated_strategy_ids = []
                self._safe_commit(db, "stop_session_fallback")
            except Exception as _stop_fb_err:
                # 2026-06-17: 停止会话的二次回退也失败必须留痕，否则会话状态可能
                # 卡在非 stopped，导致后续 restore 错误恢复已停会话。
                logger.warning("[stop_session] fallback commit 失败: %s", _stop_fb_err)
                db.rollback()

        self._invalidate_session_status_cache(session_id)
        self._unregister_health_check(session_id)
        self._orch_bg_running = False
        self._running_sessions.pop(session_id, None)

        logger.info(f"[FullAuto] 会话 {session_id} 已停止，清理 {deleted_count} 个策略")
        return {"success": True, "paused_strategies": paused_count, "deleted_strategies": deleted_count}

    def delete_session(self, db: Session, session_id: str) -> dict:
        """删除全自动交易会话（先停止再删除记录）"""
        from backend.database.models import AutoCoinSelection, FullAutoSession

        session = db.query(FullAutoSession).filter(
            FullAutoSession.session_id == session_id
        ).first()
        if not session:
            return {"success": False, "error": "会话不存在"}

        # Stop first if running
        if session.status in ("running", "defensive", "paused"):
            self.stop_session(db, session_id)
            # Refresh after stop
            session = db.query(FullAutoSession).filter(
                FullAutoSession.session_id == session_id
            ).first()
            if not session:
                return {"success": True, "deleted": True}

        # Remove from memory
        self._running_sessions.pop(session_id, None)
        self._unregister_health_check(session_id)

        # Delete FK-linked child rows first (auto_coin_selections → full_auto_sessions)
        db.query(AutoCoinSelection).filter(
            AutoCoinSelection.session_id == session_id
        ).delete(synchronize_session="fetch")

        # Delete the record
        db.delete(session)
        commit_ok = self._safe_commit(db, "delete_session")
        if not commit_ok:
            logger.error(f"[FullAuto] delete_session commit 失败 {session_id}")
            return {"success": False, "error": "删除失败（数据库写入错误）"}

        logger.info(f"[FullAuto] 会话 {session_id} 已删除")
        return {"success": True, "deleted": True}

    def pause_session(self, db: Session, session_id: str) -> dict:
        """暂停会话（策略继续监控但不开新单）"""
        from backend.database.models import FullAutoSession

        session = db.query(FullAutoSession).filter(
            FullAutoSession.session_id == session_id
        ).first()
        if not session:
            return {"success": False, "error": "会话不存在"}

        session.status = "paused"
        session.pause_reason = "manual"
        self._append_event(session, "session_paused", "全自动交易暂停（手动）")
        self._safe_commit(db, "pause_session")

        self._invalidate_session_status_cache(session_id)
        self._unregister_health_check(session_id)

        logger.info(f"[FullAuto] 会话 {session_id} 已暂停（手动）")
        return {"success": True}

    def resume_session(self, db: Session, session_id: str) -> dict:
        """恢复暂停/防守的会话"""
        from backend.database.models import FullAutoSession

        session = db.query(FullAutoSession).filter(
            FullAutoSession.session_id == session_id
        ).first()
        if not session or session.status not in ("paused", "defensive"):
            return {"success": False, "error": "会话不存在或未处于暂停/防守状态"}

        prev_status = session.status
        session.status = "running"
        session.pause_reason = None
        self._append_event(session, "session_resumed",
            f"全自动交易恢复（从 {prev_status} 状态）")
        self._safe_commit(db, "resume_session")

        self._invalidate_session_status_cache(session_id)
        self._register_health_check(
            session_id,
            self._resolve_unified_tick_interval(session.health_check_interval or 300),
        )
        return {"success": True}

    def add_symbols(self, db: Session, session_id: str, symbols: List[str],
                    *, is_auto_coin: bool = False, tier: str | None = None) -> dict:
        """向运行中的会话添加交易对。

        两张独立表（互不占槽）：
        - session.symbols / fixed_symbols_by_tier = 手动固定交易对
        - session.auto_coin_symbols = AI 选币（短线），仅此表受 auto_coin_max_slots 限制

        is_auto_coin: True → 只写 auto_coin_symbols；False → 写固定币（可指定 tier）。
        tier: short|mid|long；None 时写入全部周期并集兼容旧行为（写 long + 同步并集）。
        """
        from backend.database.models import FullAutoSession
        from backend.services.auto_coin_selector import (
            _parse_by_tier_map,
            _parse_symbol_list,
            _union_preserve,
            validate_symbols_in_backup_pool,
        )

        session = db.query(FullAutoSession).filter(
            FullAutoSession.session_id == session_id
        ).first()
        if not session:
            return {"success": False, "error": "会话不存在"}
        if session.status not in ("running", "defensive", "paused"):
            return {"success": False, "error": f"会话状态为 {session.status}，运行/防守/暂停中可添加"}

        current = _parse_symbol_list(session.symbols)
        auto_coin = _parse_symbol_list(getattr(session, "auto_coin_symbols", None))
        by_tier = _parse_by_tier_map(getattr(session, "fixed_symbols_by_tier", None))
        # 脏数据：同币两表并存时，固定表优先，踢出 AI 池（固定不占 AI 槽）
        overlap = set(current) & set(auto_coin)
        if overlap:
            auto_coin = [s for s in auto_coin if s not in overlap]
            session.auto_coin_symbols = auto_coin
            logger.info(
                f"[FullAuto] 会话 {session_id} 清理两表重叠(固定优先): {sorted(overlap)}"
            )
        added = []
        skipped_fixed = []

        if is_auto_coin:
            # AI 选币：只进 auto_coin_symbols，受会话槽位限制（5~10，默认 5）
            try:
                raw_slots = getattr(session, "auto_coin_max_slots", None)
                max_slots = 5 if raw_slots is None else max(5, min(10, int(raw_slots)))
            except Exception:
                max_slots = 5
            fixed_set = set(current)
            for _tv in by_tier.values():
                fixed_set |= set(_tv)
            room = max(0, max_slots - len(auto_coin))
            if room <= 0:
                return {
                    "success": False,
                    "error": f"AI选币槽位已满（{len(auto_coin)}/{max_slots}），请先移除或调高槽位(5-10)",
                    "symbols": current,
                    "auto_coin_symbols": auto_coin,
                }
            for sym in symbols:
                s = (sym or "").strip().upper()
                if not s or s in auto_coin:
                    continue
                if s in fixed_set:
                    skipped_fixed.append(s)
                    continue
                if len(added) >= room:
                    break
                auto_coin.append(s)
                added.append(s)
            if not added:
                msg = "无新自动选币需要添加"
                if skipped_fixed:
                    msg = f"已在固定交易对中，不占AI槽位: {', '.join(skipped_fixed)}"
                return {
                    "success": True,
                    "symbols": current,
                    "auto_coin_symbols": auto_coin,
                    "added": [],
                    "skipped_fixed": skipped_fixed,
                    "message": msg,
                }
            session.auto_coin_symbols = auto_coin
            session.symbols = current  # 固定表不动
        else:
            t = str(tier or "long").strip().lower()
            if t not in ("short", "mid", "long"):
                return {"success": False, "error": "tier 须为 short/mid/long"}
            ok, bad = validate_symbols_in_backup_pool(symbols)
            if bad:
                return {
                    "success": False,
                    "error": f"不在固定币备选池(交易对): {', '.join(bad)}",
                    "rejected": bad,
                }
            tier_list = list(
                by_tier[t] if t in by_tier else (current if not by_tier else [])
            )
            for s in ok:
                if s in tier_list:
                    continue
                if s in auto_coin:
                    auto_coin = [x for x in auto_coin if x != s]
                tier_list.append(s)
                added.append(s)
            if not added:
                return {
                    "success": True,
                    "symbols": current,
                    "auto_coin_symbols": auto_coin,
                    "fixed_symbols_by_tier": by_tier,
                    "added": [],
                    "message": "无新交易对需要添加",
                }
            by_tier[t] = tier_list
            # 确保三键都有：缺键补空列表（不要用并集回填，否则短/中/长又被焊死）
            for k in ("short", "mid", "long"):
                by_tier.setdefault(k, [])
            by_tier[t] = tier_list
            union = _union_preserve(
                by_tier.get("short", []),
                by_tier.get("mid", []),
                by_tier.get("long", []),
            )
            session.fixed_symbols_by_tier = by_tier
            session.symbols = union
            session.auto_coin_symbols = auto_coin
            current = union

        self._append_event(
            session,
            "symbols_added",
            (
                f"添加AI选币: {', '.join(added)}，自动池 {len(auto_coin)}/{getattr(session, 'auto_coin_max_slots', 5) or 5}"
                + (f"（跳过固定币 {', '.join(skipped_fixed)}）" if skipped_fixed else "")
                if is_auto_coin
                else f"添加{tier or 'long'}固定交易对: {', '.join(added)}，固定并集 {len(current)} / AI池 {len(auto_coin)}"
            ),
        )
        self._safe_commit(db, "add_symbols")

        if session_id in self._running_sessions:
            self._running_sessions[session_id]["symbols"] = current
            self._running_sessions[session_id]["auto_coin_symbols"] = list(auto_coin)

        logger.info(
            f"[FullAuto] 会话 {session_id} 添加{'AI' if is_auto_coin else '固定'}: {added}，"
            f"固定={current} AI={auto_coin}"
        )

        # 为新增币种异步回填 K 线（不阻塞注入；否则 VIP 补仓会被 stale K 线卡死几十秒）
        if added:
            def _backfill_klines(syms: list) -> None:
                try:
                    from backend.services.market_data import get_kline_data
                    for _sym in syms:
                        for _tf in ["5m", "15m", "1h", "4h", "1d"]:
                            try:
                                get_kline_data(_sym, period=_tf, count=200)
                            except Exception:
                                pass
                    logger.info(f"[FullAuto] 新增币种 {syms} K线异步回填完成")
                except Exception as _bkerr:
                    logger.warning(f"[FullAuto] 新增币种 K线异步回填失败: {_bkerr}")

            try:
                import threading
                threading.Thread(
                    target=_backfill_klines,
                    args=(list(added),),
                    daemon=True,
                    name=f"kline-backfill-{session_id}",
                ).start()
            except Exception as _bkerr:
                logger.warning(f"[FullAuto] 启动 K线回填线程失败: {_bkerr}")

        # Fix 22b: 注入后立即触发订单流订阅（原不触发 → 新币 OI/CVD/Taker 全缺）
        if added:
            try:
                from services.market_flow_collector import market_flow_collector
                trade_universe = list(dict.fromkeys(current + auto_coin))
                if market_flow_collector.running:
                    market_flow_collector.refresh_subscriptions(trade_universe)
                    logger.info(f"[FullAuto] 新增币种 {added} 订单流订阅已刷新")
                elif trade_universe:
                    market_flow_collector.start(symbols=trade_universe)
                    logger.info(f"[FullAuto] 订单流采集器已启动: {trade_universe}")
            except Exception as _mfc_err:
                logger.warning(f"[FullAuto] 新增币种订单流订阅失败(下次健康检查重试): {_mfc_err}")

        return {
            "success": True,
            "symbols": current,
            "auto_coin_symbols": auto_coin,
            "added": added,
            "skipped_fixed": skipped_fixed,
        }

    def _orphan_trading_symbols(self, db: Session, session, candidates: List[str]) -> List[str]:
        """候选中「不在 session.symbols、但仍在实际交易」的币。

        判据：仍有 active 策略，或仍有未平持仓。这两者都会被
        `_resolve_session_trade_symbols` 并入扫描 universe，使该币在被 AI 选币
        轮出、K线停采之后依然被下单。只认这两种实据，不做无条件放行，避免把
        任意传入的 symbol 都当成"要下架"。
        """
        from backend.database.models import AIStrategy, PaperPosition

        norm: List[str] = []
        seen = set()
        for c in candidates or []:
            u = (c or "").strip().upper()
            if u and u not in seen:
                seen.add(u)
                norm.append(u)
        if not norm:
            return []

        out: List[str] = []

        def _collect(rows) -> None:
            for (s,) in rows:
                u = (s or "").strip().upper()
                if u and u not in out:
                    out.append(u)

        try:
            _collect(
                db.query(AIStrategy.primary_symbol)
                .filter(
                    AIStrategy.primary_symbol.in_(norm),
                    AIStrategy.status == "active",
                )
                .distinct()
                .all()
            )
        except Exception as e:
            logger.debug(f"[FullAuto] _orphan_trading_symbols 查策略失败: {e}")

        try:
            _acct = self._get_trading_account_id(db, session)
            if _acct:
                _collect(
                    db.query(PaperPosition.symbol)
                    .filter(
                        PaperPosition.account_id == _acct,
                        PaperPosition.symbol.in_(norm),
                        PaperPosition.status == "open",
                    )
                    .distinct()
                    .all()
                )
        except Exception as e:
            logger.debug(f"[FullAuto] _orphan_trading_symbols 查持仓失败: {e}")

        return out

    def remove_symbols(self, db: Session, session_id: str, symbols: List[str]) -> dict:
        """从运行中的会话移除交易对，并暂停相关策略"""
        from backend.database.models import AIStrategy, FullAutoSession

        session = db.query(FullAutoSession).filter(
            FullAutoSession.session_id == session_id
        ).first()
        if not session:
            return {"success": False, "error": "会话不存在"}
        # 2026-07-20：与 add_symbols 一致，running/defensive/paused 都允许移除交易对
        if session.status not in ("running", "defensive", "paused"):
            return {"success": False, "error": f"会话状态为 {session.status}，运行/防守/暂停中可移除"}

        # 两张表独立：固定 symbols / AI auto_coin_symbols，互不占对方名额
        from backend.services.auto_coin_selector import (
            _parse_by_tier_map,
            _parse_symbol_list,
            _union_preserve,
        )
        current = _parse_symbol_list(session.symbols)
        auto_coin = _parse_symbol_list(getattr(session, "auto_coin_symbols", None))
        by_tier = _parse_by_tier_map(getattr(session, "fixed_symbols_by_tier", None))
        want = []
        for sym in symbols:
            s = (sym or "").strip().upper()
            if s and s not in want:
                want.append(s)

        fixed_removed = [s for s in want if s in current]
        # 也从分周期表移除
        for tlist in by_tier.values():
            for s in want:
                if s in tlist and s not in fixed_removed:
                    fixed_removed.append(s)
        auto_removed = [s for s in want if s in auto_coin]
        for s in fixed_removed:
            if s in current:
                current.remove(s)
        for k in list(by_tier.keys()):
            by_tier[k] = [x for x in by_tier.get(k, []) if x not in set(want)]

        # 2026-07-31：AI 选币轮出的币不在 session.symbols 里（那里只有手动固定对），
        # 原逻辑因此把它们判为"无匹配"直接 return，策略从未被下架。但只要该币还有
        # active 策略或未平持仓，`_resolve_session_trade_symbols` 就会通过持仓/策略
        # 分支把它并回扫描 universe 继续下单——而它的 K线已随选币池停采，等于拿陈旧
        # 数据决策（实测出现过 25 小时前的 5m K线）。故把"不在两表但仍在实际
        # 交易"的币一并纳入下架范围。
        already = set(fixed_removed) | set(auto_removed)
        for s in self._orphan_trading_symbols(
            db, session, [x for x in want if x not in already]
        ):
            if s not in already:
                already.add(s)
                logger.info(
                    f"[FullAuto] remove_symbols: {s} 不在固定/AI表但仍有活跃策略/持仓，一并下架"
                )

        removed = list(already)
        if not removed:
            return {
                "success": True,
                "symbols": current,
                "auto_coin_symbols": auto_coin,
                "removed": [],
                "message": "无匹配的交易对",
            }

        new_auto = [s for s in auto_coin if s not in set(removed)]
        # 两表合计至少留一个交易对（可只留固定，或只留 AI）
        if len(current) == 0 and len(new_auto) == 0:
            return {"success": False, "error": "至少保留一个交易对（固定或AI选币），不能全部移除"}

        auto_coin = new_auto

        paused_count = 0
        # 2026-07-20：显式从 DB 查询最新 active_strategy_ids，避免 ORM 缓存导致
        # 删除时策略不在 active_ids（内存旧值）但 DB 实际含该策略 → 漏 pause。
        try:
            from sqlalchemy import text as _sa_text
            _row = db.execute(
                _sa_text("SELECT active_strategy_ids FROM full_auto_sessions "
                         "WHERE session_id = :sid"),
                {"sid": session_id},
            ).first()
            if _row and _row[0] is not None:
                _active_ids = list(_row[0])
                session.active_strategy_ids = _active_ids
        except Exception as _refresh_err:
            logger.warning(f"[FullAuto] remove_symbols: 刷新 active_ids 失败: {_refresh_err}")
            _active_ids = list(session.active_strategy_ids or [])
        # 2026-07-20：查询本会话 active_strategy_ids 里的所有 active/paused 策略（不限 auto_mode）。
        # 原查询 auto_mode=="full_auto" 漏了 template_driven 的策略，
        # 导致 ASTER 的 template_driven 策略（tpl_*）一直 active → 仪表盘仍显示 ASTER。
        # 也不用 account_id 过滤，因为策略的 account_id 可能是实盘账户(5)，
        # 而会话用的是模拟账户(14)，account_id 过滤会漏掉这些策略。
        # 用 active_strategy_ids 过滤更准确——只处理属于本会话的策略。
        for sym in removed:
            strats_q = db.query(AIStrategy).filter(
                AIStrategy.primary_symbol == sym,
                AIStrategy.status.in_(["active", "paused"]),
            )
            # 2026-07-31：此处原按 active_strategy_ids 过滤，使下方「不在 active_ids
            # 但仍 active」的分支永远取不到数据（死代码）——AI 选币轮出的币的策略正好
            # 落在这一类，于是一直保持 active、把该币钉在扫描 universe 里。改为不过滤，
            # 由下方分支按是否在 active_ids 分别处理。
            strats = strats_q.all()
            _matched_in_active = 0
            for st in strats:
                if st.strategy_id in _active_ids:
                    _matched_in_active += 1
                    st.status = "paused"
                    paused_count += 1
                    _active_ids.remove(st.strategy_id)
                    # 2026-06-19: 统一注册到 SymbolLockRegistry
                    try:
                        from backend.services.symbol_lock_registry import lock_registry
                        lock_registry.lock(sym, strategy_id=str(st.strategy_id),
                                           reason_code="symbol_removed", by="remove_symbol")
                    except Exception:
                        pass
                else:
                    # 2026-07-20：策略不在 active_strategy_ids 里但仍为 active 的，
                    # 同样要 pause，否则 tier_status 仍会显示该 symbol。
                    # 原逻辑只处理 active_strategy_ids 内的策略，导致删除 BNB 后
                    # 仪表盘仍显示 BNB（因为 BNB 的策略还在 active）。
                    if st.status == "active":
                        st.status = "paused"
                        paused_count += 1
                        logger.info(
                            f"[FullAuto] remove_symbols: 暂停未在 active_ids 的策略 {st.strategy_id} ({sym})"
                        )
                # 2026-07-20：保险起见，无论如何都尝试从 _active_ids 移除
                # （应对 ORM 缓存导致内存 active_ids 与 DB 不一致的情况）
                if st.strategy_id in _active_ids and st.status == "paused":
                    _active_ids.remove(st.strategy_id)
            logger.info(
                f"[FullAuto] remove_symbols: {sym} 查到 {len(strats)} 个策略，"
                f"其中 {_matched_in_active} 个在 active_strategy_ids 内，共暂停 {paused_count} 个"
            )
        # 把更新后的 _active_ids 写回 session
        session.active_strategy_ids = _active_ids

        session.symbols = current
        if by_tier:
            # 同步并集；若 by_tier 非空则写回
            union = _union_preserve(
                by_tier.get("short", []),
                by_tier.get("mid", []),
                by_tier.get("long", []),
            )
            session.fixed_symbols_by_tier = by_tier
            if union:
                session.symbols = union
                current = union
        session.auto_coin_symbols = auto_coin
        self._append_event(
            session,
            "symbols_removed",
            f"移除交易对: {', '.join(removed)}（暂停 {paused_count} 个策略），"
            f"固定剩余: {', '.join(current) or '无'}；AI选币剩余: {', '.join(auto_coin) or '无'}"
        )
        self._safe_commit(db, "remove_symbols")

        if session_id in self._running_sessions:
            self._running_sessions[session_id]["symbols"] = current
            self._running_sessions[session_id]["auto_coin_symbols"] = list(auto_coin)

        # 2026-07-20：平掉被删币种的未平仓持仓。
        # 原实现只从 session.symbols 移除并暂停策略，但持仓仍残留 → paper monitor
        # 继续在已删币上做风控（SL 下压等），且会占用 unified tick 让三周期卡住。
        # 删除币种时应同步平仓，让该币彻底退出交易。
        # 用独立 session 做平仓，避免与 paper monitor 的锁冲突污染主 session。
        closed_positions = 0
        try:
            from backend.services.paper_trading_engine import paper_engine
            from backend.database.models import PaperPosition
            from backend.database.connection import SessionLocal as _CloseSessionLocal
            _acct = self._get_trading_account_id(db, session)
            if _acct:
                for sym in removed:
                    # 每个币种用独立 session，隔离锁冲突。
                    # paper monitor 每 30s 更新持仓，与平仓操作可能 deadlock。
                    # 失败后 rollback 重试，最多 3 次。
                    _close_db = _CloseSessionLocal()
                    _max_attempts = 3
                    for _attempt in range(_max_attempts):
                        try:
                            open_positions = _close_db.query(PaperPosition).filter(
                                PaperPosition.account_id == _acct,
                                PaperPosition.symbol == sym,
                                PaperPosition.status == "open",
                            ).all()
                            if not open_positions:
                                break
                            for pos in open_positions:
                                try:
                                    paper_engine.close_position(
                                        _close_db, _acct, sym, pos.side,
                                        reason="symbol_removed",
                                        strategy_id=pos.strategy_id,
                                    )
                                    closed_positions += 1
                                    logger.info(
                                        f"[FullAuto] remove_symbols: 已平仓 {sym} {pos.side} "
                                        f"(strategy={pos.strategy_id})"
                                    )
                                except Exception as _cp_err:
                                    logger.warning(
                                        f"[FullAuto] remove_symbols: 平仓 {sym} {pos.side} 失败: {_cp_err}"
                                    )
                                    try:
                                        _close_db.rollback()
                                    except Exception:
                                        pass
                            # 重新查询是否还有未平仓持仓
                            _remaining = _close_db.query(PaperPosition).filter(
                                PaperPosition.account_id == _acct,
                                PaperPosition.symbol == sym,
                                PaperPosition.status == "open",
                            ).count()
                            if _remaining == 0:
                                break
                        except Exception as _sym_err:
                            logger.warning(
                                f"[FullAuto] remove_symbols: 查询 {sym} 持仓失败(第{_attempt+1}次): {_sym_err}"
                            )
                            try:
                                _close_db.rollback()
                            except Exception:
                                pass
                            if _attempt < _max_attempts - 1:
                                import time as _time
                                _time.sleep(1)  # 等 paper monitor 释放锁
                    try:
                        _close_db.close()
                    except Exception:
                        pass
        except Exception as _close_err:
            logger.warning(f"[FullAuto] remove_symbols: 平仓清理异常: {_close_err}")

        # 平仓完成后补一条事件记录平仓结果
        if closed_positions > 0:
            try:
                self._append_event(
                    session,
                    "positions_closed",
                    f"已平仓 {closed_positions} 个持仓（币种: {', '.join(removed)}，原因: symbol_removed）",
                )
                self._safe_commit(db, "remove_symbols_close")
            except Exception as _ev_err:
                logger.warning(f"[FullAuto] remove_symbols: 平仓事件记录失败: {_ev_err}")

        logger.info(
            f"[FullAuto] 会话 {session_id} 移除交易对: {removed}，暂停 {paused_count} 个策略，"
            f"平仓 {closed_positions} 个持仓，固定={current} AI={auto_coin}"
        )
        return {
            "success": True,
            "symbols": current,
            "auto_coin_symbols": auto_coin,
            "removed": removed,
            "paused_strategies": paused_count,
            "closed_positions": closed_positions,
        }

    def get_session_status(self, db: Session, session_id: str) -> Optional[dict]:
        """获取会话完整状态"""
        from backend.database.models import (
            AIStrategy,
            FullAutoSession,
            PaperPosition,
            StrategyTrade,
        )

        session = db.query(FullAutoSession).filter(
            FullAutoSession.session_id == session_id
        ).first()
        if not session:
            return None

        trading_mode = session.trading_mode or "paper"

        price_cache: dict[str, float] = {}
        if trading_mode == "paper":
            try:
                from backend.services.price_cache import get_cached_price
                for sym in (session.symbols or []):
                    p = get_cached_price(sym, "CRYPTO", "mainnet")
                    if p:
                        price_cache[sym] = p
            except Exception:
                pass

        def _calc_strategy_stats(strategy_id: str) -> dict:
            """单策略统计（paper 模式：PaperPosition 唯一源，含分批 PnL - 已扣分批 fee）

            PnL 口径：
            - 剩余仓位算术 PnL：``(close/mark - entry) * 剩余 size``（按方向取反）
            - + ``partial_realized_pnl``：分批止盈累计已实现盈亏
            - - ``partial_fee_paid``：分批平仓已支付的手续费
            - 最后一笔全平的 fee 不单独落在 PaperPosition，仅能在账户本
              ``PaperBalance.total_fee_paid`` 找到，因此单策略视图会比
              session 真账户权益差略偏乐观（差异 = 全平笔数 × taker fee）。
            - 若需 100% 精确，需改走 PaperTrade 流水聚合，代价较大暂不做。
            """
            total_trades = 0
            wins = 0
            total_pnl = 0.0

            if trading_mode != "paper":
                st_pnl = db.query(sa_func.coalesce(sa_func.sum(StrategyTrade.pnl), 0)).filter(
                    StrategyTrade.strategy_id == strategy_id,
                ).scalar()
                total_pnl += float(st_pnl or 0)
                st_count = db.query(sa_func.count(StrategyTrade.id)).filter(
                    StrategyTrade.strategy_id == strategy_id,
                ).scalar() or 0
                total_trades += st_count
            else:
                all_positions = db.query(PaperPosition).filter(
                    PaperPosition.strategy_id == strategy_id,
                ).all()

                for pos in all_positions:
                    total_trades += 1
                    partial_pnl = float(pos.partial_realized_pnl or 0)
                    partial_fee = float(pos.partial_fee_paid or 0)
                    pos_pnl = 0.0

                    if pos.status in ("closed", "liquidated"):
                        remain_pnl = 0.0
                        if pos.close_price and pos.entry_price:
                            sz = float(pos.size or 0)
                            if pos.side == "long":
                                remain_pnl = (float(pos.close_price) - float(pos.entry_price)) * sz
                            else:
                                remain_pnl = (float(pos.entry_price) - float(pos.close_price)) * sz
                        pos_pnl = remain_pnl + partial_pnl - partial_fee
                    elif pos.status == "open":
                        mark = price_cache.get(pos.symbol) or pos.mark_price
                        if mark and pos.entry_price:
                            sz = float(pos.size or 0)
                            if pos.side == "long":
                                cur_upnl = (float(mark) - float(pos.entry_price)) * sz
                            else:
                                cur_upnl = (float(pos.entry_price) - float(mark)) * sz
                        else:
                            cur_upnl = float(pos.unrealized_pnl or 0)
                        pos_pnl = cur_upnl + partial_pnl - partial_fee

                    total_pnl += pos_pnl
                    if pos_pnl > 0:
                        wins += 1

            win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
            return {
                "total_trades": total_trades,
                "win_rate": round(win_rate, 1),
                "total_pnl": round(total_pnl, 2),
            }

        def _build_strategy_info(strat, status_override: str = None):
            stats = _calc_strategy_stats(strat.strategy_id)
            return {
                "strategy_id": strat.strategy_id,
                "name": strat.name,
                "status": status_override or strat.status,
                "primary_symbol": strat.primary_symbol,
                "timeframe": strat.timeframe,
                "timeframe_tier": getattr(strat, "timeframe_tier", None) or self._NATURE_TO_TIER_MAP.get(
                    (strat.genome or {}).get("trade_nature", ""), None) if strat.genome else None,
                "auto_mode": strat.auto_mode,
                "total_trades": stats["total_trades"],
                "win_rate": stats["win_rate"],
                "total_pnl": stats["total_pnl"],
            }

        active_ids = session.active_strategy_ids or []
        strategies_info = []
        seen_ids = set()

        for sid in active_ids:
            strat = db.query(AIStrategy).filter(AIStrategy.strategy_id == sid).first()
            if strat:
                strategies_info.append(_build_strategy_info(strat))
                seen_ids.add(sid)

        for symbol in (session.symbols or []):
            paused = db.query(AIStrategy).filter(
                AIStrategy.primary_symbol == symbol,
                AIStrategy.status == "paused",
            ).all()
            for ps in paused:
                if ps.strategy_id in seen_ids:
                    continue
                if ps.strategy_id in (session.terminated_strategy_ids or []):
                    continue
                strategies_info.append(_build_strategy_info(ps, "paused"))
                seen_ids.add(ps.strategy_id)

        recent_events = list((session.event_log or [])[-50:])
        try:
            from backend.database.connection import AnalyticsSessionLocal
            from backend.database.models import AIDecisionLog

            _trading_acct_for_logs = self._get_trading_account_id(db, session)
            _ana_db = AnalyticsSessionLocal()
            try:
                _latest_decisions = (
                    _ana_db.query(AIDecisionLog)
                    .filter(AIDecisionLog.account_id == _trading_acct_for_logs)
                    .order_by(AIDecisionLog.created_at.desc())
                    .limit(40)
                    .all()
                )
                for _log in _latest_decisions:
                    _confidence = None
                    _tier_lbl = "AI"
                    _raw_reason = _log.reason or ""
                    try:
                        _snap = json.loads(_log.decision_snapshot or "{}")
                        if isinstance(_snap, dict):
                            _confidence = _snap.get("confidence")
                            # 从 snapshot 提取 tier 标注（短/中/长）
                            _tn = (_snap.get("trade_nature") or _snap.get("nature") or "").strip().lower()
                            _tier_lbl = self._event_scope_label(_tn, _snap.get("tier", "mid"))
                            _raw_reason = _snap.get("reasoning") or _raw_reason
                    except Exception:
                        # 旧数据：decision_snapshot 是纯文本不是 JSON
                        # 从 reason 文本推断 tier
                        _reason_lower = _raw_reason.lower()
                        if "scalp" in _reason_lower or "scalprouter" in _reason_lower or "短线" in _raw_reason:
                            _tier_lbl = "短线"
                        elif "swing" in _reason_lower or "波段" in _raw_reason or "中线" in _raw_reason:
                            _tier_lbl = "中线"
                        elif "trend" in _reason_lower or "趋势" in _raw_reason or "长线" in _raw_reason:
                            _tier_lbl = "长线"
                    _conf_text = f" (置信={_confidence}%)" if _confidence is not None else ""
                    _log_time = _log.created_at or _log.decision_time
                    if _log_time is not None and getattr(_log_time, "tzinfo", None) is None:
                        # PostgreSQL TIMESTAMP 不带时区，当前库里写入的是本地北京时间。
                        # 不能按 UTC 标记，否则前端会再 +8 小时显示成次日 00:xx。
                        _log_time = _log_time.replace(tzinfo=timezone(timedelta(hours=8)))
                    recent_events.append({
                        "time": self._utc_iso(_log_time),
                        "event": "master_decision",
                        "detail": (
                            f"🎯 {_log.symbol or '?'}[{_tier_lbl}]: {_log.operation}{_conf_text} | "
                            f"{_raw_reason[:220]}"
                        ),
                        "severity": "info",
                        "source": _log.decision_source or "llm",
                    })
            finally:
                _ana_db.close()

            def _event_ts(_evt):
                try:
                    return datetime.fromisoformat(str(_evt.get("time", "")).replace("Z", "+00:00"))
                except Exception:
                    return datetime.min.replace(tzinfo=timezone.utc)

            _dedup = {}
            for _evt in recent_events:
                _key = (_evt.get("time"), _evt.get("event"), _evt.get("detail"))
                _dedup[_key] = _evt
            recent_events = sorted(_dedup.values(), key=_event_ts)[-80:]
        except Exception as _evt_err:
            logger.debug(f"[FullAuto] latest AI events merge skipped: {_evt_err}")
        display_market_summary = dict(session.last_market_summary or {})
        try:
            for _sym in (session.symbols or []):
                _info = display_market_summary.get(_sym)
                if not isinstance(_info, dict):
                    _info = {}
                    display_market_summary[_sym] = _info
                _cached = self._market_scan_cache.get(_sym)
                if isinstance(_cached, dict):
                    for k, v in _cached.items():
                        if v is None or v == "":
                            continue
                        if k == "orchestrator" and isinstance(v, dict):
                            _prev = _info.get("orchestrator") if isinstance(_info.get("orchestrator"), dict) else {}
                            _info["orchestrator"] = {**_prev, **v}
                        else:
                            _info[k] = v
            if display_market_summary:
                self._ensure_market_prices(display_market_summary, list(session.symbols or []))
                from backend.services.exchange_config import get_active_exchange
                from backend.services.market_data import get_ticker_data

                _env = get_active_exchange()
                for _sym in (session.symbols or []):
                    _info = display_market_summary.get(_sym)
                    if not isinstance(_info, dict):
                        continue
                    if float(_info.get("current_price") or 0) <= 0:
                        tk = get_ticker_data(_sym, _env) or {}
                        _live = float(tk.get("price") or tk.get("last") or 0)
                        if _live > 0:
                            _info["current_price"] = _live
                            _info["price_source"] = "live_ticker"
                            _info["data_reliable"] = True
                            _info.pop("error", None)
                    self._normalize_orchestrator_for_ui(_info)
                    self._attach_scalp_advisory_for_ui(_sym, _info)
        except Exception:
            pass

        _live_total_pnl = session.total_pnl or 0
        _live_total_trades = session.total_trades or 0
        _live_winning = session.winning_trades or 0
        _account_balance = None
        if trading_mode == "paper":
            _trading_acct = self._get_trading_account_id(db, session)
            if _trading_acct:
                try:
                    from backend.database.models import PaperBalance
                    _pb = db.query(PaperBalance).filter(PaperBalance.account_id == _trading_acct).first()
                    if _pb:
                        _init = float(_pb.initial_balance or 10000)
                        _eq = float(_pb.total_equity or _init)
                        _live_total_pnl = round(_eq - _init, 4)
                        _account_balance = {
                            "initial_capital": _init,
                            "total_equity": _eq,
                            "available_balance": float(_pb.available_balance or 0),
                            "frozen_margin": float(_pb.frozen_margin or 0),
                            "unrealized_pnl": float(_pb.unrealized_pnl or 0),
                            "realized_pnl": float(_pb.realized_pnl or 0),
                            "total_fee_paid": float(_pb.total_fee_paid or 0),
                        }
                        _pos_q = db.query(PaperPosition).filter(PaperPosition.account_id == _trading_acct)
                        _reset_at = _pb.last_reset_at
                        if _reset_at:
                            _pos_q = _pos_q.filter(PaperPosition.opened_at >= _reset_at)
                        _all_pp = _pos_q.all()
                        _live_total_trades = len(_all_pp)
                        _live_winning = sum(1 for pp in _all_pp if float(pp.unrealized_pnl or 0) + float(pp.partial_realized_pnl or 0) > 0)
                except Exception:
                    pass

        _trader_mental_state = None
        try:
            _trading_acct_for_mental = self._get_trading_account_id(db, session)
            if _trading_acct_for_mental:
                from backend.services.position_memory_manager import position_manager
                _trader_mental_state = position_manager.get_mental_status_snapshot(
                    db, _trading_acct_for_mental
                )
        except Exception as _mental_err:
            logger.debug("[FullAuto] trader_mental_state 读取跳过: %s", _mental_err)

        _backup_pool: list = []
        try:
            from backend.services.trading_pairs_config import get_user_trading_pairs
            _backup_pool = list(get_user_trading_pairs() or [])
        except Exception:
            _backup_pool = []
        _by_tier = getattr(session, "fixed_symbols_by_tier", None) or {}
        try:
            from backend.services.auto_coin_selector import _parse_by_tier_map
            _by_tier = _parse_by_tier_map(_by_tier) or {}
        except Exception:
            if not isinstance(_by_tier, dict):
                _by_tier = {}
        _ai_mid_watch: list = []
        try:
            from backend.services.auto_coin_selector import get_ai_mid_candidates_for_session
            if getattr(session, "auto_coin_mid_enabled", False):
                _ai_mid_watch = list(
                    get_ai_mid_candidates_for_session(session.session_id, db=db) or []
                )
        except Exception:
            _ai_mid_watch = []

        return {
            "session_id": session.session_id,
            "account_id": session.account_id,
            "paper_account_id": getattr(session, 'paper_account_id', None),
            "trading_account_id": self._get_trading_account_id(db, session),
            "status": session.status,
            "symbols": session.symbols,
            "risk_level": session.risk_level,
            "risk_mode": getattr(session, "risk_mode", None) or "ai_dynamic",
            "trading_mode": session.trading_mode,
            "auto_coin_enabled": getattr(session, "auto_coin_enabled", False),
            "auto_coin_symbols": getattr(session, "auto_coin_symbols", None) or [],
            "auto_coin_max_slots": int(getattr(session, "auto_coin_max_slots", None) or 5),
            "auto_coin_mid_enabled": bool(getattr(session, "auto_coin_mid_enabled", False)),
            "auto_coin_mid_max_slots": int(getattr(session, "auto_coin_mid_max_slots", None) or 3),
            "auto_coin_mid_symbols": _ai_mid_watch,
            "fixed_symbols_by_tier": _by_tier,
            "backup_pool": _backup_pool,
            "started_at": self._utc_iso(session.started_at),
            "stopped_at": self._utc_iso(session.stopped_at),
            "total_strategies_created": session.total_strategies_created or 0,
            "active_strategies": strategies_info,
            "terminated_count": len(session.terminated_strategy_ids or []),
            "total_pnl": _live_total_pnl,
            "total_trades": _live_total_trades,
            "winning_trades": _live_winning,
            "win_rate": (_live_winning / _live_total_trades * 100) if _live_total_trades else 0,
            "max_drawdown": session.max_drawdown or 0,
            "current_drawdown": getattr(session, "current_drawdown", None) or 0,
            "account_balance": _account_balance,
            # Trade history from PaperPosition
            "trade_history": self._get_trade_history(db, session),
            "last_health_check": self._utc_iso(session.last_health_check_at),
            "last_market_summary": display_market_summary,
            "analyst_reports": getattr(session, "analyst_reports", None),
            "master_decision": getattr(session, "master_decision", None),  # D7: 总策报告
            "recent_events": recent_events,
            "system_health": {
                "data_flow_ok": self._health_status.get("data_flow_ok", True),
                "ai_connection_ok": self._health_status.get("ai_connection_ok", True),
                "consecutive_ai_failures": self._health_status.get("consecutive_ai_failures", 0),
                "last_ai_success": self._health_status.get("last_ai_success"),
                "active_alerts": [
                    e for e in recent_events
                    if e.get("severity") in ("critical", "warning")
                ][-5:],
            },
            "current_risk_assessment": getattr(session, "current_risk_assessment", None),
            "cooldown_recovery": self._get_cooldown_status(session.session_id),
            "trader_mental_state": _trader_mental_state,
            "config": {
                "max_concurrent_strategies": session.max_concurrent_strategies,
                "max_total_drawdown_pct": session.max_total_drawdown_pct,
                "daily_loss_limit_pct": session.daily_loss_limit_pct,
                "health_check_interval": session.health_check_interval,
                "strategy_min_lifetime": session.strategy_min_lifetime,
                "strategy_max_consecutive_losses": session.strategy_max_consecutive_losses,
            },
        }

    def restore_running_sessions(self):
        """应用启动时恢复所有运行中/防守/暂停的会话

        手动暂停(pause_reason=manual)的会话不恢复，需要用户手动resume。
        running/defensive 的会话都恢复调度循环。
        启动时自动清理 symbol:tier 重复策略。

        深挖第 5 项 (2026-05-08)：增加启动健康摘要，让用户直接从日志看到
        "AI 是否在跑"，避免再出现 ai_decision_logs=0 + 0 个 session 但用户
        以为系统在自动交易的迷惑场面。
        """
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import func as _sa_func

        from backend.database.connection import SessionLocal
        from backend.database.models import (
            AIDecisionLog,
            DecisionSnapshot,
            FullAutoSession,
        )

        db = SessionLocal()
        try:
            # ── 修复空字符串 JSON 列（防止 ORM 反序列化崩溃）──
            # 仅 SQLite 上有空字符串 JSON 列问题；PostgreSQL 上 jsonb='' 会报
            # "operator does not exist"，导致事务 abort，所以必须跳过非 SQLite 方言
            from backend.database.dialect import dialect as _dialect
            if _dialect.is_sqlite:
                _json_cols = ["symbols", "auto_coin_symbols", "active_strategy_ids",
                              "terminated_strategy_ids", "current_risk_assessment",
                              "last_market_summary", "analyst_reports", "event_log"]
                for _col in _json_cols:
                    try:
                        _fixed = db.execute(
                            sa_text(f"UPDATE full_auto_sessions SET {_col} = NULL "
                                     f"WHERE {_col} = ''")
                        ).rowcount
                        if _fixed:
                            logger.warning(f"[FullAuto] 修复空JSON列 {_col}: {_fixed} 行 -> NULL")
                    except Exception:
                        pass

            # 启动时先清理重复策略
            self._cleanup_duplicate_strategies(db)

            sessions = db.query(FullAutoSession).filter(
                FullAutoSession.status.in_(["running", "defensive", "paused"])
            ).all()
            for s in sessions:
                pause_reason = getattr(s, "pause_reason", None)
                if s.status == "paused" and pause_reason == "manual":
                    logger.info(f"[FullAuto] 跳过手动暂停的会话 {s.session_id}")
                    continue
                _trading_acct = s.paper_account_id if (s.trading_mode == "paper" and getattr(s, "paper_account_id", None)) else s.account_id
                self._running_sessions[s.session_id] = {
                    "account_id": s.account_id,
                    "trading_account_id": _trading_acct,
                    "symbols": s.symbols or [],
                    "risk_level": s.risk_level,
                    "risk_mode": getattr(s, "risk_mode", None) or "ai_dynamic",
                    "trading_mode": s.trading_mode,
                    "auto_coin_enabled": getattr(s, "auto_coin_enabled", False),
                    "arb_enabled": bool(getattr(s, "arb_enabled", False)),
                    "active_exchange": getattr(s, "active_exchange", None),
                }
                if s.status == "defensive":
                    self._defensive_entered_at[s.session_id] = time.time()
                    logger.info(f"[FullAuto] 恢复防守会话 {s.session_id}，开始计算峰值衰减")
                if self._paper_loss_locks_disabled(s):
                    if self._paper_auto_unlock_session(db, s):
                        self._safe_commit(db, "paper_unlock_restore", session=s)
                self._register_health_check(
                    s.session_id,
                    self._resolve_unified_tick_interval(s.health_check_interval or 300),
                )
                # [2026-07-10 修复] 显式注册中长线独立循环。
                # register_session 内部本应注册 scalp+midlong，但重启后 session 恢复
                # 走此路径时，register_session 内部的 DB 查询(_syms)可能因连接抖动异常，
                # 导致 midlong 注册被 except 吞掉 → 中长线永远不分析。
                # 这里显式补注册，确保 midlong 循环一定被调度。
                try:
                    self._register_midlong_agent_loop(s.session_id)
                except Exception as _ml_err:
                    logger.warning(f"[FullAuto] 恢复时 midlong 注册失败 {s.session_id}: {_ml_err}")
                logger.info(f"[FullAuto] 恢复会话 {s.session_id} (status={s.status}): {s.symbols}")
            if sessions:
                logger.info(f"[FullAuto] 共恢复 {len(sessions)} 个全自动会话")

            # ── 恢复 auto_coin 调度器 ──
            auto_coin_count = 0
            for sid, sinfo in self._running_sessions.items():
                sess_row = db.query(FullAutoSession).filter(
                    FullAutoSession.session_id == sid
                ).first()
                auto_syms = getattr(sess_row, "auto_coin_symbols", None) or [] if sess_row else []
                if sinfo.get("auto_coin_enabled") or auto_syms:
                    try:
                        from backend.services.auto_coin_selector import auto_coin_scheduler
                        auto_coin_scheduler.register_session(sid, sinfo["account_id"])
                        auto_coin_count += 1
                        logger.info(
                            f"[FullAuto] 恢复 auto_coin 调度器: {sid}"
                            f" (enabled={sinfo.get('auto_coin_enabled')}, auto_syms={len(auto_syms)})"
                        )
                    except Exception as _ac_err:
                        logger.warning(f"[FullAuto] 恢复 auto_coin 调度器失败 {sid}: {_ac_err}")
            if auto_coin_count > 0:
                logger.info(f"[FullAuto] 共恢复 {auto_coin_count} 个 auto_coin 调度器")

            # ── 启动健康摘要 ────────────────────────────
            try:
                _now = datetime.now(timezone.utc)
                _yesterday = _now - timedelta(hours=24)
                _all_sessions = (
                    db.query(FullAutoSession.status, _sa_func.count())
                    .group_by(FullAutoSession.status).all()
                )
                _status_str = ", ".join(f"{st}={n}" for st, n in _all_sessions) or "(无)"
                # DecisionSnapshot / AIDecisionLog 属于 AnalyticsBase，需使用
                # AnalyticsSessionLocal 查询；否则通过 Core DB 会话会触发跨库路由问题
                _ana_db = None
                try:
                    from backend.database.connection import AnalyticsSessionLocal
                    _ana_db = AnalyticsSessionLocal()
                    _snap_24h = (
                        _ana_db.query(_sa_func.count(DecisionSnapshot.id))
                        .filter(DecisionSnapshot.timestamp >= _yesterday).scalar() or 0
                    )
                    _log_24h = (
                        _ana_db.query(_sa_func.count(AIDecisionLog.id))
                        .filter(AIDecisionLog.created_at >= _yesterday).scalar() or 0
                    )
                except Exception:
                    _snap_24h = 0
                    _log_24h = 0
                finally:
                    if _ana_db is not None:
                        try:
                            _ana_db.close()
                        except Exception:
                            pass
                # 孤儿快照检测：session_id 不在 full_auto_sessions 中
                _orphan = db.execute(
                    "SELECT COUNT(*) FROM decision_snapshots ds WHERE ds.session_id IS NOT NULL "
                    "AND ds.session_id NOT IN (SELECT id FROM full_auto_sessions)"
                ).scalar() if False else None
                logger.warning(
                    "════════════════════════════════════════════════════════"
                )
                logger.warning(
                    f"[FullAuto] 启动健康摘要 ({_now.strftime('%Y-%m-%d %H:%M UTC')})"
                )
                logger.warning(f"[FullAuto] 全自动会话状态: {_status_str}")
                logger.warning(f"[FullAuto] 24h 决策快照: {_snap_24h} 条")
                logger.warning(f"[FullAuto] 24h AI 决策日志: {_log_24h} 条")
                if not sessions:
                    logger.warning(
                        "[FullAuto] ⚠️  当前没有任何 running/defensive/paused 会话，"
                        "AI 不会自动交易；如需启动请到 UI 「全自动交易」面板点开始"
                    )
                logger.warning(
                    "════════════════════════════════════════════════════════"
                )
            except Exception as _hc_err:
                logger.debug(f"[FullAuto] 启动健康摘要异常（不影响主流程）: {_hc_err}")
        except Exception as e:
            logger.warning(f"[FullAuto] 恢复会话失败: {e}")
        finally:
            db.close()

    def _cleanup_duplicate_strategies(self, db):
        from backend.services.full_auto.paper_session_helpers import (
            build_paper_session_host,
            cleanup_duplicate_strategies,
        )
        return cleanup_duplicate_strategies(db, build_paper_session_host(self))

    def _run_v3_factor_pipeline(
        self,
        db: Session = None,
        session=None,
        symbols: List[str] = None,
        market_summary: Dict[str, Any] = None,
        unified_snapshot=None,
        force: bool = False,
    ) -> tuple:
        from backend.services.full_auto.v3_factor_pipeline import (
            build_v3_factor_host,
            run_v3_factor_pipeline,
        )
        host = build_v3_factor_host(self)
        result = run_v3_factor_pipeline(
            host=host,
            db=db,
            session=session,
            symbols=symbols,
            market_summary=market_summary,
            unified_snapshot=unified_snapshot,
            force=force,
        )
        self._v3_factor_cache = host.v3_factor_cache
        return result

    def _run_with_timeout(self, fn, timeout_s, fallback=None, label=""):
        """Phase 0: 通用超时包装器 — 在独立线程中执行 fn, 超时返回 fallback。

        用于给主循环的每个子步骤添加独立超时, 防止单步卡死导致整个 tick 失败。

        超时处理:
        - 子线程被标记为 detached 但继续运行。
        - 在超时前注册的 DB session 会被关闭，防止 detached 线程持有写锁。
        """
        result_box = [fallback]
        exception_box = [None]
        _track_key = f"timeout:{label[:30]}:{id(fn)}"

        def _target():
            try:
                result_box[0] = fn()
            except Exception as _e:
                exception_box[0] = _e
            finally:
                # 子线程完成时自动从追踪字典中移除（如果有注册的话）
                self._active_db_sessions.pop(_track_key, None)

        t = threading.Thread(target=_target, daemon=True, name=f"timeout-{label[:20]}")
        t.start()
        t.join(timeout=timeout_s)
        if t.is_alive():
            logger.warning(
                f"[FullAuto][Phase0] 超时: {label or fn.__name__} "
                f"超过 {timeout_s}s, 使用 fallback"
            )
            # 关闭该超时线程可能持有的 DB session
            _leaked = self._active_db_sessions.pop(_track_key, None)
            if _leaked is not None:
                try:
                    _leaked.close()
                    logger.warning(f"[FullAuto][Phase0] 关闭超时线程 DB session: {_track_key}")
                except Exception:
                    pass
            return fallback
        if exception_box[0] is not None:
            logger.warning(
                f"[FullAuto][Phase0] 异常: {label or fn.__name__} "
                f"→ {type(exception_box[0]).__name__}: {exception_box[0]}"
            )
            return fallback
        return result_box[0]

    @staticmethod
    def _build_fast_stability_result(
        symbols,
        *,
        trigger: str = "timeout",
        timeout_s: Optional[float] = None,
    ) -> dict:
        from backend.services.full_auto.orch_background import build_fast_stability_result
        return build_fast_stability_result(symbols, trigger=trigger, timeout_s=timeout_s)

    def _run_health_check_safe(self, session_id: str):
        """安全包装的健康检查入口"""
        try:
            self._run_health_check(session_id)
        except Exception as e:
            logger.error(f"[FullAuto] 健康检查异常 {session_id}: {e}", exc_info=True)

    def _purge_stale_caches(self):
        from backend.services.full_auto.orch_background import (
            build_orch_background_host,
            purge_stale_caches,
        )
        host = build_orch_background_host(self)
        purge_stale_caches(host)
        self._last_cache_purge = host.last_cache_purge
        self._last_close_time = host.last_close_time
        self._last_reduce_time = host.last_reduce_time
        self._master_strat_cache = host.master_strat_cache
        self._partial_close_tracker = host.partial_close_tracker
        self._market_scan_cache = host.market_scan_cache
        self._market_scan_cache_ts = host.market_scan_cache_ts
        self._strategy_creation_ts = host.strategy_creation_ts
        self._health_status = host.health_status

    def _run_health_check(self, session_id: str, *, maintenance_only: bool = False):
        from backend.services.full_auto.health_check_cycle import (
            build_health_check_host,
            run_health_check,
        )
        host = build_health_check_host(self)
        run_health_check(session_id, host, maintenance_only=maintenance_only)
        self._current_trace_id = host.current_trace_id
        FullAutoTradingService._current_trace_id = host.current_trace_id
        self._last_orch_decisions = host.last_orch_decisions
        self._last_orch_decisions_ts = host.last_orch_decisions_ts
        self._last_unified_snapshot = host.last_unified_snapshot

    def _sanitize_market_summary_for_qaa(market_summary: dict) -> dict:
        from backend.services.full_auto.market_summary_helpers import sanitize_market_summary_for_qaa
        return sanitize_market_summary_for_qaa(market_summary)

    def _annotate_auto_coin_meta(self, session_id: str, market_summary: dict) -> None:
        from backend.services.full_auto.market_summary_helpers import annotate_auto_coin_meta
        annotate_auto_coin_meta(session_id, market_summary)

    def _market_summary_ctx(self):
        from backend.services.full_auto.market_summary_helpers import MarketSummaryContext
        return MarketSummaryContext(
            market_scan_cache=self._market_scan_cache,
            last_unified_snapshot=getattr(self, "_last_unified_snapshot", None),
            bg_scan_running=self._bg_scan_running,
            start_bg_scan=self._bg_market_scan,
        )

    def _bootstrap_market_summary(self, symbols: List[str]) -> Dict[str, Any]:
        from backend.services.full_auto.market_summary_helpers import bootstrap_market_summary
        ctx = self._market_summary_ctx()
        result = bootstrap_market_summary(symbols, ctx)
        self._bg_scan_running = ctx.bg_scan_running
        return result

    def _ensure_market_prices(self, market_summary: Dict[str, Any], symbols: List[str]) -> None:
        from backend.services.full_auto.market_summary_helpers import ensure_market_prices
        ctx = self._market_summary_ctx()
        ensure_market_prices(market_summary, symbols, ctx)
        self._bg_scan_running = ctx.bg_scan_running

    @staticmethod
    def _normalize_orchestrator_for_ui(info: Dict[str, Any]) -> None:
        from backend.services.full_auto.orchestrator_ui_helpers import normalize_orchestrator_for_ui
        normalize_orchestrator_for_ui(info)

    @staticmethod
    def _tier_confidence_pct(
        *,
        tier: str = "long",
        orch: Optional[dict] = None,
        orch_dec=None,
    ) -> int:
        from backend.services.full_auto.orchestrator_ui_helpers import tier_confidence_pct
        return tier_confidence_pct(tier=tier, orch=orch, orch_dec=orch_dec)

    @staticmethod
    def _backfill_dec_confidence_from_orch(
        dec: dict,
        *,
        sym: str,
        market_summary: dict,
        tier: str = "long",
    ) -> int:
        from backend.services.full_auto.orchestrator_ui_helpers import backfill_dec_confidence_from_orch
        return backfill_dec_confidence_from_orch(
            dec, sym=sym, market_summary=market_summary, tier=tier,
        )

    def _orch_payload_from_decision(self, dec) -> dict:
        from backend.services.full_auto.orchestrator_ui_helpers import orch_payload_from_decision
        return orch_payload_from_decision(dec)

    def _orch_cache_ctx(self):
        from backend.services.full_auto.orch_cache_helpers import OrchCacheContext
        return OrchCacheContext(
            market_scan_cache=self._market_scan_cache,
            market_scan_cache_ts=float(getattr(self, "_market_scan_cache_ts", 0) or 0),
            orch_bg_thread=getattr(self, "_orch_bg_thread", None),
            last_orch_decisions=getattr(self, "_last_orch_decisions", None),
            last_orch_decisions_ts=float(getattr(self, "_last_orch_decisions_ts", 0) or 0),
        )

    def _orch_bg_cache_covers_symbols(self, symbols: list) -> bool:
        from backend.services.full_auto.orch_cache_helpers import orch_bg_cache_covers_symbols
        return orch_bg_cache_covers_symbols(symbols, self._orch_cache_ctx())

    def _merge_orch_from_scan_cache(self, market_summary: dict, symbols: list) -> None:
        from backend.services.full_auto.orch_cache_helpers import merge_orch_from_scan_cache
        merge_orch_from_scan_cache(market_summary, symbols, self._orch_cache_ctx())

    def _ensure_fresh_orch_decisions(self, market_summary: dict) -> dict:
        from backend.services.full_auto.orch_cache_helpers import ensure_fresh_orch_decisions
        ctx = self._orch_cache_ctx()
        result = ensure_fresh_orch_decisions(market_summary, ctx)
        self._last_orch_decisions = ctx.last_orch_decisions
        self._last_orch_decisions_ts = ctx.last_orch_decisions_ts
        return result

    def _inject_orch_scheduled_stubs(
        self,
        decisions: list,
        market_summary: dict,
        session=None,
    ) -> list:
        from backend.services.full_auto.orch_background import (
            build_orch_background_host,
            inject_orch_scheduled_stubs,
        )
        host = build_orch_background_host(self)
        result = inject_orch_scheduled_stubs(decisions, market_summary, host, session=session)
        self._last_orch_decisions = host.last_orch_decisions
        self._last_orch_decisions_ts = host.last_orch_decisions_ts
        return result

    def _build_portfolio_for_agents(self, db: Session, session) -> dict:
        """拉取账户持仓+balance，供 Swing/Trend 跨 tier 可见（独立循环与主循环共用）。"""
        try:
            from backend.services.paper_trading_engine import paper_engine
            from backend.services.strategy_analysis_context import (
                build_strategy_meta_cache,
                enrich_positions_with_strategy_meta,
            )

            acct_id = self._get_trading_account_id(db, session)
            if not acct_id:
                return {}
            positions = paper_engine.get_positions(db, acct_id) or []
            bal = paper_engine.get_balance(db, acct_id) or {}
            pos_ids = [p.get("strategy_id") for p in positions if p.get("strategy_id")]
            meta = build_strategy_meta_cache(db, pos_ids)
            enrich_positions_with_strategy_meta(positions, meta)
            for p in positions:
                if not isinstance(p, dict):
                    continue
                margin = float(p.get("margin") or 0)
                upnl = float(p.get("unrealized_pnl") or 0)
                if margin > 0:
                    p.setdefault("pnl_pct", upnl / margin * 100.0)
            return {"balance": bal, "positions": positions}
        except Exception as err:
            logger.debug("[FullAuto] portfolio snapshot 跳过: %s", err)
            return {}

    def _resolve_independent_strategy(self, db: Session, session, sym_u: str, tier: str):
        from backend.services.full_auto.midlong_helpers import (
            build_midlong_helpers_host,
            resolve_independent_strategy,
        )
        return resolve_independent_strategy(
            db, session, sym_u, tier, build_midlong_helpers_host(self),
        )

    def _persist_tcp_snapshot(
        self,
        session,
        *,
        symbol: str,
        tier: str,
        action: str,
        confidence: float,
        reasoning: str = "",
        market_snapshot: dict = None,
        proposal: dict = None,
        evaluate_verdict: dict = None,
        source_lane: str = "",
        trace_id: str = "",
        proposal_id: str = "",
        executed: bool = False,
        execution_channel: str = "",
        strategy_id: str = "",
    ) -> bool:
        """统一 DecisionSnapshot v2 + HMAC 链写入 analytics DB。"""
        from backend.services.decision_snapshot_writer import decision_snapshot_writer

        _mode = self._session_trading_mode(session)
        _acct = int(getattr(session, "account_id", None) or 0)
        try:
            snap = decision_snapshot_writer.build(
                session_id=getattr(session, "id", None),
                strategy_id=strategy_id,
                symbol=symbol,
                tier=tier,
                action=action,
                confidence=confidence,
                reasoning=reasoning,
                market_snapshot=market_snapshot,
                proposal=proposal,
                evaluate_verdict=evaluate_verdict,
                source_lane=source_lane,
                trace_id=trace_id,
                proposal_id=proposal_id,
                executed=executed,
                execution_channel=execution_channel or _mode,
                account_id=_acct,
                mode=_mode,
            )
            return decision_snapshot_writer.persist(snap)
        except Exception as err:
            logger.debug("[TCP] snapshot 跳过: %s", err)
            return False

    def _mark_master_decision_executed(
        self,
        snap,
        dec_log,
        db=None,
        *,
        operation: str = "",
    ) -> None:
        """Master 路径：下单成功后回写 AIDecisionLog + DecisionSnapshot.executed。"""
        if dec_log is not None:
            try:
                dec_log.executed = "true"
                if operation:
                    dec_log.operation = operation
                if db is not None:
                    db.flush()
            except Exception as err:
                logger.warning("[FullAuto] decision executed flush: %s", err)
                try:
                    if db is not None:
                        db.rollback()
                except Exception:
                    pass
        if snap is not None and hasattr(snap, "executed"):
            snap.executed = True

    @staticmethod
    def _decision_price_consistency_ok(sym: str, mkt: dict, proposal, mode: str) -> tuple:
        from backend.services.full_auto.execution_gates import decision_price_consistency_ok
        return decision_price_consistency_ok(sym, mkt, proposal, mode)

    def _evaluate_and_execute_proposal(
        self,
        *,
        db: Session,
        session,
        proposal,
        market_summary: dict,
        session_mode: str = "running",
        strat=None,
    ) -> bool:
        from backend.services.full_auto.proposal_execution import (
            build_proposal_execution_host,
            evaluate_and_execute_proposal,
        )
        return evaluate_and_execute_proposal(
            db=db,
            session=session,
            proposal=proposal,
            market_summary=market_summary,
            host=build_proposal_execution_host(self),
            session_mode=session_mode,
            strat=strat,
        )

    def _try_execute_independent_agent_open(
        self,
        *,
        db: Session,
        session,
        sym: str,
        tier: str,
        action: str,
        confidence: int,
        sl_pct: float = 0.0,
        tp_pct: float = 0.0,
        trade_nature: str,
        market_summary: dict,
        session_mode: str = "running",
        # [2026-07-21 修复] S2-5d 给 midlong_helpers.try_execute_independent_agent_open
        # 加了这三个新参数（写 exit_state_json 用），但这层薄封装方法一直没同步更新签名。
        # mlto_cycle.py 的 SwingAgent/TrendAgent独立开仓调用点透传了这些 kwargs 给
        # host.try_execute_independent_agent_open（绑定到本方法），Python 直接抛
        # TypeError: got an unexpected keyword argument 'tp_sl_proposal'——且这个异常在
        # 调用处被 try/except 吞掉只打 WARNING 日志，导致中线 SwingAgent 每次
        # should_open=True 想真正下单时都在这里静默崩溃，从未真正开过仓（长线因为走
        # MIDLONG_MLTO_CONTROLS_EXEC 网关下的另一条 MLTO 独立开仓路径，没有传这几个新
        # kwargs，所以没暴露此 bug）。修复：补全签名并透传，不再吞掉这条执行链路。
        tp_sl_proposal: Optional[Dict] = None,
        invalidation_condition: str = "",
        expected_hold_hours: float = 0.0,
        # [2026-07-31] MLTO 分档缩仓比例；此前漏透传 → TypeError 被吞，分档摆设
        tranche_margin_pct: float = 1.0,
    ) -> bool:
        from backend.services.full_auto.midlong_helpers import (
            build_midlong_helpers_host,
            try_execute_independent_agent_open,
        )
        return try_execute_independent_agent_open(
            db=db, session=session, sym=sym, tier=tier, action=action,
            confidence=confidence, sl_pct=sl_pct, tp_pct=tp_pct,
            trade_nature=trade_nature, market_summary=market_summary,
            session_mode=session_mode, host=build_midlong_helpers_host(self),
            tp_sl_proposal=tp_sl_proposal,
            invalidation_condition=invalidation_condition,
            expected_hold_hours=expected_hold_hours,
            tranche_margin_pct=tranche_margin_pct,
        )

    def _record_midlong_factor_snapshots(
        self,
        *,
        db,
        account_id: int,
        trade_id: int,
        symbol: str,
        side: str,
        market_data: dict,
    ) -> None:
        from backend.services.full_auto.midlong_helpers import record_midlong_factor_snapshots
        return record_midlong_factor_snapshots(
            db=db, account_id=account_id, trade_id=trade_id,
            symbol=symbol, side=side, market_data=market_data,
        )

    def _persist_independent_scan_log(
        self,
        *,
        account_id: Optional[int],
        symbol: str,
        tier: str,
        trade_nature: str,
        action: str,
        confidence: float,
        reasoning: str,
        agent_source: str,
        cited_fact_ids: Optional[List[str]] = None,
        evidence_audit: Optional[dict] = None,
        market_summary: Optional[dict] = None,
    ) -> None:
        from backend.services.full_auto.midlong_helpers import persist_independent_scan_log
        return persist_independent_scan_log(
            account_id=account_id, symbol=symbol, tier=tier,
            trade_nature=trade_nature, action=action, confidence=confidence,
            reasoning=reasoning, agent_source=agent_source,
            cited_fact_ids=cited_fact_ids, evidence_audit=evidence_audit,
            market_summary=market_summary,
        )

    def _inject_midlong_indicators(
        self, market_summary: dict, symbol: str, include_weekly: bool = False
    ) -> None:
        from backend.services.full_auto.midlong_helpers import inject_midlong_indicators
        return inject_midlong_indicators(market_summary, symbol, include_weekly=include_weekly)

    def _maintain_mlto_theses_for_session(
        self,
        *,
        session,
        market_summary: dict,
        analyst_reports: dict,
        mode: str,
        portfolio: dict,
        symbols_batch: Optional[List[str]] = None,
        mid_universe: Optional[List[str]] = None,
        run_mid: bool = True,
        run_long: bool = True,
        light_context: bool = False,
    ) -> None:
        from backend.services.full_auto.mlto_cycle import (
            build_mlto_cycle_host,
            maintain_mlto_theses_for_session,
        )
        host = build_mlto_cycle_host(self)
        maintain_mlto_theses_for_session(
            session=session,
            market_summary=market_summary,
            analyst_reports=analyst_reports,
            mode=mode,
            portfolio=portfolio,
            host=host,
            symbols_batch=symbols_batch,
            mid_universe=mid_universe,
            run_mid=run_mid,
            run_long=run_long,
            light_context=light_context,
        )
        self._mlto_handled_keys = host.mlto_handled_keys
        self._mlto_handled_lock = host.mlto_handled_lock
        # [Phase 5] 模式 B 分批止盈状态与 analyst 系统共享同一份 svc 状态，需写回
        self._long_tier_staged_tp_state = host.long_tier_staged_tp_state

    # [2026-08-17] _execute_mlto_lane 已删：旧长线 MLTO lane LLM 已下线，
    # 唯一决策源是独立 midlong 循环的 long_trend_v2。

    @staticmethod
    def _build_midlong_agent_envelope(
        *,
        agent_source: str,
        dec: dict,
        quant_brief: dict,
        sl_pct: float,
        tp_pct: float,
        sl_price: float,
        tp_price: float,
        sl_source: str,
        orch_snapshot_ts: float = 0.0,
        mlto_result=None,
    ) -> dict:
        from backend.services.agent_decision_envelope import AgentDecisionEnvelope
        kwargs = dict(
            agent_source=agent_source,
            alignment_score=int(quant_brief.get("alignment_score") or 0),
            cited_fact_ids=list(quant_brief.get("cited_fact_ids") or []),
            evidence_available_ratio=float(quant_brief.get("evidence_available_ratio") or 0),
            structure_sl_price=float(sl_price or 0),
            structure_tp_price=float(tp_price or 0),
            sl_pct=float(sl_pct or 0),
            tp_pct=float(tp_pct or 0),
            sl_source=sl_source,
            quant_brief=quant_brief or {},
            orch_snapshot_ts=orch_snapshot_ts,
        )
        if mlto_result and getattr(mlto_result, "thesis", None):
            t = mlto_result.thesis
            h = mlto_result.hub
            kwargs.update(
                thesis_id=t.thesis_id,
                hub_composite=float(h.composite if h else t.hub_composite),
                hub_adjusted=float(h.adjusted if h else t.hub_adjusted),
                consistency=float(h.consistency if h else t.consistency),
                open_readiness=int(t.open_readiness),
                open_readiness_at_entry=int(t.open_readiness),
                memory_event_ids=list(mlto_result.memory_event_ids or []),
                evidence_chain_snapshot=[
                    {"event_id": eid} for eid in (mlto_result.memory_event_ids or [])[:8]
                ],
                tranche_stage=int(t.tranche_stage),
                regime_hash=str(t.regime_hash or ""),
            )
        env = AgentDecisionEnvelope.new(**kwargs)
        env.attach_to_dec(dec)
        return env.to_dict()

    def _midlong_persistence_allow(self, sym: str, trade_nature: str, action: str) -> bool:
        """同 symbol+nature 需连续 MIDLONG_PERSISTENCE_TICKS 个 tick 同向才放行开仓。"""
        try:
            from backend.config.settings import MIDLONG_PERSISTENCE_TICKS
            ticks = max(1, int(MIDLONG_PERSISTENCE_TICKS or 1))
        except Exception:
            ticks = 1
        if ticks <= 1 or action not in ("buy", "sell"):
            return True
        key = f"{(sym or '').upper()}:{trade_nature or 'unknown'}"
        state = self._midlong_persistence_state.get(key, {})
        if state.get("action") == action:
            state["count"] = int(state.get("count") or 0) + 1
        else:
            state = {"action": action, "count": 1}
        self._midlong_persistence_state[key] = state
        return int(state.get("count") or 0) >= ticks

    @staticmethod
    def _attach_scalp_advisory_for_ui(sym: str, info: Dict[str, Any]) -> None:
        """合并 ScalpAdvisoryCache 到市场概览（UI 短线参谋条）。"""
        if not isinstance(info, dict):
            return
        if info.get("scalp_advisory"):
            return
        try:
            from backend.services.scalp.scalp_advisory_cache import scalp_advisory_cache
            adv = scalp_advisory_cache.get(sym)
            if adv:
                info["scalp_advisory"] = adv.to_dict()
        except Exception:
            pass

    def _bg_market_scan(self, symbols: List[str]):
        from backend.services.full_auto.market_scan_cycle import (
            build_market_scan_host,
            run_bg_market_scan,
        )
        host = build_market_scan_host(self)
        run_bg_market_scan(symbols, host)
        self._market_scan_cache = host.market_scan_cache
        self._market_scan_cache_ts = host.market_scan_cache_ts
        self._bg_scan_running = host.bg_scan_running


    def _scan_markets(self, db: Session, symbols: List[str]) -> Dict[str, Any]:
        from backend.services.full_auto.market_scan_cycle import (
            build_market_scan_host,
            run_scan_markets,
        )
        host = build_market_scan_host(self)
        result = run_scan_markets(db, symbols, host)
        self._market_scan_cache = host.market_scan_cache
        self._market_scan_cache_ts = host.market_scan_cache_ts
        self._bg_scan_running = host.bg_scan_running

        return result

    def _is_champion_strategy(self, mem) -> bool:
        from backend.services.full_auto.strategy_lifecycle import is_champion_strategy
        return is_champion_strategy(mem)

    def _should_terminate_strategy(self, db: Session, strategy, session) -> tuple:
        from backend.services.full_auto.strategy_lifecycle import should_terminate_strategy
        return should_terminate_strategy(db, strategy, session)

    def _pause_champion_strategy(self, db: Session, strategy, reason: str):
        from backend.services.full_auto.strategy_lifecycle import pause_champion_strategy
        return pause_champion_strategy(db, strategy, reason)

    def _snapshot_strategy_genome(self, db: Session, strategy, memory):
        from backend.services.full_auto.strategy_lifecycle import snapshot_strategy_genome
        return snapshot_strategy_genome(db, strategy, memory)

    def _terminate_strategy(self, db: Session, strategy, reason: str):
        from backend.services.full_auto.strategy_lifecycle import (
            build_strategy_lifecycle_host,
            terminate_strategy,
        )
        return terminate_strategy(db, strategy, reason, build_strategy_lifecycle_host(self))

    # REGIME_PARAM_PROFILES 已迁至 full_auto.strategy_lifecycle（保持类属性兼容）
    from backend.services.full_auto.strategy_lifecycle import (  # noqa: F811
        REGIME_PARAM_PROFILES as REGIME_PARAM_PROFILES,
    )

    def _get_regime_profile(self, regime: str) -> dict:
        from backend.services.full_auto.strategy_lifecycle import get_regime_profile
        return get_regime_profile(regime)

    def _adapt_strategy_params(self, db: Session, strategy, market_info: dict):
        from backend.services.full_auto.strategy_lifecycle import adapt_strategy_params
        return adapt_strategy_params(db, strategy, market_info)

    def _try_create_from_template(self, db, symbol: str, tier: str,
                                   account_id: int, risk_level: str,
                                   trading_mode: str) -> Optional[str]:
        from backend.services.full_auto.strategy_creation import try_create_from_template
        return try_create_from_template(db, symbol, tier, account_id, risk_level, trading_mode)

    def _auto_create_strategy(self, db, session, symbol: str,
                              market_info: dict,
                              _account_id: int = None,
                              _risk_level: str = None,
                              _trading_mode: str = None,
                              _symbols: list = None) -> Optional[str]:
        from backend.services.full_auto.strategy_creation import (
            auto_create_strategy,
            build_strategy_creation_host,
        )
        host = build_strategy_creation_host(self)
        result = auto_create_strategy(
            db, session, symbol, market_info, host,
            _account_id=_account_id, _risk_level=_risk_level,
            _trading_mode=_trading_mode, _symbols=_symbols,
        )
        self._strategy_creation_ts = host.strategy_creation_ts
        return result

    def _infer_timeframe_slots(self, market_info: dict) -> list:
        from backend.services.full_auto.strategy_creation import infer_timeframe_slots
        return infer_timeframe_slots(market_info)

    def _infer_timeframe_slot(self, market_info: dict) -> str:
        from backend.services.full_auto.strategy_creation import infer_timeframe_slot
        return infer_timeframe_slot(market_info)

    def _bg_create_strategy(self, session_id: str, account_id: int, symbol: str,
                           market_info: dict,
                           risk_level: str, trading_mode: str, symbols: list,
                           reason: str):
        from backend.services.full_auto.strategy_creation import bg_create_strategy
        return bg_create_strategy(
            session_id, account_id, symbol, market_info,
            risk_level, trading_mode, symbols, reason,
        )

    def _evaluate_dynamic_risk(self, session, market_summary: Dict[str, Any]):
        from backend.services.full_auto.symbol_risk import (
            build_symbol_risk_host,
            evaluate_dynamic_risk,
        )
        evaluate_dynamic_risk(session, market_summary, build_symbol_risk_host(self))

    # per-symbol 风控结果类型（兼容旧引用）
    from backend.services.full_auto.symbol_risk import PerSymbolRiskResult as _PerSymbolRiskResult

    def _update_symbol_daily_pnl(self, db: Session, session):
        from backend.services.full_auto.symbol_risk import (
            build_symbol_risk_host,
            update_symbol_daily_pnl,
        )
        host = build_symbol_risk_host(self)
        update_symbol_daily_pnl(db, session, host)
        self._symbol_daily_pnl = host.symbol_daily_pnl
        self._symbol_tier_daily_pnl = host.symbol_tier_daily_pnl

    def _freeze_symbol_strategies(self, db: Session, session, symbol: str, reason: str):
        from backend.services.full_auto.symbol_risk import (
            build_symbol_risk_host,
            freeze_symbol_strategies,
        )
        host = build_symbol_risk_host(self)
        freeze_symbol_strategies(db, session, symbol, reason, host)
        self._symbol_frozen_set = host.symbol_frozen_set
        self._symbol_frozen_tiers = host.symbol_frozen_tiers

    def _unfreeze_recovered_symbols(self, db: Session, session, still_frozen: List[str]):
        from backend.services.full_auto.symbol_risk import (
            build_symbol_risk_host,
            unfreeze_recovered_symbols,
        )
        host = build_symbol_risk_host(self)
        unfreeze_recovered_symbols(db, session, still_frozen, host)
        self._symbol_frozen_set = host.symbol_frozen_set
        self._symbol_frozen_tiers = host.symbol_frozen_tiers

    def _check_per_symbol_risk(self, db: Session, session) -> '_PerSymbolRiskResult':
        from backend.services.full_auto.symbol_risk import (
            build_symbol_risk_host,
            check_per_symbol_risk,
        )
        return check_per_symbol_risk(db, session, build_symbol_risk_host(self))

    def _check_global_risk(self, db: Session, session) -> Optional[str]:
        from backend.services.full_auto.symbol_risk import (
            build_symbol_risk_host,
            check_global_risk,
        )
        host = build_symbol_risk_host(self)
        result = check_global_risk(db, session, host)
        self._defensive_entered_at = host.defensive_entered_at
        return result

    def _run_analyst_system(self, db: Session, session, active_ids: list, market_summary: dict):
        from backend.services.full_auto.analyst_system_cycle import (
            build_analyst_system_host,
            run_analyst_system,
        )
        host = build_analyst_system_host(self)
        run_analyst_system(db, session, active_ids, market_summary, host)
        self._long_tier_staged_tp_state = host.long_tier_staged_tp_state
        self._pre_screen_results = host.pre_screen_results
        self._pre_screen_passed = host.pre_screen_passed
        self._mlto_handled_keys = host.mlto_handled_keys


    def _run_analyst_system_unified(self, db: Session, session, account, active_ids: list, market_summary: dict):
        from backend.services.full_auto.analyst_system_cycle import (
            build_analyst_system_host,
            run_analyst_system_unified,
        )
        host = build_analyst_system_host(self)
        run_analyst_system_unified(db, session, account, active_ids, market_summary, host)
        self._long_tier_staged_tp_state = host.long_tier_staged_tp_state
        self._pre_screen_results = host.pre_screen_results
        self._pre_screen_passed = host.pre_screen_passed
        self._mlto_handled_keys = host.mlto_handled_keys

    def _build_tier_protection(self) -> dict:
        from backend.config.settings import TIER_PROTECTION_PARAMS
        result = {}
        for tier, params in TIER_PROTECTION_PARAMS.items():
            _emerg = params.get("min_hold_emergency_loss_pct")
            if _emerg is None:
                _emerg = 5.0
            result[tier] = {
                "protect_min": params["min_hold_sec"] / 60,
                # 保护期内：浮亏超过 -emergency_pct（保证金%）才允许 close/reduce
                "emergency_pct": -float(_emerg),
            }
        return result

    @property
    def TIER_PROTECTION(self):
        if not hasattr(self, '_tier_protection_cache'):
            self._tier_protection_cache = self._build_tier_protection()
        return self._tier_protection_cache

    DEFAULT_PROTECTION = {"protect_min": 30, "emergency_pct": -5.0}
    REDUCE_COOLDOWN_MINUTES = 45
    REDUCE_MIN_LOSS_PCT = -4.0

    # ── 分层置信度门控：按仓位所属 tier 匹配对应周期的编排器置信度 ──
    # 只有对应周期置信度低于此阈值时，才允许 reduce/close
    _TIER_CONF_GATE = {
        "long":  0.45,   # 长线仓：长线置信度跌破 45% 才允许减仓/平仓
        "mid":   0.40,   # 中线仓：中线置信度跌破 40% 才允许
        "short": 0.35,   # 短线仓：短线置信度跌破 35% 才允许
    }

    @staticmethod
    def _get_tier_confidence(
        market_summary: dict, sym: str, pos_tier: str,
    ) -> float:
        """根据仓位的 tier，提取编排器中对应周期的置信度（0~1）。
        long 仓 → long_confidence，mid 仓 → mid_confidence，short 仓 → short_confidence。
        """
        try:
            _mkt = (market_summary or {}).get(sym, {})
            _orch = _mkt.get("orchestrator", {}) if isinstance(_mkt, dict) else {}
            if not isinstance(_orch, dict):
                return 0.0
            _field = {"long": "long_confidence", "mid": "mid_confidence", "short": "short_confidence"}.get(
                pos_tier, "mid_confidence")
            return float(_orch.get(_field, 0) or 0)
        except Exception:
            return 0.0

    # P1-3: 方向胜率缓存（避免每次决策都查 DB）
    _direction_wr_cache: Dict[str, Any] = {"data": None, "ts": 0}
    _DIRECTION_WR_CACHE_TTL = 3600  # 1 小时

    def _get_direction_win_rate(self, direction: str) -> Optional[float]:
        """P1-3: 查询最近30天某方向的整体胜率（滚动窗口）。"""
        now = time.time()
        cache = self._direction_wr_cache
        if cache["data"] is not None and now - cache["ts"] < self._DIRECTION_WR_CACHE_TTL:
            return cache["data"].get(direction)
        try:
            from sqlalchemy import text

            from backend.database.connection import SessionLocal
            from backend.database.dialect import dialect
            db = SessionLocal()
            try:
                rows = db.execute(text("""
                    SELECT side,
                           COUNT(*) as cnt,
                           SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
                    FROM strategy_trades
                    WHERE created_at >= """ + dialect.datetime_now_minus(30) + """
                    GROUP BY side
                """)).fetchall()
                data = {}
                for side, cnt, wins in rows:
                    data[side.lower()] = float(wins) / float(cnt) if cnt else None
                cache["data"] = data
                cache["ts"] = now
                return data.get(direction)
            finally:
                db.close()
        except Exception:
            return None

    # P2-2: Symbol+方向级别的胜率缓存
    _sym_dir_wr_cache: Dict[str, Any] = {"data": None, "ts": 0}
    _SYM_DIR_WR_CACHE_TTL = 21600  # 6 小时

    def _get_symbol_direction_wr(self, symbol: str, direction: str) -> tuple:
        """P2-2: 查询某 symbol+方向 最近30天的胜率和样本数。"""
        now = time.time()
        cache = self._sym_dir_wr_cache
        cache_key = f"{symbol}_{direction}"
        if cache["data"] is not None and now - cache["ts"] < self._SYM_DIR_WR_CACHE_TTL:
            cached = cache["data"].get(cache_key)
            if cached:
                return cached
        try:
            from sqlalchemy import text

            from backend.database.connection import SessionLocal
            from backend.database.dialect import dialect
            db = SessionLocal()
            try:
                row = db.execute(text("""
                    SELECT COUNT(*) as cnt,
                           SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
                    FROM strategy_trades
                    WHERE symbol = :sym AND side = :dir
                      AND created_at >= """ + dialect.datetime_now_minus(30) + """
                """), {"sym": symbol, "dir": direction}).fetchone()
                cnt, wins = int(row[0] or 0), int(row[1] or 0)
                wr = float(wins) / float(cnt) if cnt > 0 else 0.5
                if cache["data"] is None:
                    cache["data"] = {}
                cache["data"][cache_key] = (wr, cnt)
                cache["ts"] = now
                return (wr, cnt)
            finally:
                db.close()
        except Exception:
            return (0.5, 0)

    def _ai_dynamic_position_pct(self, confidence: int, volatility: float,
                                  open_position_count: int,
                                  tier: str = "mid",
                                  tier_budget_pct: float = 0.0) -> float:
        from backend.services.full_auto.decision_sizing import ai_dynamic_position_pct
        return ai_dynamic_position_pct(
            confidence, volatility, open_position_count,
            tier=tier, tier_budget_pct=tier_budget_pct,
        )

    def _apply_tdi_position_advice(
        self,
        symbol: str,
        base_pct: float,
        confidence: int,
        volatility: float,
        open_position_count: int,
        tier: str = "mid",
        tier_budget_pct: float = 0.0,
        equity: float = 0.0,
        regime: str = "ranging",
        base_direction: str = "hold",
    ):
        from backend.services.full_auto.decision_sizing import apply_tdi_position_advice
        return apply_tdi_position_advice(
            symbol, base_pct, confidence, volatility, open_position_count,
            tier=tier, tier_budget_pct=tier_budget_pct, equity=equity,
            regime=regime, base_direction=base_direction,
        )

    def _log_pipeline_audit(self, symbol: str, decision_data: dict, action: str) -> None:
        """记录 Direction→Sizing→Risk→Execution 审计轨迹。"""
        try:
            from backend.services.agent_pipeline_contracts import build_audit_trail
            from backend.services.ai_agent_entrypoint_map import audit_decision_sizing_fields

            trail = build_audit_trail(
                {**decision_data, "symbol": symbol, "action": action},
                direction_source="master",
            )
            warn = audit_decision_sizing_fields(decision_data)
            if warn:
                logger.warning("[PipelineAudit] %s", warn)
            logger.info(
                "[PipelineAudit] %s action=%s sizing=%s lev=%s pct=%.2f%% notional=$%.0f",
                symbol,
                trail.final_action,
                trail.sizing_source,
                trail.final_leverage,
                trail.final_position_pct * 100,
                trail.final_notional_usd,
            )
        except Exception as audit_err:
            logger.debug("[PipelineAudit] skip: %s", audit_err)

    def _extract_ai_position_pct(self, dec: dict) -> Optional[float]:
        """从 AI 决策中提取仓位比例（占可用余额）。"""
        for _key in ("position_pct", "target_portion_of_balance", "size_percent"):
            try:
                _raw = dec.get(_key)
                if _raw is None or float(_raw) <= 0:
                    continue
                pct = float(_raw)
                if _key == "size_percent" or pct > 1.0:
                    pct = pct / 100.0
                return round(max(0.04, min(0.35, pct)), 4)
            except (TypeError, ValueError):
                continue
        return None

    def _resolve_alignment_scale(self, sym: str) -> float:
        from backend.services.full_auto.decision_sizing import (
            build_decision_sizing_host,
            resolve_alignment_scale,
        )
        return resolve_alignment_scale(sym, build_decision_sizing_host(self))

    def _resolve_decision_leverage(
        self,
        dec: dict,
        sym: str,
        tier: str,
        mkt: dict,
        db: Session,
        account_id: int,
        trade_nature: str = "",
        market_summary: dict = None,
    ) -> tuple:
        from backend.services.full_auto.decision_sizing import resolve_decision_leverage
        return resolve_decision_leverage(
            dec, sym, tier, mkt, db, account_id,
            trade_nature=trade_nature, market_summary=market_summary,
        )

    def _resolve_decision_position_pct(
        self,
        dec: dict,
        confidence: int,
        vol_value: float,
        open_position_count: int,
        tier: str,
        tier_budget_pct: float,
        total_equity: float,
        market_regime: str,
        sym: str,
        action: str,
    ) -> tuple:
        from backend.services.full_auto.decision_sizing import (
            build_decision_sizing_host,
            resolve_decision_position_pct,
        )
        return resolve_decision_position_pct(
            dec, confidence, vol_value, open_position_count, tier,
            tier_budget_pct, total_equity, market_regime, sym, action,
            build_decision_sizing_host(self),
        )

    def _calibrate_confidence(self, raw_conf: int, action: str, symbol: str,
                               analyst_reports: dict, market_summary: dict) -> int:
        from backend.services.full_auto.decision_sizing import (
            build_decision_sizing_host,
            calibrate_confidence,
        )
        return calibrate_confidence(
            raw_conf, action, symbol, analyst_reports, market_summary,
            build_decision_sizing_host(self),
        )

    def _expand_multi_tier_decisions(
        self,
        decisions: List[Dict],
        strat_tier_map: dict,
        orch_directions: dict,
        session,
    ) -> List[Dict]:
        from backend.services.full_auto.tier_fanout import (
            build_tier_fanout_host,
            expand_multi_tier_decisions,
        )
        return expand_multi_tier_decisions(
            decisions, strat_tier_map, orch_directions, session,
            build_tier_fanout_host(self),
        )

    def _factor_veto_check(self, db: Session, sym: str, action: str, mode: str = "paper") -> Optional[str]:
        from backend.services.full_auto.tp_sl_gates import (
            build_tp_sl_gates_host,
            factor_veto_check,
        )
        return factor_veto_check(db, sym, action, build_tp_sl_gates_host(self), mode=mode)

    def _execute_master_decisions(self, db: Session, session, account_id: int,
                                   decisions: List[Dict], positions_list: List[Dict],
                                   active_ids: list, market_summary: dict,
                                   mode: str, analyst_reports: dict = None,
                                   balance_info: dict = None,
                                   orch_directions: dict = None,
                                   strat_tier_map: dict = None):
        from backend.services.full_auto.master_execution import (
            build_master_execution_host,
            execute_master_decisions,
        )
        host = build_master_execution_host(self)
        execute_master_decisions(
            db, session, account_id, decisions, positions_list, active_ids,
            market_summary, mode, host,
            analyst_reports=analyst_reports,
            balance_info=balance_info,
            orch_directions=orch_directions,
            strat_tier_map=strat_tier_map,
        )
        self._current_decision_tier = getattr(host, "current_decision_tier", "")

    def _validate_tp_sl_by_nature(
        self, trade_nature: str, side: str, entry_price: float,
        tp_price, sl_price, symbol: str = "",
    ) -> tuple:
        from backend.services.full_auto.tp_sl_gates import validate_tp_sl_by_nature
        return validate_tp_sl_by_nature(
            trade_nature, side, entry_price, tp_price, sl_price, symbol=symbol,
        )

    def _compute_dynamic_min_sl(
        self, symbol: str, trade_nature: str, entry_price: float,
        fallback_pct: float = 0.025,
    ) -> float:
        from backend.services.full_auto.tp_sl_gates import compute_dynamic_min_sl
        return compute_dynamic_min_sl(symbol, trade_nature, entry_price, fallback_pct)

    def _evaluate_strategy_switches(self, db: Session, session, active_ids: list):
        """评估是否有策略需要切换到更适合当前环境的模板"""
        try:
            from backend.database.models import AIStrategy
            from backend.services.strategy_intelligence_engine import strategy_intelligence

            for sid in active_ids:
                strat = db.query(AIStrategy).filter(AIStrategy.strategy_id == sid).first()
                if not strat or strat.status != "active":
                    continue

                template_id = str(getattr(strat, "master_prompt_template_id", "") or "")
                if not template_id:
                    continue

                # 检测当前市场状态
                regime = strategy_intelligence._detect_regime_for_symbol(db, strat.primary_symbol)

                switch_rec = strategy_intelligence.should_switch_strategy(
                    db, template_id, regime,
                )
                if switch_rec:
                    self._append_event(
                        session, "strategy_switch_recommended",
                        f"{strat.name}: 建议切换到 {switch_rec['target_template_id']} "
                        f"(当前{switch_rec['current_score']:.3f} → 目标{switch_rec['target_score']:.3f}, "
                        f"提升{switch_rec['improvement_pct']:.0f}%)",
                    )
                    logger.info(
                        f"[FullAuto] 策略切换建议: {sid} → {switch_rec['target_template_id']} "
                        f"regime={regime} improvement={switch_rec['improvement_pct']:.0f}%"
                    )
        except Exception as e:
            # [fix] rollback 避免 InFailedSqlTransaction 污染后续操作
            try:
                db.rollback()
            except Exception:
                pass
            logger.debug(f"[FullAuto] 策略切换评估跳过: {e}")

    def _update_session_stats(self, db: Session, session, active_ids: list):
        from backend.services.full_auto.session_stats import (
            build_session_stats_host,
            update_session_stats,
        )
        update_session_stats(db, session, active_ids, build_session_stats_host(self))

    def cleanup_stale_strategies(self, db: Session) -> dict:
        from backend.services.full_auto.strategy_maintenance import (
            build_strategy_maintenance_host,
            cleanup_stale_strategies,
        )
        return cleanup_stale_strategies(db, build_strategy_maintenance_host(self))

    def merge_duplicate_strategies(self, db: Session, session_id: str) -> dict:
        from backend.services.full_auto.strategy_maintenance import (
            build_strategy_maintenance_host,
            merge_duplicate_strategies,
        )
        return merge_duplicate_strategies(db, session_id, build_strategy_maintenance_host(self))

    def _pause_all_strategies(self, db: Session, session) -> int:
        """暂停会话内所有活跃策略（批量查询）"""
        from backend.database.models import AIStrategy
        from backend.services.autonomous_strategy_service import autonomous_service

        sids = list(session.active_strategy_ids or [])
        if not sids:
            return 0

        strats = db.query(AIStrategy).filter(
            AIStrategy.strategy_id.in_(sids),
            AIStrategy.status == "active",
        ).all()

        for strat in strats:
            strat.status = "paused"
            try:
                autonomous_service.unregister_strategy(strat.strategy_id)
            except Exception:
                pass
            # 2026-06-19: 统一注册到 SymbolLockRegistry
            try:
                from backend.services.symbol_lock_registry import lock_registry
                lock_registry.lock(strat.primary_symbol or "", strategy_id=str(strat.strategy_id),
                                   reason_code="session_paused", by="pause_all")
            except Exception:
                pass
        try:
            db.flush()
        except Exception as flush_err:
            logger.warning(f"[FullAuto] _pause_all_strategies flush 异常(已回滚): {flush_err}")
            try:
                db.rollback()
            except Exception:
                pass
        return len(strats)

    # ══════════════════════════════════════════════════
    #  调度器集成
    # ══════════════════════════════════════════════════

    def _apply_paper_fast_trial_pace(self, trading_mode: str = "paper") -> None:
        """模拟盘快速试单：切到 blitz 档位（30s tick + 加速学习触发）。"""
        if (trading_mode or "paper").lower() != "paper":
            return
        try:
            from backend.config.settings import PAPER_FAST_TRIAL
            if not PAPER_FAST_TRIAL:
                return
            from backend.services.paper_pace_controller import paper_pace_controller
            if paper_pace_controller.gear != "blitz":
                paper_pace_controller.set_gear("blitz", reason="paper_fast_trial")
            logger.info(
                "[FullAuto] PAPER_FAST_TRIAL 已启用 pace=%s tick=%ss",
                paper_pace_controller.gear,
                paper_pace_controller.get_tick_seconds(),
            )
        except Exception as err:
            logger.debug("[FullAuto] paper fast trial pace skip: %s", err)

    @staticmethod
    def _resolve_unified_tick_interval(interval_seconds: int) -> int:
        """协调器 tick：轻量心跳。AI 分析间隔见 TIER_MID/LONG_AI_TICK_SEC。"""
        try:
            from backend.config.settings import (
                PAPER_FAST_TRIAL,
                TIER_COORDINATOR_TICK_SEC,
                TIER_TICK_SCHEDULER_ENABLED,
            )
            if TIER_TICK_SCHEDULER_ENABLED:
                return int(TIER_COORDINATOR_TICK_SEC or 45)
            if PAPER_FAST_TRIAL and (not interval_seconds or int(interval_seconds) >= 120):
                return 0
        except Exception:
            pass
        return int(interval_seconds or 0)

    def _register_health_check(self, session_id: str, interval_seconds: int):
        """注册统一循环任务（Phase2 orchestrator 接线）。"""
        from backend.services.full_auto.orchestrator import get_orchestrator
        get_orchestrator(self).register_session(session_id, interval_seconds)

    def _register_scalp_factor_loop(self, session_id: str, unified_tick_sec: int):
        from backend.services.full_auto.orchestrator import get_orchestrator
        get_orchestrator(self).register_scalp_loop(session_id, unified_tick_sec)

    def _register_midlong_agent_loop(self, session_id: str):
        from backend.services.full_auto.orchestrator import get_orchestrator
        get_orchestrator(self).register_midlong_loop(session_id)

    def _unregister_midlong_agent_loop(self, session_id: str):
        from backend.services.full_auto.orchestrator import get_orchestrator
        get_orchestrator(self)._unregister_midlong(session_id)

    def _unregister_scalp_factor_loop(self, session_id: str):
        from backend.services.full_auto.orchestrator import get_orchestrator
        get_orchestrator(self)._unregister_scalp(session_id)

    def _unregister_health_check(self, session_id: str):
        from backend.services.full_auto.orchestrator import get_orchestrator
        get_orchestrator(self).unregister_session(session_id)

    def _on_pace_gear_change(self, old_gear: str, new_gear: str) -> None:
        """PaperPace 档位变化回调：按各会话原 interval 口径重注册循环，
        使 tick_seconds 旋钮真正生效（interval 为 None/0 的会话才采用 pace tick）。"""
        try:
            intervals = getattr(self, "_session_intervals", {}) or {}
            session_ids = list(intervals.keys())
            for sid in session_ids:
                try:
                    self._register_health_check(sid, intervals.get(sid) or 0)
                except Exception as err:
                    logger.warning(f"[FullAuto] pace 重注册 {sid} 失败: {err}")
            if session_ids:
                logger.info(
                    f"[FullAuto] pace {old_gear}→{new_gear}，已重注册 "
                    f"{len(session_ids)} 个会话循环"
                )
        except Exception as e:
            logger.warning(f"[FullAuto] pace 档位变化处理失败: {e}")

    # ══════════════════════════════════════════════════
    #  统一循环 — 替代原双循环（健康检查 + 市场监测）
    # ══════════════════════════════════════════════════

    _FULL_CHECK_EVERY_N_TICKS = 3  # legacy 模式：每 3 tick 完整健康检查

    _unified_loop_running: Dict[str, bool] = {}  # session_id -> whether loop is currently executing
    _unified_loop_started: Dict[str, float] = {}  # session_id -> timestamp when loop started
    _unified_loop_process_locks: Dict[str, Any] = {}  # session_id -> fd held by current tick thread
    _scalp_loop_running: Dict[str, bool] = {}  # 短线因子独立循环（与 AI 主循环锁分离）
    _scalp_loop_started: Dict[str, float] = {}
    _midlong_loop_running: Dict[str, bool] = {}
    _midlong_loop_started: Dict[str, float] = {}
    _midlong_tick_count: Dict[str, int] = {}
    _last_hold_timeout_ai_review: Dict[str, float] = {}  # session_id -> ts
    _SCALP_LOOP_HANG_TIMEOUT_SECONDS = 180  # 7 币扫描通常 <2min；超时则强制放行下一轮
    _MIDLONG_LOOP_HANG_TIMEOUT_SECONDS = 300  # mid/long LLM 单轮上限 ~5min
    # ── unified loop hang 阈值：自适应动态计算（根据近 N 轮真实完成耗时） ──
    # 固定 360s(Paper) / 1000s(Live) 实测会误杀正常长轮（单轮可达 868-1003s）。
    # 现改为：阈值 = clamp(近N轮 P90 × 倍数, 下限, 上限)。全部可通过 env 覆盖。
    _LOOP_HANG_TIMEOUT_LIVE_FLOOR = float(os.getenv("FULLAUTO_HANG_TIMEOUT_LIVE_FLOOR_SEC", "1200"))
    _LOOP_HANG_TIMEOUT_PAPER_FLOOR = float(os.getenv("FULLAUTO_HANG_TIMEOUT_PAPER_FLOOR_SEC", "1200"))
    _LOOP_HANG_TIMEOUT_CEILING = float(os.getenv("FULLAUTO_HANG_TIMEOUT_MAX_SEC", "3600"))
    _LOOP_HANG_TIMEOUT_SAMPLES = int(os.getenv("FULLAUTO_HANG_TIMEOUT_SAMPLES", "12"))
    _LOOP_HANG_TIMEOUT_MULTIPLIER = float(os.getenv("FULLAUTO_HANG_TIMEOUT_MULTIPLIER", "2.5"))
    _LOOP_HANG_TIMEOUT_WARMUP = 3  # 样本不足此数时用下限兜底（冷启动保护）
    # 每 session 最近 N 轮完成耗时（秒），deque: session_id -> [float]
    _loop_tick_durations: Dict[str, Any] = {}

    def _loop_hang_floor_seconds(self) -> float:
        """冷启动/兜底下限：Paper 用 600s，Live 用 900s。"""
        try:
            from backend.config.settings import PAPER_FAST_TRIAL
            if PAPER_FAST_TRIAL:
                return self._LOOP_HANG_TIMEOUT_PAPER_FLOOR
        except Exception:
            pass
        return self._LOOP_HANG_TIMEOUT_LIVE_FLOOR

    def _loop_hang_timeout_seconds(self, session_id: str = "") -> float:
        """自适应 hang 阈值：近 N 轮 P90 × 倍数，clamp 到 [下限, 上限]。

        冷启动（样本 < 3）用下限兜底；样本充足后自动跟随真实负载放宽/收紧，
        避免固定阈值在 LLM 变慢或币种增多时误杀正常长轮。
        """
        floor = self._loop_hang_floor_seconds()
        ceiling = self._LOOP_HANG_TIMEOUT_CEILING
        from collections import deque as _deque
        dq = self._loop_tick_durations.get(session_id)
        if not dq or len(dq) < self._LOOP_HANG_TIMEOUT_WARMUP:
            return floor
        samples = sorted(dq)
        idx = min(len(samples) - 1, int(len(samples) * 0.9))
        p90 = samples[idx]
        adaptive = p90 * self._LOOP_HANG_TIMEOUT_MULTIPLIER
        return max(floor, min(adaptive, ceiling))

    def _record_tick_duration(self, session_id: str, elapsed: float) -> None:
        """记录一轮完成耗时，喂给自适应阈值统计器。在 trading_cycle 完成时调用。"""
        from collections import deque as _deque
        dq = self._loop_tick_durations.get(session_id)
        if dq is None:
            dq = _deque(maxlen=self._LOOP_HANG_TIMEOUT_SAMPLES)
            self._loop_tick_durations[session_id] = dq
        dq.append(elapsed)

    def _force_release_unified_loop_state(self, session_id: str, *, reason: str = "") -> None:
        """hang/超时后清理 running 标记 + 进程锁，避免会话永久卡死。

        [2026-07-16 修复 DetachedInstanceError 根因] 不再调用 _cleanup_leaked_db_sessions
        强关业务线程正在使用的 db session。原实现会 close 掉 trading_cycle_loop 注册进
        _active_db_sessions 的同一个 db 对象，而业务线程无协作取消机制、仍继续访问该
        已关闭的 ORM 对象，导致 DetachedInstanceError，使该轮 AI 决策（master_decision）
        写库失败。watchdog 的核心职责是"释放锁和 running 标记让下一轮 tick 能进"，
        业务线程的 db 生命周期由它自己在 finally 里管理（trading_cycle_loop.py:307-308）。
        """
        self._unified_loop_running[session_id] = False
        self._unified_loop_started.pop(session_id, None)
        leaked_lock = self._unified_loop_process_locks.pop(session_id, None)
        if leaked_lock is not None:
            try:
                self._release_unified_loop_process_lock(leaked_lock)
            except Exception:
                pass
        if reason:
            logger.error("[FullAuto] %s session=%s", reason, session_id)

    def _try_acquire_unified_loop_process_lock(self, session_id: str):
        """Acquire a non-blocking per-session process lock for full-auto ticks.

        跨平台：Linux/Mac 用 fcntl.flock，Windows 用 msvcrt.locking。
        （2026-06-11 修复：Windows 无 fcntl 模块，旧代码 ImportError 后误判
        "其他进程在执行"，导致每个 tick 都被跳过、AI 策略完全停摆。）
        """
        lock_fd = None
        try:
            lock_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "locks"))
            os.makedirs(lock_dir, exist_ok=True)
            safe_sid = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in session_id)
            lock_path = os.path.join(lock_dir, f"fullauto_unified_{safe_sid}.lock")
            lock_fd = open(lock_path, "w")
            try:
                import fcntl
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except ImportError:
                import msvcrt
                msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
            lock_fd.write(f"{os.getpid()} {time.time():.0f}\n")
            lock_fd.flush()
            return lock_fd
        except (BlockingIOError, PermissionError, OSError):
            # 锁确实被其他进程持有 → 本 tick 让位
            try:
                if lock_fd is not None:
                    lock_fd.close()
            except Exception:
                pass
            return None
        except Exception as lock_err:
            # 平台锁机制不可用：退化为进程内防重入（_unified_loop_running 已兜底），
            # 绝不能因此跳过 tick
            logger.warning(
                f"[FullAuto] 跨进程循环锁不可用，退化为进程内锁 {session_id}: {lock_err}")
            return lock_fd if lock_fd is not None else open(os.devnull, "w")

    @staticmethod
    def _release_unified_loop_process_lock(lock_fd) -> None:
        if lock_fd is None:
            return
        try:
            import fcntl
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            lock_fd.close()
        except Exception:
            pass

    def _run_unified_loop_safe(self, session_id: str):
        """Safe wrapper: prevents rapid overlapping, but allows new tick if previous hung.

        [fix] 不再 join() tick 线程，避免阻塞 APScheduler 工作线程。
        改为 fire-and-forget + hang 检测守护线程，确保 APScheduler 线程立即释放。
        """
        try:
            from backend.services.scheduler import task_scheduler
            if not task_scheduler.is_running():
                logger.debug(f"[FullAuto] scheduler 已停止，跳过 tick {session_id}")
                return
        except Exception:
            pass

        prev_started = self._unified_loop_started.get(session_id, 0)
        hang_timeout = self._loop_hang_timeout_seconds(session_id)
        if self._unified_loop_running.get(session_id):
            elapsed = time.time() - prev_started
            if elapsed < hang_timeout:
                logger.info(
                    f"[FullAuto] 上轮循环仍在执行({elapsed:.0f}s，含 LLM，hang阈值={hang_timeout:.0f}s)，"
                    f"跳过本次协调 tick {session_id}"
                )
                return
            else:
                self._force_release_unified_loop_state(
                    session_id,
                    reason=f"上轮循环疑似 hang({elapsed:.0f}s)，强制允许新 tick",
                )

        if session_id in self._unified_loop_process_locks:
            logger.warning(f"[FullAuto] 上轮循环锁仍未释放，跳过本次调度 {session_id}")
            return

        process_lock = self._try_acquire_unified_loop_process_lock(session_id)
        if process_lock is None:
            logger.info(f"[FullAuto] 其他进程正在执行统一循环，跳过本次调度 {session_id}")
            return

        self._unified_loop_running[session_id] = True
        self._unified_loop_started[session_id] = time.time()
        self._unified_loop_process_locks[session_id] = process_lock

        # 启动独立线程执行 tick，fire-and-forget（不阻塞 APScheduler 线程）
        loop_thread = threading.Thread(
            target=self._run_unified_loop_wrapped,
            args=(session_id, process_lock),
            daemon=True,
            name=f"fullauto-loop-{session_id[:8]}",
        )
        loop_thread.start()

        # [fix] 启动超时守护线程：tick 超过 hang 上限仍未结束则强制释放锁
        self._start_hang_watchdog(session_id, loop_thread, hang_timeout)

    def _cleanup_leaked_db_sessions(self, session_id: str):
        """[deprecated 2026-07-16] 仅清字典里的悬空 key，绝不 close db 对象。

        原实现会 _leaked_db.close()，但 detach 后业务线程仍持有该 db 继续跑，
        close 会导致 DetachedInstanceError。现在改为只 pop 字典引用（防止悬空 key
        堆积），db 对象的真正关闭交由业务线程自己的 finally（trading_cycle_loop:307）。
        此方法目前无调用方，保留以备未来字典清理需要。
        """
        leaked_keys = [k for k in self._active_db_sessions if k.startswith(session_id)]
        for _lk in leaked_keys:
            self._active_db_sessions.pop(_lk, None)
            logger.debug(f"[FullAuto][Phase0] 清理悬空 db session 引用(未关闭对象): {_lk}")

    def _start_hang_watchdog(self, session_id: str, loop_thread: threading.Thread, hang_timeout: float):
        """超时守护线程：检测 tick hang 并清理泄漏资源 + 进程锁。"""
        def _watchdog():
            loop_thread.join(timeout=hang_timeout)
            if loop_thread.is_alive():
                self._force_release_unified_loop_state(
                    session_id,
                    reason=f"循环超时({hang_timeout:.0f}s)，线程 detached",
                )

        watchdog_thread = threading.Thread(
            target=_watchdog,
            daemon=True,
            name=f"fullauto-watchdog-{session_id[:8]}",
        )
        watchdog_thread.start()

    def _run_unified_loop_wrapped(self, session_id: str, process_lock=None):
        """实际执行 _run_unified_loop 的内部方法，用于 threading.Thread 包装。"""
        # 为整个 tick 绑定 trace_id（新线程不继承调度器 ContextVar）
        # 日志格式: [tr=fullauto-abc123] ... 便于跨子系统排查
        from backend.utils.trace_context import bind_trace, generate_trace_id
        _tick_trace = generate_trace_id(f"fullauto-{session_id[:6]}")
        try:
            with bind_trace(_tick_trace):
                self._run_unified_loop(session_id)
        except Exception as e:
            logger.error(f"[FullAuto] 统一循环异常 {session_id}: {e}", exc_info=True)
        finally:
            self._unified_loop_running[session_id] = False
            self._unified_loop_started.pop(session_id, None)
            self._unified_loop_process_locks.pop(session_id, None)
            self._release_unified_loop_process_lock(process_lock)
            # [fix] P2-4: 每次 tick 完成后主动 GC + 内存日志（每 10 个 tick 输出一次）
            try:
                import gc as _gc
                _gc.collect()
                _cnt = self._unified_tick_count.get(session_id, 0)
                if _cnt % 10 == 0:
                    try:
                        import psutil
                        _proc = psutil.Process()
                        _mem_mb = _proc.memory_info().rss / 1024 / 1024
                        logger.debug(f"[FullAuto] Memory: {_mem_mb:.1f}MB (tick#{_cnt} session={session_id[:8]})")
                    except Exception:
                        pass
            except Exception:
                pass

    def _run_scalp_loop_safe(self, session_id: str):
        """短线因子独立调度入口：与 AI 主循环锁分离，AI 慢时不阻塞因子扫描。"""
        try:
            from backend.services.scheduler import task_scheduler
            if not task_scheduler.is_running():
                return
        except Exception:
            pass

        if session_id not in self._running_sessions:
            return

        prev_started = self._scalp_loop_started.get(session_id, 0)
        if self._scalp_loop_running.get(session_id):
            elapsed = time.time() - prev_started
            if elapsed < self._SCALP_LOOP_HANG_TIMEOUT_SECONDS:
                logger.debug(
                    f"[ScalpRouter独立] 上轮扫描仍在执行({elapsed:.0f}s)，跳过 {session_id}"
                )
                return
            logger.warning(
                f"[ScalpRouter独立] 上轮扫描疑似 hang({elapsed:.0f}s)，强制新扫描 {session_id}"
            )
            self._scalp_loop_running[session_id] = False

        session_status = self._get_session_status_fast(session_id)
        if session_status == "paused":
            return

        self._scalp_loop_running[session_id] = True
        self._scalp_loop_started[session_id] = time.time()

        loop_thread = threading.Thread(
            target=self._run_scalp_loop_wrapped,
            args=(session_id,),
            daemon=True,
            name=f"fullauto-scalp-{session_id[:8]}",
        )
        loop_thread.start()

    def _run_scalp_loop_wrapped(self, session_id: str):
        """执行一轮短线因子扫描（独立线程）。"""
        from backend.utils.trace_context import bind_trace, generate_trace_id

        tick = self._scalp_tick_count.get(session_id, 0) + 1
        self._scalp_tick_count[session_id] = tick
        self._scalp_traded_this_tick = set()
        _trace = generate_trace_id(f"scalp-{session_id[:6]}")
        try:
            with bind_trace(_trace):
                self._run_scalp_independent(session_id, tick)
                # 周期性 IC 评估 → 更新 factor_runtime_weights.json → pipeline 自动降/升权
                try:
                    from backend.config.settings import SCALP_FACTOR_IC_EVAL_EVERY_N_TICKS
                    _ic_n = max(1, int(SCALP_FACTOR_IC_EVAL_EVERY_N_TICKS or 80))
                    if tick % _ic_n == 0:
                        import threading

                        def _scalp_ic_eval():
                            try:
                                from backend.database.connection import SessionLocal
                                from backend.services.factor_ic_evaluator import run_factor_ic_evaluation
                                _ic_db = SessionLocal()
                                try:
                                    stats = run_factor_ic_evaluation(_ic_db, lookback_days=14)
                                    if stats:
                                        logger.info(
                                            f"[ScalpRouter独立] IC权重回写: {len(stats)} 个因子已更新"
                                        )
                                finally:
                                    _ic_db.close()
                            except Exception as _ic_err:
                                logger.debug(f"[ScalpRouter独立] IC评估跳过: {_ic_err}")

                        threading.Thread(
                            target=_scalp_ic_eval, daemon=True, name="scalp-ic-eval",
                        ).start()
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"[ScalpRouter独立] 独立调度异常 {session_id}: {e}", exc_info=True)
        finally:
            self._scalp_loop_running[session_id] = False
            self._scalp_loop_started.pop(session_id, None)

    def _run_midlong_loop_safe(self, session_id: str):
        """中线/长线 Agent 独立循环入口（不阻塞 QAA 主循环）。"""
        # 2026-07-20：诊断 midlong 立即返回的根因（曾因某个早期 return 静默跳过整个 tick）
        try:
            from backend.services.scheduler import task_scheduler
            if not task_scheduler.is_running():
                logger.warning("[MidLongAgent独立] 跳过: task_scheduler 未运行 %s", session_id)
                return
        except Exception as _e:
            logger.warning("[MidLongAgent独立] 跳过: scheduler 检查异常 %s: %s", session_id, _e)
            return
        if session_id not in self._running_sessions:
            logger.warning("[MidLongAgent独立] 跳过: session 不在 _running_sessions %s", session_id)
            return
        prev_started = self._midlong_loop_started.get(session_id, 0)
        if self._midlong_loop_running.get(session_id):
            elapsed = time.time() - prev_started
            if elapsed < self._MIDLONG_LOOP_HANG_TIMEOUT_SECONDS:
                logger.debug(
                    f"[MidLongAgent独立] 上轮仍在执行({elapsed:.0f}s)，跳过 {session_id}"
                )
                return
            logger.warning(
                f"[MidLongAgent独立] 上轮疑似 hang({elapsed:.0f}s)，强制新扫描 {session_id}"
            )
            self._midlong_loop_running[session_id] = False
        _st = self._get_session_status_fast(session_id)
        if _st == "paused":
            logger.warning("[MidLongAgent独立] 跳过: session 状态=paused %s", session_id)
            return
        logger.info("[MidLongAgent独立] 启动 tick %s status=%s", session_id, _st)
        self._midlong_loop_running[session_id] = True
        self._midlong_loop_started[session_id] = time.time()
        threading.Thread(
            target=self._run_midlong_loop_wrapped,
            args=(session_id,),
            daemon=True,
            name=f"fullauto-midlong-{session_id[:8]}",
        ).start()

    def _run_midlong_loop_wrapped(self, session_id: str):
        from backend.utils.trace_context import bind_trace, generate_trace_id
        tick = self._midlong_tick_count.get(session_id, 0) + 1
        self._midlong_tick_count[session_id] = tick
        _trace = generate_trace_id(f"midlong-{session_id[:6]}")
        try:
            with bind_trace(_trace):
                self._run_midlong_independent(session_id, tick)
        except Exception as e:
            logger.warning(f"[MidLongAgent独立] 调度异常 {session_id}: {e}", exc_info=True)
            # DB 连接被掐断后 Session 处于不可用状态，主动 rollback+dispose
            # 防止后续 tick 继承脏连接导致连锁失败
            try:
                from backend.database.connection import engine
                engine.dispose()
                logger.info("[MidLongAgent独立] 连接池已 dispose（恢复脏连接）%s", session_id)
            except Exception:
                pass
        finally:
            self._midlong_loop_running[session_id] = False
            self._midlong_loop_started.pop(session_id, None)

    def _run_midlong_independent(self, session_id: str, tick: int) -> None:
        """#8 thin shim → full_auto.loops.midlong_loop"""
        from backend.services.full_auto.loops.midlong_loop import run_midlong_independent
        return run_midlong_independent(self, session_id, tick)

    def _run_midlong_active_exit(self, db, session, market_summary: dict) -> None:
        """遍历中长线 open 持仓：bias 强反向 / 无进展超时 → 统一离场状态机。"""
        from backend.config.settings import MIDLONG_ACTIVE_EXIT_ENABLED
        if not MIDLONG_ACTIVE_EXIT_ENABLED:
            return
        from backend.services.exit.exit_types import ExitAction, PositionContext
        from backend.services.exit.exit_types import ExitRequest as ExitReq
        from backend.services.exit.unified_exit_state_machine import exit_state_machine
        from backend.services.midlong_exit_guard import evaluate_midlong_exit
        from backend.services.mlto.midlong_portfolio_risk import evaluate_no_progress_exit
        from backend.services.paper_trading_engine import paper_engine

        acct_id = getattr(session, "paper_account_id", None) or getattr(session, "account_id", None)
        if not acct_id:
            return
        positions = paper_engine.get_positions(db, acct_id, status="open") or []
        for pos in positions:
            nature = str(pos.get("trade_nature") or "").lower()
            tier = str(pos.get("timeframe_tier") or "").lower()
            if nature not in ("swing", "trend_follow", "position") and tier not in ("mid", "long"):
                continue
            sym = str(pos.get("symbol") or "").upper()
            md = market_summary.get(sym) if isinstance(market_summary, dict) else None

            # [2026-08-16 long_trend_v2] 长线仓改由 V2 每日管理器（Chandelier/结构退出/
            # 新高金字塔）接管，跳过本函数的 bias 反转 / no_progress 退出。
            try:
                from backend.services.long_trend_v2 import long_v2_enabled, manage_long_position
                _v2_active = long_v2_enabled()
            except Exception:
                _v2_active = False
            if _v2_active and tier == "long":
                try:
                    _v2d = manage_long_position(db, account_id=acct_id, position=pos)
                    if _v2d.get("action") == "close":
                        paper_engine.close_position(
                            db, acct_id, sym, pos.get("side"),
                            reason=("long_trend_v2:" + str(_v2d.get("reason") or ""))[:120],
                            strategy_id=pos.get("strategy_id"),
                        )
                        logger.info("[MidLongExit][V2] 结构/止损离场 %s: %s", sym, _v2d.get("reason"))
                    elif _v2d.get("action") == "tighten_sl" and _v2d.get("new_sl"):
                        paper_engine.update_position_tp_sl(
                            db, int(pos.get("id") or 0), sl_price=float(_v2d["new_sl"]),
                        )
                        logger.info("[MidLongExit][V2] 收紧止损 %s SL→%s", sym, _v2d["new_sl"])
                    elif _v2d.get("action") == "add":
                        _pyr_ratio = float(_v2d.get("ratio") or 0.25)
                        _pyr_qty = float(pos.get("size") or 0) * _pyr_ratio
                        if _pyr_qty > 0:
                            paper_engine.place_order(
                                db, acct_id, sym, "buy",
                                quantity=_pyr_qty,
                                leverage=float(pos.get("leverage", 10) or 10),
                                sl_price=float(pos.get("sl_price") or 0) or None,
                                strategy_id=pos.get("strategy_id"),
                                timeframe_tier="long",
                                trade_nature=pos.get("trade_nature"),
                                add_type="pyramid",
                            )
                            logger.info("[MidLongExit][V2] 金字塔加仓 %s qty=%s: %s", sym, _pyr_qty, _v2d.get("reason"))
                            # [A2 同核] 加仓成功后批次计数 +1（exit_state_json.pyramid_batch，供下次决策读）
                            try:
                                import json as _json_p
                                from backend.database.models import PaperPosition as _PP
                                _row = db.query(_PP).filter(_PP.id == int(pos.get("id") or 0)).first()
                                if _row is not None:
                                    _st = _row.exit_state_json or "{}"
                                    try:
                                        _d = _json_p.loads(_st) if isinstance(_st, str) else {}
                                    except Exception:
                                        _d = {}
                                    if not isinstance(_d, dict):
                                        _d = {}
                                    _d["pyramid_batch"] = int(_d.get("pyramid_batch") or 0) + 1
                                    _row.exit_state_json = _json_p.dumps(_d, ensure_ascii=False)
                                    db.commit()
                            except Exception as _be:
                                logger.debug("[MidLongExit][V2] pyramid_batch 写入失败: %s", _be)
                    elif _v2d.get("action") == "reduce":
                        # [A3 兜底] 部分减仓（极端回撤减半；A4 结构目标减仓复用此分支）
                        _red_ratio = float(_v2d.get("ratio") or 0.5)
                        _red_qty = float(pos.get("size") or 0) * _red_ratio
                        if _red_qty > 0:
                            paper_engine.close_position(
                                db, acct_id, sym, pos.get("side"),
                                reason=("long_trend_v2:" + str(_v2d.get("reason") or ""))[:120],
                                quantity=_red_qty,
                                strategy_id=pos.get("strategy_id"),
                            )
                            logger.info("[MidLongExit][V2] 部分减仓 %s qty=%s: %s", sym, _red_qty, _v2d.get("reason"))
                except Exception as _v2e:
                    logger.warning("[MidLongExit][V2] 管理异常 %s: %s", sym, _v2e)
                continue

            decision = evaluate_midlong_exit(pos, md)
            exit_source = "bias_reversal"
            if decision.action != "close":
                npd = evaluate_no_progress_exit(pos)
                if npd.action == "close":
                    decision = type(decision)(
                        action="close",
                        reason=npd.reason,
                        bias=getattr(decision, "bias", "") or "",
                        confidence=getattr(decision, "confidence", 0.0) or 0.0,
                    )
                    exit_source = "no_progress"
            if decision.action == "close":
                # 统一走离场状态机（不再直调 close_position）
                pos_id = int(pos.get("id") or 0)
                entry = float(pos.get("entry_price") or 0)
                cur_price = float(pos.get("mark_price") or (md or {}).get("price") or entry or 0)
                pnl_pct = float(pos.get("unrealized_pnl_pct") or pos.get("pnl_pct") or 0)
                _qty = float(pos.get("size") or pos.get("quantity") or 0)
                try:
                    # 无进展 / bias 强反向：全平 + CRITICAL。
                    # midlong_exit_guard 已按 MIDLONG_EXIT_MIN_HOLD / TIER_LONG_MIN_HOLD
                    # 过滤过短持仓；再用 HIGH+半仓会被 ExitSM 的 min_hold / 微利减仓闸吞掉，
                    # 导致论点已破坏却只收紧止损或 hold（P1-1）。
                    _qty_ratio = 1.0
                    _urgency = "CRITICAL"
                    _proposed = ExitAction.CLOSE.value
                    req = ExitReq(
                        position_id=pos_id, symbol=sym, tier=tier or "long",
                        source=exit_source,
                        proposed_action=_proposed,
                        proposed_qty_ratio=_qty_ratio, urgency=_urgency,
                        reason_detail=decision.reason or f"midlong {exit_source}",
                    )
                    ctx = PositionContext(
                        position_id=pos_id, symbol=sym, tier=tier or "long",
                        side=pos.get("side", "long"), entry_price=entry, current_price=cur_price,
                        quantity=_qty, leverage=float(pos.get("leverage") or 1),
                        unrealized_pnl_pct=pnl_pct, peak_pnl_pct=max(float(pos.get("peak_pnl_pct") or 0), 0),
                        hold_seconds=int(pos.get("hold_seconds") or (float(pos.get("hold_age_hours") or 0) * 3600) or 7200),
                    )
                    exit_decision = exit_state_machine.submit(req, ctx)
                    if exit_decision.action in ("close", "reduce") and exit_decision.qty_ratio > 0:
                        close_qty = None if exit_decision.qty_ratio >= 0.95 else exit_decision.qty_ratio * _qty
                        paper_engine.close_position(
                            db, acct_id, sym, pos.get("side"),
                            reason=(exit_decision.reason or "midlong_exit")[:120],
                            strategy_id=pos.get("strategy_id"),
                            quantity=close_qty,
                        )
                        logger.info(
                            "[MidLongExit] 状态机仲裁 %s src=%s: %s qty_ratio=%.2f",
                            sym, exit_source, exit_decision.action, exit_decision.qty_ratio,
                        )
                    elif exit_decision.action == "tighten_sl" and exit_decision.new_sl_price:
                        paper_engine.update_position_tp_sl(
                            db, pos_id, sl_price=exit_decision.new_sl_price,
                        )
                        logger.info("[MidLongExit] 收紧止损 %s: SL→%s", sym, exit_decision.new_sl_price)
                except Exception as _c_err:
                    logger.warning("[MidLongExit] 状态机异常 %s: %s", sym, _c_err)

    _DEFENSIVE_FULL_CHECK_EVERY_N_TICKS = 4  # 防守模式每 4 个 tick (~360s) 做一次完整检查（整改项2: 降频减少过度交易）

    # ── 套利域 Tick ──────────────────────────────────────────────────────
    def _run_arbitrage_tick(self, session_id: str) -> None:
        """#8 thin shim → full_auto.loops.arbitrage_loop"""
        from backend.services.full_auto.loops.arbitrage_loop import run_arbitrage_tick
        return run_arbitrage_tick(self, session_id)

    def _record_arb_tick_error(self, domain: str, exc: Exception) -> None:
        """#8 thin shim → full_auto.loops.arbitrage_loop"""
        from backend.services.full_auto.loops.arbitrage_loop import record_arb_tick_error
        return record_arb_tick_error(self, domain, exc)

    def get_arb_tick_error_stats(self) -> Dict[str, Dict[str, Any]]:
        """套利/返利 tick 异常统计（健康检查用）"""
        return dict(getattr(self, "_arb_tick_errors", {}) or {})

    def _get_session_fast(self, session_id: str):
        """快速获取 session 对象（从内存缓存）"""
        return self._running_sessions.get(session_id, {}).get("session_obj")

    # ── 积分/返利套利域 Tick ──────────────────────────────────────────
    def _run_rebate_arb_tick(self, session_id: str) -> None:
        """#8 thin shim → full_auto.loops.arbitrage_loop"""
        from backend.services.full_auto.loops.arbitrage_loop import run_rebate_arb_tick
        return run_rebate_arb_tick(self, session_id)

    def _check_liq_magnet_reversal_exit(
        self, *, db, account_id: int, symbol: str, router_direction: str,
    ) -> None:
        """已有反向 scalp/intraday 仓位遇到高强度清算磁吸反转信号 → 主动平仓。

        触发条件（三者都满足）：
        1. 该 symbol 存在状态为 open 的 scalp/intraday 仓位；
        2. 仓位方向与 ScalpFactorRouter 本次给出的方向（router_direction）相反；
        3. 链上原始清算簇数据显示 severity=high 且磁吸方向与 router_direction
           一致（即与仓位方向相反）——这是比因子打分更硬的事实证据。

        命中即调用 paper_engine.close_position 全平，reason 打
        "liq_magnet_reversal"，master_close_guard 已将其列入硬事实白名单，
        不会被当作低质量的 master_running_reduce 计入胜率统计。

        只做「已有反向仓位」的退出判断，不影响新开仓路径（新开仓的清算磁吸
        拦截逻辑在 scalp_factor_router / short_tier_entry_gate 里，本函数不重复）。
        """
        from backend.database.models import PaperPosition

        opp_side = "short" if router_direction == "long" else "long"
        opp_pos = db.query(PaperPosition).filter(
            PaperPosition.account_id == account_id,
            PaperPosition.symbol == symbol,
            PaperPosition.side == opp_side,
            PaperPosition.status == "open",
            PaperPosition.trade_nature.in_(("scalp", "intraday")),
        ).first()
        if not opp_pos:
            return

        from backend.services.crypto_alpha_signals import crypto_alpha
        lm = crypto_alpha.liquidation_magnet(symbol)
        if not (lm.available and lm.severity == "high" and lm.direction == router_direction):
            return

        from backend.services.exit.exit_types import ExitAction, ExitUrgency, PositionContext
        from backend.services.exit.exit_types import ExitRequest as ExitReq
        from backend.services.exit.unified_exit_state_machine import exit_state_machine
        from backend.services.paper_trading_engine import paper_engine

        # 统一走离场状态机（liq_magnet 作为 HIGH 紧急度走硬事实层直通）
        pos_id = int(getattr(opp_pos, "id", 0) or 0)
        entry = float(getattr(opp_pos, "entry_price", 0) or 0)
        cur_price = float(getattr(opp_pos, "mark_price", 0) or entry or 0)
        pnl_pct = float(getattr(opp_pos, "unrealized_pnl_pct", 0) or 0)
        try:
            req = ExitReq(
                position_id=pos_id, symbol=symbol, tier="short",
                source="liq_magnet", proposed_action=ExitAction.CLOSE.value,
                proposed_qty_ratio=1.0, urgency=ExitUrgency.CRITICAL.value,
                reason_detail=f"清算磁吸反转 severity=high dir={router_direction}",
            )
            ctx = PositionContext(
                position_id=pos_id, symbol=symbol, tier="short",
                side=opp_side, entry_price=entry, current_price=cur_price,
                quantity=float(getattr(opp_pos, "quantity", 0) or 0),
                leverage=float(getattr(opp_pos, "leverage", 1) or 1),
                unrealized_pnl_pct=pnl_pct, peak_pnl_pct=max(pnl_pct, 0),
                hold_seconds=int(getattr(opp_pos, "hold_seconds", 7200) or 7200),
            )
            exit_decision = exit_state_machine.submit(req, ctx)
            if exit_decision.action in ("close", "reduce") and exit_decision.qty_ratio > 0:
                close_qty = None if exit_decision.qty_ratio >= 0.95 else exit_decision.qty_ratio * float(getattr(opp_pos, "quantity", 0) or 0)
                result = paper_engine.close_position(
                    db, account_id, symbol, opp_side,
                    reason=exit_decision.reason[:120] if exit_decision.reason else "liq_magnet_reversal",
                    strategy_id=getattr(opp_pos, "strategy_id", None),
                    quantity=close_qty,
                )
                db.commit()
                if result:
                    logger.warning(
                        f"[ScalpRouter][清算磁吸反转] {symbol} {opp_side}仓位经状态机仲裁 "
                        f"action={exit_decision.action} ({lm.note})"
                    )
        except Exception as _exit_err:
            logger.warning(f"[ScalpRouter] liq_magnet 状态机异常 {symbol}: {_exit_err}")

    def _warmup_scalp_factor_engine(self, symbol: str = "BTC") -> None:
        """后台预热因子引擎（一次性，进程级）。

        首次 compute_all_factors 会触发大量 numba JIT 编译 / lazy import，冷启动实测
        首个币耗时可达 ~96s（后续换蜡烛重算才回落到 ~10s）。本方法在短线调度注册时
        用后台守护线程先用真实 5m K线跑一遍，把这笔一次性开销抢在第一次实盘扫描前
        消化掉。只触发计算、不产生任何信号/交易，对策略行为零影响；失败静默降级。
        """
        if self._scalp_factor_warmup_started:
            return
        self._scalp_factor_warmup_started = True

        def _bg():
            try:
                import pandas as _pd

                from backend.services.factor_engine.base_factors import factor_engine
                from backend.services.kline_data_service import kline_service
                _raw = kline_service.get_klines_from_db(
                    symbol.upper(), "5m", 100,
                )
                if _raw and len(_raw) > 20:
                    _t = time.perf_counter()
                    factor_engine.compute_all_factors(
                        _pd.DataFrame(_raw),
                        {"symbol": symbol.upper(), "timeframe": "5m"},
                    )
                    logger.info(
                        "[ScalpWarmup] 因子引擎预热完成(%s) 耗时=%.1fs，冷启动开销已在后台消化",
                        symbol.upper(), time.perf_counter() - _t,
                    )
            except Exception as _e:
                logger.debug("[ScalpWarmup] 因子引擎预热跳过: %s", _e)

        threading.Thread(
            target=_bg, daemon=True, name="scalp-factor-warmup",
        ).start()

    def _warmup_analyst_reports(self, symbols: list) -> None:
        """后台预热 KlineAnalyst LLM 缓存。

        首次 MasterController.synthesize 依赖各分析师报告（KlineAnalyst 每个 symbol
        ~19s LLM）。首次冷启动时这些报告需要串行/并行生成，导致 report_text 构建耗
        36s+。本方法在会话恢复时用后台守护线程提前触发 KlineAnalyst 分析，让首次
        synthesize 能命中 _llm_cache（0.2s 而非 36s）。
        """
        if not symbols:
            return

        def _bg():
            try:
                from backend.services.trading_analysts import KlineAnalyst
                _t = time.perf_counter()
                _analyst = KlineAnalyst()
                _syms = [s.upper() for s in symbols[:3]]
                try:
                    _analyst.analyze(_syms)
                except Exception:
                    pass
                logger.info(
                    "[AnalystWarmup] KlineAnalyst 缓存预热完成 symbols=%s 耗时=%.1fs",
                    _syms, time.perf_counter() - _t,
                )
            except Exception as _e:
                logger.debug("[AnalystWarmup] 分析师报告预热跳过: %s", _e)

        threading.Thread(
            target=_bg, daemon=True, name="analyst-report-warmup",
        ).start()

    def _run_scalp_independent(self, session_id: str, tick: int):
        """#8 thin shim → full_auto.loops.scalp_loop"""
        from backend.services.full_auto.loops.scalp_loop import run_scalp_independent
        return run_scalp_independent(self, session_id, tick)

    def _run_unified_loop(self, session_id: str):
        """#8 thin shim → full_auto.loops.coordinator_loop"""
        from backend.services.full_auto.loops.coordinator_loop import run_unified_loop
        return run_unified_loop(self, session_id)

    def _apply_training_phase_tick_constraints(self, session) -> None:
        """#8 thin shim → full_auto.loops.trading_cycle_loop"""
        from backend.services.full_auto.loops.trading_cycle_loop import _apply_training_phase_tick_constraints
        return _apply_training_phase_tick_constraints(self, session)

    def _run_trading_cycle(self, session_id: str, ai_tiers: Optional[List[str]] = None) -> None:
        """#8 thin shim → full_auto.loops.trading_cycle_loop"""
        from backend.services.full_auto.loops.trading_cycle_loop import run_trading_cycle
        return run_trading_cycle(self, session_id, ai_tiers)

    def _run_learning_integration(
        self, session_id: str, tick: int = 0, *, is_maintenance: bool = False
    ) -> None:
        """#8 thin shim → full_auto.loops.learning_loop"""
        from backend.services.full_auto.loops.learning_loop import run_learning_integration
        return run_learning_integration(self, session_id, tick, is_maintenance=is_maintenance)

    def _run_mlto_learning_tick(self, session_id: str) -> None:
        """#8 thin shim → full_auto.loops.learning_loop"""
        from backend.services.full_auto.loops.learning_loop import run_mlto_learning_tick
        return run_mlto_learning_tick(self, session_id)

    def _run_maintenance_cycle(self, session_id: str) -> None:
        """#8 thin shim → full_auto.loops.maintenance_loop"""
        from backend.services.full_auto.loops.maintenance_loop import run_maintenance_cycle
        return run_maintenance_cycle(self, session_id)

    _mlto_learning_last: Dict[str, float] = {}
    _session_status_cache: Dict[str, Any] = {}
    _SESSION_STATUS_CACHE_TTL = 30  # 30 秒缓存

    def _get_session_status_fast(self, session_id: str) -> str:
        """快速获取会话状态（带短 TTL 缓存）"""
        now = time.time()
        cached = self._session_status_cache.get(session_id)
        if cached and now - cached["ts"] < self._SESSION_STATUS_CACHE_TTL:
            return cached["status"]

        from backend.database.connection import SessionLocal
        from backend.database.models import FullAutoSession
        db = SessionLocal()
        try:
            # [2026-08-04 修复] 会话状态权威查询穿透 RLS：本方法可能被后台线程
            # （APScheduler midlong tick / QAA v3）调用，无 HTTP 租户上下文时
            # RLS fail-closed 隐藏行 → 误报 "stopped" → 独立 tick 可能被提前跳过。
            # 自建连接设 admin GUC（不动 ContextVar），查询仍按 session_id 严格过滤。
            try:
                db.connection().exec_driver_sql("SET app.is_admin = 'on'")
            except Exception:
                pass
            s = db.query(FullAutoSession.status).filter(
                FullAutoSession.session_id == session_id
            ).first()
            status = s.status if s else "stopped"
            self._session_status_cache[session_id] = {"status": status, "ts": now}
            return status
        except Exception:
            return "unknown"
        finally:
            db.close()

    def _record_strategy_pause(self, strategy_id, reason: str, *, by: str = "quick_eval") -> None:
        sid = str(strategy_id)
        self._strat_pause_meta[sid] = {
            "reason": reason,
            "since": time.time(),
            "by": by,
        }

    def _clear_strategy_pause_meta(self, strategy_id) -> None:
        sid = str(strategy_id)
        self._strat_pause_meta.pop(sid, None)
        try:
            self._strat_pause_meta.pop(int(strategy_id), None)
        except (ValueError, TypeError):
            pass

    def _can_resume_strategy(self, strategy_id, *, is_ranging_pause: bool) -> bool:
        sid = str(strategy_id)
        meta = self._strat_pause_meta.get(sid)
        if not meta:
            return True
        reason = str(meta.get("reason") or "")
        if "风险冻结" in reason or "崩盘" in reason or "死锁" in reason:
            return True
        if is_ranging_pause or "震荡市" in reason:
            elapsed = time.time() - float(meta.get("since", 0) or 0)
            return elapsed >= self._RANGING_MIN_PAUSE_SEC
        return True

    def _should_log_pause_event(self, session_id: str, event_key: str) -> bool:
        now = time.time()
        full_key = f"{session_id}:{event_key}"
        last = self._pause_event_last_ts.get(full_key, 0)
        if now - last < self._PAUSE_EVENT_COOLDOWN_SEC:
            return False
        self._pause_event_last_ts[full_key] = now
        return True

    @staticmethod
    def _clear_hold_timeout_queue_entry(pos: dict) -> None:
        try:
            from backend.services.hold_timeout_review_queue import clear_position
            pid = pos.get("id")
            if pid:
                clear_position(int(pid))
        except Exception:
            pass

    @staticmethod
    def _sync_hold_timeout_alerts(
        account_id: int,
        positions_list: list,
        bal_info: dict,
    ) -> list:
        """扫描超时/超期持仓并注入 LLM prompt（统一分析与复审共用）。"""
        try:
            from backend.services.hold_timeout_review_queue import (
                get_alerts_for_prompt,
                sync_open_positions,
            )
            sync_open_positions(int(account_id), positions_list or [])
            alerts = get_alerts_for_prompt(int(account_id))
            if alerts:
                bal_info["_hold_timeout_alerts"] = alerts
                bal_info["_hold_timeout_has_expired"] = any(
                    bool(a.get("expired")) for a in alerts
                )
            return alerts
        except Exception as exc:
            logger.debug(f"[FullAuto] hold_timeout alerts 注入失败: {exc}")
            return []

    def _get_trading_account_id(self, db: Session, session) -> int:
        """解析实际交易/资金池账户ID：模拟模式用paper_account_id，实盘用account_id"""
        return getattr(session, 'paper_account_id', None) or session.account_id

    def _session_trading_mode(self, session) -> str:
        return (getattr(session, "trading_mode", "") or "paper").strip().lower()

    def _is_live_trading_session(self, session) -> bool:
        return self._session_trading_mode(session) == "live"

    def _live_constitutional_enabled(self, session) -> bool:
        from backend.services.full_auto.live_trading import (
            build_live_trading_host,
            live_constitutional_enabled,
        )
        return live_constitutional_enabled(session, build_live_trading_host(self))

    def _fetch_live_account_snapshot(self, db: Session, account_id: int) -> dict:
        from backend.services.full_auto.live_trading import fetch_live_account_snapshot
        return fetch_live_account_snapshot(db, account_id)

    def _live_constitutional_pre_trade_check(
        self,
        db: Session,
        session,
        strat,
        decision: dict,
    ) -> tuple:
        from backend.services.full_auto.live_trading import (
            build_live_trading_host,
            live_constitutional_pre_trade_check,
        )
        return live_constitutional_pre_trade_check(
            db, session, strat, decision, build_live_trading_host(self),
        )

    def _check_live_constitutional_session_risk(self, db: Session, session) -> None:
        from backend.services.full_auto.live_trading import (
            build_live_trading_host,
            check_live_constitutional_session_risk,
        )
        host = build_live_trading_host(self)
        check_live_constitutional_session_risk(db, session, host)
        self._defensive_entered_at = host.defensive_entered_at

    def _resolve_session_trade_symbols(
        self,
        session,
        db: Session = None,
        *,
        include_positions: bool = True,
        include_active_strategies: bool = True,
    ) -> List[str]:
        """会话实际交易 universe（单一来源，避免编排器/Agent/K线各读各的列表）。

        合并：手动选币 + AI选币 + 当前持仓 + 本账户 active 策略币。
        用户无需再为 XPL 等币「单独配编排器」——只要在交易/持仓/策略里出现就会纳入。
        """
        merged: List[str] = []
        seen: set = set()

        def _add(sym) -> None:
            u = str(sym or "").strip().upper()
            if u and u not in seen:
                seen.add(u)
                merged.append(u)

        try:
            from backend.services.auto_coin_selector import get_fixed_symbols_for_session
        except Exception:
            get_fixed_symbols_for_session = None  # type: ignore

        if get_fixed_symbols_for_session and getattr(session, "session_id", None):
            for s in get_fixed_symbols_for_session(
                session.session_id, db, tier="short"
            ):
                _add(s)
        else:
            for s in (getattr(session, "symbols", None) or []):
                _add(s)

        # 短线 AI 选币：开关开启时并入；关闭时仅靠持仓/策略续管
        if getattr(session, "auto_coin_enabled", False):
            for s in (getattr(session, "auto_coin_symbols", None) or []):
                _add(s)

        # 兼容兜底：固定+AI 都空时回退旧 symbols 并集
        if not merged:
            for s in (getattr(session, "symbols", None) or []):
                _add(s)
            for s in (getattr(session, "auto_coin_symbols", None) or []):
                _add(s)

        if db is not None:
            if include_positions:
                try:
                    acct = self._get_trading_account_id(db, session)
                    if acct:
                        from backend.services.paper_trading_engine import paper_engine
                        for p in (paper_engine.get_positions(db, acct) or []):
                            _add(p.get("symbol"))
                except Exception:
                    pass
            if include_active_strategies:
                try:
                    from backend.database.models import AIStrategy
                    acct = self._get_trading_account_id(db, session)
                    if acct:
                        rows = (
                            db.query(AIStrategy.primary_symbol)
                            .filter(
                                AIStrategy.account_id == acct,
                                AIStrategy.status == "active",
                            )
                            .distinct()
                            .all()
                        )
                        for (sym,) in rows:
                            _add(sym)
                except Exception:
                    pass
        return merged

    def _invalidate_session_status_cache(self, session_id: str):
        """状态变更时清除缓存"""
        self._session_status_cache.pop(session_id, None)

    def _run_hold_timeout_ai_review_if_needed(
        self, session_id: str, *, priority_expired: bool = False,
    ) -> None:
        from backend.services.full_auto.hold_timeout_trend_review import (
            build_hold_trend_review_host,
            run_hold_timeout_ai_review_if_needed,
        )
        host = build_hold_trend_review_host(self)
        run_hold_timeout_ai_review_if_needed(
            session_id, host, priority_expired=priority_expired,
        )
        self._last_hold_timeout_ai_review = host.last_hold_timeout_ai_review

    def _run_hold_timeout_ai_review(
        self, db: Session, session, pending: list,
    ) -> None:
        from backend.services.full_auto.hold_timeout_trend_review import (
            build_hold_trend_review_host,
            run_hold_timeout_ai_review,
        )
        run_hold_timeout_ai_review(
            db, session, pending, build_hold_trend_review_host(self),
        )

    def _run_trend_review(self, db, session, account_id, market_summary):
        from backend.services.full_auto.hold_timeout_trend_review import (
            build_hold_trend_review_host,
            run_trend_review,
        )
        run_trend_review(
            db, session, account_id, market_summary,
            build_hold_trend_review_host(self),
        )

    # [2026-08-17 删除] _run_light_trading_cycle / _run_quick_orchestrator_eval
    # 死代码（审计实锤 0 调用，与 trading_cycle_loop / orch_background 功能重叠）。
    # ══════════════════════════════════════════════════
    #  工具
    # ══════════════════════════════════════════════════

    def _execute_ai_decisions(self, db: Session, session, active_ids: list,
                              market_data: dict):
        from backend.services.full_auto.ai_decisions import (
            build_ai_decisions_host,
            execute_ai_decisions,
        )
        host = build_ai_decisions_host(self)
        execute_ai_decisions(db, session, active_ids, market_data, host)

    def _is_unified_executor_on(self) -> bool:
        """阶段 3 统一执行器开关（默认 false，灰度启用）。

        true 时 _execute_paper_trade / _execute_live_trade 通过统一 ExecutionChannel 下单。
        现阶段仅影响最终下单调用点，仓位管理/风控/TP-SL 计算逻辑不变。
        """
        try:
            from backend.services.exchange.executors import is_unified_executor_enabled
            return is_unified_executor_enabled()
        except Exception:
            return False

    # ── trade_nature 合法值集合 ──
    _VALID_TRADE_NATURES = {"scalp", "intraday", "swing", "position", "trend_follow"}

    def _get_validated_trade_nature(self, genome: dict, decision: dict, tier: str) -> str:
        """获取并验证trade_nature，确保返回有效值。

        优先级: genome > decision > tier反推
        """
        # 第一优先：strategy genome
        strat_nature = (genome.get("trade_nature") or "").strip().lower()
        if strat_nature in self._VALID_TRADE_NATURES:
            return strat_nature

        # 第二优先：AI decision
        decision_nature = (decision.get("trade_nature") or "").strip().lower()
        if decision_nature in self._VALID_TRADE_NATURES:
            return decision_nature

        # 第三优先：从tier反推（带warning日志），并自动回写 genome 避免下次再 fallback
        tier_fallback = {"short": "intraday", "mid": "swing", "long": "position"}
        fallback = tier_fallback.get((tier or "mid").strip().lower(), "swing")

        logger.warning(
            f"[FullAuto] trade_nature无效(genome='{strat_nature}', decision='{decision_nature}')，"
            f"自动从tier={tier}推断为{fallback}"
        )
        # 回写 genome，防止历史策略每次都触发 fallback
        if isinstance(genome, dict):
            genome["trade_nature"] = fallback
        return fallback

    def _apply_auto_coin_position_scale(
        self,
        db: Session,
        session,
        account_id: int,
        symbol: str,
        plan,
    ) -> None:
        """AI 自动选币：全局缩仓；历史成交不足时进入更严试探期。

        阶段C：当该币被 AI 标记为 LAYER 2 试仓（test_position=True）时，
        额外乘 AUTO_COIN_PROBE_SIZE_MULT（50%）——与「历史成交不足」的试探期
        叠加，进一步降低首笔风险。
        """
        if plan.action not in ("open", "close_and_open"):
            return
        try:
            from backend.config.settings import (
                AUTO_COIN_POSITION_SIZE_MULT,
                AUTO_COIN_PROBE_MIN_CLOSED,
                AUTO_COIN_PROBE_SIZE_MULT,
            )
            from backend.services.auto_coin_policy import applies_strict_auto_coin_rules
            auto_syms = getattr(session, "auto_coin_symbols", None) or []
            if not applies_strict_auto_coin_rules(symbol, auto_syms):
                return
            mult = float(AUTO_COIN_POSITION_SIZE_MULT)
            from backend.database.models import PaperOrder as _PO
            closed_n = db.query(_PO).filter(
                _PO.account_id == account_id,
                _PO.symbol == symbol,
                _PO.pnl.isnot(None),
            ).count()
            probe = closed_n < int(AUTO_COIN_PROBE_MIN_CLOSED)
            if probe:
                mult *= float(AUTO_COIN_PROBE_SIZE_MULT)
            # 阶段C：LAYER 2 试仓 → 再乘 PROBE_SIZE_MULT（50% 标准仓位）
            ai_test_position = False
            try:
                from backend.services.auto_coin_selector import auto_coin_scheduler
                _sid = getattr(session, "session_id", None)
                if _sid:
                    _meta = auto_coin_scheduler.get_selection_meta(_sid)
                    _sym_meta = _meta.get(str(symbol).upper()) or {}
                    ai_test_position = bool(_sym_meta.get("test_position", False))
            except Exception:
                pass
            if ai_test_position:
                mult *= float(AUTO_COIN_PROBE_SIZE_MULT)
            if hasattr(plan, "margin_usd"):
                plan.margin_usd = round(float(plan.margin_usd or 0) * mult, 2)
            if hasattr(plan, "notional_usd"):
                plan.notional_usd = round(float(plan.notional_usd or 0) * mult, 2)
            _tags = []
            if probe:
                _tags.append("试探期")
            if ai_test_position:
                _tags.append("AI试仓50%")
            _tag = f"（{'/'.join(_tags)}）" if _tags else ""
            detail = f"🌟 {symbol} AI自动选币缩仓×{mult:.2f}{_tag}"
            logger.info(f"[FullAuto] {detail}")
            self._append_event(session, "auto_coin_probe", detail)
        except Exception as err:
            logger.debug(f"[FullAuto] 自动选币缩仓跳过: {err}")
            try:
                db.rollback()
            except Exception:
                pass

    def _execute_paper_trade(self, db: Session, session, strat, decision: dict) -> bool:
        from backend.services.full_auto.paper_execution import (
            build_paper_execution_host,
            execute_paper_trade,
        )
        return execute_paper_trade(
            db, session, strat, decision, build_paper_execution_host(self),
        )

    def _execute_defensive_analysis(self, db: Session, session, market_summary: dict):
        from backend.services.full_auto.defensive_cycle import (
            build_defensive_host,
            run_defensive_analysis,
        )
        run_defensive_analysis(db, session, market_summary, build_defensive_host(self))

    def _execute_defensive_verdicts(self, db: Session, session, account_id: int,
                                     verdicts: list, positions_list: list):
        from backend.services.full_auto.defensive_cycle import (
            build_defensive_host,
            run_defensive_verdicts,
        )
        run_defensive_verdicts(db, session, account_id, verdicts, positions_list, build_defensive_host(self))

    def _rule_based_defensive(self, db: Session, session, positions_list: list, market_summary: dict):
        from backend.services.full_auto.defensive_cycle import (
            build_defensive_host,
            run_rule_based_defensive,
        )
        run_rule_based_defensive(db, session, positions_list, market_summary, build_defensive_host(self))

    def _execute_live_trade(self, db: Session, session, strat, decision: dict):
        from backend.services.full_auto.live_trading import (
            build_live_trading_host,
            execute_live_trade,
        )
        execute_live_trade(db, session, strat, decision, build_live_trading_host(self))

    def _is_reduce_cooldown_exempt(self, pos: dict, reason_tag: str = "") -> bool:
        """判断是否豁免减仓冷却检查"""
        # 止损触发
        if pos.get("is_stop_loss") or pos.get("sl_triggered"):
            return True
        # 风控门强制操作
        exempt_keywords = ("sl_hit", "risk_gate", "account_risk", "emergency", "margin_call")
        if any(k in (reason_tag or "").lower() for k in exempt_keywords):
            return True
        # 深度亏损紧急出场(>-8%)
        margin_val = float(pos.get("margin", 0))
        upnl_val = float(pos.get("unrealized_pnl", 0))
        if margin_val > 0:
            pnl_pct = upnl_val / margin_val
            if pnl_pct <= -0.08:
                return True
        return False

    # ── 整改项6: 仓位最小决策间隔 ────────────────────
    def _should_evaluate_position(self, pos_id: int, tier: str) -> bool:
        """检查该仓位是否应该在本轮被AI评估（最小决策间隔检查）"""
        from backend.config.settings import POSITION_MIN_DECISION_INTERVAL_ENABLED
        if not POSITION_MIN_DECISION_INTERVAL_ENABLED:
            return True

        interval = self._POSITION_MIN_DECISION_INTERVAL.get(tier, 600)
        with self._state_lock:
            last_ts = self._position_last_decision_ts.get(pos_id, 0)
        elapsed = time.time() - last_ts
        return elapsed >= interval

    def _record_position_decision(self, pos_id: int) -> None:
        """记录仓位决策时间，顺便清理超过24小时的旧条目"""
        now = time.time()
        with self._state_lock:
            self._position_last_decision_ts[pos_id] = now
            # 清理超过24小时的旧条目（防止内存泄漏）
            if len(self._position_last_decision_ts) > 200:
                cutoff = now - 86400  # 24小时
                self._position_last_decision_ts = {
                    k: v for k, v in self._position_last_decision_ts.items()
                    if v > cutoff
                }

    @staticmethod
    def _format_agent_event_detail(
        symbol: str,
        tier_label: str,
        action: str,
        *,
        metric_label: str,
        metric_value: int,
        agent_label: str,
        reasoning: str = "",
        hold_reason: str = "",
        reasoning_max: int = 320,
    ) -> str:
        """AI 决策事件文案：hold 时附带代码层 why=，reasoning 不再截到 100 字。"""
        head = f"🎯 {symbol}[{tier_label}]: {action} ({metric_label}={metric_value})"
        parts = [head]
        if action == "hold" and hold_reason:
            parts.append(f"代码原因={hold_reason}")
        _reason = (reasoning or "").strip()[:reasoning_max]
        if _reason:
            parts.append(f"[{agent_label}] {_reason}")
        return " | ".join(parts)

    @staticmethod
    def _append_event(session, event_type: str, detail: str,
                      severity: str = "info"):
        """向会话追加事件日志
        severity: info / warning / critical
        """
        log = list(session.event_log or [])
        entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            "detail": detail,
        }
        # 附带 trace_id 用于跨服务追踪
        _tid = getattr(FullAutoTradingService, '_current_trace_id', None)
        if _tid:
            entry["trace_id"] = _tid
        if severity != "info":
            entry["severity"] = severity
        log.append(entry)
        if len(log) > 200:
            log = log[-200:]
        session.event_log = log
        try:
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(session, "event_log")
        except Exception:
            pass

        # v3 整改: 阻断类事件汇入 BlockReportAggregator，供 /api/system/block-report-top 查询
        try:
            _etype = (event_type or "").lower()
            _is_block = (
                "block" in _etype
                or "blocked" in _etype
                or "rebound_gate" in _etype
                or "cooldown" in _etype
                or "circuit_breaker" in _etype
                or "defensive_entry" in _etype
            )
            if _is_block:
                from backend.services.block_report_aggregator import record_block
                record_block(event_type, detail)
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════
    #  数据断流 / API 失败 → 显著告警
    # ══════════════════════════════════════════════════════════════

    def _check_data_health(self, session, market_summary: Dict[str, Any],
                           symbols: List[str], db=None):
        from backend.services.full_auto.data_health import (
            build_data_health_host,
            check_data_health,
        )
        host = build_data_health_host(self)
        check_data_health(session, market_summary, symbols, host, db=db)
        self._symbol_frozen_set = host.symbol_frozen_set
        self._health_status = host.health_status

    def _record_ai_success(self, session):
        """AI 调用成功时更新健康状态"""
        self._health_status["ai_connection_ok"] = True
        self._health_status["consecutive_ai_failures"] = 0
        self._health_status["last_ai_success"] = datetime.now(timezone.utc).isoformat()
        self._health_status["ai_issues"] = []

    def _record_ai_failure(self, session, error_msg: str, is_timeout: bool = False):
        """AI 调用失败时更新健康状态并发出告警"""
        self._health_status["consecutive_ai_failures"] += 1
        fails = self._health_status["consecutive_ai_failures"]
        self._health_status["ai_issues"].append({
            "time": datetime.now(timezone.utc).isoformat(),
            "error": error_msg[:200],
            "is_timeout": is_timeout,
        })
        if len(self._health_status["ai_issues"]) > 20:
            self._health_status["ai_issues"] = self._health_status["ai_issues"][-20:]

        if fails >= 3:
            self._health_status["ai_connection_ok"] = False
            self._append_event(session, "system_alert",
                f"🚨 AI 连续{fails}次调用失败！最近错误: {error_msg[:100]} | "
                f"系统禁止假数据开仓，仅 hold/止盈止损管仓",
                severity="critical")
            logger.critical(f"[FullAuto] AI 连续{fails}次失败: {error_msg[:100]}")
        elif fails >= 1:
            self._append_event(session, "ai_warning",
                f"⚠️ AI 调用失败(第{fails}次): "
                f"{'超时' if is_timeout else error_msg[:80]} | 正在自动重试...",
                severity="warning")
            logger.warning(f"[FullAuto] AI 调用失败(第{fails}次): {error_msg[:80]}")

    # ══════════════════════════════════════════════════════════════
    #  AI 返回结果结构化审核 — 字段完整性 / 数值合理性 / 逻辑一致性
    # ══════════════════════════════════════════════════════════════

    def _validate_ai_decisions(self, session, master_result: Dict,
                                session_symbols: List[str],
                                positions_list: List[Dict]) -> Dict:
        from backend.services.full_auto.ai_decision_audit import (
            build_ai_decision_audit_host,
            validate_ai_decisions,
        )
        return validate_ai_decisions(
            session, master_result, session_symbols, positions_list,
            build_ai_decision_audit_host(self),
        )

    def _register_qaa_agents(self):
        from backend.services.full_auto.qaa_legacy_cycle import (
            build_qaa_legacy_host,
            register_qaa_agents,
        )
        host = build_qaa_legacy_host(self)
        register_qaa_agents(host)
        self._pre_screen_results = host.pre_screen_results
        self._pre_screen_passed = host.pre_screen_passed
        self._qaa_last_decision = host.qaa_last_decision
        self._qaa_agents_registered = host.qaa_agents_registered


    def _get_qaa_handler(self, agent_id: str):
        from backend.services.full_auto.qaa_legacy_cycle import (
            build_qaa_legacy_host,
            get_qaa_handler,
        )
        return get_qaa_handler(agent_id, build_qaa_legacy_host(self))

    def _run_qaa_tick(self, session_id: str):
        from backend.services.full_auto.qaa_legacy_cycle import (
            build_qaa_legacy_host,
            run_qaa_tick,
        )
        host = build_qaa_legacy_host(self)
        run_qaa_tick(session_id, host)
        self._pre_screen_results = host.pre_screen_results
        self._pre_screen_passed = host.pre_screen_passed
        self._qaa_last_decision = host.qaa_last_decision
        self._qaa_agents_registered = host.qaa_agents_registered

    # ═══════════════════════════════════════════════════════════════════
    #  编排器独立后台评估线程
    # ═══════════════════════════════════════════════════════════════════

    def _ensure_orchestrator_bg_running(self, session_id: str, symbols: list):
        from backend.services.full_auto.orch_background import (
            build_orch_background_host,
            ensure_orchestrator_bg_running,
        )
        host = build_orch_background_host(self)
        ensure_orchestrator_bg_running(session_id, symbols, host)
        self._orch_bg_thread = host.orch_bg_thread
        self._orch_bg_session_id = host.orch_bg_session_id
        self._orch_bg_symbols = host.orch_bg_symbols
        self._orch_bg_running = host.orch_bg_running
        self._last_unified_snapshot = host.last_unified_snapshot
        self._market_scan_cache = host.market_scan_cache
        self._market_scan_cache_ts = host.market_scan_cache_ts
        self._last_orch_decisions = host.last_orch_decisions
        self._last_orch_decisions_ts = host.last_orch_decisions_ts

    # [2026-08-17 删除] _run_qaa_v3_tick 死代码（qaa_v3_tick_cycle 零调用，QAA v3
    # 路由断裂，实际跑的是 qaa_legacy + ai_first）。

    def _run_analyst_system_v3(
        self,
        session_id: str,
        session_status: str,
        session_orm_id: int,
        account_id: int,
        active_ids: list,
        market_summary: dict,
    ):
        from backend.services.full_auto.analyst_system_v3_cycle import (
            build_analyst_v3_host,
            run_analyst_system_v3,
        )
        host = build_analyst_v3_host(self)
        run_analyst_system_v3(
            session_id, session_status, session_orm_id, account_id,
            active_ids, market_summary, host,
        )
        self._mlto_handled_keys = host.mlto_handled_keys

    def _write_qaa_v3_forced_decision_logs(
        self,
        *,
        session_orm_id: int,
        account_id: int,
        decisions: list,
        balance_info: dict,
        positions_list: list,
        market_summary: dict,
    ) -> None:
        from backend.services.full_auto.qaa_v3_forced_logs import write_qaa_v3_forced_decision_logs
        write_qaa_v3_forced_decision_logs(
            session_orm_id=session_orm_id,
            account_id=account_id,
            decisions=decisions,
            balance_info=balance_info,
            positions_list=positions_list,
            market_summary=market_summary,
        )

full_auto_service = FullAutoTradingService()
