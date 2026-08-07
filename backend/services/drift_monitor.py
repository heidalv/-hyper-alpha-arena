"""市场漂移监控服务 — Market Drift Monitor (M-18)

P3 核心组件，负责:
1. 定时巡检活跃策略的概念漂移状态 (每6h)
2. 调用 strategy_learning_service._detect_concept_drift 检测 KS + MMD 漂移
3. 漂移触发时自动调用 training_orchestrator 执行增量重训练
4. 漂移事件日志 + 告警

用法:
    from services.drift_monitor import drift_monitor
    drift_monitor.start()         # 启动定时巡检 (后台线程)
    drift_monitor.run_once()      # 手动触发一次全量巡检
    drift_monitor.stop()          # 停止定时巡检
"""

import logging
import threading
import time as _time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class DriftMonitor:
    """市场漂移监控器 — 单例"""

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
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._check_interval_hours = 6  # 默认每6小时巡检一次
        self._min_trades_for_check = 10  # 最少交易数才检查
        self._last_check_ts: Dict[str, float] = {}  # strategy_id → 上次检查时间
        self._drift_history: List[Dict[str, Any]] = []  # 漂移事件历史
        self._drift_history_max = 200
        self._active = False
        logger.info("[DriftMonitor] 漂移监控器初始化完成")

    # ════════════════════════ 公共 API ════════════════════════

    def start(self, interval_hours: int = 6) -> None:
        """启动定时漂移巡检（后台线程）"""
        if self._active:
            logger.warning("[DriftMonitor] 已在运行中，跳过重复启动")
            return
        self._check_interval_hours = interval_hours
        self._stop_event.clear()
        self._active = True
        self._thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="drift-monitor",
        )
        self._thread.start()
        logger.info(
            f"[DriftMonitor] 定时巡检已启动 (间隔={self._check_interval_hours}h)"
        )

    def stop(self) -> None:
        """停止定时巡检"""
        self._stop_event.set()
        self._active = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=30)
        logger.info("[DriftMonitor] 定时巡检已停止")

    def run_once(self) -> Dict[str, Any]:
        """手动触发一次全量漂移巡检"""
        return self._run_check()

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取漂移事件历史"""
        return self._drift_history[-limit:]

    def get_status(self) -> Dict[str, Any]:
        """获取监控器状态"""
        return {
            "active": self._active,
            "interval_hours": self._check_interval_hours,
            "last_full_check": max(self._last_check_ts.values()) if self._last_check_ts else None,
            "total_drift_events": len(self._drift_history),
            "recent_events": self._drift_history[-5:],
        }

    # ════════════════════════ 内部实现 ════════════════════════

    def _monitor_loop(self) -> None:
        """后台监控循环"""
        while not self._stop_event.wait(timeout=self._check_interval_hours * 3600):
            try:
                logger.info("[DriftMonitor] 定时巡检开始...")
                result = self._run_check()
                if result.get("drifts_detected", 0) > 0:
                    logger.warning(
                        f"[DriftMonitor] 巡检完成: 检查{result['strategies_checked']}个策略, "
                        f"发现{result['drifts_detected']}个漂移, "
                        f"触发{result['retrains_triggered']}次重训练"
                    )
                else:
                    logger.debug(
                        f"[DriftMonitor] 巡检完成: 检查{result['strategies_checked']}个策略, "
                        f"无漂移"
                    )
            except Exception as e:
                logger.error(f"[DriftMonitor] 巡检异常: {e}", exc_info=True)

    def _run_check(self) -> Dict[str, Any]:
        """执行一次全量漂移检查"""
        result = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "strategies_checked": 0,
            "drifts_detected": 0,
            "retrains_triggered": 0,
            "details": [],
        }

        try:
            # 获取活跃策略列表
            active_strategies = self._get_active_strategies()
            if not active_strategies:
                logger.debug("[DriftMonitor] 无活跃策略，跳过巡检")
                return result

            result["strategies_checked"] = len(active_strategies)

            for strategy_id in active_strategies:
                try:
                    drift_detail = self._check_strategy_drift(strategy_id)
                    if drift_detail:
                        result["details"].append(drift_detail)
                        if drift_detail.get("drift_detected"):
                            result["drifts_detected"] += 1
                            if drift_detail.get("retrain_triggered"):
                                result["retrains_triggered"] += 1
                except Exception as e:
                    logger.error(
                        f"[DriftMonitor] 策略 {strategy_id} 漂移检查异常: {e}",
                        exc_info=True,
                    )

            self._last_check_ts["__global__"] = _time.time()

        except Exception as e:
            logger.error(f"[DriftMonitor] 全量巡检失败: {e}", exc_info=True)

        return result

    def _get_active_strategies(self) -> List[str]:
        """获取所有活跃策略ID列表"""
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import AIStrategy
            from sqlalchemy import or_

            db = SessionLocal()
            try:
                strategies = db.query(AIStrategy.strategy_id).filter(
                    AIStrategy.status.in_(["active", "graduated", "golden"]),
                    or_(
                        AIStrategy.total_trades >= self._min_trades_for_check,
                        AIStrategy.is_template == True,  # noqa: E712
                    ),
                ).all()
                return [s.strategy_id for s in strategies]
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[DriftMonitor] 获取活跃策略失败: {e}")
            return []

    def _check_strategy_drift(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        """检查单个策略的漂移状态，漂移时触发重训练"""
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import StrategyTrade
            from backend.services.strategy_learning_service import (
                StrategyLearningService,
            )

            db = SessionLocal()
            try:
                # 获取最近交易 (30天内)
                cutoff = datetime.now(timezone.utc) - timedelta(days=30)
                all_trades = db.query(StrategyTrade).filter(
                    StrategyTrade.strategy_id == strategy_id,
                    StrategyTrade.opened_at >= cutoff,
                    StrategyTrade.pnl_pct.isnot(None),
                ).order_by(StrategyTrade.opened_at.desc()).all()

                if len(all_trades) < self._min_trades_for_check:
                    return None

                # 分开近期 (最新50%) vs 历史 (较早50%)
                mid = len(all_trades) // 2
                recent = all_trades[:mid]
                historical = all_trades[mid:]

                if len(recent) < 10:
                    return None

                # 调用现有漂移检测
                learning_svc = StrategyLearningService()
                drift_result = learning_svc._detect_concept_drift(
                    db=db,
                    strategy_id=strategy_id,
                    recent_trades=recent,
                    historical_trades=historical,
                )

                detail = {
                    "strategy_id": strategy_id,
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "drift_detected": drift_result.get("drift_detected", False),
                    "severity": drift_result.get("drift_severity", "none"),
                    "ks_statistic": drift_result.get("ks_statistic", 0),
                    "ks_pvalue": drift_result.get("ks_pvalue", 1.0),
                    "win_rate_delta": drift_result.get("win_rate_delta", 0),
                    "hold_duration_delta": drift_result.get("hold_duration_delta", 0),
                    "recommended_action": drift_result.get("recommended_action", "none"),
                    "retrain_triggered": False,
                }

                self._last_check_ts[strategy_id] = _time.time()

                # ── 漂移触发自动重训练 ──
                if drift_result.get("drift_detected"):
                    severity = drift_result.get("drift_severity", "none")
                    self._record_drift_event(detail)

                    if severity in ("medium", "high"):
                        retrained = self._trigger_retraining(
                            strategy_id, severity, detail
                        )
                        detail["retrain_triggered"] = retrained

                        if severity == "high":
                            logger.warning(
                                f"[DriftMonitor] ⚠️ {strategy_id} 严重漂移: "
                                f"KS={detail['ks_statistic']:.3f}(p={detail['ks_pvalue']:.3f}), "
                                f"WR_delta={detail['win_rate_delta']:+.1%}, "
                                f"已{'触发' if retrained else '尝试触发'}增量重训练"
                            )
                        else:
                            logger.info(
                                f"[DriftMonitor] {strategy_id} 中度漂移: "
                                f"KS={detail['ks_statistic']:.3f}, "
                                f"已{'触发' if retrained else '尝试触发'}增量重训练"
                            )

                return detail

            finally:
                db.close()

        except Exception as e:
            logger.error(
                f"[DriftMonitor] 策略 {strategy_id} 漂移检查失败: {e}",
                exc_info=True,
            )
            return None

    def _trigger_retraining(
        self, strategy_id: str, severity: str, detail: Dict[str, Any]
    ) -> bool:
        """触发 training_orchestrator 的增量重训练"""
        try:
            from backend.services.training_orchestrator import run_validated_merge
            from backend.database.connection import SessionLocal

            db = SessionLocal()
            try:
                merge_result = run_validated_merge(db)
                if merge_result and not merge_result.get("error"):
                    logger.info(
                        f"[DriftMonitor] {strategy_id} 重训练触发成功: "
                        f"merged={merge_result.get('strategies_merged', 0)}"
                    )
                    return True
                else:
                    logger.warning(
                        f"[DriftMonitor] {strategy_id} 重训练返回空/失败: "
                        f"{merge_result.get('error', 'unknown') if merge_result else 'None'}"
                    )
                    return False
            finally:
                db.close()

        except Exception as e:
            logger.error(f"[DriftMonitor] {strategy_id} 重训练触发异常: {e}")
            return False

    def _record_drift_event(self, detail: Dict[str, Any]) -> None:
        """记录漂移事件到内存历史"""
        self._drift_history.append(detail)
        if len(self._drift_history) > self._drift_history_max:
            self._drift_history = self._drift_history[-self._drift_history_max:]


# ── 全局单例 ──
drift_monitor = DriftMonitor()


def get_drift_monitor() -> DriftMonitor:
    """获取漂移监控器实例"""
    return drift_monitor
