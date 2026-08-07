"""Read facade for SnapshotStore."""

from __future__ import annotations

from typing import Any

from backend.services.snapshot_store import snapshot_store


class SnapshotReader:
    def get_snapshot(self, max_age: float = 120) -> dict[str, Any] | None:
        snapshot = snapshot_store.get_latest(max_age=max_age)
        return snapshot.to_dict() if snapshot else None

    def status(self) -> dict[str, Any]:
        return snapshot_store.status()


snapshot_reader = SnapshotReader()
