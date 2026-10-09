"""AI因子: 波动调整动量加速度 | 置信:60% | 多周期动量之差（加速度）除以近期波动率，衡量单位风险下的动量变化。正值表示上涨动能加速，未来继续上涨概率更高；负值反之。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumAcceleration(BaseFactor):
    """多周期动量之差（加速度）除以近期波动率，衡量单位风险下的动量变化。正值表示上涨动能加速，未来继续上涨概率更高；负值反之。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_adjusted_momentum_accel",
            name="Volatility Adjusted Momentum Acceleration",
            display_name="波动调整动量加速度",
            description="多周期动量之差（加速度）除以近期波动率，衡量单位风险下的动量变化。正值表示上涨动能加速，未来继续上涨概率更高；负值反之。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom_fast = data['close'].pct_change(5)
        mom_slow = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = ((mom_fast - mom_slow) / (vol + 1e-9)).clip(-1, 1)
        return result
