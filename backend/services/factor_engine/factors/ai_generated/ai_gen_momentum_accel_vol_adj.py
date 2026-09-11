"""AI因子: 波动调整动量加速度 | 置信:60% | 多周期动量差(短期收益减长期收益)衡量动量加速度，除以已实现波动率做风险调整，正值表示动量加速向上，负值表示动量衰竭，预测未来收益方向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumAcceleration(BaseFactor):
    """多周期动量差(短期收益减长期收益)衡量动量加速度，除以已实现波动率做风险调整，正值表示动量加速向上，负值表示动量衰竭，预测未来收益方向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_vol_adj",
            name="Volatility Adjusted Momentum Acceleration",
            display_name="波动调整动量加速度",
            description="多周期动量差(短期收益减长期收益)衡量动量加速度，除以已实现波动率做风险调整，正值表示动量加速向上，负值表示动量衰竭，预测未来收益方向。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short = data['close'].pct_change(5)
        long = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((short - long) / vol).clip(-1, 1)
        return result
