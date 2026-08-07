"""
AsterdexAdapter — Asterdex 交易所适配器

Asterdex 的 API 与 Binance 完全兼容（同样的签名方式、端点结构），
使用 CCXT 的 binance 驱动并覆盖 base URL 为 Asterdex 服务器。

激励特色:
- Maker/Taker 仅 0.005%，叠加 10% 返佣后净费率 -0.045%
- Rh 积分体系 + ASTER 空投（Stage 6: 64M 代币）
"""

import logging
import time
from typing import Any, Dict, List

from backend.services.exchange.base_exchange_client import (
    ExchangeFeeTier,
    ExchangeIncentiveSummary,
    ExchangePointsSnapshot,
    ExchangeRebateInfo,
    ExchangeType,
)
from backend.services.exchange.ccxt_base_adapter import CcxtBaseAdapter

logger = logging.getLogger(__name__)

ASTERDEX_FUTURES_URL = "https://fapi.asterdex.com"


class AsterdexAdapter(CcxtBaseAdapter):
    """
    Asterdex 合约适配器

    基于 Binance-兼容 API，覆盖 CCXT binance 的 URL 配置。
    特色：0.005% Maker/Taker + 10% 返佣 → 净费率 -0.045%
    """

    _ccxt_id = "binance"
    _exchange_type = ExchangeType.ASTERDEX
    _supports_spot_flag = False
    _supports_futures_flag = True

    # 费率配置：Asterdex 极致低费率
    _fee_tier_config: Dict[str, Any] = {
        "tier_name": "pro",
        "maker_rate": 0.00005,   # 0.005%
        "taker_rate": 0.00005,   # 0.005%
        "rebate_rate": 0.10,     # 10% 返佣
    }
    _base_rebate_rate: float = 0.10

    # Rh 积分乘数阶梯（基于交易量）
    _rh_multiplier_tiers = [
        (100_000, 1.1),
        (500_000, 1.3),
        (1_000_000, 1.5),
        (5_000_000, 1.8),
        (10_000_000, 2.0),
    ]

    def __init__(
        self,
        api_key: str = "",
        secret: str = "",
        password: str = "",
        testnet: bool = False,
    ):
        super().__init__(
            api_key=api_key,
            secret=secret,
            password=password,
            testnet=testnet,
        )
        if self._exchange is not None:
            self._exchange.urls["api"] = {
                "fapiPublic": ASTERDEX_FUTURES_URL + "/fapi/v1",
                "fapiPrivate": ASTERDEX_FUTURES_URL + "/fapi/v1",
                "fapiPublicV2": ASTERDEX_FUTURES_URL + "/fapi/v2",
                "fapiPrivateV2": ASTERDEX_FUTURES_URL + "/fapi/v2",
                "public": ASTERDEX_FUTURES_URL + "/api/v3",
                "private": ASTERDEX_FUTURES_URL + "/api/v3",
            }
            self._exchange.options["defaultType"] = "future"
            logger.info("AsterdexAdapter initialized → %s", ASTERDEX_FUTURES_URL)

    # ── 覆盖激励方法（带缓存） ──

    async def get_points_snapshot(self) -> ExchangePointsSnapshot:
        """获取 Rh 积分快照（带缓存）"""
        cache = self._get_cache()
        cache_key = "asterdex_points"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        if self._exchange is None:
            return ExchangePointsSnapshot(exchange="asterdex")

        try:
            result = await self._exchange.private_get_fapi_v1_rh_points()
            data = result if isinstance(result, dict) else {}
            snapshot = ExchangePointsSnapshot(
                exchange="asterdex",
                points_balance=float(data.get("totalRhPoints", 0) or 0),
                points_multiplier=float(data.get("multiplier", 1.0) or 1.0),
                season=str(data.get("currentStage", "")),
                qualifying_days=int(data.get("activeDays", 0) or 0),
                required_days=int(data.get("requiredDays", 2) or 2),
                airdrop_eligible=bool(data.get("airdropEligible", False)),
                estimated_airdrop_value=float(data.get("estimatedAirdropValue", 0) or 0),
            )
            config = self._get_config()
            ttl = config.cache_ttls.points_seconds if config else 300
            cache.set(cache_key, snapshot, ttl)
            logger.info("[Asterdex] Rh points: %.1f (x%.1f)", snapshot.points_balance, snapshot.points_multiplier)
            return snapshot
        except Exception as e:
            logger.warning("AsterdexAdapter.get_points_snapshot failed: %s", e)
            stale = cache.get_or_stale(cache_key)
            return stale if stale else ExchangePointsSnapshot(exchange="asterdex")

    async def get_fee_tier(self) -> ExchangeFeeTier:
        """Asterdex 费率 — 固定 0.005% + 10% rebate, 带缓存"""
        cache = self._get_cache()
        cache_key = "asterdex_fee_tier"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        # Asterdex has fixed fees, but check from API if available
        tier = ExchangeFeeTier(
            exchange="asterdex",
            tier_name="pro",
            maker_rate=self._fee_tier_config["maker_rate"],
            taker_rate=self._fee_tier_config["taker_rate"],
            rebate_rate=self._fee_tier_config["rebate_rate"],
        )

        if self._exchange is not None:
            try:
                result = await self._exchange.private_get_fapi_v1_account()
                if isinstance(result, dict):
                    maker = float(result.get("makerCommission", 5) or 5) / 100000  # Convert from internal format
                    taker = float(result.get("takerCommission", 5) or 5) / 100000
                    if maker > 0:
                        tier = ExchangeFeeTier(
                            exchange="asterdex",
                            tier_name="pro",
                            maker_rate=maker if maker < 0.01 else maker / 10000,
                            taker_rate=taker if taker < 0.01 else taker / 10000,
                            rebate_rate=self._fee_tier_config["rebate_rate"],
                            volume_30d_usd=float(result.get("totalTradeVolume30d", 0) or 0),
                        )
            except Exception as e:
                logger.debug("AsterdexAdapter.get_fee_tier API: %s", e)

        config = self._get_config()
        ttl = config.cache_ttls.fee_tier_seconds if config else 3600
        cache.set(cache_key, tier, ttl)
        return tier

    async def get_rebate_info(self) -> ExchangeRebateInfo:
        """获取返利配置 — 10% 基础返佣 + Rh 积分乘数叠加（带缓存）"""
        cache = self._get_cache()
        cache_key = "asterdex_rebate_info"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        points = await self.get_points_snapshot()
        stacked_multiplier = points.points_multiplier
        current_rate = self._base_rebate_rate * stacked_multiplier

        volume_7d = 0.0
        if self._exchange is not None:
            try:
                vol_data = await self._exchange.private_get_fapi_v1_account()
                if isinstance(vol_data, dict):
                    volume_7d = float(vol_data.get("totalTradeVolume7d", 0) or 0)
            except Exception:
                pass

        projected_weekly = volume_7d * current_rate * 0.143

        info = ExchangeRebateInfo(
            exchange="asterdex",
            base_rebate_rate=self._base_rebate_rate,
            current_rebate_rate=current_rate,
            stacked_multiplier=stacked_multiplier,
            trading_volume_7d=volume_7d,
            projected_weekly_rebate=projected_weekly,
        )
        config = self._get_config()
        ttl = config.cache_ttls.rebate_info_seconds if config else 600
        cache.set(cache_key, info, ttl)
        return info

    # ── USDF 铸造与保证金管理 ──

    async def mint_usdf(self, amount_usd: float, skip_if_sufficient: bool = False) -> Dict[str, Any]:
        """
        USDT → USDF 铸造 (1:1)

        USDF 是 Asterdex 的 delta-neutral 稳定币:
        - 7-10% APY 被动收益
        - 作为保证金时触发 20x Au积分乘数

        Args:
            amount_usd: 铸造金额 (USDT)
            skip_if_sufficient: 若现有USDF余额>=amount则跳过

        Returns:
            {"success": True/False, "minted": float, ...}
        """
        if self._exchange is None:
            return {"success": False, "error": "exchange_not_initialized", "fallback": "usdt"}

        try:
            # 检查现有余额
            if skip_if_sufficient:
                current_balance = await self.get_usdf_balance()
                if current_balance >= amount_usd:
                    logger.info("[Asterdex] USDF balance %.2f >= %.2f, skip mint", current_balance, amount_usd)
                    return {"success": True, "minted": 0, "balance": current_balance, "skipped": True}

            # 调用 USDF 铸造端点
            result = await self._exchange.private_post_fapi_v1_usdf_mint({
                "amount": str(amount_usd),
                "fromAsset": "USDT",
            })
            data = result if isinstance(result, dict) else {}

            minted = float(data.get("mintedAmount", amount_usd) or amount_usd)
            logger.info("[Asterdex] USDF minted: %.2f (txId: %s)", minted, data.get("txId", "N/A"))

            return {
                "success": True,
                "minted": minted,
                "tx_id": data.get("txId"),
                "balance_after": float(data.get("balanceAfter", 0) or 0),
            }
        except Exception as e:
            logger.warning("[Asterdex] USDF mint failed (will fallback to USDT): %s", e)
            return {"success": False, "error": str(e), "fallback": "usdt"}

    async def get_usdf_balance(self) -> float:
        """获取当前 USDF 余额"""
        if self._exchange is None:
            return 0.0

        cache = self._get_cache()
        cache_key = "asterdex_usdf_balance"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            result = await self._exchange.private_get_fapi_v1_balance()
            balances = result if isinstance(result, list) else []
            for item in balances:
                if isinstance(item, dict) and item.get("asset") == "USDF":
                    balance = float(item.get("availableBalance", 0) or 0)
                    cache.set(cache_key, balance, 60)  # 60s缓存
                    return balance
            return 0.0
        except Exception as e:
            logger.debug("[Asterdex] get_usdf_balance failed: %s", e)
            return 0.0

    async def set_collateral_type(self, symbol: str, collateral: str = "USDF") -> bool:
        """
        设置交易对的保证金类型

        Args:
            symbol: 交易对 (e.g. "ETHUSDT")
            collateral: "USDF" 或 "USDT"

        Returns:
            True on success
        """
        if self._exchange is None:
            return False

        if collateral not in ("USDT", "USDF"):
            logger.warning("[Asterdex] Invalid collateral type: %s", collateral)
            return False

        try:
            await self._exchange.private_post_fapi_v1_margin_type({
                "symbol": symbol.replace("/", ""),
                "marginType": "CROSSED",
                "collateralAsset": collateral,
            })
            logger.info("[Asterdex] Collateral set to %s for %s", collateral, symbol)
            return True
        except Exception as e:
            logger.warning("[Asterdex] set_collateral_type failed: %s", e)
            return False

    # ── 活动信息 ──

    async def get_active_campaigns(self) -> List[Dict]:
        """获取 Asterdex 活动信息（带缓存）"""
        cache = self._get_cache()
        cache_key = "asterdex_campaigns"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        campaigns = [{
            "campaign_id": "aster_stage6_airdrop",
            "name": "ASTER Stage 6 Airdrop",
            "type": "airdrop",
            "total_allocation": 64_000_000,
            "token": "ASTER",
            "status": "active",
            "points_required": "rh_points",
            "end_time": None,
        }]

        if self._exchange is not None:
            try:
                result = await self._exchange.private_get_fapi_v1_campaigns()
                if isinstance(result, list):
                    campaigns.extend(result)
            except Exception:
                pass

        config = self._get_config()
        ttl = config.cache_ttls.campaigns_seconds if config else 1800
        cache.set(cache_key, campaigns, ttl)
        return campaigns
