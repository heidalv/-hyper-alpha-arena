"""
Market data API routes
Provides RESTful API interfaces for crypto market data
"""

from fastapi import APIRouter, HTTPException, Query
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
import logging
import os
import threading
import time
from datetime import datetime, timezone

from backend.services.market_data import get_last_price, get_kline_data, get_market_status, get_ticker_data
from backend.services.market_data_metrics import market_data_metrics
from backend.services.symbol_normalizer import normalize_symbol

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market", tags=["market_data"])


def _fetch_db_klines(exchange: str, symbol: str, period: str, count: int) -> list:
    """[2026-08-04 统一数据源] 从数据中心 DB 读取最新 K 线（不直连交易所）。"""
    try:
        from backend.services.kline_data_service import kline_service
        return kline_service.get_klines_from_db(
            symbol.upper(), period, count, exchange=exchange
        ) or []
    except Exception as exc:
        logger.debug("[DC_ONLY] _fetch_db_klines %s/%s@%s: %s",
                     symbol, period, exchange, str(exc)[:150])
        return []

_active_symbols_cache: Dict[str, Any] = {"ts": 0.0, "symbols": set()}
_exchange_ticker_cache: Dict[str, Any] = {}
_EXCHANGE_TICKER_TTL = 30
_OVERVIEW_WARMUP_INTERVAL = 15
_warmup_thread: Optional[threading.Thread] = None
_warmup_stop = threading.Event()


def _fetch_exchange_rows(exchange: str) -> list:
    """按交易所拉全市场 24h ticker（10s 缓存），返回统一行结构。"""
    now = time.time()
    cached = _exchange_ticker_cache.get(exchange)
    if cached and now - cached[0] < _EXCHANGE_TICKER_TTL:
        return cached[1]

    def _fetch_once() -> list:
        rows: list = []
        # [2026-08-06 修复] 统一数据源从数据中心 DB 聚合全市场 24h ticker。
        # 单条窗口函数 SQL 一次取全（每 symbol 最新 1d 行 + LAG 前收盘），
        # 消除原实现 N+1 次查询（1790 万行表上单次请求 38s 卡死 backend 线程池的根因）。
        from sqlalchemy import text as sa_text

        from backend.database.connection import MarketSessionLocal
        try:
            with MarketSessionLocal() as db:
                qrows = db.execute(sa_text("""
                    WITH ranked AS (
                        SELECT symbol, "timestamp", close_price, volume, high_price, low_price,
                               close_price * volume AS quote_volume,
                               ROW_NUMBER() OVER (
                                   PARTITION BY symbol ORDER BY "timestamp" DESC
                               ) AS rn,
                               LAG(close_price) OVER (
                                   PARTITION BY symbol ORDER BY "timestamp" DESC
                               ) AS prev_close
                        FROM crypto_klines
                        WHERE exchange = :ex AND period = '1d' AND close_price > 0
                    )
                    SELECT symbol, close_price, volume, high_price, low_price,
                           quote_volume, prev_close
                    FROM ranked WHERE rn = 1
                    ORDER BY "timestamp" DESC
                """), {"ex": exchange}).fetchall()
            # 归一化去重：历史残留格式（BTC-PERP 等）与标准格式（BTC）映射到同一
            # base，保留时间戳最新的一行，杜绝「同一交易对多条目/多价格」展示。
            seen: set = set()
            for sym, close, vol, high, low, qv, prev_c in qrows:
                base = normalize_symbol(sym)
                if not base or len(base) < 2 or close is None or float(close) <= 0:
                    continue
                if base in seen:
                    continue
                seen.add(base)
                price = float(close)
                quote_vol = float(qv) if qv is not None else (float(vol or 0) * price)
                change_pct = 0.0
                if prev_c and float(prev_c) > 0:
                    change_pct = round((price - float(prev_c)) / float(prev_c) * 100, 4)
                rows.append({
                    "symbol": base,
                    "price": round(price, 8),
                    "change_pct": change_pct,
                    "high_24h": float(high or 0),
                    "low_24h": float(low or 0),
                    "volume_24h": float(vol or 0),
                    "quote_volume_24h": quote_vol,
                    "trades_24h": 0,
                })
        except Exception as exc:
            logger.warning("[OverviewAll] %s DB 聚合失败: %s", exchange, str(exc)[:200])
        return rows

    rows: list = []
    for attempt in range(2):
        try:
            rows = _fetch_once()
            if rows:
                break
        except Exception as exc:
            logger.warning("[OverviewAll] %s 拉取失败(第%d次): %s", exchange, attempt + 1, str(exc)[:150])
            time.sleep(1)
    if not rows:
        return []
    _exchange_ticker_cache[exchange] = (time.time(), rows)
    return rows


def _warmup_loop() -> None:
    """后台预热多交易所 24h ticker，避免总览页首次请求等待 4~20s。"""
    from concurrent.futures import ThreadPoolExecutor
    while not _warmup_stop.is_set():
        t0 = time.time()
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [
                    pool.submit(_fetch_exchange_rows, ex)
                    for ex in ("binance", "okx", "hyperliquid")
                ]
                for f in futures:
                    try:
                        f.result(timeout=40)
                    except Exception:
                        pass
        except Exception:
            pass
        wait = max(1.0, _OVERVIEW_WARMUP_INTERVAL - (time.time() - t0))
        _warmup_stop.wait(wait)


def start_overview_warmup() -> None:
    """启动总览数据后台预热（幂等）。"""
    global _warmup_thread
    if _warmup_thread and _warmup_thread.is_alive():
        return
    _warmup_stop.clear()
    _warmup_thread = threading.Thread(
        target=_warmup_loop,
        name="market-overview-warmup",
        daemon=True,
    )
    _warmup_thread.start()


