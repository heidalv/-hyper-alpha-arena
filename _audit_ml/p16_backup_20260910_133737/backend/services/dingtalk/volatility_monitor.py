"""
价格波动监控器
实时监控价格波动，超过阈值时触发预警
"""
import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from backend.database.connection import SessionLocal
from backend.database.models import DingTalkBot
from services.dingtalk.notification_service import get_notification_service

logger = logging.getLogger(__name__)


class VolatilityMonitor:
    """价格波动监控器"""

    def __init__(self):
        """初始化波动监控器"""
        # symbol -> deque of (timestamp, price)
        self.price_cache: Dict[str, deque] = {}

        # 配置
        self.max_cache_size = 1000  # 每个交易对最多缓存1000个价格点
        self.check_interval = 10  # 每10秒检查一次波动

        # 运行状态
        self.running = False
        self.task = None

    async def start(self):
        """启动监控任务"""
        if self.running:
            logger.warning("波动监控器已在运行")
            return

        self.running = True
        self.task = asyncio.create_task(self._monitor_loop())
        logger.info("波动监控器已启动")

    async def stop(self):
        """停止监控任务"""
        self.running = False

        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

        logger.info("波动监控器已停止")

    async def update_price(self, symbol: str, price: float, timestamp: Optional[int] = None):
        """
        更新价格数据

        Args:
            symbol: 交易对符号
            price: 当前价格
            timestamp: 时间戳（秒），可选
        """
        try:
            if timestamp is None:
                timestamp = int(time.time())

            # 初始化deque
            if symbol not in self.price_cache:
                self.price_cache[symbol] = deque(maxlen=self.max_cache_size)

            # 添加价格点
            self.price_cache[symbol].append((timestamp, price))

            # 清理过期数据（保留最近1小时）
            cutoff_time = timestamp - 3600
            while self.price_cache[symbol] and self.price_cache[symbol][0][0] < cutoff_time:
                self.price_cache[symbol].popleft()

            logger.debug(f"更新价格: {symbol} = ${price}")

        except Exception as e:
            logger.error(f"更新价格失败: {e}")

    async def _monitor_loop(self):
        """监控循环（后台任务）"""
        while self.running:
            try:
                await asyncio.sleep(self.check_interval)
                await self._check_all_symbols()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"监控循环错误: {e}")

    async def _check_all_symbols(self):
        """检查所有交易对的波动"""
        try:
            db = SessionLocal()
            try:
                # 获取启用了波动预警的机器人
                bots = db.query(DingTalkBot).filter(
                    DingTalkBot.volatility_alert_enabled == True
                ).all()

                if not bots:
                    return

                # 获取需要监控的交易对
                symbols_to_monitor = set()

                for bot in bots:
                    if bot.symbol_filter:
                        import json
                        symbol_filter = json.loads(bot.symbol_filter)
                        symbols_to_monitor.update(symbol_filter)
                    else:
                        # 如果没有过滤，监控所有有价格数据的交易对
                        symbols_to_monitor.update(self.price_cache.keys())

                # 检查每个交易对
                for symbol in symbols_to_monitor:
                    if symbol not in self.price_cache:
                        continue

                    # 为每个机器人检查该交易对
                    for bot in bots:
                        # 检查交易对过滤
                        if bot.symbol_filter:
                            symbol_filter = json.loads(bot.symbol_filter)
                            if symbol not in symbol_filter:
                                continue

                        # 计算波动率
                        volatility = self._calculate_volatility(
                            symbol,
                            int(bot.volatility_timeframe)
                        )

                        if volatility is None:
                            continue

                        # 检查是否超过阈值
                        threshold = float(bot.volatility_threshold)
                        if abs(volatility) >= threshold:
                            # 获取当前价格
                            current_price = self._get_current_price(symbol)

                            if current_price is None:
                                continue

                            # 发送预警
                            logger.info(f"触发波动预警: {symbol} 波动率={volatility:.2f}%")

                            notification_service = get_notification_service(db)
                            await notification_service.notify_volatility_alert(
                                symbol=symbol,
                                change_percent=volatility,
                                current_price=current_price,
                                timeframe=int(bot.volatility_timeframe)
                            )

            finally:
                db.close()

        except Exception as e:
            logger.error(f"检查所有交易对失败: {e}")

    def _calculate_volatility(self, symbol: str, timeframe: int) -> Optional[float]:
        """
        计算指定时间窗口的波动率

        Args:
            symbol: 交易对
            timeframe: 时间窗口（秒）

        Returns:
            波动百分比，如果没有足够数据则返回None
        """
        try:
            if symbol not in self.price_cache:
                return None

            cache = self.price_cache[symbol]
            if len(cache) < 2:
                return None

            now = int(time.time())
            cutoff_time = now - timeframe

            # 找到时间窗口内的价格范围
            prices_in_window = []
            for timestamp, price in cache:
                if timestamp >= cutoff_time:
                    prices_in_window.append(price)
                elif timestamp < cutoff_time:
                    # 缓存是按时间排序的，可以提前退出
                    break

            if len(prices_in_window) < 2:
                return None

            # 计算价格变化百分比
            start_price = prices_in_window[0]
            end_price = prices_in_window[-1]

            if start_price == 0:
                return None

            volatility = ((end_price - start_price) / start_price) * 100

            logger.debug(f"计算波动率: {symbol} {timeframe}s = {volatility:.2f}%")

            return volatility

        except Exception as e:
            logger.error(f"计算波动率失败: {e}")
            return None

    def _get_current_price(self, symbol: str) -> Optional[float]:
        """
        获取当前价格

        Args:
            symbol: 交易对

        Returns:
            当前价格，如果没有数据则返回None
        """
        try:
            if symbol not in self.price_cache:
                return None

            cache = self.price_cache[symbol]
            if not cache:
                return None

            # 返回最新的价格
            return cache[-1][1]

        except Exception as e:
            logger.error(f"获取当前价格失败: {e}")
            return None

    def get_monitored_symbols(self) -> List[str]:
        """
        获取正在监控的交易对列表

        Returns:
            交易对列表
        """
        return list(self.price_cache.keys())

    def get_price_history(
        self,
        symbol: str,
        duration: int = 3600
    ) -> List[Tuple[int, float]]:
        """
        获取价格历史

        Args:
            symbol: 交易对
            duration: 时间范围（秒）

        Returns:
            [(timestamp, price), ...] 列表
        """
        try:
            if symbol not in self.price_cache:
                return []

            cache = self.price_cache[symbol]
            cutoff_time = int(time.time()) - duration

            history = [
                (ts, price)
                for ts, price in cache
                if ts >= cutoff_time
            ]

            return history

        except Exception as e:
            logger.error(f"获取价格历史失败: {e}")
            return []

    def clear_cache(self, symbol: Optional[str] = None):
        """
        清除缓存

        Args:
            symbol: 交易对，如果为None则清除所有
        """
        if symbol:
            if symbol in self.price_cache:
                self.price_cache[symbol].clear()
                logger.debug(f"清除价格缓存: {symbol}")
        else:
            self.price_cache.clear()
            logger.debug("清除所有价格缓存")


# 全局波动监控器实例
_volatility_monitor: Optional[VolatilityMonitor] = None


def get_volatility_monitor() -> VolatilityMonitor:
    """
    获取波动监控器实例

    Returns:
        波动监控器实例
    """
    global _volatility_monitor
    if _volatility_monitor is None:
        _volatility_monitor = VolatilityMonitor()
    return _volatility_monitor
