"""AI因子: 动量加速度 | 置信:60% | 短期动量相对中期动量的加速度，捕捉趋势加强或衰竭。当短期动量显著高于中期动量时趋势加速向上；反之趋势减速或反转。用多周期收益率差衡量。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration(BaseFactor):
    """短期动量相对中期动量的加速度，捕捉趋势加强或衰竭。当短期动量显著高于中期动量时趋势加速向上；反之趋势减速或反转。用多周期收益率差衡量。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_15",
            name="Momentum Acceleration",
            display_name="动量加速度",
            description="短期动量相对中期动量的加速度，捕捉趋势加强或衰竭。当短期动量显著高于中期动量时趋势加速向上；反之趋势减速或反转。用多周期收益率差衡量。",
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
