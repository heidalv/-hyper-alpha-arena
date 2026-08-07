#!/usr/bin/env python3
"""
Market Flow Indicators Service for AI Prompt Variables

Provides aggregated market flow data formatted for AI prompt injection.
Unlike the chart API which returns time series, this returns:
- Current value
- Last N period values
- Relevant context (e.g., averages for comparison)

Supported variables:
- {SYMBOL}_CVD_{PERIOD} - Cumulative Volume Delta
- {SYMBOL}_TAKER_{PERIOD} - Taker Buy/Sell Volume and Ratio
- {SYMBOL}_OI_{PERIOD} - Open Interest
- {SYMBOL}_FUNDING_{PERIOD} - Funding Rate
- {SYMBOL}_DEPTH_{PERIOD} - Order Book Depth Ratio
"""

import logging
from decimal import Decimal
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.database.models import (
    MarketTradesAggregated,
    MarketOrderbookSnapshots,
    MarketAssetMetrics
)

logger = logging.getLogger(__name__)

# Timeframe to milliseconds mapping
# Fix 15c: 新增 1d/8h/12h 周期支持（原不含 → 1d 衍生品查询报 Unsupported period）
TIMEFRAME_MS = {
    "1m": 60 * 1000,
    "3m": 3 * 60 * 1000,
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "2h": 2 * 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "8h": 8 * 60 * 60 * 1000,
    "12h": 12 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}


