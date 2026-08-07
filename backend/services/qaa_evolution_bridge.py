"""
QAA Evolution Bridge — 将后端交易数据适配到 QAA 进化系统

作为监控和优化覆盖层（overlay），不替代现有的 UnifiedLearningService 核心管道。
所有 QAA 调用在主流程 try/except 中执行，确保零风险。

架构:
    TradeOutcome → OutcomeAdapter → PerformanceTracker
                                        FeedbackCollector
                                        EvolutionHistory
    StrategyTuner → AutoOptimizer (灰度发布 + 自动回滚)
"""

from __future__ import annotations

import logging
import os
import sys
import time
import copy
import threading
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 将 QAA 包加入 Python path ──
_QAA_PKG_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "qaa_architecture_package")
)
if _QAA_PKG_DIR not in sys.path:
    sys.path.insert(0, _QAA_PKG_DIR)

# ── 延迟导入 QAA 组件 ──
_tracker_cls = None
_feedback_cls = None
_optimizer_cls = None
_history_cls = None
_policy_cls = None


def _lazy_imports():
    """延迟导入 QAA 组件，避免启动时 import 错误"""
    global _tracker_cls, _feedback_cls, _optimizer_cls, _history_cls, _policy_cls
    if _tracker_cls is not None:
        return True
    try:
        from qaa.evolution.tracker import PerformanceTracker
        from qaa.evolution.feedback import FeedbackCollector
        from qaa.evolution.optimizer import AutoOptimizer, OptimizationPolicy
        from qaa.evolution.history import EvolutionHistory
        _tracker_cls = PerformanceTracker
        _feedback_cls = FeedbackCollector
        _optimizer_cls = AutoOptimizer
        _history_cls = EvolutionHistory
        _policy_cls = OptimizationPolicy
        return True
    except Exception as e:
        logger.warning(f"[QAABridge] QAA 包导入失败，进化系统不可用: {e}")
        return False


# ════════════════════════════════════════════════════════════════
#  灰度计划 — 记录每个灰度发布的状态
# ════════════════════════════════════════════════════════════════

@dataclass
class GrayscalePlan:
    """灰度发布计划 — 按 symbol 划分 canary/control"""
    plan_id: str
    strategy_id: str
    old_genome: Dict[str, Any] = field(default_factory=dict)
    new_genome: Dict[str, Any] = field(default_factory=dict)
    status: str = "observing"  # observing | confirmed | rolled_back | failed
    canary_symbols: List[str] = field(default_factory=list)
    control_symbols: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    observation_started_at: float = 0.0
    observation_seconds: float = 600.0  # 10分钟
    baseline_canary_pnl: float = 0.0
    baseline_control_pnl: float = 0.0
    min_trades_for_eval: int = 3


# ════════════════════════════════════════════════════════════════
#  OutcomeAdapter — 将 TradeOutcome 喂入 QAA 组件
# ════════════════════════════════════════════════════════════════

