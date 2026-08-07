"""
Batch writer for market-data tables.

Phase 1 keeps this behind MARKET_DATA_BATCH_WRITE_ENABLED so the old write path
remains the default rollback path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text

from backend.database.connection import MarketSessionLocal, sqlite_write_commit
from backend.database.dialect import dialect
from backend.services.market_data_metrics import market_data_metrics


class MarketDataWriteBatcher:
    """Bulk insert helper for K-line rows."""

    def insert_klines(self, exchange: str, symbol: str, period: str, klines: list[dict[str, Any]]) -> int:
        if not klines:
            return 0

        insert_sql = text(dialect.insert_on_conflict_do_nothing(
            "crypto_klines",
            "exchange, symbol, market, timestamp, period, datetime_str, "
            "open_price, high_price, low_price, close_price, volume, environment",
            ":exchange, :symbol, 'CRYPTO', :timestamp, :period, :datetime_str, "
            ":open_price, :high_price, :low_price, :close_price, :volume, 'mainnet'",
            conflict_cols="exchange, symbol, market, period, timestamp, environment",
        ))

        rows = []
        for k in klines:
            ts = int(k["timestamp"])
            rows.append({
                "exchange": exchange,
                "symbol": symbol.upper(),
                "timestamp": ts,
                "period": period,
                "datetime_str": datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
                "open_price": k["open"],
                "high_price": k["high"],
                "low_price": k["low"],
                "close_price": k["close"],
                "volume": k["volume"],
            })

        with market_data_metrics.timer("db.crypto_klines.batch_insert"):
            with MarketSessionLocal() as db:
                db.execute(insert_sql, rows)
                sqlite_write_commit(db, label="crypto_klines.batch_insert")

        return len(rows)


market_data_write_batcher = MarketDataWriteBatcher()
