"""Snapshot producer for the market-data foundation."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.services.exchange_data_profile import exchange_data_profile_service
from backend.services.kline_data_service import kline_service
from backend.services.market_data_metrics import market_data_metrics
from backend.services.snapshot_models import MarketDataSnapshot
from backend.services.snapshot_store import snapshot_store


class SnapshotProducer:
    """Builds read-optimized market-data snapshots."""

    @staticmethod
    def enabled() -> bool:
        return os.getenv("SNAPSHOT_STORE_ENABLED", "false").lower() in {"1", "true", "yes", "on"}

    def capture(
        self,
        symbols: list[str] | None = None,
        periods: list[str] | None = None,
        exchange: str | None = None,
        count: int = 50,
        force: bool = False,
    ) -> dict[str, Any]:
        if not force and not self.enabled():
            return {
                "captured": False,
                "reason": "SNAPSHOT_STORE_ENABLED=false",
                "status": snapshot_store.status(),
            }

        symbols = symbols or ["BTC", "ETH", "SOL"]
        periods = periods or ["1m", "15m", "1h"]
        count = max(1, min(count, 200))

        try:
            exchange_profiles = exchange_data_profile_service.get_profiles()
            klines: dict[str, Any] = {}
            for symbol in symbols:
                for period in periods:
                    key = f"{exchange or 'active'}:{symbol.upper()}:{period}"
                    klines[key] = kline_service.get_klines_from_db(
                        symbol=symbol,
                        period=period,
                        count=count,
                        exchange=exchange,
                    )

            snapshot = MarketDataSnapshot(
                snapshot_id=uuid.uuid4().hex,
                as_of=datetime.now(timezone.utc).isoformat(),
                markets={
                    "symbols": [s.upper() for s in symbols],
                    "periods": periods,
                    "exchange": exchange or "active",
                },
                klines=klines,
                exchange_profiles=exchange_profiles,
                metrics=market_data_metrics.snapshot(),
                data_quality={
                    "kline_groups": len(klines),
                    "empty_groups": sum(1 for value in klines.values() if not value),
                },
            )
            snapshot_store.put(snapshot)
            return {
                "captured": True,
                "snapshot_id": snapshot.snapshot_id,
                "as_of": snapshot.as_of,
                "status": snapshot_store.status(),
            }
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            snapshot_store.record_error(error)
            return {
                "captured": False,
                "reason": error,
                "status": snapshot_store.status(),
            }


snapshot_producer = SnapshotProducer()
