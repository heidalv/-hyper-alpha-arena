"""决策 block 模式学习 — 消费 DecisionSnapshot code_reason 统计高频拦截。"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Dict

from sqlalchemy.orm import Session

from ..backend_base import LearningBackend

logger = logging.getLogger(__name__)

# 内存聚合：tier -> reason -> count（进程内；LearningLoop 可扩展落库）
_block_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
_last_flush = 0.0


class BlockPatternLearningBackend(LearningBackend):
    name = "block_pattern_learning"
    priority = 145

    def should_trigger(self, db: Session, outcome) -> bool:
        if not self.enabled:
            return False
        meta = getattr(outcome, "metadata", None) or {}
        if meta.get("code_reason") or meta.get("block_reason"):
            return True
        if meta.get("was_blocked") or meta.get("gate_blocked"):
            return True
        return False

    def handle_outcome(self, db: Session, outcome) -> None:
        meta = getattr(outcome, "metadata", None) or {}
        reason = (
            meta.get("code_reason")
            or meta.get("block_reason")
            or meta.get("gate_reason")
            or "unknown_block"
        )
        tier = (getattr(outcome, "tier", None) or meta.get("tier") or "unknown").lower()
        key = f"{tier}:{str(reason)[:120]}"
        _block_stats[tier][str(reason)[:120]] += 1
        logger.info("[BlockPatternLearning] %s count=%d", key, _block_stats[tier][str(reason)[:120]])
        self._maybe_flush_to_memory(db, tier, str(reason)[:120])

    @staticmethod
    def _maybe_flush_to_memory(db: Session, tier: str, reason: str) -> None:
        global _last_flush
        now = time.time()
        if now - _last_flush < 300:
            return
        _last_flush = now
        try:
            from backend.database.models import StrategyMemory
            top = sorted(_block_stats.get(tier, {}).items(), key=lambda x: -x[1])[:5]
            if not top:
                return
            lesson = "; ".join(f"{r}×{c}" for r, c in top)
            mem = db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == "_global_",
                StrategyMemory.category == "block_pattern",
            ).first()
            if mem:
                mem.content = lesson
            else:
                mem = StrategyMemory(
                    strategy_id="_global_",
                    category="block_pattern",
                    content=lesson,
                    source="block_pattern_learning",
                )
                db.add(mem)
            db.commit()
            logger.info("[BlockPatternLearning] 已写入全局 block 模式: %s", lesson[:200])
        except Exception as exc:
            logger.debug("[BlockPatternLearning] flush 跳过: %s", exc)
            try:
                db.rollback()
            except Exception:
                pass

    @staticmethod
    def get_stats() -> Dict[str, Any]:
        return {tier: dict(reasons) for tier, reasons in _block_stats.items()}
