"""交易模式挖掘后端。

迁移自 learning_bus.py 的 miner 触发逻辑。
每 25 笔触发 trade_memory_miner.inject_patterns_to_memory，冷却 24h。
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from ..backend_base import ThresholdBackend

logger = logging.getLogger(__name__)

MINER_TRIGGER_EVERY_N_TRADES = 25
MINER_COOLDOWN_HOURS = 24


class MinerBackend(ThresholdBackend):
    name = "pattern_mining"
    priority = 210

    def __init__(self) -> None:
        super().__init__()
        self.threshold = MINER_TRIGGER_EVERY_N_TRADES
        self.cooldown_seconds = MINER_COOLDOWN_HOURS * 3600

    @property
    def enabled(self) -> bool:
        try:
            from backend.config import settings as _s
            return bool(getattr(_s, "LEARNING_LOOP_ENABLED", True))
        except Exception:
            return True

    def _on_trigger(self, db: Session, outcome) -> None:
        from backend.services.trade_memory_miner import inject_patterns_to_memory, mine_trade_patterns
        symbol: Optional[str] = outcome.symbol if outcome.symbol else None
        strategy_id = outcome.strategy_id or outcome.template_id
        if not strategy_id:
            return
        injected = inject_patterns_to_memory(db, strategy_id, symbol)
        if injected:
            try:
                result = mine_trade_patterns(db, symbol=symbol)
                if result and result.get("total_records"):
                    from backend.services.qaa_trade_memory_bridge import ingest_learning_artifact

                    ingest_learning_artifact(
                        artifact_type="pattern_mining_report",
                        text=result.get("summary_text", ""),
                        payload=result,
                        strategy_id=strategy_id,
                        symbol=symbol or "",
                        source=f"pattern_mining:{strategy_id}:{symbol or 'all'}",
                    )
            except Exception as qaa_err:
                logger.debug("[pattern_mining] QAA artifact sync skipped: %s", qaa_err)
            logger.info("[pattern_mining] 模式挖掘已触发: %s", strategy_id)
        else:
            # 未产出时不重置计数？旧逻辑会重置（_trigger_miner 成功才重置）。
            # 这里保持一致：inject_patterns_to_memory 返回 falsy 视为未触发。
            raise RuntimeError("no pattern injected")
