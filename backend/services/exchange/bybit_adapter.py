"""
BybitAdapter — Bybit交易所适配器

使用 CcxtBaseAdapter 共享基类，CCXT bybit 驱动。

激励特色:
- Launchpad 积分 + 竞赛奖励
- Market Maker Program: 负费率 (MM1-MM3+)
- RPI Program: Retail Price Improvement 返佣
"""

import logging
from typing import Any, Dict, List

from backend.services.exchange.base_exchange_client import (
    ExchangeFeeTier,
    ExchangePointsSnapshot,
    ExchangeRebateInfo,
    ExchangeType,
)
from backend.services.exchange.ccxt_base_adapter import CcxtBaseAdapter

logger = logging.getLogger(__name__)


class BybitAdapter(CcxtBaseAdapter):
    """Bybit 合约 + 现货适配器"""

    _ccxt_id = "bybit"
    _exchange_type = ExchangeType.BYBIT
    _supports_spot_flag = True
    _supports_futures_flag = True

    # 费率配置：Bybit VIP0
    _fee_tier_config: Dict[str, Any] = {
        "tier_name": "VIP0",
        "maker_rate": 0.0002,   # 0.02%
        "taker_rate": 0.00055,  # 0.055%
        "rebate_rate": 0.0,
    }
    _base_rebate_rate: float = 0.0

    async def get_fee_tier(self) -> ExchangeFeeTier:
        """获取Bybit费率 — 使用 /v5/account/fee-rate"""
        cache = self._get_cache()
        cache_key = "bybit_fee_tier"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        if self._exchange is None:
            return await super().get_fee_tier()

        try:
            # CCXT unified: fetchTradingFee for Bybit V5
            result = await self._exchange.fetch_trading_fee("BTC/USDT:USDT")
            if isinstance(result, dict):
                maker = float(result.get("maker", 0.0002) or 0.0002)
                taker = float(result.get("taker", 0.00055) or 0.00055)

                # Negative maker = MM rebate on Bybit
                rebate_rate = abs(maker) if maker < 0 else 0.0
                effective_maker = maker if maker >= 0 else 0.0

                tier_name = "MM" if maker < 0 else self._detect_vip_tier(maker, taker)

                tier = ExchangeFeeTier(
                    exchange="bybit",
                    tier_name=tier_name,
                    maker_rate=effective_maker,
                    taker_rate=taker,
                    rebate_rate=rebate_rate,
                )
                config = self._get_config()
                ttl = config.cache_ttls.fee_tier_seconds if config else 3600
                cache.set(cache_key, tier, ttl)
                logger.info(
                    "[Bybit] Fee tier fetched: %s maker=%.5f%% taker=%.5f%%",
                    tier_name, effective_maker * 100, taker * 100
                )
                return tier
        except Exception as e:
            logger.warning("BybitAdapter.get_fee_tier failed: %s", e)

        return await super().get_fee_tier()

    async def get_points_snapshot(self) -> ExchangePointsSnapshot:
        """Bybit Launchpad积分"""
        cache = self._get_cache()
        cache_key = "bybit_points"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        snapshot = ExchangePointsSnapshot(exchange="bybit")

        if self._exchange is not None:
            try:
                # Bybit doesn't have a direct points API via CCXT
                # We track volume as a proxy for rewards eligibility
                bal = await self._exchange.fetch_balance()
                total_usdt = float(bal.get("total", {}).get("USDT", 0) or 0)
                snapshot = ExchangePointsSnapshot(
                    exchange="bybit",
                    points_balance=0,
                    daily_points_rate=0,
                )
            except Exception as e:
                logger.debug("BybitAdapter.get_points_snapshot: %s", e)

        config = self._get_config()
        ttl = config.cache_ttls.points_seconds if config else 300
        cache.set(cache_key, snapshot, ttl)
        return snapshot

    async def get_rebate_info(self) -> ExchangeRebateInfo:
        """Bybit返利 — MM Program有负费率"""
        cache = self._get_cache()
        cache_key = "bybit_rebate_info"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        fee_tier = await self.get_fee_tier()
        info = ExchangeRebateInfo(
            exchange="bybit",
            base_rebate_rate=0.0,
            current_rebate_rate=fee_tier.rebate_rate,
            stacked_multiplier=1.0,
        )

        config = self._get_config()
        ttl = config.cache_ttls.rebate_info_seconds if config else 600
        cache.set(cache_key, info, ttl)
        return info

    async def get_active_campaigns(self) -> List[Dict]:
        """Bybit活动列表"""
        cache = self._get_cache()
        cache_key = "bybit_campaigns"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        campaigns = [{
            "campaign_id": "bybit_launchpad",
            "name": "Bybit Launchpad",
            "type": "token_subscription",
            "status": "active",
        }]

        config = self._get_config()
        ttl = config.cache_ttls.campaigns_seconds if config else 1800
        cache.set(cache_key, campaigns, ttl)
        return campaigns
