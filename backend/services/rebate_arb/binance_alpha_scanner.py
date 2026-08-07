"""
Binance Alpha积分扫描器 (S7专用)

深度扫描 Binance Alpha 积分系统，获取：
- Alpha积分余额
- 积分获取速率
- 当前/历史空投项目
- Alpha积分→代币兑换比例
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class BinanceAlphaScanner:
    """Binance Alpha 积分专用扫描器"""

    # Alpha积分参数
    TOKENS_PER_POINT = 22
    GRADUATION_RATE = 0.475
    FIRST_DAY_PREMIUM = 3.0

    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._alpha_history: Dict[str, Dict] = {}  # token -> {points, tokens, value}

    async def scan(self, exchange_client: Optional[Any] = None) -> Dict[str, Any]:
        """
        扫描 Binance Alpha 积分数据

        Args:
            exchange_client: BinanceAdapter 实例

        Returns:
            Binance Alpha 积分汇总数据
        """
        result = {
            "alpha_points_balance": 0.0,
            "alpha_daily_rate": 10.0,
            "fee_tier": {},
            "points_snapshot": {},
            "campaigns": [],
            "estimated_monthly_value": 0.0,
        }

        if exchange_client is None:
            logger.debug("[BinanceAlphaScanner] 无交易所客户端，返回默认数据")
            return result

        try:
            fee_tier = await exchange_client.get_fee_tier()
            result["fee_tier"] = {
                "tier_name": fee_tier.tier_name,
                "maker_rate": fee_tier.maker_rate,
                "taker_rate": fee_tier.taker_rate,
            }

            points = await exchange_client.get_points_snapshot()
            result["points_snapshot"] = {
                "balance": points.points_balance,
                "multiplier": points.points_multiplier,
                "daily_rate": points.daily_points_rate,
            }
            result["alpha_points_balance"] = points.points_balance
            result["alpha_daily_rate"] = points.daily_points_rate

            campaigns = await exchange_client.get_active_campaigns()
            result["campaigns"] = campaigns

            # 估算月价值
            monthly_points = points.daily_points_rate * 30
            estimated_tokens = monthly_points * self.TOKENS_PER_POINT
            estimated_value = (
                estimated_tokens
                * 0.50  # 平均代币价值
                * self.GRADUATION_RATE
                * self.FIRST_DAY_PREMIUM
            )
            result["estimated_monthly_value"] = estimated_value

        except Exception as e:
            logger.warning(f"[BinanceAlphaScanner] 扫描异常: {e}")

        return result

    def estimate_airdrop_value(self, points: float, avg_token_value: float = 0.50) -> float:
        """估算空投价值"""
        tokens = points * self.TOKENS_PER_POINT
        return tokens * avg_token_value * self.GRADUATION_RATE * self.FIRST_DAY_PREMIUM

    def get_cached(self) -> Dict[str, Any]:
        """获取缓存数据"""
        return self._cache.copy()


# 模块级单例
binance_alpha_scanner = BinanceAlphaScanner()
