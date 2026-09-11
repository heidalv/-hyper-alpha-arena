"""AI因子: 波动调整动量加速度 | 置信:60% | 多周期动量差(5日收益-20日收益)衡量动量加速度，除以20日已实现波动率做风险调整，捕捉趋势加速阶段的延续alpha。高值表示短周期动量显著强于长周期且波动可控，未来上涨概率更高。输出[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumAcceleration(BaseFactor):
    """多周期动量差(5日收益-20日收益)衡量动量加速度，除以20日已实现波动率做风险调整，捕捉趋势加速阶段的延续alpha。高值表示短周期动量显著强于长周期且波动可控，未来上涨概率更高。输出[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_voladj",
            name="Volatility-Adjusted Momentum Acceleration",
            display_name="波动调整动量加速度",
            description="多周期动量差(5日收益-20日收益)衡量动量加速度，除以20日已实现波动率做风险调整，捕捉趋势加速阶段的延续alpha。高值表示短周期动量显著强于长周期且波动可控，未来上涨概率更高。输出[-1,1]。",
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
