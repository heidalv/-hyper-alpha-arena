"""AI因子: 动量加速度 | 置信:60% | 短期动量与长期动量的差值反映动量加速或减速。加速上行预示继续上涨，加速下行预示继续下跌。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration(BaseFactor):
    """短期动量与长期动量的差值反映动量加速或减速。加速上行预示继续上涨，加速下行预示继续下跌。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_acceleration",
            name="Momentum Acceleration",
            display_name="动量加速度",
            description="短期动量与长期动量的差值反映动量加速或减速。加速上行预示继续上涨，加速下行预示继续下跌。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short = data['close'].pct_change(5)
        long = data['close'].pct_change(20)
        result = (short - long).rolling(3).mean().clip(-1, 1)
        return result
