"""
频率限制器 - 令牌桶算法
用于控制钉钉推送频率，防止触发限流
"""
import asyncio
import time
import logging
from typing import Dict
from collections import defaultdict

logger = logging.getLogger(__name__)


class RateLimiter:
    """频率限制器 - 令牌桶算法"""

    def __init__(self):
        """初始化频率限制器"""
        # bot_id -> tokens_count
        self.tokens: Dict[int, float] = defaultdict(float)
        # bot_id -> max_tokens
        self.max_tokens: Dict[int, int] = {}
        # bot_id -> last_refill_time
        self.last_refill: Dict[int, float] = {}

        # 锁
        self.lock = asyncio.Lock()

    def configure(self, bot_id: int, max_per_hour: int):
        """
        配置机器人的限流参数

        Args:
            bot_id: 机器人ID
            max_per_hour: 每小时最大推送数
        """
        self.max_tokens[bot_id] = max_per_hour
        self.tokens[bot_id] = float(max_per_hour)  # 初始填满令牌
        self.last_refill[bot_id] = time.time()

        logger.debug(f"配置频率限制: bot_id={bot_id}, max_per_hour={max_per_hour}")

    async def acquire(self, bot_id: int, max_per_hour: int = None) -> bool:
        """
        获取发送许可（消耗一个令牌）

        Args:
            bot_id: 机器人ID
            max_per_hour: 每小时最大推送数（可选，覆盖配置）

        Returns:
            是否获取成功
        """
        async with self.lock:
            # 如果没有配置过，先配置
            if bot_id not in self.max_tokens:
                max_per_hour = max_per_hour or 20
                self.configure(bot_id, max_per_hour)

            # 补充令牌
            await self._refill(bot_id)

            # 检查是否有足够令牌
            if self.tokens[bot_id] >= 1:
                self.tokens[bot_id] -= 1
                logger.debug(f"频率限制: bot_id={bot_id}, 剩余令牌={self.tokens[bot_id]:.1f}")
                return True
            else:
                logger.warning(f"频率限制: bot_id={bot_id}, 令牌不足，推送被拒绝")
                return False

    async def _refill(self, bot_id: int):
        """
        补充令牌

        Args:
            bot_id: 机器人ID
        """
        if bot_id not in self.last_refill:
            return

        now = time.time()
        elapsed = now - self.last_refill[bot_id]

        if elapsed <= 0:
            return

        # 计算应该补充的令牌数
        # 每小时补充 max_tokens，即每秒补充 max_tokens / 3600
        refill_rate = self.max_tokens[bot_id] / 3600.0
        refill_amount = elapsed * refill_rate

        # 补充令牌，但不超过最大值
        self.tokens[bot_id] = min(
            self.tokens[bot_id] + refill_amount,
            float(self.max_tokens[bot_id])
        )

        # 更新最后补充时间
        self.last_refill[bot_id] = now

        logger.debug(f"补充令牌: bot_id={bot_id}, 补充={refill_amount:.2f}, 当前={self.tokens[bot_id]:.1f}")

    async def start_auto_refill(self, interval: int = 60):
        """
        启动自动补充任务（后台任务）

        Args:
            interval: 补充间隔（秒）
        """
        while True:
            try:
                await asyncio.sleep(interval)

                async with self.lock:
                    for bot_id in list(self.max_tokens.keys()):
                        await self._refill(bot_id)

            except Exception as e:
                logger.error(f"自动补充令牌失败: {e}")

    def get_tokens(self, bot_id: int) -> float:
        """
        获取当前令牌数

        Args:
            bot_id: 机器人ID

        Returns:
            当前令牌数
        """
        return self.tokens.get(bot_id, 0.0)

    def reset(self, bot_id: int):
        """
        重置机器人的令牌（填满）

        Args:
            bot_id: 机器人ID
        """
        if bot_id in self.max_tokens:
            self.tokens[bot_id] = float(self.max_tokens[bot_id])
            self.last_refill[bot_id] = time.time()
            logger.debug(f"重置令牌: bot_id={bot_id}")


# 全局频率限制器实例
rate_limiter = RateLimiter()
