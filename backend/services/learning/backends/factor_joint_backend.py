"""因子-策略贝叶斯联合更新后端。

迁移自 unified_learning_service.py:293-318 内联块。
受 AI_FACTOR_STRATEGY_JOINT_ENABLED 开关控制（L5 起读 LearningConfig）。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..backend_base import LearningBackend

logger = logging.getLogger(__name__)


class FactorJointBackend(LearningBackend):
    name = "factor_strategy_joint"
    priority = 130

    @property
    def enabled(self) -> bool:
        from backend.config.learning_config import is_enabled
        return is_enabled("factor_strategy_joint")

    def handle_outcome(self, db: Session, outcome) -> None:
        try:
            from backend.services.factor_strategy_joint import get_factor_strategy_joint
            joint = get_factor_strategy_joint()
            fp = getattr(outcome, "fingerprint_at_entry", None) or {}
            factor_values = {
                "funding_rate": fp.get("funding_rate", 0),
                "oi_change_pct": fp.get("oi_change_pct", 0),
                "liquidation_imbalance": fp.get("liquidation_imbalance", 0),
                "stablecoin_flow": fp.get("stablecoin_flow", 0),
                "volume_ratio": fp.get("volume_ratio", 1.0),
                "rsi_14": fp.get("rsi_14", 50.0),
                "volatility_30d": fp.get("volatility_30d", 0),
                "ema_trend_slope": fp.get("ema_trend_slope", 0),
            }
            is_win = float(outcome.pnl or 0) > 0
            regime = outcome.regime_at_entry or "ranging"
            joint.update(
                strategy_id=str(outcome.strategy_id),
                symbol=outcome.symbol or "",
                factor_values=factor_values,
                is_win=is_win,
                regime=regime,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("[factor_strategy_joint] 因子联合跳过: %s", e)
