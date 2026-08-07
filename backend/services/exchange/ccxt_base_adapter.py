"""
CcxtBaseAdapter — CCXT 交易所共享基类

所有基于 CCXT 的交易所适配器（Binance/Bybit/OKX/Gate.io/Asterdex）
继承此类即可获得完整的 BaseExchangeClient 实现。
子类只需指定 _ccxt_class / _exchange_type / _supports_spot 等属性。
"""

import logging
import os
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
)

logger = logging.getLogger(__name__)


class CcxtBaseAdapter(BaseExchangeClient):
    """
    CCXT 通用适配器基类。

    子类只需覆盖以下类变量:
        _ccxt_id:        str   — ccxt exchange id (e.g. "bybit")
        _exchange_type:  ExchangeType
        _supports_spot:  bool
        _supports_futures: bool
    以及可选的 _extra_ccxt_config: dict 用于覆盖 ccxt 初始化参数。
    """

    _ccxt_id: str = ""
    _exchange_type: ExchangeType = ExchangeType.BINANCE
    _supports_spot_flag: bool = True
    _supports_futures_flag: bool = True
    _extra_ccxt_config: Dict[str, Any] = {}

    def __init__(
        self,
        api_key: str = "",
        secret: str = "",
        password: str = "",
        testnet: bool = False,
    ):
        self._exchange = None
        try:
            import ccxt.async_support as ccxt

            cls = getattr(ccxt, self._ccxt_id, None)
            if cls is None:
                logger.warning(
                    "ccxt has no exchange '%s', adapter in stub mode", self._ccxt_id
                )
                return

            config: Dict[str, Any] = {
                "apiKey": api_key,
                "secret": secret,
                "sandbox": testnet,
                "options": {"defaultType": "future"},
                "enableRateLimit": True,
            }
            # [2026-07-10 Phase0] 代理透传：国内环境访问 Binance/Bybit/OKX 必须走代理。
            # 不配代理 → ccxt 直连全部超时 → 多所聚合数据全空。
            _proxy = os.environ.get("BINANCE_HTTPS_PROXY") or os.environ.get("HTTPS_PROXY")
            if _proxy:
                config["proxies"] = {"http": _proxy, "https": _proxy}
                config["aiohttp_proxy"] = _proxy  # async WS (watch_order_book)
            if password:
                config["password"] = password
            config.update(self._extra_ccxt_config)

            self._exchange = cls(config)
        except ImportError:
            logger.warning(
                "ccxt not installed, %s adapter in stub mode",
                self._ccxt_id,
            )

    # ── Properties ────────────────────────────────

    @property
    def exchange_type(self) -> ExchangeType:
        return self._exchange_type

    @property
    def supports_spot(self) -> bool:
        return self._supports_spot_flag

    @property
    def supports_futures(self) -> bool:
        return self._supports_futures_flag

    # ── Balance ───────────────────────────────────

    async def get_balance(self) -> ExchangeBalance:
        if self._exchange is None:
            return ExchangeBalance(0, 0, 0, 0)
        try:
            bal = await self._exchange.fetch_balance()
            total = bal.get("total", {})
            free = bal.get("free", {})
            used = bal.get("used", {})
            usdt_total = float(total.get("USDT", 0) or 0)
            return ExchangeBalance(
                total_equity=usdt_total,
                available_balance=float(free.get("USDT", 0) or 0),
                frozen_margin=float(used.get("USDT", 0) or 0),
                unrealized_pnl=0,
            )
        except Exception as e:
            logger.warning("%s.get_balance failed: %s", self.__class__.__name__, e)
            return ExchangeBalance(0, 0, 0, 0)

    # ── Positions ─────────────────────────────────

    async def get_positions(self) -> List[ExchangePosition]:
        if self._exchange is None:
            return []
        try:
            raw = await self._exchange.fetch_positions()
            positions = []
            for p in raw:
                size = float(p.get("contracts", 0) or 0)
                if size == 0:
                    continue
                positions.append(
                    ExchangePosition(
                        symbol=p.get("symbol", ""),
                        side=p.get("side", ""),
                        size=size,
                        entry_price=float(p.get("entryPrice", 0) or 0),
                        mark_price=float(p.get("markPrice", 0) or 0),
                        unrealized_pnl=float(p.get("unrealizedPnl", 0) or 0),
                        margin=float(p.get("initialMargin", 0) or 0),
                        leverage=float(p.get("leverage", 1) or 1),
                        liquidation_price=_safe_float(p.get("liquidationPrice")),
                    )
                )
            return positions
        except Exception as e:
            logger.warning("%s.get_positions failed: %s", self.__class__.__name__, e)
            return []

    # ── Orders ────────────────────────────────────

    async def place_order(self, order: ExchangeOrder) -> Dict:
        if self._exchange is None:
            return {"status": "error", "message": "ccxt not available"}
        try:
            params: Dict[str, Any] = {}
            if order.reduce_only:
                params["reduceOnly"] = True
            if order.leverage and order.leverage != 1:
                params["leverage"] = order.leverage
            if getattr(order, "tp", None):
                params["takeProfitPrice"] = float(order.tp)
            if getattr(order, "sl", None):
                params["stopLossPrice"] = float(order.sl)
            result = await self._exchange.create_order(
                symbol=order.symbol,
                type=order.order_type.value,
                side=order.side.value,
                amount=order.size,
                price=order.price,
                params=params if params else None,
            )
            return result if isinstance(result, dict) else {"status": "ok"}
        except Exception as e:
            logger.warning("%s.place_order failed: %s", self.__class__.__name__, e)
            return {"status": "error", "message": str(e)}

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        if self._exchange is None:
            return False
        try:
            await self._exchange.cancel_order(order_id, symbol)
            return True
        except Exception as e:
            logger.warning("%s.cancel_order failed: %s", self.__class__.__name__, e)
            return False

    # ── Funding Rates ─────────────────────────────

    async def get_funding_rate(self, symbol: str) -> float:
        if self._exchange is None:
            return 0.0
        try:
            result = await self._exchange.fetch_funding_rate(symbol)
            return float(result.get("fundingRate", 0) or 0)
        except Exception as e:
            logger.warning(
                "%s.get_funding_rate failed: %s", self.__class__.__name__, e
            )
            return 0.0

    async def get_all_funding_rates(self) -> Dict[str, float]:
        if self._exchange is None:
            return {}
        try:
            results = await self._exchange.fetch_funding_rates()
            rates: Dict[str, float] = {}
            if isinstance(results, dict):
                for sym, item in results.items():
                    if isinstance(item, dict):
                        rates[sym] = float(item.get("fundingRate", 0) or 0)
                    else:
                        rates[sym] = float(item or 0)
            elif isinstance(results, list):
                for item in results:
                    if isinstance(item, dict):
                        rates[item.get("symbol", "")] = float(
                            item.get("fundingRate", 0) or 0
                        )
            return rates
        except Exception as e:
            logger.warning(
                "%s.get_all_funding_rates failed: %s", self.__class__.__name__, e
            )
            return {}

    async def get_funding_rate(self, symbol: str) -> Optional[float]:
        """获取单个交易对当前资金费率（小时/结算费率，小数）。

        2026-07-06 新增：部分交易所（如 OKX）不支持批量 fetch_funding_rates，
        或批量会踩到 linear/inverse 端点歧义（如 Asterdex 走 binance 驱动时的 dapiPublic）。
        逐 symbol 用统一 ccxt 符号（如 "BTC/USDT:USDT"）显式取 linear 永续，稳定可靠。
        无数据/异常返回 None（由上游决定跳过，绝不臆造）。
        """
        if self._exchange is None:
            return None
        try:
            res = await self._exchange.fetch_funding_rate(symbol)
            if isinstance(res, dict):
                r = res.get("fundingRate")
                if r is not None:
                    return float(r)
        except Exception as e:
            logger.debug(
                "%s.get_funding_rate(%s) failed: %s", self.__class__.__name__, symbol, e
            )
        return None

    # ── Orderbook ─────────────────────────────────

    async def get_orderbook(self, symbol: str, depth: int = 20) -> Dict:
        if self._exchange is None:
            return {"bids": [], "asks": []}
        try:
            book = await self._exchange.fetch_order_book(symbol, limit=depth)
            return book if isinstance(book, dict) else {"bids": [], "asks": []}
        except Exception as e:
            logger.warning(
                "%s.get_orderbook failed: %s", self.__class__.__name__, e
            )
            return {"bids": [], "asks": []}

    # ── Klines ────────────────────────────────────

    async def get_klines(
        self, symbol: str, interval: str, limit: int = 100
    ) -> List[Dict]:
        if self._exchange is None:
            return []
        try:
            ohlcv = await self._exchange.fetch_ohlcv(symbol, interval, limit=limit)
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
            logger.warning("%s.get_klines failed: %s", self.__class__.__name__, e)
            return []

    # ── 积分/返利套利扩展方法（带缓存 + 真实API） ──────

    # 子类可覆盖的费率配置类变量（作为 fallback 默认值）
    _fee_tier_config: Dict[str, Any] = {
        "tier_name": "VIP0",
        "maker_rate": 0.0002,
        "taker_rate": 0.0005,
        "rebate_rate": 0.0,
    }
    _base_rebate_rate: float = 0.0

    def _get_cache(self):
        """Lazy import cache to avoid circular imports."""
        from backend.services.rebate_arb.incentive_cache import incentive_cache
        return incentive_cache

    def _get_config(self):
        """Lazy import config."""
        try:
            from backend.config.rebate_config_loader import rebate_config
            return rebate_config
        except Exception:
            return None

    async def get_fee_tier(self) -> ExchangeFeeTier:
        """获取当前费率等级 — 优先从CCXT API获取，带TTL缓存和 fallback"""
        cache = self._get_cache()
        cache_key = f"{self._ccxt_id}_fee_tier"

        # Check cache
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        # Try real API
        if self._exchange is not None:
            try:
                # CCXT unified: fetchTradingFees() returns {symbol: {maker, taker, ...}}
                fees = await self._exchange.fetch_trading_fees()
                # Use BTC/USDT:USDT (futures) or BTC/USDT (spot) as reference
                ref_symbols = ["BTC/USDT:USDT", "BTC/USDT", "ETH/USDT:USDT"]
                maker = None
                taker = None
                for sym in ref_symbols:
                    if sym in fees:
                        maker = float(fees[sym].get("maker", 0) or 0)
                        taker = float(fees[sym].get("taker", 0) or 0)
                        break

                if maker is not None:
                    # Detect VIP tier from maker rate
                    tier_name = self._detect_vip_tier(maker, taker)
                    rebate_rate = abs(maker) if maker < 0 else 0.0
                    effective_maker = maker if maker >= 0 else 0.0

                    tier = ExchangeFeeTier(
                        exchange=self._ccxt_id,
                        tier_name=tier_name,
                        maker_rate=effective_maker,
                        taker_rate=taker,
                        rebate_rate=rebate_rate,
                    )
                    config = self._get_config()
                    ttl = config.cache_ttls.fee_tier_seconds if config else 3600
                    cache.set(cache_key, tier, ttl)
                    logger.info(
                        "[%s] Fee tier fetched: maker=%.5f%% taker=%.5f%% tier=%s",
                        self._ccxt_id, maker * 100, taker * 100, tier_name
                    )
                    return tier
            except Exception as e:
                logger.warning(
                    "[%s] fetchTradingFees failed: %s, using defaults",
                    self._ccxt_id, e
                )

        # Fallback to class defaults
        tier = ExchangeFeeTier(
            exchange=self._ccxt_id,
            tier_name=self._fee_tier_config.get("tier_name", "VIP0"),
            maker_rate=self._fee_tier_config.get("maker_rate", 0.0002),
            taker_rate=self._fee_tier_config.get("taker_rate", 0.0005),
            rebate_rate=self._fee_tier_config.get("rebate_rate", 0.0),
        )
        return tier

    def _detect_vip_tier(self, maker: float, taker: float) -> str:
        """Heuristic VIP tier detection from fee rates."""
        if maker < 0:
            return "MM"  # Market maker (negative fee = rebate)
        if maker <= 0.00005:
            return "VIP5+"
        if maker <= 0.0001:
            return "VIP3-4"
        if maker <= 0.00016:
            return "VIP1-2"
        return "VIP0"

    async def get_points_snapshot(self) -> ExchangePointsSnapshot:
        """获取积分快照 — 默认返回全0快照，需交易所子类覆盖"""
        return ExchangePointsSnapshot(
            exchange=self._ccxt_id,
        )

    async def get_rebate_info(self) -> ExchangeRebateInfo:
        """获取返利配置 — 基于 fee_tier 推导，子类可覆盖"""
        cache = self._get_cache()
        cache_key = f"{self._ccxt_id}_rebate_info"

        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        # Derive from fee tier
        fee_tier = await self.get_fee_tier()
        rebate_rate = fee_tier.rebate_rate if fee_tier.rebate_rate > 0 else self._base_rebate_rate

        info = ExchangeRebateInfo(
            exchange=self._ccxt_id,
            base_rebate_rate=self._base_rebate_rate,
            current_rebate_rate=rebate_rate,
        )

        config = self._get_config()
        ttl = config.cache_ttls.rebate_info_seconds if config else 600
        cache.set(cache_key, info, ttl)
        return info

    async def get_incentive_summary(self) -> ExchangeIncentiveSummary:
        """获取激励政策汇总 — 组合 get_fee_tier + get_points_snapshot + get_rebate_info"""
        import time

        fee_tier = await self.get_fee_tier()
        points = await self.get_points_snapshot()
        rebate = await self.get_rebate_info()
        return ExchangeIncentiveSummary(
            exchange=self._ccxt_id,
            exchange_type=self._exchange_type,
            fee_tier=fee_tier,
            points=points,
            rebate=rebate,
            is_connected=self._exchange is not None,
            last_update=time.time(),
        )

    async def get_active_campaigns(self) -> List[Dict]:
        """获取进行中的活动 — 默认返回空列表，需交易所子类覆盖"""
        return []

    # ── Cleanup ───────────────────────────────────

    async def close(self):
        """Release CCXT resources."""
        if self._exchange is not None:
            try:
                await self._exchange.close()
            except Exception:
                pass


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