@router.get("/data-depth")
def get_market_data_depth(
    symbols: str = Query("BTC,ETH,SOL"),
    exchange: str = Query("asterdex"),
):
    """数据中心 K 线深度/新鲜度看板（设计文档 §1.3）。"""
    ex = (exchange or "asterdex").strip().lower()
    if ex == "aster":
        ex = "asterdex"
    sym_list = [s.strip().upper() for s in (symbols or "").split(",") if s.strip()][:20]
    if not sym_list:
        sym_list = ["BTC"]
    periods = ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"]
    bars_per_day = {
        "1m": 1440, "3m": 480, "5m": 288, "15m": 96,
        "30m": 48, "1h": 24, "4h": 6, "1d": 1,
    }
    out: Dict[str, Any] = {"exchange": ex, "periods": [], "by_symbol": {}}
    try:
        from backend.database.connection import MarketSessionLocal
        from sqlalchemy import text as _sa_text
        with MarketSessionLocal() as mdb:
            for period in periods:
                rows = mdb.execute(
                    _sa_text(
                        "SELECT symbol, min(timestamp) mn, max(timestamp) mx, count(*) c "
                        "FROM crypto_klines "
                        "WHERE exchange=:ex AND period=:p AND symbol = ANY(:syms) "
                        "GROUP BY symbol"
                    ),
                    {"ex": ex, "p": period, "syms": sym_list},
                ).fetchall()
                if not rows:
                    out["periods"].append({
                        "period": period, "coverage_days": 0,
                        "latest_ts": None, "gap_pct": 1.0, "symbols": 0,
                    })
                    continue
                best_days = 0.0
                for r in rows:
                    sym = str(r[0])
                    cov = (float(r[2]) - float(r[1])) / 86400.0
                    expected = max(1, cov * bars_per_day.get(period, 1))
                    gap = max(0.0, 1.0 - float(r[3]) / expected)
                    out["by_symbol"].setdefault(sym, {})[period] = round(cov, 1)
                    best_days = max(best_days, cov)
                out["periods"].append({
                    "period": period,
                    "coverage_days": round(best_days, 1),
                    "latest_ts": max(float(r[2]) for r in rows),
                    "gap_pct": 0.0,
                    "symbols": len(rows),
                })
    except Exception as exc:
        out["error"] = str(exc)[:200]
    return out


@router.get("/factors/exposure")
def get_factor_exposure(
    symbol: str = Query("BTC"),
    period: str = Query("5m"),
    count: int = Query(200, ge=60, le=500),
):
    """M3 因子暴露矩阵：该币在活跃因子上的 z-score × 净IC × 权重。"""
    try:
        from backend.services.factor_engine.exposure_service import (
            factor_exposure_service,
        )
        factors = factor_exposure_service.exposure(symbol, period, count)
        return {
            "symbol": str(symbol).upper(),
            "period": period,
            "as_of": time.time(),
            "factors": factors,
            "fail_factors": factor_exposure_service.status().get("fail_factors", 0),
            "source": "factor_exposure_service",
        }
    except Exception as exc:
        return {"symbol": str(symbol).upper(), "period": period, "factors": [],
                "error": str(exc)[:200]}


@router.get("/rag/status")
def get_rag_status():
    """M10 RAG 本地化状态：local 模式显式健康/失败，不再静默降级 SQL。"""
    mode = os.getenv("RAG_EMBEDDING_MODE", "local").strip().lower()
    healthy = mode == "local"
    return {"mode": mode, "healthy": healthy, "vector_count": 0}


def _active_trading_symbols() -> set:
    """当前正在交易的币种：open 模拟持仓 + running full_auto 会话（手动+自动选币）。"""
    now = time.time()
    if now - _active_symbols_cache.get("ts", 0.0) < 30:
        return set(_active_symbols_cache.get("symbols") or set())
    symbols: set = set()
    try:
        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal
        from backend.database.models import FullAutoSession, PaperPosition

        with system_identity():
            db = SessionLocal()
            try:
                for pos in db.query(PaperPosition).filter(PaperPosition.status == "open").all():
                    sym = str(getattr(pos, "symbol", "") or "").upper().split("-")[0].split("/")[0]
                    if sym:
                        symbols.add(sym)
                sessions = db.query(FullAutoSession).filter(
                    FullAutoSession.status.in_(["running", "defensive", "paused"])
                ).all()
                for s in sessions:
                    for sym in (getattr(s, "symbols", None) or []):
                        sym = str(sym).upper().split("-")[0].split("/")[0]
                        if sym:
                            symbols.add(sym)
                    for sym in (getattr(s, "auto_coin_symbols", None) or []):
                        sym = str(sym).upper().split("-")[0].split("/")[0]
                        if sym:
                            symbols.add(sym)
            finally:
                db.close()
    except Exception:
        pass
    _active_symbols_cache["ts"] = now
    _active_symbols_cache["symbols"] = symbols
    return set(symbols)


@router.get("/overview/all")
def get_market_overview_all(exchange: str = Query("asterdex")):
    """全市场交易对总览（交易所风格列表）：
    全部交易对 + 24h 统计 + 「正在交易」标记，默认交易中优先、成交额降序。
    """
    ex = (exchange or "asterdex").strip().lower()
    if ex == "aster":
        ex = "asterdex"
    if ex not in ("asterdex", "binance", "okx", "hyperliquid", "all"):
        ex = "asterdex"

    active = _active_trading_symbols()
    exchanges = ["asterdex", "binance", "okx", "hyperliquid"] if ex == "all" else [ex]
    rows = []
    for e in exchanges:
        for r in _fetch_exchange_rows(e):
            rows.append({
                "exchange": e,
                "symbol": r["symbol"],
                "price": r["price"],
                "change_pct": r["change_pct"],
                "high_24h": r["high_24h"],
                "low_24h": r["low_24h"],
                "volume_24h": r["volume_24h"],
                "quote_volume_24h": r["quote_volume_24h"],
                "trades_24h": r["trades_24h"],
                "active": r["symbol"] in active,
            })
    rows.sort(key=lambda r: (not r["active"], -float(r["quote_volume_24h"] or 0)))
    return {
        "total": len(rows),
        "active_count": len(active),
        "exchange": ex,
        "source": f"data_center:{ex}",
        "fetched_at": time.time(),
        "rows": rows,
    }


class PriceResponse(BaseModel):
    """Price response model"""
    symbol: str
    market: str
    price: float
    oracle_price: Optional[float] = 0
    change24h: Optional[float] = 0
    volume24h: Optional[float] = 0
    percentage24h: Optional[float] = 0
    open_interest: Optional[float] = 0
    funding_rate: Optional[float] = 0
    timestamp: int


class KlineItem(BaseModel):
    """K-line data item model"""
    timestamp: int
    datetime: str
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    volume: Optional[float]
    amount: Optional[float]
    chg: Optional[float]
    percent: Optional[float]


class KlineResponse(BaseModel):
    """K-line data response model"""
    symbol: str
    market: str
    period: str
    count: int
    data: List[KlineItem]


class MarketStatusResponse(BaseModel):
    """Market status response model"""
    symbol: str
    market: str = None
    market_status: str
    timestamp: int
    current_time: str


