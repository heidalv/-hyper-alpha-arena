"""QAA 3.1 semantic memory backend.

Stores every unified TradeOutcome into the embedded QAA RAG memory so the
whole learning system, not just paper retrospectives, feeds long-horizon
semantic recall.
"""

from __future__ import annotations

import logging
import os

from sqlalchemy.orm import Session

from ..backend_base import LearningBackend

logger = logging.getLogger(__name__)


class QaaSemanticMemoryBackend(LearningBackend):
    name = "qaa_semantic_memory"
    # Run after deterministic DB updates, before heavier periodic miners.
    priority = 125

    @property
    def enabled(self) -> bool:
        raw = os.getenv("QAA_SEMANTIC_MEMORY_ENABLED", "true").strip().lower()
        return raw in ("1", "true", "yes", "on")

    def should_trigger(self, db: Session, outcome) -> bool:
        if not self.enabled:
            return False
        # Partial closes create noisy duplicate memories; the final close is
        # enough for long-horizon semantic learning.
        meta = getattr(outcome, "metadata", None)
        if isinstance(meta, dict) and meta.get("partial_close"):
            return False
        return bool(getattr(outcome, "symbol", "") and getattr(outcome, "source", ""))

    def handle_outcome(self, db: Session, outcome) -> None:
        try:
            from backend.services.qaa_trade_memory_bridge import ingest_trade_outcome

            ids = ingest_trade_outcome(outcome)
            if ids:
                logger.debug(
                    "[qaa_semantic_memory] stored outcome: %s %s ids=%d",
                    getattr(outcome, "symbol", ""),
                    getattr(outcome, "source", ""),
                    len(ids),
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[qaa_semantic_memory] outcome sync skipped: %s", exc)
