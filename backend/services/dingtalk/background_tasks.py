"""
钉钉推送后台任务管理器
管理定时推送、重试、波动监控等后台任务
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.database.connection import SessionLocal
from backend.database.models import DingTalkBot
from services.dingtalk.notification_service import get_notification_service
from services.dingtalk.volatility_monitor import get_volatility_monitor
from services.dingtalk.rate_limiter import rate_limiter
from config.dingtalk_config import config

logger = logging.getLogger(__name__)


class DingTalkBackgroundTasks:
    """钉钉推送后台任务管理器"""

    def __init__(self):
        """初始化后台任务管理器"""
        self.running = False
        self.tasks = []

    async def start(self):
        """启动所有后台任务"""
        if self.running:
            logger.warning("后台任务已在运行")
            return

        self.running = True

        # 启动各项任务
        self.tasks.append(asyncio.create_task(self._position_summary_task()))
        self.tasks.append(asyncio.create_task(self._retry_failed_task()))
        self.tasks.append(asyncio.create_task(self._rate_limiter_refill_task()))
        self.tasks.append(asyncio.create_task(self._cleanup_old_notifications_task()))

        logger.info("钉钉推送后台任务已启动")

    async def stop(self):
        """停止所有后台任务"""
        if not self.running:
            return

        self.running = False

        # 取消所有任务
        for task in self.tasks:
            if not task.done():
                task.cancel()

        # 等待所有任务完成
        await asyncio.gather(*self.tasks, return_exceptions=True)

        self.tasks.clear()
        logger.info("钉钉推送后台任务已停止")

    def _is_quiet_hours(self) -> bool:
        """
        检查当前是否为免打扰时段（北京时间23:00-08:00）

        Returns:
            True: 免打扰时段（不推送持仓汇总）
            False: 正常时段（可以推送）
        """
        # 获取UTC时间并转换为北京时间（UTC+8）
        utc_now = datetime.now(timezone.utc)
        beijing_tz = timezone(timedelta(hours=8))
        beijing_now = utc_now.astimezone(beijing_tz)
        current_hour = beijing_now.hour

        # 晚上11点到早上8点为免打扰时段
        is_quiet = 23 <= current_hour or current_hour < 8

        if is_quiet:
            logger.debug(f"北京时间: {beijing_now.strftime('%Y-%m-%d %H:%M:%S')} (小时: {current_hour}) - 免打扰时段")

        return is_quiet

    async def _position_summary_task(self):
        """
        定时持仓汇总任务
        每个机器人按照配置的间隔发送持仓汇总

        注意：晚上11点到早上8点（北京时间）为免打扰时段，
        此时段不推送持仓汇总，但开仓/平仓通知仍正常推送
        """
        logger.info("持仓汇总任务已启动")

        while self.running:
            try:
                db = SessionLocal()
                try:
                    # 检查是否为免打扰时段
                    if self._is_quiet_hours():
                        logger.debug("当前为免打扰时段（23:00-08:00），跳过持仓汇总推送")
                        await asyncio.sleep(300)  # 免打扰时段，每5分钟检查一次
                        continue

                    # 获取所有启用了定时推送的机器人
                    bots = db.query(DingTalkBot).filter(
                        DingTalkBot.enabled == True,
                        DingTalkBot.notify_on_position_scheduled == True
                    ).all()

                    if not bots:
                        await asyncio.sleep(60)  # 没有机器人时，每分钟检查一次
                        continue

                    # 找出最小的间隔时间
                    min_interval = min(
                        bot.position_schedule_interval for bot in bots
                    )

                    # 检查每个机器人是否到了发送时间
                    now = datetime.now()
                    for bot in bots:
                        if bot.last_sent_at is None:
                            # 从未发送过，立即发送
                            await self._send_position_summary_for_bot(bot.id, db)
                        else:
                            # 计算距离上次发送的时间
                            elapsed = (now - bot.last_sent_at).total_seconds()
                            if elapsed >= bot.position_schedule_interval:
                                await self._send_position_summary_for_bot(bot.id, db)

                    # 等待一段时间后再次检查 # 等待时间是最小间隔的1/10，最少10秒，最多1分钟
                    sleep_time = max(10, min(60, min_interval / 10))
                    await asyncio.sleep(sleep_time)

                finally:
                    db.close()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"持仓汇总任务错误: {e}")
                await asyncio.sleep(60)

    async def _send_position_summary_for_bot(self, bot_id: int, db):
        """
        为指定机器人发送持仓汇总

        Args:
            bot_id: 机器人ID
            db: 数据库会话
        """
        try:
            notification_service = get_notification_service(db)
            await notification_service.send_position_summary()

        except Exception as e:
            logger.error(f"发送持仓汇总失败 (bot_id={bot_id}): {e}")

    async def _retry_failed_task(self):
        """
        重试失败的推送任务
        定期检查并重试失败的推送
        """
        logger.info("重试任务已启动")

        while self.running:
            try:
                # 等待重试延迟时间
                await asyncio.sleep(config.retry_delay_seconds)

                db = SessionLocal()
                try:
                    notification_service = get_notification_service(db)
                    await notification_service.retry_failed_notifications()

                finally:
                    db.close()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"重试任务错误: {e}")

    async def _rate_limiter_refill_task(self):
        """
        频率限制器令牌补充任务
        定期补充令牌
        """
        logger.info("频率限制器补充任务已启动")

        while self.running:
            try:
                await asyncio.sleep(60)  # 每分钟补充一次

                async with rate_limiter.lock:
                    for bot_id in list(rate_limiter.max_tokens.keys()):
                        await rate_limiter._refill(bot_id)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"令牌补充任务错误: {e}")

    async def _cleanup_old_notifications_task(self):
        """
        清理旧推送记录任务
        定期清理超过30天的推送记录
        """
        logger.info("清理任务已启动")

        while self.running:
            try:
                # 每天执行一次
                await asyncio.sleep(86400)

                db = SessionLocal()
                try:
                    from backend.database.models import DingTalkNotification
                    from datetime import timedelta

                    # 删除30天前的记录
                    cutoff_date = datetime.now() - timedelta(days=30)

                    deleted_count = db.query(DingTalkNotification).filter(
                        DingTalkNotification.created_at < cutoff_date
                    ).delete()

                    if deleted_count > 0:
                        db.commit()
                        logger.info(f"清理了 {deleted_count} 条旧推送记录")

                finally:
                    db.close()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"清理任务错误: {e}")


# 全局后台任务管理器实例
_background_tasks: Optional[DingTalkBackgroundTasks] = None


def get_background_tasks() -> DingTalkBackgroundTasks:
    """
    获取后台任务管理器实例

    Returns:
        后台任务管理器实例
    """
    global _background_tasks
    if _background_tasks is None:
        _background_tasks = DingTalkBackgroundTasks()
    return _background_tasks
