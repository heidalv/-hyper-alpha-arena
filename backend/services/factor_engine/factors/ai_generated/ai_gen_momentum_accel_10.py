"""AI因子: 动量加速度 | 置信:60% | 短期动量与中期动量的差值反映动量加速或衰减，加速上行预示趋势延续，加速下行预示趋势反转，经波动率标准化后输出方向性因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration(BaseFactor):
    """短期动量与中期动量的差值反映动量加速或衰减，加速上行预示趋势延续，加速下行预示趋势反转，经波动率标准化后输出方向性因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_10",
            name="Momentum Acceleration",
            display_name="动量加速度",
            description="短期动量与中期动量的差值反映动量加速或衰减，加速上行预示趋势延续，加速下行预示趋势反转，经波动率标准化后输出方向性因子。",
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
