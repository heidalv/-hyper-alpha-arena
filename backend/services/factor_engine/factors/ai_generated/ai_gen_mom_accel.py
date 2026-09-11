"""AI因子: 动量加速度 | 置信:60% | 短期动量与中期动量之差，捕捉趋势加速或衰竭：短周期收益显著高于中周期收益时动能向上，反之向下，作为方向性 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration(BaseFactor):
    """短期动量与中期动量之差，捕捉趋势加速或衰竭：短周期收益显著高于中周期收益时动能向上，反之向下，作为方向性 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel",
            name="Momentum Acceleration",
            display_name="动量加速度",
            description="短期动量与中期动量之差，捕捉趋势加速或衰竭：短周期收益显著高于中周期收益时动能向上，反之向下，作为方向性 alpha。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        fast = data['close'].pct_change(5)
        slow = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = ((fast - slow) / (vol + 1e-9)).clip(-1, 1)
        return result
