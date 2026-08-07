"""
BinanceAdapter — Binance交易所适配器

使用 CcxtBaseAdapter 共享基类，CCXT binance 驱动。

激励特色:
- Alpha 积分体系 → 新币空投（2025年221代币上线，105个毕业）
- VIP 等级降费（VIP1 现货 Maker 返还起步）
- Launchpad 新币认购
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


class BinanceAdapter(CcxtBaseAdapter):
    """Binance 合约 + 现货适配器"""

    _ccxt_id = "binance"
    _exchange_type = ExchangeType.BINANCE
    _supports_spot_flag = True
    _supports_futures_flag = True

    # 费率配置：Binance VIP0
    _fee_tier_config: Dict[str, Any] = {
        "tier_name": "VIP0",
        "maker_rate": 0.0002,   # 0.02%
        "taker_rate": 0.0004,   # 0.04%
        "rebate_rate": 0.0,     # VIP0 无返佣
    }
    _base_rebate_rate: float = 0.0

    # ── 覆盖激励方法（带缓存） ──

    async def get_points_snapshot(self) -> ExchangePointsSnapshot:
        """获取币安 Alpha 积分快照（带缓存）"""
        cache = self._get_cache()
        cache_key = "binance_points"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        if self._exchange is None:
            return ExchangePointsSnapshot(exchange="binance")

        try:
            result = await self._exchange.private_get_bapi_asset_v1_private_alpha_points_wallet_balance()
            data = result if isinstance(result, dict) else {}
            inner = data.get("data", data) if isinstance(data, dict) else {}

            snapshot = ExchangePointsSnapshot(
                exchange="binance",
                points_balance=float(inner.get("totalPointsBalance", 0) or 0),
                points_multiplier=float(inner.get("totalMultiplier", 1.0) or 1.0),
                season=str(inner.get("currentSeason", "")),
                qualifying_days=int(inner.get("totalQualifiedDaysCountInCurrentWeek", 0) or 0),
                required_days=int(inner.get("requiredDaysPerWeek", 2) or 2),
                airdrop_eligible=bool(inner.get("airdropEligible", False)),
                estimated_airdrop_value=float(inner.get("estimatedAirdropValue", 0) or 0),
            )
            config = self._get_config()
            ttl = config.cache_ttls.points_seconds if config else 300
            cache.set(cache_key, snapshot, ttl)
            logger.info("[Binance] Alpha points fetched: %.1f pts", snapshot.points_balance)
            return snapshot
        except Exception as e:
            logger.warning("BinanceAdapter.get_points_snapshot failed: %s", e)
            # Return stale cache if available
            stale = cache.get_or_stale(cache_key)
            return stale if stale else ExchangePointsSnapshot(exchange="binance")

    async def get_fee_tier(self) -> ExchangeFeeTier:
        """获取费率等级 — 优先 fapi/v1/account API，再 CCXT unified，带缓存"""
        cache = self._get_cache()
        cache_key = "binance_fee_tier"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        if self._exchange is None:
            return await super().get_fee_tier()

        try:
            result = await self._exchange.private_get_fapi_v1_account()
            if isinstance(result, dict):
                fee_tier = result.get("feeTier", "VIP0")
                maker_fee = float(result.get("makerCommission", 0.0002) or 0.0002)
                taker_fee = float(result.get("takerCommission", 0.0004) or 0.0004)
                # Binance returns rates in decimals (0.0002) or basis points - normalize
                if maker_fee > 0.01:
                    maker_fee = maker_fee / 10000  # Convert from basis points
                if taker_fee > 0.01:
                    taker_fee = taker_fee / 10000

                tier = ExchangeFeeTier(
                    exchange="binance",
                    tier_name=str(fee_tier),
                    maker_rate=maker_fee,
                    taker_rate=taker_fee,
                    rebate_rate=0.0,
                    volume_30d_usd=float(result.get("totalTradeVolume30d", 0) or 0),
                )
                config = self._get_config()
                ttl = config.cache_ttls.fee_tier_seconds if config else 3600
                cache.set(cache_key, tier, ttl)
                logger.info(
                    "[Binance] Fee tier fetched: %s maker=%.4f%% taker=%.4f%%",
                    tier.tier_name, maker_fee * 100, taker_fee * 100
                )
                return tier
        except Exception as e:
            logger.debug("BinanceAdapter.get_fee_tier private API failed: %s", e)

        # Fallback to CCXT unified method (parent)
        return await super().get_fee_tier()

    async def get_active_campaigns(self) -> List[Dict]:
        """获取币安 Alpha 奖励季等信息（带缓存）"""
        cache = self._get_cache()
        cache_key = "binance_campaigns"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        campaigns = [{
            "campaign_id": "binance_alpha_points",
            "name": "Binance Alpha Points Airdrop",
            "type": "points_airdrop",
            "status": "active",
            "points_required": "alpha_points",
            "total_tokens_launched": 221,
            "total_graduated": 105,
            "graduation_rate": 0.475,
        }]

        if self._exchange is not None:
            try:
                result = await self._exchange.private_get_bapi_asset_v1_private_alpha_points_campaigns()
                if isinstance(result, list):
                    campaigns.extend(result)
                elif isinstance(result, dict):
                    inner = result.get("data", [])
                    if isinstance(inner, list):
                        campaigns.extend(inner)
            except Exception:
                pass

        config = self._get_config()
        ttl = config.cache_ttls.campaigns_seconds if config else 1800
        cache.set(cache_key, campaigns, ttl)
        return campaigns
