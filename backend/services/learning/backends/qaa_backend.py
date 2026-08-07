"""QAA 进化系统后端。

迁移自 unified_learning_service.py:284-290 内联块。
非阻塞，失败不影响主流程。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..backend_base import LearningBackend

logger = logging.getLogger(__name__)


class QaaBackend(LearningBackend):
    name = "qaa_evolution"
    priority = 120

    def should_trigger(self, db: Session, outcome) -> bool:
        if not self.enabled:
            return False
        try:
            from backend.services.qaa_evolution_bridge import qaa_bridge
            return bool(getattr(qaa_bridge, "_enabled", False))
        except Exception:
            return False

    def handle_outcome(self, db: Session, outcome) -> None:
        try:
            from backend.services.qaa_evolution_bridge import qaa_bridge
            qaa_bridge.outcome_adapter.feed_outcome(outcome)
        except Exception as e:  # noqa: BLE001
            logger.debug("[qaa_evolution] feed skip: %s", e)
