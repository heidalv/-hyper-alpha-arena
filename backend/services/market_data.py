from datetime import datetime
from typing import Dict, List, Any
import logging
import os
from .hyperliquid_market_data import (
    get_last_price_from_hyperliquid,
    get_kline_data_from_hyperliquid,
    get_market_status_from_hyperliquid,
    get_all_symbols_from_hyperliquid,
    get_ticker_data_from_hyperliquid,
    get_default_hyperliquid_client,
)

logger = logging.getLogger(__name__)


def _dc_only_enabled() -> bool:
    """数据中心唯一数据源开关（默认开）。

    MARKET_DATA_DC_ONLY=true 时，所有行情读取只走数据中心落库数据（DB），
    禁止兜底直连交易所——保证「项目内所有数据请求的唯一来源是数据中心」。

    关闭（false）则保留旧兜底逻辑，用于应急/排障。
    """
    return os.getenv("MARKET_DATA_DC_ONLY", "true").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _resolve_exchange(market: str = None) -> str:
    """将 market 参数解析为交易所标识（支持多交易所）。

    2026-07-31：CRYPTO/空 不再默认 hyperliquid（会导致「交易 Aster、数据 HL」）。
    改为跟随 get_active_exchange()（会话 active_exchange → DEFAULT_EXCHANGE）。
    """
    from backend.services.market_data_adapters.registry import ExchangeAdapterRegistry
    from backend.services.exchange_config import get_active_exchange

    if not market or str(market).upper() == "CRYPTO":
        return get_active_exchange()
    return ExchangeAdapterRegistry.normalize_exchange(str(market))


