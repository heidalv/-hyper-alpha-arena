"""AI 策略达标自动晋升后端。

迁移自 unified_learning_service.py:261-266 内联块。
auto 策略（无 source_template_id）在 paper/live 达标后自动 promote。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..backend_base import LearningBackend

logger = logging.getLogger(__name__)


class PromotionBackend(LearningBackend):
    name = "promotion"
    priority = 100

    def should_trigger(self, db: Session, outcome) -> bool:
        return (
            self.enabled
            and bool(outcome.strategy_id)
            and outcome.source in ("paper", "live", "paper_partial")
        )

    def handle_outcome(self, db: Session, outcome) -> None:
        try:
            from backend.services.strategy_learning_service import strategy_learning_service
            strategy_learning_service.try_promote_single_strategy(
                db, str(outcome.strategy_id)
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("[promotion] 自动晋升检查跳过: %s", e)
