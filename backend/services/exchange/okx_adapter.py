"""
OKXAdapter — OKX交易所适配器

使用 CcxtBaseAdapter 共享基类，CCXT okx 驱动。
OKX 需要额外的 passphrase 参数（通过 password 传入）。

激励特色:
- VIP4+ Maker 降至 0%
- VIP7-9 负费率 (Maker 返佣)
- Jumpstart 新币认购 + 交易积分
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


class OKXAdapter(CcxtBaseAdapter):
    """OKX 合约 + 现货适配器"""

    _ccxt_id = "okx"
    _exchange_type = ExchangeType.OKX
    _supports_spot_flag = True
    _supports_futures_flag = True
    _extra_ccxt_config = {
        "options": {
            "defaultType": "swap",
        },
    }

    # 费率配置：OKX VIP0 defaults
    _fee_tier_config: Dict[str, Any] = {
        "tier_name": "VIP0",
        "maker_rate": 0.0002,   # 0.02%
        "taker_rate": 0.0005,   # 0.05%
        "rebate_rate": 0.0,
    }
    _base_rebate_rate: float = 0.0

    # OKX VIP tier thresholds (30d volume in USD)
    _VIP_TIERS = {
        "VIP0": (0, 0.0002, 0.0005),
        "VIP1": (5_000_000, 0.00015, 0.00045),
        "VIP2": (10_000_000, 0.0001, 0.0004),
        "VIP3": (20_000_000, 0.00008, 0.00035),
        "VIP4": (50_000_000, 0.0, 0.0003),      # 0% maker!
        "VIP5": (100_000_000, -0.00005, 0.00025),  # Negative = rebate
        "VIP6": (200_000_000, -0.0001, 0.0002),
        "VIP7": (500_000_000, -0.00015, 0.00015),
    }

    async def get_fee_tier(self) -> ExchangeFeeTier:
        """获取OKX费率 — 使用 /api/v5/account/trade-fee 端点"""
        cache = self._get_cache()
        cache_key = "okx_fee_tier"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        if self._exchange is None:
            return await super().get_fee_tier()

        try:
            # OKX V5 API: GET /api/v5/account/trade-fee
            # CCXT maps this internally
            result = await self._exchange.fetch_trading_fee("BTC/USDT:USDT")
            if isinstance(result, dict):
                maker = float(result.get("maker", 0.0002) or 0.0002)
                taker = float(result.get("taker", 0.0005) or 0.0005)

                # Negative maker means rebate on OKX
                rebate_rate = abs(maker) if maker < 0 else 0.0
                effective_maker = maker if maker >= 0 else 0.0

                # Detect tier from rates
                tier_name = "VIP0"
                for name, (_, m, t) in sorted(self._VIP_TIERS.items(), key=lambda x: x[1][0], reverse=True):
                    if abs(maker - m) < 0.00001:
                        tier_name = name
                        break

                tier = ExchangeFeeTier(
                    exchange="okx",
                    tier_name=tier_name,
                    maker_rate=effective_maker,
                    taker_rate=taker,
                    rebate_rate=rebate_rate,
                )
                config = self._get_config()
                ttl = config.cache_ttls.fee_tier_seconds if config else 3600
                cache.set(cache_key, tier, ttl)
                logger.info(
                    "[OKX] Fee tier fetched: %s maker=%.5f%% taker=%.5f%% rebate=%.5f%%",
                    tier_name, effective_maker * 100, taker * 100, rebate_rate * 100
                )
                return tier
        except Exception as e:
            logger.warning("OKXAdapter.get_fee_tier failed: %s", e)

        return await super().get_fee_tier()

    async def get_points_snapshot(self) -> ExchangePointsSnapshot:
        """OKX Jumpstart/积分快照 — 尝试获取交易积分"""
        cache = self._get_cache()
        cache_key = "okx_points"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        snapshot = ExchangePointsSnapshot(exchange="okx")

        if self._exchange is not None:
            try:
                # Try to get account trading volume (proxy for points eligibility)
                result = await self._exchange.private_get_api_v5_account_balance()
                if isinstance(result, dict):
                    data = result.get("data", [{}])
                    if data and isinstance(data, list):
                        details = data[0].get("details", [])
                        # Extract total equity as indicator
                        total_eq = float(data[0].get("totalEq", 0) or 0)
                        snapshot = ExchangePointsSnapshot(
                            exchange="okx",
                            points_balance=0,  # OKX doesn't expose points directly via API
                            daily_points_rate=0,
                        )
            except Exception as e:
                logger.debug("OKXAdapter.get_points_snapshot: %s", e)

        config = self._get_config()
        ttl = config.cache_ttls.points_seconds if config else 300
        cache.set(cache_key, snapshot, ttl)
        return snapshot

    async def get_rebate_info(self) -> ExchangeRebateInfo:
        """OKX返利信息 — VIP4+有负费率返利"""
        cache = self._get_cache()
        cache_key = "okx_rebate_info"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        fee_tier = await self.get_fee_tier()
        rebate_rate = fee_tier.rebate_rate

        info = ExchangeRebateInfo(
            exchange="okx",
            base_rebate_rate=0.0,
            current_rebate_rate=rebate_rate,
            stacked_multiplier=1.0,
        )

        config = self._get_config()
        ttl = config.cache_ttls.rebate_info_seconds if config else 600
        cache.set(cache_key, info, ttl)
        return info

    async def get_active_campaigns(self) -> List[Dict]:
        """OKX活动列表"""
        cache = self._get_cache()
        cache_key = "okx_campaigns"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        campaigns = [{
            "campaign_id": "okx_jumpstart",
            "name": "OKX Jumpstart Mining",
            "type": "new_token_mining",
            "status": "active",
        }]

        config = self._get_config()
        ttl = config.cache_ttls.campaigns_seconds if config else 1800
        cache.set(cache_key, campaigns, ttl)
        return campaigns
