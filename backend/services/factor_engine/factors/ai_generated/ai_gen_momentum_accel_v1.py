"""AI因子: 多周期动量加速度 | 置信:60% | 短期收益(5日)减去长期收益(20日)衡量动量加速度，正值表示近期动能强于中期趋势，捕捉趋势加速延续的alpha；用波动率归一化以稳定量纲。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiPeriodMomentumAcceleration(BaseFactor):
    """短期收益(5日)减去长期收益(20日)衡量动量加速度，正值表示近期动能强于中期趋势，捕捉趋势加速延续的alpha；用波动率归一化以稳定量纲。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_v1",
            name="Multi-Period Momentum Acceleration",
            display_name="多周期动量加速度",
            description="短期收益(5日)减去长期收益(20日)衡量动量加速度，正值表示近期动能强于中期趋势，捕捉趋势加速延续的alpha；用波动率归一化以稳定量纲。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        fast = data['close'].pct_change(5)
        slow = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((fast - slow) / vol).clip(-1, 1)
        return result
