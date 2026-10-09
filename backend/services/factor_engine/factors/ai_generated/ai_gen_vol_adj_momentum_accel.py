"""AI因子: 波动调整动量加速度 | 置信:60% | 以短周期收益率与长周期收益率之差衡量动量加速度，并用已实现波动率做标准化，捕捉趋势加速阶段的alpha。波动调整后因子在[-1,1]区间，正值表示上行加速，负值表示下行加速。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumAcceleration(BaseFactor):
    """以短周期收益率与长周期收益率之差衡量动量加速度，并用已实现波动率做标准化，捕捉趋势加速阶段的alpha。波动调整后因子在[-1,1]区间，正值表示上行加速，负值表示下行加速。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_adj_momentum_accel",
            name="Volatility Adjusted Momentum Acceleration",
            display_name="波动调整动量加速度",
            description="以短周期收益率与长周期收益率之差衡量动量加速度，并用已实现波动率做标准化，捕捉趋势加速阶段的alpha。波动调整后因子在[-1,1]区间，正值表示上行加速，负值表示下行加速。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_ret = data['close'].pct_change(5)
        long_ret = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = ((short_ret - long_ret) / (vol + 1e-9)).clip(-1, 1)
        return result
