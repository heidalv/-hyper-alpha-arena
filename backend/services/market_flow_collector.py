"""
Market Flow Data Collector Service

Collects real-time market flow data from Hyperliquid using native SDK WebSocket:
- Trades (for CVD, Taker Volume)
- L2 Orderbook (for Depth Ratio, Liquidity)
- Asset Context (for OI, Funding Rate, Premium)

Data is aggregated in 15-second windows and persisted to database.
"""

import json
import time
import logging
import threading
from decimal import Decimal
from typing import Dict, List, Optional, Any
from collections import defaultdict
from dataclasses import dataclass, field

from hyperliquid.info import Info

logger = logging.getLogger(__name__)

# 本采集器的数据源固定为 Hyperliquid WebSocket
# 如果用户主要使用 Binance 交易，此处采集的市场流数据仍来自 Hyperliquid
# 未来需要实现 BinanceFlowCollector 来支持 Binance 的市场流数据
FLOW_DATA_SOURCE = "hyperliquid"

# Aggregation window in seconds (increased from 15 to 60 to reduce SQLite write contention)
AGGREGATION_WINDOW_SECONDS = 60

# Connection health check settings
HEALTH_CHECK_INTERVAL_SECONDS = 30
DATA_STALE_THRESHOLD_SECONDS = 30  # Consider data stale if no update for 30s
MAX_RECONNECT_ATTEMPTS = 5
RECONNECT_BASE_DELAY_SECONDS = 5

# Degraded mode settings (infinite retry with longer intervals)
DEGRADED_MODE_RETRY_INTERVAL_SECONDS = 120  # 2 minutes between retries
DEGRADED_MODE_LOG_INTERVAL = 5  # Log warning every 5 failed attempts


@dataclass
class TradeBuffer:
    """Buffer for aggregating trades within a time window"""
    taker_buy_volume: Decimal = Decimal("0")
    taker_sell_volume: Decimal = Decimal("0")
    taker_buy_count: int = 0
    taker_sell_count: int = 0
    taker_buy_notional: Decimal = Decimal("0")
    taker_sell_notional: Decimal = Decimal("0")
    high_price: Optional[Decimal] = None
    low_price: Optional[Decimal] = None
    total_volume: Decimal = Decimal("0")
    total_notional: Decimal = Decimal("0")

    def reset(self):
        """Reset buffer for next window"""
        self.taker_buy_volume = Decimal("0")
        self.taker_sell_volume = Decimal("0")
        self.taker_buy_count = 0
        self.taker_sell_count = 0
        self.taker_buy_notional = Decimal("0")
        self.taker_sell_notional = Decimal("0")
        self.high_price = None
        self.low_price = None
        self.total_volume = Decimal("0")
        self.total_notional = Decimal("0")


