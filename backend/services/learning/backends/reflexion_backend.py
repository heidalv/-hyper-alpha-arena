"""Reflexion 亏损反思后端。

迁移自 unified_learning_service.py:238-256 内联块。
后台异步生成一句教训并入分层记忆，下一轮决策 prompt 检索注入。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..backend_base import AsyncBackend

logger = logging.getLogger(__name__)


class ReflexionBackend(AsyncBackend):
    name = "reflexion"
    priority = 60  # 诊断之后、其他后端之前，确保反思用上根因

    def should_trigger(self, db: Session, outcome) -> bool:
        return self.enabled and bool(outcome.strategy_id) and (outcome.pnl or 0) < 0

    def _run(self, db: Session, outcome) -> None:
        try:
            from backend.services.trade_memory_context import (
                generate_loss_reflection_async,
            )
            _meta_rx = outcome.metadata if isinstance(outcome.metadata, dict) else {}
            generate_loss_reflection_async(
                strategy_id=str(outcome.strategy_id),
                symbol=outcome.symbol or "",
                side=outcome.side or "",
                pnl=float(outcome.pnl or 0),
                pnl_pct=float(outcome.pnl_pct or 0),
                exit_reason=str(
                    getattr(outcome, "exit_reason", None)
                    or _meta_rx.get("close_reason")
                    or ""
                ),
                regime=str(outcome.regime_at_exit or outcome.regime_at_entry or ""),
                duration_seconds=int(outcome.duration_seconds or 0),
                confidence=float(outcome.confidence or 0),
                account_equity=float(_meta_rx.get("account_equity") or 0),
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("[reflexion] 反思跳过: %s", e)
