"""
Hyperliquid market data service using CCXT
"""
import logging
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

import ccxt

logger = logging.getLogger(__name__)

# ── 模块级 metaAndAssetCtxs 响应缓存 ──────────────────────────────
# 避免 /api/market/prices 对 N 个币种发出 N 次相同请求
# 缓存 TTL = 3 秒（价格变化已足够体现，同时大幅减少网络负载）
_META_CTX_CACHE_TTL = 3.0
_meta_ctx_cache: Dict[str, Tuple[float, list]] = {}  # env -> (timestamp, data)
_meta_ctx_lock = Lock()

# K 线反复失败的 symbol 冷却，避免 24h 刷上千条 ERROR
_kline_fail_until: Dict[str, float] = {}
_kline_fail_lock = Lock()
_KLINE_FAIL_COOLDOWN_S = 3600.0

# ccxt load_markets patch 日志降噪（每进程只 INFO 一次）
_ccxt_patch_logged = False

# 全局 Hyperliquid API 限速（约 120 req/min）
_HL_RATE_LIMIT_PER_MIN = 120
_hl_rate_tokens = float(_HL_RATE_LIMIT_PER_MIN)
_hl_rate_last_refill = time.time()
_hl_rate_lock = Lock()


def _hl_rate_limit_wait() -> None:
    """简单令牌桶：全进程共享，避免多线程 429 风暴。"""
    global _hl_rate_tokens, _hl_rate_last_refill
    while True:
        with _hl_rate_lock:
            now = time.time()
            elapsed = now - _hl_rate_last_refill
            if elapsed >= 60.0:
                _hl_rate_tokens = min(
                    float(_HL_RATE_LIMIT_PER_MIN),
                    _hl_rate_tokens + elapsed * (_HL_RATE_LIMIT_PER_MIN / 60.0),
                )
                _hl_rate_last_refill = now
            if _hl_rate_tokens >= 1.0:
                _hl_rate_tokens -= 1.0
                return
            need = (1.0 - _hl_rate_tokens) * (60.0 / _HL_RATE_LIMIT_PER_MIN)
        time.sleep(min(max(need, 0.05), 2.0))


def _get_cached_meta_ctx(api_url: str, environment: str) -> Optional[list]:
    """从缓存中获取 metaAndAssetCtxs 数据，超 TTL 则返回 None"""
    with _meta_ctx_lock:
        entry = _meta_ctx_cache.get(environment)
        if entry and (time.time() - entry[0]) < _META_CTX_CACHE_TTL:
            return entry[1]
    return None


def _fetch_and_cache_meta_ctx(api_url: str, environment: str) -> list:
    """请求 metaAndAssetCtxs 并写入缓存（429 重试 sleep 在锁外，避免阻塞全站）"""
    with _meta_ctx_lock:
        entry = _meta_ctx_cache.get(environment)
        if entry and (time.time() - entry[0]) < _META_CTX_CACHE_TTL:
            return entry[1]

    import requests
    data = None
    last_err = None
    for attempt in range(3):
        _hl_rate_limit_wait()
        try:
            resp = requests.post(api_url, json={"type": "metaAndAssetCtxs"}, timeout=10)
            if resp.status_code == 429:
                delay = 2.0 * (2 ** attempt)
                logger.warning(f"429 on metaAndAssetCtxs, retry {attempt+1}/3 in {delay:.1f}s")
                time.sleep(delay)
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(1.0 * (2 ** attempt))
                continue
            raise last_err

    if data is None:
        raise RuntimeError(f"metaAndAssetCtxs failed after retries: {last_err}")

    with _meta_ctx_lock:
        entry = _meta_ctx_cache.get(environment)
        if entry and (time.time() - entry[0]) < _META_CTX_CACHE_TTL:
            return entry[1]
        _meta_ctx_cache[environment] = (time.time(), data)
        return data