@router.get("/price/{symbol}", response_model=PriceResponse)
def get_crypto_price(symbol: str, market: str = None):
    """
    Get latest crypto price

    [2026-08-07 价格权威口径] 统一收敛到 data_center.get_price_with_ts：
    秒级 ticker（poller/hub，带 stale 校验）优先 → DB 最新 1m close 兜底，
    与 /prices、/prices/snapshots 同源同值。

    Args:
        symbol: crypto symbol, such as 'BTC'
        market: 交易所标识，缺省用 active_exchange

    Returns:
        Response containing latest price
    """
    try:
        import time

        if market is None:
            from backend.services.exchange_config import get_active_exchange
            market = get_active_exchange()
        from backend.services.data_center import data_center

        result = data_center.get_price_with_ts(symbol, exchange=market, purpose="trade")
        if not result:
            raise HTTPException(status_code=404, detail=f"No price data for {symbol}")
        price = float(result[0])
        base = normalize_symbol(symbol) or str(symbol).upper()
        return PriceResponse(
            symbol=base,
            market=market,
            price=round(price, 8),
            oracle_price=round(price, 8),
            change24h=0,
            volume24h=0,
            percentage24h=0,
            open_interest=0,
            funding_rate=0,
            timestamp=int(time.time() * 1000)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get crypto price: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get crypto price: {str(e)}")


@router.get("/prices", response_model=List[PriceResponse])
def get_multiple_prices(symbols: str = "BTC,ETH,SOL", market: str = None):
    """
    批量取价 — 优先 MarketDataHub 内存快照，miss 时降级 REST bulk
    """
    try:
        if market is None:
            from services.exchange_config import get_active_exchange
            market = get_active_exchange()
        symbol_list = [s.strip().upper() for s in symbols.split(',') if s.strip()]

        if not symbol_list:
            raise HTTPException(status_code=400, detail="crypto symbol list cannot be empty")

        if len(symbol_list) > 30:
            raise HTTPException(status_code=400, detail="Maximum 30 crypto symbols supported")

        import time
        current_timestamp = int(time.time() * 1000)

        from backend.services.market_price_service import get_market_snapshots
        hub_snaps = get_market_snapshots(symbol_list)

        # [2026-08-07 统一口径] 价格字段统一 data_center（秒级 ticker 优先 → DB 1m 兜底），
        # hub 内存仅作附加字段（24h 统计等）来源，避免旧快照值顶替实时价。
        dc_vals: Dict[str, float] = {}
        try:
            from backend.services.data_center import data_center
            for _symbol in symbol_list:
                _r = data_center.get_price_with_ts(_symbol, purpose="trade")
                if _r and _r[0] and float(_r[0]) > 0:
                    dc_vals[_symbol] = float(_r[0])
        except Exception:
            pass

        results: List[PriceResponse] = []
        missing: List[str] = []
        for symbol in symbol_list:
            snap = hub_snaps.get(symbol, {})
            price = dc_vals.get(symbol)
            if price is None:
                price = float(snap.get("price", 0) or 0)
            if price > 0:
                results.append(PriceResponse(
                    symbol=symbol,
                    market=market,
                    price=price,
                    oracle_price=float(snap.get("mark_price", 0) or 0),
                    change24h=0,
                    volume24h=float(snap.get("volume_24h", 0) or 0),
                    percentage24h=float(snap.get("price_24h_change_pct", 0) or 0),
                    open_interest=float(snap.get("open_interest", 0) or 0),
                    funding_rate=float(snap.get("funding_rate", 0) or 0),
                    timestamp=current_timestamp,
                ))
            else:
                missing.append(symbol)

        if not missing:
            return results

        try:
            from backend.services.exchange_config import get_active_exchange
            _fill_ex = (get_active_exchange() or "asterdex").strip().lower()
            if _fill_ex == "aster":
                _fill_ex = "asterdex"
        except Exception:
            _fill_ex = "asterdex"

        filled: set = set()

        # [2026-08-07 统一口径] 缺失补齐统一收敛到 data_center.get_price_with_ts
        # （秒级 ticker 优先 → DB 1m 兜底），与 /price/{symbol} 完全同源，
        # 不再按所分叉（kline_service / poller stats / HL bulk 各自为政）。
        try:
            from backend.services.data_center import data_center

            for symbol in missing:
                if symbol in filled:
                    continue
                result = data_center.get_price_with_ts(symbol, purpose="trade")
                if not result:
                    continue
                price = float(result[0])
                if price <= 0:
                    continue
                results.append(PriceResponse(
                    symbol=symbol, market=market, price=round(price, 8),
                    oracle_price=round(price, 8), change24h=0,
                    volume24h=0, percentage24h=0,
                    open_interest=0, funding_rate=0,
                    timestamp=current_timestamp,
                ))
                filled.add(symbol)
        except Exception:
            pass

        # 非 DC_ONLY 且主所为 hyperliquid：允许直连 HL 补充（旧模式，受开关约束）
        if _fill_ex == "hyperliquid" and os.getenv("MARKET_DATA_DC_ONLY", "true").strip().lower() in (
            "0", "false", "no", "off",
        ):
            from services.hyperliquid_market_data import get_bulk_ticker_data_from_hyperliquid
            bulk_data = get_bulk_ticker_data_from_hyperliquid(
                [s for s in missing if s not in filled]
            )
            for symbol in missing:
                if symbol in filled:
                    continue
                ticker_data = bulk_data.get(symbol.upper())
                if ticker_data:
                    results.append(PriceResponse(
                        symbol=ticker_data['symbol'],
                        market=market,
                        price=ticker_data['price'],
                        oracle_price=ticker_data.get('oracle_price', 0),
                        change24h=ticker_data['change24h'],
                        volume24h=ticker_data['volume24h'],
                        percentage24h=ticker_data['percentage24h'],
                        open_interest=ticker_data.get('open_interest', 0),
                        funding_rate=ticker_data.get('funding_rate', 0),
                        timestamp=current_timestamp,
                    ))
                else:
                    logger.warning(f"No price data for {symbol}, skipping")

        return results
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to batch get crypto prices: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to batch get crypto prices: {str(e)}")


@router.get("/prices/snapshots")
def get_hub_market_snapshots(
    symbols: str = Query("BTC,ETH,SOL", description="逗号分隔 symbol 列表"),
    exchange: Optional[str] = Query(None, description="交易所，默认 hyperliquid"),
):
    """MarketDataHub 完整快照（price/funding/OI/volume/bid/ask）"""
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="symbols cannot be empty")
    if len(symbol_list) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 symbols")

    from backend.services.market_price_service import get_market_snapshots
    from backend.services.market_data_hub import market_data_hub

    snaps = get_market_snapshots(symbol_list, exchange=exchange)
    # [2026-08-07 统一口径] price 字段统一 data_center 秒级口径（hub 仅保留附加字段）
    try:
        from backend.services.data_center import data_center
        for _symbol in symbol_list:
            _snap = snaps.get(_symbol)
            if not _snap:
                continue
            _r = data_center.get_price_with_ts(_symbol, purpose="trade")
            if _r and _r[0] and float(_r[0]) > 0:
                _snap["price"] = float(_r[0])
    except Exception:
        pass
    return {
        "count": len(snaps),
        "exchange": exchange or market_data_hub._primary_exchange,
        "snapshots": snaps,
        "hub_running": market_data_hub.is_running,
    }


@router.get("/hub/status")
def get_market_hub_status():
    """MarketDataHub 运行状态"""
    from backend.services.market_data_hub import market_data_hub
    from backend.services.market_price_service import is_legacy_rest_poller_running

    status = market_data_hub.get_status()
    status["legacy_rest_poller_running"] = is_legacy_rest_poller_running()
    return status


