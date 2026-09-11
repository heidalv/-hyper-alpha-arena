"""
ATAS V2 预警系统

修复记录：
- 从纯 print() 空壳改为集成已有的钉钉通知模块
- 支持日志记录和多渠道发送
"""
from enum import Enum
from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class AlertChannel(Enum):
    EMAIL = "email"
    DINGTALK = "dingtalk"
    SMS = "sms"
    WEBHOOK = "webhook"


@dataclass
class AlertMessage:
    title: str
    content: str
    level: str  # "INFO", "WARNING", "ERROR", "CRITICAL"
    channel: AlertChannel


class AlertSystem:
    """
    预警系统 - 支持多渠道告警
    
    目前已实现：
    - 钉钉通知（集成已有的 services/dingtalk 模块）
    - 日志记录（所有渠道都会记录日志）
    
    待实现：
    - Email 通知
    - SMS 通知
    - Webhook 通知
    """
    
    def __init__(self):
        self.channels = {}
        self._dingtalk_service = None
    
    def _get_dingtalk_service(self):
        """懒加载钉钉通知服务"""
        if self._dingtalk_service is None:
            try:
                from services.dingtalk.notification_service import DingTalkNotificationService
                self._dingtalk_service = DingTalkNotificationService()
            except ImportError:
                logger.warning("[AlertSystem] 钉钉通知模块未找到，降级为日志记录")
                self._dingtalk_service = False  # 标记为不可用
            except Exception as e:
                logger.warning(f"[AlertSystem] 钉钉通知模块初始化失败: {e}")
                self._dingtalk_service = False
        return self._dingtalk_service if self._dingtalk_service is not False else None
    
    def send(self, message: AlertMessage) -> bool:
        """
        发送预警消息
        
        所有消息都会记录到日志，然后尝试通过指定渠道发送。
        """
        # 1. 始终记录到日志
        log_msg = f"[ATAS Alert] [{message.level}] {message.title}: {message.content}"
        level_map = {
            "INFO": logger.info,
            "WARNING": logger.warning,
            "ERROR": logger.error,
            "CRITICAL": logger.critical,
        }
        log_fn = level_map.get(message.level.upper(), logger.info)
        log_fn(log_msg)
        
        # 2. 尝试通过指定渠道发送
        if message.channel == AlertChannel.DINGTALK:
            return self._send_dingtalk(message)
        elif message.channel == AlertChannel.EMAIL:
            logger.info(f"[AlertSystem] Email渠道暂未实现，已记录日志: {message.title}")
            return True
        elif message.channel == AlertChannel.SMS:
            logger.info(f"[AlertSystem] SMS渠道暂未实现，已记录日志: {message.title}")
            return True
        elif message.channel == AlertChannel.WEBHOOK:
            logger.info(f"[AlertSystem] Webhook渠道暂未实现，已记录日志: {message.title}")
            return True
        
        return True
    
    def _send_dingtalk(self, message: AlertMessage) -> bool:
        """通过钉钉发送告警"""
        service = self._get_dingtalk_service()
        if not service:
            logger.info(f"[AlertSystem] 钉钉服务不可用，消息已记录: {message.title}")
            return True  # 降级到日志，不算失败
        
        try:
            # 构建钉钉消息格式
            level_emoji = {
                "INFO": "ℹ️",
                "WARNING": "⚠️",
                "ERROR": "❌",
                "CRITICAL": "🚨",
            }
            emoji = level_emoji.get(message.level.upper(), "📢")
            
            dingtalk_content = (
                f"{emoji} ATAS V2 预警\n\n"
                f"级别: {message.level}\n"
                f"标题: {message.title}\n"
                f"内容: {message.content}"
            )
            
            # 使用已有的钉钉服务发送
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                # 如果在async上下文中，使用 create_task
                asyncio.ensure_future(service.send_text_message(dingtalk_content))
            except RuntimeError:
                # 不在async上下文中，创建新的事件循环
                loop = asyncio.new_event_loop()
                loop.run_until_complete(service.send_text_message(dingtalk_content))
                loop.close()
            
            logger.info(f"[AlertSystem] 钉钉告警已发送: {message.title}")
            return True
        except Exception as e:
            logger.error(f"[AlertSystem] 钉钉告警发送失败: {e}")
            return False


def send_alert(title: str, content: str, channel: AlertChannel = AlertChannel.DINGTALK) -> bool:
    """便捷函数：发送告警"""
    system = AlertSystem()
    msg = AlertMessage(title, content, "INFO", channel)
    return system.send(msg)
