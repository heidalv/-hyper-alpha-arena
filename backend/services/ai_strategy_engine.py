"""AI Strategy Engine — Phase 2 存根（已废弃，由 strategy_coordinator + ai_decision_service 替代）"""
import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class StrategyDecisionResult:
    """策略执行结果 DTO（存根）"""
    success: bool = False
    reason: Optional[str] = "AIStrategyEngine 已废弃（Phase 2），请使用 strategy_coordinator"
    decisions: List[Dict[str, Any]] = field(default_factory=list)
    strategy_id: Optional[str] = None
    market_env: None = None
    risk_params: None = None


class AIStrategyEngine:
    """Phase 2 存根：此引擎已废弃，核心逻辑已迁移到 strategy_coordinator + ai_decision_service。"""

    def __init__(self, db=None):
        self.db = db
        logger.warning("AIStrategyEngine 已废弃（Phase 2 重构），请使用 strategy_coordinator + ai_decision_service")

    def execute_strategy_decision(self, strategy_id: str, **kwargs) -> StrategyDecisionResult:
        logger.warning(f"AIStrategyEngine.execute_strategy_decision called for {strategy_id} — stub, no-op")
        return StrategyDecisionResult(
            success=False,
            reason="AIStrategyEngine 已废弃（Phase 2），请通过 /api/ai-trading 接口触发决策",
            strategy_id=strategy_id,
        )
