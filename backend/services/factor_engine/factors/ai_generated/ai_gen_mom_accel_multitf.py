"""AI因子: 多周期动量加速度 | 置信:60% | 短期动量与中期动量的差值衡量动量加速度：短期显著强于中期说明趋势正在加速，未来延续上涨概率高；反之动量衰减则未来走弱。用波动率标准化后截断，得到方向性 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiTimeframeMomentumAcceleration(BaseFactor):
    """短期动量与中期动量的差值衡量动量加速度：短期显著强于中期说明趋势正在加速，未来延续上涨概率高；反之动量衰减则未来走弱。用波动率标准化后截断，得到方向性 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_multitf",
            name="Multi-Timeframe Momentum Acceleration",
            display_name="多周期动量加速度",
            description="短期动量与中期动量的差值衡量动量加速度：短期显著强于中期说明趋势正在加速，未来延续上涨概率高；反之动量衰减则未来走弱。用波动率标准化后截断，得到方向性 alpha。",
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