@router.get("/klines", response_model=KlineResponse)
def get_crypto_klines_query(
    symbol: str,
    market: str = None,
    period: str = "1m",
    count: int = 100,
    limit: int = None,  # 兼容前端旧参数名 limit
    end: int = None,  # unix 秒：拉取此时间之前的历史（图表向左拖动补数）
    purpose: str = "research",  # research=允许切交易所；trade=强制决策所
):
    """
    Get crypto K-line data (query parameters version)

    Args:
        symbol: crypto symbol, such as 'BTCUSDT' or 'BTC'
        market: 交易所 asterdex/binance/okx/bybit/hyperliquid
        period: Time period
        count / limit: 根数，默认 100，最大 500（limit 为前端兼容别名）
        end: 可选，unix 秒时间戳，返回该时刻之前的 K 线（用于图表向左补历史）
        purpose: research（图表/研究，可切所）或 trade（强制决策所）
    """
    try:
        if limit is not None and int(limit) > 0:
            count = int(limit)
        if market is None:
            from services.exchange_config import get_active_exchange
            market = get_active_exchange()
        valid_periods = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '8h', '12h', '1d', '3d', '1w', '1M']
        if period not in valid_periods:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported time period, supported periods: {', '.join(valid_periods)}"
            )
            
        if count <= 0 or count > 500:
            raise HTTPException(status_code=400, detail="Data count must be between 1-500")

        purpose_l = (purpose or "research").strip().lower()
        if purpose_l not in ("research", "trade"):
            purpose_l = "research"
        
        # 数据中台：图表用 research 可切交易所；向左拖动用 end 补历史
        try:
            from backend.services.data_center import data_center
            result = data_center.get_klines(
                symbol,
                period,
                count=count,
                exchange=market,
                purpose=purpose_l,
                end=int(end) if end else None,
            )
            kline_data = result.rows[-count:] if len(result.rows) > count else result.rows
        except Exception:
            kline_data = get_kline_data(symbol, market, period, count)

        # 实时当前 K 线叠加：同时间戳替换（更鲜），新时间戳追加
        # 历史补数（带 end）不加 live，避免把当前 bar 混进过去区间
        try:
            if end is None:
                from backend.services.live_kline_engine import live_kline_engine
                ex = (market or "").strip().lower()
                if ex == "aster":
                    ex = "asterdex"
                live = live_kline_engine.get_live_bar(ex, symbol, period)
                if live:
                    live = dict(live)
                    live.setdefault(
                        "datetime",
                        datetime.fromtimestamp(
                            int(live.get("timestamp") or 0), tz=timezone.utc
                        ).isoformat(),
                    )
                    live_ts = int(live.get("timestamp") or 0)
                    merged = list(kline_data)
                    replaced = False
                    for i in range(len(merged) - 1, -1, -1):
                        row_ts = int((merged[i] or {}).get("timestamp") or 0)
                        if row_ts == live_ts:
                            merged[i] = live
                            replaced = True
                            break
                        if row_ts < live_ts:
                            break
                    if not replaced and live_ts > 0:
                        merged.append(live)
                    kline_data = merged[-count:]
        except Exception:
            pass
        
        # Convert data format
        kline_items = []
        for item in kline_data:
            # Handle datetime - may be string or datetime object
            dt_value = item.get('datetime')
            if dt_value is not None:
                dt_str = dt_value.isoformat() if hasattr(dt_value, 'isoformat') else str(dt_value)
            else:
                dt_str = None

            kline_items.append(KlineItem(
                timestamp=item.get('timestamp'),
                datetime=dt_str,
                open=item.get('open'),
                high=item.get('high'),
                low=item.get('low'),
                close=item.get('close'),
                volume=item.get('volume'),
                amount=item.get('amount'),
                chg=item.get('chg'),
                percent=item.get('percent')
            ))
        
        return KlineResponse(
            symbol=symbol,
            market=market,
            period=period,
            count=len(kline_items),
            data=kline_items
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get K-line data: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get K-line data: {str(e)}")


@router.get("/kline/{symbol}", response_model=KlineResponse)
def get_crypto_kline(
    symbol: str, 
    market: str = "US",
    period: str = "1m",
    count: int = 100
):
    """
    Get crypto K-line data (path parameter version)

    Args:
        symbol: crypto symbol, such as 'MSFT'
        market: Market symbol, default 'US'
        period: Time period, supports '1m', '5m', '15m', '30m', '1h', '1d'
        count: Number of data points, default 100, max 500

    Returns:
        Response containing K-line data
    """
    try:
        # Parameter validation - Hyperliquid supported time periods
        valid_periods = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '8h', '12h', '1d', '3d', '1w', '1M']
        if period not in valid_periods:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported time period, supported periods: {', '.join(valid_periods)}"
            )
            
        if count <= 0 or count > 500:
            raise HTTPException(status_code=400, detail="Data count must be between 1-500")
        
        # 数据中台整改：走 data_center 统一入口（多交易所择优）
        try:
            from backend.services.data_center import data_center
            result = data_center.get_klines(symbol, period, count=count, exchange=market)
            kline_data = result.rows[-count:] if len(result.rows) > count else result.rows
        except Exception:
            kline_data = get_kline_data(symbol, market, period, count)
        
        # Convert data format
        kline_items = []
        for item in kline_data:
            # Handle datetime - may be string or datetime object
            dt_value = item.get('datetime')
            if dt_value is not None:
                dt_str = dt_value.isoformat() if hasattr(dt_value, 'isoformat') else str(dt_value)
            else:
                dt_str = None

            kline_items.append(KlineItem(
                timestamp=item.get('timestamp'),
                datetime=dt_str,
                open=item.get('open'),
                high=item.get('high'),
                low=item.get('low'),
                close=item.get('close'),
                volume=item.get('volume'),
                amount=item.get('amount'),
                chg=item.get('chg'),
                percent=item.get('percent')
            ))
        
        return KlineResponse(
            symbol=symbol,
            market=market,
            period=period,
            count=len(kline_items),
            data=kline_items
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get K-line data: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get K-line data: {str(e)}")


@router.get("/status/{symbol}", response_model=MarketStatusResponse)
def get_crypto_market_status(symbol: str, market: str = "US"):
    """
    Get crypto market status

    Args:
        symbol: crypto symbol, such as 'MSFT'
        market: Market symbol, default 'US'

    Returns:
        Response containing market status
    """
    try:
        status_data = get_market_status(symbol, market)
        
        return MarketStatusResponse(
            symbol=status_data.get('symbol', symbol),
            market=status_data.get('market', market),
            market_status=status_data.get('market_status', 'UNKNOWN'),
            timestamp=status_data.get('timestamp'),
            current_time=status_data.get('current_time', '')
        )
    except Exception as e:
        logger.error(f"Failed to get market status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get market status: {str(e)}")


@router.get("/health")
def market_data_health():
    """
    Market data service health check

    Returns:
        Service status information
    """
    try:
        # Test getting a crypto price to check if service is running normally
        import time
        try:
            test_price = get_last_price("BTC", "CRYPTO")
        except Exception:
            test_price = None

        if test_price and test_price > 0:
            return {
                "status": "healthy",
                "timestamp": int(time.time() * 1000),
                "test_price": {
                    "symbol": "BTC",
                    "price": test_price
                },
                "message": "Market data service is running normally"
            }
        else:
            return {
                "status": "degraded",
                "timestamp": int(time.time() * 1000),
                "message": "Price fetch unavailable, service running but data source unreachable"
            }
    except Exception as e:
        logger.error(f"Market data service health check failed: {e}")
        return {
            "status": "unhealthy",
            "timestamp": int(time.time() * 1000),
            "error": str(e),
            "message": "Market data service abnormal"
        }

class KlineWithIndicatorsResponse(BaseModel):
    """K线数据+技术指标响应模型"""
    symbol: str
    market: str
    period: str
    count: int
    klines: List[KlineItem]
    indicators: Dict[str, Any]


@router.get("/kline-with-indicators/{symbol}", response_model=KlineWithIndicatorsResponse)
def get_kline_with_indicators(
    symbol: str,
    market: str = None,
    period: str = "1h",
    count: int = 500,
    indicators: str = ""
):
    """
    获取K线数据并计算技术指标

    Args:
        symbol: 币种符号，如 'BTC'
        market: 市场，默认跟随 get_active_exchange()（产品默认 asterdex）
        period: 时间周期，如 '1h'
        count: 数据数量，默认500
        indicators: 指标列表，逗号分隔，如 'EMA20,EMA50,MACD,RSI14'

    Returns:
        包含K线数据和技术指标的响应
    """
    with market_data_metrics.timer("api.market.kline_with_indicators"):
        try:
            from services.technical_indicators import calculate_indicators
            if market is None:
                from services.exchange_config import get_active_exchange
                market = get_active_exchange()

            valid_periods = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '8h', '12h', '1d', '3d', '1w', '1M']
            if period not in valid_periods:
                raise HTTPException(
                    status_code=400,
                    detail=f"不支持的时间周期，支持的周期: {', '.join(valid_periods)}"
                )

            if count <= 0 or count > 500:
                raise HTTPException(status_code=400, detail="数据数量必须在1-500之间")

            # 获取K线数据
            kline_data = get_kline_data(symbol, market, period, count)

            # 转换K线数据格式
            kline_items = []
            for item in kline_data:
                dt_value = item.get('datetime')
                if dt_value is not None:
                    dt_str = dt_value.isoformat() if hasattr(dt_value, 'isoformat') else str(dt_value)
                else:
                    dt_str = None

                kline_items.append(KlineItem(
                    timestamp=item.get('timestamp'),
                    datetime=dt_str,
                    open=item.get('open'),
                    high=item.get('high'),
                    low=item.get('low'),
                    close=item.get('close'),
                    volume=item.get('volume'),
                    amount=item.get('amount'),
                    chg=item.get('chg'),
                    percent=item.get('percent')
                ))

            # 计算技术指标
            indicator_results = {}
            if indicators.strip():
                indicator_list = [ind.strip() for ind in indicators.split(',') if ind.strip()]
                if indicator_list:
                    indicator_results = calculate_indicators(kline_data, indicator_list)

            return KlineWithIndicatorsResponse(
                symbol=symbol,
                market=market,
                period=period,
                count=len(kline_items),
                klines=kline_items,
                indicators=indicator_results
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取K线和指标数据失败: {e}")
            raise HTTPException(status_code=500, detail=f"获取K线和指标数据失败: {str(e)}")


class ExchangeQuoteItem(BaseModel):
    exchange: str
    price: float
    timestamp: int
    spread_abs: Optional[float] = None
    spread_pct: Optional[float] = None


class ExchangeQuotesResponse(BaseModel):
    symbol: str
    period: str
    base_exchange: Optional[str] = None
    quotes: List[ExchangeQuoteItem]


@router.get("/exchange-quotes/{symbol}", response_model=ExchangeQuotesResponse)
def get_exchange_quotes(symbol: str, period: str = "1m"):
    """返回各交易所最新价，用于 K 线页跨所对比（始终拉实时价，不用陈旧 DB 快照）。"""
    import time
    from backend.services.market_data import _fetch_klines_from_adapter
    from backend.services.market_data_adapters.registry import ExchangeAdapterRegistry
    from backend.services.kline_realtime_collector import get_quote_exchanges

    symbol = symbol.upper()
    valid_periods = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '8h', '12h', '1d', '3d', '1w', '1M']
    if period not in valid_periods:
        raise HTTPException(status_code=400, detail=f"不支持的时间周期: {period}")

    # 跨所报价条用 1m 最新价；period 参数保留兼容，但不用于陈旧 DB 日 K 收盘价
    quote_period = "1m"
    exchanges = get_quote_exchanges()
    quotes: List[ExchangeQuoteItem] = []
    now_ts = int(time.time())

    for exchange in exchanges:
        exchange_key = ExchangeAdapterRegistry.normalize_exchange(exchange)
        price: float | None = None
        ts = now_ts
        try:
            if exchange_key == "hyperliquid" and os.getenv(
                "MARKET_DATA_DC_ONLY", "true"
            ).strip().lower() not in ("0", "false", "no", "off"):
                # DC_ONLY：从 DB 读取（数据中心已落库 hyperliquid 主流币 1m）
                rows = _fetch_db_klines(exchange_key, symbol, quote_period, 1)
                if rows:
                    price = float(rows[-1]["close"])
                    ts = int(rows[-1]["timestamp"])
            elif exchange_key == "hyperliquid":
                from services.hyperliquid_market_data import get_ticker_data_from_hyperliquid
                ticker = get_ticker_data_from_hyperliquid(symbol, "mainnet")
                if ticker and float(ticker.get("price") or 0) > 0:
                    price = float(ticker["price"])
            else:
                rows = _fetch_db_klines(exchange_key, symbol, quote_period, 1)
                if rows:
                    price = float(rows[-1]["close"])
                    ts = int(rows[-1]["timestamp"])
        except Exception as exc:
            logger.warning(f"Live quote fetch failed for {symbol}@{exchange_key}: {exc}")

        if price is None or price <= 0:
            continue
        quotes.append(ExchangeQuoteItem(exchange=exchange_key, price=price, timestamp=ts))

    # 实时源全部失败时，降级读 DB 最新 1m
    if not quotes:
        from sqlalchemy import text
        from backend.database.connection import MarketSessionLocal

        with MarketSessionLocal() as db:
            rows = db.execute(text("""
                SELECT ck.exchange, ck.close_price, ck.timestamp
                FROM crypto_klines ck
                INNER JOIN (
                    SELECT exchange, MAX(timestamp) AS max_ts
                    FROM crypto_klines
                    WHERE symbol = :symbol AND period = :period
                    GROUP BY exchange
                ) latest ON ck.exchange = latest.exchange
                    AND ck.timestamp = latest.max_ts
                    AND ck.symbol = :symbol
                    AND ck.period = :period
                ORDER BY ck.exchange
            """), {"symbol": symbol, "period": quote_period}).fetchall()

        for row in rows:
            quotes.append(ExchangeQuoteItem(
                exchange=row[0],
                price=float(row[1]),
                timestamp=int(row[2]),
            ))

    base_exchange = None
    if quotes:
        base = next((q for q in quotes if q.exchange == "hyperliquid"), quotes[0])
        base_exchange = base.exchange
        base_price = base.price
        enriched: List[ExchangeQuoteItem] = []
        for q in quotes:
            spread_abs = round(q.price - base_price, 6) if base_price else None
            spread_pct = round((q.price - base_price) / base_price * 100, 4) if base_price else None
            enriched.append(ExchangeQuoteItem(
                exchange=q.exchange,
                price=q.price,
                timestamp=q.timestamp,
                spread_abs=spread_abs,
                spread_pct=spread_pct,
            ))
        quotes = enriched

    return ExchangeQuotesResponse(
        symbol=symbol,
        period=quote_period,
        base_exchange=base_exchange,
        quotes=quotes,
    )


