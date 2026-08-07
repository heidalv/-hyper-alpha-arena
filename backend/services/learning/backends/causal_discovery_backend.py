"""因果发现后端。

迁移自 learning_bus.py 的 causal_discovery 触发逻辑（_should_trigger_causal_discovery
+ _trigger_causal_discovery）。后台线程执行，冷却 6h。

注意：这是 causal_discovery_engine（因果发现引擎），与 drift_detection_backend
（concept_drift_detector 概念漂移）是不同模块，勿混淆。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..backend_base import AsyncBackend

logger = logging.getLogger(__name__)


class CausalDiscoveryBackend(AsyncBackend):
    name = "causal_discovery"
    priority = 230

    def should_trigger(self, db: Session, outcome) -> bool:
        if not self.enabled:
            return False
        if not outcome.strategy_id or not outcome.symbol:
            return False
        try:
            from backend.services.causal_discovery_engine import (
                get_causal_discovery_engine,
            )
            cde = get_causal_discovery_engine()
            return cde.should_trigger(outcome.strategy_id, outcome.symbol)
        except Exception as e:  # noqa: BLE001
            logger.debug("[causal_discovery] 触发检查跳过: %s", e)
            return False

    @property
    def enabled(self) -> bool:
        from backend.config.learning_config import is_enabled
        return is_enabled("causal_discovery")

    def _run(self, db: Session, outcome) -> None:
        try:
            from backend.services.causal_discovery_engine import (
                get_causal_discovery_engine,
            )
            cde = get_causal_discovery_engine()
            rules = cde.discover(db, outcome.strategy_id, outcome.symbol)
            logger.info(
                "[causal_discovery] 完成: %s/%s 产出 %d 条规则",
                outcome.strategy_id, outcome.symbol, len(rules),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[causal_discovery] 后台任务失败: %s", e)