class HyperliquidClient:
    def __init__(self, environment: str = "mainnet"):
        self.environment = environment
        self.exchange = None
        self._initialize_exchange()

    def _initialize_exchange(self):
        """Initialize CCXT Hyperliquid exchange"""
        try:
            # Dynamic sandbox mode based on environment
            sandbox_mode = self.environment == "testnet"

            self.exchange = ccxt.hyperliquid({
                'sandbox': sandbox_mode,  # Dynamic based on environment
                'enableRateLimit': True,
                'rateLimit': 200,  # 200ms minimum between requests (Hyperliquid default is 120req/min)
                'timeout': 15000,  # [fix] 单次请求最多 15s，避免 Hyperliquid 不响应时永久挂起（曾导致采集循环冻结 18h）
                'options': {
                    'fetchMarkets': {
                        'hip3': {
                            'dex': []  # Empty list to skip HIP3 DEX markets (we only need perp markets)
                        }
                    }
                }
            })
            self._disable_hip3_markets()
            self._patch_ccxt_fetch_spot_markets()
            logger.info(f"Hyperliquid exchange initialized successfully for {self.environment} environment")
        except Exception as e:
            logger.error(f"Failed to initialize Hyperliquid exchange for {self.environment}: {e}")
            raise

    def _patch_ccxt_fetch_spot_markets(self) -> None:
        """Monkey-patch ccxt's load_markets to survive None base/quote tokens.

        ccxt 4.5.11 hyperliquid.py:613: ``symbol = base + '/' + quote``
        crashes when Hyperliquid API returns spot tokens with ``base: None``.

        Strategy: wrap load_markets to catch TypeError, then monkey-patch
        fetch_spot_markets to filter null-base tokens before re-invoking.
        """
        import types as _types

        _original_load = self.exchange.load_markets

        def _safe_load_markets(self_ex, reload=False, params=None):
            global _ccxt_patch_logged
            try:
                return _original_load(reload=reload, params=params or {})
            except TypeError as e:
                if "NoneType" not in str(e) or "str" not in str(e):
                    raise
                if not _ccxt_patch_logged:
                    logger.warning(
                        "[Hyperliquid] ccxt TypeError in load_markets "
                        "(None base/quote bug) — patching fetch_spot_markets"
                    )
                    _ccxt_patch_logged = True
                else:
                    logger.debug(
                        "[Hyperliquid] ccxt load_markets TypeError (patched, suppressed)"
                    )
                # ccxt bug: fetch_spot_markets crashes on null base/quote tokens.
                # Since we only trade perpetuals (not spot), replace it with
                # a stub that returns an empty list. Perpetual markets are loaded
                # separately via fetch_markets → fetch_perpetual_markets path.
                def _safe_spot(inst, params=None):
                    return []

                self_ex.fetch_spot_markets = _types.MethodType(_safe_spot, self_ex)
                if not _ccxt_patch_logged:
                    logger.info(
                        "[Hyperliquid] Patched fetch_spot_markets → empty (spot not needed, "
                        "ccxt v%s has None-base bug)", getattr(self_ex, '__version__', '?')
                    )
                return _original_load(reload=reload, params=params or {})

        self.exchange.load_markets = _types.MethodType(
            _safe_load_markets, self.exchange
        )
        logger.debug("[Hyperliquid] ccxt TypeError workaround installed")

    def _disable_hip3_markets(self) -> None:
        """Ensure HIP3 market fetching is disabled."""
        try:
            fetch_markets_options = self.exchange.options.setdefault('fetchMarkets', {})
            hip3_options = fetch_markets_options.setdefault('hip3', {})
            hip3_options['enabled'] = False
            hip3_options['dex'] = []
            # Manually initialize hip3TokensByName to prevent KeyError in coin_to_market_id()
            self.exchange.options.setdefault('hip3TokensByName', {})
        except Exception as options_error:
            logger.debug(f"Unable to update HIP3 fetch options: {options_error}")

        if hasattr(self.exchange, 'fetch_hip3_markets'):
            def _skip_hip3_markets(exchange_self, params=None):
                logger.debug("Skipping HIP3 market fetch in market data client")
                return []
            self.exchange.fetch_hip3_markets = _skip_hip3_markets.__get__(self.exchange, type(self.exchange))
            logger.info("HIP3 market fetch disabled for market data client")

    def get_last_price(self, symbol: str) -> Optional[float]:
        """Get the last price for a symbol

        优先使用 metaAndAssetCtxs API 的 markPx（标记价格，最可靠），
        回退到 CCXT fetch_ticker 的 last 价格。
        """
        try:
            # 优先路径：使用 get_ticker_data 的 markPx（与系统其他地方一致）
            ticker = self.get_ticker_data(symbol)
            if ticker and ticker.get('price') and ticker['price'] > 0:
                return float(ticker['price'])
        except Exception:
            pass  # 回退到 CCXT

        try:
            if not self.exchange:
                self._initialize_exchange()

            # Ensure symbol is in CCXT format (e.g., 'BTC/USD')
            formatted_symbol = self._format_symbol(symbol)

            ticker = self.exchange.fetch_ticker(formatted_symbol)
            price = ticker['last']

            logger.debug(f"Got price for {formatted_symbol}: {price}")
            return float(price) if price else None

        except Exception as e:
            # Use debug level for price fetch failures - often caused by delisted symbols
            # that user hasn't removed from watchlist yet
            logger.debug(f"Failed to fetch price for {symbol}: {e}")
            return None

    def _get_api_url(self) -> str:
        """Return environment-specific Hyperliquid API URL"""
        if self.environment == "testnet":
            return "https://api.hyperliquid-testnet.xyz/info"
        return "https://api.hyperliquid.xyz/info"

    def _extract_ticker_from_meta(self, data: list, symbol: str) -> Optional[Dict[str, Any]]:
        """从 metaAndAssetCtxs 响应中提取单个 symbol 的 ticker 数据"""
        symbol_upper = symbol.upper()
        symbol_index = None

        if isinstance(data[0], dict) and 'universe' in data[0]:
            for i, asset_meta in enumerate(data[0]['universe']):
                if isinstance(asset_meta, dict):
                    asset_name = asset_meta.get('name', '').upper()
                    if asset_name == symbol_upper or asset_name == symbol_upper.replace('/', ''):
                        symbol_index = i
                        break

        if symbol_index is None or symbol_index >= len(data[1]):
            return None

        asset_data = data[1][symbol_index]
        if not isinstance(asset_data, dict):
            return None

        mark_px = float(asset_data.get('markPx', 0))
        oracle_px = float(asset_data.get('oraclePx', 0))
        prev_day_px = float(asset_data.get('prevDayPx', 0))
        day_ntl_vlm = float(asset_data.get('dayNtlVlm', 0))
        open_interest = float(asset_data.get('openInterest', 0))
        funding_rate = float(asset_data.get('funding', 0))

        change_24h = mark_px - prev_day_px if prev_day_px else 0
        percentage_24h = (change_24h / prev_day_px * 100) if prev_day_px else 0
        open_interest_usd = open_interest * mark_px

        return {
            'symbol': symbol,
            'price': mark_px,
            'oracle_price': oracle_px,
            'change24h': change_24h,
            'volume24h': day_ntl_vlm,
            'percentage24h': percentage_24h,
            'open_interest': open_interest_usd,
            'funding_rate': funding_rate,
        }

    def get_ticker_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get complete ticker data - uses module-level cache to avoid repeated API calls"""
        try:
            api_url = self._get_api_url()
            # 优先从缓存读取（3s TTL），避免并发多币种请求重复拉取全量数据
            data = _get_cached_meta_ctx(api_url, self.environment)
            if data is None:
                data = _fetch_and_cache_meta_ctx(api_url, self.environment)

            if not isinstance(data, list) or len(data) < 2:
                raise Exception("Invalid API response structure")

            result = self._extract_ticker_from_meta(data, symbol)
            if result is None:
                return self._get_ccxt_ticker_fallback(symbol)

            logger.debug(f"Got Hyperliquid ticker for {symbol}: price={result['price']}")
            return result

        except Exception as e:
            _err_s = str(e)
            if "502" in _err_s or "Bad Gateway" in _err_s:
                logger.warning(f"Hyperliquid API 502 for {symbol}, using CCXT fallback")
            else:
                logger.error(f"Error fetching Hyperliquid ticker for {symbol}: {e}")
            return self._get_ccxt_ticker_fallback(symbol)

    def get_bulk_ticker_data(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """一次 API 请求批量获取多个 symbol 的 ticker 数据（最高效）"""
        results: Dict[str, Dict[str, Any]] = {}
        try:
            api_url = self._get_api_url()
            data = _get_cached_meta_ctx(api_url, self.environment)
            if data is None:
                data = _fetch_and_cache_meta_ctx(api_url, self.environment)

            if not isinstance(data, list) or len(data) < 2:
                raise Exception("Invalid API response structure")

            for symbol in symbols:
                ticker = self._extract_ticker_from_meta(data, symbol)
                if ticker:
                    results[symbol.upper()] = ticker
                    # 同步更新简单价格缓存，确保 get_positions / get_last_price 取到最新值
                    try:
                        from services.price_cache import cache_price
                        cache_price(symbol.upper(), "CRYPTO", ticker['price'], "mainnet")
                    except Exception:
                        pass
                else:
                    # 对不在 Hyperliquid universe 的 symbol 用 CCXT fallback
                    try:
                        fallback = self._get_ccxt_ticker_fallback(symbol)
                        if fallback:
                            results[symbol.upper()] = fallback
                            try:
                                from services.price_cache import cache_price
                                cache_price(symbol.upper(), "CRYPTO", fallback['price'], "mainnet")
                            except Exception:
                                pass
                    except Exception:
                        pass

        except Exception as e:
            logger.error(f"Bulk ticker fetch failed: {e}")
            # 降级为逐个获取
            for symbol in symbols:
                try:
                    ticker = self.get_ticker_data(symbol)
                    if ticker:
                        results[symbol.upper()] = ticker
                except Exception:
                    pass

        return results

    def _get_ccxt_ticker_fallback(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fallback to CCXT ticker for unsupported symbols"""
        try:
            if not self.exchange:
                self._initialize_exchange()

            formatted_symbol = self._format_symbol(symbol)
            ticker = self.exchange.fetch_ticker(formatted_symbol)

            result = {
                'symbol': symbol,
                'price': float(ticker['last']) if ticker['last'] else 0,
                'change24h': float(ticker['change']) if ticker['change'] else 0,
                'volume24h': float(ticker['baseVolume']) if ticker['baseVolume'] else 0,
                'percentage24h': float(ticker['percentage']) if ticker['percentage'] else 0,
            }
            return result
        except Exception as e:
            logger.error(f"CCXT fallback failed for {symbol}: {e}")
            return None

    def check_symbol_tradability(self, symbol: str) -> bool:
        """
        Check if a symbol is tradable (can fetch price data).

        This method is designed for validation purposes during symbol refresh
        and won't log errors for invalid symbols.

        Returns:
            True if symbol can fetch valid price data, False otherwise
        """
        try:
            if not self.exchange:
                self._initialize_exchange()

            formatted_symbol = self._format_symbol(symbol)
            ticker = self.exchange.fetch_ticker(formatted_symbol)
            price = ticker['last']

            is_valid = price is not None and price > 0
            if is_valid:
                logger.debug(f"Symbol {symbol} is tradable (price: {price})")
            return is_valid

        except Exception:
            # Silently return False for invalid symbols during validation
            return False

    # ── 429 退避重试 ──
    _KLINE_MAX_RETRIES = 3
    _KLINE_RETRY_BASE_DELAY = 2.0  # seconds, doubles each retry
    
    def get_kline_data(self, symbol: str, period: str = '1d', count: int = 100, persist: bool = True, since: int = 0) -> List[Dict[str, Any]]:
        """Get kline/candlestick data for a symbol (with 429 retry backoff).

        Args:
            since: Unix ms timestamp — fetch klines FROM this time (not just latest N).
                   0 = fetch latest N (backward compatible).
        """
        formatted_symbol = self._format_symbol(symbol)
        timeframe_map = {
            '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m',
            '1h': '1h', '2h': '2h', '4h': '4h', '8h': '8h', '12h': '12h',
            '1d': '1d', '3d': '3d', '1w': '1w', '1M': '1M',
        }
        timeframe = timeframe_map.get(period, '1d')

        ohlcv = self._fetch_ohlcv_with_retry(formatted_symbol, timeframe, count, since=since)
        if ohlcv is None:
            return []
    
        # Convert to our format
        klines = []
        for candle in ohlcv:
            timestamp_ms = candle[0]
            open_price = candle[1]
            high_price = candle[2]
            low_price = candle[3]
            close_price = candle[4]
            volume = candle[5]
    
            change = close_price - open_price if open_price else 0
            percent = (change / open_price * 100) if open_price else 0
    
            klines.append({
                'timestamp': int(timestamp_ms / 1000),
                'datetime': datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat(),
                'open': float(open_price) if open_price else None,
                'high': float(high_price) if high_price else None,
                'low': float(low_price) if low_price else None,
                'close': float(close_price) if close_price else None,
                'volume': float(volume) if volume else None,
                'amount': float(volume * close_price) if volume and close_price else None,
                'chg': float(change),
                'percent': float(percent),
            })
    
        # Auto-persist data to database
        if persist and klines:
            try:
                self._persist_kline_data(symbol, period, klines)
            except Exception as persist_error:
                logger.warning(f"Failed to persist kline data for {symbol}: {persist_error}")
    
        # [2026-08-06] INFO→DEBUG：该行是日志量最大来源（58 万行日志中海量刷屏），
        # 实际数据落库由 _persist_kline_data 负责，无需逐条 INFO。
        logger.debug(f"Got {len(klines)} klines for {formatted_symbol}")
        return klines
    
    def _fetch_ohlcv_with_retry(self, formatted_symbol: str, timeframe: str, count: int, since: int = 0):
        """Fetch OHLCV data with exponential backoff on 429 errors.

        Args:
            since: Unix ms timestamp — fetch FROM this time. 0 = latest N.
        """
        now = time.time()
        with _kline_fail_lock:
            suppress_until = _kline_fail_until.get(formatted_symbol, 0.0)
        if suppress_until > now:
            logger.debug("[Kline] skip %s (cooldown %.0fs left)", formatted_symbol, suppress_until - now)
            return None

        if not self.exchange:
            self._initialize_exchange()

        import ccxt as _ccxt
        last_err: Optional[Exception] = None
        for attempt in range(self._KLINE_MAX_RETRIES):
            try:
                _hl_rate_limit_wait()
                if since:
                    return self.exchange.fetch_ohlcv(formatted_symbol, timeframe, since=since, limit=count)
                return self.exchange.fetch_ohlcv(formatted_symbol, timeframe, limit=count)
            except _ccxt.RateLimitExceeded as e:
                last_err = e
                delay = self._KLINE_RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    f"429 rate limit for {formatted_symbol} klines, "
                    f"retry {attempt+1}/{self._KLINE_MAX_RETRIES} in {delay:.1f}s"
                )
                time.sleep(delay)
            except TypeError as e:
                # Non-retryable: ccxt bug (None + str) or malformed API response
                logger.warning(
                    f"[Kline] Non-retryable TypeError for {formatted_symbol}: {e} "
                    f"(likely ccxt compatibility issue — not retrying)"
                )
                with _kline_fail_lock:
                    _kline_fail_until[formatted_symbol] = now + _KLINE_FAIL_COOLDOWN_S
                return None
            except Exception as e:
                last_err = e
                err_s = str(e).lower()
                non_retryable = any(
                    token in err_s
                    for token in (
                        "does not have market",
                        "not found",
                        "invalid symbol",
                        "unknown symbol",
                        "bad symbol",
                        "market not found",
                    )
                )
                if non_retryable:
                    logger.warning(
                        "[Kline] symbol unavailable %s: %s (cooldown %ds)",
                        formatted_symbol,
                        e,
                        int(_KLINE_FAIL_COOLDOWN_S),
                    )
                    with _kline_fail_lock:
                        _kline_fail_until[formatted_symbol] = now + _KLINE_FAIL_COOLDOWN_S
                    return None
                if "429" in str(e) or "Too Many Requests" in str(e):
                    delay = self._KLINE_RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        f"429 error (non-ccxt) for {formatted_symbol} klines, "
                        f"retry {attempt+1}/{self._KLINE_MAX_RETRIES} in {delay:.1f}s"
                    )
                    time.sleep(delay)
                else:
                    logger.warning(
                        f"Error fetching klines for {formatted_symbol}: {e} "
                        f"(attempt {attempt+1}/{self._KLINE_MAX_RETRIES})"
                    )
                    if attempt == self._KLINE_MAX_RETRIES - 1:
                        break
                    time.sleep(self._KLINE_RETRY_BASE_DELAY)
        with _kline_fail_lock:
            _kline_fail_until[formatted_symbol] = now + _KLINE_FAIL_COOLDOWN_S
        logger.warning(
            "Kline fetch exhausted for %s (%s) — suppress ERROR for %ds",
            formatted_symbol,
            last_err,
            int(_KLINE_FAIL_COOLDOWN_S),
        )
        return None

    def _persist_kline_data(self, symbol: str, period: str, klines: List[Dict[str, Any]]):
        """Persist kline data to database

        IMPORTANT DESIGN DECISION:
        Only mainnet K-line data is persisted to database.
        Testnet data is fetched in real-time on-demand and NOT stored.

        This design ensures:
        1. Database contains consistent historical data (mainnet only)
        2. Testnet trading uses real-time API calls without database overhead
        3. No environment mixing in stored K-line data
        """
        # CRITICAL: Only persist mainnet data per design specification
        if self.environment != "mainnet":
            logger.debug(f"Skipping K-line persistence for {symbol} {period} (environment={self.environment}, only mainnet data is stored)")
            return

        try:
            from repositories.kline_repo import KlineRepository

            from backend.database.connection import MarketSessionLocal

            db = MarketSessionLocal()
            try:
                kline_repo = KlineRepository(db)
                result = kline_repo.save_kline_data(
                    symbol=symbol,
                    market="CRYPTO",
                    period=period,
                    kline_data=klines,
                    exchange="hyperliquid",
                    environment="mainnet"  # Always store as mainnet per design
                )
                logger.debug(f"Persisted {result['total']} kline records for {symbol} {period}")
            finally:
                db.close()
        except Exception as e:
            err_s = str(e)
            if "UniqueViolation" in err_s or "duplicate key" in err_s.lower():
                logger.debug("Kline persist duplicate ignored for %s %s", symbol, period)
                return
            logger.error(f"Error persisting kline data: {e}")

    def get_market_status(self, symbol: str) -> Dict[str, Any]:
        """Get market status for a symbol"""
        try:
            if not self.exchange:
                self._initialize_exchange()
            
            formatted_symbol = self._format_symbol(symbol)
            
            # Hyperliquid is 24/7, but we can check if the market exists
            markets = self.exchange.load_markets()
            market_exists = formatted_symbol in markets
            
            status = {
                'market_status': 'OPEN' if market_exists else 'CLOSED',
                'is_trading': market_exists,
                'symbol': formatted_symbol,
                'exchange': 'Hyperliquid',
                'market_type': 'crypto',
            }
            
            if market_exists:
                market_info = markets[formatted_symbol]
                status.update({
                    'base_currency': market_info.get('base'),
                    'quote_currency': market_info.get('quote'),
                    'active': market_info.get('active', True),
                })
            
            logger.info(f"Market status for {formatted_symbol}: {status['market_status']}")
            return status
            
        except Exception as e:
            logger.error(f"Error getting market status for {symbol}: {e}")
            return {
                'market_status': 'ERROR',
                'is_trading': False,
                'error': str(e)
            }

    def get_all_symbols(self) -> List[str]:
        """Get all available trading symbols (no truncation)"""
        try:
            if not self.exchange:
                self._initialize_exchange()
            
            markets = self.exchange.load_markets()
            symbols = list(markets.keys())
            
            usdc_symbols = [s for s in symbols if '/USDC' in s]
            
            mainstream = ['BTC/', 'ETH/', 'SOL/', 'DOGE/', 'BNB/', 'XRP/']
            mainstream_perps = [s for s in usdc_symbols if any(crypto in s for crypto in mainstream)]
            other_symbols = [s for s in usdc_symbols if s not in mainstream_perps]
            
            result = mainstream_perps + other_symbols
            
            logger.info(f"Found {len(usdc_symbols)} USDC trading pairs, returning all {len(result)}")
            return result
            
        except Exception as e:
            logger.error(f"Error getting symbols: {e}")
            return ['BTC/USD', 'ETH/USD', 'SOL/USD']

    # 旧 ticker → 现行 coin（meta 仍可能残留旧名）
    _SYMBOL_ALIASES = {
        "MATIC": "POL",
        "RNDR": "RENDER",
        "FTM": "S",
    }

    def _format_symbol(self, symbol: str) -> str:
        """Format symbol for CCXT Hyperliquid perpetual swap.

        Hyperliquid is a perpetual futures exchange — ALL symbols use
        the ``BASE/USDC:USDC`` format (not spot ``BASE/USDC``).
        """
        if '/' in symbol and ':' in symbol:
            return symbol
        elif '/' in symbol:
            base = symbol.split('/')[0].upper()
            base = self._SYMBOL_ALIASES.get(base, base)
            return f"{base}/USDC:USDC"

        base = symbol.upper()
        base = self._SYMBOL_ALIASES.get(base, base)
        return f"{base}/USDC:USDC"


