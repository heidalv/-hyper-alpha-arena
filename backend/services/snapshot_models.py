"""Snapshot models for the high-throughput data foundation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class MarketDataSnapshot:
    snapshot_id: str
    as_of: str
    version: str = "market-data-v1"
    markets: Dict[str, Any] = field(default_factory=dict)
    klines: Dict[str, Any] = field(default_factory=dict)
    exchange_profiles: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    data_quality: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "as_of": self.as_of,
            "version": self.version,
            "markets": self.markets,
            "klines": self.klines,
            "exchange_profiles": self.exchange_profiles,
            "metrics": self.metrics,
            "data_quality": self.data_quality,
        }