class MarketFlowCollector:
    """
    Singleton service for collecting market flow data via WebSocket.
    Aggregates data in 15-second windows and persists to database.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self.info: Optional[Info] = None
        self.running = False
        self.subscribed_symbols: List[str] = []
        self.subscription_ids: Dict[str, Dict[str, int]] = defaultdict(dict)

        # Data buffers
        self.trade_buffers: Dict[str, TradeBuffer] = {}
        self.latest_orderbook: Dict[str, Any] = {}
        self.latest_asset_ctx: Dict[str, Any] = {}

        # Data freshness tracking (timestamp of last update for each data source)
        self.last_update_time: Dict[str, float] = {
            "l2book": 0.0,
            "asset_ctx": 0.0,
            "trades": 0.0,
        }

        # Timing
        self.last_flush_time = time.time()
        self.flush_timer: Optional[threading.Timer] = None
        self.health_check_timer: Optional[threading.Timer] = None

        # Reconnection state
        self.reconnect_attempts = 0
        self.is_reconnecting = False
        self.reconnect_lock = threading.Lock()

        # Degraded mode state (infinite retry when normal retries exhausted)
        self.degraded_mode = False
        self.degraded_retry_count = 0
        self.degraded_retry_timer: Optional[threading.Timer] = None

        # Thread safety
        self.buffer_lock = threading.Lock()

        logger.info("MarketFlowCollector initialized")

    def start(self, symbols: Optional[List[str]] = None):
        """Start the collector with given symbols or from watchlist"""
        if self.running:
            logger.warning("MarketFlowCollector already running")
            return

        # Get symbols from watchlist if not provided
        if symbols is None:
            from services.hyperliquid_symbol_service import get_selected_symbols
            symbols = get_selected_symbols()

        if not symbols:
            try:
                from backend.database.connection import SessionLocal
                from sqlalchemy import text

                db = SessionLocal()
                try:
                    result = db.execute(text("SELECT symbols FROM signal_pools WHERE enabled = true"))
                    enabled_pool_symbols: List[str] = []
                    has_multi_marker = False

                    for (raw_symbols,) in result.fetchall():
                        if isinstance(raw_symbols, str):
                            try:
                                parsed = json.loads(raw_symbols)
                            except json.JSONDecodeError:
                                parsed = []
                        else:
                            parsed = raw_symbols
                        if isinstance(parsed, list):
                            # Check if pool uses MULTI marker
                            if "MULTI" in parsed or "*" in parsed or "ALL" in parsed:
                                has_multi_marker = True
                            enabled_pool_symbols.extend([s for s in parsed if isinstance(s, str) and s.strip()])

                    # 🔥 FIX: If any pool uses MULTI, load symbols from ENABLED exchanges only
                    if has_multi_marker:
                        logger.info("Signal pool uses MULTI marker, loading symbols from enabled exchanges...")
                        try:
                            from database.models import Account

                            # Reuse outer db session instead of opening a new one
                            try:
                                # Check which exchanges are enabled
                                binance_enabled = db.query(Account).filter(
                                    Account.binance_enabled == "true",
                                    Account.is_active == "true"
                                ).first() is not None

                                hyperliquid_enabled = db.query(Account).filter(
                                    Account.hyperliquid_enabled == "true",
                                    Account.is_active == "true"
                                ).first() is not None

                                symbols = []

                                # Binance removed (Phase 1)

                                # Only load Hyperliquid symbols if Hyperliquid is enabled
                                if hyperliquid_enabled:
                                    from services.hyperliquid_symbol_service import get_selected_symbols as get_hyperliquid_symbols
                                    hyperliquid_symbols = get_hyperliquid_symbols()
                                    symbols.extend(hyperliquid_symbols)
                                    logger.info(f"Loaded Hyperliquid symbols: {hyperliquid_symbols}")

                                # Remove duplicates and sort
                                symbols = sorted(set(symbols))
                                logger.info(f"Total symbols to monitor: {symbols}")

                            finally:
                                pass  # outer db.close() handles cleanup
                        except Exception as e:
                            logger.warning(f"Failed to load symbols from enabled exchanges: {e}")
                            # Fallback to pool symbols (excluding MULTI)
                            excluded = {"MULTI", "*", "ALL"}
                            symbols = sorted({s.strip().upper() for s in enabled_pool_symbols if s.strip().upper() not in excluded})
                    else:
                        # No MULTI marker, use pool symbols directly
                        excluded = {"MULTI", "*", "ALL"}
                        symbols = sorted({s.strip().upper() for s in enabled_pool_symbols if s.strip().upper() not in excluded})
                finally:
                    db.close()
            except Exception as e:
                logger.warning(f"Failed to load enabled pool symbols for MarketFlowCollector: {e}")

        if not symbols:
            try:
                from services.trading_commands import AI_TRADING_SYMBOLS
                symbols = list(AI_TRADING_SYMBOLS)
                logger.warning(f"Using hardcoded AI_TRADING_SYMBOLS fallback: {symbols}")
            except Exception as e:
                logger.warning(f"Failed to load default symbols for MarketFlowCollector: {e}")

        if not symbols:
            logger.warning("No symbols to monitor, collector not started")
            return

        # Store symbols for retry
        self._pending_symbols = symbols
        self.reconnect_attempts = 0

        # Try to start with retry logic
        self._start_with_retry()

    def _start_with_retry(self):
        """Internal method to start collector with retry on failure"""
        symbols = getattr(self, '_pending_symbols', None)
        if not symbols:
            logger.warning("No pending symbols for retry")
            return

        try:
            base_url = "https://api.hyperliquid.xyz"
            logger.info(f"[Start] Connecting to Hyperliquid API: {base_url}")
            try:
                self.info = Info(base_url=base_url, skip_ws=False)
            except IndexError as _idx_err:
                raise ConnectionError(
                    f"Hyperliquid Info 初始化失败 (API 可能暂时不可用): {_idx_err}"
                ) from _idx_err

            self.running = True
            self.subscribed_symbols = []
            self.reconnect_attempts = 0

            for symbol in symbols:
                self._subscribe_symbol(symbol)

            self._schedule_flush()
            self._schedule_health_check()

            logger.info(f"MarketFlowCollector started with symbols: {symbols}")

        except Exception as e:
            _log_level = logging.WARNING if self.reconnect_attempts > 0 else logging.ERROR
            logger.log(_log_level, f"Failed to start MarketFlowCollector (attempt %d): %s",
                       self.reconnect_attempts + 1, e)
            if self.reconnect_attempts == 0:
                logger.error(f"Failed to start MarketFlowCollector(首次): {e}", exc_info=True)
            self.running = False

            self.reconnect_attempts += 1
            if self.reconnect_attempts <= MAX_RECONNECT_ATTEMPTS:
                delay = RECONNECT_BASE_DELAY_SECONDS * (2 ** (self.reconnect_attempts - 1))
                logger.warning(
                    f"[Start] Will retry in {delay}s "
                    f"(attempt {self.reconnect_attempts}/{MAX_RECONNECT_ATTEMPTS})"
                )
                retry_timer = threading.Timer(delay, self._start_with_retry)
                retry_timer.daemon = True
                retry_timer.start()
            else:
                logger.error(
                    f"[Start] FAILED after {MAX_RECONNECT_ATTEMPTS} attempts. "
                    f"Manual restart required!"
                )

    def stop(self):
        """Stop the collector and cleanup"""
        if not self.running:
            return

        self.running = False

        # Cancel flush timer
        if self.flush_timer:
            self.flush_timer.cancel()
            self.flush_timer = None

        # Cancel health check timer
        if self.health_check_timer:
            self.health_check_timer.cancel()
            self.health_check_timer = None

        # Cancel degraded mode retry timer
        if self.degraded_retry_timer:
            self.degraded_retry_timer.cancel()
            self.degraded_retry_timer = None

        # Reset degraded mode state
        self.degraded_mode = False
        self.degraded_retry_count = 0

        # Flush remaining data
        self._flush_to_database()

        # Unsubscribe all
        for symbol in list(self.subscribed_symbols):
            self._unsubscribe_symbol(symbol)

        # Disconnect WebSocket
        if self.info and self.info.ws_manager:
            try:
                self.info.disconnect_websocket()
            except Exception as e:
                logger.warning(f"Error disconnecting websocket: {e}")

        self.info = None
        logger.info("MarketFlowCollector stopped")

    def refresh_subscriptions(self, new_symbols: List[str]):
        """Update subscriptions when watchlist changes"""
        if not self.running:
            return

        current = set(self.subscribed_symbols)
        new = set(new_symbols)

        # Unsubscribe removed symbols
        for symbol in current - new:
            self._unsubscribe_symbol(symbol)

        # Subscribe new symbols
        for symbol in new - current:
            self._subscribe_symbol(symbol)

    def _subscribe_symbol(self, symbol: str):
        """Subscribe to all data streams for a symbol"""
        if not self.info:
            return

        try:
            # Initialize buffer
            self.trade_buffers[symbol] = TradeBuffer()

            # Subscribe to trades
            trades_id = self.info.subscribe(
                {"type": "trades", "coin": symbol},
                lambda msg, s=symbol: self._on_trades(s, msg)
            )
            self.subscription_ids[symbol]["trades"] = trades_id

            # Subscribe to L2 orderbook
            l2_id = self.info.subscribe(
                {"type": "l2Book", "coin": symbol},
                lambda msg, s=symbol: self._on_l2book(s, msg)
            )
            self.subscription_ids[symbol]["l2Book"] = l2_id

            # Subscribe to asset context (OI, funding, etc.)
            ctx_id = self.info.subscribe(
                {"type": "activeAssetCtx", "coin": symbol},
                lambda msg, s=symbol: self._on_asset_ctx(s, msg)
            )
            self.subscription_ids[symbol]["activeAssetCtx"] = ctx_id

            self.subscribed_symbols.append(symbol)
            logger.info(f"Subscribed to market flow data for {symbol}")

        except Exception as e:
            logger.error(f"Failed to subscribe {symbol}: {e}")

    def _unsubscribe_symbol(self, symbol: str):
        """Unsubscribe from all data streams for a symbol"""
        if not self.info or symbol not in self.subscription_ids:
            return

        try:
            ids = self.subscription_ids[symbol]

            if "trades" in ids:
                self.info.unsubscribe({"type": "trades", "coin": symbol}, ids["trades"])
            if "l2Book" in ids:
                self.info.unsubscribe({"type": "l2Book", "coin": symbol}, ids["l2Book"])
            if "activeAssetCtx" in ids:
                self.info.unsubscribe({"type": "activeAssetCtx", "coin": symbol}, ids["activeAssetCtx"])

            del self.subscription_ids[symbol]
            if symbol in self.subscribed_symbols:
                self.subscribed_symbols.remove(symbol)
            if symbol in self.trade_buffers:
                del self.trade_buffers[symbol]

            logger.info(f"Unsubscribed from {symbol}")

        except Exception as e:
            logger.error(f"Failed to unsubscribe {symbol}: {e}")

    def _on_trades(self, symbol: str, msg: dict):
        """Handle incoming trade messages"""
        try:
            if msg.get("channel") != "trades":
                return

            trades = msg.get("data", [])
            if not trades:
                return

            # Update freshness timestamp
            self.last_update_time["trades"] = time.time()

            with self.buffer_lock:
                buffer = self.trade_buffers.get(symbol)
                if not buffer:
                    return

                for trade in trades:
                    # SDK returns: coin, side (A=ask/sell, B=bid/buy), px, sz, hash, time
                    price = Decimal(str(trade["px"]))
                    size = Decimal(str(trade["sz"]))
                    side = trade["side"]  # "A" = taker sell, "B" = taker buy
                    notional = price * size

                    # Update buffer
                    if side == "B":  # Taker buy
                        buffer.taker_buy_volume += size
                        buffer.taker_buy_count += 1
                        buffer.taker_buy_notional += notional
                    else:  # Taker sell (side == "A")
                        buffer.taker_sell_volume += size
                        buffer.taker_sell_count += 1
                        buffer.taker_sell_notional += notional

                    buffer.total_volume += size
                    buffer.total_notional += notional

                    # Track high/low
                    if buffer.high_price is None or price > buffer.high_price:
                        buffer.high_price = price
                    if buffer.low_price is None or price < buffer.low_price:
                        buffer.low_price = price

            try:
                from backend.services.market_data_hub import market_data_hub
                for trade in trades:
                    market_data_hub.publish_trade(
                        "hyperliquid", symbol, trade, source="ws"
                    )
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Error processing trades for {symbol}: {e}")

    def _on_l2book(self, symbol: str, msg: dict):
        """Handle incoming L2 orderbook messages"""
        try:
            if msg.get("channel") != "l2Book":
                return

            data = msg.get("data", {})
            if data:
                self.latest_orderbook[symbol] = data
                # [v6-S2-1] L2 重建层接线：把 HL l2Book 快照喂给默认重建器
                # （跳变防护 + 深度派生），flush 时取末帧前5档名义深度落库。
                try:
                    if data.get("levels"):
                        from services.market_flow.l2_reconstructor import default_reconstructor
                        default_reconstructor.ingest_hl(
                            FLOW_DATA_SOURCE, symbol,
                            [data["levels"][0] or [], data["levels"][1] or []],
                        )
                except Exception as _rec_err:
                    logger.warning(f"[S2-1] L2Reconstructor ingest 跳过 {symbol}: {_rec_err}")
                # Update freshness timestamp
                self.last_update_time["l2book"] = time.time()
                # Phase 4: 推送至 MarketDataHub + 跨所 mid 缓存
                try:
                    from backend.services.arbitrage.cross_exchange_ws_feed import (
                        push_hyperliquid_l2book,
                    )
                    push_hyperliquid_l2book(symbol, data)
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"Error processing l2book for {symbol}: {e}")

    def _on_asset_ctx(self, symbol: str, msg: dict):
        """Handle incoming asset context messages"""
        try:
            channel = msg.get("channel")
            if channel not in ("activeAssetCtx", "activeSpotAssetCtx"):
                return

            data = msg.get("data", {})
            if data:
                self.latest_asset_ctx[symbol] = data
                # Update freshness timestamp
                self.last_update_time["asset_ctx"] = time.time()
                try:
                    from backend.services.market_data_hub import market_data_hub
                    market_data_hub.publish_asset_ctx(
                        "hyperliquid", symbol, data, source="ws"
                    )
                    ctx = data.get("ctx", data)
                    funding = ctx.get("funding") if isinstance(ctx, dict) else None
                    if funding is not None:
                        market_data_hub.publish_funding(
                            "hyperliquid",
                            symbol,
                            {"rate": float(funding)},
                            source="ws",
                        )
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"Error processing asset ctx for {symbol}: {e}")

    def _schedule_health_check(self):
        """Schedule next health check"""
        if not self.running:
            return
        self.health_check_timer = threading.Timer(
            HEALTH_CHECK_INTERVAL_SECONDS, self._health_check_and_reschedule
        )
        self.health_check_timer.daemon = True
        self.health_check_timer.start()

    def _health_check_and_reschedule(self):
        """Check connection health and schedule next check"""
        if not self.running:
            return
        self._check_connection_health()
        self._schedule_health_check()

    def _check_connection_health(self):
        """Check if WebSocket data is stale and trigger reconnect if needed"""
        if self.is_reconnecting:
            logger.debug("Health check skipped - reconnection in progress")
            return

        # In degraded mode, reconnection is handled by degraded_retry_timer
        if self.degraded_mode:
            logger.debug("Health check skipped - in degraded mode (timer-controlled retry)")
            return

        now = time.time()
        # Check l2book and asset_ctx freshness (these should update frequently)
        l2book_age = now - self.last_update_time["l2book"] if self.last_update_time["l2book"] > 0 else -1
        asset_ctx_age = now - self.last_update_time["asset_ctx"] if self.last_update_time["asset_ctx"] > 0 else -1
        trades_age = now - self.last_update_time["trades"] if self.last_update_time["trades"] > 0 else -1

        # Log current health status
        logger.info(
            f"[HealthCheck] Data freshness - l2book: {l2book_age:.0f}s, "
            f"asset_ctx: {asset_ctx_age:.0f}s, trades: {trades_age:.0f}s "
            f"(threshold: {DATA_STALE_THRESHOLD_SECONDS}s)"
        )

        # If both are stale, connection is likely dead
        if (self.last_update_time["l2book"] > 0 and l2book_age > DATA_STALE_THRESHOLD_SECONDS and
            self.last_update_time["asset_ctx"] > 0 and asset_ctx_age > DATA_STALE_THRESHOLD_SECONDS):
            logger.warning(
                f"[HealthCheck] STALE DATA DETECTED! WebSocket likely disconnected. "
                f"l2book: {l2book_age:.0f}s ago, asset_ctx: {asset_ctx_age:.0f}s ago. "
                f"Initiating reconnect..."
            )
            self._reconnect()

    def _reconnect(self):
        """Reconnect WebSocket with exponential backoff, then degraded mode"""
        with self.reconnect_lock:
            if self.is_reconnecting:
                logger.debug("[Reconnect] Already reconnecting, skipping")
                return
            self.is_reconnecting = True

        try:
            # Check if we should enter or continue degraded mode
            if self.reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
                if not self.degraded_mode:
                    # First time entering degraded mode
                    self.degraded_mode = True
                    self.degraded_retry_count = 0
                    logger.warning(
                        f"[Reconnect] Normal retries exhausted ({MAX_RECONNECT_ATTEMPTS}). "
                        f"Entering DEGRADED MODE - will retry every "
                        f"{DEGRADED_MODE_RETRY_INTERVAL_SECONDS}s indefinitely."
                    )

                self.degraded_retry_count += 1
                # Log every DEGRADED_MODE_LOG_INTERVAL attempts
                if self.degraded_retry_count % DEGRADED_MODE_LOG_INTERVAL == 1:
                    logger.warning(
                        f"[Reconnect] DEGRADED MODE attempt #{self.degraded_retry_count} "
                        f"(logging every {DEGRADED_MODE_LOG_INTERVAL} attempts)"
                    )
            else:
                # Normal mode with exponential backoff
                self.reconnect_attempts += 1
                delay = RECONNECT_BASE_DELAY_SECONDS * (2 ** (self.reconnect_attempts - 1))
                logger.warning(
                    f"[Reconnect] Attempt {self.reconnect_attempts}/{MAX_RECONNECT_ATTEMPTS} "
                    f"starting after {delay}s delay..."
                )
                time.sleep(delay)

            # Save current symbols (use _pending_symbols as fallback)
            symbols_to_restore = list(self.subscribed_symbols) if self.subscribed_symbols else \
                                 getattr(self, '_pending_symbols', [])
            logger.info(f"[Reconnect] Will restore {len(symbols_to_restore)} symbols")

            # Disconnect old WebSocket and clean up
            self._cleanup_old_connection()

            # Create new Info client
            logger.info("[Reconnect] Creating new Hyperliquid Info client...")
            base_url = "https://api.hyperliquid.xyz"
            self.info = Info(base_url=base_url, skip_ws=False)
            logger.info("[Reconnect] New Info client created")

            # Resubscribe to all symbols
            for symbol in symbols_to_restore:
                self._subscribe_symbol(symbol)

            # SUCCESS - reset all reconnection state
            self.reconnect_attempts = 0
            self.degraded_mode = False
            self.degraded_retry_count = 0
            now = time.time()
            self.last_update_time["l2book"] = now
            self.last_update_time["asset_ctx"] = now
            self.last_update_time["trades"] = now
            logger.warning(
                f"[Reconnect] SUCCESS! Resubscribed to {len(symbols_to_restore)} symbols. "
                f"Data collection resumed."
            )

        except Exception as e:
            logger.error(f"Reconnect failed: {e}", exc_info=True)
            # Ensure info is None on failure to avoid using corrupted object
            self.info = None
            # Schedule next retry in degraded mode
            if self.degraded_mode:
                self._schedule_degraded_retry()
        finally:
            self.is_reconnecting = False

    def _cleanup_old_connection(self):
        """Clean up old WebSocket connection and subscription state"""
        if self.info and self.info.ws_manager:
            try:
                self.info.disconnect_websocket()
                logger.info("[Reconnect] Old WebSocket disconnected")
            except Exception as e:
                logger.warning(f"[Reconnect] Error disconnecting old websocket: {e}")
        self.info = None
        self.subscribed_symbols = []
        self.subscription_ids.clear()

    def _schedule_degraded_retry(self):
        """Schedule next reconnection attempt in degraded mode"""
        if not self.running:
            return
        self.degraded_retry_timer = threading.Timer(
            DEGRADED_MODE_RETRY_INTERVAL_SECONDS,
            self._reconnect
        )
        self.degraded_retry_timer.daemon = True
        self.degraded_retry_timer.start()
        logger.debug(
            f"[Reconnect] Degraded mode retry scheduled in "
            f"{DEGRADED_MODE_RETRY_INTERVAL_SECONDS}s"
        )

    def _schedule_flush(self):
        """Schedule next flush"""
        if not self.running:
            return
        self.flush_timer = threading.Timer(AGGREGATION_WINDOW_SECONDS, self._flush_and_reschedule)
        self.flush_timer.daemon = True
        self.flush_timer.start()

    def _flush_and_reschedule(self):
        """Flush data and schedule next flush"""
        if not self.running:
            return
        self._flush_to_database()
        self._schedule_flush()

    def _flush_to_database(self):
        """Flush all buffered data to database. Each symbol gets its own short
        transaction. Write serialization handled globally by connection.py session events."""
        if not self.subscribed_symbols:
            return

        timestamp_ms = int(time.time() * 1000)
        timestamp_ms = (timestamp_ms // (AGGREGATION_WINDOW_SECONDS * 1000)) * (AGGREGATION_WINDOW_SECONDS * 1000)

        flushed = 0
        from backend.database.connection import MarketSessionLocal
        for symbol in list(self.subscribed_symbols):
            db = MarketSessionLocal()
            try:
                self._flush_trades(db, symbol, timestamp_ms)
                self._flush_orderbook(db, symbol, timestamp_ms)
                self._flush_asset_metrics(db, symbol, timestamp_ms)
                db.commit()
                flushed += 1
            except Exception as e:
                db.rollback()
                err_s = str(e).lower()
                if "unique" in err_s or "duplicate" in err_s:
                    # 同一时间窗口被重复采集，数据已存在，忽略即可
                    logger.debug(f"Market flow duplicate skip for {symbol}: {e}")
                else:
                    logger.error(f"Failed to flush market flow for {symbol}: {e}")
            finally:
                db.close()

        if flushed > 0:
            logger.debug(f"Flushed market flow data for {flushed}/{len(self.subscribed_symbols)} symbols")
            self._run_signal_detection()

    def _run_signal_detection(self):
        """Run signal detection for all subscribed symbols"""
        try:
            from services.signal_detection_service import signal_detection_service

            for symbol in self.subscribed_symbols:
                # Build market data context for signal detection
                market_data = {
                    "asset_ctx": self.latest_asset_ctx.get(symbol, {}),
                    "orderbook": self.latest_orderbook.get(symbol, {}),
                }

                # Detect signals (returns pool triggers now)
                triggered = signal_detection_service.detect_signals(symbol, market_data)
                if triggered:
                    logger.info(f"Pools triggered for {symbol}: {[p['pool_name'] for p in triggered]}")

        except Exception as e:
            logger.error(f"Error in signal detection: {e}", exc_info=True)

    def _current_depth_notional(self, symbol: str):
        """[v6-S2-1] 取当前末帧前5档名义深度 (bid, ask)。

        主来源：实例属性 self.latest_orderbook —— _on_l2book 与 flush 位于同一
        采集器实例内共享，天然规避"模块双加载导致重建器单例不一致"（2026-08-06
        实测 services.* 与 backend.services.* 可被加载成两个不同模块实例）；
        L2 重建器仅作辅助来源。无订单簿帧或单边为空时返回 (None, None)。
        """
        # ── 主来源：实例订单簿（HL levels / bids-asks 双格式兼容）──
        try:
            book = self.latest_orderbook.get(symbol)
            if isinstance(book, dict):
                bids_raw = asks_raw = None
                if book.get("levels"):
                    try:
                        bids_raw, asks_raw = book["levels"][0] or [], book["levels"][1] or []
                    except (TypeError, IndexError, ValueError):
                        bids_raw = asks_raw = None
                elif book.get("bids") or book.get("asks"):
                    bids_raw, asks_raw = book.get("bids"), book.get("asks")

                if bids_raw is not None:
                    def _notional(rows) -> float:
                        total = 0.0
                        for r in (rows or [])[:5]:
                            try:
                                if isinstance(r, dict):
                                    total += float(r["px"]) * float(r["sz"])
                                else:
                                    total += float(r[0]) * float(r[1])
                            except (TypeError, ValueError, IndexError, KeyError):
                                continue
                        return total

                    bid_d, ask_d = _notional(bids_raw), _notional(asks_raw)
                    if bid_d > 0 and ask_d > 0:
                        return float(bid_d), float(ask_d)
        except Exception as e:
            logger.warning(f"[S2-1] 深度列读取(实例订单簿)失败 {symbol}: {e}")
        # ── 辅助来源：L2 重建器 ──
        try:
            from services.market_flow.l2_reconstructor import default_reconstructor
            frame = default_reconstructor.latest(FLOW_DATA_SOURCE, symbol)
            if frame is None or not frame.bids or not frame.asks:
                return None, None
            bid_d, ask_d = frame.notional_depth(5)
            return float(bid_d), float(ask_d)
        except Exception as e:
            logger.warning(f"[S2-1] 深度列读取(重建器)失败 {symbol}: {e}")
            return None, None

    def _flush_trades(self, db, symbol: str, timestamp_ms: int):
        """Flush trade buffer for a symbol"""
        from database.models import MarketTradesAggregated

        # [v6-S2-1] 当前末帧前5档名义深度（无帧时 None，列保持 NULL）
        bid_depth_top5, ask_depth_top5 = self._current_depth_notional(symbol)

        with self.buffer_lock:
            buffer = self.trade_buffers.get(symbol)
            if not buffer or buffer.total_volume == 0:
                return

            # Calculate VWAP
            vwap = None
            if buffer.total_volume > 0:
                vwap = buffer.total_notional / buffer.total_volume

            # Upsert: check if record exists, update or insert
            existing = db.query(MarketTradesAggregated).filter(
                MarketTradesAggregated.exchange == FLOW_DATA_SOURCE,
                MarketTradesAggregated.symbol == symbol,
                MarketTradesAggregated.timestamp == timestamp_ms
            ).first()

            if existing:
                existing.taker_buy_volume = buffer.taker_buy_volume
                existing.taker_sell_volume = buffer.taker_sell_volume
                existing.taker_buy_count = buffer.taker_buy_count
                existing.taker_sell_count = buffer.taker_sell_count
                existing.taker_buy_notional = buffer.taker_buy_notional
                existing.taker_sell_notional = buffer.taker_sell_notional
                existing.vwap = vwap
                existing.high_price = buffer.high_price
                existing.low_price = buffer.low_price
                existing.bid_depth_top5 = bid_depth_top5
                existing.ask_depth_top5 = ask_depth_top5
            else:
                record = MarketTradesAggregated(
                    exchange=FLOW_DATA_SOURCE,
                    symbol=symbol,
                    timestamp=timestamp_ms,
                    taker_buy_volume=buffer.taker_buy_volume,
                    taker_sell_volume=buffer.taker_sell_volume,
                    taker_buy_count=buffer.taker_buy_count,
                    taker_sell_count=buffer.taker_sell_count,
                    taker_buy_notional=buffer.taker_buy_notional,
                    taker_sell_notional=buffer.taker_sell_notional,
                    vwap=vwap,
                    high_price=buffer.high_price,
                    low_price=buffer.low_price,
                    bid_depth_top5=bid_depth_top5,
                    ask_depth_top5=ask_depth_top5,
                )
                db.add(record)

            buffer.reset()

    def _flush_orderbook(self, db, symbol: str, timestamp_ms: int):
        """Flush orderbook snapshot for a symbol"""
        from database.models import MarketOrderbookSnapshots

        # Skip if data is stale (WebSocket disconnected)
        l2book_age = time.time() - self.last_update_time["l2book"]
        if self.last_update_time["l2book"] > 0 and l2book_age > DATA_STALE_THRESHOLD_SECONDS:
            logger.warning(f"[StaleData] Skipping orderbook flush for {symbol} - data is {l2book_age:.0f}s old")
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

            # Calculate depth for top 5 and 10 levels
            bid_depth_5 = sum(Decimal(b["sz"]) for b in bids[:5])
            ask_depth_5 = sum(Decimal(a["sz"]) for a in asks[:5])
            bid_depth_10 = sum(Decimal(b["sz"]) for b in bids[:10])
            ask_depth_10 = sum(Decimal(a["sz"]) for a in asks[:10])

            # Count orders
            bid_orders = sum(b.get("n", 1) for b in bids)
            ask_orders = sum(a.get("n", 1) for a in asks)

            # Upsert: check if record exists, update or insert
            existing = db.query(MarketOrderbookSnapshots).filter(
                MarketOrderbookSnapshots.exchange == FLOW_DATA_SOURCE,
                MarketOrderbookSnapshots.symbol == symbol,
                MarketOrderbookSnapshots.timestamp == timestamp_ms
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
                record = MarketOrderbookSnapshots(
                    exchange=FLOW_DATA_SOURCE,
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
                    raw_levels=json.dumps(levels),
                )
                db.add(record)

        except Exception as e:
            logger.error(f"Error flushing orderbook for {symbol}: {e}")

    def _flush_asset_metrics(self, db, symbol: str, timestamp_ms: int):
        """Flush asset metrics for a symbol"""
        from database.models import MarketAssetMetrics

        # Skip if data is stale (WebSocket disconnected)
        asset_ctx_age = time.time() - self.last_update_time["asset_ctx"]
        if self.last_update_time["asset_ctx"] > 0 and asset_ctx_age > DATA_STALE_THRESHOLD_SECONDS:
            logger.warning(f"[StaleData] Skipping asset metrics flush for {symbol} - data is {asset_ctx_age:.0f}s old")
            return

        data = self.latest_asset_ctx.get(symbol)
        if not data:
            return

        try:
            ctx = data.get("ctx", {})

            # Upsert: check if record exists, update or insert
            existing = db.query(MarketAssetMetrics).filter(
                MarketAssetMetrics.exchange == FLOW_DATA_SOURCE,
                MarketAssetMetrics.symbol == symbol,
                MarketAssetMetrics.timestamp == timestamp_ms
            ).first()

            if existing:
                existing.open_interest = Decimal(ctx["openInterest"]) if ctx.get("openInterest") else None
                existing.funding_rate = Decimal(ctx["funding"]) if ctx.get("funding") else None
                existing.mark_price = Decimal(ctx["markPx"]) if ctx.get("markPx") else None
                existing.oracle_price = Decimal(ctx["oraclePx"]) if ctx.get("oraclePx") else None
                existing.mid_price = Decimal(ctx["midPx"]) if ctx.get("midPx") else None
                existing.premium = Decimal(ctx["premium"]) if ctx.get("premium") else None
                existing.day_notional_volume = Decimal(ctx["dayNtlVlm"]) if ctx.get("dayNtlVlm") else None
            else:
                record = MarketAssetMetrics(
                    exchange=FLOW_DATA_SOURCE,
                    symbol=symbol,
                    timestamp=timestamp_ms,
                    open_interest=Decimal(ctx["openInterest"]) if ctx.get("openInterest") else None,
                    funding_rate=Decimal(ctx["funding"]) if ctx.get("funding") else None,
                    mark_price=Decimal(ctx["markPx"]) if ctx.get("markPx") else None,
                    oracle_price=Decimal(ctx["oraclePx"]) if ctx.get("oraclePx") else None,
                    mid_price=Decimal(ctx["midPx"]) if ctx.get("midPx") else None,
                    premium=Decimal(ctx["premium"]) if ctx.get("premium") else None,
                    day_notional_volume=Decimal(ctx["dayNtlVlm"]) if ctx.get("dayNtlVlm") else None,
                )
                db.add(record)

            # 同时写入 perp_funding 历史表（每条 metrics 里带 funding 就记一笔）
            if ctx.get("funding"):
                self._save_perp_funding(db, symbol, timestamp_ms, ctx)

        except Exception as e:
            logger.error(f"Error flushing asset metrics for {symbol}: {e}")

    def _save_perp_funding(self, db, symbol: str, timestamp_ms: int, ctx: dict):
        """将资金费率写入 perp_funding 历史表"""
        try:
            from database.models import PerpFunding
            funding_val = Decimal(ctx["funding"])
            mark_price = Decimal(ctx["markPx"]) if ctx.get("markPx") else None

            existing = db.query(PerpFunding).filter(
                PerpFunding.exchange == FLOW_DATA_SOURCE,
                PerpFunding.symbol == symbol,
                PerpFunding.timestamp == timestamp_ms,
            ).first()

            if not existing:
                db.add(PerpFunding(
                    exchange=FLOW_DATA_SOURCE,
                    symbol=symbol,
                    timestamp=timestamp_ms,
                    funding_rate=funding_val,
                    mark_price=mark_price,
                ))
        except Exception as e:
            logger.debug(f"[FlowCollector] perp_funding 写入跳过: {e}")


# Singleton instance
market_flow_collector = MarketFlowCollector()


# Data retention settings
DATA_RETENTION_DAYS = 30


def cleanup_old_market_flow_data():
    """
    Delete market flow data older than DATA_RETENTION_DAYS.
    This function is designed to be called by a scheduled task.
    """
    import time
    from backend.database.connection import MarketSessionLocal
    from database.models import (
        MarketTradesAggregated,
        MarketOrderbookSnapshots,
        MarketAssetMetrics,
    )

    cutoff_ms = int((time.time() - DATA_RETENTION_DAYS * 86400) * 1000)

    db = MarketSessionLocal()
    try:
        trades_deleted = (
            db.query(MarketTradesAggregated)
            .filter(MarketTradesAggregated.timestamp < cutoff_ms)
            .delete(synchronize_session=False)
        )

        # Delete old orderbook snapshots
        orderbook_deleted = (
            db.query(MarketOrderbookSnapshots)
            .filter(MarketOrderbookSnapshots.timestamp < cutoff_ms)
            .delete(synchronize_session=False)
        )

        # Delete old asset metrics
        metrics_deleted = (
            db.query(MarketAssetMetrics)
            .filter(MarketAssetMetrics.timestamp < cutoff_ms)
            .delete(synchronize_session=False)
        )

        db.commit()

        total_deleted = trades_deleted + orderbook_deleted + metrics_deleted
        if total_deleted > 0:
            logger.info(
                f"Market flow data cleanup: deleted {trades_deleted} trades, "
                f"{orderbook_deleted} orderbook snapshots, {metrics_deleted} asset metrics "
                f"(older than {DATA_RETENTION_DAYS} days)"
            )
        else:
            logger.debug("Market flow data cleanup: no old records to delete")

    except Exception as e:
        db.rollback()
        logger.error(f"Market flow data cleanup failed: {e}")
    finally:
        db.close()
