"""
Realtime Monitor - 实时监控器

提供交易系统的实时监控功能：
1. 账户状态监控
2. 持仓监控
3. 风险指标监控
4. 系统健康度监控

Author: Hyper-Alpha-Arena
"""

import logging
import time
import threading
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta, timezone
from collections import deque

logger = logging.getLogger(__name__)


class MetricType(Enum):
    """监控指标类型"""
    ACCOUNT_EQUITY = "account_equity"
    MARGIN_USAGE = "margin_usage"
    UNREALIZED_PNL = "unrealized_pnl"
    POSITION_COUNT = "position_count"
    DAILY_PNL = "daily_pnl"
    DRAWDOWN = "drawdown"
    WIN_RATE = "win_rate"
    SHARPE_RATIO = "sharpe_ratio"
    API_LATENCY = "api_latency"
    ERROR_COUNT = "error_count"


@dataclass
class MonitoringMetrics:
    """监控指标集合"""
    account_id: int
    timestamp: datetime
    
    # 账户指标
    total_equity: float = 0.0
    available_balance: float = 0.0
    margin_usage_pct: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl_today: float = 0.0
    
    # 持仓指标
    position_count: int = 0
    total_position_value: float = 0.0
    largest_position_pct: float = 0.0
    
    # 风险指标
    current_drawdown: float = 0.0
    max_drawdown: float = 0.0
    daily_pnl_pct: float = 0.0
    
    # 性能指标
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    
    # 系统指标
    api_latency_ms: float = 0.0
    error_count_1h: int = 0
    last_trade_time: Optional[datetime] = None


@dataclass
class MonitoringAlert:
    """监控告警"""
    alert_id: str
    account_id: int
    metric_type: MetricType
    severity: str  # "info", "warning", "critical"
    message: str
    current_value: float
    threshold: float
    timestamp: datetime
    acknowledged: bool = False


@dataclass
class MonitoringThresholds:
    """监控阈值配置"""
    # 保证金使用率阈值
    margin_usage_warning: float = 0.6
    margin_usage_critical: float = 0.8
    
    # 回撤阈值
    drawdown_warning: float = 0.05
    drawdown_critical: float = 0.10
    
    # 单日亏损阈值
    daily_loss_warning: float = 0.03
    daily_loss_critical: float = 0.05
    
    # 持仓集中度阈值
    position_concentration_warning: float = 0.3
    position_concentration_critical: float = 0.5
    
    # API延迟阈值 (ms)
    api_latency_warning: float = 1000
    api_latency_critical: float = 3000
    
    # 错误数量阈值
    error_count_warning: int = 5
    error_count_critical: int = 10


