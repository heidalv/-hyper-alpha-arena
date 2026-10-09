"""AI因子: 波动调整动量加速度 | 置信:60% | 多周期动量差(短周期收益-长周期收益)衡量动量加速度，并用已实现波动率归一化，捕捉趋势加速阶段的持续方向性收益。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumAcceleration(BaseFactor):
    """多周期动量差(短周期收益-长周期收益)衡量动量加速度，并用已实现波动率归一化，捕捉趋势加速阶段的持续方向性收益。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_vol_adj",
            name="Volatility Adjusted Momentum Acceleration",
            display_name="波动调整动量加速度",
            description="多周期动量差(短周期收益-长周期收益)衡量动量加速度，并用已实现波动率归一化，捕捉趋势加速阶段的持续方向性收益。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short = data['close'].pct_change(5)
        long = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = ((short - long) / (vol + 1e-9)).clip(-1, 1)
        return result