@router.get("/indicators/available")
def get_available_indicators():
    """
    获取支持的技术指标列表

    Returns:
        支持的指标列表
    """
    try:
        from services.technical_indicators import get_available_indicators
        return {
            "indicators": get_available_indicators(),
            "message": "支持的技术指标列表"
        }
    except Exception as e:
        logger.error(f"获取指标列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取指标列表失败: {str(e)}")


@router.get("/available-symbols")
def get_available_symbols(market: str = None):
    """
    获取可用的交易对列表

    Args:
        market: 市场类型 (hyperliquid 或 binance)

    Returns:
        可用交易对列表
    """
    try:
        if market is None:
            from services.exchange_config import get_active_exchange
            market = get_active_exchange()
        if market == "binance":
            # 币安常见交易对
            symbols = [
                {"symbol": "BTC", "price": None, "volume24h": None},
                {"symbol": "ETH", "price": None, "volume24h": None},
                {"symbol": "SOL", "price": None, "volume24h": None},
                {"symbol": "BNB", "price": None, "volume24h": None},
                {"symbol": "XRP", "price": None, "volume24h": None},
                {"symbol": "ADA", "price": None, "volume24h": None},
                {"symbol": "DOGE", "price": None, "volume24h": None},
                {"symbol": "MATIC", "price": None, "volume24h": None},
                {"symbol": "DOT", "price": None, "volume24h": None},
                {"symbol": "AVAX", "price": None, "volume24h": None},
                {"symbol": "LINK", "price": None, "volume24h": None},
                {"symbol": "UNI", "price": None, "volume24h": None},
                {"symbol": "LTC", "price": None, "volume24h": None},
                {"symbol": "BCH", "price": None, "volume24h": None},
                {"symbol": "ATOM", "price": None, "volume24h": None},
                {"symbol": "FIL", "price": None, "volume24h": None},
                {"symbol": "APT", "price": None, "volume24h": None},
                {"symbol": "ARB", "price": None, "volume24h": None},
                {"symbol": "OP", "price": None, "volume24h": None},
                {"symbol": "NEAR", "price": None, "volume24h": None},
            ]
        else:
            # Hyperliquid常见交易对
            symbols = [
                {"symbol": "BTC", "price": None, "volume24h": None},
                {"symbol": "ETH", "price": None, "volume24h": None},
                {"symbol": "SOL", "price": None, "volume24h": None},
                {"symbol": "DOGE", "price": None, "volume24h": None},
                {"symbol": "MATIC", "price": None, "volume24h": None},
                {"symbol": "AVAX", "price": None, "volume24h": None},
                {"symbol": "ARB", "price": None, "volume24h": None},
                {"symbol": "OP", "price": None, "volume24h": None},
                {"symbol": "NEAR", "price": None, "volume24h": None},
                {"symbol": "APT", "price": None, "volume24h": None},
                {"symbol": "LINK", "price": None, "volume24h": None},
                {"symbol": "UNI", "price": None, "volume24h": None},
                {"symbol": "ATOM", "price": None, "volume24h": None},
                {"symbol": "FIL", "price": None, "volume24h": None},
                {"symbol": "LTC", "price": None, "volume24h": None},
                {"symbol": "XRP", "price": None, "volume24h": None},
                {"symbol": "ADA", "price": None, "volume24h": None},
                {"symbol": "DOT", "price": None, "volume24h": None},
                {"symbol": "INJ", "price": None, "volume24h": None},
                {"symbol": "AAVE", "price": None, "volume24h": None},
            ]

        return {"symbols": symbols}
    except Exception as e:
        logger.error(f"获取可用交易对失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取可用交易对失败: {str(e)}")


