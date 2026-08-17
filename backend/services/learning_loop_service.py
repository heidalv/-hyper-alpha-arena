"""
LearningLoopService — AI 自动学习 & 自动进化 闭环中枢（P0-1）

本服务是 `UnifiedLearningService` / `SystemCoordinator` / `EvolutionScheduler` /
DRL / Kelly / PortfolioRiskAggregator 之间的**唯一定时反馈中枢**。主交易线程
只负责执行下单和平仓，学习、聚合、触发进化与重训都在本服务内通过三个
interval job 完成：

  * `_tick_outcome_batch`     （默认 5 min）: 扫描 `StrategyTrade` 最近 5 min 已
    平仓记录，批量走一遍 `UnifiedLearningService.process_outcome`，确保即使事件
    级 hook 遗漏，也能兜底反哺绩效矩阵 / 经验提炼。
  * `_tick_kelly_portfolio`   （默认 30 min）: 调 `SystemCoordinator.update_kelly_from_outcomes`
    做组合 Kelly + PortfolioRiskAggregator 聚合，把快照写入 `MultiSymbolKelly` +
    `SystemCoordinatorState.last_kelly_update_at`；供后续 `check_portfolio_risk`
    读取夹紧下单仓位。
  * `_tick_coordinator`       （默认 1 h）: 调 `SystemCoordinator.check_and_coordinate`
    得到 `CoordinationAction` 并路由到：
      - `trigger_evolution`    → `EvolutionScheduler.trigger_emergency_evolution("all_new")`
      - `trigger_drl_retrain`  → `drl_train_job.run_shadow_training()` 异步 shadow
      - `trigger_kelly_update` → 立即再跑一次 `update_kelly_from_outcomes` 兜底
    所有决策写入 `coordinator_actions` 表 + 广播 WS `coordinator_status`。

所有 tick 都通过 flag `LEARNING_LOOP_ENABLED` / `ENABLE_COORDINATOR` 控制；
任何阶段失败都只记录日志，绝不抛出到调度器线程。

API：通过 `/api/learning/loop/{status,metrics,pause,resume,trigger/{job}}` 暴露
（实现见 `api/learning_loop_routes.py`）。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)


# 任务 id 常量，与 scheduler 中的 task_id 对齐
JOB_OUTCOME_BATCH = "learning_loop_outcome_batch"
JOB_PAPER_OUTCOME_BACKFILL = "learning_loop_paper_outcome_backfill"
JOB_KELLY_PORTFOLIO = "learning_loop_kelly_portfolio"
JOB_COORDINATOR = "learning_loop_coordinator"
JOB_HEARTBEAT = "learning_loop_heartbeat"  # P2-1 WS 心跳
JOB_FACTOR_DECAY = "learning_loop_factor_decay"  # P0-2 因子衰减评估（接线 factor_decay_monitor）
JOB_LIVE_OUTCOME_BACKFILL = "learning_loop_live_outcome_backfill"  # P1-4 live 仓位级 7 天补扫

# 默认 tick 周期（秒），可被 .env 覆盖
DEFAULT_INTERVALS: Dict[str, int] = {
    JOB_OUTCOME_BATCH: 5 * 60,
    JOB_PAPER_OUTCOME_BACKFILL: 10 * 60,
    JOB_KELLY_PORTFOLIO: 30 * 60,
    JOB_COORDINATOR: 60 * 60,
    JOB_HEARTBEAT: 30,  # P2-1 每 30s 推一次 coordinator_status
    JOB_FACTOR_DECAY: 6 * 3600,  # P0-2 每 6h 评估因子衰减（可 env 覆盖）
    JOB_LIVE_OUTCOME_BACKFILL: 10 * 60,  # P1-4 live 补扫每 10min
}

_METRIC_HISTORY = 200


class LearningLoopService:
    """定时批处理中枢（线程安全单例）"""

    _instance: Optional["LearningLoopService"] = None
    _instance_lock = threading.Lock()

    # ─────────────────────────────
    #  单例
    # ─────────────────────────────

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._paused = False
        self._registered = False
        self._state_lock = threading.Lock()
        # 每个 job 的最近运行情况（timestamp, elapsed_ms, success, extra_dict）
        self._metrics: Dict[str, Deque[Dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=_METRIC_HISTORY)
        )
        self._last_tick_at: Dict[str, Optional[datetime]] = {
            JOB_OUTCOME_BATCH: None,
            JOB_PAPER_OUTCOME_BACKFILL: None,
            JOB_KELLY_PORTFOLIO: None,
            JOB_COORDINATOR: None,
            JOB_HEARTBEAT: None,
            JOB_FACTOR_DECAY: None,
            JOB_LIVE_OUTCOME_BACKFILL: None,
        }
        self._last_coord_action: Dict[str, Any] = {}
        logger.info("[LearningLoop] 实例化完成")

    # ─────────────────────────────
    #  调度注册
    # ─────────────────────────────

    def register_tasks(self) -> None:
        """把三个 tick 挂到全局 `task_scheduler`（与 EvolutionScheduler 共用）。"""
        if self._registered:
            return
        if not self._flag_enabled():
            logger.info("[LearningLoop] LEARNING_LOOP_ENABLED=false，不注册定时任务")
            return

        try:
            from backend.services.scheduler import task_scheduler
            if not task_scheduler.is_running():
                task_scheduler.start()

            intervals = self._resolve_intervals()
            task_scheduler.add_interval_task(
                task_func=self._tick_outcome_batch,
                interval_seconds=intervals[JOB_OUTCOME_BATCH],
                task_id=JOB_OUTCOME_BATCH,
            )
            task_scheduler.add_interval_task(
                task_func=self._tick_paper_outcome_backfill,
                interval_seconds=intervals[JOB_PAPER_OUTCOME_BACKFILL],
                task_id=JOB_PAPER_OUTCOME_BACKFILL,
            )
            task_scheduler.add_interval_task(
                task_func=self._tick_kelly_portfolio,
                interval_seconds=intervals[JOB_KELLY_PORTFOLIO],
                task_id=JOB_KELLY_PORTFOLIO,
            )
            task_scheduler.add_interval_task(
                task_func=self._tick_coordinator,
                interval_seconds=intervals[JOB_COORDINATOR],
                task_id=JOB_COORDINATOR,
            )
            # P2-1 心跳：高频广播 coordinator_status 给前端 Banner
            task_scheduler.add_interval_task(
                task_func=self._tick_heartbeat,
                interval_seconds=intervals[JOB_HEARTBEAT],
                task_id=JOB_HEARTBEAT,
            )
            # P0-2 因子衰减评估：evaluate_all_factors 消费 record_ic 累积的 IC 历史，
            # 产出 DecayStatus 供 get_factor_weight_penalty 在实盘信号加权层生效。
            task_scheduler.add_interval_task(
                task_func=self._tick_factor_decay,
                interval_seconds=intervals[JOB_FACTOR_DECAY],
                task_id=JOB_FACTOR_DECAY,
            )
            # P1-4 live 仓位级补扫：宕机>10min 期间漏掉的 live 平仓反馈（事件级 hook
            # 失败时连 StrategyTrade 都没有）从 AIDecisionLog 补齐。
            task_scheduler.add_interval_task(
                task_func=self._tick_live_outcome_backfill,
                interval_seconds=intervals[JOB_LIVE_OUTCOME_BACKFILL],
                task_id=JOB_LIVE_OUTCOME_BACKFILL,
            )
            self._registered = True
            logger.info(
                f"[LearningLoop] tick 已注册：outcome={intervals[JOB_OUTCOME_BATCH]}s "
                f"paper_backfill={intervals[JOB_PAPER_OUTCOME_BACKFILL]}s "
                f"kelly={intervals[JOB_KELLY_PORTFOLIO]}s "
                f"coord={intervals[JOB_COORDINATOR]}s "
                f"heartbeat={intervals[JOB_HEARTBEAT]}s"
            )

            # 启动时尝试恢复 DRL 训练冷却
            try:
                from backend.services.rl.drl_train_job import _restore_cooldown_from_db
                _restore_cooldown_from_db()
            except Exception as e:
                # [2026-08-05 v6 8.3 阶段1] 静默→告警：DRL 冷却恢复失败必须可见
                logger.warning(f"[LearningLoop] 恢复 DRL 冷却失败: {e}")
        except Exception as e:
            logger.error(f"[LearningLoop] register_tasks 失败: {e}", exc_info=True)

    @staticmethod
    def _flag_enabled() -> bool:
        try:
            from backend.config import settings
            return bool(getattr(settings, "LEARNING_LOOP_ENABLED", True))
        except Exception:
            return True

    @staticmethod
    def _resolve_intervals() -> Dict[str, int]:
        """允许通过 settings 覆盖默认 tick 周期（以秒为单位）。"""
        try:
            from backend.config import settings
            return {
                JOB_OUTCOME_BATCH: int(getattr(
                    settings, "LEARNING_LOOP_OUTCOME_INTERVAL_S",
                    DEFAULT_INTERVALS[JOB_OUTCOME_BATCH],
                )),
                JOB_PAPER_OUTCOME_BACKFILL: int(getattr(
                    settings, "LEARNING_LOOP_PAPER_BACKFILL_INTERVAL_S",
                    DEFAULT_INTERVALS[JOB_PAPER_OUTCOME_BACKFILL],
                )),
                JOB_KELLY_PORTFOLIO: int(getattr(
                    settings, "LEARNING_LOOP_KELLY_INTERVAL_S",
                    DEFAULT_INTERVALS[JOB_KELLY_PORTFOLIO],
                )),
                JOB_COORDINATOR: int(getattr(
                    settings, "LEARNING_LOOP_COORD_INTERVAL_S",
                    DEFAULT_INTERVALS[JOB_COORDINATOR],
                )),
                JOB_HEARTBEAT: int(getattr(
                    settings, "LEARNING_LOOP_HEARTBEAT_INTERVAL_S",
                    DEFAULT_INTERVALS[JOB_HEARTBEAT],
                )),
                JOB_FACTOR_DECAY: int(getattr(
                    settings, "LEARNING_LOOP_FACTOR_DECAY_INTERVAL_S",
                    DEFAULT_INTERVALS[JOB_FACTOR_DECAY],
                )),
                JOB_LIVE_OUTCOME_BACKFILL: int(getattr(
                    settings, "LEARNING_LOOP_LIVE_BACKFILL_INTERVAL_S",
                    DEFAULT_INTERVALS[JOB_LIVE_OUTCOME_BACKFILL],
                )),
            }
        except Exception:
            return dict(DEFAULT_INTERVALS)

    def _tick_heartbeat(self) -> None:
        """P2-1 — 每 30s 把 status() 广播给前端 coordinator_status topic。"""
        if self._paused:
            return
        job = JOB_HEARTBEAT
        t0 = time.time()
        success = True
        try:
            self._broadcast_coord_status()
        except Exception as e:
            success = False
            # [2026-08-05 v6 8.3 阶段1] 静默→告警：心跳失败=WS 链路断，必须可见
            logger.warning(f"[LearningLoop] heartbeat 失败: {e}")
        finally:
            self._record_tick(job, t0, success, {})

    def _tick_live_outcome_backfill(self) -> None:
        """P1-4 — live 仓位级 7 天补扫（每 10min）。

        修复：live 平仓若在 close→process_outcome 之间崩溃/宕机（>10min），
        该笔交易永远不进 UnifiedLearning（原 600s 兜底只扫 StrategyTrade，
        而 StrategyTrade 恒带 _learning_loop_processed=true，是死路）。
        本 tick 从 AIDecisionLog（realized_pnl + pnl_updated_at）补齐，
        按 decision_log_id 去重（process_outcome 幂等 + decision_context 键）。
        """
        if self._paused:
            return
        job = JOB_LIVE_OUTCOME_BACKFILL
        t0 = time.time()
        success = True
        extra: Dict[str, Any] = {}
        try:
            extra = self._do_live_outcome_backfill(days=7)
        except Exception as e:
            success = False
            logger.warning(f"[LearningLoop] live_outcome_backfill 失败: {e}")
        finally:
            self._record_tick(job, t0, success, extra)

    def _do_live_outcome_backfill(self, days: int = 7) -> Dict[str, Any]:
        """从 AIDecisionLog 扫描已平仓 live 决策，补齐未写 StrategyTrade 的学习结果。"""
        from sqlalchemy import cast
        from sqlalchemy.types import Text
        from backend.database.connection import SessionLocal, AnalyticsSessionLocal
        from backend.database.models import AIDecisionLog, StrategyTrade
        from backend.services.unified_learning_service import unified_learning, TradeOutcome

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        scanned = backfilled = skipped_existing = skipped_no_strategy = failed = 0

        _ana_db = None
        rows = []
        try:
            _ana_db = AnalyticsSessionLocal()
            rows = (
                _ana_db.query(AIDecisionLog)
                .filter(
                    AIDecisionLog.executed == "true",
                    AIDecisionLog.operation.in_(["buy", "sell"]),
                    AIDecisionLog.realized_pnl.isnot(None),
                    AIDecisionLog.pnl_updated_at.isnot(None),
                    AIDecisionLog.pnl_updated_at >= cutoff.replace(tzinfo=None),
                )
                .order_by(AIDecisionLog.pnl_updated_at.desc())
                .limit(500)
                .all()
            )
        except Exception as e:
            logger.debug("[LearningLoop] live 补扫查询失败: %s", e)
        finally:
            if _ana_db is not None:
                try:
                    _ana_db.close()
                except Exception:
                    pass

        scanned = len(rows)
        if not rows:
            return {"scanned": 0, "backfilled": 0, "skipped_existing": 0, "failed": 0}

        db = SessionLocal()
        try:
            for log in rows:
                _sid = (log.ai_strategy_id or "").strip()
                if not _sid:
                    skipped_no_strategy += 1
                    continue
                existing = (
                    db.query(StrategyTrade)
                    .filter(
                        StrategyTrade.strategy_id == _sid,
                        cast(StrategyTrade.decision_context, Text).contains(
                            f'"decision_log_id": {log.id}'
                        ),
                    )
                    .first()
                )
                if existing is not None:
                    skipped_existing += 1
                    continue
                try:
                    _pnl = float(log.realized_pnl or 0)
                    _side = "long" if (log.operation or "").strip().lower() == "buy" else "short"
                    _notional = 0.0
                    try:
                        _prev = float(log.prev_portion or 0)
                        _bal = float(log.total_balance or 0)
                        _notional = _prev * _bal
                    except Exception:
                        _notional = 0.0
                    _pnl_pct = (_pnl / _notional) if _notional > 0 else 0.0
                    outcome = TradeOutcome(
                        source="live",
                        strategy_id=_sid,
                        symbol=log.symbol or "",
                        side=_side,
                        tier="mid",
                        trade_nature="",
                        entry_price=0.0,
                        exit_price=0.0,
                        pnl=_pnl,
                        pnl_pct=float(_pnl_pct),
                        duration_seconds=0,
                        regime_at_entry="unknown",
                        regime_at_exit="unknown",
                        confidence=0.6,
                        position_size=0.0,
                        metadata={
                            "loop_backfill": True,
                            "close_reason": "live_backfill",
                            "decision_log_id": log.id,
                            "data_source": "aiddecisionlog_backfill",
                            "market_type": "perp",
                            "leverage": 1.0,
                        },
                        persist_trade=True,
                    )
                    unified_learning.process_outcome(db, outcome)
                    backfilled += 1
                except Exception as e:
                    failed += 1
                    logger.warning(
                        "[LearningLoop] live_outcome_backfill 单笔失败 log=%s: %s",
                        getattr(log, "id", None), e,
                    )
        finally:
            db.close()

        return {
            "scanned": scanned,
            "backfilled": backfilled,
            "skipped_existing": skipped_existing,
            "skipped_no_strategy": skipped_no_strategy,
            "failed": failed,
        }

    def _tick_factor_decay(self) -> None:
        """P0-2 — 每 6h 评估全部因子衰减状态。

        修复：factor_decay_monitor.evaluate_all_factors 此前全库无调用点，
        _decay_status 永不填充 → get_factor_weight_penalty 恒返回 1.0，
        衰减因子永远满权重参与实盘合成。本 tick 把评估接入调度并持久化状态。
        """
        if self._paused:
            return
        job = JOB_FACTOR_DECAY
        t0 = time.time()
        success = True
        extra: Dict[str, Any] = {}
        try:
            from backend.services.factor_engine.factor_decay_monitor import decay_monitor
            results = decay_monitor.evaluate_all_factors()
            extra = {
                "evaluated": len(results),
                "retired": sum(1 for s in results.values() if s.recommendation == "retire"),
                "reduced": sum(1 for s in results.values() if s.recommendation == "reduce"),
            }
        except Exception as e:
            success = False
            # 衰减评估失败必须可见（因子权重保护失效）
            logger.warning(f"[LearningLoop] factor_decay 评估失败: {e}")
        finally:
            self._record_tick(job, t0, success, extra)

    # ─────────────────────────────
    #  外部控制（pause/resume/trigger）
    # ─────────────────────────────

    def pause(self) -> None:
        with self._state_lock:
            self._paused = True
        logger.info("[LearningLoop] 已暂停所有 tick")

    def resume(self) -> None:
        with self._state_lock:
            self._paused = False
        logger.info("[LearningLoop] 已恢复所有 tick")

    @property
    def is_paused(self) -> bool:
        return self._paused

    def trigger_job(self, job: str) -> Dict[str, Any]:
        """手动触发一个 tick（测试 / 运维用）。"""
        mapping = {
            "outcome_batch": self._tick_outcome_batch,
            "paper_outcome_backfill": self._tick_paper_outcome_backfill,
            "kelly_portfolio": self._tick_kelly_portfolio,
            "coordinator": self._tick_coordinator,
            "factor_decay": self._tick_factor_decay,
            "live_outcome_backfill": self._tick_live_outcome_backfill,
        }
        fn = mapping.get(job)
        if fn is None:
            return {"ok": False, "error": f"unknown job: {job}"}
        t0 = time.time()
        try:
            fn()
            return {"ok": True, "elapsed_ms": int((time.time() - t0) * 1000)}
        except Exception as e:
            logger.error(f"[LearningLoop] 手动触发 {job} 失败: {e}", exc_info=True)
            return {"ok": False, "error": str(e)}

    # ─────────────────────────────
    #  状态 / 指标查询
    # ─────────────────────────────

    def last_tick_map(self) -> Dict[str, Optional[str]]:
        """[2026-08-05 v6 8.3 阶段1] 线程安全导出各 job 最近活动时间（ISO）。

        供 learning_health_service 判定超时标红：每条闭环最后活动时间
        超过间隔阈值即 ok→warn→dead。
        """
        with self._state_lock:
            return {
                k: (v.isoformat() if v else None)
                for k, v in self._last_tick_at.items()
            }

    def status(self) -> Dict[str, Any]:
        intervals = self._resolve_intervals()
        with self._state_lock:
            last = {k: (v.isoformat() if v else None) for k, v in self._last_tick_at.items()}
            coord_action = dict(self._last_coord_action or {})
        next_at = {}
        for k, v in self._last_tick_at.items():
            if v is None:
                next_at[k] = None
                continue
            gap = intervals.get(k, 0)
            if not gap:
                next_at[k] = None
                continue
            nxt = v + timedelta(seconds=gap)
            next_at[k] = nxt.isoformat()
        return {
            "enabled": self._flag_enabled(),
            "paused": self._paused,
            "registered": self._registered,
            "intervals": intervals,
            "last_tick_at": last,
            "next_tick_at": next_at,
            "last_coord_action": coord_action,
        }

    def metrics(self) -> Dict[str, Any]:
        def _summary(dq: Deque[Dict[str, Any]]) -> Dict[str, Any]:
            if not dq:
                return {
                    "count": 0,
                    "success_rate": 0.0,
                    "p50_ms": 0,
                    "p95_ms": 0,
                    "last_elapsed_ms": 0,
                }
            elapsed = sorted(x["elapsed_ms"] for x in dq)
            succ = sum(1 for x in dq if x.get("success"))
            n = len(elapsed)
            p50 = elapsed[n // 2]
            p95 = elapsed[min(n - 1, int(n * 0.95))]
            return {
                "count": n,
                "success_rate": round(succ / n, 4),
                "p50_ms": p50,
                "p95_ms": p95,
                "last_elapsed_ms": dq[-1]["elapsed_ms"],
                "last_extra": dq[-1].get("extra", {}),
            }
        with self._state_lock:
            snap = {k: list(v) for k, v in self._metrics.items()}
        return {k: _summary(deque(v)) for k, v in snap.items()}

    # ─────────────────────────────
    #  Tick 1: 交易结果批处理（兜底 P0-7）
    # ─────────────────────────────

    def _tick_outcome_batch(self) -> None:
        if self._paused:
            return
        job = JOB_OUTCOME_BATCH
        t0 = time.time()
        success = True
        extra: Dict[str, Any] = {}
        try:
            extra = self._do_outcome_batch()
        except Exception as e:
            success = False
            logger.error(f"[LearningLoop] {job} 异常: {e}", exc_info=True)
        finally:
            self._record_tick(job, t0, success, extra)

    def _do_outcome_batch(self) -> Dict[str, Any]:
        """扫描最近 5min（略有冗余窗口）已结算 StrategyTrade → 重放到 UnifiedLearning。

        UnifiedLearning.process_outcome 本身是幂等写库 + EMA 更新，若事件级
        hook 已经处理过同一 trade，这里不会造成重复写入（只影响 EMA 的权重）。
        因此我们仅在 `_learning_loop_processed=true` 未标记时处理，避免重复累加。
        """
        from backend.database.connection import SessionLocal
        from backend.database.models import StrategyTrade
        from backend.services.unified_learning_service import (
            unified_learning, TradeOutcome,
        )

        intervals = self._resolve_intervals()
        window_s = max(intervals[JOB_OUTCOME_BATCH] * 2, 300)
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_s)

        processed = 0
        skipped = 0
        db = SessionLocal()
        try:
            # 深挖第 3 轮 (2026-05-08)：排除历史 legacy_dirty 污染数据
            from sqlalchemy import cast
            from sqlalchemy.types import Text
            rows = db.query(StrategyTrade).filter(
                StrategyTrade.status == "closed",
                StrategyTrade.closed_at.isnot(None),
                StrategyTrade.closed_at >= cutoff.replace(tzinfo=None),
                (StrategyTrade.decision_context.is_(None))
                | (~cast(StrategyTrade.decision_context, Text).contains('"legacy_dirty": true')),
            ).order_by(StrategyTrade.closed_at.desc()).limit(500).all()

            for t in rows:
                ctx = t.decision_context or {}
                already = bool(ctx.get("_learning_loop_processed"))
                if already:
                    skipped += 1
                    continue

                try:
                    outcome = TradeOutcome(
                        source=str(ctx.get("source") or "live"),
                        strategy_id=t.strategy_id or "",
                        template_id=str(ctx.get("template_id") or ""),
                        symbol=t.symbol or "",
                        side=t.side or "",
                        tier=str(ctx.get("tier") or "mid"),
                        trade_nature=str(ctx.get("nature") or ""),
                        entry_price=float(t.entry_price or 0.0),
                        exit_price=float(t.exit_price or 0.0),
                        pnl=float(t.pnl or 0.0),
                        pnl_pct=float(t.pnl_pct or 0.0),
                        duration_seconds=int(t.holding_period or 0),
                        regime_at_entry=str(ctx.get("regime") or "ranging"),
                        # [P1-12 标签卫生] 原实现把 entry regime 复制为 exit regime，
                        # 区制条件学习统计被污染。平仓时真实区制若未记录则诚实标 unknown。
                        regime_at_exit=str(ctx.get("regime_at_exit") or "unknown"),
                        confidence=float(ctx.get("confidence") or 0.6),
                        metadata={"loop_backfill": True},
                        # 关键修复：回填路径不允许再生成新的 StrategyTrade，
                        # 否则 5min 后又会被扫到，形成 ghost 放大循环。
                        persist_trade=False,
                    )
                    unified_learning.process_outcome(db, outcome)
                    new_ctx = dict(ctx)
                    new_ctx["_learning_loop_processed"] = True
                    t.decision_context = new_ctx
                    db.add(t)
                    processed += 1
                except Exception as e:
                    # [2026-08-05 v6 8.3 阶段1] 静默→告警：单笔回填失败必须可见
                    logger.warning(f"[LearningLoop] outcome_batch 单笔失败: {e}")

            db.commit()
        finally:
            db.close()

        # —— RAG 增量索引（trade_decisions，近 1 天）——
        # 把 5 min 的学习刷新同步推给本地知识库，避免依赖
        # daily_signal_weight_update 的每日刷新带来 24h 延迟。
        # 仅在"确实有新 processed 交易"时触发，避免空转 IO。
        rag_indexed = 0
        rag_elapsed_ms: Optional[int] = None
        rag_skipped_reason: Optional[str] = None
        if processed > 0:
            _rag_t0 = time.time()
            _rag_db = None
            try:
                from backend.services.rag_knowledge_service import rag_knowledge_service
                if not getattr(rag_knowledge_service, "is_ready", False):
                    rag_skipped_reason = "rag_not_ready"
                else:
                    from backend.database.connection import SessionLocal as _SL
                    _rag_db = _SL()
                    rag_indexed = int(
                        rag_knowledge_service.index_from_db(
                            _rag_db,
                            "trade_decisions",
                            incremental=True,
                            days=1,
                        )
                        or 0
                    )
            except Exception as _rag_err:
                # RAG 失败不影响主 tick 成功率
                rag_skipped_reason = f"exception:{_rag_err}"
                logger.warning(
                    f"[LearningLoop] RAG 增量索引失败: {_rag_err}",
                    exc_info=False,
                )
            finally:
                if _rag_db is not None:
                    try:
                        _rag_db.close()
                    except Exception:
                        pass
                rag_elapsed_ms = int((time.time() - _rag_t0) * 1000)

        extra: Dict[str, Any] = {
            "scanned": len(rows) if "rows" in locals() else 0,
            "processed": processed,
            "skipped_already": skipped,
            "rag_indexed": rag_indexed,
        }
        if rag_elapsed_ms is not None:
            extra["rag_elapsed_ms"] = rag_elapsed_ms
        if rag_skipped_reason is not None:
            extra["rag_skipped"] = rag_skipped_reason

        # ── QAA 进化系统：推送 StrategyMemory 聚合指标 ──
        if processed > 0:
            try:
                from backend.services.qaa_evolution_bridge import qaa_bridge
                if qaa_bridge._enabled:
                    _qaa_db = None
                    try:
                        from backend.database.connection import SessionLocal as _QAA_SL
                        _qaa_db = _QAA_SL()
                        qaa_bridge.feed_aggregate_metrics(_qaa_db)
                    finally:
                        if _qaa_db:
                            _qaa_db.close()
            except Exception:
                pass

        # ── DRL 影子预测回填（2026-08-09 打通）──
        # 预测落定窗口（5 根 1h bar）后回填 actual_direction/is_correct，
        # 供 SystemCoordinator._should_retrain_drl 的准确率判据使用；
        # 与 outcome 批处理同频兜底，失败不影响主 tick。
        drl_backfilled = 0
        try:
            from backend.services.rl.drl_performance_backfill import backfill_pending
            _drl_db = None
            try:
                from backend.database.connection import SessionLocal as _DRL_SL
                _drl_db = _DRL_SL()
                drl_backfilled = int(backfill_pending(_drl_db) or 0)
            finally:
                if _drl_db:
                    _drl_db.close()
        except Exception as _drl_err:
            logger.warning(f"[LearningLoop] DRL 影子回填失败: {_drl_err}")

        extra["drl_backfilled"] = drl_backfilled
        return extra

    # ─────────────────────────────
    # Tick 1b: Paper 平仓补偿扫描
    # ─────────────────────────────

    def _tick_paper_outcome_backfill(self) -> None:
        if self._paused:
            return
        job = JOB_PAPER_OUTCOME_BACKFILL
        t0 = time.time()
        success = True
        extra: Dict[str, Any] = {}
        try:
            extra = self._do_paper_outcome_backfill()
        except Exception as e:
            success = False
            logger.error(f"[LearningLoop] {job} 异常: {e}", exc_info=True)
        finally:
            self._record_tick(job, t0, success, extra)

    @staticmethod
    def _paper_position_pnl(pos) -> float:
        """closed 仓位已实现盈亏。

        [P0-6 权威口径] paper_trading_engine.close_position 落库时把 unrealized_pnl
        复用为「全仓已实现盈亏（已含分批 partial_realized_pnl）」，因此 closed 仓位
        直接取 unrealized_pnl，禁止再叠加 partial_realized_pnl（否则分批止盈仓位双计，
        小亏会被误判为盈利）。仅当该列缺失时才按价格差兜底重算。
        """
        _stored = getattr(pos, "unrealized_pnl", None)
        if _stored is not None:
            return float(_stored or 0)
        partial = float(getattr(pos, "partial_realized_pnl", 0) or 0)
        entry = float(getattr(pos, "entry_price", 0) or 0)
        close = float(getattr(pos, "close_price", 0) or 0)
        size = float(getattr(pos, "size", 0) or 0)
        if entry <= 0 or close <= 0 or size <= 0:
            return partial
        if str(getattr(pos, "side", "")).lower() == "short":
            full = (entry - close) * size
        else:
            full = (close - entry) * size
        return full + partial

    def _do_paper_outcome_backfill(self, days: int = 7) -> Dict[str, Any]:
        """扫描已平仓 paper_positions，补齐未写入 strategy_trades 的学习结果。"""
        from sqlalchemy import cast
        from sqlalchemy.types import Text
        from backend.database.connection import SessionLocal
        from backend.database.models import PaperPosition, StrategyTrade
        from backend.services.unified_learning_service import unified_learning, TradeOutcome

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        scanned = 0
        backfilled = 0
        skipped_existing = 0
        skipped_no_strategy = 0
        failed = 0
        last_error = ""

        db = SessionLocal()
        try:
            rows = (
                db.query(PaperPosition)
                .filter(
                    PaperPosition.status == "closed",
                    PaperPosition.closed_at.isnot(None),
                    PaperPosition.closed_at >= cutoff.replace(tzinfo=None),
                )
                .order_by(PaperPosition.closed_at.desc())
                .limit(1000)
                .all()
            )
            scanned = len(rows)
            for pos in rows:
                pos_id = getattr(pos, "id", None)
                strategy_id = getattr(pos, "strategy_id", None) or ""
                if not strategy_id:
                    skipped_no_strategy += 1
                    continue

                existing = (
                    db.query(StrategyTrade)
                    .filter(
                        StrategyTrade.strategy_id == strategy_id,
                        cast(StrategyTrade.decision_context, Text).contains(
                            f'"paper_position_id": {pos_id}'
                        ),
                    )
                    .first()
                )
                if existing is not None:
                    skipped_existing += 1
                    continue

                try:
                    entry = float(pos.entry_price or 0)
                    close = float(pos.close_price or pos.mark_price or 0)
                    size = float(pos.original_size or pos.size or 0)
                    if entry <= 0 or close <= 0 or size <= 0:
                        skipped_no_strategy += 1
                        continue

                    pnl = self._paper_position_pnl(pos)
                    notional = max(entry * size, 1e-9)
                    pnl_pct = pnl / notional
                    duration = 0
                    if pos.opened_at and pos.closed_at:
                        o = pos.opened_at
                        c = pos.closed_at
                        if o.tzinfo is None:
                            o = o.replace(tzinfo=timezone.utc)
                        if c.tzinfo is None:
                            c = c.replace(tzinfo=timezone.utc)
                        duration = max(0, int((c - o).total_seconds()))

                    try:
                        from backend.services.exchange_config import get_active_exchange
                        exchange = get_active_exchange() or "unknown"
                    except Exception:
                        exchange = "unknown"

                    outcome = TradeOutcome(
                        source="paper",
                        strategy_id=strategy_id,
                        symbol=str(pos.symbol or ""),
                        side=str(pos.side or ""),
                        tier=str(getattr(pos, "timeframe_tier", None) or "mid"),
                        trade_nature=str(getattr(pos, "trade_nature", None) or ""),
                        entry_price=entry,
                        exit_price=close,
                        pnl=float(pnl),
                        pnl_pct=float(pnl_pct),
                        duration_seconds=duration,
                        regime_at_entry="unknown",
                        regime_at_exit="unknown",
                        confidence=0.6,
                        position_size=size,
                        opened_at=pos.opened_at,
                        metadata={
                            "loop_backfill": True,
                            "close_reason": getattr(pos, "close_reason", None),
                            "paper_position_id": pos_id,
                            "closed_at": pos.closed_at.isoformat() if pos.closed_at else None,
                            "exchange": exchange,
                            "market_type": "perp",
                            "data_source": "paper_position_backfill",
                            "leverage": float(getattr(pos, "leverage", 1.0) or 1.0),
                        },
                        persist_trade=True,
                    )
                    unified_learning.process_outcome(db, outcome)
                    backfilled += 1
                except Exception as e:
                    failed += 1
                    last_error = str(e)
                    logger.warning(
                        f"[LearningLoop] paper_outcome_backfill 单笔失败: "
                        f"pos={pos_id} strategy={strategy_id} err={e}"
                    )

        finally:
            db.close()

        return {
            "scanned": scanned,
            "backfilled": backfilled,
            "skipped_existing": skipped_existing,
            "skipped_no_strategy": skipped_no_strategy,
            "failed": failed,
            "last_error": last_error,
        }

    # ─────────────────────────────
    #  Tick 2: 组合 Kelly 聚合
    # ─────────────────────────────

    def _tick_kelly_portfolio(self) -> None:
        if self._paused:
            return
        job = JOB_KELLY_PORTFOLIO
        t0 = time.time()
        success = True
        extra: Dict[str, Any] = {}
        try:
            extra = self._do_kelly_portfolio()
        except Exception as e:
            success = False
            logger.error(f"[LearningLoop] {job} 异常: {e}", exc_info=True)
        finally:
            self._record_tick(job, t0, success, extra)

    def _do_kelly_portfolio(self) -> Dict[str, Any]:
        from backend.config.settings import ENABLE_KELLY_POSITION
        if not ENABLE_KELLY_POSITION:
            return {"skipped": "ENABLE_KELLY_POSITION=false"}

        from backend.database.connection import SessionLocal
        from backend.services.rl.system_coordinator import system_coordinator

        db = SessionLocal()
        try:
            system_coordinator.update_kelly_from_outcomes(db)
        finally:
            db.close()
        return {"updated": True}

    # ─────────────────────────────
    #  Tick 3: 协调器
    # ─────────────────────────────

    def _tick_coordinator(self) -> None:
        if self._paused:
            return
        job = JOB_COORDINATOR
        t0 = time.time()
        success = True
        extra: Dict[str, Any] = {}
        try:
            extra = self._do_coordinator()
        except Exception as e:
            success = False
            logger.error(f"[LearningLoop] {job} 异常: {e}", exc_info=True)
        finally:
            self._record_tick(job, t0, success, extra)
            # 更新 SystemCoordinatorState.last_loop_tick_at（P1-3）
            self._update_last_loop_tick_at()
            # 广播到 WS（P2-1）
            self._broadcast_coord_status()

    def _do_coordinator(self) -> Dict[str, Any]:
        from backend.config.settings import ENABLE_COORDINATOR
        if not ENABLE_COORDINATOR:
            return {"skipped": "ENABLE_COORDINATOR=false"}

        from backend.database.connection import SessionLocal
        from backend.services.rl.system_coordinator import system_coordinator

        db = SessionLocal()
        triggered: List[str] = []
        skipped: List[str] = []
        try:
            action = system_coordinator.check_and_coordinate(db)

            # 1) Kelly：立即重跑，兜底 _tick_kelly_portfolio 的冷启动窗口
            if action.trigger_kelly_update:
                try:
                    system_coordinator.update_kelly_from_outcomes(db)
                    triggered.append("kelly_update")
                except Exception as e:
                    skipped.append(f"kelly_update:{e}")

            # 2) DRL 重训：调 shadow 训练，带 2h 冷却（P1-4）
            if action.trigger_drl_retrain:
                try:
                    from backend.config import settings as _s
                    drl_auto = bool(getattr(_s, "DRL_RETRAIN_AUTO", True))
                except Exception:
                    drl_auto = True
                if not drl_auto:
                    skipped.append("drl_retrain:disabled_by_flag")
                else:
                    try:
                        from backend.services.rl.drl_train_job import run_shadow_training
                        r = run_shadow_training()
                        if r.get("started"):
                            triggered.append(f"drl_retrain:{r.get('task_id')}")
                        else:
                            skipped.append(f"drl_retrain:{r.get('reason')}")
                    except Exception as e:
                        skipped.append(f"drl_retrain:{e}")

            # 3) 紧急进化：通过 OpenCode 分析路由，降级直接触发（V1 双轨已移除）
            if action.trigger_evolution:
                try:
                    _reason_str = "; ".join(action.reasons or []) or "coordinator"
                    # 先尝试 OpenCode 分析
                    try:
                        from backend.database.connection import SessionLocal as _OCDB
                        from backend.services.opencode_bridge import run_scheduled_analysis
                        _oc_db = _OCDB()
                        try:
                            analysis = run_scheduled_analysis(_oc_db, window="6h", domain="ai")
                            if not analysis or analysis.get("skipped"):
                                raise ValueError("OpenCode analysis skipped")
                            triggered.append("opencode_routed_evolution")
                        finally:
                            _oc_db.close()
                    except Exception as _oc_fallback:
                        # 降级: 直接触发紧急进化
                        # [2026-08-05 v6 8.3 阶段1] 静默→告警：路由降级必须可见
                        logger.warning("[LearningLoop] OpenCode 路由降级: %s", _oc_fallback)
                        from backend.services.evolution_scheduler import evolution_scheduler
                        result = evolution_scheduler.trigger_emergency_evolution(
                            template_id="all_new", reason=_reason_str,
                        )
                        if isinstance(result, dict) and result.get("started"):
                            triggered.append("emergency_evolution(fallback)")
                        elif isinstance(result, dict):
                            _reason = result.get("reason", "unknown")
                            skipped.append(f"evolution_fallback:{_reason}")
                        else:
                            triggered.append("emergency_evolution(fallback)")
                except Exception as e:
                    skipped.append(f"evolution:{e}")

            # 记录 coordinator_actions
            system_coordinator.log_action(db, action, triggered, skipped)

            with self._state_lock:
                self._last_coord_action = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trigger_evolution": action.trigger_evolution,
                    "trigger_drl_retrain": action.trigger_drl_retrain,
                    "trigger_kelly_update": action.trigger_kelly_update,
                    "reasons": list(action.reasons or []),
                    "triggered_jobs": list(triggered),
                    "skipped_reasons": list(skipped),
                }
            return {"reasons": list(action.reasons or []),
                    "triggered": triggered, "skipped": skipped}
        finally:
            db.close()

    # ─────────────────────────────
    #  辅助
    # ─────────────────────────────

    def _update_last_loop_tick_at(self) -> None:
        """写 SystemCoordinatorState.last_loop_tick_at，供外部观测心跳。"""
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import SystemCoordinatorState
            db = SessionLocal()
            try:
                now_utc = datetime.now(timezone.utc)
                state = db.query(SystemCoordinatorState).first()
                if state is None:
                    state = SystemCoordinatorState()
                    db.add(state)
                if hasattr(state, "last_loop_tick_at"):
                    state.last_loop_tick_at = now_utc
                db.commit()
            finally:
                db.close()
        except Exception as e:
            # [2026-08-05 v6 8.3 阶段1] 静默→告警：心跳落库失败必须可见
            logger.warning(f"[LearningLoop] last_loop_tick_at 写入失败: {e}")

    def _broadcast_coord_status(self) -> None:
        """广播 coordinator_status 到 WS（P2-1）。"""
        try:
            from backend.services.ws_broadcast import ws_broadcast_hub
            payload = self.status()
            ws_broadcast_hub.broadcast_coordinator_status(payload)
        except Exception as e:
            # [2026-08-05 v6 8.3 阶段1] 静默→告警：广播失败必须可见
            logger.warning(f"[LearningLoop] 广播 coordinator_status 失败: {e}")

    def _record_tick(self, job: str, t_start: float, success: bool, extra: Dict[str, Any]) -> None:
        elapsed_ms = int((time.time() - t_start) * 1000)
        now = datetime.now(timezone.utc)
        with self._state_lock:
            self._metrics[job].append({
                "ts": now.isoformat(),
                "elapsed_ms": elapsed_ms,
                "success": success,
                "extra": extra,
            })
            self._last_tick_at[job] = now


# 全局单例
learning_loop = LearningLoopService()
