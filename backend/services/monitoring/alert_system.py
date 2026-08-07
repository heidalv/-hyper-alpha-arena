"""
Alert System - 告警系统

提供灵活的告警规则配置和多渠道通知：
1. 规则引擎 - 支持自定义告警规则
2. 通知系统 - 支持钉钉、日志等多种通知渠道
3. 告警管理 - 支持告警确认、静默、升级

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


class AlertLevel(Enum):
    """告警级别"""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


class AlertChannel(Enum):
    """告警通道"""
    LOG = "log"
    DINGTALK = "dingtalk"
    WEBHOOK = "webhook"
    EMAIL = "email"


class AlertCategory(Enum):
    """告警类别"""
    RISK = "risk"           # 风险相关
    TRADE = "trade"         # 交易相关
    SYSTEM = "system"       # 系统相关
    PERFORMANCE = "performance"  # 绩效相关


@dataclass
class AlertRule:
    """告警规则"""
    rule_id: str
    name: str
    description: str
    category: AlertCategory
    level: AlertLevel
    
    # 规则条件
    metric_name: str                    # 监控指标名称
    operator: str                       # 比较操作符: >, <, >=, <=, ==, !=
    threshold: float                    # 阈值
    
    # 规则配置
    enabled: bool = True
    cooldown_seconds: int = 300         # 冷却时间（秒）
    repeat_count: int = 1               # 连续触发次数后告警
    auto_resolve: bool = True           # 是否自动恢复
    
    # 通知配置
    channels: List[AlertChannel] = field(default_factory=lambda: [AlertChannel.LOG])
    
    # 运行时状态
    last_triggered: Optional[datetime] = None
    trigger_count: int = 0
    is_active: bool = False


@dataclass
class AlertNotification:
    """告警通知"""
    notification_id: str
    rule_id: str
    rule_name: str
    level: AlertLevel
    category: AlertCategory
    message: str
    details: Dict[str, Any]
    timestamp: datetime
    channels: List[AlertChannel]
    delivered: bool = False
    delivery_results: Dict[str, bool] = field(default_factory=dict)


class AlertSystem:
    """
    告警系统
    
    提供灵活的告警规则配置和多渠道通知
    """
    
    def __init__(self):
        # 告警规则
        self._rules: Dict[str, AlertRule] = {}
        
        # 活跃告警
        self._active_notifications: Dict[str, AlertNotification] = {}
        
        # 告警历史
        self._notification_history: deque = deque(maxlen=1000)
        
        # 静默规则 {rule_id: until_datetime}
        self._silenced_rules: Dict[str, datetime] = {}
        
        # 通知发送器
        self._channel_senders: Dict[AlertChannel, Callable] = {
            AlertChannel.LOG: self._send_log_alert,
            AlertChannel.DINGTALK: self._send_dingtalk_alert,
        }
        
        # 规则触发计数器 {rule_id: deque of timestamps}
        self._trigger_history: Dict[str, deque] = {}
        
        # 初始化默认规则
        self._init_default_rules()
        
        logger.info("[AlertSystem] Initialized")
    
    def _init_default_rules(self):
        """初始化默认告警规则"""
        default_rules = [
            # 风险告警
            AlertRule(
                rule_id="margin_critical",
                name="保证金危险",
                description="保证金使用率超过80%",
                category=AlertCategory.RISK,
                level=AlertLevel.CRITICAL,
                metric_name="margin_usage_pct",
                operator=">=",
                threshold=0.8,
                channels=[AlertChannel.LOG, AlertChannel.DINGTALK]
            ),
            AlertRule(
                rule_id="margin_warning",
                name="保证金警告",
                description="保证金使用率超过60%",
                category=AlertCategory.RISK,
                level=AlertLevel.WARNING,
                metric_name="margin_usage_pct",
                operator=">=",
                threshold=0.6,
                channels=[AlertChannel.LOG]
            ),
            AlertRule(
                rule_id="drawdown_critical",
                name="回撤危险",
                description="回撤超过10%",
                category=AlertCategory.RISK,
                level=AlertLevel.CRITICAL,
                metric_name="current_drawdown",
                operator=">=",
                threshold=0.10,
                channels=[AlertChannel.LOG, AlertChannel.DINGTALK]
            ),
            AlertRule(
                rule_id="drawdown_warning",
                name="回撤警告",
                description="回撤超过5%",
                category=AlertCategory.RISK,
                level=AlertLevel.WARNING,
                metric_name="current_drawdown",
                operator=">=",
                threshold=0.05,
                channels=[AlertChannel.LOG]
            ),
            AlertRule(
                rule_id="daily_loss_critical",
                name="日亏损危险",
                description="日亏损超过5%",
                category=AlertCategory.RISK,
                level=AlertLevel.CRITICAL,
                metric_name="daily_pnl_pct",
                operator="<=",
                threshold=-0.05,
                channels=[AlertChannel.LOG, AlertChannel.DINGTALK]
            ),
            # 系统告警
            AlertRule(
                rule_id="error_count_high",
                name="错误过多",
                description="1小时内错误超过10次",
                category=AlertCategory.SYSTEM,
                level=AlertLevel.WARNING,
                metric_name="error_count_1h",
                operator=">=",
                threshold=10,
                channels=[AlertChannel.LOG]
            ),
            AlertRule(
                rule_id="api_latency_high",
                name="API延迟高",
                description="API延迟超过3秒",
                category=AlertCategory.SYSTEM,
                level=AlertLevel.WARNING,
                metric_name="api_latency_ms",
                operator=">=",
                threshold=3000,
                channels=[AlertChannel.LOG]
            ),
        ]
        
        for rule in default_rules:
            self._rules[rule.rule_id] = rule
        
        logger.info(f"[AlertSystem] Initialized {len(default_rules)} default rules")
    
    def add_rule(self, rule: AlertRule):
        """添加告警规则"""
        self._rules[rule.rule_id] = rule
        logger.info(f"[AlertSystem] Added rule: {rule.rule_id} - {rule.name}")
    
    def remove_rule(self, rule_id: str):
        """删除告警规则"""
        if rule_id in self._rules:
            del self._rules[rule_id]
            logger.info(f"[AlertSystem] Removed rule: {rule_id}")
    
    def enable_rule(self, rule_id: str):
        """启用告警规则"""
        if rule_id in self._rules:
            self._rules[rule_id].enabled = True
    
    def disable_rule(self, rule_id: str):
        """禁用告警规则"""
        if rule_id in self._rules:
            self._rules[rule_id].enabled = False
    
    def silence_rule(self, rule_id: str, duration_minutes: int = 60):
        """静默告警规则"""
        until = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
        self._silenced_rules[rule_id] = until
        logger.info(f"[AlertSystem] Silenced rule {rule_id} until {until}")
    
    def unsilence_rule(self, rule_id: str):
        """取消静默"""
        if rule_id in self._silenced_rules:
            del self._silenced_rules[rule_id]
    
    def check_metrics(self, account_id: int, metrics: Dict[str, float]) -> List[AlertNotification]:
        """检查指标并触发告警"""
        triggered_notifications = []
        
        for rule_id, rule in self._rules.items():
            if not rule.enabled:
                continue
            
            # 检查静默
            if rule_id in self._silenced_rules:
                if datetime.now(timezone.utc) < self._silenced_rules[rule_id]:
                    continue
                else:
                    del self._silenced_rules[rule_id]
            
            # 检查冷却时间
            if rule.last_triggered:
                elapsed = (datetime.now(timezone.utc) - rule.last_triggered).total_seconds()
                if elapsed < rule.cooldown_seconds:
                    continue
            
            # 获取指标值
            metric_value = metrics.get(rule.metric_name)
            if metric_value is None:
                continue
            
            # 检查条件
            triggered = self._evaluate_condition(metric_value, rule.operator, rule.threshold)
            
            if triggered:
                # 记录触发
                self._record_trigger(rule_id)
                
                # 检查连续触发次数
                if self._get_recent_trigger_count(rule_id) >= rule.repeat_count:
                    notification = self._create_notification(rule, account_id, metric_value)
                    self._deliver_notification(notification)
                    triggered_notifications.append(notification)
                    
                    rule.last_triggered = datetime.now(timezone.utc)
                    rule.is_active = True
            else:
                # 自动恢复
                if rule.auto_resolve and rule.is_active:
                    self._resolve_rule(rule_id)
        
        return triggered_notifications
    
    def _evaluate_condition(self, value: float, operator: str, threshold: float) -> bool:
        """评估条件"""
        if operator == ">":
            return value > threshold
        elif operator == "<":
            return value < threshold
        elif operator == ">=":
            return value >= threshold
        elif operator == "<=":
            return value <= threshold
        elif operator == "==":
            return value == threshold
        elif operator == "!=":
            return value != threshold
        return False
    
    def _record_trigger(self, rule_id: str):
        """记录触发"""
        if rule_id not in self._trigger_history:
            self._trigger_history[rule_id] = deque(maxlen=10)
        self._trigger_history[rule_id].append(datetime.now(timezone.utc))
    
    def _get_recent_trigger_count(self, rule_id: str, window_seconds: int = 60) -> int:
        """获取最近触发次数"""
        if rule_id not in self._trigger_history:
            return 0
        
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
        return sum(1 for t in self._trigger_history[rule_id] if t > cutoff)
    
    def _create_notification(
        self,
        rule: AlertRule,
        account_id: int,
        metric_value: float
    ) -> AlertNotification:
        """创建告警通知"""
        notification_id = f"{rule.rule_id}_{account_id}_{int(time.time())}"
        
        message = (
            f"[{rule.level.value.upper()}] {rule.name}\n"
            f"账户: {account_id}\n"
            f"指标: {rule.metric_name} = {metric_value:.4f}\n"
            f"条件: {rule.operator} {rule.threshold}\n"
            f"描述: {rule.description}"
        )
        
        notification = AlertNotification(
            notification_id=notification_id,
            rule_id=rule.rule_id,
            rule_name=rule.name,
            level=rule.level,
            category=rule.category,
            message=message,
            details={
                "account_id": account_id,
                "metric_name": rule.metric_name,
                "metric_value": metric_value,
                "threshold": rule.threshold,
                "operator": rule.operator
            },
            timestamp=datetime.now(timezone.utc),
            channels=rule.channels
        )
        
        self._active_notifications[notification_id] = notification
        self._notification_history.append(notification)
        
        return notification
    
    def _deliver_notification(self, notification: AlertNotification):
        """发送告警通知"""
        for channel in notification.channels:
            sender = self._channel_senders.get(channel)
            if sender:
                try:
                    success = sender(notification)
                    notification.delivery_results[channel.value] = success
                except Exception as e:
                    logger.error(f"[AlertSystem] Failed to deliver via {channel.value}: {e}")
                    notification.delivery_results[channel.value] = False
        
        notification.delivered = True
    
    def _send_log_alert(self, notification: AlertNotification) -> bool:
        """发送日志告警"""
        level = notification.level
        if level == AlertLevel.EMERGENCY:
            logger.critical(notification.message)
        elif level == AlertLevel.CRITICAL:
            logger.error(notification.message)
        elif level == AlertLevel.WARNING:
            logger.warning(notification.message)
        else:
            logger.info(notification.message)
        return True
    
    def _send_dingtalk_alert(self, notification: AlertNotification) -> bool:
        """发送钉钉告警"""
        try:
            from services.dingtalk import get_dingtalk_bot_client
            
            bot = get_dingtalk_bot_client()
            if not bot:
                logger.warning("[AlertSystem] DingTalk bot not configured")
                return False
            
            # 构建钉钉消息
            title = f"[{notification.level.value.upper()}] {notification.rule_name}"
            content = notification.message
            
            # 根据告警级别选择是否 @ 所有人
            at_all = notification.level in (AlertLevel.CRITICAL, AlertLevel.EMERGENCY)
            
            result = bot.send_markdown(title, content, at_all=at_all)
            return result.get("errcode") == 0 if result else False
        except Exception as e:
            logger.error(f"[AlertSystem] DingTalk send error: {e}")
            return False
    
    def _resolve_rule(self, rule_id: str):
        """恢复规则"""
        if rule_id in self._rules:
            self._rules[rule_id].is_active = False
            logger.info(f"[AlertSystem] Rule {rule_id} auto-resolved")
    
    def acknowledge_notification(self, notification_id: str):
        """确认告警"""
        if notification_id in self._active_notifications:
            del self._active_notifications[notification_id]
    
    def get_active_notifications(
        self,
        level: Optional[AlertLevel] = None,
        category: Optional[AlertCategory] = None
    ) -> List[AlertNotification]:
        """获取活跃告警"""
        notifications = list(self._active_notifications.values())
        
        if level:
            notifications = [n for n in notifications if n.level == level]
        if category:
            notifications = [n for n in notifications if n.category == category]
        
        return sorted(notifications, key=lambda n: n.timestamp, reverse=True)
    
    def get_notification_history(self, limit: int = 100) -> List[AlertNotification]:
        """获取告警历史"""
        history = list(self._notification_history)
        return history[-limit:] if len(history) > limit else history
    
    def get_rules(self) -> List[AlertRule]:
        """获取所有规则"""
        return list(self._rules.values())
    
    def get_rule(self, rule_id: str) -> Optional[AlertRule]:
        """获取规则"""
        return self._rules.get(rule_id)
    
    def get_status(self) -> Dict[str, Any]:
        """获取系统状态"""
        return {
            "total_rules": len(self._rules),
            "enabled_rules": sum(1 for r in self._rules.values() if r.enabled),
            "active_rules": sum(1 for r in self._rules.values() if r.is_active),
            "silenced_rules": len(self._silenced_rules),
            "active_notifications": len(self._active_notifications),
            "notification_history_size": len(self._notification_history)
        }
    
    def trigger_manual_alert(
        self,
        account_id: int,
        level: AlertLevel,
        message: str,
        category: AlertCategory = AlertCategory.SYSTEM,
        channels: Optional[List[AlertChannel]] = None
    ) -> AlertNotification:
        """手动触发告警"""
        notification_id = f"manual_{account_id}_{int(time.time())}"
        
        notification = AlertNotification(
            notification_id=notification_id,
            rule_id="manual",
            rule_name="手动告警",
            level=level,
            category=category,
            message=message,
            details={"account_id": account_id, "manual": True},
            timestamp=datetime.now(timezone.utc),
            channels=channels or [AlertChannel.LOG]
        )
        
        self._deliver_notification(notification)
        self._active_notifications[notification_id] = notification
        self._notification_history.append(notification)
        
        return notification


# 全局实例
_alert_system: Optional[AlertSystem] = None


def get_alert_system() -> AlertSystem:
    """获取全局告警系统"""
    global _alert_system
    if _alert_system is None:
        _alert_system = AlertSystem()
    return _alert_system
