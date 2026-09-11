"""
HyperliquidMarketFlowCollector — Hyperliquid 原生 SDK WS 市场流采集器

从旧的 services/market_flow_collector.py（单所硬绑单例）抽取而来，
重构为继承 BaseMarketFlowCollector，纳入多交易所注册表体系。

保留的能力：
- 原生 Hyperliquid SDK（Info）的 trades / l2Book / activeAssetCtx 三频道订阅
- 健康检查 + 指数退避重连 + 降级模式无限重试
- orderbook / asset_metrics / perp_funding 落库

变化：
- endpoint 从硬编码 https://api.hyperliquid.xyz 改为读 settings.HYPERLIQUID_API_URL
- 聚合窗口由 registry 启动时传入（settings.CVD_AGGREGATION_WINDOW_SECONDS）
- trades 解析后转为统一 ExchangeTrade 再喂给基类 _on_trade
"""

from __future__ import annotations

import json
import logging
import threading
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional
from collections import defaultdict

from hyperliquid.info import Info

from services.exchange.base_exchange_client import ExchangeTrade
from services.market_flow.base_collector import (
    BaseMarketFlowCollector,
    TradeBuffer,
)

logger = logging.getLogger(__name__)

# 连接健康检查 / 重连参数（沿用旧实现）
HEALTH_CHECK_INTERVAL_SECONDS = 30
DATA_STALE_THRESHOLD_SECONDS = 30
MAX_RECONNECT_ATTEMPTS = 5
RECONNECT_BASE_DELAY_SECONDS = 5
DEGRADED_MODE_RETRY_INTERVAL_SECONDS = 120
DEGRADED_MODE_LOG_INTERVAL = 5


