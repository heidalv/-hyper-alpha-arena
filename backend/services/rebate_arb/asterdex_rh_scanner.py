"""
Asterdex Rh积分扫描器 (S8专用)

深度扫描 Asterdex Rh积分系统，获取：
- Rh积分余额和乘数
- ASTER空投进度
- 费率和返佣配置
- 活跃活动信息
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class AsterdexRhScanner:
    """Asterdex Rh积分专用扫描器"""

    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._last_update: float = 0.0

    async def scan(self, exchange_client: Optional[Any] = None) -> Dict[str, Any]:
        """
        扫描 Asterdex Rh积分数据

        Args:
            exchange_client: AsterdexAdapter 实例

        Returns:
            Asterdex Rh 积分汇总数据
        """
        result = {
            "rh_points": 0.0,
            "rh_multiplier": 1.0,
            "aster_price": 0.01,
            "stage_progress": 0.0,
            "maker_rate": 0.00005,
            "taker_rate": 0.00005,
            "rebate_rate": 0.10,
            "fee_tier": {},
            "points_snapshot": {},
            "rebate_info": {},
            "campaigns": [],
        }

        if exchange_client is None:
            logger.debug("[AsterdexRhScanner] 无交易所客户端，返回默认数据")
            return result

        try:
            fee_tier = await exchange_client.get_fee_tier()
            result["fee_tier"] = {
                "tier_name": fee_tier.tier_name,
                "maker_rate": fee_tier.maker_rate,
                "taker_rate": fee_tier.taker_rate,
                "rebate_rate": fee_tier.rebate_rate,
            }
            result["maker_rate"] = fee_tier.maker_rate
            result["taker_rate"] = fee_tier.taker_rate
            result["rebate_rate"] = fee_tier.rebate_rate

            points = await exchange_client.get_points_snapshot()
            result["points_snapshot"] = {
                "balance": points.points_balance,
                "multiplier": points.points_multiplier,
                "season": points.season,
                "daily_rate": points.daily_points_rate,
            }
            result["rh_points"] = points.points_balance
            result["rh_multiplier"] = points.points_multiplier

            rebate = await exchange_client.get_rebate_info()
            result["rebate_info"] = {
                "base_rate": rebate.base_rebate_rate,
                "current_rate": rebate.current_rebate_rate,
                "stacked_multiplier": rebate.stacked_multiplier,
            }

            campaigns = await exchange_client.get_active_campaigns()
            result["campaigns"] = campaigns

        except Exception as e:
            logger.warning(f"[AsterdexRhScanner] 扫描异常: {e}")

        return result

    def get_cached(self) -> Dict[str, Any]:
        """获取缓存数据"""
        return self._cache.copy()


# 模块级单例
asterdex_rh_scanner = AsterdexRhScanner()
