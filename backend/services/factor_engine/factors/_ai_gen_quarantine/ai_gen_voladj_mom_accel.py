"""AI因子: 波动率调整动量加速度 | 置信:60% | 短期动量(5日)减去长期动量(20日)得到加速度，再除以20日已实现波动率做风险调整。正值代表上涨动能加速且波动可控，预示后续延续上涨；负值反之。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumAcceleration(BaseFactor):
    """短期动量(5日)减去长期动量(20日)得到加速度，再除以20日已实现波动率做风险调整。正值代表上涨动能加速且波动可控，预示后续延续上涨；负值反之。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_voladj_mom_accel",
            name="Volatility Adjusted Momentum Acceleration",
            display_name="波动率调整动量加速度",
            description="短期动量(5日)减去长期动量(20日)得到加速度，再除以20日已实现波动率做风险调整。正值代表上涨动能加速且波动可控，预示后续延续上涨；负值反之。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom5 = data['close'].pct_change(5)
        mom20 = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((mom5 - mom20) / vol).clip(-1, 1)
        return result