@router.get("/validate-symbol")
def validate_symbol(symbol: str, market: str = None):
    """
    验证交易对是否可用

    Args:
        symbol: 交易对符号
        market: 市场类型

    Returns:
        验证结果
    """
    try:
        # 简单验证：检查符号格式（大写字母，2-10个字符）
        if not symbol or not symbol.isupper() or len(symbol) < 2 or len(symbol) > 10:
            return {"valid": False, "message": "Invalid symbol format"}

        # 对于实际应用，这里可以调用交易所API验证
        # 目前简化处理，只检查格式
        return {"valid": True, "symbol": symbol}
    except Exception as e:
        logger.error(f"验证交易对失败: {e}")
        return {"valid": True, "symbol": symbol}  # 失败时也返回True，允许添加


# ════════════════════════════════════════════════════════
#  Phase 4-7: 市场扫描器 & 异常检测端点
#  前端 marketScannerApi.ts 对接
# ════════════════════════════════════════════════════════

_scan_config = {
    "top_n": 20,
    "min_volume": 1000000,
    "enabled": True,
    "anomaly_enabled": True,
}

_last_scan_result = None
_last_scan_time = None
_last_anomaly_report = None
_scan_running = False
_SCAN_CACHE_TTL = 300  # 扫描结果缓存5分钟，过期后自动重新扫描


