"""亏损根因诊断后端。

迁移自 unified_learning_service.py:222-234 内联块。
区分「策略错误」与「市场不可交易」。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..backend_base import LearningBackend

logger = logging.getLogger(__name__)


class CausalDiagnosisBackend(LearningBackend):
    name = "causal_diagnosis"
    priority = 50  # 亏损诊断优先，结果写入 outcome.exit_reason 供下游反思使用

    def should_trigger(self, db: Session, outcome) -> bool:
        return self.enabled and bool(outcome.strategy_id) and (outcome.pnl or 0) < 0

    def handle_outcome(self, db: Session, outcome) -> None:
        try:
            from backend.services.causal_analyzer import causal_analyzer
            market_ctx = {
                "regime": outcome.regime_at_exit or outcome.regime_at_entry or "unknown",
                "symbol": outcome.symbol,
                "side": outcome.side,
            }
            diagnosis = causal_analyzer.diagnose_loss(outcome, market_ctx)
            if diagnosis and diagnosis.root_cause:
                outcome.exit_reason = (outcome.exit_reason or "") + (
                    f" [根因: {diagnosis.root_cause}]"
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("[causal_diagnosis] 诊断跳过: %s", e)
