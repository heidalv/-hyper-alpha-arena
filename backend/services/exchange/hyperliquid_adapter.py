"""
HyperliquidAdapter — Hyperliquid交易所适配器

包装现有 HyperliquidTradingClient (需 db + 私钥的交易方法)
和 ccxt.hyperliquid 无密钥行情 API，实现 BaseExchangeClient 接口。
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from backend.services.exchange.base_exchange_client import (
    BaseExchangeClient,
    ExchangeBalance,
    ExchangeFeeTier,
    ExchangeIncentiveSummary,
    ExchangeOrder,
    ExchangePointsSnapshot,
    ExchangePosition,
    ExchangeRebateInfo,
    ExchangeType,
    OrderSide,
)

logger = logging.getLogger(__name__)


class HyperliquidAdapter(BaseExchangeClient):
    """
    Hyperliquid适配器

    trading_client: 已初始化的 HyperliquidTradingClient (同步, 需 db)
    db_factory:     返回 DB Session 的 callable (用于 trading_client 方法)
    """

    def __init__(
        self,
        existing_client=None,
        db_factory=None,
    ):
        self._client = existing_client
        self._db_factory = db_factory
        self._market_exchange = None

        try:
            import ccxt.async_support as ccxt
            self._market_exchange = ccxt.hyperliquid({
                "enableRateLimit": True,
            })
        except ImportError:
            logger.warning("ccxt not installed, HL market data unavailable")

    def _get_db(self):
        if self._db_factory:
            return self._db_factory()
        try:
            from backend.database.connection import SessionLocal
            return SessionLocal()
        except Exception:
            return None

    # ── Properties ────────────────────────────────

    @property
    def exchange_type(self) -> ExchangeType:
        return ExchangeType.HYPERLIQUID

    @property
    def supports_spot(self) -> bool:
        return False

    @property
    def supports_futures(self) -> bool:
        return True

    # ── Balance ───────────────────────────────────

    async def get_balance(self) -> ExchangeBalance:
        if self._client is None:
            return ExchangeBalance(0, 0, 0, 0)
        db = self._get_db()
        if db is None:
            return ExchangeBalance(0, 0, 0, 0)
        try:
            raw = await asyncio.to_thread(self._client.get_account_state, db)
            return ExchangeBalance(
                total_equity=float(raw.get("total_equity", 0)),
                available_balance=float(raw.get("available_balance", 0)),
                frozen_margin=float(raw.get("used_margin", 0)),
                unrealized_pnl=0,
            )
        except Exception as e:
            logger.warning("HyperliquidAdapter.get_balance failed: %s", e)
            return ExchangeBalance(0, 0, 0, 0)
        finally:
            try:
                db.close()
            except Exception:
                pass

    # ── Positions ─────────────────────────────────

    async def get_positions(self) -> List[ExchangePosition]:
        if self._client is None:
            return []
        db = self._get_db()
        if db is None:
            return []
        try:
            raw_list = await asyncio.to_thread(self._client.get_positions, db)
            positions = []
            if isinstance(raw_list, list):
                for p in raw_list:
                    if not isinstance(p, dict):
                        continue
                    szi = float(p.get("szi", 0) or p.get("size", 0) or 0)
                    if szi == 0:
                        continue
                    positions.append(ExchangePosition(
                        symbol=p.get("coin", p.get("symbol", "")),
                        side="long" if szi > 0 else "short",
                        size=abs(szi),
                        entry_price=float(p.get("entryPx", 0) or p.get("entry_price", 0) or 0),
                        mark_price=float(p.get("markPx", 0) or p.get("mark_price", 0) or 0),
                        unrealized_pnl=float(p.get("unrealizedPnl", 0) or 0),
                        margin=float(p.get("marginUsed", 0) or p.get("margin", 0) or 0),
                        leverage=float(p.get("leverage", {}).get("value", 1) if isinstance(p.get("leverage"), dict) else p.get("leverage", 1)),
                        liquidation_price=_safe_float(p.get("liquidationPx")),
                    ))
            return positions
        except Exception as e:
            logger.warning("HyperliquidAdapter.get_positions failed: %s", e)
            return []
        finally:
            try:
                db.close()
            except Exception:
                pass

    # ── Orders ────────────────────────────────────

    async def place_order(self, order: ExchangeOrder) -> Dict:
        if self._client is None:
            return {"status": "error", "message": "no HL client"}
        db = self._get_db()
        if db is None:
            return {"status": "error", "message": "no db session"}
        try:
            is_buy = order.side == OrderSide.BUY
            result = await asyncio.to_thread(
                self._client.place_order,
                db=db,
                symbol=order.symbol,
                is_buy=is_buy,
                size=order.size,
                order_type=order.order_type.value,
                price=order.price,
                reduce_only=order.reduce_only,
                leverage=order.leverage,
            )
            return result if isinstance(result, dict) else {"status": "ok", "result": str(result)}
        except Exception as e:
            logger.warning("HyperliquidAdapter.place_order failed: %s", e)
            return {"status": "error", "message": str(e)}
        finally:
            try:
                db.close()
            except Exception:
                pass

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        if self._client is None:
            return False
        db = self._get_db()
        if db is None:
            return False
        try:
            result = await asyncio.to_thread(
                self._client.cancel_order, db, order_id, symbol
            )
            return bool(result)
        except Exception as e:
            logger.warning("HyperliquidAdapter.cancel_order failed: %s", e)
            return False
        finally:
            try:
                db.close()
            except Exception:
                pass

    # ── Market Data (公开 API, 无需私钥) ──────────

    async def get_funding_rate(self, symbol: str) -> float:
        if self._market_exchange is None:
            return 0.0
        try:
            result = await self._market_exchange.fetch_funding_rate(symbol)
            return float(result.get("fundingRate", 0) or 0)
        except Exception as e:
            logger.warning("HyperliquidAdapter.get_funding_rate failed: %s", e)
            return 0.0

    async def get_all_funding_rates(self) -> Dict[str, float]:
        if self._market_exchange is None:
            return {}
        try:
            results = await self._market_exchange.fetch_funding_rates()
            rates: Dict[str, float] = {}
            if isinstance(results, dict):
                for sym, item in results.items():
                    if isinstance(item, dict):
                        rates[sym] = float(item.get("fundingRate", 0) or 0)
                    else:
                        rates[sym] = float(item or 0)
            return rates
        except Exception as e:
            logger.warning("HyperliquidAdapter.get_all_funding_rates failed: %s", e)
            return {}

    async def get_orderbook(self, symbol: str, depth: int = 20) -> Dict:
        if self._market_exchange is None:
            return {"bids": [], "asks": []}
        try:
            book = await self._market_exchange.fetch_order_book(symbol, limit=depth)
            return book if isinstance(book, dict) else {"bids": [], "asks": []}
        except Exception as e:
            logger.warning("HyperliquidAdapter.get_orderbook failed: %s", e)
            return {"bids": [], "asks": []}

    async def get_klines(
        self, symbol: str, interval: str, limit: int = 100
    ) -> List[Dict]:
        if self._market_exchange is None:
            return []
        try:
            ohlcv = await self._market_exchange.fetch_ohlcv(
                symbol, interval, limit=limit
            )
            return [
                {
                    "timestamp": c[0],
                    "open": c[1],
                    "high": c[2],
                    "low": c[3],
                    "close": c[4],
                    "volume": c[5],
                }
                for c in ohlcv
            ]
        except Exception as e:
            logger.warning("HyperliquidAdapter.get_klines failed: %s", e)
            return []

    # ── 积分/返利套利扩展方法（带缓存） ──

    # Hyperliquid 固定费率，无 VIP 等级
    _hl_fee_config = {
        "tier_name": "standard",
        "maker_rate": 0.0002,   # 0.02%
        "taker_rate": 0.00035,  # 0.035%
        "rebate_rate": 0.0,
    }

    def _get_cache(self):
        from backend.services.rebate_arb.incentive_cache import incentive_cache
        return incentive_cache

    def _get_config(self):
        try:
            from backend.config.rebate_config_loader import rebate_config
            return rebate_config
        except Exception:
            return None

    async def get_fee_tier(self) -> ExchangeFeeTier:
        """Hyperliquid 固定费率（带缓存）"""
        cache = self._get_cache()
        cache_key = "hyperliquid_fee_tier"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        tier = ExchangeFeeTier(
            exchange="hyperliquid",
            tier_name=self._hl_fee_config["tier_name"],
            maker_rate=self._hl_fee_config["maker_rate"],
            taker_rate=self._hl_fee_config["taker_rate"],
            rebate_rate=self._hl_fee_config["rebate_rate"],
        )
        config = self._get_config()
        ttl = config.cache_ttls.fee_tier_seconds if config else 3600
        cache.set(cache_key, tier, ttl)
        return tier

    async def get_points_snapshot(self) -> ExchangePointsSnapshot:
        """获取 Hyperliquid Points 积分快照（带缓存）"""
        cache = self._get_cache()
        cache_key = "hyperliquid_points"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        snapshot = ExchangePointsSnapshot(exchange="hyperliquid")

        if self._client is not None:
            db = self._get_db()
            if db is not None:
                try:
                    raw = await asyncio.to_thread(self._client.get_account_state, db)
                    if isinstance(raw, dict):
                        points_data = raw.get("points", {})
                        snapshot = ExchangePointsSnapshot(
                            exchange="hyperliquid",
                            points_balance=float(points_data.get("totalPoints", 0) or 0),
                            points_multiplier=float(points_data.get("multiplier", 1.0) or 1.0),
                            season=str(points_data.get("currentSeason", "")),
                            daily_points_rate=float(points_data.get("dailyRate", 0) or 0),
                            airdrop_eligible=bool(points_data.get("airdropEligible", False)),
                            estimated_airdrop_value=float(points_data.get("estimatedValue", 0) or 0),
                        )
                        logger.info("[Hyperliquid] Points: %.1f (x%.1f)", snapshot.points_balance, snapshot.points_multiplier)
                except Exception as e:
                    logger.debug("HyperliquidAdapter.get_points_snapshot: %s", e)
                finally:
                    try:
                        db.close()
                    except Exception:
                        pass

        config = self._get_config()
        ttl = config.cache_ttls.points_seconds if config else 300
        cache.set(cache_key, snapshot, ttl)
        return snapshot

    async def get_rebate_info(self) -> ExchangeRebateInfo:
        """Hyperliquid 无返利（带缓存）"""
        cache = self._get_cache()
        cache_key = "hyperliquid_rebate_info"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        info = ExchangeRebateInfo(
            exchange="hyperliquid",
            base_rebate_rate=0.0,
            current_rebate_rate=0.0,
        )
        config = self._get_config()
        ttl = config.cache_ttls.rebate_info_seconds if config else 600
        cache.set(cache_key, info, ttl)
        return info

    async def get_incentive_summary(self) -> ExchangeIncentiveSummary:
        """获取 Hyperliquid 激励政策汇总"""
        import time
        fee_tier = await self.get_fee_tier()
        points = await self.get_points_snapshot()
        rebate = await self.get_rebate_info()
        return ExchangeIncentiveSummary(
            exchange="hyperliquid",
            exchange_type=ExchangeType.HYPERLIQUID,
            fee_tier=fee_tier,
            points=points,
            rebate=rebate,
            is_connected=self._client is not None or self._market_exchange is not None,
            last_update=time.time(),
        )

    async def get_active_campaigns(self) -> List[Dict]:
        """Hyperliquid Points Season 空投（带缓存）"""
        cache = self._get_cache()
        cache_key = "hyperliquid_campaigns"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        campaigns = [{
            "campaign_id": "hl_points_season",
            "name": "Hyperliquid Points Season",
            "type": "points_airdrop",
            "status": "active",
            "points_required": "hl_points",
        }]
        config = self._get_config()
        ttl = config.cache_ttls.campaigns_seconds if config else 1800
        cache.set(cache_key, campaigns, ttl)
        return campaigns

    async def close(self):
        if self._market_exchange:
            try:
                await self._market_exchange.close()
            except Exception:
                pass


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