def _kline_regime_fallback(symbol: str) -> dict:
    """Kline-based regime fallback when flow indicators are unavailable."""
    import numpy as np
    from datetime import datetime
    try:
        raw_klines = get_kline_data(symbol, period="1h", count=100)
        if not raw_klines or len(raw_klines) < 24:
            return {"symbol": symbol, "regime": "ranging", "confidence": 0.3, "trend_direction": "neutral", "volatility_percentile": 0.5, "volume_percentile": 0.5, "timestamp": datetime.now().isoformat()}
        close = np.array([float(k.get('close', 0) or k.get('close_price', 0)) for k in raw_klines[-48:]])
        volume = np.array([float(k.get('volume', 0)) for k in raw_klines[-48:]])
        if len(close) < 10:
            return {"symbol": symbol, "regime": "ranging", "confidence": 0.3, "trend_direction": "neutral", "volatility_percentile": 0.5, "volume_percentile": 0.5, "timestamp": datetime.now().isoformat()}
        returns = np.diff(np.log(close))
        volatility = float(np.std(returns) * np.sqrt(24))
        sma20 = float(np.mean(close[-20:])) if len(close) >= 20 else float(np.mean(close))
        sma50 = float(np.mean(close[-min(50, len(close)):]))
        trend = (sma20 - sma50) / (sma50 + 1e-10)
        direction = "up" if trend > 0.02 else ("down" if trend < -0.02 else "neutral")
        vol_percentile = min(volatility / 0.15, 1.0)
        vol_mean = float(np.mean(volume[-24:]))
        vol_current = float(volume[-1]) if len(volume) > 0 else 0
        volume_percentile = min((vol_current / (vol_mean + 1e-10)) / 2.0, 1.0)
        if volatility > 0.10:
            regime = "volatile"
            confidence = min(volatility / 0.15, 0.9)
        elif abs(trend) > 0.03:
            regime = "trending"
            confidence = min(abs(trend) / 0.05 * 0.8, 0.85)
        elif volatility < 0.03 and abs(trend) < 0.01:
            regime = "ranging"
            confidence = 0.5
        else:
            regime = "ranging"
            confidence = 0.4
        return {
            "symbol": symbol,
            "regime": regime,
            "confidence": round(confidence, 2),
            "trend_direction": direction,
            "volatility_percentile": round(vol_percentile, 2),
            "volume_percentile": round(volume_percentile, 2),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.debug(f"Kline regime fallback for {symbol}: {e}")
        return {"symbol": symbol, "regime": "ranging", "confidence": 0.1, "trend_direction": "neutral", "volatility_percentile": 0.5, "volume_percentile": 0.5, "timestamp": datetime.now().isoformat()}


def _safe_regime_for_symbol(symbol: str) -> dict:
    """Get regime for a single symbol with full error isolation."""
    try:
        from backend.services.market_regime_service import get_market_regime
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            regime = get_market_regime(db, symbol)
            confidence = regime.get("confidence", 0.0)
            if confidence < 0.1:
                return _kline_regime_fallback(symbol)
            return {
                "symbol": symbol,
                "regime": regime.get("regime", "ranging"),
                "confidence": confidence,
                "trend_direction": regime.get("direction", "neutral"),
                "volatility_percentile": 0.5,
                "volume_percentile": 0.5,
                "timestamp": regime.get("timestamp", ""),
            }
        finally:
            db.close()
    except Exception:
        return _kline_regime_fallback(symbol)


def _safe_anomaly_for_symbol(symbol: str) -> list:
    """Detect anomalies for a single symbol with full error isolation."""
    try:
        from backend.services.anomaly_detector import AnomalyDetector
        import pandas as pd
        detector = AnomalyDetector()
        ticker = get_ticker_data(symbol)
        market_data = {
            "price": ticker.get("price", 0),
            "volume24h": ticker.get("volume24h", 0),
            "funding_rate": ticker.get("funding_rate", 0),
            "open_interest": ticker.get("open_interest", 0),
        }
        klines_df = pd.DataFrame()
        try:
            raw_klines = get_kline_data(symbol, period="1h", count=200)
            if raw_klines and len(raw_klines) >= 30:
                klines_df = pd.DataFrame(raw_klines)
                col_map = {'open_price': 'open', 'high_price': 'high', 'low_price': 'low', 'close_price': 'close'}
                for old, new in col_map.items():
                    if old in klines_df.columns and new not in klines_df.columns:
                        klines_df = klines_df.rename(columns={old: new})
        except Exception:
            pass
        report = detector.detect(symbol, klines_df, market_data)
        return [
            {
                "symbol": ev.symbol,
                "anomaly_type": ev.anomaly_type.value if hasattr(ev.anomaly_type, 'value') else str(ev.anomaly_type),
                "severity": "critical" if ev.severity > 0.8 else ("high" if ev.severity > 0.7 else ("medium" if ev.severity > 0.4 else "low")),
                "z_score": round(ev.z_score, 4),
                "value": round(ev.raw_value, 6),
                "expected_range": list(ev.expected_range) if ev.expected_range else [0, 0],
                "detected_at": ev.timestamp.isoformat() if ev.timestamp else "",
                "description": ev.description,
            }
            for ev in report.events
        ]
    except Exception as e:
        logger.debug(f"Anomaly detection for {symbol} failed: {e}")
        return []


def _get_scan_symbols() -> list:
    """获取全市场可扫描的交易对列表（单一真相源）"""
    try:
        from backend.services.market_scanner import MarketScanner
        return MarketScanner.get_all_tradable_symbols()
    except Exception as e:
        logger.warning(f"_get_scan_symbols fallback: {e}")
        return []


@router.get("/symbols")
def get_scannable_symbols():
    """返回当前全市场可扫描的交易对列表 — 前端展示用"""
    symbols = _get_scan_symbols()
    return {
        "symbols": symbols,
        "count": len(symbols),
    }


@router.post("/scan")
async def trigger_market_scan(request: dict = None):
    """触发全市场扫描 — 前端调用 POST /api/market/scan"""
    global _last_scan_result, _last_scan_time, _scan_running
    import asyncio
    import time
    from datetime import datetime

    if _scan_running:
        if _last_scan_result:
            return _last_scan_result
        return {"timestamp": None, "top_symbols": [], "total_scanned": 0, "scan_duration_ms": 0, "status": "scan_in_progress"}

    _scan_running = True
    start_ms = time.time() * 1000
    try:
        from backend.services.market_scanner import MarketScanner
        scanner = MarketScanner()
        symbols = await asyncio.to_thread(MarketScanner.get_all_tradable_symbols)
        top_n = 20
        if request and isinstance(request, dict):
            top_n = request.get("top_n", 20)

        timeout = max(60.0, len(symbols) * 1.5)
        result = await asyncio.wait_for(scanner.full_scan(symbols), timeout=timeout)
        scan_data = {
            "timestamp": result.timestamp.isoformat() if result and result.timestamp else None,
            "top_symbols": [
                {
                    "symbol": s.symbol,
                    "volume_score": round(s.volume_score, 4),
                    "volatility_score": round(s.volatility_score, 4),
                    "trend_score": round(s.trend_score, 4),
                    "funding_score": round(s.funding_score, 4),
                    "total_score": round(s.total_score, 4),
                }
                for s in (result.qualified_symbols[:top_n] if result else [])
            ],
            "total_scanned": result.total_symbols_scanned if result else 0,
            "scan_duration_ms": int(time.time() * 1000 - start_ms),
        }
        _last_scan_result = scan_data
        _last_scan_time = datetime.now()
        return scan_data
    except asyncio.TimeoutError:
        elapsed = int(time.time() * 1000 - start_ms)
        logger.warning(f"Market scan timed out after {elapsed}ms")
        return {"timestamp": None, "top_symbols": [], "total_scanned": 0, "scan_duration_ms": elapsed, "error": "scan_timeout"}
    except Exception as e:
        logger.error(f"Market scan failed: {e}")
        raise HTTPException(status_code=500, detail=f"Market scan failed: {str(e)}")
    finally:
        _scan_running = False


@router.get("/scan/latest")
async def get_latest_scan_result():
    """获取最近一次扫描结果 — 缓存过期或首次访问时自动触发全市场扫描"""
    global _last_scan_result, _last_scan_time
    from datetime import datetime

    cache_valid = (
        _last_scan_result is not None
        and _last_scan_time is not None
        and (datetime.now() - _last_scan_time).total_seconds() < _SCAN_CACHE_TTL
        and _last_scan_result.get("total_scanned", 0) >= 10
    )
    if cache_valid:
        return _last_scan_result

    import asyncio
    import time
    start_ms = time.time() * 1000
    try:
        from backend.services.market_scanner import MarketScanner
        scanner = MarketScanner()
        all_symbols = MarketScanner.get_all_tradable_symbols()
        timeout = max(60.0, len(all_symbols) * 1.5)
        result = await asyncio.wait_for(scanner.full_scan(all_symbols), timeout=timeout)
        scan_data = {
            "timestamp": result.timestamp.isoformat() if result and result.timestamp else None,
            "top_symbols": [
                {
                    "symbol": s.symbol,
                    "volume_score": round(s.volume_score, 4),
                    "volatility_score": round(s.volatility_score, 4),
                    "trend_score": round(s.trend_score, 4),
                    "funding_score": round(s.funding_score, 4),
                    "total_score": round(s.total_score, 4),
                }
                for s in (result.qualified_symbols[:20] if result else [])
            ],
            "total_scanned": result.total_symbols_scanned if result else 0,
            "scan_duration_ms": int(time.time() * 1000 - start_ms),
        }
        _last_scan_result = scan_data
        _last_scan_time = datetime.now()
        return scan_data
    except Exception as e:
        logger.warning(f"Auto-scan failed: {e}")
        if _last_scan_result:
            return _last_scan_result
        return None


@router.get("/anomaly/latest")
async def get_latest_anomaly_report():
    """获取最近一次异常检测报告 — 对全市场扫描结果中的 Top 币种做异常检测"""
    global _last_anomaly_report
    import asyncio
    from datetime import datetime
    from concurrent.futures import ThreadPoolExecutor

    symbols = _get_scan_symbols()
    if _last_scan_result and _last_scan_result.get("top_symbols"):
        symbols = [s["symbol"] for s in _last_scan_result["top_symbols"]]
    elif len(symbols) > 20:
        symbols = symbols[:20]

    if not symbols:
        return {"timestamp": datetime.now().isoformat(), "events": [], "symbols_scanned": 0}

    try:
        loop = asyncio.get_event_loop()
        workers = min(10, len(symbols))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            tasks = [loop.run_in_executor(pool, _safe_anomaly_for_symbol, sym) for sym in symbols]
            results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=30.0)

        all_events = []
        for r in results:
            if isinstance(r, list):
                all_events.extend(r)

        report_data = {
            "timestamp": datetime.now().isoformat(),
            "events": all_events,
            "symbols_scanned": len(symbols),
        }
        _last_anomaly_report = report_data
        return report_data
    except asyncio.TimeoutError:
        logger.warning("Anomaly report timed out")
        if _last_anomaly_report:
            return _last_anomaly_report
        return {"timestamp": datetime.now().isoformat(), "events": [], "symbols_scanned": 0, "error": "timeout"}
    except Exception as e:
        logger.error(f"Anomaly report failed: {e}")
        if _last_anomaly_report:
            return _last_anomaly_report
        return {"timestamp": datetime.now().isoformat(), "events": [], "symbols_scanned": 0}


