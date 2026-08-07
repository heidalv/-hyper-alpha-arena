"""概念漂移检测后端。

合并自两处重复调用：
    unified_learning_service.py:320-335  (process_outcome 内联)
    learning_bus.py:133-154              (dispatch 内联 causal_discovery_engine.record_trade)

注意：旧 dispatch 里的那块调的是 causal_discovery_engine.record_trade（因果发现），
与本后端（concept_drift_detector.record_pnl）是不同模块。
两者已在各自的 backend 里分离（见 causal_discovery_backend.py）。
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from ..backend_base import LearningBackend

logger = logging.getLogger(__name__)


class DriftDetectionBackend(LearningBackend):
    name = "concept_drift"
    priority = 140

    @property
    def enabled(self) -> bool:
        from backend.config.learning_config import is_enabled
        return is_enabled("concept_drift_detection")

    def handle_outcome(self, db: Session, outcome) -> None:
        try:
            from backend.services.concept_drift_detector import (
                get_concept_drift_detector,
            )
            detector = get_concept_drift_detector()
            opened = getattr(outcome, "opened_at", None)
            is_weekend = opened.weekday() >= 5 if opened else False
            detector.record_pnl(
                strategy_id=str(outcome.strategy_id),
                symbol=outcome.symbol or "",
                pnl_pct=float(outcome.pnl_pct or 0),
                is_weekend=is_weekend,
                opened_at=opened,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("[concept_drift] 漂移检测跳过: %s", e)