class OutcomeAdapter:
    """将后端 TradeOutcome 转换为 QAA 进化系统的指标/反馈"""

    def __init__(self, bridge: "QAABridge"):
        self._bridge = bridge

    def feed_outcome(self, outcome, decision_quality: float = 0.5):
        """
        在 UnifiedLearningService.process_outcome() 的 db.commit() 之后调用。
        将交易结果喂入 QAA PerformanceTracker + FeedbackCollector + EvolutionHistory。
        """
        b = self._bridge
        if not b._enabled:
            return

        try:
            strategy_id = getattr(outcome, "strategy_id", "") or "unknown"
            symbol = getattr(outcome, "symbol", "") or "unknown"
            pnl = getattr(outcome, "pnl", 0) or 0
            pnl_pct = getattr(outcome, "pnl_pct", 0) or 0
            regime = getattr(outcome, "regime_at_entry", "unknown")
            tier = getattr(outcome, "tier", "swing")

            # 1. PerformanceTracker — 多维指标
            b.tracker.record(
                "win_rate", 1.0 if pnl > 0 else 0.0,
                domain="trading", agent_id=strategy_id,
            )
            b.tracker.record(
                "pnl_pct", pnl_pct,
                domain="trading", agent_id=strategy_id,
            )

            # 2. FeedbackCollector — 策略质量评分
            b.feedback.submit(
                target_id=strategy_id,
                target_type="agent",
                rating=max(0.0, min(1.0, decision_quality)),
                category="accuracy",
                domain="trading",
                context={"regime": regime, "symbol": symbol, "tier": tier},
            )

            # 3. FeedbackCollector — 币种可靠性
            symbol_rating = max(0.0, min(1.0, 0.5 + pnl_pct * 5))
            b.feedback.submit(
                target_id=symbol,
                target_type="skill",
                rating=symbol_rating,
                category="reliability",
                domain="trading",
            )

            # 4. EvolutionHistory — 审计日志
            b.history.record(
                target_id=strategy_id,
                action="trade_outcome",
                domain="trading",
                details={
                    "symbol": symbol,
                    "pnl": round(pnl, 6),
                    "pnl_pct": round(pnl_pct, 6),
                    "regime": regime,
                    "decision_quality": round(decision_quality, 4),
                },
            )

        except Exception as e:
            logger.debug(f"[QAABridge] feed_outcome skip: {e}")

    def feed_strategy_memory_snapshot(self, strategy_id: str, memory):
        """将 StrategyMemory 聚合指标推送到 PerformanceTracker"""
        b = self._bridge
        if not b._enabled:
            return
        try:
            if memory is None:
                return
            b.tracker.record(
                "aggregate_win_rate",
                getattr(memory, "win_rate", 0) or 0,
                domain="trading", agent_id=strategy_id,
            )
            b.tracker.record(
                "aggregate_sharpe",
                getattr(memory, "sharpe_ratio", 0) or 0,
                domain="trading", agent_id=strategy_id,
            )
            b.tracker.record(
                "aggregate_drawdown",
                getattr(memory, "max_drawdown", 0) or 0,
                domain="trading", agent_id=strategy_id,
            )
        except Exception as e:
            logger.debug(f"[QAABridge] feed_strategy_memory skip: {e}")


# ════════════════════════════════════════════════════════════════
#  StrategyTuner — 注册策略为 QAA 可调优对象
# ════════════════════════════════════════════════════════════════

