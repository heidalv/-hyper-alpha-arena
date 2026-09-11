"""AI因子: 动量加速度波动调整 | 置信:60% | 多周期动量差（5日收益减20日收益）刻画动量加速度，除以已实现波动率做风险调整。加速度为正且波动可控时，未来上涨概率更高；负加速度则倾向回落。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationVolatilityAdjusted(BaseFactor):
    """多周期动量差（5日收益减20日收益）刻画动量加速度，除以已实现波动率做风险调整。加速度为正且波动可控时，未来上涨概率更高；负加速度则倾向回落。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_vol",
            name="Momentum Acceleration Volatility Adjusted",
            display_name="动量加速度波动调整",
            description="多周期动量差（5日收益减20日收益）刻画动量加速度，除以已实现波动率做风险调整。加速度为正且波动可控时，未来上涨概率更高；负加速度则倾向回落。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        fast = data['close'].pct_change(5)
        slow = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = ((fast - slow) / (vol + 1e-9)).clip(-1, 1)
        return result
