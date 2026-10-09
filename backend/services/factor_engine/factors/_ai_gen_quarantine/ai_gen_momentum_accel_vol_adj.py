"""AI因子: 波动调整动量加速度 | 置信:60% | 用短周期收益减长周期收益衡量动量加速度，再除以近期波动率做标准化，捕捉趋势加速阶段的持续性 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumAcceleration(BaseFactor):
    """用短周期收益减长周期收益衡量动量加速度，再除以近期波动率做标准化，捕捉趋势加速阶段的持续性 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_vol_adj",
            name="Volatility Adjusted Momentum Acceleration",
            display_name="波动调整动量加速度",
            description="用短周期收益减长周期收益衡量动量加速度，再除以近期波动率做标准化，捕捉趋势加速阶段的持续性 alpha。",
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
