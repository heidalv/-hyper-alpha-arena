"""AI因子: 波动调整动量加速度 | 置信:60% | 多周期动量差（5日减20日收益）反映动量加速度，除以20日已实现波动率做风险调整，正值代表近期动量相对中期走强，捕捉趋势加速的持续性alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumAcceleration(BaseFactor):
    """多周期动量差（5日减20日收益）反映动量加速度，除以20日已实现波动率做风险调整，正值代表近期动量相对中期走强，捕捉趋势加速的持续性alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_vol_adj",
            name="Volatility-Adjusted Momentum Acceleration",
            display_name="波动调整动量加速度",
            description="多周期动量差（5日减20日收益）反映动量加速度，除以20日已实现波动率做风险调整，正值代表近期动量相对中期走强，捕捉趋势加速的持续性alpha。",
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