def floor_timestamp(ts_ms: int, interval_ms: int) -> int:
    """Floor timestamp to interval boundary"""
    return (ts_ms // interval_ms) * interval_ms


def decimal_to_float(val) -> Optional[float]:
    """Convert Decimal to float, handling None"""
    if val is None:
        return None
    return float(val)


def format_volume(value: float) -> str:
    """Format volume with appropriate unit (K, M, B)"""
    abs_val = abs(value)
    sign = "+" if value >= 0 else "-"
    if abs_val >= 1_000_000_000:
        return f"{sign}${abs_val/1_000_000_000:.2f}B"
    elif abs_val >= 1_000_000:
        return f"{sign}${abs_val/1_000_000:.2f}M"
    elif abs_val >= 1_000:
        return f"{sign}${abs_val/1_000:.2f}K"
    else:
        return f"{sign}${abs_val:.2f}"


def _get_market_db():
    """创建独立的 MarketSessionLocal 会话，用于查询市场数据表。

    所有市场数据表（MarketTradesAggregated, MarketAssetMetrics, MarketOrderbookSnapshots）
    只存在于 alpha_market 数据库。使用独立会话确保：
    1. 总是连接正确的数据库
    2. 查询失败不会污染调用者的 session
    """
    from backend.database.connection import MarketSessionLocal
    return MarketSessionLocal()


def get_indicator_value(
    db: Session,
    symbol: str,
    indicator: str,
    period: str,
    current_time_ms: Optional[int] = None
) -> Optional[float]:
    """
    Get a single indicator's current value for signal detection.

    This is the canonical function for retrieving indicator values.
    Use this for signal detection, alerts, and any feature that needs
    a single numeric value.

    Args:
        db: Database session (IGNORED — always uses MarketSessionLocal internally)
        symbol: Trading symbol (e.g., "BTC")
        indicator: Indicator type - one of:
            - "OI_DELTA": Open Interest change percentage
            - "CVD": Cumulative Volume Delta
            - "DEPTH": Order book depth ratio (bid/ask)
            - "IMBALANCE": Order book imbalance (-1 to 1)
            - "TAKER": Taker buy/sell ratio
        period: Time period (e.g., "1m", "5m", "15m", "1h")
        current_time_ms: Current timestamp in ms (defaults to now)

    Returns:
        Current value as float, or None if data unavailable
    """
    if period not in TIMEFRAME_MS:
        logger.warning(f"Unsupported period: {period}")
        return None

    interval_ms = TIMEFRAME_MS[period]

    if current_time_ms is None:
        from datetime import datetime, timezone
        current_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    indicator_upper = indicator.upper()

    _own_db = _get_market_db()
    try:
        if indicator_upper == "OI_DELTA":
            data = _get_oi_delta_data(_own_db, symbol, period, interval_ms, current_time_ms)
            return data.get("current") if data else None
        elif indicator_upper == "CVD":
            data = _get_cvd_data(_own_db, symbol, period, interval_ms, current_time_ms)
            return data.get("current") if data else None
        elif indicator_upper == "DEPTH":
            data = _get_depth_data(_own_db, symbol, period, interval_ms, current_time_ms)
            return data.get("ratio") if data else None
        elif indicator_upper == "IMBALANCE":
            data = _get_imbalance_data(_own_db, symbol, period, interval_ms, current_time_ms)
            return data.get("current") if data else None
        elif indicator_upper == "TAKER":
            data = _get_taker_data(_own_db, symbol, period, interval_ms, current_time_ms)
            return data.get("ratio") if data else None
        elif indicator_upper == "OI":
            data = _get_oi_data(_own_db, symbol, period, interval_ms, current_time_ms)
            return data.get("current") if data else None
        elif indicator_upper == "FUNDING":
            data = _get_funding_data(_own_db, symbol, period, interval_ms, current_time_ms)
            return data.get("current") if data else None
        else:
            logger.warning(f"Unknown indicator: {indicator}")
            return None
    except Exception as e:
        logger.error(f"Error getting indicator {indicator} for {symbol}: {e}")
        return None
    finally:
        try:
            _own_db.close()
        except Exception:
            pass


def get_flow_indicators_for_prompt(
    db: Session,
    symbol: str,
    period: str,
    indicators: List[str],
    current_time_ms: Optional[int] = None,
    exchange: Optional[str] = None  # 新增：按交易所过滤
) -> Dict[str, Any]:
    """
    Get market flow indicator data formatted for AI prompt injection.

    Args:
        db: Database session (IGNORED — always uses MarketSessionLocal internally)
        symbol: Trading symbol (e.g., "BTC")
        period: Time period (e.g., "15m", "1h")
        indicators: List of indicators to calculate ["CVD", "TAKER", "OI", "FUNDING", "DEPTH"]
        current_time_ms: Current timestamp in ms (defaults to now)
        exchange: 可选，指定交易所（如 'hyperliquid' / 'asterdex'）。None 则跨所聚合。

    Returns:
        Dict with indicator name as key and raw data dict as value
    """
    if period not in TIMEFRAME_MS:
        logger.warning(f"Unsupported period: {period}")
        return {}

    interval_ms = TIMEFRAME_MS[period]

    if current_time_ms is None:
        from datetime import datetime, timezone
        current_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    results = {}
    _own_db = _get_market_db()

    try:
        for indicator in indicators:
            indicator_upper = indicator.upper()
            try:
                if indicator_upper == "CVD":
                    results["CVD"] = _get_cvd_data(_own_db, symbol, period, interval_ms, current_time_ms, exchange)
                elif indicator_upper == "TAKER":
                    results["TAKER"] = _get_taker_data(_own_db, symbol, period, interval_ms, current_time_ms, exchange)
                elif indicator_upper == "OI":
                    results["OI"] = _get_oi_data(_own_db, symbol, period, interval_ms, current_time_ms, exchange)
                elif indicator_upper == "OI_DELTA":
                    results["OI_DELTA"] = _get_oi_delta_data(_own_db, symbol, period, interval_ms, current_time_ms, exchange)
                elif indicator_upper == "FUNDING":
                    results["FUNDING"] = _get_funding_data(_own_db, symbol, period, interval_ms, current_time_ms, exchange)
                elif indicator_upper == "DEPTH":
                    results["DEPTH"] = _get_depth_data(_own_db, symbol, period, interval_ms, current_time_ms, exchange)
                elif indicator_upper == "IMBALANCE":
                    results["IMBALANCE"] = _get_imbalance_data(_own_db, symbol, period, interval_ms, current_time_ms, exchange)
                else:
                    logger.warning(f"Unknown flow indicator: {indicator}")
            except Exception as e:
                logger.error(f"Error calculating flow indicator {indicator}: {e}")
                results[indicator_upper] = None
    finally:
        try:
            _own_db.close()
        except Exception:
            pass

    return results


def _get_cvd_data(
    db: Session, symbol: str, period: str, interval_ms: int, current_time_ms: int,
    exchange: Optional[str] = None  # 新增参数：按交易所过滤（None=跨所聚合）
) -> Optional[Dict[str, Any]]:
    """
    Get CVD (Cumulative Volume Delta) data.

    CVD = Cumulative(Taker Buy Notional - Taker Sell Notional)
    
    Args:
        exchange: 可选，指定交易所（如 'hyperliquid' / 'asterdex'）。None 则跨所聚合。
    """
    lookback_ms = interval_ms * 10
    start_time = current_time_ms - lookback_ms

    query = db.query(
        MarketTradesAggregated.timestamp,
        MarketTradesAggregated.taker_buy_notional,
        MarketTradesAggregated.taker_sell_notional
    ).filter(
        MarketTradesAggregated.symbol == symbol.upper(),
        MarketTradesAggregated.timestamp >= start_time,
        MarketTradesAggregated.timestamp <= current_time_ms
    )
    
    if exchange:
        query = query.filter(MarketTradesAggregated.exchange == exchange.lower())
    
    records = query.order_by(MarketTradesAggregated.timestamp).all()

    if not records:
        from datetime import datetime
        logger.warning(
            f"CVD insufficient data: symbol={symbol}, period={period}, "
            f"query_range=[{datetime.utcfromtimestamp(start_time/1000)} - "
            f"{datetime.utcfromtimestamp(current_time_ms/1000)}], records_found=0"
        )
        return None

    # Aggregate by period
    buckets = {}
    for ts, buy_notional, sell_notional in records:
        bucket_ts = floor_timestamp(ts, interval_ms)
        if bucket_ts not in buckets:
            buckets[bucket_ts] = {"buy": Decimal("0"), "sell": Decimal("0")}
        buckets[bucket_ts]["buy"] += buy_notional or Decimal("0")
        buckets[bucket_ts]["sell"] += sell_notional or Decimal("0")

    # Calculate CVD for each period
    sorted_times = sorted(buckets.keys())
    period_deltas = []

    for ts in sorted_times:
        bucket = buckets[ts]
        delta = float(bucket["buy"] - bucket["sell"])
        period_deltas.append(delta)

    if not period_deltas:
        from datetime import datetime
        logger.warning(
            f"CVD insufficient data: symbol={symbol}, period={period}, "
            f"records_found={len(records)}, buckets=0"
        )
        return None

    last_5 = period_deltas[-5:] if len(period_deltas) >= 5 else period_deltas
    current_delta = period_deltas[-1]
    cumulative = sum(period_deltas)

    return {
        "current": current_delta,
        "last_5": last_5,
        "cumulative": cumulative,
        "period": period
    }


def _get_taker_data(
    db: Session, symbol: str, period: str, interval_ms: int, current_time_ms: int,
    exchange: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Get Taker Buy/Sell Volume data.

    Returns buy volume, sell volume, and buy/sell ratio.
    """
    lookback_ms = interval_ms * 10
    start_time = current_time_ms - lookback_ms

    query = db.query(
        MarketTradesAggregated.timestamp,
        MarketTradesAggregated.taker_buy_notional,
        MarketTradesAggregated.taker_sell_notional
    ).filter(
        MarketTradesAggregated.symbol == symbol.upper(),
        MarketTradesAggregated.timestamp >= start_time,
        MarketTradesAggregated.timestamp <= current_time_ms
    )
    
    if exchange:
        query = query.filter(MarketTradesAggregated.exchange == exchange.lower())
    
    records = query.order_by(MarketTradesAggregated.timestamp).all()

    if not records:
        return None

    # Aggregate by period
    buckets = {}
    for ts, buy_notional, sell_notional in records:
        bucket_ts = floor_timestamp(ts, interval_ms)
        if bucket_ts not in buckets:
            buckets[bucket_ts] = {"buy": Decimal("0"), "sell": Decimal("0")}
        buckets[bucket_ts]["buy"] += buy_notional or Decimal("0")
        buckets[bucket_ts]["sell"] += sell_notional or Decimal("0")

    sorted_times = sorted(buckets.keys())
    ratios = []
    volumes = []

    for ts in sorted_times:
        bucket = buckets[ts]
        buy = float(bucket["buy"])
        sell = float(bucket["sell"])
        ratio = buy / sell if sell > 0 else 1.0
        ratios.append(ratio)
        volumes.append(buy + sell)

    if not ratios:
        return None

    # Current period data
    current_bucket = buckets[sorted_times[-1]]
    current_buy = float(current_bucket["buy"])
    current_sell = float(current_bucket["sell"])
    current_ratio = current_buy / current_sell if current_sell > 0 else 1.0

    last_5_ratios = ratios[-5:] if len(ratios) >= 5 else ratios
    last_5_volumes = volumes[-5:] if len(volumes) >= 5 else volumes

    return {
        "buy": current_buy,
        "sell": current_sell,
        "ratio": current_ratio,
        "ratio_last_5": last_5_ratios,
        "volume_last_5": last_5_volumes,
        "period": period
    }


def _get_oi_data(
    db: Session, symbol: str, period: str, interval_ms: int, current_time_ms: int,
    exchange: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Get Open Interest absolute value data.

    Returns current OI and last 5 values.
    """
    lookback_ms = interval_ms * 10
    start_time = current_time_ms - lookback_ms

    records = db.query(
        MarketAssetMetrics.timestamp,
        MarketAssetMetrics.open_interest
    ).filter(
        MarketAssetMetrics.symbol == symbol.upper(),
        MarketAssetMetrics.timestamp >= start_time,
        MarketAssetMetrics.timestamp <= current_time_ms
    ).order_by(MarketAssetMetrics.timestamp).all()

    if not records:
        logger.debug(
            f"OI no data: symbol={symbol}, period={period}, records=0"
        )
        return None

    buckets = {}
    for ts, oi in records:
        bucket_ts = floor_timestamp(ts, interval_ms)
        buckets[bucket_ts] = oi

    sorted_times = sorted(buckets.keys())
    if not sorted_times:
        logger.debug(
            f"OI no buckets: symbol={symbol}, period={period}, records={len(records)}"
        )
        return None

    oi_values = [decimal_to_float(buckets[ts]) for ts in sorted_times]
    oi_values = [v for v in oi_values if v is not None]

    if not oi_values:
        logger.debug(
            f"OI no valid values: symbol={symbol}, period={period}, buckets={len(sorted_times)}"
        )
        return None

    current_oi = oi_values[-1]
    last_5 = oi_values[-5:] if len(oi_values) >= 5 else oi_values

    return {
        "current": current_oi,
        "last_5": last_5,
        "period": period
    }


def _get_oi_delta_data(
    db: Session, symbol: str, period: str, interval_ms: int, current_time_ms: int,
    exchange: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Get Open Interest Delta (change percentage) data.

    Returns current OI change % and last 5 changes.
    """
    lookback_ms = interval_ms * 10
    start_time = current_time_ms - lookback_ms

    records = db.query(
        MarketAssetMetrics.timestamp,
        MarketAssetMetrics.open_interest
    ).filter(
        MarketAssetMetrics.symbol == symbol.upper(),
        MarketAssetMetrics.timestamp >= start_time,
        MarketAssetMetrics.timestamp <= current_time_ms
    ).order_by(MarketAssetMetrics.timestamp).all()

    if not records:
        logger.debug(f"OI_DELTA no data: symbol={symbol}, period={period}")
        return None

    buckets = {}
    for ts, oi in records:
        bucket_ts = floor_timestamp(ts, interval_ms)
        buckets[bucket_ts] = oi

    sorted_times = sorted(buckets.keys())
    if len(sorted_times) < 2:
        logger.debug(
            f"OI_DELTA need 2+ buckets: symbol={symbol}, period={period}, got={len(sorted_times)}"
        )
        return None

    oi_values = [decimal_to_float(buckets[ts]) for ts in sorted_times]
    oi_changes = []
    for i in range(1, len(oi_values)):
        if oi_values[i] and oi_values[i-1] and oi_values[i-1] != 0:
            change_pct = ((oi_values[i] - oi_values[i-1]) / oi_values[i-1]) * 100
            oi_changes.append(change_pct)

    if not oi_changes:
        logger.debug(
            f"OI_DELTA no valid changes: symbol={symbol}, period={period}"
        )
        return None

    current_change = oi_changes[-1]
    last_5 = oi_changes[-5:] if len(oi_changes) >= 5 else oi_changes

    return {
        "current": current_change,
        "last_5": last_5,
        "period": period
    }


def _get_funding_data(
    db: Session, symbol: str, period: str, interval_ms: int, current_time_ms: int,
    exchange: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Get Funding Rate data.

    Returns current funding rate and last 5 values.
    """
    lookback_ms = interval_ms * 10
    start_time = current_time_ms - lookback_ms

    records = db.query(
        MarketAssetMetrics.timestamp,
        MarketAssetMetrics.funding_rate
    ).filter(
        MarketAssetMetrics.symbol == symbol.upper(),
        MarketAssetMetrics.timestamp >= start_time,
        MarketAssetMetrics.timestamp <= current_time_ms
    ).order_by(MarketAssetMetrics.timestamp).all()

    if not records:
        return None

    # Aggregate by period - take last value in each bucket
    buckets = {}
    for ts, funding in records:
        bucket_ts = floor_timestamp(ts, interval_ms)
        buckets[bucket_ts] = funding

    sorted_times = sorted(buckets.keys())
    if not sorted_times:
        return None

    # Get funding rate values
    # [2026-07-10 资金费率修复] 原 float(fr) * 100 是单位错误：DB 里 funding_rate
    # 已是小数形式（0.0000125 = 0.00125%），再 ×100 得 0.00125 被下游当成费率小数，
    # 导致 derivatives_analytics 的风控阈值(>0.001 算极端)误判、且放大真实值 100 倍。
    # 改为返回原始小数，与 Hyperliquid API 原值及风控阈值口径一致。
    funding_values = []
    for ts in sorted_times:
        fr = buckets[ts]
        if fr is not None:
            funding_values.append(float(fr))

    if not funding_values:
        return None

    current_funding = funding_values[-1]
    last_5 = funding_values[-5:] if len(funding_values) >= 5 else funding_values

    # Calculate annualized rate (assuming 8-hour funding periods, 3 per day)
    annualized = current_funding * 3 * 365

    return {
        "current": current_funding,
        "last_5": last_5,
        "annualized": annualized,
        "period": period
    }


def _get_depth_data(
    db: Session, symbol: str, period: str, interval_ms: int, current_time_ms: int,
    exchange: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Get Order Book Depth data.

    Returns bid/ask depth ratio and last 5 values.
    """
    lookback_ms = interval_ms * 10
    start_time = current_time_ms - lookback_ms

    records = db.query(
        MarketOrderbookSnapshots.timestamp,
        MarketOrderbookSnapshots.bid_depth_5,
        MarketOrderbookSnapshots.ask_depth_5,
        MarketOrderbookSnapshots.spread
    ).filter(
        MarketOrderbookSnapshots.symbol == symbol.upper(),
        MarketOrderbookSnapshots.timestamp >= start_time,
        MarketOrderbookSnapshots.timestamp <= current_time_ms
    ).order_by(MarketOrderbookSnapshots.timestamp).all()

    if not records:
        return None

    # Aggregate by period - take last value in each bucket
    buckets = {}
    for ts, bid_depth, ask_depth, spread in records:
        bucket_ts = floor_timestamp(ts, interval_ms)
        buckets[bucket_ts] = {
            "bid": bid_depth,
            "ask": ask_depth,
            "spread": spread
        }

    sorted_times = sorted(buckets.keys())
    if not sorted_times:
        return None

    # Calculate depth ratios
    ratios = []
    for ts in sorted_times:
        bucket = buckets[ts]
        bid = decimal_to_float(bucket["bid"]) or 0
        ask = decimal_to_float(bucket["ask"]) or 0
        ratio = bid / ask if ask > 0 else 1.0
        ratios.append(ratio)

    current_bucket = buckets[sorted_times[-1]]
    current_bid = decimal_to_float(current_bucket["bid"]) or 0
    current_ask = decimal_to_float(current_bucket["ask"]) or 0
    current_ratio = current_bid / current_ask if current_ask > 0 else 1.0
    current_spread = decimal_to_float(current_bucket["spread"])

    last_5_ratios = ratios[-5:] if len(ratios) >= 5 else ratios

    return {
        "bid": current_bid,
        "ask": current_ask,
        "ratio": current_ratio,
        "ratio_last_5": last_5_ratios,
        "spread": current_spread,
        "period": period
    }


def _get_imbalance_data(
    db: Session, symbol: str, period: str, interval_ms: int, current_time_ms: int,
    exchange: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Get Order Book Imbalance data.

    Imbalance = (Bid - Ask) / (Bid + Ask), range -1 to 1
    Positive = more bid support, Negative = more ask pressure
    """
    lookback_ms = interval_ms * 10
    start_time = current_time_ms - lookback_ms

    records = db.query(
        MarketOrderbookSnapshots.timestamp,
        MarketOrderbookSnapshots.bid_depth_5,
        MarketOrderbookSnapshots.ask_depth_5
    ).filter(
        MarketOrderbookSnapshots.symbol == symbol.upper(),
        MarketOrderbookSnapshots.timestamp >= start_time,
        MarketOrderbookSnapshots.timestamp <= current_time_ms
    ).order_by(MarketOrderbookSnapshots.timestamp).all()

    if not records:
        return None

    # Aggregate by period - take last value in each bucket
    buckets = {}
    for ts, bid_depth, ask_depth in records:
        bucket_ts = floor_timestamp(ts, interval_ms)
        buckets[bucket_ts] = {"bid": bid_depth, "ask": ask_depth}

    sorted_times = sorted(buckets.keys())
    if not sorted_times:
        return None

    # Calculate imbalance values
    imbalances = []
    for ts in sorted_times:
        bucket = buckets[ts]
        bid = decimal_to_float(bucket["bid"]) or 0
        ask = decimal_to_float(bucket["ask"]) or 0
        total = bid + ask
        imbalance = (bid - ask) / total if total > 0 else 0.0
        imbalances.append(imbalance)

    current_imbalance = imbalances[-1]
    last_5 = imbalances[-5:] if len(imbalances) >= 5 else imbalances

    return {
        "current": current_imbalance,
        "last_5": last_5,
        "period": period
    }
