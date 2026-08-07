"""
Raw market event store for the v2 shadow ingest path.

Events are stored separately from crypto_klines so the new path can be replayed
and compared without changing existing readers.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import text

from backend.database.connection import MarketSessionLocal, sqlite_write_commit
from backend.database.dialect import dialect
from backend.services.market_data_metrics import market_data_metrics


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class RawMarketEventStore:
    """Append-only raw event store with idempotent event hashes."""

    def __init__(self) -> None:
        self._initialized = False
        self._lock = threading.Lock()

    def ensure_table(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            with MarketSessionLocal() as db:
                db.execute(text(f"""
                    CREATE TABLE IF NOT EXISTS raw_market_events (
                        id {dialect.big_auto_pk()},
                        event_hash VARCHAR(64) NOT NULL UNIQUE,
                        exchange VARCHAR(32) NOT NULL,
                        market_type VARCHAR(16) NOT NULL,
                        data_type VARCHAR(32) NOT NULL,
                        canonical_symbol VARCHAR(32) NOT NULL,
                        exchange_symbol VARCHAR(64) NOT NULL,
                        timeframe VARCHAR(16),
                        event_ts BIGINT NOT NULL,
                        payload_json TEXT NOT NULL,
                        source VARCHAR(64) NOT NULL DEFAULT 'market_data_v2',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                db.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_raw_market_events_lookup "
                    "ON raw_market_events (exchange, data_type, canonical_symbol, timeframe, event_ts)"
                ))
                db.commit()
            self._initialized = True

    def event_hash(
        self,
        exchange: str,
        data_type: str,
        canonical_symbol: str,
        timeframe: str | None,
        event_ts: int,
        payload: Any,
    ) -> str:
        raw = _json_dumps({
            "exchange": exchange,
            "data_type": data_type,
            "canonical_symbol": canonical_symbol,
            "timeframe": timeframe or "",
            "event_ts": event_ts,
            "payload": payload,
        })
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def append_event(
        self,
        *,
        exchange: str,
        market_type: str,
        data_type: str,
        canonical_symbol: str,
        exchange_symbol: str,
        event_ts: int,
        payload: Any,
        timeframe: str | None = None,
        source: str = "market_data_v2",
    ) -> str:
        self.ensure_table()
        event_hash = self.event_hash(exchange, data_type, canonical_symbol, timeframe, event_ts, payload)
        insert_sql = text(dialect.insert_on_conflict_do_nothing(
            "raw_market_events",
            "event_hash, exchange, market_type, data_type, canonical_symbol, exchange_symbol, "
            "timeframe, event_ts, payload_json, source",
            ":event_hash, :exchange, :market_type, :data_type, :canonical_symbol, :exchange_symbol, "
            ":timeframe, :event_ts, :payload_json, :source",
            conflict_cols="event_hash",
        ))
        with market_data_metrics.timer("db.raw_market_events.insert"):
            with MarketSessionLocal() as db:
                db.execute(insert_sql, {
                    "event_hash": event_hash,
                    "exchange": exchange,
                    "market_type": market_type,
                    "data_type": data_type,
                    "canonical_symbol": canonical_symbol,
                    "exchange_symbol": exchange_symbol,
                    "timeframe": timeframe,
                    "event_ts": int(event_ts),
                    "payload_json": _json_dumps(payload),
                    "source": source,
                })
                sqlite_write_commit(db, label="raw_market_events.insert")
        return event_hash

    def append_many(self, events: Iterable[dict[str, Any]]) -> list[str]:
        self.ensure_table()
        rows = []
        hashes = []
        for event in events:
            event_hash = self.event_hash(
                event["exchange"],
                event["data_type"],
                event["canonical_symbol"],
                event.get("timeframe"),
                int(event["event_ts"]),
                event["payload"],
            )
            hashes.append(event_hash)
            rows.append({
                "event_hash": event_hash,
                "exchange": event["exchange"],
                "market_type": event["market_type"],
                "data_type": event["data_type"],
                "canonical_symbol": event["canonical_symbol"],
                "exchange_symbol": event["exchange_symbol"],
                "timeframe": event.get("timeframe"),
                "event_ts": int(event["event_ts"]),
                "payload_json": _json_dumps(event["payload"]),
                "source": event.get("source", "market_data_v2"),
            })

        if not rows:
            return hashes

        insert_sql = text(dialect.insert_on_conflict_do_nothing(
            "raw_market_events",
            "event_hash, exchange, market_type, data_type, canonical_symbol, exchange_symbol, "
            "timeframe, event_ts, payload_json, source",
            ":event_hash, :exchange, :market_type, :data_type, :canonical_symbol, :exchange_symbol, "
            ":timeframe, :event_ts, :payload_json, :source",
            conflict_cols="event_hash",
        ))
        with market_data_metrics.timer("db.raw_market_events.batch_insert"):
            with MarketSessionLocal() as db:
                db.execute(insert_sql, rows)
                sqlite_write_commit(db, label="raw_market_events.batch_insert")
        return hashes

    def summary(self, limit: int = 20) -> dict[str, Any]:
        self.ensure_table()
        with MarketSessionLocal() as db:
            total = db.execute(text("SELECT COUNT(*) FROM raw_market_events")).scalar() or 0
            rows = db.execute(text("""
                SELECT exchange, data_type, canonical_symbol, timeframe, COUNT(*) AS count, MAX(event_ts) AS latest_ts
                FROM raw_market_events
                GROUP BY exchange, data_type, canonical_symbol, timeframe
                ORDER BY latest_ts DESC
                LIMIT :limit
            """), {"limit": limit}).mappings().all()
        return {
            "total": int(total),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "groups": [dict(row) for row in rows],
        }


raw_market_event_store = RawMarketEventStore()
