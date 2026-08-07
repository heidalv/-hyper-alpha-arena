"""
ATAS V2 风险监控器

实时风险监控与预警
"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import List, Dict, Optional, Callable
import time


class AlertLevel(Enum):
    """预警级别"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class RiskAlert:
    """风险预警"""
    timestamp: datetime
    level: AlertLevel
    category: str  # 预警类别
    message: str  # 预警信息
    metrics: Dict[str, float]  # 相关指标
    acknowledged: bool = False  # 是否已确认


class RiskMonitor:
    """风险监控器"""
    
    def __init__(self, check_interval: int = 60):
        self.check_interval = check_interval  # 检查间隔（秒）
        self.alerts: List[RiskAlert] = []
        self.alert_callbacks: List[Callable] = []
        self.monitoring = False
        
        # 预警阈值配置
        self.thresholds = {
            'drawdown_warning': 0.10,
            'drawdown_critical': 0.20,
            'loss_rate_warning': 0.60,
            'volatility_warning': 0.30,
            'concentration_warning': 0.40,
        }
    
    def start_monitoring(self, portfolio_getter: Callable):
        """
        开始监控
        
        Args:
            portfolio_getter: 获取投资组合状态的函数
        """
        self.monitoring = True
        self.portfolio_getter = portfolio_getter
    
    def stop_monitoring(self):
        """停止监控"""
        self.monitoring = False
    
    def check_once(self, portfolio: Dict) -> List[RiskAlert]:
        """
        执行一次风险检查
        
        Args:
            portfolio: 投资组合状态
            
        Returns:
            List[RiskAlert]: 新产生的预警
        """
        new_alerts = []
        
        # 1. 检查回撤
        drawdown_alerts = self._check_drawdown(portfolio)
        new_alerts.extend(drawdown_alerts)
        
        # 2. 检查亏损率
        loss_alerts = self._check_loss_rate(portfolio)
        new_alerts.extend(loss_alerts)
        
        # 3. 检查波动率
        volatility_alerts = self._check_volatility(portfolio)
        new_alerts.extend(volatility_alerts)
        
        # 4. 检查集中度
        concentration_alerts = self._check_concentration(portfolio)
        new_alerts.extend(concentration_alerts)
        
        # 5. 检查流动性
        liquidity_alerts = self._check_liquidity(portfolio)
        new_alerts.extend(liquidity_alerts)
        
        # 添加到历史记录
        self.alerts.extend(new_alerts)
        
        # 触发回调
        for alert in new_alerts:
            self._trigger_callbacks(alert)
        
        return new_alerts
    
    def _check_drawdown(self, portfolio: Dict) -> List[RiskAlert]:
        """检查回撤"""
        alerts = []
        
        drawdown = portfolio.get('current_drawdown', 0)
        
        if drawdown > self.thresholds['drawdown_critical']:
            alerts.append(RiskAlert(
                timestamp=datetime.now(),
                level=AlertLevel.CRITICAL,
                category='drawdown',
                message=f'回撤达到临界水平: {drawdown:.2%}',
                metrics={'drawdown': drawdown}
            ))
        elif drawdown > self.thresholds['drawdown_warning']:
            alerts.append(RiskAlert(
                timestamp=datetime.now(),
                level=AlertLevel.WARNING,
                category='drawdown',
                message=f'回撤超过预警线: {drawdown:.2%}',
                metrics={'drawdown': drawdown}
            ))
        
        return alerts
    
    def _check_loss_rate(self, portfolio: Dict) -> List[RiskAlert]:
        """检查亏损率"""
        alerts = []
        
        if 'recent_trades' not in portfolio:
            return alerts
        
        recent_trades = portfolio['recent_trades']
        if len(recent_trades) < 10:
            return alerts
        
        # 计算最近10笔交易的亏损率
        losing_trades = sum(1 for t in recent_trades[-10:] if t.get('pnl', 0) < 0)
        loss_rate = losing_trades / 10
        
        if loss_rate >= self.thresholds['loss_rate_warning']:
            alerts.append(RiskAlert(
                timestamp=datetime.now(),
                level=AlertLevel.WARNING,
                category='loss_rate',
                message=f'近期亏损率偏高: {loss_rate:.1%}',
                metrics={'loss_rate': loss_rate}
            ))
        
        return alerts
    
    def _check_volatility(self, portfolio: Dict) -> List[RiskAlert]:
        """检查波动率"""
        alerts = []
        
        volatility = portfolio.get('volatility', 0)
        
        if volatility > self.thresholds['volatility_warning']:
            alerts.append(RiskAlert(
                timestamp=datetime.now(),
                level=AlertLevel.WARNING,
                category='volatility',
                message=f'账户波动率过高: {volatility:.2%}',
                metrics={'volatility': volatility}
            ))
        
        return alerts
    
    def _check_concentration(self, portfolio: Dict) -> List[RiskAlert]:
        """检查持仓集中度"""
        alerts = []
        
        positions = portfolio.get('positions', {})
        if not positions:
            return alerts
        
        total_value = portfolio.get('total_value', 0)
        if total_value == 0:
            return alerts
        
        # 检查单个持仓占比
        for symbol, position in positions.items():
            position_value = abs(position.get('quantity', 0) * position.get('current_price', 0))
            concentration = position_value / total_value
            
            if concentration > self.thresholds['concentration_warning']:
                alerts.append(RiskAlert(
                    timestamp=datetime.now(),
                    level=AlertLevel.WARNING,
                    category='concentration',
                    message=f'{symbol} 持仓集中度过高: {concentration:.2%}',
                    metrics={'concentration': concentration, 'symbol': symbol}
                ))
        
        return alerts
    
    def _check_liquidity(self, portfolio: Dict) -> List[RiskAlert]:
        """检查流动性"""
        alerts = []
        
        cash_ratio = portfolio.get('cash_ratio', 0)
        
        if cash_ratio < 0.05:  # 现金比例低于5%
            alerts.append(RiskAlert(
                timestamp=datetime.now(),
                level=AlertLevel.WARNING,
                category='liquidity',
                message=f'账户流动性不足: {cash_ratio:.2%}',
                metrics={'cash_ratio': cash_ratio}
            ))
        
        return alerts
    
    def add_alert_callback(self, callback: Callable[[RiskAlert], None]):
        """添加预警回调函数"""
        self.alert_callbacks.append(callback)
    
    def _trigger_callbacks(self, alert: RiskAlert):
        """触发预警回调"""
        for callback in self.alert_callbacks:
            try:
                callback(alert)
            except Exception as e:
                print(f"Error in alert callback: {e}")
    
    def get_active_alerts(self, level: Optional[AlertLevel] = None) -> List[RiskAlert]:
        """获取活跃预警"""
        alerts = [a for a in self.alerts if not a.acknowledged]
        
        if level:
            alerts = [a for a in alerts if a.level == level]
        
        return sorted(alerts, key=lambda x: x.timestamp, reverse=True)
    
    def acknowledge_alert(self, alert: RiskAlert):
        """确认预警"""
        alert.acknowledged = True
    
    def get_alert_summary(self) -> Dict:
        """获取预警摘要"""
        active_alerts = self.get_active_alerts()
        
        return {
            'total_alerts': len(self.alerts),
            'active_alerts': len(active_alerts),
            'by_level': {
                'critical': len([a for a in active_alerts if a.level == AlertLevel.CRITICAL]),
                'error': len([a for a in active_alerts if a.level == AlertLevel.ERROR]),
                'warning': len([a for a in active_alerts if a.level == AlertLevel.WARNING]),
                'info': len([a for a in active_alerts if a.level == AlertLevel.INFO]),
            },
            'by_category': self._count_by_category(active_alerts)
        }
    
    def _count_by_category(self, alerts: List[RiskAlert]) -> Dict[str, int]:
        """按类别统计预警"""
        counts = {}
        for alert in alerts:
            counts[alert.category] = counts.get(alert.category, 0) + 1
        return counts


def monitor_risk(portfolio: Dict, thresholds: Optional[Dict] = None) -> List[RiskAlert]:
    """便捷函数：监控风险"""
    monitor = RiskMonitor()
    if thresholds:
        monitor.thresholds.update(thresholds)
    return monitor.check_once(portfolio)