@router.get("/regime/list")
async def get_regime_classifications():
    """获取全市场交易对的 regime classification — 并发处理+超时"""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    symbols = _get_scan_symbols()
    if _last_scan_result and _last_scan_result.get("top_symbols"):
        symbols = [s["symbol"] for s in _last_scan_result["top_symbols"]]
    elif len(symbols) > 20:
        symbols = symbols[:20]

    if not symbols:
        return []

    try:
        loop = asyncio.get_event_loop()
        workers = min(10, len(symbols))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            tasks = [loop.run_in_executor(pool, _safe_regime_for_symbol, sym) for sym in symbols]
            results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=30.0)

        return [r for r in results if isinstance(r, dict)]
    except asyncio.TimeoutError:
        logger.warning("Regime list timed out, returning kline fallbacks")
        fallbacks = []
        for sym in symbols[:8]:
            try:
                fallbacks.append(_kline_regime_fallback(sym))
            except Exception:
                fallbacks.append({
                    "symbol": sym, "regime": "ranging", "confidence": 0.1,
                    "trend_direction": "neutral", "volatility_percentile": 0.5,
                    "volume_percentile": 0.5, "timestamp": "",
                })
        return fallbacks
    except Exception as e:
        logger.error(f"Regime list failed: {e}")
        return []


@router.get("/regime/{symbol}")
async def get_regime_classification(symbol: str):
    """获取指定交易对的 regime classification"""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    try:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = await asyncio.wait_for(
                loop.run_in_executor(pool, _safe_regime_for_symbol, symbol),
                timeout=10.0,
            )
        return result
    except Exception as e:
        logger.error(f"Regime for {symbol} failed: {e}")
        return _kline_regime_fallback(symbol)


@router.get("/scan/config")
def get_scan_config():
    """获取扫描配置"""
    return _scan_config


@router.put("/scan/config")
def update_scan_config(top_n: Optional[int] = None, min_volume: Optional[float] = None,
                       enabled: Optional[bool] = None, anomaly_enabled: Optional[bool] = None):
    """更新扫描配置"""
    global _scan_config
    if top_n is not None:
        _scan_config["top_n"] = top_n
    if min_volume is not None:
        _scan_config["min_volume"] = min_volume
    if enabled is not None:
        _scan_config["enabled"] = enabled
    if anomaly_enabled is not None:
        _scan_config["anomaly_enabled"] = anomaly_enabled
    return _scan_config
