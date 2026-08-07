"""Compare v2 raw events with existing crypto_klines rows."""

from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any

from sqlalchemy import bindparam, text

from backend.database.connection import MarketSessionLocal
from backend.services.raw_market_event_store import raw_market_event_store


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except Exception:
        return None


def _period_seconds(period: str) -> int:
    unit = period[-1:]
    try:
        value = int(period[:-1])
    except Exception:
        return 60
    if unit == "m":
        return value * 60
    if unit == "h":
        return value * 3600
    if unit == "d":
        return value * 86400
    return 60


class MarketDataShadowCompare:
    """Read-only consistency checker for shadow ingest output."""

    def compare_klines(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        limit: int = 200,
        tolerance: float = 1e-9,
        volume_tolerance: float = 0.01,
        include_open: bool = False,
        settle_periods: int = 3,
        settle_seconds: int = 4500,
    ) -> dict[str, Any]:
        raw_market_event_store.ensure_table()
        exchange = exchange.strip().lower()
        symbol = symbol.strip().upper()
        limit = max(1, min(limit, 1000))

        interval = _period_seconds(timeframe)
        current_open_ts = int(time.time() // interval * interval)
        stable_delay = max(max(0, settle_periods) * interval, max(0, settle_seconds))
        stable_before_ts = current_open_ts - stable_delay
        open_filter = "" if include_open else "AND event_ts < :stable_before_ts"

        with MarketSessionLocal() as db:
            raw_rows = db.execute(text(f"""
                SELECT r.event_ts, r.payload_json
                FROM raw_market_events r
                JOIN (
                    SELECT event_ts, MAX(id) AS latest_id
                    FROM raw_market_events
                    WHERE exchange = :exchange
                      AND data_type = 'kline'
                      AND canonical_symbol = :symbol
                      AND timeframe = :timeframe
                      {open_filter}
                    GROUP BY event_ts
                    ORDER BY event_ts DESC
                    LIMIT :limit
                ) latest ON r.id = latest.latest_id
                ORDER BY r.event_ts DESC
            """), {
                "exchange": exchange,
                "symbol": symbol,
                "timeframe": timeframe,
                "current_open_ts": current_open_ts,
                "stable_before_ts": stable_before_ts,
                "limit": limit,
            }).mappings().all()

            if not raw_rows:
                return {
                    "status": "no_raw_events",
                    "exchange": exchange,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "include_open": include_open,
                    "settle_periods": settle_periods,
                    "settle_seconds": settle_seconds,
                    "compared": 0,
                    "matched": 0,
                    "match_rate": None,
                    "mismatches": [],
                }

            timestamps = [int(row["event_ts"]) for row in raw_rows]
            old_stmt = text("""
                SELECT timestamp, open_price, high_price, low_price, close_price, volume
                FROM crypto_klines
                WHERE exchange = :exchange
                  AND symbol = :symbol
                  AND period = :timeframe
                  AND timestamp IN :timestamps
            """).bindparams(bindparam("timestamps", expanding=True))
            old_rows = db.execute(old_stmt, {
                "exchange": exchange,
                "symbol": symbol,
                "timeframe": timeframe,
                "timestamps": timestamps,
            }).mappings().all()

        old_by_ts = {int(row["timestamp"]): row for row in old_rows}
        compared = 0
        matched = 0
        mismatches: list[dict[str, Any]] = []

        for row in raw_rows:
            event_ts = int(row["event_ts"])
            old = old_by_ts.get(event_ts)
            compared += 1
            if old is None:
                mismatches.append({"timestamp": event_ts, "reason": "missing_old_kline"})
                continue

            payload = json.loads(row["payload_json"])
            field_pairs = {
                "open": ("open", "open_price"),
                "high": ("high", "high_price"),
                "low": ("low", "low_price"),
                "close": ("close", "close_price"),
                "volume": ("volume", "volume"),
            }
            field_errors = {}
            for label, (raw_key, old_key) in field_pairs.items():
                raw_value = _to_float(payload.get(raw_key))
                old_value = _to_float(old.get(old_key))
                if raw_value is None and old_value is None:
                    continue
                if raw_value is None or old_value is None:
                    field_errors[label] = {"raw": raw_value, "old": old_value}
                    continue
                field_tolerance = volume_tolerance if label == "volume" else tolerance
                if abs(raw_value - old_value) > field_tolerance:
                    field_errors[label] = {"raw": raw_value, "old": old_value}

            if field_errors:
                mismatches.append({
                    "timestamp": event_ts,
                    "reason": "ohlcv_mismatch",
                    "fields": field_errors,
                })
            else:
                matched += 1

        return {
            "status": "ok" if compared > 0 else "no_overlap",
            "exchange": exchange,
            "symbol": symbol,
            "timeframe": timeframe,
            "include_open": include_open,
            "settle_periods": settle_periods,
            "settle_seconds": settle_seconds,
            "raw_events": len(raw_rows),
            "compared": compared,
            "matched": matched,
            "match_rate": round(matched / compared, 6) if compared else None,
            "mismatches": mismatches[:20],
        }


market_data_shadow_compare = MarketDataShadowCompare()
