"""AI因子: 多周期动量加速度 | 置信:60% | 短期动量与中期动量之差衡量动量加速度：正值代表近期动能强于中期趋势(趋势加速)，负值代表动能衰减。除以中期波动率做标准化，输出方向性 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiHorizonMomentumAcceleration(BaseFactor):
    """短期动量与中期动量之差衡量动量加速度：正值代表近期动能强于中期趋势(趋势加速)，负值代表动能衰减。除以中期波动率做标准化，输出方向性 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_ratio",
            name="Multi-Horizon Momentum Acceleration",
            display_name="多周期动量加速度",
            description="短期动量与中期动量之差衡量动量加速度：正值代表近期动能强于中期趋势(趋势加速)，负值代表动能衰减。除以中期波动率做标准化，输出方向性 alpha。",
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