def _normalize_adapter_klines(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    klines: List[Dict[str, Any]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        ts = item.get("timestamp")
        if ts is None:
            continue
        ts = int(ts)
        if ts > 1_000_000_000_000:
            ts //= 1000
        try:
            klines.append({
                "timestamp": ts,
                "datetime": datetime.utcfromtimestamp(ts).isoformat(),
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "volume": float(item["volume"]) if item.get("volume") is not None else None,
            })
        except (KeyError, TypeError, ValueError):
            continue
    return klines


def _period_stale_seconds(period: str) -> int:
    """K 线 DB 数据允许的最大滞后（秒）。"""
    return {
        "1m": 180,
        "3m": 360,
        "5m": 600,
        "15m": 1200,
        "30m": 2400,
        "1h": 7200,
        "2h": 14400,
        "4h": 18000,
        "8h": 36000,
        "12h": 54000,
        "1d": 90000,
        "3d": 259200,
        "1w": 604800,
        "1M": 2592000,
    }.get(period, 180)


def _db_klines_are_fresh(db_data: List[Dict[str, Any]], period: str) -> bool:
    if not db_data:
        return False
    import time
    latest_ts = int(db_data[-1].get("timestamp") or 0)
    if latest_ts <= 0:
        return False
    return (int(time.time()) - latest_ts) <= _period_stale_seconds(period)


def _fetch_klines_from_adapter(exchange: str, symbol: str, period: str, count: int) -> List[Dict[str, Any]]:
    import asyncio
    import concurrent.futures

    from backend.services.market_data_adapters.registry import exchange_adapter_registry

    async def _fetch():
        return await exchange_adapter_registry.get_klines(exchange, symbol, period, limit=count)

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                raw = pool.submit(asyncio.run, _fetch()).result(timeout=20)
        else:
            raw = asyncio.run(_fetch())
        return _normalize_adapter_klines(raw)
    except Exception as exc:
        logger.warning(f"Adapter kline fetch failed for {symbol}.{exchange}: {exc}")
        return []


def get_last_price(symbol: str, market: str = "CRYPTO", environment: str = "mainnet") -> float:
    exchange = _resolve_exchange(market)
    key = f"{symbol}.{exchange}.{environment}" if exchange == "hyperliquid" else f"{symbol}.{exchange}"

    from .price_cache import get_cached_price, cache_price
    # [P2-1 跨所串价修复] 优先取本所缓存；legacy 空所键仅作二级回退（保持旧写者兼容）。
    # asterdex 的写入现在带 exchange="asterdex"，不再污染 hyperliquid 的读数。
    cached_price = get_cached_price(symbol, market, environment, exchange=exchange)
    if cached_price is None:
        cached_price = get_cached_price(symbol, market, environment)
    if cached_price is not None and exchange == "hyperliquid":
        return cached_price

    if exchange == "hyperliquid":
        # [2026-08-04 修复] 数据中心唯一数据源：DC_ONLY 模式禁止直连 Hyperliquid，
        # 价格统一从 price_cache（数据中心 ticker 写入）或 DB 最新 K 线读取。
        if _dc_only_enabled():
            if cached_price is not None:
                return cached_price
            try:
                from .kline_data_service import kline_service
                db_data = kline_service.get_klines_from_db(
                    symbol.upper(), "1m", 1, exchange=exchange
                )
                if db_data:
                    price = float(db_data[-1]["close"])
                    cache_price(symbol, market, price, environment, exchange=exchange)
                    return price
            except Exception as e:
                logger.debug(f"[DC_ONLY] HL price DB fallback failed: {e}")
            raise Exception(
                f"Unable to get real-time price for {key}: "
                f"DC_ONLY 模式无缓存/DB 数据（数据中心未采集该所价格）"
            )

        logger.info(f"Getting real-time price for {key}...")
        try:
            price = get_last_price_from_hyperliquid(symbol, environment)
            if price and price > 0:
                logger.info(f"Got price for {key} from Hyperliquid: {price}")
                cache_price(symbol, market, price, environment, exchange=exchange)
                return price
            raise Exception(f"Hyperliquid returned invalid price: {price}")
        except Exception as hl_err:
            logger.error(f"Price fetch failed for {key}: {hl_err}")
            raise Exception(f"Unable to get real-time price for {key}: {hl_err}")

    # [2026-08-15 P0-3 修复] 非 hyperliquid 所不再直接取 DB 1m close 当作
    # 「实时价」；统一走 data_center.get_price_with_ts 权威链路：
    # 秒级 ticker（跨进程 DC REST 2s 通道 → hub）→ DB 1m close（带 stale 门）。
    # 决策价与成交价口径一致，避免 1m 收盘价造成的最大 ~60s+ 漂移。
    try:
        from backend.services.data_center import data_center
        result = data_center.get_price_with_ts(symbol, exchange, purpose="trade")
        if result:
            price, ts = result
            if price and float(price) > 0:
                cache_price(symbol, market, float(price), environment, exchange=exchange)
                return float(price)
    except Exception as e:
        logger.debug(f"[DC_ONLY] data_center price failed for {symbol}.{exchange}: {e}")
    raise Exception(
        f"Unable to get price for {key}: 数据中心无可用价格（秒级 ticker 与 1m 兜底均失败）"
    )


def get_kline_data(symbol: str, market: str = "CRYPTO", period: str = "1d", count: int = 100, environment: str = "mainnet") -> List[Dict[str, Any]]:
    """数据中台整改：统一走 data_center（多交易所择优）。

    [2026-08-04 修复] DC_ONLY 模式（默认开）：只读数据中心落库数据，
    禁止 adapter/交易所直连兜底，保证唯一数据源。
    """
    exchange = _resolve_exchange(market)

    # 走数据中心统一入口
    try:
        from backend.services.data_center import data_center
        result = data_center.get_klines(symbol, period, count=count, exchange=exchange, purpose="trade")
        if result.rows:
            return result.rows[-count:] if len(result.rows) > count else result.rows
    except Exception:
        pass

    # DC_ONLY：禁止直连交易所，仅回退到 DB 原始读取
    if _dc_only_enabled():
        try:
            from .kline_data_service import kline_service
            db_data = kline_service.get_klines_from_db(symbol.upper(), period, count, exchange=exchange)
            if db_data:
                return db_data
        except Exception as e:
            logger.debug(f"[DC_ONLY] DB kline read for {symbol}.{exchange}: {e}")
        raise Exception(
            f"Unable to get K-line data for {symbol}.{exchange}: "
            f"DC_ONLY 模式数据中心无数据"
        )

    # 兜底：data_center 不可用时走原逻辑（仅 MARKET_DATA_DC_ONLY=false）
    key = f"{symbol}.{exchange}.{environment}" if exchange == "hyperliquid" else f"{symbol}.{exchange}"
    db_data: List[Dict[str, Any]] = []

    # 非 Hyperliquid：优先走实时 adapter，避免 DB 里历史快照被当成最新 K 线
    if exchange != "hyperliquid":
        adapter_data = _fetch_klines_from_adapter(exchange, symbol.upper(), period, count)
        if adapter_data:
            logger.info(f"Got K-line for {key} from adapter ({len(adapter_data)} items)")
            return adapter_data

    # 策略1: 数据库（需通过新鲜度检查）
    try:
        from .kline_data_service import kline_service
        db_data = kline_service.get_klines_from_db(symbol.upper(), period, count, exchange=exchange)
        if db_data and len(db_data) >= count * 0.8 and _db_klines_are_fresh(db_data, period):
            logger.info(f"Got K-line for {key} from DB ({len(db_data)} items)")
            return db_data
        if db_data and not _db_klines_are_fresh(db_data, period):
            logger.info(f"Stale DB K-line for {key} (period={period}), refreshing from API")
    except Exception as e:
        logger.debug(f"DB read for {key}: {e}")

    # 策略2: HyperLiquid API（仅 hyperliquid）
    if exchange == "hyperliquid":
        try:
            data = get_kline_data_from_hyperliquid(symbol, period, count, persist=True, environment=environment)
            if data:
                logger.info(f"Got K-line for {key} from Hyperliquid ({len(data)} items)")
                return data
        except Exception as api_err:
            logger.warning(f"Hyperliquid API fetch failed for {key}: {api_err}")

    # 策略3: 其他交易所 adapter（hyperliquid 失败时的回退已在上面处理）
    if exchange != "hyperliquid":
        adapter_data = _fetch_klines_from_adapter(exchange, symbol.upper(), period, count)
        if adapter_data:
            logger.info(f"Got K-line for {key} from adapter fallback ({len(adapter_data)} items)")
            return adapter_data

    # 策略4: 部分 DB 数据（最后兜底）
    if db_data:
        logger.info(f"Returning partial/stale DB data for {key} ({len(db_data)} items)")
        return db_data

    raise Exception(f"Unable to get K-line data for {key}: no data from DB or API")


def get_market_status(symbol: str, market: str = "CRYPTO") -> Dict[str, Any]:
    exchange = _resolve_exchange(market)
    key = f"{symbol}.{exchange}"

    if exchange != "hyperliquid":
        return {"symbol": symbol, "exchange": exchange, "market_status": "unknown"}

    if _dc_only_enabled():
        # DC_ONLY：交易状态从数据中心 catalog 读取（本地库，无直连）
        try:
            from sqlalchemy import text as sa_text

            from backend.database.connection import MarketSessionLocal
            with MarketSessionLocal() as db:
                row = db.execute(sa_text(
                    "SELECT status FROM symbol_catalog WHERE exchange=:ex AND symbol=:sym LIMIT 1"
                ), {"ex": "hyperliquid", "sym": symbol.upper()}).scalar()
            return {
                "symbol": symbol,
                "exchange": exchange,
                "market_status": str(row) if row else "unknown",
            }
        except Exception as e:
            logger.debug(f"[DC_ONLY] market_status DB read failed: {e}")
            return {"symbol": symbol, "exchange": exchange, "market_status": "unknown"}

    try:
        status = get_market_status_from_hyperliquid(symbol)
        logger.info(f"Retrieved market status for {key} from Hyperliquid: {status.get('market_status')}")
        return status
    except Exception as hl_err:
        logger.error(f"Failed to get market status: {hl_err}")
        raise Exception(f"Unable to get market status for {key}: {hl_err}")


def get_all_symbols() -> List[str]:
    """Get all available trading pairs（DC_ONLY 读数据中心 catalog，不直连交易所）"""
    if _dc_only_enabled():
        try:
            from backend.services.data_center import data_center
            syms = data_center.list_symbols()
            if syms:
                return [s if "/" in s else s for s in syms]
        except Exception as e:
            logger.debug(f"[DC_ONLY] list_symbols failed: {e}")
        return []
    try:
        symbols = get_all_symbols_from_hyperliquid()
        logger.info(f"Got {len(symbols)} trading pairs from Hyperliquid")
        return symbols
    except Exception as err:
        logger.error(f"Failed to get trading pairs: {err}")
        return ['BTC/USD', 'ETH/USD', 'SOL/USD']


def get_ticker_data(symbol: str, market: str = "CRYPTO", environment: str = "mainnet") -> Dict[str, Any]:
    """获取 ticker 数据；hyperliquid 走实时 API，其他交易所走 DB 最新 K 线。

    [2026-08-04 修复] DC_ONLY 模式：所有交易所统一从 DB/data_center 读取。
    """
    exchange = _resolve_exchange(market)
    key = f"{symbol}.{exchange}.{environment}" if exchange == "hyperliquid" else f"{symbol}.{exchange}"

    if exchange == "hyperliquid" and not _dc_only_enabled():
        try:
            ticker_data = get_ticker_data_from_hyperliquid(symbol, environment)
            if ticker_data:
                logger.info(f"Got ticker for {key}: price={ticker_data.get('price')}")
                return ticker_data
            raise Exception("Hyperliquid returned empty ticker data")
        except Exception as err:
            logger.error(f"Failed to get ticker data: {err}")
            try:
                price = get_last_price(symbol, market, environment)
                return {
                    'symbol': symbol,
                    'price': price,
                    'change24h': 0,
                    'volume24h': 0,
                    'percentage24h': 0,
                }
            except Exception:
                raise Exception(f"Unable to get ticker data for {key}: {err}")

    # DC_ONLY 或非 hyperliquid：统一从 DB 最新 K 线聚合 ticker（数据中心唯一数据源）
    from .kline_data_service import kline_service
    klines = kline_service.get_klines_from_db(symbol.upper(), "1m", 2, exchange=exchange)
    if not klines:
        # 1m 缺失时退 1h（数据中心按周期分级落库）
        klines = kline_service.get_klines_from_db(symbol.upper(), "1h", 2, exchange=exchange)
    if not klines:
        raise Exception(f"Unable to get ticker data for {key}: no DB data")
    latest = klines[-1]
    prev = klines[-2] if len(klines) > 1 else latest
    price = float(latest["close"])
    prev_close = float(prev["close"]) or price
    change = price - prev_close
    pct = (change / prev_close * 100) if prev_close else 0
    return {
        "symbol": symbol.upper(),
        "price": price,
        "change24h": change,
        "volume24h": float(latest.get("volume") or 0),
        "percentage24h": pct,
        "open_interest": 0,
        "funding_rate": 0,
    }
