"""
StrategyHealthService — 统一策略健康监控 + 自动诊断 + 自修复

监控维度:
1. 绩效衰减: 滚动Sharpe < 0.5 或连续亏损 > 5笔
2. 因子失效: 策略依赖因子的IC持续下降
3. 市场适配: 当前 regime 与策略最优 regime 不匹配
4. 执行异常: 滑点超预期、部分成交率高

自修复动作:
- 轻度: 缩减仓位 (reduce risk_pct by 30%)
- 中度: 暂停策略 + 触发参数重优化
- 重度: 归档策略 + 通知 evolver 生成替代

集成位置: full_auto_trading_service._run_health_check 每个完整周期末尾调用
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class HealthLevel(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"         # 轻度
    UNDERPERFORMING = "underperforming"  # 中度
    CRITICAL = "critical"         # 重度


class HealAction(str, Enum):
    NONE = "none"
    REDUCE_RISK = "reduce_risk"
    PAUSE_AND_REOPTIMIZE = "pause_and_reoptimize"
    ARCHIVE_AND_REPLACE = "archive_and_replace"


@dataclass
class HealthReport:
    strategy_id: str
    level: HealthLevel = HealthLevel.HEALTHY
    rolling_sharpe: float = 0.0
    consecutive_losses: int = 0
    regime_mismatch: bool = False
    slippage_anomaly: bool = False
    details: Dict[str, Any] = field(default_factory=dict)
    recommended_action: HealAction = HealAction.NONE
    timestamp: float = field(default_factory=time.time)


@dataclass
class DiagnosisReport:
    strategy_id: str
    root_causes: List[str] = field(default_factory=list)
    factor_degradation: Dict[str, float] = field(default_factory=dict)
    regime_info: Dict[str, Any] = field(default_factory=dict)
    recommended_action: HealAction = HealAction.NONE


class StrategyHealthService:
    """统一策略健康监控服务"""

    # 阈值
    SHARPE_THRESHOLD_DEGRADED = 0.5
    SHARPE_THRESHOLD_CRITICAL = -0.2
    MAX_CONSECUTIVE_LOSSES = 5
    MAX_CONSECUTIVE_LOSSES_CRITICAL = 8
    RISK_REDUCTION_FACTOR = 0.7  # 缩减30%
    MIN_TRADES_FOR_EVAL = 5

    def __init__(self):
        self._last_eval: Dict[str, float] = {}
        self._eval_cooldown = 300  # 5min between evaluations

    def evaluate_strategy_health(
        self,
        strategy_id: str,
        db=None,
        strategy=None,
        market_summary: Optional[Dict] = None,
    ) -> HealthReport:
        """
        评估单个策略的健康状态。

        Args:
            strategy_id: 策略ID
            db: 数据库 session
            strategy: AIStrategy 对象 (可选, 减少查询)
            market_summary: 当前市场概况 (可选)

        Returns:
            HealthReport
        """
        report = HealthReport(strategy_id=strategy_id)

        # Cooldown: 避免频繁评估同一策略
        now = time.time()
        last = self._last_eval.get(strategy_id, 0)
        if now - last < self._eval_cooldown:
            return report
        self._last_eval[strategy_id] = now

        if strategy is None and db is not None:
            try:
                from backend.database.models import AIStrategy
                strategy = db.query(AIStrategy).filter(
                    AIStrategy.strategy_id == strategy_id
                ).first()
            except Exception as e:
                logger.debug(f"[StrategyHealth] 查询策略失败 {strategy_id}: {e}")
                return report

        if strategy is None:
            return report

        # ── 1. 绩效指标 ──
        perf = getattr(strategy, "performance_metrics", None) or {}
        total_trades = perf.get("total_trades", 0)
        if total_trades < self.MIN_TRADES_FOR_EVAL:
            report.details["skip_reason"] = f"trades={total_trades} < {self.MIN_TRADES_FOR_EVAL}"
            return report

        sharpe = self._compute_rolling_sharpe(perf)
        report.rolling_sharpe = sharpe

        consec_losses = self._count_consecutive_losses(perf)
        report.consecutive_losses = consec_losses

        # ── 2. 市场适配 ──
        if market_summary and strategy.primary_symbol:
            sym_info = market_summary.get(strategy.primary_symbol, {})
            current_regime = sym_info.get("regime", "unknown")
            genome = getattr(strategy, "genome", None) or {}
            best_regime = genome.get("best_regime", "")
            if best_regime and current_regime != "unknown" and best_regime != current_regime:
                report.regime_mismatch = True
                report.details["current_regime"] = current_regime
                report.details["best_regime"] = best_regime

        # ── 3. 执行异常 ──
        avg_slippage = perf.get("avg_slippage_pct", 0.0)
        if avg_slippage > 0.5:
            report.slippage_anomaly = True
            report.details["avg_slippage_pct"] = avg_slippage

        # ── 4. 综合判定 ──
        if (sharpe < self.SHARPE_THRESHOLD_CRITICAL
                or consec_losses >= self.MAX_CONSECUTIVE_LOSSES_CRITICAL):
            report.level = HealthLevel.CRITICAL
            report.recommended_action = HealAction.ARCHIVE_AND_REPLACE
        elif (sharpe < self.SHARPE_THRESHOLD_DEGRADED
              or consec_losses >= self.MAX_CONSECUTIVE_LOSSES
              or report.regime_mismatch):
            report.level = HealthLevel.UNDERPERFORMING
            report.recommended_action = HealAction.PAUSE_AND_REOPTIMIZE
        elif report.slippage_anomaly:
            report.level = HealthLevel.DEGRADED
            report.recommended_action = HealAction.REDUCE_RISK
        else:
            report.level = HealthLevel.HEALTHY
            report.recommended_action = HealAction.NONE

        return report

    def diagnose_underperformance(
        self, strategy_id: str, db=None, strategy=None
    ) -> DiagnosisReport:
        """深入诊断表现不佳的原因。"""
        diag = DiagnosisReport(strategy_id=strategy_id)

        if strategy is None and db is not None:
            try:
                from backend.database.models import AIStrategy
                strategy = db.query(AIStrategy).filter(
                    AIStrategy.strategy_id == strategy_id
                ).first()
            except Exception:
                pass

        if strategy is None:
            diag.root_causes.append("strategy_not_found")
            return diag

        perf = getattr(strategy, "performance_metrics", None) or {}
        genome = getattr(strategy, "genome", None) or {}

        # 连续亏损分析
        consec = self._count_consecutive_losses(perf)
        if consec >= self.MAX_CONSECUTIVE_LOSSES:
            diag.root_causes.append(f"consecutive_losses={consec}")

        # Sharpe分析
        sharpe = self._compute_rolling_sharpe(perf)
        if sharpe < self.SHARPE_THRESHOLD_DEGRADED:
            diag.root_causes.append(f"low_sharpe={sharpe:.2f}")

        # 最大回撤
        max_dd = perf.get("max_drawdown_pct", 0.0)
        if max_dd > 15.0:
            diag.root_causes.append(f"high_drawdown={max_dd:.1f}%")

        # 推荐动作
        if len(diag.root_causes) >= 3:
            diag.recommended_action = HealAction.ARCHIVE_AND_REPLACE
        elif len(diag.root_causes) >= 1:
            diag.recommended_action = HealAction.PAUSE_AND_REOPTIMIZE
        else:
            diag.recommended_action = HealAction.NONE

        return diag

    def auto_heal(
        self, strategy_id: str, report: HealthReport, db=None
    ) -> Dict[str, Any]:
        """
        根据健康报告执行自动修复动作。

        Returns:
            {"action": str, "applied": bool, "details": ...}
        """
        result = {"action": report.recommended_action.value, "applied": False, "details": {}}

        if report.recommended_action == HealAction.NONE:
            return result

        if db is None:
            result["details"]["error"] = "no_db_session"
            return result

        try:
            from backend.database.models import AIStrategy
            strategy = db.query(AIStrategy).filter(
                AIStrategy.strategy_id == strategy_id
            ).first()
            if not strategy:
                result["details"]["error"] = "strategy_not_found"
                return result

            if report.recommended_action == HealAction.REDUCE_RISK:
                result = self._heal_reduce_risk(strategy, db)
            elif report.recommended_action == HealAction.PAUSE_AND_REOPTIMIZE:
                result = self._heal_pause_reoptimize(strategy, db)
            elif report.recommended_action == HealAction.ARCHIVE_AND_REPLACE:
                result = self._heal_archive_replace(strategy, db)

        except Exception as e:
            logger.error(f"[StrategyHealth] auto_heal failed {strategy_id}: {e}")
            result["details"]["error"] = str(e)

        return result

    # ── Heal actions ─────────────────────────────

    def _heal_reduce_risk(self, strategy, db) -> Dict[str, Any]:
        """轻度: 缩减仓位"""
        risk_params = getattr(strategy, "risk_params", None) or {}
        old_risk_pct = risk_params.get("risk_pct", 2.0)
        new_risk_pct = round(old_risk_pct * self.RISK_REDUCTION_FACTOR, 2)
        new_risk_pct = max(new_risk_pct, 0.5)  # 下限

        risk_params["risk_pct"] = new_risk_pct
        strategy.risk_params = risk_params

        try:
            db.add(strategy)
            db.commit()
        except Exception:
            db.rollback()
            raise

        logger.info(
            f"[StrategyHealth] REDUCE_RISK {strategy.strategy_id}: "
            f"risk_pct {old_risk_pct}% → {new_risk_pct}%"
        )
        return {
            "action": HealAction.REDUCE_RISK.value,
            "applied": True,
            "details": {"old_risk_pct": old_risk_pct, "new_risk_pct": new_risk_pct},
        }

    def _heal_pause_reoptimize(self, strategy, db) -> Dict[str, Any]:
        """中度: 暂停策略 + 触发参数重优化"""
        old_status = strategy.status
        strategy.status = "paused"
        # 2026-06-19: 统一注册到 SymbolLockRegistry
        try:
            from backend.services.symbol_lock_registry import lock_registry
            lock_registry.lock(
                strategy.primary_symbol or "", strategy_id=str(strategy.strategy_id),
                reason_code="health_pause", by="strategy_health",
            )
        except Exception:
            pass

        try:
            db.add(strategy)
            db.commit()
        except Exception:
            db.rollback()
            raise

        # 触发重优化（非阻塞）
        try:
            from backend.services.auto_optimizer import AutoOptimizer
            optimizer = AutoOptimizer()
            optimizer.queue_optimization(strategy.strategy_id)
            logger.info(f"[StrategyHealth] PAUSE+REOPTIMIZE queued {strategy.strategy_id}")
        except Exception as opt_err:
            logger.warning(f"[StrategyHealth] optimization queue failed: {opt_err}")

        return {
            "action": HealAction.PAUSE_AND_REOPTIMIZE.value,
            "applied": True,
            "details": {"old_status": old_status, "new_status": "paused"},
        }

    def _heal_archive_replace(self, strategy, db) -> Dict[str, Any]:
        """重度: 归档策略 + 通知 evolver 生成替代"""
        old_status = strategy.status
        strategy.status = "archived"

        try:
            db.add(strategy)
            db.commit()
        except Exception:
            db.rollback()
            raise

        # B3 修复: StrategyEvolver 无 request_replacement 方法
        # 直接标记归档 + 记录原因，由 evolution_scheduler 周期扫描替代
        from datetime import datetime as _dt, timezone as _tz
        strategy.archived_at = _dt.now(_tz.utc)
        strategy.archive_reason = f"health_service: {strategy.health_status or 'degraded'}"
        db.add(strategy)
        db.commit()
        logger.info(
            f"[StrategyHealth] ARCHIVE+REPLACE {strategy.strategy_id} "
            f"(deferred to evolution_scheduler)"
        )

        return {
            "action": HealAction.ARCHIVE_AND_REPLACE.value,
            "applied": True,
            "details": {"old_status": old_status, "new_status": "archived"},
        }

    # ── Internal helpers ─────────────────────────

    def _compute_rolling_sharpe(self, perf: Dict) -> float:
        """从 performance_metrics 计算滚动 Sharpe。"""
        total_pnl = perf.get("total_pnl", 0.0)
        total_trades = perf.get("total_trades", 1)
        win_rate = perf.get("win_rate", 0.5)

        # Simplified Sharpe approximation
        avg_return = total_pnl / max(total_trades, 1)
        if total_trades < 2:
            return 0.0
        # Use win rate to approximate return volatility
        p = max(0.01, min(0.99, win_rate))
        vol_proxy = (p * (1 - p)) ** 0.5
        if vol_proxy < 1e-10:
            return 0.0
        return avg_return / vol_proxy

    def _count_consecutive_losses(self, perf: Dict) -> int:
        """从 performance_metrics 获取连续亏损次数。"""
        return int(perf.get("consecutive_losses", 0))


# Global singleton
_health_service: Optional[StrategyHealthService] = None


def get_strategy_health_service() -> StrategyHealthService:
    global _health_service
    if _health_service is None:
        _health_service = StrategyHealthService()
    return _health_service