class StrategyTuner:
    """将后端策略的 genome 注册为 QAA AutoOptimizer 的可调优对象"""

    def __init__(self, bridge: "QAABridge"):
        self._bridge = bridge

    def register_strategy(self, strategy_id: str, db):
        """注册单个策略为可调优对象（含 CMA-ES eval 回调）"""
        b = self._bridge
        if not b._enabled or b.optimizer is None:
            return

        try:
            from backend.database.models import AIStrategy

            # 预取策略静态信息，供 eval 回调构造回测上下文
            strat = db.query(AIStrategy).filter(
                AIStrategy.strategy_id == strategy_id
            ).first()
            symbol = (strat.primary_symbol if strat else None) or "BTC"
            timeframe = (strat.timeframe if strat else None) or "15m"
            tier = (strat.timeframe_tier if strat else None) or "mid"

            def get_config():
                try:
                    s = db.query(AIStrategy).filter(
                        AIStrategy.strategy_id == strategy_id
                    ).first()
                    return dict(s.genome or {}) if s else {}
                except Exception:
                    return {}

            def set_config(new_config: dict):
                try:
                    s = db.query(AIStrategy).filter(
                        AIStrategy.strategy_id == strategy_id
                    ).first()
                    if s:
                        s.genome = new_config
                        db.commit()
                        logger.info(f"[StrategyTuner] genome updated for {strategy_id}")
                except Exception as e:
                    db.rollback()
                    logger.error(f"[StrategyTuner] genome update failed: {e}")

            # ── eval 回调：genome 参数 -> Sharpe fitness（越大越好）──────────
            # CMA-ES 会对当前 genome 的数值参数做 ~100 次试验，每次调用 eval_fn。
            # bars 在闭包内懒加载一次后复用，避免每次 trial 都查库。
            # 仅当 QAA_OPTIMIZER=cmaes 时 _apply_plan 才会用到此回调。
            _cached_bars = None  # type: ignore[var-annotated]

            def eval_config(config: dict) -> float:
                nonlocal _cached_bars
                try:
                    from backend.services.live_pipeline_backtest_engine import (
                        LivePipelineBacktestEngine,
                    )
                    from backend.services.strategy_evolver import StrategyEvolver

                    if _cached_bars is None:
                        _cached_bars = StrategyEvolver._load_bars(symbol, timeframe, days=30)
                    if not _cached_bars or len(_cached_bars) < 50:
                        return -1.0  # 数据不足，给惩罚分

                    engine = LivePipelineBacktestEngine()
                    result = engine.run(
                        bars=_cached_bars,
                        pipeline_params=config,
                        tier=tier,
                    )
                    # fitness = Sharpe（越大越好）；回测失败/无交易给负分
                    sharpe = getattr(result, "sharpe_ratio", None)
                    if sharpe is not None and isinstance(sharpe, (int, float)):
                        return float(sharpe)
                    return -1.0
                except Exception as e:
                    logger.debug(f"[StrategyTuner] eval failed for {strategy_id}: {e}")
                    return -1.0

            b.optimizer.register_tunable(
                strategy_id, get_config, set_config, eval_fn=eval_config
            )
        except Exception as e:
            logger.debug(f"[StrategyTuner] register {strategy_id} skip: {e}")

    def auto_discover(self, db):
        """扫描所有 active 策略并注册为可调优对象"""
        b = self._bridge
        if not b._enabled:
            return
        try:
            from backend.database.models import AIStrategy
            strategies = db.query(AIStrategy).filter(
                AIStrategy.status.in_(["active", "running", "paused"])
            ).all()
            for strat in strategies:
                self.register_strategy(strat.strategy_id, db)
            logger.info(f"[StrategyTuner] auto_discover: {len(strategies)} strategies registered")
        except Exception as e:
            logger.warning(f"[StrategyTuner] auto_discover failed: {e}")


# ════════════════════════════════════════════════════════════════
#  QAABridge — 顶层单例编排
# ════════════════════════════════════════════════════════════════

