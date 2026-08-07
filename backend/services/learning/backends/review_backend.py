"""定期复盘后端。

迁移自 learning_bus.py 的 review 触发逻辑（_should_trigger_review + _trigger_review）。
每 N 笔交易触发一次 strategy_learning_service.run_periodic_review。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..backend_base import ThresholdBackend

logger = logging.getLogger(__name__)

# 默认每 15 笔触发一次复盘（与旧 REVIEW_TRIGGER_EVERY_N_TRADES 一致）
DEFAULT_REVIEW_EVERY_N = 15


def _get_review_every_n() -> int:
    """从 paper_pace_controller 动态读取阈值（保持与旧逻辑一致）。"""
    try:
        from backend.services.paper_pace_controller import paper_pace_controller
        return paper_pace_controller.get_knobs().learning_review_every_n
    except Exception:
        return DEFAULT_REVIEW_EVERY_N


class ReviewBackend(ThresholdBackend):
    name = "periodic_review"
    priority = 200

    def __init__(self) -> None:
        super().__init__()
        # threshold 动态读取，should_trigger 里实时判断
        self.threshold = DEFAULT_REVIEW_EVERY_N

    @property
    def enabled(self) -> bool:
        try:
            from backend.config import settings as _s
            return bool(getattr(_s, "LEARNING_LOOP_ENABLED", True))
        except Exception:
            return True

    def should_trigger(self, db: Session, outcome) -> bool:
        if not self.enabled:
            return False
        from ..backend_base import _is_partial_outcome
        if _is_partial_outcome(outcome):
            return False
        if not getattr(outcome, "strategy_id", None):
            return False
        # 动态阈值
        self.threshold = _get_review_every_n()
        return super().should_trigger(db, outcome)

    def _on_trigger(self, db: Session, outcome) -> None:
        from backend.services.strategy_learning_service import strategy_learning
        strategy_id = outcome.strategy_id
        if not strategy_id:
            return
        strategy_learning.run_periodic_review(strategy_id, days=14)
        logger.info("[periodic_review] 定期复盘已触发: %s", strategy_id)
