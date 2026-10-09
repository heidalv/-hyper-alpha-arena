"""AI因子: 波动率调整动量加速度 | 置信:60% | 多周期动量差(短周期收益-长周期收益)衡量动量加速度，再除以已实现波动率进行风险调整。动量加速且波动可控时，未来延续上涨概率更高，形成趋势型alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationWithVolatilityScaling(BaseFactor):
    """多周期动量差(短周期收益-长周期收益)衡量动量加速度，再除以已实现波动率进行风险调整。动量加速且波动可控时，未来延续上涨概率更高，形成趋势型alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_vol",
            name="Momentum Acceleration with Volatility Scaling",
            display_name="波动率调整动量加速度",
            description="多周期动量差(短周期收益-长周期收益)衡量动量加速度，再除以已实现波动率进行风险调整。动量加速且波动可控时，未来延续上涨概率更高，形成趋势型alpha。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_ret = data['close'].pct_change(5)
        long_ret = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = ((short_ret - long_ret) / (vol + 1e-9)).rolling(3).mean().clip(-1, 1)
        return result
