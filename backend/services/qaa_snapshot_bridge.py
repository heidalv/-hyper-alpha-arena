"""Bridge for future QAA SnapshotReader adoption.

This module does not change QAA behavior by itself. Callers must explicitly use
it and enable QAA_READ_SNAPSHOT_STORE=true.
"""

from __future__ import annotations

import os
from typing import Any

from backend.services.snapshot_reader import snapshot_reader


class QAASnapshotBridge:
    @staticmethod
    def enabled() -> bool:
        return os.getenv("QAA_READ_SNAPSHOT_STORE", "false").lower() in {"1", "true", "yes", "on"}

    def get_snapshot_for_qaa(self, max_age: float = 120) -> dict[str, Any] | None:
        if not self.enabled():
            return None
        return snapshot_reader.get_snapshot(max_age=max_age)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled(),
            "snapshot": snapshot_reader.status(),
        }


qaa_snapshot_bridge = QAASnapshotBridge()
