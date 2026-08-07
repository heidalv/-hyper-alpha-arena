"""
AsterdexMarketFlowCollector — Asterdex CVD/市场流采集器（ccxt.pro WS）

Asterdex 与 Binance 完全兼容（同一签名/端点结构），因此通过 ccxt.pro 的
binance 驱动 + 覆盖 base URL 实现 watch_trades（与 AsterdexAdapter 同构）。

数据流：
    ccxt.pro binance.watch_trades(symbol)  →  ExchangeTrade  →  基类 _on_trade
                                                       ↓
                                         MarketTradesAggregated (exchange='asterdex')

注意：
- Asterdex 的 trades 接口对应 Binance 的 @aggTrade / bookTicker WS 频道
- ccxt 的 trade 对象含 side（'buy'/'sell'），可直接映射到 ExchangeTrade.side
- aggTrade 的 side 表示 taker 方向，正好是 CVD 需要的"主动买卖"语义
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import threading
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional

from services.exchange.base_exchange_client import ExchangeTrade
from services.market_flow.base_collector import BaseMarketFlowCollector

from backend.services.symbol_normalizer import normalize_symbol

logger = logging.getLogger(__name__)

ASTERDEX_FUTURES_URL = "https://fapi.asterdex.com"


class AsterdexMarketFlowCollector(BaseMarketFlowCollector):
    """Asterdex CVD 采集器，基于 ccxt.pro watch_trades。"""

    DEFAULT_AGGREGATION_WINDOW_SECONDS = 15

    def __init__(self, aggregation_window_seconds=None, on_symbol_subscribed=None):
        super().__init__(aggregation_window_seconds, on_symbol_subscribed)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._exchange: Any = None  # ccxt.async_support.binance 实例（URL 已覆盖）
        self._ws_task: Optional[asyncio.Task] = None

    @property
    def exchange_id(self) -> str:
        return "asterdex"

    # ── 创建 ccxt.pro binance 实例并覆盖 URL 为 asterdex ──

    def _create_ccxt_exchange(self) -> Any:
        import ccxt.async_support as ccxt

        ex = ccxt.binance({
            "enableRateLimit": True,
            "options": {
                "defaultType": "future",
                # [2026-08-04 修复] ccxt binance fetch_markets 默认加载
                # ['spot','linear','inverse'] 三类市场；其中 spot 走 public URL
                # （fapi.asterdex.com/api/v3 实测 404），inverse 走 dapi URL（不存在）。
                # 只加载 linear（fapi 合约）即可，避免 load_markets 因现货 404 整体失败。
                "fetchMarkets": {"types": ["linear"]},
            },
            "timeout": 15000,  # 15s 超时（[2026-08-06] 10s→15s：容忍代理链路抖动，减少 SSL EOF 误判；容量收缩后单批任务数已下降，15s 不拖慢整轮）
            "apiKey": "",  # Aster DEX 公共端点不需要 API key
            "secret": "",
        })
        # 覆盖为 asterdex 端点（与 AsterdexAdapter 一致）。
        # [2026-08-04 修复] 此前 public/private 指向 https://api.asterdex.com/api/v3，
        # 该域名 DNS 不可解析 → ccxt 惰性 load_markets() 拉 exchangeInfo 100% 失败 →
        # fetch_trades/fetch_order_book 全部异常。Asterdex 全部端点都在
        # fapi.asterdex.com（实测 fapi/v1/exchangeInfo、fapi/v1/premiumIndex 均 200）。
        ex.urls["api"] = {
            "fapiPublic": ASTERDEX_FUTURES_URL + "/fapi/v1",
            "fapiPrivate": ASTERDEX_FUTURES_URL + "/fapi/v1",
            "fapiPublicV2": ASTERDEX_FUTURES_URL + "/fapi/v2",
            "fapiPrivateV2": ASTERDEX_FUTURES_URL + "/fapi/v2",
            "public": ASTERDEX_FUTURES_URL + "/api/v3",  # 与 AsterdexAdapter 一致
            "private": ASTERDEX_FUTURES_URL + "/api/v3",
            "vapiPublic": ASTERDEX_FUTURES_URL + "/vapi/v1",  # 期权 API
        }
        # 对于永续合约，override 完整的 URLs 结构
        ex.urls["www"] = "https://www.asterdex.com"

        # [2026-08-04 修复] 注入行情代理（与 CcxtBaseAdapter 一致）。
        # 实测：Python 直连 fapi.asterdex.com 被远端 TLS 重置（WinError 10054），
        # 经 127.0.0.1:1080 代理 200 可达 → 必须走代理。BINANCE_HTTPS_PROXY/HTTPS_PROXY
        # 来自 .env（行情侧代理），与 LLM 直连（LLM_HTTP_PROXY）完全隔离。
        _proxy = os.environ.get("BINANCE_HTTPS_PROXY") or os.environ.get("HTTPS_PROXY")
        if _proxy:
            ex.proxies = {"http": _proxy, "https": _proxy}
            ex.aiohttp_proxy = _proxy  # aiohttp 底层（fetch_trades/fetch_order_book 均走 REST）
        return ex

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """统一 symbol（如 'BTC'）→ ccxt 永续格式（'BTC/USDT:USDT'）。

        [2026-08-07] 统一走全局 normalize_symbol 得到纯 BASE，再拼 ccxt 格式。"""
        base = normalize_symbol(symbol)
        return f"{base}/USDT:USDT"

    @staticmethod
    def _denormalize_symbol(ccxt_symbol: str) -> str:
        """ccxt symbol（'BTC/USDT:USDT'）→ 统一 symbol（'BTC'）。"""
        return normalize_symbol(ccxt_symbol)

    # ── 行情源线程（在独立线程里跑 asyncio event loop）──

    def _run_source_loop(self, symbols: List[str]) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main(symbols))
        except Exception as e:
            logger.error("[asterdex] 行情源线程异常: %s", e, exc_info=True)
        finally:
            # 清理 ccxt 连接
            if self._exchange is not None:
                try:
                    self._loop.run_until_complete(self._exchange.close())
                except Exception:
                    pass
            self._loop.close()
            self._loop = None
            self._source_started.set()

    async def _async_main(self, symbols: List[str]) -> None:
        """主协程：创建 exchange、登记 symbols、启动 REST 轮询循环（trades + orderbook + asset_metrics）。"""
        try:
            self._exchange = self._create_ccxt_exchange()
        except Exception as e:
            logger.error("[asterdex] 创建 ccxt 实例失败: %s", e, exc_info=True)
            return

        # [2026-08-04 修复] 预热 load_markets：
        # fetch_trades/fetch_order_book 首次调用都会惰性 load_markets（拉 exchangeInfo），
        # 30 个并发任务同时触发会串行等待。这里先主动加载一次，让轮询循环立即就绪。
        try:
            await self._exchange.load_markets()
            _mc = len(getattr(self._exchange, "markets", {}) or {})
            logger.info("[asterdex] load_markets 预热完成，合约市场数=%s", _mc)
        except Exception as e:
            logger.warning("[asterdex] load_markets 预热失败: %s（轮询将在各循环内继续尝试）", e)

        # 登记 subscribed_symbols（用统一 symbol 名，便于落库）
        self.subscribed_symbols = list(symbols)
        for s in symbols:
            self.trade_buffers.setdefault(s, self._new_buffer())

        # [2026-08-04 修复] 全站并发限流：
        # 全部 asterdex REST 请求共享同一出口 IP（Shadowsocks 代理 126.227.100.196），
        # 30 symbol × 3 任务 = 90 个协程启动瞬间齐发 + P0/P1/P2 + ticker 轮询并发，
        # 会在几秒内触发 Asterdex WAF 418 封禁（实测 ban 到 16:00，全员 418）。
        # 方案：全局信号量限 4 并发 + 每任务随机错峰起步，把瞬时速率压回可接受范围。
        self._poll_sem = asyncio.Semaphore(4)

        self._source_started.set()
        logger.info("[asterdex] REST 轮询启动 (trades + orderbook + asset_metrics)，symbols=%s", symbols)

        # 每个 symbol 三个并发任务：trades + orderbook + asset_metrics (全部使用 REST 轮询)
        tasks = []
        for idx, sym in enumerate(symbols):
            # 启动错峰：同一 symbol 的三个任务依次错开，symbol 间再错开，
            # 避免 gather 后 90 个任务同时发出第一批请求。
            stagger = (idx % 6) * 0.8 + random.random() * 0.6
            tasks.append(asyncio.create_task(self._staggered(self._poll_trades_loop, sym, stagger)))
            tasks.append(asyncio.create_task(self._staggered(self._poll_orderbook_loop, sym, stagger + 1.0)))
            tasks.append(asyncio.create_task(self._staggered(self._poll_asset_metrics_loop, sym, stagger + 2.0)))
        
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            for t in tasks:
                t.cancel()

    @staticmethod
    def _new_buffer():
        from services.market_flow.base_collector import TradeBuffer
        return TradeBuffer()

    @staticmethod
    def _rate_limited(exc: Exception) -> bool:
        """WAF 限流/封禁判定：418/429/Too Many Requests/-1003 需长退避。

        实测 Asterdex 封禁消息形如：
        `418 Client Error (418) {"code":-1003,"msg":"Way too many requests; IP(...) banned until ..."}`
        """
        msg = str(exc).lower()
        return any(
            k in msg
            for k in (
                "418", "429", "too many requests", "way too many",
                "banned", "-1003", "rate limit",
            )
        )

    async def _staggered(self, fn, symbol: str, delay: float) -> None:
        """启动错峰包装：每个轮询协程先睡 delay 秒再进入正式循环。

        90 个任务同时首发的第一个请求是 WAF 封禁的主要诱因；错峰 + 信号量
        双管齐下把瞬时并发压到个位数。
        """
        await asyncio.sleep(max(0.0, delay))
        try:
            await fn(symbol)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[asterdex] 轮询协程 %s 提前退出: %s", symbol, e)

    async def _wait_global_ban(self) -> None:
        """[2026-08-04 修复] 接入 kline_collectors 进程级全局限流封禁。

        Asterdex 2400 req/min 是整 IP 共享的。任一组件（P0/P1/P2/depth/
        market_flow）命中 429/418 都会触发全局冷却（默认 180s）。market_flow
        在冷却期内也必须停手，否则自己 120s 退避后恢复发包会继续撞窗口，
        拖慢整 IP 的恢复。这里每轮轮询前检查全局冷却，未过则等待。
        """
        from backend.services.kline_collectors import _AsterdexRateLimiter
        while self.running:
            rem = _AsterdexRateLimiter.banned_remaining()
            if rem <= 0:
                return
            await asyncio.sleep(min(rem, 10.0))

    async def _poll_trades_loop(self, symbol: str) -> None:
        """单 symbol 的 trades REST 轮询循环（每 5 秒获取最近 trades）。"""
        ccxt_symbol = self._normalize_symbol(symbol)
        last_trade_id = None
        
        while self.running:
            try:
                # 全局冷却检查（429/418 后全链路停手）
                await self._wait_global_ban()
                # 使用 fetch_trades 获取最近的 trades（受全局信号量限流）
                async with self._poll_sem:
                    trades = await self._exchange.fetch_trades(ccxt_symbol, limit=100)
                
                if trades:
                    for tr in trades:
                        trade_id = tr.get("id")
                        # 避免重复处理
                        if last_trade_id and trade_id and str(trade_id) <= str(last_trade_id):
                            continue
                        
                        side = tr.get("side", "")
                        price = tr.get("price")
                        amount = tr.get("amount")
                        ts = tr.get("timestamp") or int(time.time() * 1000)
                        
                        if price is None or amount is None:
                            continue
                        
                        self._on_trade(ExchangeTrade(
                            timestamp=int(ts),
                            symbol=symbol,
                            price=float(price),
                            size=float(amount),
                            side=side if side in ("buy", "sell") else "buy",
                        ))
                        
                        if trade_id:
                            last_trade_id = trade_id
                
                # 30 秒轮询间隔（原 15s，进一步降频：全站共享 2400 req/min 上限，
                # market_flow 三通道合计需让出配额给 P0/P1/P2）
                await asyncio.sleep(30)
                
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self._rate_limited(e):
                    from backend.services.kline_collectors import _AsterdexRateLimiter
                    _AsterdexRateLimiter.note_banned()
                backoff = 120 if self._rate_limited(e) else 10
                logger.warning(
                    "[asterdex] poll_trades %s 异常: %s，%ds 后重试",
                    ccxt_symbol, e, backoff,
                )
                await asyncio.sleep(backoff)

    async def _poll_orderbook_loop(self, symbol: str) -> None:
        """单 symbol 的 orderbook REST 轮询循环（每 10 秒获取一次）。"""
        ccxt_symbol = self._normalize_symbol(symbol)
        
        while self.running:
            try:
                # 全局冷却检查（429/418 后全链路停手）
                await self._wait_global_ban()
                # 使用 fetch_order_book 获取订单簿（受全局信号量限流）
                async with self._poll_sem:
                    ob = await self._exchange.fetch_order_book(ccxt_symbol, limit=20)
                
                # 转换为统一格式喂给基类
                self._on_orderbook(symbol, {
                    "bids": [{"px": str(p), "sz": str(s)} for p, s, *_ in ob.get("bids", [])[:20]],
                    "asks": [{"px": str(p), "sz": str(s)} for p, s, *_ in ob.get("asks", [])[:20]],
                })
                
                # 30 秒轮询间隔（原 20s，降频让配额给 P0/P1/P2）
                await asyncio.sleep(30)
                
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self._rate_limited(e):
                    from backend.services.kline_collectors import _AsterdexRateLimiter
                    _AsterdexRateLimiter.note_banned()
                backoff = 120 if self._rate_limited(e) else 20
                logger.warning(
                    "[asterdex] poll_orderbook %s 异常: %s，%ds 后重试",
                    ccxt_symbol, e, backoff,
                )
                await asyncio.sleep(backoff)

    async def _poll_asset_metrics_loop(self, symbol: str) -> None:
        """每 30 秒轮询一次资产指标（funding rate / mark price）"""
        ccxt_symbol = self._normalize_symbol(symbol)
        # [2026-08-04 修复] 此前 ccxt_symbol.replace("/","").replace(":","") 把
        # "BTC/USDT:USDT" 变成 "BTCUSDTUSDT"（USDT 重复）→ 交易所返回 Invalid symbol。
        # 正确做法：取基础币 + USDT（如 BTC → BTCUSDT）。
        binance_symbol = symbol.replace("-", "").replace("_", "").replace("/", "").upper() + "USDT"
        
        while self.running:
            try:
                # 全局冷却检查（429/418 后全链路停手）
                await self._wait_global_ban()
                # 通过 REST API 获取 funding rate / mark price
                # Aster DEX 兼容 Binance API: GET /fapi/v1/premiumIndex
                # [2026-08-04 修复] fapiPrivateGetPremiumIndex 不存在（ccxt binance 只有
                # fapiPublicGetPremiumIndex），premiumIndex 是公开端点。此前 100% AttributeError。
                async with self._poll_sem:
                    response = await self._exchange.fapiPublicGetPremiumIndex({"symbol": binance_symbol})

                self._on_asset_ctx(symbol, {
                    "ctx": {
                        "funding": response.get("lastFundingRate"),
                        "markPx": response.get("markPrice"),
                        "openInterest": None,  # Aster DEX premiumIndex 实测不提供 OI
                    }
                })
                
                await asyncio.sleep(120)  # 120 秒轮询（原 60s，资金费率变化缓慢，进一步降频让配额）
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self._rate_limited(e):
                    from backend.services.kline_collectors import _AsterdexRateLimiter
                    _AsterdexRateLimiter.note_banned()
                backoff = 120 if self._rate_limited(e) else 60
                logger.warning("[asterdex] poll_asset_metrics %s 异常: %s，%ds 后重试", symbol, e, backoff)
                await asyncio.sleep(backoff)

    # ── 停止 ──

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        super().stop()  # 取消 flush 定时器 + 残留 flush
        # event loop 的退出由 _run_source_loop 的 gather 返回驱动（running=False 后循环自然结束）
        logger.info("[asterdex] 采集器已停止")

    # ── 订单簿落库（覆盖基类空实现）──

    def _flush_orderbook(self, db, symbol: str, timestamp_ms: int) -> None:
        """复用 hyperliquid 的实现逻辑，适配 Aster DEX 数据格式"""
        from database.models import MarketOrderbookSnapshots

        l2book_age = time.time() - self.last_update_time["l2book"]
        if self.last_update_time["l2book"] > 0 and l2book_age > 30:
            return

        data = self.latest_orderbook.get(symbol)
        if not data:
            return

        try:
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            best_bid = Decimal(bids[0]["px"]) if bids else None
            best_ask = Decimal(asks[0]["px"]) if asks else None
            spread = (best_ask - best_bid) if (best_bid and best_ask) else None

            bid_depth_5 = sum(Decimal(b["sz"]) for b in bids[:5])
            ask_depth_5 = sum(Decimal(a["sz"]) for a in asks[:5])
            bid_depth_10 = sum(Decimal(b["sz"]) for b in bids[:10])
            ask_depth_10 = sum(Decimal(a["sz"]) for a in asks[:10])
            bid_orders = len(bids)
            ask_orders = len(asks)

            existing = db.query(MarketOrderbookSnapshots).filter(
                MarketOrderbookSnapshots.exchange == self.exchange_id,
                MarketOrderbookSnapshots.symbol == symbol,
                MarketOrderbookSnapshots.timestamp == timestamp_ms,
            ).first()

            if existing:
                existing.best_bid = best_bid
                existing.best_ask = best_ask
                existing.spread = spread
                existing.bid_depth_5 = bid_depth_5
                existing.ask_depth_5 = ask_depth_5
                existing.bid_depth_10 = bid_depth_10
                existing.ask_depth_10 = ask_depth_10
                existing.bid_orders_count = bid_orders
                existing.ask_orders_count = ask_orders
            else:
                db.add(MarketOrderbookSnapshots(
                    exchange=self.exchange_id,
                    symbol=symbol,
                    timestamp=timestamp_ms,
                    best_bid=best_bid,
                    best_ask=best_ask,
                    spread=spread,
                    bid_depth_5=bid_depth_5,
                    ask_depth_5=ask_depth_5,
                    bid_depth_10=bid_depth_10,
                    ask_depth_10=ask_depth_10,
                    bid_orders_count=bid_orders,
                    ask_orders_count=ask_orders,
                ))
        except Exception as e:
            logger.error("[asterdex] orderbook flush %s 异常: %s", symbol, e)

    # ── 资产指标落库（覆盖基类空实现）──

    def _flush_asset_metrics(self, db, symbol: str, timestamp_ms: int) -> None:
        """复用 hyperliquid 的逻辑，适配 Aster DEX 数据格式"""
        from database.models import MarketAssetMetrics

        asset_ctx_age = time.time() - self.last_update_time["asset_ctx"]
        if self.last_update_time["asset_ctx"] > 0 and asset_ctx_age > 30:
            return

        data = self.latest_asset_ctx.get(symbol)
        if not data:
            return

        try:
            ctx = data.get("ctx", {})
            existing = db.query(MarketAssetMetrics).filter(
                MarketAssetMetrics.exchange == self.exchange_id,
                MarketAssetMetrics.symbol == symbol,
                MarketAssetMetrics.timestamp == timestamp_ms,
            ).first()

            def _d(key):
                val = ctx.get(key)
                return Decimal(str(val)) if val is not None else None

            if existing:
                existing.funding_rate = _d("funding")
                existing.mark_price = _d("markPx")
                existing.open_interest = _d("openInterest")
            else:
                db.add(MarketAssetMetrics(
                    exchange=self.exchange_id,
                    symbol=symbol,
                    timestamp=timestamp_ms,
                    funding_rate=_d("funding"),
                    mark_price=_d("markPx"),
                    open_interest=_d("openInterest"),
                ))
        except Exception as e:
            logger.error("[asterdex] asset_metrics flush %s 异常: %s", symbol, e)
