"""在线学习域适配器 — 薄封装 UnifiedLearningService / LearningLoopService / EvolutionScheduler。

统一内核通过本适配器聚合学习闭环状态，并把交易 outcome 反馈落成 feedback 血缘。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class LearningAdapter:
    """封装在线学习闭环。"""

    source = "learning_loop"

    def loop_status(self) -> Dict[str, Any]:
        try:
            from backend.services.learning_loop_service import learning_loop
            return learning_loop.status()
        except Exception as exc:
            logger.debug("[LearningAdapter] loop_status 不可用: %s", exc)
            return {}

    def loop_metrics(self) -> Dict[str, Any]:
        try:
            from backend.services.learning_loop_service import learning_loop
            return learning_loop.metrics()
        except Exception as exc:
            logger.debug("[LearningAdapter] loop_metrics 不可用: %s", exc)
            return {}

    def evolution_status(self) -> Dict[str, Any]:
        try:
            from backend.services.evolution_scheduler import evolution_scheduler
            status = getattr(evolution_scheduler, "get_status", None)
            if callable(status):
                return status()
            return {
                "running": getattr(evolution_scheduler, "_running_evolution", False),
            }
        except Exception as exc:
            logger.debug("[LearningAdapter] evolution_status 不可用: %s", exc)
            return {}

    def backends_status(self) -> Dict[str, Any]:
        try:
            from backend.services.learning_registry_bridge import get_registry
            return get_registry().status()
        except Exception as exc:
            logger.debug("[LearningAdapter] backends_status 不可用: %s", exc)
            return {}
