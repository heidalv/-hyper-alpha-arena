#!/usr/bin/env python3
"""Market-data database optimization helpers.

Indexes are idempotent and safe to run at startup. They target hot paths used by
multi-exchange ingestion, K-line reads, shadow comparison, and raw-event replay.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import text

from backend.database.connection import MarketSessionLocal, market_engine
from backend.database.dialect import dialect
from backend.services.raw_market_event_store import raw_market_event_store


class MarketDataDbOptimizer:
    def __init__(self) -> None:
        self._last_result: dict[str, Any] | None = None

    def ensure_indexes(self) -> dict[str, Any]:
        started = time.time()
        raw_market_event_store.ensure_table()

        statements = [
            (
                "idx_crypto_klines_hot_lookup",
                """
                CREATE INDEX IF NOT EXISTS idx_crypto_klines_hot_lookup
                ON crypto_klines (exchange, symbol, period, environment, timestamp DESC)
                """,
            ),
            (
                "idx_crypto_klines_exchange_period_ts",
                """
                CREATE INDEX IF NOT EXISTS idx_crypto_klines_exchange_period_ts
                ON crypto_klines (exchange, period, timestamp DESC)
                """,
            ),
            (
                "idx_crypto_klines_period_ts",
                """
                CREATE INDEX IF NOT EXISTS idx_crypto_klines_period_ts
                ON crypto_klines (period, timestamp)
                """,
            ),
            (
                "idx_raw_market_events_hot_lookup",
                """
                CREATE INDEX IF NOT EXISTS idx_raw_market_events_hot_lookup
                ON raw_market_events (exchange, data_type, canonical_symbol, timeframe, event_ts DESC, id DESC)
                """,
            ),
            (
                "idx_raw_market_events_created_at",
                """
                CREATE INDEX IF NOT EXISTS idx_raw_market_events_created_at
                ON raw_market_events (created_at)
                """,
            ),
        ]

        created = []
        with market_engine.begin() as conn:
            for name, sql in statements:
                conn.execute(text(sql))
                created.append(name)

        result = {
            "ok": True,
            "indexes": created,
            "elapsed_seconds": round(time.time() - started, 3),
        }
        self._last_result = result
        return result

    def optimize(self) -> dict[str, Any]:
        started = time.time()
        self.ensure_indexes()
        actions: list[str] = []
        with MarketSessionLocal() as db:
            if dialect.is_sqlite:
                db.execute(text("PRAGMA optimize"))
                db.execute(text("PRAGMA wal_checkpoint(PASSIVE)"))
                actions.extend(["pragma_optimize", "wal_checkpoint_passive"])
            else:
                db.execute(text("ANALYZE crypto_klines"))
                db.execute(text("ANALYZE raw_market_events"))
                actions.extend(["analyze_crypto_klines", "analyze_raw_market_events"])
            db.commit()

        result = {
            "ok": True,
            "actions": actions,
            "elapsed_seconds": round(time.time() - started, 3),
        }
        self._last_result = result
        return result

    def status(self) -> dict[str, Any]:
        return {
            "dialect": "postgresql" if dialect.is_postgresql else "sqlite",
            "last_result": self._last_result,
        }


market_data_db_optimizer = MarketDataDbOptimizer()
