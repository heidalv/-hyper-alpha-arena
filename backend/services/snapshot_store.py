"""In-memory snapshot store, disabled from primary reads by default."""

from __future__ import annotations

import threading
import time
from typing import Optional

from backend.services.snapshot_models import MarketDataSnapshot


class SnapshotStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: Optional[MarketDataSnapshot] = None
        self._updated_at: float | None = None
        self._errors: list[str] = []

    def put(self, snapshot: MarketDataSnapshot) -> None:
        with self._lock:
            self._latest = snapshot
            self._updated_at = time.time()

    def get_latest(self, max_age: float | None = None) -> Optional[MarketDataSnapshot]:
        with self._lock:
            if self._latest is None:
                return None
            if max_age is not None and self._updated_at is not None:
                if time.time() - self._updated_at > max_age:
                    return None
            return self._latest

    def record_error(self, error: str) -> None:
        with self._lock:
            self._errors.append(error)
            self._errors = self._errors[-20:]

    def status(self) -> dict:
        with self._lock:
            age = time.time() - self._updated_at if self._updated_at else None
            return {
                "has_snapshot": self._latest is not None,
                "snapshot_id": self._latest.snapshot_id if self._latest else None,
                "as_of": self._latest.as_of if self._latest else None,
                "age_seconds": round(age, 2) if age is not None else None,
                "errors": list(self._errors),
            }


snapshot_store = SnapshotStore()
