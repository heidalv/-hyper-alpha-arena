"""
钉钉推送服务核心逻辑
负责订阅交易事件、过滤、格式化、发送推送
"""
import asyncio
import json
import logging
from datetime import datetime, date
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, desc
from backend.database.models import DingTalkBot, DingTalkNotification, DingTalkNotificationStats, Account
from services.dingtalk.dingtalk_bot_client import DingTalkBotClientSync
from services.dingtalk.message_formatter import MessageFormatter
from services.dingtalk.rate_limiter import rate_limiter
from config.dingtalk_config import config

logger = logging.getLogger(__name__)


class NotificationService:
    """推送服务核心类"""

    def __init__(self, db: Session):
        """
        初始化推送服务

        Args:
            db: 数据库会话
        """
        self.db = db
        self.formatter = MessageFormatter()

    async def notify_position_opened(
        self,
        account_id: int,
        position_data: Dict
    ):
        """
        推送开仓通知

        Args:
            account_id: 账户ID
            position_data: 持仓数据
        """
        try:
            symbol = position_data.get('symbol', '')

            # 获取符合条件的机器人
            bots = self._filter_eligible_bots(
                event_type='position_opened',
                account_id=account_id,
                symbol=symbol
            )

            if not bots:
                logger.debug(f"没有符合条件的机器人推送开仓通知: account_id={account_id}, symbol={symbol}")
                return

            # 格式化消息
            message = self.formatter.format_position_opened(position_data)

            # 发送推送
            for bot in bots:
                await self._send_notification(
                    bot=bot,
                    message=message,
                    event_type='position_opened',
                    account_id=account_id,
                    position_id=position_data.get('position_id'),
                    symbol=symbol
                )

        except Exception as e:
            logger.error(f"推送开仓通知失败: {e}")

    async def notify_position_closed(
        self,
        account_id: int,
        position_data: Dict
    ):
        """
        推送平仓通知

        Args:
            account_id: 账户ID
            position_data: 持仓数据
        """
        try:
            symbol = position_data.get('symbol', '')

            # 获取符合条件的机器人
            bots = self._filter_eligible_bots(
                event_type='position_closed',
                account_id=account_id,
                symbol=symbol
            )

            if not bots:
                logger.debug(f"没有符合条件的机器人推送平仓通知: account_id={account_id}, symbol={symbol}")
                return

            # 格式化消息
            message = self.formatter.format_position_closed(position_data)

            # 发送推送
            for bot in bots:
                await self._send_notification(
                    bot=bot,
                    message=message,
                    event_type='position_closed',
                    account_id=account_id,
                    position_id=position_data.get('position_id'),
                    symbol=symbol
                )

        except Exception as e:
            logger.error(f"推送平仓通知失败: {e}")

    async def notify_stop_loss_triggered(
        self,
        account_id: int,
        position_data: Dict
    ):
        """
        推送止损触发通知

        Args:
            account_id: 账户ID
            position_data: 持仓数据
        """
        try:
            symbol = position_data.get('symbol', '')

            # 获取符合条件的机器人
            bots = self._filter_eligible_bots(
                event_type='stop_loss_triggered',
                account_id=account_id,
                symbol=symbol
            )

            if not bots:
                return

            # 格式化消息
            message = self.formatter.format_stop_loss_triggered(position_data)

            # 发送推送
            for bot in bots:
                await self._send_notification(
                    bot=bot,
                    message=message,
                    event_type='stop_loss_triggered',
                    account_id=account_id,
                    position_id=position_data.get('position_id'),
                    symbol=symbol
                )

        except Exception as e:
            logger.error(f"推送止损通知失败: {e}")

    async def notify_take_profit_triggered(
        self,
        account_id: int,
        position_data: Dict
    ):
        """
        推送止盈触发通知

        Args:
            account_id: 账户ID
            position_data: 持仓数据
        """
        try:
            symbol = position_data.get('symbol', '')

            # 获取符合条件的机器人
            bots = self._filter_eligible_bots(
                event_type='take_profit_triggered',
                account_id=account_id,
                symbol=symbol
            )

            if not bots:
                return

            # 格式化消息
            message = self.formatter.format_take_profit_triggered(position_data)

            # 发送推送
            for bot in bots:
                await self._send_notification(
                    bot=bot,
                    message=message,
                    event_type='take_profit_triggered',
                    account_id=account_id,
                    position_id=position_data.get('position_id'),
                    symbol=symbol
                )

        except Exception as e:
            logger.error(f"推送止盈通知失败: {e}")

    async def notify_volatility_alert(
        self,
        symbol: str,
        change_percent: float,
        current_price: float,
        timeframe: int = 300
    ):
        """
        推送波动预警

        Args:
            symbol: 交易对
            change_percent: 变化百分比
            current_price: 当前价格
            timeframe: 时间窗口（秒）
        """
        try:
            # 获取启用了波动预警的机器人
            bots = self.db.query(DingTalkBot).filter(
                and_(
                    DingTalkBot.enabled == True,
                    DingTalkBot.volatility_alert_enabled == True
                )
            ).all()

            if not bots:
                logger.debug(f"没有启用波动预警的机器人")
                return

            # 格式化消息
            message = self.formatter.format_volatility_alert(
                symbol=symbol,
                change_percent=change_percent,
                current_price=current_price,
                timeframe=timeframe
            )

            # 发送推送
            for bot in bots:
                # 检查交易对过滤
                if bot.symbol_filter:
                    symbol_filter = json.loads(bot.symbol_filter)
                    if symbol not in symbol_filter:
                        continue

                await self._send_notification(
                    bot=bot,
                    message=message,
                    event_type='volatility_alert',
                    account_id=None,
                    symbol=symbol
                )

        except Exception as e:
            logger.error(f"推送波动预警失败: {e}")

    async def send_position_summary(self):
        """
        发送持仓汇总（定时任务）
        """
        try:
            # 获取所有启用了定时推送的机器人
            bots = self.db.query(DingTalkBot).filter(
                and_(
                    DingTalkBot.enabled == True,
                    DingTalkBot.notify_on_position_scheduled == True
                )
            ).all()

            if not bots:
                logger.debug("没有启用定时持仓推送的机器人")
                return

            for bot in bots:
                try:
                    # 获取账户列表
                    if bot.account_ids:
                        account_ids = json.loads(bot.account_ids)
                        accounts = self.db.query(Account).filter(
                            Account.id.in_(account_ids)
                        ).all()
                    else:
                        accounts = self.db.query(Account).filter(
                            Account.is_active == 'true'
                        ).all()

                    for account in accounts:
                        # 获取账户的持仓（这里需要根据实际情况获取） # TODO: 从实际的数据源获取持仓
                        position_data = self._get_account_positions(account.id)

                        if not position_data:
                            continue

                        # 格式化消息
                        message = self.formatter.format_position_summary(
                            account_data={
                                'name': account.name,
                                'total_balance': position_data.get('total_balance', 0),
                                'available_balance': position_data.get('available_balance', 0),
                                'margin_used': position_data.get('margin_used', 0),
                                'total_unrealized_pnl': position_data.get('total_pnl', 0)
                            },
                            positions=position_data.get('positions', [])
                        )

                        # 发送推送
                        await self._send_notification(
                            bot=bot,
                            message=message,
                            event_type='position_summary',
                            account_id=account.id,
                            symbol=None
                        )

                except Exception as e:
                    logger.error(f"发送持仓汇总失败 (bot_id={bot.id}): {e}")

        except Exception as e:
            logger.error(f"发送持仓汇总任务失败: {e}")

    def _get_account_positions(self, account_id: int) -> Optional[Dict]:
        """
        获取账户持仓（从Binance/Hyperliquid API实时获取）

        Args:
            account_id: 账户ID

        Returns:
            持仓数据
        """
        try:
            from backend.database.models import Account

            # 获取账户信息
            account = self.db.query(Account).get(account_id)
            if not account:
                logger.warning(f"账户不存在: account_id={account_id}")
                return None

            # 尝试从Hyperliquid获取持仓
            if account.hyperliquid_enabled == "true":
                try:
                    from services.hyperliquid_environment import get_hyperliquid_client
                    
                    # 确定环境
                    env = account.hyperliquid_environment or "testnet"
                    
                    client = get_hyperliquid_client(self.db, account.id, override_environment=env)
                    
                    # 获取账户状态和持仓
                    account_state = client.get_account_state(self.db)
                    positions_data = client.get_positions(self.db)
                    
                    # 计算总额
                    total_equity = float(account_state.get('total_equity', 0))
                    available_balance = float(account_state.get('available_balance', 0))
                    margin_used = float(account_state.get('used_margin', 0))
                    
                    position_list = []
                    total_pnl = 0
                    
                    for pos in positions_data:
                        size = float(pos.get('szi', 0))
                        if size == 0:
                            continue
                            
                        unrealized_pnl = float(pos.get('unrealized_pnl', 0))
                        total_pnl += unrealized_pnl
                        
                        position_list.append({
                            'symbol': pos.get('coin', ''),
                            'side': 'long' if size > 0 else 'short',
                            'size': abs(size),
                            'entry_price': float(pos.get('entry_price', 0)),
                            'mark_price': 0, # 暂时无法获取实时标记价格
                            'leverage': int(pos.get('leverage', 1)),
                            'unrealized_pnl': unrealized_pnl
                        })
                        
                    if position_list or total_equity > 0:
                        return {
                            'total_balance': total_equity,
                            'available_balance': available_balance,
                            'margin_used': margin_used,
                            'total_pnl': total_pnl,
                            'positions': position_list
                        }

                except Exception as e:
                    logger.warning(f"从Hyperliquid获取持仓失败: {e}", exc_info=True)

            # Binance removed (Phase 1) - skip Binance branch; fall through to DB

            # 从本地数据库获取
            from backend.database.models import Position

            total_balance = float(account.current_cash) + float(account.frozen_cash)

            positions = self.db.query(Position).filter(
                Position.account_id == account_id,
                Position.quantity > 0
            ).all()

            position_list = []
            total_pnl = 0

            for pos in positions:
                qty = float(pos.quantity)
                if qty <= 0:
                    continue

                avg_cost = float(pos.avg_cost)
                position_value = qty * avg_cost

                position_list.append({
                    'symbol': pos.symbol,
                    'side': 'long',
                    'size': qty,
                    'entry_price': avg_cost,
                    'mark_price': avg_cost,
                    'leverage': 1,
                    'unrealized_pnl': 0
                })

            # 只有在有持仓或余额时才返回
            if not position_list and total_balance <= 0:
                logger.debug(f"账户无余额且无持仓: account_id={account_id}, name={account.name}")
                return None

            return {
                'total_balance': total_balance,
                'available_balance': float(account.current_cash),
                'margin_used': 0,
                'total_pnl': total_pnl,
                'positions': position_list
            }

        except Exception as e:
            logger.error(f"获取账户持仓失败: {e}")
            return None

    async def _send_notification(
        self,
        bot: DingTalkBot,
        message: Dict,
        event_type: str,
        account_id: Optional[int],
        position_id: Optional[str] = None,
        symbol: Optional[str] = None
    ):
        """
        发送推送（内部方法）

        Args:
            bot: 机器人配置
            message: 格式化后的消息
            event_type: 事件类型
            account_id: 账户ID
            position_id: 持仓ID
            symbol: 交易对
        """
        # 检查频率限制
        allowed = await rate_limiter.acquire(bot.id, bot.max_notifications_per_hour)
        if not allowed:
            logger.debug(f"频率限制: bot_id={bot.id}, 推送被拒绝")
            return

        # 创建推送记录
        notification = DingTalkNotification(
            bot_id=bot.id,
            account_id=account_id,
            event_type=event_type,
            message_type=message.get('message_type', 'text'),
            title=message.get('title'),
            content=message.get('content'),
            raw_data=json.dumps(message.get('raw_data', {})),
            status='pending',
            position_id=position_id,
            symbol=symbol
        )

        self.db.add(notification)
        self.db.commit()

        try:
            # 发送到钉钉
            client = DingTalkBotClientSync(
                webhook_url=bot.webhook_url,
                sign_secret=bot.sign_secret
            )

            start_time = datetime.now()

            # [2026-07-11 修复] DingTalkBotClientSync 内部用同步 requests.post # （最长 10s 超时）。这里原来是在 async def 方法里直接同步调用， # 会整整冻住 asyncio 事件循环最多 10s——冻住期间，全进程里其他 # 协程已经打开但还没来得及 commit/close 的数据库会话，都会被 # DB LeakGuard 误判为"泄漏"（其实只是排不上队），这正是观测到 # 多张毫不相关的表（strategy_memories/paper_orders/...）同时 # 报警的根因之一。改为丢到线程池执行，不阻塞事件循环。
            if message['message_type'] == 'text':
                response = await asyncio.to_thread(client.send_text, message['content'])
            elif message['message_type'] == 'markdown':
                response = await asyncio.to_thread(
                    client.send_markdown, message['title'], message['content']
                )
            elif message['message_type'] == 'card':
                response = await asyncio.to_thread(
                    client.send_card,
                    message['title'],
                    '',  # message_url 可选
                )
            else:
                response = await asyncio.to_thread(client.send_text, message['content'])

            end_time = datetime.now()
            response_time_ms = int((end_time - start_time).total_seconds() * 1000)

            # 更新推送记录
            notification.response_code = response.get('errcode')
            notification.response_body = json.dumps(response)
            notification.sent_at = datetime.now()

            if response.get('errcode') == 0:
                notification.status = 'sent'
                notification.dingtalk_msg_id = response.get('msgid', '')

                # 更新机器人统计
                bot.total_sent_count += 1
                bot.last_sent_at = datetime.now()

                logger.info(f"推送成功: notification_id={notification.id}, bot_id={bot.id}, event_type={event_type}")

                # 更新今日统计
                self._update_daily_stats(bot.id, event_type, True, response_time_ms)

            else:
                notification.status = 'failed'
                notification.error_message = response.get('errmsg', '未知错误')

                bot.last_error_at = datetime.now()
                bot.last_error_message = notification.error_message

                logger.error(f"推送失败: notification_id={notification.id}, bot_id={bot.id}, error={notification.error_message}")

                # 更新今日统计
                self._update_daily_stats(bot.id, event_type, False, response_time_ms, notification.error_message)

            self.db.commit()

        except Exception as e:
            logger.error(f"发送推送异常: {e}")

            notification.status = 'failed'
            notification.error_message = str(e)

            bot.last_error_at = datetime.now()
            bot.last_error_message = str(e)

            self.db.commit()

    def _filter_eligible_bots(
        self,
        event_type: str,
        account_id: int,
        symbol: str
    ) -> List[DingTalkBot]:
        """
        过滤符合条件的机器人

        Args:
            event_type: 事件类型
            account_id: 账户ID
            symbol: 交易对

        Returns:
            符合条件的机器人列表
        """
        # 映射事件类型到配置字段
        event_field_map = {
            'position_opened': 'notify_on_position_opened',
            'position_closed': 'notify_on_position_closed',
            'stop_loss_triggered': 'notify_on_stop_loss_triggered',
            'take_profit_triggered': 'notify_on_take_profit_triggered',
            'volatility_alert': 'volatility_alert_enabled',
            'position_summary': 'notify_on_position_scheduled'
        }

        field_name = event_field_map.get(event_type)
        if not field_name:
            return []

        # 构建查询
        query = self.db.query(DingTalkBot).filter(
            and_(
                DingTalkBot.enabled == True,
                getattr(DingTalkBot, field_name) == True
            )
        )

        # 获取所有启用的机器人
        bots = query.all()

        # 过滤账户和交易对
        eligible_bots = []
        for bot in bots:
            # 检查账户过滤
            if bot.account_ids:
                account_ids = json.loads(bot.account_ids)
                if account_id not in account_ids:
                    continue

            # 检查交易对过滤
            if bot.symbol_filter:
                symbol_filter = json.loads(bot.symbol_filter)
                if symbol not in symbol_filter:
                    continue

            eligible_bots.append(bot)

        return eligible_bots

    def _update_daily_stats(
        self,
        bot_id: int,
        event_type: str,
        success: bool,
        response_time_ms: int,
        error_message: str = None
    ):
        """
        更新每日统计

        Args:
            bot_id: 机器人ID
            event_type: 事件类型
            success: 是否成功
            response_time_ms: 响应时间
            error_message: 错误信息
        """
        try:
            today = date.today()

            # 获取或创建今日统计
            stats = self.db.query(DingTalkNotificationStats).filter(
                and_(
                    DingTalkNotificationStats.bot_id == bot_id,
                    DingTalkNotificationStats.date == today
                )
            ).first()

            if not stats:
                stats = DingTalkNotificationStats(
                    bot_id=bot_id,
                    date=today,
                    total_sent=0,
                    total_success=0,
                    total_failed=0,
                    event_breakdown='{}',
                    error_breakdown='{}'
                )
                self.db.add(stats)

            # 更新统计
            stats.total_sent += 1
            if success:
                stats.total_success += 1
            else:
                stats.total_failed += 1

            # 更新事件分布
            event_breakdown = json.loads(stats.event_breakdown or '{}')
            event_breakdown[event_type] = event_breakdown.get(event_type, 0) + 1
            stats.event_breakdown = json.dumps(event_breakdown)

            # 更新响应时间
            if stats.avg_response_time_ms is None:
                stats.avg_response_time_ms = response_time_ms
            else:
                # 简单的移动平均
                stats.avg_response_time_ms = int((stats.avg_response_time_ms + response_time_ms) / 2)

            if stats.max_response_time_ms is None or response_time_ms > stats.max_response_time_ms:
                stats.max_response_time_ms = response_time_ms

            # 更新错误分布
            if error_message:
                error_breakdown = json.loads(stats.error_breakdown or '{}')
                error_type = 'network' if 'network' in error_message.lower() else 'api' if 'api' in error_message.lower() else 'other'
                error_breakdown[error_type] = error_breakdown.get(error_type, 0) + 1
                stats.error_breakdown = json.dumps(error_breakdown)

            self.db.commit()

        except Exception as e:
            logger.error(f"更新每日统计失败: {e}")

    async def retry_failed_notifications(self):
        """
        重试失败的推送（定时任务）
        """
        try:
            # 获取需要重试的推送记录
            failed_notifications = self.db.query(DingTalkNotification).filter(
                and_(
                    DingTalkNotification.status == 'failed',
                    DingTalkNotification.retry_count < config.max_retry_count
                )
            ).limit(100).all()

            # [2026-07-17 修复] .all() 已把结果整批取到内存，上面这条只读 SELECT # 打开的事务不再需要占着连接——下面循环里每条记录都要
            # `await asyncio.sleep(retry_delay_seconds)`，如果这里不先提交/结束掉，
            # 这个事务会一直挂着陪跑整个循环（最多 100 条 × 60s = 慢慢累积到几千秒的 # idle-in-transaction，正是 DB LeakGuard 报警里 dingtalk_notifications/ # dingtalk_bots 查询挂 2 小时+ 的根因）。
            self.db.commit()

            for notification in failed_notifications:
                try:
                    # 等待一段时间后重试
                    await asyncio.sleep(config.retry_delay_seconds)

                    # 获取机器人配置
                    bot = self.db.query(DingTalkBot).get(notification.bot_id)
                    if not bot or not bot.enabled:
                        # [2026-07-17 修复] 之前这里直接 continue，会把上面 get() # 打开的事务原样带进下一轮 sleep；如果连续多条记录都命中这个 # 分支（比如机器人被禁用/删除），事务会跨越 N×60s 一直挂在 # "idle in transaction"。这里提交掉这条只读事务再继续。
                        self.db.commit()
                        continue

                    # 重新发送
                    message = {
                        'message_type': notification.message_type,
                        'title': notification.title,
                        'content': notification.content,
                        'raw_data': json.loads(notification.raw_data) if notification.raw_data else {}
                    }

                    await self._send_notification(
                        bot=bot,
                        message=message,
                        event_type=notification.event_type,
                        account_id=notification.account_id,
                        position_id=notification.position_id,
                        symbol=notification.symbol
                    )

                    # 更新重试次数
                    notification.retry_count += 1
                    self.db.commit()

                except Exception as e:
                    logger.error(f"重试推送失败 (notification_id={notification.id}): {e}")
                    # [2026-07-17 修复] 异常路径同样要清掉事务状态，否则下一轮 # sleep 又会带着一个悬空/中止的事务继续挂着。
                    try:
                        self.db.rollback()
                    except Exception:
                        pass

        except Exception as e:
            logger.error(f"重试任务失败: {e}")


# 全局推送服务实例
_notification_service: Optional[NotificationService] = None


def get_notification_service(db: Session) -> NotificationService:
    """
    获取推送服务实例

    Args:
        db: 数据库会话

    Returns:
        推送服务实例
    """
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService(db)
    return _notification_service