class RealtimeMonitor:
    """
    实时监控器
    
    监控交易系统的各项指标，
    并在异常情况下触发告警
    """
    
    def __init__(
        self,
        thresholds: Optional[MonitoringThresholds] = None,
        check_interval: float = 30.0
    ):
        self.thresholds = thresholds or MonitoringThresholds()
        self.check_interval = check_interval
        
        # 监控状态
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None
        
        # 账户指标历史 {account_id: deque of MonitoringMetrics}
        self._metrics_history: Dict[int, deque] = {}
        self._history_max_size = 1000  # 保留最近1000条记录
        
        # 当前告警
        self._active_alerts: Dict[str, MonitoringAlert] = {}
        
        # 告警回调
        self._alert_callbacks: List[Callable[[MonitoringAlert], None]] = []
        
        # 峰值记录 (用于计算回撤)
        self._peak_equity: Dict[int, float] = {}
        
        # 错误计数
        self._error_counts: Dict[int, deque] = {}
        
        logger.info("[RealtimeMonitor] Initialized")
    
    def start(self):
        """启动监控"""
        if self._running:
            logger.warning("[RealtimeMonitor] Already running")
            return
        
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True
        )
        self._monitor_thread.start()
        logger.info("[RealtimeMonitor] Started")
    
    def stop(self):
        """停止监控"""
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5.0)
        logger.info("[RealtimeMonitor] Stopped")
    
    def register_alert_callback(self, callback: Callable[[MonitoringAlert], None]):
        """注册告警回调"""
        if callback not in self._alert_callbacks:
            self._alert_callbacks.append(callback)
            logger.info(f"[RealtimeMonitor] Alert callback registered")
    
    def _monitor_loop(self):
        """监控循环"""
        while self._running:
            try:
                self._check_all_accounts()
            except Exception as e:
                logger.error(f"[RealtimeMonitor] Monitor loop error: {e}", exc_info=True)
            
            time.sleep(self.check_interval)
    
    def _check_all_accounts(self):
        """检查所有账户"""
        try:
            from backend.database.connection import SessionLocal
            from database.models import Account
            
            db = SessionLocal()
            try:
                accounts = db.query(Account).filter(
                    Account.is_active == "true"
                ).all()
                
                for account in accounts:
                    try:
                        metrics = self._collect_account_metrics(db, account)
                        if metrics:
                            self._store_metrics(metrics)
                            self._check_thresholds(metrics)
                    except Exception as acc_err:
                        logger.warning(f"[RealtimeMonitor] Error checking account {account.id}: {acc_err}")
                        self._record_error(account.id)
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[RealtimeMonitor] Failed to check accounts: {e}")
    
    def _collect_account_metrics(self, db, account) -> Optional[MonitoringMetrics]:
        """收集账户指标"""
        try:
            metrics = MonitoringMetrics(
                account_id=account.id,
                timestamp=datetime.now(timezone.utc)
            )
            
            # 尝试获取 Hyperliquid 数据
            if hasattr(account, 'hyperliquid_enabled') and account.hyperliquid_enabled == "true":
                self._collect_hyperliquid_metrics(db, account, metrics)
            
            # 尝试获取 Binance 数据
            elif hasattr(account, 'binance_enabled') and account.binance_enabled == "true":
                self._collect_binance_metrics(db, account, metrics)
            
            # 计算回撤
            self._calculate_drawdown(metrics)
            
            # 计算错误数
            metrics.error_count_1h = self._get_error_count(account.id)
            
            return metrics
        except Exception as e:
            logger.warning(f"[RealtimeMonitor] Failed to collect metrics for account {account.id}: {e}")
            return None
    
    def _collect_hyperliquid_metrics(self, db, account, metrics: MonitoringMetrics):
        """收集 Hyperliquid 指标"""
        try:
            from services.hyperliquid_cache import get_cached_account_state
            from services.hyperliquid_environment import get_global_trading_mode
            
            environment = get_global_trading_mode(db)
            state = get_cached_account_state(account.id, environment)
            
            if state:
                metrics.total_equity = float(state.get('total_equity', 0) or 0)
                metrics.available_balance = float(state.get('available_balance', 0) or 0)
                metrics.margin_usage_pct = float(state.get('margin_usage_percent', 0) or 0) / 100
                
                positions = state.get('positions', [])
                metrics.position_count = len(positions)
                
                total_pnl = 0.0
                total_value = 0.0
                max_position_value = 0.0
                
                for pos in positions:
                    pnl = float(pos.get('unrealized_pnl', 0) or 0)
                    value = float(pos.get('position_value', 0) or 0)
                    total_pnl += pnl
                    total_value += abs(value)
                    max_position_value = max(max_position_value, abs(value))
                
                metrics.unrealized_pnl = total_pnl
                metrics.total_position_value = total_value
                
                if metrics.total_equity > 0:
                    metrics.largest_position_pct = max_position_value / metrics.total_equity
        except Exception as e:
            logger.debug(f"[RealtimeMonitor] Hyperliquid metrics collection error: {e}")
    
    def _collect_binance_metrics(self, db, account, metrics: MonitoringMetrics):
        """Binance removed (Phase 1) - no-op"""
        pass
    
    def _calculate_drawdown(self, metrics: MonitoringMetrics):
        """计算回撤"""
        account_id = metrics.account_id
        equity = metrics.total_equity
        
        if equity <= 0:
            return
        
        # 更新峰值
        if account_id not in self._peak_equity:
            self._peak_equity[account_id] = equity
        else:
            self._peak_equity[account_id] = max(self._peak_equity[account_id], equity)
        
        peak = self._peak_equity[account_id]
        if peak > 0:
            metrics.current_drawdown = (peak - equity) / peak
    
    def _store_metrics(self, metrics: MonitoringMetrics):
        """存储指标"""
        account_id = metrics.account_id
        
        if account_id not in self._metrics_history:
            self._metrics_history[account_id] = deque(maxlen=self._history_max_size)
        
        self._metrics_history[account_id].append(metrics)
    
    def _check_thresholds(self, metrics: MonitoringMetrics):
        """检查阈值并触发告警"""
        # 检查保证金使用率
        if metrics.margin_usage_pct >= self.thresholds.margin_usage_critical:
            self._trigger_alert(
                metrics.account_id,
                MetricType.MARGIN_USAGE,
                "critical",
                f"保证金使用率达到 {metrics.margin_usage_pct:.1%}，超过危险阈值",
                metrics.margin_usage_pct,
                self.thresholds.margin_usage_critical
            )
        elif metrics.margin_usage_pct >= self.thresholds.margin_usage_warning:
            self._trigger_alert(
                metrics.account_id,
                MetricType.MARGIN_USAGE,
                "warning",
                f"保证金使用率达到 {metrics.margin_usage_pct:.1%}，接近危险阈值",
                metrics.margin_usage_pct,
                self.thresholds.margin_usage_warning
            )
        
        # 检查回撤
        if metrics.current_drawdown >= self.thresholds.drawdown_critical:
            self._trigger_alert(
                metrics.account_id,
                MetricType.DRAWDOWN,
                "critical",
                f"回撤达到 {metrics.current_drawdown:.1%}，超过危险阈值",
                metrics.current_drawdown,
                self.thresholds.drawdown_critical
            )
        elif metrics.current_drawdown >= self.thresholds.drawdown_warning:
            self._trigger_alert(
                metrics.account_id,
                MetricType.DRAWDOWN,
                "warning",
                f"回撤达到 {metrics.current_drawdown:.1%}，接近危险阈值",
                metrics.current_drawdown,
                self.thresholds.drawdown_warning
            )
        
        # 检查持仓集中度
        if metrics.largest_position_pct >= self.thresholds.position_concentration_critical:
            self._trigger_alert(
                metrics.account_id,
                MetricType.POSITION_COUNT,
                "critical",
                f"持仓集中度达到 {metrics.largest_position_pct:.1%}，风险过高",
                metrics.largest_position_pct,
                self.thresholds.position_concentration_critical
            )
        
        # 检查错误数
        if metrics.error_count_1h >= self.thresholds.error_count_critical:
            self._trigger_alert(
                metrics.account_id,
                MetricType.ERROR_COUNT,
                "critical",
                f"过去1小时内错误数达到 {metrics.error_count_1h}",
                metrics.error_count_1h,
                self.thresholds.error_count_critical
            )
    
    def _trigger_alert(
        self,
        account_id: int,
        metric_type: MetricType,
        severity: str,
        message: str,
        current_value: float,
        threshold: float
    ):
        """触发告警"""
        alert_id = f"{account_id}_{metric_type.value}_{severity}"
        
        # 避免重复告警
        if alert_id in self._active_alerts:
            existing = self._active_alerts[alert_id]
            if (datetime.now(timezone.utc) - existing.timestamp).total_seconds() < 300:
                return  # 5分钟内不重复告警
        
        alert = MonitoringAlert(
            alert_id=alert_id,
            account_id=account_id,
            metric_type=metric_type,
            severity=severity,
            message=message,
            current_value=current_value,
            threshold=threshold,
            timestamp=datetime.now(timezone.utc)
        )
        
        self._active_alerts[alert_id] = alert
        
        logger.warning(f"[RealtimeMonitor] Alert: {message}")
        
        # 触发回调
        for callback in self._alert_callbacks:
            try:
                callback(alert)
            except Exception as e:
                logger.error(f"[RealtimeMonitor] Alert callback error: {e}")
    
    def _record_error(self, account_id: int):
        """记录错误"""
        if account_id not in self._error_counts:
            self._error_counts[account_id] = deque(maxlen=100)
        self._error_counts[account_id].append(datetime.now(timezone.utc))
    
    def _get_error_count(self, account_id: int) -> int:
        """获取过去1小时的错误数"""
        if account_id not in self._error_counts:
            return 0
        
        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        return sum(1 for t in self._error_counts[account_id] if t > one_hour_ago)
    
    def get_metrics(self, account_id: int, limit: int = 100) -> List[MonitoringMetrics]:
        """获取账户指标历史"""
        if account_id not in self._metrics_history:
            return []
        
        history = list(self._metrics_history[account_id])
        return history[-limit:] if len(history) > limit else history
    
    def get_latest_metrics(self, account_id: int) -> Optional[MonitoringMetrics]:
        """获取最新指标"""
        if account_id not in self._metrics_history:
            return None
        
        history = self._metrics_history[account_id]
        return history[-1] if history else None
    
    def get_active_alerts(self, account_id: Optional[int] = None) -> List[MonitoringAlert]:
        """获取活跃告警"""
        alerts = list(self._active_alerts.values())
        if account_id:
            alerts = [a for a in alerts if a.account_id == account_id]
        return sorted(alerts, key=lambda a: a.timestamp, reverse=True)
    
    def acknowledge_alert(self, alert_id: str):
        """确认告警"""
        if alert_id in self._active_alerts:
            self._active_alerts[alert_id].acknowledged = True
    
    def clear_alert(self, alert_id: str):
        """清除告警"""
        if alert_id in self._active_alerts:
            del self._active_alerts[alert_id]
    
    def get_status(self) -> Dict[str, Any]:
        """获取监控状态"""
        return {
            "running": self._running,
            "check_interval": self.check_interval,
            "monitored_accounts": len(self._metrics_history),
            "active_alerts": len(self._active_alerts),
            "alert_callbacks": len(self._alert_callbacks)
        }


# 全局实例
_realtime_monitor: Optional[RealtimeMonitor] = None


def get_realtime_monitor() -> RealtimeMonitor:
    """获取全局实时监控器"""
    global _realtime_monitor
    if _realtime_monitor is None:
        _realtime_monitor = RealtimeMonitor()
    return _realtime_monitor
