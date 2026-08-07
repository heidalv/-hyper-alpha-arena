"""Hermes Agent 决策智慧学习后端 — 平仓后写入 agent_decision_wisdom。"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..backend_base import LearningBackend

logger = logging.getLogger(__name__)


class HermesAgentWisdomBackend(LearningBackend):
    name = "hermes_agent_wisdom"
    priority = 150

    def should_trigger(self, db: Session, outcome) -> bool:
        if not self.enabled:
            return False
        nature = (getattr(outcome, "trade_nature", "") or "").lower()
        if nature in ("swing", "trend_follow", "position", "intraday", "midlong"):
            return True
        meta = getattr(outcome, "metadata", None) or {}
        if meta.get("thesis_id") or meta.get("mlto_thesis_id"):
            return True
        if (meta.get("agent_envelope") or {}).get("thesis_id"):
            return True
        return False

    def handle_outcome(self, db: Session, outcome) -> None:
        try:
            from backend.services.hermes_agent_wisdom_engine import agent_wisdom
            agent_wisdom.extract_wisdom_from_outcome(outcome)
        except Exception as exc:
            logger.debug("[hermes_agent_wisdom] 跳过: %s", exc)