class HyperliquidMarketFlowCollector(BaseMarketFlowCollector):
    """Hyperliquid 原生 SDK WS 采集器。"""

    # 健康检查默认关闭于基类；HL 实现自带动效
    DEFAULT_AGGREGATION_WINDOW_SECONDS = 15

    def __init__(self, aggregation_window_seconds=None, on_symbol_subscribed=None):
        super().__init__(aggregation_window_seconds, on_symbol_subscribed)

        self.info: Optional[Info] = None
        self.subscription_ids: Dict[str, Dict[str, int]] = defaultdict(dict)

        # 健康检查 / 重连状态
        self.health_check_timer: Optional[threading.Timer] = None
        self.reconnect_attempts = 0
        self.is_reconnecting = False
        self.reconnect_lock = threading.Lock()
        self.degraded_mode = False
        self.degraded_retry_count = 0
        self.degraded_retry_timer: Optional[threading.Timer] = None

    @property
    def exchange_id(self) -> str:
        return "hyperliquid"

    def _get_base_url(self) -> str:
        try:
            from config import settings
            return settings.HYPERLIQUID_API_URL
        except Exception:
            return "https://api.hyperliquid.xyz"

    # ── 启动入口（覆盖基类，因为 HL 需要在源线程里建立 Info 连接 + 订阅）──

    def start(self, symbols=None) -> bool:
        if self.running:
            logger.warning("[hyperliquid] collector 已在运行")
            return True

        if symbols is not None:
            self._pending_symbols = list(symbols)

        # 若调用方未给 symbols，沿用旧的 watchlist/信号池/AI_TRADING_SYMBOLS 回退逻辑
        if not self._pending_symbols:
            self._pending_symbols = self._resolve_default_symbols()

        if not self._pending_symbols:
            logger.warning("[hyperliquid] 无 symbols，采集器未启动")
            return False

        self.running = True
        self.reconnect_attempts = 0
        self.subscribed_symbols = []
        self.subscription_ids.clear()
        self.trade_buffers = {}

        try:
            self._connect_and_subscribe()
        except Exception as e:
            _log_level = logging.WARNING if self.reconnect_attempts > 0 else logging.ERROR
            logger.log(_log_level, "[hyperliquid] 启动连接失败 (attempt %d): %s",
                       self.reconnect_attempts + 1, e)
            if self.reconnect_attempts == 0:
                logger.error("[hyperliquid] 启动连接失败(首次): %s", e, exc_info=True)
            self.running = False
            self._schedule_start_retry()
            # 返回 True 表示已有重试在途，调用方视为"接受"
            return True

        self._schedule_flush()
        self._schedule_health_check()
        logger.info(
            "[hyperliquid] 采集器已启动，symbols=%s", self._pending_symbols,
        )
        return True

    def _resolve_default_symbols(self) -> List[str]:
        """沿用旧 MarketFlowCollector.start 的 symbol 解析逻辑。"""
        try:
            from services.hyperliquid_symbol_service import get_selected_symbols
            syms = get_selected_symbols()
            if syms:
                return syms
        except Exception:
            pass

        try:
            from backend.database.connection import SessionLocal
            from sqlalchemy import text
            db = SessionLocal()
            try:
                result = db.execute(
                    text("SELECT symbols FROM signal_pools WHERE enabled = true")
                )
                pool_symbols: List[str] = []
                for (raw,) in result.fetchall():
                    parsed = json.loads(raw) if isinstance(raw, str) else (raw or [])
                    if isinstance(parsed, list):
                        pool_symbols.extend(
                            [s for s in parsed if isinstance(s, str) and s.strip()]
                        )
                excluded = {"MULTI", "*", "ALL"}
                syms = sorted({
                    s.strip().upper() for s in pool_symbols
                    if s.strip().upper() not in excluded
                })
                if syms:
                    return syms
            finally:
                db.close()
        except Exception as e:
            logger.warning("[hyperliquid] 信号池 symbol 加载失败: %s", e)

        try:
            from services.trading_commands import AI_TRADING_SYMBOLS
            return list(AI_TRADING_SYMBOLS)
        except Exception as e:
            logger.warning("[hyperliquid] AI_TRADING_SYMBOLS 回退失败: %s", e)
        return []

    def _connect_and_subscribe(self) -> None:
        base_url = self._get_base_url()
        logger.info("[hyperliquid] 连接 API: %s", base_url)
        try:
            # [2026-07-18 修复] Info() 默认会调用 self.spot_meta() 拉取现货市场元数据，
            # 逐个 universe 条目用 base,quote = spot_info["tokens"] 去索引 spot_meta["tokens"] # 列表——一旦 Hyperliquid 线上新增/调整了某个现货对而 tokens 列表暂时不同步 # （官方接口自身的数据一致性问题，不是本项目代码bug），任何客户端在这个窗口期
            # 构造 Info() 都会 100% 复现 IndexError，且是确定性的、重试也不会自愈（不是网络 # 抖动）——这正是此前"重试5次全部失败、WS连不上、L2/trades全断"的根因。本项目只做
            # 永续合约(perp)交易，完全不需要现货元数据，因此传入空 spot_meta 跳过这段有问题的
            # 现货解析逻辑（Info 支持显式传入 spot_meta 来跳过内部的 self.spot_meta() 拉取）。
            self.info = Info(
                base_url=base_url, skip_ws=False,
                spot_meta={"universe": [], "tokens": []},
            )
        except IndexError as _idx_err:
            raise ConnectionError(
                f"Hyperliquid Info 初始化失败 (API 可能暂时不可用): {_idx_err}"
            ) from _idx_err
        for symbol in self._pending_symbols:
            self._subscribe_symbol(symbol)

    def _schedule_start_retry(self) -> None:
        """启动失败后的重试（沿用旧的指数退避 + 降级模式）。"""
        self.reconnect_attempts += 1
        if self.reconnect_attempts <= MAX_RECONNECT_ATTEMPTS:
            delay = RECONNECT_BASE_DELAY_SECONDS * (2 ** (self.reconnect_attempts - 1))
            logger.warning(
                "[hyperliquid] 启动重试 %d/%d，%ss 后重试",
                self.reconnect_attempts, MAX_RECONNECT_ATTEMPTS, delay,
            )
            t = threading.Timer(delay, self._retry_start)
            t.daemon = True
            t.start()
        else:
            self.degraded_mode = True
            self.degraded_retry_count = 0
            logger.warning(
                "[hyperliquid] 常规重试耗尽，进入降级模式（每 %ss 重试）",
                DEGRADED_MODE_RETRY_INTERVAL_SECONDS,
            )
            self._schedule_degraded_retry()

    def _retry_start(self) -> None:
        if not self.running:
            return
        try:
            self._connect_and_subscribe()
            self.reconnect_attempts = 0
            self.degraded_mode = False
            self.degraded_retry_count = 0
            self._schedule_flush()
            self._schedule_health_check()
            logger.info("[hyperliquid] 重连成功，symbols=%s", self.subscribed_symbols)
        except Exception as e:
            logger.error("[hyperliquid] 重连失败: %s", e, exc_info=True)
            self.info = None
            self._schedule_start_retry()

    # ── 订阅 ──

    def _subscribe_symbol(self, symbol: str) -> None:
        if not self.info:
            return
        try:
            self.trade_buffers.setdefault(symbol, TradeBuffer())

            trades_id = self.info.subscribe(
                {"type": "trades", "coin": symbol},
                lambda msg, s=symbol: self._on_trades_raw(s, msg),
            )
            self.subscription_ids[symbol]["trades"] = trades_id

            l2_id = self.info.subscribe(
                {"type": "l2Book", "coin": symbol},
                lambda msg, s=symbol: self._on_l2book_raw(s, msg),
            )
            self.subscription_ids[symbol]["l2Book"] = l2_id

            ctx_id = self.info.subscribe(
                {"type": "activeAssetCtx", "coin": symbol},
                lambda msg, s=symbol: self._on_asset_ctx_raw(s, msg),
            )
            self.subscription_ids[symbol]["activeAssetCtx"] = ctx_id

            if symbol not in self.subscribed_symbols:
                self.subscribed_symbols.append(symbol)
        except Exception as e:
            logger.error("[hyperliquid] 订阅 %s 失败: %s", symbol, e)

    def _unsubscribe_symbol(self, symbol: str) -> None:
        if not self.info or symbol not in self.subscription_ids:
            return
        try:
            ids = self.subscription_ids[symbol]
            for ch, sid in ids.items():
                chname = {"trades": "trades", "l2Book": "l2Book",
                          "activeAssetCtx": "activeAssetCtx"}[ch]
                self.info.unsubscribe({"type": chname, "coin": symbol}, sid)
            del self.subscription_ids[symbol]
            if symbol in self.subscribed_symbols:
                self.subscribed_symbols.remove(symbol)
            self.trade_buffers.pop(symbol, None)
        except Exception as e:
            logger.error("[hyperliquid] 取消订阅 %s 失败: %s", symbol, e)

    # ── SDK 原始回调 → 转 ExchangeTrade / 喂基类 ──

    def _on_trades_raw(self, symbol: str, msg: dict) -> None:
        try:
            if msg.get("channel") != "trades":
                return
            trades = msg.get("data", [])
            if not trades:
                return
            self.last_update_time["trades"] = time.time()
            for tr in trades:
                # SDK: side "B"=主动买, "A"=主动卖
                side = "buy" if tr.get("side") == "B" else "sell"
                self._on_trade(ExchangeTrade(
                    timestamp=int(tr.get("time", time.time() * 1000)),
                    symbol=symbol,
                    price=float(tr["px"]),
                    size=float(tr["sz"]),
                    side=side,
                ))
        except Exception as e:
            logger.error("[hyperliquid] trades 解析异常 %s: %s", symbol, e)

    def _on_l2book_raw(self, symbol: str, msg: dict) -> None:
        try:
            if msg.get("channel") != "l2Book":
                return
            data = msg.get("data", {})
            if data:
                self._on_orderbook(symbol, data)
                # 兼容：推送至跨所 mid 缓存
                try:
                    from services.arbitrage.cross_exchange_ws_feed import (
                        push_hyperliquid_l2book,
                    )
                    push_hyperliquid_l2book(symbol, data)
                except Exception:
                    pass
        except Exception as e:
            logger.error("[hyperliquid] l2book 解析异常 %s: %s", symbol, e)

    def _on_asset_ctx_raw(self, symbol: str, msg: dict) -> None:
        try:
            channel = msg.get("channel")
            if channel not in ("activeAssetCtx", "activeSpotAssetCtx"):
                return
            data = msg.get("data", {})
            if data:
                self._on_asset_ctx(symbol, data)
                try:
                    from services.market_data_hub import market_data_hub
                    market_data_hub.publish_asset_ctx(
                        self.exchange_id, symbol, data, source="ws",
                    )
                    ctx = data.get("ctx", data)
                    funding = ctx.get("funding") if isinstance(ctx, dict) else None
                    if funding is not None:
                        market_data_hub.publish_funding(
                            self.exchange_id, symbol,
                            {"rate": float(funding)}, source="ws",
                        )
                except Exception:
                    pass
        except Exception as e:
            logger.error("[hyperliquid] asset_ctx 解析异常 %s: %s", symbol, e)

    # ── 行情源线程（HL 用 SDK 的 WS 后台线程，源线程只做空循环保活）──

    def _run_source_loop(self, symbols: List[str]) -> None:
        """
        HL SDK 的 WS 是通过 subscribe() 内部启动后台线程驱动的（Info 内部管理），
        所以这里只需在 self.running 为 True 时阻塞等待，保证线程不退出。
        """
        self._source_started.set()
        while self.running:
            time.sleep(1.0)

    # ── 停止 ──

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False

        for timer_attr in ("flush_timer", "health_check_timer", "degraded_retry_timer"):
            t = getattr(self, timer_attr, None)
            if t:
                t.cancel()
                setattr(self, timer_attr, None)

        try:
            self._flush_to_database()
        except Exception as e:
            logger.warning("[hyperliquid] stop flush 失败: %s", e)

        for symbol in list(self.subscribed_symbols):
            self._unsubscribe_symbol(symbol)

        if self.info and getattr(self.info, "ws_manager", None):
            try:
                self.info.disconnect_websocket()
            except Exception as e:
                logger.warning("[hyperliquid] WS 断开异常: %s", e)
        self.info = None

        self.degraded_mode = False
        self.degraded_retry_count = 0
        logger.info("[hyperliquid] 采集器已停止")

    # ── 健康检查 / 重连 ──

    def _schedule_health_check(self) -> None:
        if not self.running:
            return
        self.health_check_timer = threading.Timer(
            HEALTH_CHECK_INTERVAL_SECONDS, self._health_check_and_reschedule,
        )
        self.health_check_timer.daemon = True
        self.health_check_timer.start()

    def _health_check_and_reschedule(self) -> None:
        if not self.running:
            return
        self._check_connection_health()
        self._schedule_health_check()

    def _check_connection_health(self) -> None:
        if self.is_reconnecting or self.degraded_mode:
            return
        now = time.time()
        l2book_age = (now - self.last_update_time["l2book"]
                      if self.last_update_time["l2book"] > 0 else -1)
        asset_ctx_age = (now - self.last_update_time["asset_ctx"]
                         if self.last_update_time["asset_ctx"] > 0 else -1)
        if (self.last_update_time["l2book"] > 0
                and l2book_age > DATA_STALE_THRESHOLD_SECONDS
                and self.last_update_time["asset_ctx"] > 0
                and asset_ctx_age > DATA_STALE_THRESHOLD_SECONDS):
            logger.warning(
                "[hyperliquid] 数据过期 l2book=%.0fs asset_ctx=%.0fs，触发重连",
                l2book_age, asset_ctx_age,
            )
            self._reconnect()

    def _reconnect(self) -> None:
        with self.reconnect_lock:
            if self.is_reconnecting:
                return
            self.is_reconnecting = True
        try:
            if self.reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
                if not self.degraded_mode:
                    self.degraded_mode = True
                    self.degraded_retry_count = 0
                    logger.warning("[hyperliquid] 进入降级模式")
                self.degraded_retry_count += 1
                if self.degraded_retry_count % DEGRADED_MODE_LOG_INTERVAL == 1:
                    logger.warning(
                        "[hyperliquid] 降级模式重试 #%d", self.degraded_retry_count,
                    )
            else:
                self.reconnect_attempts += 1
                delay = RECONNECT_BASE_DELAY_SECONDS * (2 ** (self.reconnect_attempts - 1))
                logger.warning(
                    "[hyperliquid] 重连尝试 %d/%d，延迟 %ss",
                    self.reconnect_attempts, MAX_RECONNECT_ATTEMPTS, delay,
                )
                time.sleep(delay)

            symbols_to_restore = list(self.subscribed_symbols) or self._pending_symbols
            self._cleanup_old_connection()
            self._connect_and_subscribe()

            self.reconnect_attempts = 0
            self.degraded_mode = False
            self.degraded_retry_count = 0
            now = time.time()
            for k in ("l2book", "asset_ctx", "trades"):
                self.last_update_time[k] = now
            logger.info("[hyperliquid] 重连成功，恢复 %d symbols", len(symbols_to_restore))
        except Exception as e:
            logger.error("[hyperliquid] 重连失败: %s", e, exc_info=True)
            self.info = None
            if self.degraded_mode:
                self._schedule_degraded_retry()
        finally:
            self.is_reconnecting = False

    def _cleanup_old_connection(self) -> None:
        if self.info and getattr(self.info, "ws_manager", None):
            try:
                self.info.disconnect_websocket()
            except Exception:
                pass
        self.info = None
        self.subscribed_symbols = []
        self.subscription_ids.clear()

    def _schedule_degraded_retry(self) -> None:
        if not self.running:
            return
        self.degraded_retry_timer = threading.Timer(
            DEGRADED_MODE_RETRY_INTERVAL_SECONDS, self._reconnect,
        )
        self.degraded_retry_timer.daemon = True
        self.degraded_retry_timer.start()

    # ── 订单簿 / 资产指标落库（HL 特有，覆盖基类空实现）──

    def _flush_orderbook(self, db, symbol: str, timestamp_ms: int) -> None:
        from backend.database.models import MarketOrderbookSnapshots

        l2book_age = time.time() - self.last_update_time["l2book"]
        if self.last_update_time["l2book"] > 0 and l2book_age > DATA_STALE_THRESHOLD_SECONDS:
            return
        data = self.latest_orderbook.get(symbol)
        if not data:
            return
        try:
            levels = data.get("levels", [[], []])
            bids = levels[0] if len(levels) > 0 else []
            asks = levels[1] if len(levels) > 1 else []
            best_bid = Decimal(bids[0]["px"]) if bids else None
            best_ask = Decimal(asks[0]["px"]) if asks else None
            spread = (best_ask - best_bid) if (best_bid and best_ask) else None
            bid_depth_5 = sum(Decimal(b["sz"]) for b in bids[:5])
            ask_depth_5 = sum(Decimal(a["sz"]) for a in asks[:5])
            bid_depth_10 = sum(Decimal(b["sz"]) for b in bids[:10])
            ask_depth_10 = sum(Decimal(a["sz"]) for a in asks[:10])
            bid_orders = sum(b.get("n", 1) for b in bids)
            ask_orders = sum(a.get("n", 1) for a in asks)

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
                existing.raw_levels = json.dumps(levels)
            else:
                db.add(MarketOrderbookSnapshots(
                    exchange=self.exchange_id, symbol=symbol, timestamp=timestamp_ms,
                    best_bid=best_bid, best_ask=best_ask, spread=spread,
                    bid_depth_5=bid_depth_5, ask_depth_5=ask_depth_5,
                    bid_depth_10=bid_depth_10, ask_depth_10=ask_depth_10,
                    bid_orders_count=bid_orders, ask_orders_count=ask_orders,
                    raw_levels=json.dumps(levels),
                ))
        except Exception as e:
            logger.error("[hyperliquid] orderbook flush %s 异常: %s", symbol, e)

    def _flush_asset_metrics(self, db, symbol: str, timestamp_ms: int) -> None:
        from backend.database.models import MarketAssetMetrics

        asset_ctx_age = time.time() - self.last_update_time["asset_ctx"]
        if self.last_update_time["asset_ctx"] > 0 and asset_ctx_age > DATA_STALE_THRESHOLD_SECONDS:
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
                return Decimal(ctx[key]) if ctx.get(key) else None

            if existing:
                existing.open_interest = _d("openInterest")
                existing.funding_rate = _d("funding")
                existing.mark_price = _d("markPx")
                existing.oracle_price = _d("oraclePx")
                existing.mid_price = _d("midPx")
                existing.premium = _d("premium")
                existing.day_notional_volume = _d("dayNtlVlm")
            else:
                db.add(MarketAssetMetrics(
                    exchange=self.exchange_id, symbol=symbol, timestamp=timestamp_ms,
                    open_interest=_d("openInterest"), funding_rate=_d("funding"),
                    mark_price=_d("markPx"), oracle_price=_d("oraclePx"),
                    mid_price=_d("midPx"), premium=_d("premium"),
                    day_notional_volume=_d("dayNtlVlm"),
                ))

            if ctx.get("funding"):
                self._save_perp_funding(db, symbol, timestamp_ms, ctx)
        except Exception as e:
            logger.error("[hyperliquid] asset_metrics flush %s 异常: %s", symbol, e)

    def _save_perp_funding(self, db, symbol: str, timestamp_ms: int, ctx: dict) -> None:
        try:
            from backend.database.models import PerpFunding
            funding_val = Decimal(ctx["funding"])
            mark_price = Decimal(ctx["markPx"]) if ctx.get("markPx") else None
            existing = db.query(PerpFunding).filter(
                PerpFunding.exchange == self.exchange_id,
                PerpFunding.symbol == symbol,
                PerpFunding.timestamp == timestamp_ms,
            ).first()
            if not existing:
                db.add(PerpFunding(
                    exchange=self.exchange_id, symbol=symbol, timestamp=timestamp_ms,
                    funding_rate=funding_val, mark_price=mark_price,
                ))
        except Exception as e:
            logger.debug("[hyperliquid] perp_funding 跳过: %s", e)
