"""Exchange-level data profile for the data center."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from backend.database.connection import MarketSessionLocal
from backend.services.market_data_ingest_queue import market_data_ingest_queue
from backend.services.market_data_metrics import market_data_metrics
from backend.services.raw_market_event_store import raw_market_event_store
from backend.services.market_data_shadow_compare import market_data_shadow_compare


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


class ExchangeDataProfileService:
    """Build read-only profiles for each exchange's data coverage."""

    def get_profiles(self) -> dict[str, Any]:
        raw_market_event_store.ensure_table()
        now_ts = _now_ts()

        with MarketSessionLocal() as db:
            kline_rows = db.execute(text("""
                SELECT exchange,
                       COUNT(*) AS records,
                       COUNT(DISTINCT symbol) AS symbols,
                       COUNT(DISTINCT period) AS periods,
                       MAX(timestamp) AS latest_ts,
                       MIN(timestamp) AS earliest_ts
                FROM crypto_klines
                GROUP BY exchange
                ORDER BY records DESC
            """)).mappings().all()

            raw_rows = db.execute(text("""
                SELECT exchange,
                       COUNT(*) AS raw_events,
                       COUNT(DISTINCT canonical_symbol) AS raw_symbols,
                       MAX(event_ts) AS latest_raw_ts
                FROM raw_market_events
                GROUP BY exchange
            """)).mappings().all()

        raw_by_exchange = {row["exchange"]: row for row in raw_rows}
        profiles = []
        for row in kline_rows:
            exchange = row["exchange"]
            latest_ts = int(row["latest_ts"] or 0)
            raw = raw_by_exchange.get(exchange, {})
            latest_raw_ts = int(raw.get("latest_raw_ts") or 0)
            freshness_seconds = now_ts - latest_ts if latest_ts else None
            raw_freshness_seconds = now_ts - latest_raw_ts if latest_raw_ts else None
            profiles.append({
                "exchange": exchange,
                "records": int(row["records"] or 0),
                "symbols": int(row["symbols"] or 0),
                "periods": int(row["periods"] or 0),
                "earliest_ts": int(row["earliest_ts"] or 0) or None,
                "latest_ts": latest_ts or None,
                "freshness_seconds": freshness_seconds,
                "raw_events": int(raw.get("raw_events") or 0),
                "raw_symbols": int(raw.get("raw_symbols") or 0),
                "latest_raw_ts": latest_raw_ts or None,
                "raw_freshness_seconds": raw_freshness_seconds,
                "status": self._status(freshness_seconds, int(row["records"] or 0)),
                "shadow_compare": self._shadow_compare_summary(exchange),
            })

        known_exchanges = {p["exchange"] for p in profiles}
        for exchange, raw in raw_by_exchange.items():
            if exchange in known_exchanges:
                continue
            latest_raw_ts = int(raw.get("latest_raw_ts") or 0)
            profiles.append({
                "exchange": exchange,
                "records": 0,
                "symbols": 0,
                "periods": 0,
                "earliest_ts": None,
                "latest_ts": None,
                "freshness_seconds": None,
                "raw_events": int(raw.get("raw_events") or 0),
                "raw_symbols": int(raw.get("raw_symbols") or 0),
                "latest_raw_ts": latest_raw_ts or None,
                "raw_freshness_seconds": now_ts - latest_raw_ts if latest_raw_ts else None,
                "status": "raw_only",
                "shadow_compare": self._shadow_compare_summary(exchange),
            })

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "profiles": profiles,
            "queue": market_data_ingest_queue.status(),
            "metrics": market_data_metrics.snapshot(),
            "raw_summary": raw_market_event_store.summary(limit=20),
        }

    @staticmethod
    def _status(freshness_seconds: int | None, records: int) -> str:
        if records <= 0:
            return "no_data"
        if freshness_seconds is None:
            return "unknown"
        if freshness_seconds <= 300:
            return "healthy"
        if freshness_seconds <= 3600:
            return "lagging"
        return "stale"

    @staticmethod
    def _shadow_compare_summary(exchange: str) -> dict[str, Any]:
        checks = []
        for symbol, timeframe in (("BTC", "1m"), ("BTC", "5m")):
            result = market_data_shadow_compare.compare_klines(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                limit=50,
            )
            checks.append({
                "symbol": symbol,
                "timeframe": timeframe,
                "status": result.get("status"),
                "compared": result.get("compared", 0),
                "matched": result.get("matched", 0),
                "match_rate": result.get("match_rate"),
                "mismatch_count": len(result.get("mismatches") or []),
            })

        valid_rates = [
            item["match_rate"]
            for item in checks
            if item.get("match_rate") is not None
        ]
        return {
            "checks": checks,
            "overall_match_rate": round(sum(valid_rates) / len(valid_rates), 6) if valid_rates else None,
        }


exchange_data_profile_service = ExchangeDataProfileService()