class QAABridge:
    """QAA 进化系统的后端入口 — 所有 QAA 调用经此类转发"""

    _instance: Optional["QAABridge"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._enabled = False
        self.tracker = None
        self.feedback = None
        self.optimizer = None
        self.history = None
        self.outcome_adapter = OutcomeAdapter(self)
        self.strategy_tuner = StrategyTuner(self)
        self._grayscale_plans: Dict[str, GrayscalePlan] = {}
        self._plan_counter = 0

    @classmethod
    def get_instance(cls) -> "QAABridge":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def initialize(self, persist_dir: str = "./data/qaa_evolution"):
        """初始化 QAA 组件（使用独立实例，非模块单例）"""
        if not _lazy_imports():
            logger.warning("[QAABridge] QAA 包不可用，进化系统禁用")
            return

        try:
            persist_path = os.path.join(persist_dir, "evolution_history.jsonl")
            policy = _policy_cls(
                min_feedback_entries=10,
                degradation_threshold=-0.08,
                improvement_threshold=0.05,
                max_grayscale_pct=0.3,
                grayscale_step=0.1,
                observation_seconds=600,
                auto_rollback=True,
                max_concurrent_plans=3,
            )

            self.tracker = _tracker_cls(window_seconds=86400, max_records=50000)
            self.feedback = _feedback_cls(max_entries=100000, trend_threshold=0.03)
            self.history = _history_cls(max_entries=100000, persist_path=persist_path)
            self.optimizer = _optimizer_cls(
                feedback=self.feedback,
                tracker=self.tracker,
                history=self.history,
                policy=policy,
            )

            self._enabled = True
            logger.info(
                f"[QAABridge] 初始化完成 (persist={persist_path}, "
                f"policy=obs:{policy.observation_seconds}s, "
                f"max_grayscale:{policy.max_grayscale_pct:.0%})"
            )
        except Exception as e:
            logger.error(f"[QAABridge] 初始化失败: {e}")
            self._enabled = False

    # ── 灰度发布管理 ──

    def create_grayscale_plan(
        self,
        strategy_id: str,
        old_genome: Dict,
        new_genome: Dict,
        all_symbols: List[str],
        observation_seconds: float = 600,
    ) -> Optional[GrayscalePlan]:
        """创建灰度发布计划：按 symbol 划分 canary / control"""
        if not self._enabled:
            return None

        # 至少需要 2 个 symbol 才能做灰度
        if len(all_symbols) < 2:
            logger.info(
                f"[QAABridge] symbol 数量不足({len(all_symbols)})，跳过灰度，直接应用"
            )
            return None

        # 随机划分：1-2 个 canary，其余 control
        shuffled = list(all_symbols)
        random.shuffle(shuffled)
        canary_count = max(1, min(2, len(shuffled) // 3))
        canary_symbols = shuffled[:canary_count]
        control_symbols = shuffled[canary_count:]

        self._plan_counter += 1
        plan = GrayscalePlan(
            plan_id=f"gs-{self._plan_counter:04d}",
            strategy_id=strategy_id,
            old_genome=copy.deepcopy(old_genome),
            new_genome=copy.deepcopy(new_genome),
            canary_symbols=canary_symbols,
            control_symbols=control_symbols,
            observation_seconds=observation_seconds,
            observation_started_at=time.time(),
        )
        self._grayscale_plans[strategy_id] = plan

        logger.info(
            f"[QAABridge] 灰度计划 {plan.plan_id} 创建: "
            f"strategy={strategy_id}, "
            f"canary={canary_symbols}, control={control_symbols}, "
            f"观察期={observation_seconds}s"
        )
        return plan

    def get_genome_for_symbol(
        self, strategy_id: str, symbol: str
    ) -> Optional[Dict[str, Any]]:
        """灰度路由：根据 symbol 返回应使用的 genome（canary=新, control=旧）"""
        plan = self._grayscale_plans.get(strategy_id)
        if plan is None or plan.status != "observing":
            return None

        sym_upper = symbol.upper()
        canary_upper = [s.upper() for s in plan.canary_symbols]

        if sym_upper in canary_upper:
            return plan.new_genome
        else:
            return plan.old_genome

    def check_grayscale_plans(self, db):
        """评估所有活跃灰度计划 — 确认或回滚"""
        if not self._enabled:
            return

        now = time.time()
        to_finalize = []

        for strategy_id, plan in list(self._grayscale_plans.items()):
            if plan.status != "observing":
                continue

            elapsed = now - plan.observation_started_at
            if elapsed < plan.observation_seconds:
                continue  # 观察期未过

            # 观察期结束，评估 canary vs control
            to_finalize.append(plan)

        for plan in to_finalize:
            self._evaluate_grayscale_plan(plan, db)

    def _evaluate_grayscale_plan(self, plan: GrayscalePlan, db):
        """评估单个灰度计划"""
        try:
            from backend.database.models import StrategyTrade, AIStrategy

            cutoff = plan.observation_started_at

            # 查询 canary 和 control 的交易
            canary_trades = db.query(StrategyTrade).filter(
                StrategyTrade.strategy_id == plan.strategy_id,
                StrategyTrade.symbol.in_(
                    [s.upper() for s in plan.canary_symbols]
                    + [s.lower() for s in plan.canary_symbols]
                ),
                StrategyTrade.exit_time >= cutoff,
                StrategyTrade.pnl_pct.isnot(None),
            ).all()

            control_trades = db.query(StrategyTrade).filter(
                StrategyTrade.strategy_id == plan.strategy_id,
                StrategyTrade.symbol.in_(
                    [s.upper() for s in plan.control_symbols]
                    + [s.lower() for s in plan.control_symbols]
                ),
                StrategyTrade.exit_time >= cutoff,
                StrategyTrade.pnl_pct.isnot(None),
            ).all()

            canary_avg = (
                sum(t.pnl_pct for t in canary_trades) / len(canary_trades)
                if canary_trades else 0
            )
            control_avg = (
                sum(t.pnl_pct for t in control_trades) / len(control_trades)
                if control_trades else 0
            )

            total_trades = len(canary_trades) + len(control_trades)

            # 判断结果
            if total_trades < plan.min_trades_for_eval:
                # 数据不足 → 回滚
                self._rollback_grayscale(plan, db, "数据不足")
                return

            if canary_avg > control_avg + 0.005:
                # canary 表现更好 → 确认
                self._confirm_grayscale(plan, db, canary_avg, control_avg)
            elif canary_avg < control_avg - 0.01:
                # canary 表现更差 → 回滚
                self._rollback_grayscale(plan, db, "canary 表现劣于 control")
            else:
                # 差异不大 → 保守确认
                self._confirm_grayscale(plan, db, canary_avg, control_avg)

        except Exception as e:
            logger.error(f"[QAABridge] 灰度评估失败 {plan.plan_id}: {e}")
            self._rollback_grayscale(plan, db, f"评估异常: {e}")

    def _confirm_grayscale(self, plan: GrayscalePlan, db, canary_avg, control_avg):
        """确认灰度计划 — 全量应用新 genome"""
        try:
            from backend.database.models import AIStrategy
            strat = db.query(AIStrategy).filter(
                AIStrategy.strategy_id == plan.strategy_id
            ).first()
            if strat:
                strat.genome = plan.new_genome
                # 清除灰度标记
                if isinstance(strat.genome, dict):
                    strat.genome.pop("__grayscale__", None)
                if strat.status == "paused":
                    strat.status = "active"
                db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[QAABridge] 确认灰度失败: {e}")

        plan.status = "confirmed"
        self._grayscale_plans.pop(plan.strategy_id, None)

        self.history.record(
            target_id=plan.strategy_id,
            action="grayscale_confirmed",
            domain="trading",
            details={
                "plan_id": plan.plan_id,
                "canary_avg_pnl": round(canary_avg, 6),
                "control_avg_pnl": round(control_avg, 6),
            },
        )
        logger.info(
            f"[QAABridge] 灰度确认 {plan.plan_id}: "
            f"canary={canary_avg:.4f}, control={control_avg:.4f}"
        )

    def _rollback_grayscale(self, plan: GrayscalePlan, db, reason: str):
        """回滚灰度计划 — 恢复旧 genome"""
        try:
            from backend.database.models import AIStrategy
            strat = db.query(AIStrategy).filter(
                AIStrategy.strategy_id == plan.strategy_id
            ).first()
            if strat:
                strat.genome = plan.old_genome
                if isinstance(strat.genome, dict):
                    strat.genome.pop("__grayscale__", None)
                if strat.status == "paused":
                    strat.status = "active"
                db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[QAABridge] 回滚灰度失败: {e}")

        plan.status = "rolled_back"
        self._grayscale_plans.pop(plan.strategy_id, None)

        self.history.record(
            target_id=plan.strategy_id,
            action="grayscale_rollback",
            domain="trading",
            details={"plan_id": plan.plan_id, "reason": reason},
        )
        logger.warning(
            f"[QAABridge] 灰度回滚 {plan.plan_id}: {reason}"
        )

    # ── 定时优化周期 ──

    def run_optimization_cycle(self):
        """定时调用：检测退化 → 生成优化计划 → 灰度应用"""
        if not self._enabled or self.optimizer is None:
            return

        try:
            plans = self.optimizer.run_cycle()
            for p in plans:
                self.history.record(
                    target_id=p.target_id,
                    action=f"optimize_{p.status}",
                    domain="trading",
                    details={
                        "plan_id": p.plan_id,
                        "action": p.action,
                        "grayscale_pct": p.grayscale_pct,
                    },
                )
            if plans:
                logger.info(f"[QAABridge] 优化周期完成: {len(plans)} plans")
        except Exception as e:
            logger.debug(f"[QAABridge] 优化周期 skip: {e}")

    def feed_aggregate_metrics(self, db):
        """定时调用：推送 StrategyMemory 聚合指标到 PerformanceTracker"""
        if not self._enabled:
            return
        try:
            from backend.database.models import StrategyMemory
            memories = db.query(StrategyMemory).filter(
                StrategyMemory.total_trades > 0
            ).all()
            for mem in memories:
                self.outcome_adapter.feed_strategy_memory_snapshot(
                    mem.strategy_id, mem
                )
        except Exception as e:
            logger.debug(f"[QAABridge] aggregate metrics skip: {e}")

    # ── 重启恢复 ──

    def restore_grayscale_plans(self, db):
        """重启后从 genome 中的 __grayscale__ 标记恢复灰度计划"""
        if not self._enabled:
            return
        try:
            from backend.database.models import AIStrategy
            strategies = db.query(AIStrategy).filter(
                AIStrategy.status == "paused"
            ).all()
            for strat in strategies:
                gs = (strat.genome or {}).get("__grayscale__")
                if gs and isinstance(gs, dict):
                    plan = GrayscalePlan(
                        plan_id=gs.get("plan_id", f"gs-restore-{strat.strategy_id[:8]}"),
                        strategy_id=strat.strategy_id,
                        old_genome=gs.get("old_genome", {}),
                        new_genome=gs.get("new_genome", strat.genome or {}),
                        canary_symbols=gs.get("canary_symbols", []),
                        control_symbols=gs.get("control_symbols", []),
                        status="observing",
                        observation_started_at=gs.get("started_at", time.time()),
                        observation_seconds=gs.get("observation_seconds", 600),
                    )
                    self._grayscale_plans[strat.strategy_id] = plan
                    logger.info(
                        f"[QAABridge] 恢复灰度计划 {plan.plan_id} "
                        f"(strategy={strat.strategy_id})"
                    )
        except Exception as e:
            logger.debug(f"[QAABridge] 灰度恢复 skip: {e}")

    # ── 状态查询 ──

    def get_status(self) -> Dict[str, Any]:
        """返回 QAA 进化系统综合状态"""
        if not self._enabled:
            return {"enabled": False}

        return {
            "enabled": True,
            "tracker_records": self.tracker.record_count if self.tracker else 0,
            "feedback_entries": self.feedback.entry_count if self.feedback else 0,
            "history_entries": self.history.entry_count if self.history else 0,
            "optimizer_stats": self.optimizer.get_stats() if self.optimizer else {},
            "active_grayscale_plans": len(self._grayscale_plans),
            "grayscale_plans": [
                {
                    "plan_id": p.plan_id,
                    "strategy_id": p.strategy_id,
                    "canary": p.canary_symbols,
                    "control": p.control_symbols,
                    "status": p.status,
                    "elapsed": round(time.time() - p.observation_started_at, 0),
                }
                for p in self._grayscale_plans.values()
            ],
        }

    def shutdown(self):
        """关闭 QAA 进化系统"""
        self._enabled = False
        self._grayscale_plans.clear()
        logger.info("[QAABridge] 已关闭")


# ── 模块级单例 ──
qaa_bridge = QAABridge.get_instance()
