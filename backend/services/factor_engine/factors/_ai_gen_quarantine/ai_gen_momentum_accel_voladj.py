"""AI因子: 波动调整动量加速度 | 置信:60% | 短期动量与中期动量之差再除以已实现波动率，衡量动量加速度的强度。正值表示近期动能加速上行，负值表示动能衰减或反转，波动率归一化使因子跨币种可比。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumAcceleration(BaseFactor):
    """短期动量与中期动量之差再除以已实现波动率，衡量动量加速度的强度。正值表示近期动能加速上行，负值表示动能衰减或反转，波动率归一化使因子跨币种可比。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_voladj",
            name="Volatility-Adjusted Momentum Acceleration",
            display_name="波动调整动量加速度",
            description="短期动量与中期动量之差再除以已实现波动率，衡量动量加速度的强度。正值表示近期动能加速上行，负值表示动能衰减或反转，波动率归一化使因子跨币种可比。",
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
