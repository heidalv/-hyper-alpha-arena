"""模板 live stats 回灌后端。

合并自两处重复调用：
    unified_learning_service.py:269-282  (process_outcome 内联)
    learning_bus.py:118-131              (dispatch 内联)

旧代码里同一笔交易会让 strategy_library.record_trade_result 被调两次（
process_outcome 一次 + dispatch 一次）。本后端收敛为单一调用点。
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from ..backend_base import LearningBackend

logger = logging.getLogger(__name__)


class TemplateStatsBackend(LearningBackend):
    name = "template_stats"
    priority = 110

    def should_trigger(self, db: Session, outcome) -> bool:
        return self.enabled and bool(outcome.strategy_id)

    def handle_outcome(self, db: Session, outcome) -> None:
        try:
            from backend.database.models import AIStrategy
            from backend.services.strategy_library import strategy_library
            strat = (
                db.query(AIStrategy)
                .filter(AIStrategy.strategy_id == str(outcome.strategy_id))
                .first()
            )
            tpl_id: Optional[str] = None
            if strat and strat.genome:
                tpl_id = strat.genome.get("source_template_id")
            if tpl_id:
                strategy_library.record_trade_result(
                    db, tpl_id, float(outcome.pnl or 0), outcome.symbol or ""
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("[template_stats] 模板 live stats 跳过: %s", e)
