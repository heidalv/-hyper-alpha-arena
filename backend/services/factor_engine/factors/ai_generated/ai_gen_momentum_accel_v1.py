"""AI因子: 动量加速度 | 置信:60% | 短期动量(5日收益)与中期动量(20日收益)的差值，衡量动量加速度。正值表示短期动能强于中期趋势，上涨加速；负值表示动能衰减或反转。经典多周期动量加速度因子，预测未来收益方向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration(BaseFactor):
    """短期动量(5日收益)与中期动量(20日收益)的差值，衡量动量加速度。正值表示短期动能强于中期趋势，上涨加速；负值表示动能衰减或反转。经典多周期动量加速度因子，预测未来收益方向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_v1",
            name="Momentum Acceleration",
            display_name="动量加速度",
            description="短期动量(5日收益)与中期动量(20日收益)的差值，衡量动量加速度。正值表示短期动能强于中期趋势，上涨加速；负值表示动能衰减或反转。经典多周期动量加速度因子，预测未来收益方向。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_ret = data['close'].pct_change(5)
        long_ret = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((short_ret - long_ret) / vol).clip(-1, 1)
        return result
