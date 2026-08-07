"""成功模板提取后端。

迁移自 learning_bus.py 的 pattern_extraction 触发逻辑。
盈利交易时尝试提取成功模板，冷却 24h（继承自旧 _last_miner_at 共用）。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..backend_base import LearningBackend

logger = logging.getLogger(__name__)


class PatternExtractionBackend(LearningBackend):
    name = "pattern_extraction"
    priority = 220

    @property
    def enabled(self) -> bool:
        try:
            from backend.config import settings as _s
            return bool(getattr(_s, "LEARNING_LOOP_ENABLED", True))
        except Exception:
            return True

    def should_trigger(self, db: Session, outcome) -> bool:
        return self.enabled and bool(outcome.strategy_id) and (outcome.pnl or 0) > 0

    def handle_outcome(self, db: Session, outcome) -> None:
        try:
            from backend.services.pattern_extractor import PatternExtractor
            extractor = PatternExtractor()
            template = extractor.extract_successful_pattern(db, outcome.strategy_id)
            if template:
                try:
                    from backend.services.qaa_trade_memory_bridge import ingest_learning_artifact

                    text = (
                        f"成功模式模板: strategy={template.get('strategy_id')} "
                        f"best_regime={template.get('best_regime')} "
                        f"best_nature={template.get('best_nature')} "
                        f"best_symbol={template.get('best_symbol')} "
                        f"win_rate={template.get('win_rate')} "
                        f"trades={template.get('total_trades')} "
                        f"avg_pnl={template.get('avg_pnl_per_trade')}"
                    )
                    ingest_learning_artifact(
                        artifact_type="success_pattern_template",
                        text=text,
                        payload=template,
                        strategy_id=outcome.strategy_id,
                        symbol=template.get("best_symbol", "") or outcome.symbol,
                        regime=template.get("best_regime", ""),
                        trade_nature=template.get("best_nature", ""),
                        source=f"success_pattern:{outcome.strategy_id}",
                    )
                except Exception as qaa_err:
                    logger.debug("[pattern_extraction] QAA artifact sync skipped: %s", qaa_err)
                logger.info(
                    "[pattern_extraction] 成功模板已提取: %s best_regime=%s",
                    outcome.strategy_id, template.get("best_regime"),
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("[pattern_extraction] 模板提取失败: %s", e)