# Client factory functions
_client_cache = {}

def create_hyperliquid_client(environment: str = "mainnet") -> HyperliquidClient:
    """Create a new HyperliquidClient instance for the specified environment"""
    return HyperliquidClient(environment=environment)

def get_hyperliquid_client_for_environment(environment: str = "mainnet") -> HyperliquidClient:
    """Get cached HyperliquidClient instance for the specified environment"""
    if environment not in _client_cache:
        _client_cache[environment] = create_hyperliquid_client(environment)
    return _client_cache[environment]

# Backward compatibility - default to mainnet
def get_default_hyperliquid_client() -> HyperliquidClient:
    """Get default HyperliquidClient (mainnet) for backward compatibility"""
    return get_hyperliquid_client_for_environment("mainnet")


def get_last_price_from_hyperliquid(symbol: str, environment: str = "mainnet") -> Optional[float]:
    """Get last price from Hyperliquid"""
    client = get_hyperliquid_client_for_environment(environment)
    return client.get_last_price(symbol)


def get_kline_data_from_hyperliquid(symbol: str, period: str = '1d', count: int = 100, persist: bool = True, environment: str = "mainnet") -> List[Dict[str, Any]]:
    """Get kline data from Hyperliquid"""
    client = get_hyperliquid_client_for_environment(environment)
    return client.get_kline_data(symbol, period, count, persist)


def get_market_status_from_hyperliquid(symbol: str, environment: str = "mainnet") -> Dict[str, Any]:
    """Get market status from Hyperliquid"""
    client = get_hyperliquid_client_for_environment(environment)
    return client.get_market_status(symbol)


def get_all_symbols_from_hyperliquid(environment: str = "mainnet") -> List[str]:
    """Get all available symbols from Hyperliquid"""
    client = get_hyperliquid_client_for_environment(environment)
    return client.get_all_symbols()


def get_ticker_data_from_hyperliquid(symbol: str, environment: str = "mainnet") -> Optional[Dict[str, Any]]:
    """Get complete ticker data from Hyperliquid"""
    client = get_hyperliquid_client_for_environment(environment)
    return client.get_ticker_data(symbol)


def get_bulk_ticker_data_from_hyperliquid(symbols: List[str], environment: str = "mainnet") -> Dict[str, Dict[str, Any]]:
    """批量获取多个 symbol 的 ticker 数据（单次 API 请求）"""
    client = get_hyperliquid_client_for_environment(environment)
    return client.get_bulk_ticker_data(symbols)
