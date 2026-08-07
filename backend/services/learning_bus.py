"""
Learning Bus — 统一学习总线 (D5)

将 3 个独立学习系统连接到统一数据流：
- unified_learning_service: 每笔交易结果处理 (always on)
- strategy_learning_service: 定期复盘 (每 N 笔触发)
- strategy_evolver: 重进化 (过拟合检测时触发)
- trade_memory_miner: 模式挖掘 (每 M 笔触发)
- pattern_extractor: 成功模板提取 (达标时触发)

设计原则：
- 单一入口：所有交易结果通过 LearningBus.dispatch() 进入
- 按需触发：不同系统有不同的触发频率
- 零耦合：总线只负责路由，不实现学习逻辑
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# 触发阈值
REVIEW_TRIGGER_EVERY_N_TRADES = 15


def get_review_trigger_every_n() -> int:
    try:
        from backend.services.paper_pace_controller import paper_pace_controller
        return paper_pace_controller.get_knobs().learning_review_every_n
    except Exception:
        return REVIEW_TRIGGER_EVERY_N_TRADES


def get_miner_trigger_every_n() -> int:
    try:
        from backend.services.paper_pace_controller import paper_pace_controller
        return paper_pace_controller.get_knobs().learning_miner_every_n
    except Exception:
        return MINER_TRIGGER_EVERY_N_TRADES


def get_thesis_postmortem_cooldown_sec() -> int:
    try:
        from backend.config.settings import THESIS_POSTMORTEM_COOLDOWN_SEC
        return int(THESIS_POSTMORTEM_COOLDOWN_SEC or 3600)
    except Exception:
        return 3600


MINER_TRIGGER_EVERY_N_TRADES = 25       # 每 25 笔触发模式挖掘
EVOLVER_COOLDOWN_HOURS = 72             # 重进化最小间隔 72 小时
MINER_COOLDOWN_HOURS = 24               # 模式挖掘最小间隔 24 小时
THESIS_POSTMORTEM_COOLDOWN_SEC = 3600   # 同 symbol+tier 复盘节流 1h（可被 settings 覆盖）


class LearningBus:
    """
    统一学习总线 (D5)

    用法：
        bus = get_learning_bus()
        bus.dispatch(db, outcome)  # 每笔交易结果通过此处进入
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # 计数器
        self._trade_count_total: int = 0
        self._trade_count_since_review: int = 0
        self._trade_count_since_miner: int = 0
        # P0.5: 因果发现计数器（按策略+标的组合）
        self._causal_counters: Dict[str, int] = {}

        # 上次触发时间
        self._last_review_at: Optional[datetime] = None
        self._last_evolver_at: Optional[datetime] = None
        self._last_miner_at: Optional[datetime] = None
        self._last_causal_at: Optional[datetime] = None

        # 累积的 TradeOutcome 批次（用于批量处理）
        self._pending_outcomes: List[Any] = []
        self._pending_lock = threading.Lock()

        # MLTO thesis 复盘节流 {symbol:tier -> last_ts}
        self._thesis_postmortem_last: Dict[str, float] = {}
        self._thesis_postmortem_lock = threading.Lock()

        logger.info("[LearningBus] 统一学习总线初始化完成 (含因果发现触发)")

    def dispatch(
        self,
        db: Session,
        outcome,  # TradeOutcome
        trigger_review: bool = True,
        trigger_miner: bool = True,
    ) -> Dict[str, Any]:
        """[已废弃] 统一学习入口。

        L2 收敛后，process_outcome 内部已自动调用 BackendRegistry.handle_all，
        调用方无需再手动 dispatch。本方法保留仅为向后兼容，转发到 registry。

        新代码请勿调用本方法 —— 直接调 unified_learning.process_outcome 即可。

        Returns:
            包含各系统触发状态的结果字典（向后兼容旧字段名）
        """
        logger.debug(
            "[LearningBus] dispatch() 已废弃，请改用 process_outcome（内部自动调度）"
        )
        result = {
            "unified_learning": False,
            "review_triggered": False,
            "evolver_triggered": False,
            "miner_triggered": False,
            "pattern_extracted": False,
            "causal_discovery_triggered": False,
        }
        try:
            from backend.services.learning import registry as _registry
            triggered_map = _registry.handle_all(db, outcome)
            result["unified_learning"] = True
            # 映射后端名 → 旧字段名（向后兼容 dashboard/日志里的判断）
            result["review_triggered"] = bool(triggered_map.get("periodic_review"))
            result["miner_triggered"] = bool(triggered_map.get("pattern_mining"))
            result["pattern_extracted"] = bool(triggered_map.get("pattern_extraction"))
            result["causal_discovery_triggered"] = bool(triggered_map.get("causal_discovery"))
        except Exception as e:
            logger.error(f"[LearningBus] dispatch(转发)异常: {e}", exc_info=True)
        return result

    def enqueue_thesis_postmortem(self, outcome) -> bool:
        """MLTO 平仓后异步复盘队列（节流 1h/symbol+tier，不阻塞主 learning 路径）。"""
        meta = outcome.metadata if isinstance(getattr(outcome, "metadata", None), dict) else {}
        thesis_id = meta.get("thesis_id")
        if not thesis_id:
            return False
        sym = (getattr(outcome, "symbol", None) or meta.get("symbol") or "").upper()
        tier = meta.get("tier") or getattr(outcome, "tier", None) or "mid"
        key = f"{sym}:{tier}"
        now = time.time()
        with self._thesis_postmortem_lock:
            last = self._thesis_postmortem_last.get(key, 0)
            if now - last < get_thesis_postmortem_cooldown_sec():
                return False
            self._thesis_postmortem_last[key] = now

        def _worker():
            try:
                from backend.database.connection import AnalyticsSessionLocal
                from backend.services.mlto import thesis_store
                adb = AnalyticsSessionLocal()
                try:
                    thesis_store.append_event(
                        str(thesis_id),
                        "postmortem",
                        {
                            "pnl": float(getattr(outcome, "pnl", 0) or 0),
                            "pnl_pct": float(getattr(outcome, "pnl_pct", 0) or 0),
                            "close_reason": meta.get("close_reason") or getattr(outcome, "exit_channel", ""),
                            "hub_at_entry": meta.get("hub_adjusted_at_entry"),
                            "memory_event_ids": meta.get("memory_event_ids") or [],
                            "async": True,
                        },
                        db=adb,
                    )
                    logger.info("[LearningBus] MLTO postmortem queued: %s %s thesis=%s", sym, tier, str(thesis_id)[:8])
                finally:
                    adb.close()
            except Exception as exc:
                logger.debug("[LearningBus] thesis postmortem skip: %s", exc)

        threading.Thread(target=_worker, daemon=True, name=f"mlto-postmortem-{sym}").start()
        return True

    # ══════════════════════════════════════════════════
    #  触发条件判断（仅供 force_review/force_miner 手动 API 使用）
    # ══════════════════════════════════════════════════

    def _should_trigger_review(self) -> bool:
        return self._trade_count_since_review >= get_review_trigger_every_n()

    def _should_trigger_evolver(self, db: Session, outcome) -> bool:
        """过拟合检测：实盘与回测偏离超过阈值"""
        if self._last_evolver_at:
            hours_since = (datetime.now(timezone.utc) - self._last_evolver_at).total_seconds() / 3600
            if hours_since < EVOLVER_COOLDOWN_HOURS:
                return False

        if not outcome.strategy_id:
            return False

        try:
            from backend.database.models import StrategyMemory
            mem = db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == outcome.strategy_id
            ).first()
            if not mem:
                return False

            # 实盘胜率远低于回测胜率 → 过拟合
            live_wr = mem.win_rate or 0
            backtest_wr = getattr(mem, 'backtest_win_rate', None) or 0
            if backtest_wr > 0 and live_wr > 0 and (backtest_wr - live_wr) > 0.25:
                logger.info(
                    f"[LearningBus] 检测到过拟合: live_wr={live_wr:.0%} "
                    f"backtest_wr={backtest_wr:.0%}, delta={backtest_wr - live_wr:.0%}"
                )
                return True
        except Exception:
            pass

        return False

    def _should_trigger_miner(self) -> bool:
        return self._trade_count_since_miner >= get_miner_trigger_every_n()

    def _should_trigger_pattern_extraction(self, outcome) -> bool:
        """当策略达标时触发模式提取"""
        if not outcome.strategy_id:
            return False
        # 只在 profitable 交易时检查
        return outcome.pnl > 0

    # ══════════════════════════════════════════════════
    #  触发执行
    # ══════════════════════════════════════════════════

    def _trigger_review(self, db: Session, outcome) -> bool:
        """触发定期复盘 (strategy_learning_service)"""
        try:
            from backend.services.strategy_learning_service import strategy_learning
            strategy_id = outcome.strategy_id
            if not strategy_id:
                logger.info("[LearningBus] 跳过定期复盘: 无 strategy_id")
                return False
            strategy_learning.run_periodic_review(strategy_id, days=14)
            self._trade_count_since_review = 0
            self._last_review_at = datetime.now(timezone.utc)
            logger.info(f"[LearningBus] 定期复盘已触发: {strategy_id}")
            return True
        except Exception as e:
            logger.warning(f"[LearningBus] 定期复盘触发失败: {e}")
            return False

    def _trigger_evolver(self, db: Session, outcome) -> bool:
        """触发重进化 (strategy_evolver)

        B2 修复: run_evolution() 需要 template_id (tpl_xxx)，不是 strategy_id (auto_xxx)。
        通过 AIStrategy.genome.source_template_id 反查正确 ID。
        """
        try:
            strategy_id = outcome.strategy_id
            if not strategy_id:
                return False

            # B2: 从 AIStrategy.genome 反查 source_template_id
            from backend.database.models import AIStrategy
            # strategy_id 是字符串业务 ID（如 auto_xxx），对应 AIStrategy.strategy_id 而非自增主键 id
            strategy = db.query(AIStrategy).filter(AIStrategy.strategy_id == str(strategy_id)).first()
            if not strategy:
                logger.warning(f"[LearningBus] strategy {strategy_id} not found, skip evolution")
                return False

            genome = strategy.genome or {}
            template_id = genome.get("source_template_id")
            if not template_id:
                logger.info(
                    f"[LearningBus] strategy {strategy_id} has no source_template_id, skip evolution"
                )
                return False

            from backend.services.strategy_evolver import StrategyEvolver
            evolver = StrategyEvolver()
            champion = evolver.run_evolution(db, template_id, generations=8, population_size=12)
            self._last_evolver_at = datetime.now(timezone.utc)

            if champion:
                logger.info(
                    f"[LearningBus] 重进化完成: template={template_id} "
                    f"strategy={strategy_id} "
                    f"Sharpe={champion.get('sharpe', 0):.2f}"
                )
                return True
            return False
        except Exception as e:
            logger.warning(f"[LearningBus] 重进化触发失败: {e}")
            return False

    def _trigger_miner(self, db: Session, outcome) -> bool:
        """触发模式挖掘 (trade_memory_miner)"""
        try:
            from backend.services.trade_memory_miner import mine_trade_patterns, inject_patterns_to_memory

            # 检查冷却时间
            if self._last_miner_at:
                hours_since = (datetime.now(timezone.utc) - self._last_miner_at).total_seconds() / 3600
                if hours_since < MINER_COOLDOWN_HOURS:
                    return False

            symbol = outcome.symbol if outcome.symbol else None
            strategy_id = outcome.strategy_id or outcome.template_id

            if strategy_id:
                injected = inject_patterns_to_memory(db, strategy_id, symbol)
                if injected:
                    self._trade_count_since_miner = 0
                    self._last_miner_at = datetime.now(timezone.utc)
                    return True

            return False
        except Exception as e:
            logger.warning(f"[LearningBus] 模式挖掘触发失败: {e}")
            return False

    def _trigger_pattern_extraction(self, db: Session, outcome) -> bool:
        """触发成功模板提取 (pattern_extractor)"""
        try:
            strategy_id = outcome.strategy_id
            if not strategy_id:
                return False

            from backend.services.pattern_extractor import PatternExtractor
            extractor = PatternExtractor()
            template = extractor.extract_successful_pattern(db, strategy_id)
            if template:
                logger.info(
                    f"[LearningBus] 成功模板已提取: {strategy_id} "
                    f"best_regime={template.get('best_regime')}"
                )
                return True
            return False
        except Exception as e:
            logger.warning(f"[LearningBus] 模板提取失败: {e}")
            return False

    # ══════════════════════════════════════════════════
    #  P0.5: 因果发现触发
    # ══════════════════════════════════════════════════

    def _should_trigger_causal_discovery(self, outcome) -> bool:
        """检查是否应触发因果发现。

        加密适配：使用 N=30（非传统 50），冷却 6 小时。
        """
        if not outcome.strategy_id or not outcome.symbol:
            return False

        try:
            if os.getenv("AI_CAUSAL_DISCOVERY_ENABLED", "false").lower() not in ("1", "true", "yes", "on"):
                return False

            from backend.services.causal_discovery_engine import get_causal_discovery_engine
            cde = get_causal_discovery_engine()
            return cde.should_trigger(outcome.strategy_id, outcome.symbol)
        except Exception as e:
            logger.debug(f"[LearningBus] 因果发现触发检查跳过: {e}")
            return False

    def _trigger_causal_discovery(self, db: Session, outcome) -> bool:
        """触发因果发现。异步执行，不阻塞主路径。"""
        try:
            from backend.services.causal_discovery_engine import get_causal_discovery_engine
            cde = get_causal_discovery_engine()

            # 在后台线程执行（因果发现可能耗时）
            import threading
            strategy_id = outcome.strategy_id
            symbol = outcome.symbol

            def _worker():
                try:
                    from backend.database.connection import SessionLocal
                    worker_db = SessionLocal()
                    try:
                        rules = cde.discover(worker_db, strategy_id, symbol)
                        logger.info(
                            f"[LearningBus] 因果发现完成: {strategy_id}/{symbol} "
                            f"产出 {len(rules)} 条规则"
                        )
                    finally:
                        worker_db.close()
                except Exception as w_err:
                    logger.warning(f"[LearningBus] 因果发现后台任务失败: {w_err}")

            threading.Thread(
                target=_worker, daemon=True,
                name=f"causal-discovery-{strategy_id}"
            ).start()

            self._last_causal_at = datetime.now(timezone.utc)
            logger.info(f"[LearningBus] 因果发现已触发: {strategy_id}/{symbol}")
            return True
        except Exception as e:
            logger.warning(f"[LearningBus] 因果发现触发失败: {e}")
            return False

    # ══════════════════════════════════════════════════
    #  状态查询
    # ══════════════════════════════════════════════════

    def get_status(self) -> Dict[str, Any]:
        return {
            "trade_count_total": self._trade_count_total,
            "trade_count_since_review": self._trade_count_since_review,
            "trade_count_since_miner": self._trade_count_since_miner,
            "last_review_at": self._last_review_at.isoformat() if self._last_review_at else None,
            "last_evolver_at": self._last_evolver_at.isoformat() if self._last_evolver_at else None,
            "last_miner_at": self._last_miner_at.isoformat() if self._last_miner_at else None,
            "last_causal_at": self._last_causal_at.isoformat() if self._last_causal_at else None,
            "next_review_in": max(0, get_review_trigger_every_n() - self._trade_count_since_review),
            "next_miner_in": max(0, get_miner_trigger_every_n() - self._trade_count_since_miner),
        }

    def force_review(self, db: Session):
        """手动触发定期复盘（API 调用）"""
        self._trade_count_since_review = REVIEW_TRIGGER_EVERY_N_TRADES
        from backend.services.unified_learning_service import TradeOutcome
        dummy = TradeOutcome(source="manual", symbol="", pnl=0)
        self._trigger_review(db, dummy)

    def force_miner(self, db: Session, symbol: Optional[str] = None):
        """手动触发模式挖掘（API 调用）"""
        from backend.services.trade_memory_miner import mine_trade_patterns
        result = mine_trade_patterns(db, symbol=symbol)
        self._trade_count_since_miner = 0
        self._last_miner_at = datetime.now(timezone.utc)
        return result


# 全局单例
_learning_bus: Optional[LearningBus] = None


def get_learning_bus() -> LearningBus:
    global _learning_bus
    if _learning_bus is None:
        _learning_bus = LearningBus()
    return _learning_bus
