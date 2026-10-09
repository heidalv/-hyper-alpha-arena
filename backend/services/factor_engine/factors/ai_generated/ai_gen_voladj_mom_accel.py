"""AI因子: 波动率调整动量加速度 | 置信:60% | 以短周期收益与长周期收益之差衡量动量加速度，并用已实现波动率做归一化，捕捉趋势加速阶段的持续性 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumAcceleration(BaseFactor):
    """以短周期收益与长周期收益之差衡量动量加速度，并用已实现波动率做归一化，捕捉趋势加速阶段的持续性 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_voladj_mom_accel",
            name="Volatility Adjusted Momentum Acceleration",
            display_name="波动率调整动量加速度",
            description="以短周期收益与长周期收益之差衡量动量加速度，并用已实现波动率做归一化，捕捉趋势加速阶段的持续性 alpha。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        fast = data['close'].pct_change(5)
        slow = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((fast - slow) / vol).clip(-1, 1)
        return result
